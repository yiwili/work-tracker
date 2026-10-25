import json
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from awt import db, service, model_provider
from awt.app import create_app
from awt.models import Settings
from awt.privacy import redact

DAY = '2026-10-24'


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AWT_DATA_DIR", str(tmp_path / "data"))
    engine = db.make_engine(tmp_path / 'test.db')
    with TestClient(create_app(engine)) as client:
        s = client.get('/api/settings').json()
        s['codex']['paths'] = []
        s['claude_code']['paths'] = []
        client.put('/api/settings', json=s).raise_for_status()
        yield client
    engine.dispose()


def config_paths(client, codex=(), claude=(), **kw):
    s = client.get('/api/settings').json()
    s['codex']['paths'] = list(map(str, codex))
    s['claude_code']['paths'] = list(map(str, claude))
    s.update(kw)
    client.put('/api/settings', json=s).raise_for_status()


def write_jsonl(path, rows):
    path.write_text(''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), encoding='utf-8')


def codex_rows(cwd='project-a', session='session-1'):
    return [
        {'type':'session_meta','payload':{'id':session,'cwd':str(cwd)}},
        {'type':'response_item','timestamp':'2026-10-24T01:00:00Z','payload':{'type':'message','role':'user','content':[{'type':'input_text','text':'修复订单接口超时'}]}},
        {'type':'response_item','timestamp':'2026-10-24T01:01:00Z','payload':{'type':'function_call','name':'exec_command','arguments':'pytest','call_id':'c1'}},
        {'type':'response_item','timestamp':'2026-10-24T01:03:00Z','payload':{'type':'function_call_output','call_id':'c1','output':'3 passed in 2.10s'}},
    ]


def manual(minutes=0, **kw):
    return {'date':DAY,'title':'与客户确认上线计划','project':'客户 A','category':'客户沟通','confirmed_minutes':minutes,**kw}


def test_migrations_repeatable(client):
    db.migrate(client.app.state.engine)
    assert client.get('/api/health').json()['status']=='ok'


@pytest.mark.parametrize('minutes,code',[(479,400),(480,200),(481,400)])
def test_exact_minutes_confirmation(client, minutes, code):
    client.post('/api/tasks',json=manual(minutes)).raise_for_status()
    assert client.post(f'/api/reports/{DAY}/confirm',json={}).status_code==code
    report=client.get(f'/api/reports/{DAY}').json()
    assert report['confirmed']==(minutes==480)
    assert report['total_minutes']==minutes
    export=client.get(f'/api/reports/{DAY}/export.md').text
    assert ('状态：已确认' in export)==(minutes==480)
    assert '客户 A' in export


def test_confirm_invalidated_by_edits_date_and_settings(client):
    task_id=client.post('/api/tasks',json=manual(480)).json()['id']
    client.post(f'/api/reports/{DAY}/confirm',json={}).raise_for_status()
    client.patch('/api/tasks/'+task_id,json=manual(480,details='修改说明')).raise_for_status()
    assert not client.get(f'/api/reports/{DAY}').json()['confirmed']
    client.post(f'/api/reports/{DAY}/confirm',json={})
    config_paths(client,target_minutes=420)
    assert not client.get(f'/api/reports/{DAY}').json()['confirmed']
    client.patch('/api/tasks/'+task_id,json=manual(420,date='2026-10-25')).raise_for_status()
    assert client.get(f'/api/reports/{DAY}').json()['total_minutes']==0
    assert client.get('/api/reports/2026-10-25').json()['total_minutes']==420


def test_partial_patch_retains_other_fields(client):
    task_id=client.post('/api/tasks',json=manual(120,details='已沟通')).json()['id']
    client.patch('/api/tasks/'+task_id,json={'confirmed_minutes':135}).raise_for_status()
    task=client.get('/api/tasks?date='+DAY).json()[0]
    assert task['title']=='与客户确认上线计划' and task['details']=='已沟通'
    assert task['confirmed_minutes']==135


def test_incremental_duplicates_corrupt_and_partial_tail(client,tmp_path):
    a,b=tmp_path/'a.jsonl',tmp_path/'copy.jsonl'
    write_jsonl(a,codex_rows())
    shutil.copy(a,b)
    with a.open('ab') as stream:stream.write(b'broken-json\n{"type":')
    config_paths(client,codex=[a,b])
    first=client.post('/api/collect?date='+DAY,json={}).json()
    assert first['new_events']==3 and first['warnings']
    assert client.post('/api/collect?date='+DAY,json={}).json()['new_events']==0
    # Broken tail is retried; once completed it advances without losing following records.
    with a.open('ab') as stream:stream.write(b'"ignored"}\n')
    with a.open('a',encoding='utf-8') as stream:
        stream.write(json.dumps({'type':'response_item','timestamp':'2026-10-24T02:00:00Z','payload':{'type':'message','role':'user','content':'整理接口文档'}})+'\n')
    assert client.post('/api/collect?date='+DAY,json={}).json()['new_events']==1
    assert len(client.get('/api/events?date='+DAY).json())==4
    # Rotation/truncation and replay still deduplicate.
    write_jsonl(a,codex_rows())
    assert client.post('/api/collect?date='+DAY,json={}).json()['new_events']==0


def test_timezone_future_date_not_lost_and_cross_project(client,tmp_path):
    path=tmp_path/'log.jsonl'
    rows=codex_rows(tmp_path/'repo-a')
    rows += [
      {'type':'turn_context','payload':{'cwd':str(tmp_path/'repo-b')}},
      {'type':'response_item','timestamp':'2026-10-24T17:00:00Z','payload':{'type':'message','role':'user','content':'整理接口文档'}},
      {'type':'response_item','timestamp':'2026-10-23T16:05:00Z','payload':{'type':'message','role':'user','content':'讨论需求'}},
    ]
    write_jsonl(path,rows)
    config_paths(client,codex=[path])
    client.post('/api/collect?date='+DAY,json={}).raise_for_status()
    assert len(client.get('/api/events?date='+DAY).json())==4
    assert len(client.get('/api/events?date=2026-10-25').json())==1
    overview=client.get('/api/conversations?date='+DAY).json()
    assert {p for session in overview['sessions'] for p in session['projects']}=={'repo-a','repo-b'}
    client.post('/api/analyze?date='+DAY,json={}).raise_for_status()
    assert client.get('/api/tasks?date='+DAY).json()==[]
    assert [e['occurred_at'] for e in client.get('/api/events?date='+DAY).json()]==sorted(e['occurred_at'] for e in client.get('/api/events?date='+DAY).json())


def test_claude_results_and_untrusted_completion(client,tmp_path,monkeypatch):
    path=tmp_path/'claude.jsonl'
    write_jsonl(path,[
      {'type':'user','sessionId':'cc','cwd':str(tmp_path),'timestamp':'2026-10-24T01:00:00Z','message':{'content':'修复订单接口超时'}},
      {'type':'assistant','sessionId':'cc','timestamp':'2026-10-24T01:01:00Z','message':{'content':[{'type':'text','text':'已完成，所有功能均已验证。'}]}},
    ])
    config_paths(client,claude=[path],analysis_sources=['claude_code'],llm_enabled=True)
    model_answer(monkeypatch, [{'title':'修复接口','details':'讨论了修复方案，尚无验证证据','refs':['e1'],'project':tmp_path.name}])
    client.post('/api/collect?date='+DAY,json={})
    client.post('/api/analyze?date='+DAY,json={})
    task=client.get('/api/tasks?date='+DAY).json()[0]
    assert task['status']=='requested'
    assert task['confirmed_minutes']==0 and task['suggested_minutes'] is None
    assert len(task['evidence'])==2


def test_preserve_edits_and_deleted_on_reanalyze(client,tmp_path,monkeypatch):
    path=tmp_path/'log.jsonl';write_jsonl(path,codex_rows())
    config_paths(client,codex=[path],llm_enabled=True)
    model_answer(monkeypatch,[{'title':'修复订单接口超时','details':'修复后测试通过','refs':['e1'],'project':'project-a'}])
    client.post('/api/collect?date='+DAY,json={})
    client.post('/api/analyze?date='+DAY,json={})
    task=client.get('/api/tasks?date='+DAY).json()[0]
    assert task['status']=='verified'
    client.patch('/api/tasks/'+task['id'],json=manual(120,title='审核后的标题')).raise_for_status()
    client.post('/api/analyze?date='+DAY,json={})
    task=client.get('/api/tasks?date='+DAY).json()[0]
    assert task['title']=='审核后的标题' and task['confirmed_minutes']==120
    client.delete('/api/tasks/'+task['id'])
    client.post('/api/analyze?date='+DAY,json={})
    assert client.get('/api/tasks?date='+DAY).json()==[]


def test_cross_tool_daily_merge_and_manual_split_preserve_minutes(client,tmp_path,monkeypatch):
    a,b=tmp_path/'a.jsonl',tmp_path/'b.jsonl'
    write_jsonl(a,codex_rows(tmp_path))
    write_jsonl(b,[{'type':'user','sessionId':'c','cwd':str(tmp_path),'timestamp':'2026-10-24T01:00:00Z','message':{'content':'修复订单接口超时'}}])
    config_paths(client,codex=[a],claude=[b],llm_enabled=True,analysis_sources=['codex','claude_code'])
    model_answer(monkeypatch,[{'title':'修复订单接口超时','details':'跨工具讨论与测试同一问题','refs':['e1','e2'],'project':tmp_path.name}])
    client.post('/api/collect?date='+DAY,json={})
    result=client.post('/api/analyze?date='+DAY,json={});result.raise_for_status()
    tasks=client.get('/api/tasks?date='+DAY).json()
    assert len(tasks)==1 and len(tasks[0]['evidence'])==4
    assert {e['source'] for e in tasks[0]['evidence']}=={'codex','claude_code'}
    original=tasks[0]['id']
    client.patch('/api/tasks/'+original,json={'confirmed_minutes':120}).raise_for_status()
    assert client.post('/api/tasks/split',json={'id':original,'parts':[manual(60),manual(59)]}).status_code==400
    ids=client.post('/api/tasks/split',json={'id':original,'parts':[manual(60),manual(60,title='准备联调文档')]}).json()['ids']
    assert client.post('/api/tasks/merge',json={'ids':[ids[0],ids[0]]}).status_code==400
    client.post('/api/tasks/merge',json={'ids':ids}).raise_for_status()
    task=client.get('/api/tasks?date='+DAY).json()[0]
    assert task['confirmed_minutes']==120 and len(task['evidence'])==4


def test_long_idle_not_counted(client,tmp_path,monkeypatch):
    rows=codex_rows();rows[-1]['timestamp']='2026-10-24T09:03:00Z'
    path=tmp_path/'idle.jsonl';write_jsonl(path,rows)
    config_paths(client,codex=[path],llm_enabled=True)
    model_answer(monkeypatch,[{'title':'排查接口超时','details':'仅有零散工具活动，无法估算人工投入','refs':['e1'],'project':'project-a'}])
    client.post('/api/collect?date='+DAY,json={});client.post('/api/analyze?date='+DAY,json={})
    task=client.get('/api/tasks?date='+DAY).json()[0]
    assert task['suggested_minutes'] is None and task['confirmed_minutes']==0


def test_privacy_and_exclusions(client,tmp_path):
    secret='sk-abcdefghijklmnopqrstuvwxyz12345'
    raw=f'api_key={secret} password=hunter2 TOKEN=abcdefg Bearer bearerABC123 https://user:pass@example.com'
    safe=redact(raw)
    assert all(value not in safe for value in (secret,'hunter2','abcdefg','bearerABC123','user:pass'))
    path=tmp_path/'log.jsonl';rows=codex_rows(tmp_path/'sensitive');rows[1]['payload']['content']=raw;write_jsonl(path,rows)
    config_paths(client,codex=[path],excluded_paths=[str(tmp_path/'sensitive')])
    assert client.post('/api/collect?date='+DAY,json={}).json()['new_events']==0
    config_paths(client,codex=[path],excluded_paths=[])
    # Exclusion changes can be re-read by resetting cursors explicitly, keeping prior events intact.
    with client.app.state.engine.begin() as conn:conn.execute(db.cursors.delete())
    client.post('/api/collect?date='+DAY,json={})
    assert secret not in client.get('/api/events?date='+DAY).text


def test_llm_off_failure_and_allowlisted_payload(client,tmp_path,monkeypatch):
    path=tmp_path/'log.jsonl';write_jsonl(path,codex_rows(tmp_path/'private-path'))
    config_paths(client,codex=[path]);client.post('/api/collect?date='+DAY,json={})
    def forbidden(*args,**kwargs):raise AssertionError('Model must not be called by default')
    monkeypatch.setattr(model_provider,'complete',forbidden)
    result=client.post('/api/analyze?date='+DAY,json={}).json()
    assert result['analysis_required'] and not client.get('/api/tasks?date='+DAY).json()
    manual_id=client.post('/api/tasks',json=manual(120)).json()['id']
    config_paths(client,codex=[path],llm_enabled=True)
    def unavailable(*args,**kwargs):raise model_provider.ModelError('模型暂不可用')
    monkeypatch.setattr(model_provider,'complete',unavailable)
    assert client.post('/api/analyze?date='+DAY,json={}).status_code==400
    assert client.get('/api/tasks?date='+DAY).json()[0]['id']==manual_id
    from awt.daily_analysis import transcript
    from sqlalchemy import select
    with client.app.state.engine.connect() as conn: rows=[dict(e) for e in conn.execute(select(db.events)).mappings()]
    rows[0]['summary']='api_key=supersecret 修复接口'
    records,_,_=transcript(rows,Settings())
    serialized=json.dumps(records)
    assert 'supersecret' not in serialized and str(tmp_path/'private-path') not in serialized and '3 passed in 2.10s' not in serialized
    assert all('cwd' not in r and 'raw_ref' not in r for r in records)


def test_overlap_and_reject_bad_minutes(client):
    for title in ['开会','电话沟通']:
        client.post('/api/tasks',json=manual(60,title=title,started_at='2026-10-24T09:00:00+08:00',ended_at='2026-10-24T10:00:00+08:00')).raise_for_status()
    assert any('时间重叠' in w for w in client.get('/api/reports/'+DAY).json()['warnings'])
    for n in [-1,1.5,True,1500]:assert client.post('/api/tasks',json=manual(n)).status_code==422
    assert client.post('/api/tasks',json=manual(30,title=' ')).status_code==422


def test_git_history_and_current_changes_separated(client,tmp_path):
    repo=tmp_path/'repo';repo.mkdir()
    def git(*args):return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True,text=True)
    git('init');git('config','user.name','Test');git('config','user.email','test@example.invalid')
    (repo/'work.txt').write_text('first\n')
    git('add','.');git('commit','-m','Document initial work')
    (repo/'work.txt').write_text('current uncommitted changes\n')
    config_paths(client,git_roots=[str(repo)])
    from zoneinfo import ZoneInfo
    from datetime import datetime
    day=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    result=client.post('/api/collect?date='+day,json={}).json()
    assert result['git_events']==1
    events=client.get('/api/events?date='+day).json()
    assert events[0]['kind']=='git_commit' and '1\t0\twork.txt' in events[0]['metadata']['changes']
    repos=client.get('/api/repositories').json()
    assert 'work.txt' in repos[0]['current_changes'] and repos[0]['observed_at']
    assert not any(e['kind']=='file_change' for e in events)


def test_local_origin_protection(client):
    assert client.post('/api/collect?date='+DAY,json={},headers={'Origin':'https://evil.example'}).status_code==403
    assert client.post('/api/collect?date='+DAY,content='').status_code==415
    assert client.get('/api/settings',headers={'Host':'evil.example'}).status_code==403
    assert client.post('/api/tasks',json=manual(0),headers={'Origin':'http://testserver'}).status_code==200


def test_tool_workdir_and_file_path_override_session_project(client,tmp_path):
    a,b=tmp_path/'repo-a',tmp_path/'repo-b'
    a.mkdir();b.mkdir()
    for repo in [a,b]:
        subprocess.run(['git','-C',str(repo),'init'],check=True,capture_output=True)
    rows=codex_rows(a)
    rows[2]['payload']['arguments']=json.dumps({'cmd':'pytest','workdir':str(b)})
    rows += [
        {'type':'response_item','timestamp':'2026-10-24T01:05:00Z','payload':{'type':'custom_tool_call','name':'apply_patch','call_id':'p1','input':f'*** Begin Patch\n*** Update File: {b / "app.py"}\n@@\n-old\n+new\n*** End Patch'}},
    ]
    path=tmp_path/'cross.jsonl';write_jsonl(path,rows)
    config_paths(client,codex=[path]);client.post('/api/collect?date='+DAY,json={}).raise_for_status()
    events=client.get('/api/events?date='+DAY).json()
    by_kind={e['kind']:e for e in events}
    assert by_kind['user_prompt']['project']=='repo-a'
    assert by_kind['shell_command']['project']=='repo-b'
    assert by_kind['tool_result']['project']=='repo-b'
    assert by_kind['tool_call']['project']=='repo-b'
    client.post('/api/analyze?date='+DAY,json={}).raise_for_status()
    assert client.get('/api/tasks?date='+DAY).json()==[]


def test_persistence_reopen(tmp_path):
    engine=db.make_engine(tmp_path/'persist.db')
    with TestClient(create_app(engine)) as client:
        client.post('/api/tasks',json=manual(480)).raise_for_status()
    engine.dispose()
    reopened=db.make_engine(tmp_path/'persist.db')
    with TestClient(create_app(reopened)) as client:
        assert client.get('/api/reports/'+DAY).json()['total_minutes']==480
    reopened.dispose()


def model_answer(monkeypatch,tasks):
    def respond(settings,system,text,**kwargs):
        value=json.loads(text)
        if 'required_refs' in value:
            return json.dumps({'work_refs':value['required_refs'],'excluded':[],'uncertain_refs':[]})
        return json.dumps({'summary':'按全天工作目标整理','tasks':tasks},ensure_ascii=False)
    monkeypatch.setattr(model_provider,'complete',respond)
