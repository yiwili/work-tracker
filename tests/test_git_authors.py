import json
import os
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert,select

from awt import db,git_tools,service,daily_analysis,model_provider
from awt.models import Settings,GitAuthor
from awt.app import create_app

DAY=date(2026,10,24)


def git(repo,*args,env=None):
    return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,
        text=True,encoding='utf-8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.strip()


@pytest.fixture
def repositories(tmp_path,monkeypatch):
    global_config=tmp_path/'global.gitconfig'
    global_config.write_text('[user]\nname = Global User\nemail = global@example.invalid\n',encoding='utf-8')
    monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(global_config));monkeypatch.setenv('GIT_CONFIG_NOSYSTEM','1')
    for key in ('GIT_AUTHOR_NAME','GIT_AUTHOR_EMAIL','GIT_COMMITTER_NAME','GIT_COMMITTER_EMAIL'):
        monkeypatch.delenv(key,raising=False)
    def make(name):
        root=tmp_path/name;root.mkdir();git(root,'init')
        git(root,'config','user.name','Alice');git(root,'config','user.email','alice@example.invalid')
        commits={}
        for i,(author,email,title) in enumerate([
            ('Alice','alice@example.invalid','Alice work'),
            ('Alice Alias','ALICE@example.invalid','Alias work'),
            ('Alice','alice@example.invalid.other','Similar email coworker work'),
            ('Bob','bob@example.invalid','Bob work')]):
            (root/'work.txt').write_text(str(i),encoding='utf-8');git(root,'add','work.txt')
            env={**os.environ,'GIT_AUTHOR_DATE':f'2026-10-24T01:0{i}:00+0000','GIT_COMMITTER_DATE':f'2026-10-24T01:0{i}:00+0000'}
            git(root,'commit','--author',f'{author} <{email}>','-m',title,env=env)
            commits[title]=git(root,'rev-parse','HEAD')
        return root,commits
    return make,global_config


def repo(root):return git_tools.resolve_project(str(root),Settings())[1]


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path/'data'))
    engine=db.make_engine()
    with TestClient(create_app(engine)) as c:
        settings=c.get('/api/settings').json();settings['codex']['paths']=[];settings['claude_code']['paths']=[]
        c.put('/api/settings',json=settings).raise_for_status()
        yield c
    engine.dispose()


def configure(c,**changes):
    settings=c.get('/api/settings').json();settings.update(changes)
    c.put('/api/settings',json=settings).raise_for_status()
    return settings


def test_local_identity_wins_and_exact_email_uses_author_not_committer(repositories):
    root,_=repositories[0]('repo with spaces')
    detected=git_tools.configured_author(root)
    assert detected=={'name':'Alice','email':'alice@example.invalid','source':'local'}
    rows=git_tools.collect_commits(repo(root),DAY,Settings())
    assert {r['subject'] for r in rows}=={'Alice work','Alias work'}
    assert all(r['author_email'].lower()=='alice@example.invalid' for r in rows)


def test_custom_email_name_and_per_repository_override(repositories):
    first,_=repositories[0]('first');second,_=repositories[0]('second')
    settings=Settings(git_author=GitAuthor(email='bob@example.invalid'),git_author_overrides={str(first):GitAuthor(email='alice@example.invalid')})
    assert {r['subject'] for r in git_tools.collect_commits(repo(first),DAY,settings)}=={'Alice work','Alias work'}
    assert [r['subject'] for r in git_tools.collect_commits(repo(second),DAY,settings)]==['Bob work']
    settings.git_author_overrides={};settings.git_author=GitAuthor(name='Alice')
    assert {r['subject'] for r in git_tools.collect_commits(repo(first),DAY,settings)}=={'Alice work','Similar email coworker work'}
    settings.git_author=GitAuthor(name='Ali')
    assert not git_tools.collect_commits(repo(first),DAY,settings)


def test_inherited_config_and_unavailable_or_wrong_identity_never_collect_everyone(repositories):
    root,_=repositories[0]('repo')
    git(root,'config','--unset','user.name');git(root,'config','--unset','user.email')
    assert git_tools.configured_author(root)['email']=='global@example.invalid'
    assert git_tools.configured_author(root)['source']=='inherited'
    warnings=[]
    assert not git_tools.collect_commits(repo(root),DAY,Settings(),warnings)
    assert '未匹配' in warnings[0]
    repositories[1].write_text('',encoding='utf-8');warnings=[]
    assert not git_tools.collect_commits(repo(root),DAY,Settings(),warnings)
    assert '无法获取Git用户' in warnings[0]
    assert [r['subject'] for r in git_tools.collect_commits(repo(root),DAY,Settings(git_author=GitAuthor(email='bob@example.invalid')))]==['Bob work']


def test_readonly_identity_endpoint_lists_authors_and_preserves_git_config(client,repositories):
    root,_=repositories[0]('repo');before=(root/'.git/config').read_bytes()
    response=client.post('/api/git/identities',json={'roots':[str(root),str(root)]});response.raise_for_status()
    info=response.json();assert len(info)==1
    assert info[0]['detected']['email']=='alice@example.invalid'
    assert {r['email'] for r in info[0]['authors']}=={'alice@example.invalid','ALICE@example.invalid','alice@example.invalid.other','bob@example.invalid'}
    assert (root/'.git/config').read_bytes()==before
    assert client.get('/api/settings').json()['git_roots']==[]
    configure(client,excluded_paths=[str(root)])
    assert client.post('/api/git/identities',json={'roots':[str(root)]}).json()==[]


def test_old_colleague_events_are_filtered_without_deleting_reviewed_work(client,repositories,monkeypatch):
    root,commits=repositories[0]('repo')
    configure(client,git_roots=[str(root)],analysis_sources=['codex','git'],llm_enabled=True,llm_model='fixture')
    client.post('/api/collect?date='+str(DAY),json={}).raise_for_status()
    identity=repo(root)
    engine=client.app.state.engine
    with engine.begin() as conn:
        # A legacy imported coworker commit has no author metadata. Read its
        # actual Git author, never guess from repository name or committer.
        conn.execute(insert(db.events).values(id='old-bob',source='git',session_id=identity['id'],
            occurred_at='2026-10-24T01:03:00+00:00',kind='git_commit',summary='Bob work',cwd=str(root),project='repo',
            repo_id=identity['id'],raw_ref='local',metadata={'sha':commits['Bob work']}))
        conn.execute(insert(db.events).values(id='request',source='codex',session_id='session',
            occurred_at='2026-10-24T01:04:00+00:00',kind='user_prompt',summary='修复项目查询问题',cwd=str(root),project='repo',raw_ref='local',metadata={}))
    manual=client.post('/api/tasks',json={'date':str(DAY),'title':'人工会议','confirmed_minutes':60}).json()['id']
    def model(settings,system,text,**kw):
        p=json.loads(text)
        if 'required_refs' in p:return json.dumps({'work_refs':p['required_refs'],'excluded':[],'uncertain_refs':[]})
        assert {r['text'] for r in p['conversations'] if r['kind']=='git_commit'}=={'Alice work','Alias work'}
        return '{"summary":"仅分析本人提交","tasks":[]}'
    monkeypatch.setattr(model_provider,'complete',model)
    client.post('/api/analyze?date='+str(DAY),json={}).raise_for_status()
    events=client.get('/api/events?date='+str(DAY)).json()
    assert {e['summary'] for e in events if e['source']=='git'}=={'Alice work','Alias work'}
    before=client.get('/api/conversations?date='+str(DAY)).json();assert not before['stale']
    configure(client,git_author={'email':'bob@example.invalid'})
    events=client.get('/api/events?date='+str(DAY)).json()
    assert [e['summary'] for e in events if e['source']=='git']==['Bob work']
    overview=client.get('/api/conversations?date='+str(DAY)).json()
    assert overview['stale'] and next(s for s in overview['sources'] if s['source']=='git')['events']==1
    report=client.get('/api/reports/'+str(DAY)).json()
    assert report['total_minutes']==60 and report['tasks'][0]['id']==manual
    with engine.connect() as conn:assert len(conn.execute(select(db.events).where(db.events.c.source=='git')).all())==3


def test_collect_switch_user_updates_metadata_and_is_idempotent(client,repositories):
    root,_=repositories[0]('repo');configure(client,git_roots=[str(root)])
    first=client.post('/api/collect?date='+str(DAY),json={}).json();assert first['git_events']==2
    assert client.post('/api/collect?date='+str(DAY),json={}).json()['git_events']==0
    configure(client,git_author={'email':'bob@example.invalid'})
    assert client.post('/api/collect?date='+str(DAY),json={}).json()['git_events']==1
    items=client.get('/api/events?date='+str(DAY)).json()
    assert len(items)==1 and items[0]['metadata']['author_email']=='bob@example.invalid'


def test_git_style_examples_only_use_selected_author(client,repositories,tmp_path):
    root,_=repositories[0]('repo');configure(client,git_roots=[str(root)])
    history=tmp_path/'history.json';history.write_text('[{"date":"2026-10-20","title":"修复历史问题"}]',encoding='utf-8')
    client.post('/api/style/import',json={'path':str(history),'since':'2026-09-15','include_git':True}).raise_for_status()
    examples=client.get('/api/settings').json()['title_examples']
    assert 'Alice work' in examples and 'Alias work' in examples
    assert 'Bob work' not in examples and 'Similar email coworker work' not in examples


def test_excluded_git_event_is_not_read_for_identity_or_commit_metadata(monkeypatch,tmp_path):
    root=tmp_path/'private-repo'
    event={'source':'git','cwd':str(root),'metadata':{'sha':'a'*40}}
    monkeypatch.setattr(git_tools,'git',lambda *a:pytest.fail('excluded repository must not be read'))
    assert git_tools.filter_git_events([event],Settings(excluded_paths=[str(root)]))==[]


@pytest.mark.parametrize('changes',[{'git_author':{}},{'git_author':{'name':'a\nb'}},{'git_author_overrides':{'':{'name':'Alice'}}}])
def test_invalid_git_identity_config_is_rejected(client,changes):
    settings=client.get('/api/settings').json();settings.update(changes)
    assert client.put('/api/settings',json=settings).status_code==422
