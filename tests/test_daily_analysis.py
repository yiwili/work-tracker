import json
import os
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update

from awt import db, daily_analysis, model_provider, service
from awt.app import create_app
from awt.models import Settings


@pytest.fixture
def app_client(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path/'data'))
    engine=db.make_engine(tmp_path/'test.db')
    with TestClient(create_app(engine)) as client:
        s=client.get('/api/settings').json()
        s.update(llm_enabled=True,llm_model='test',analysis_sources=['codex'])
        s['codex']['paths']=[];s['claude_code']['paths']=[]
        client.put('/api/settings',json=s).raise_for_status()
        yield client,engine
    engine.dispose()


def add_events(engine,count=48):
    with engine.begin() as conn:
        for i in range(count):
            conn.execute(insert(db.events).values(id='event-'+str(i),source='codex',session_id='session-'+str(i%6),occurred_at=f'2026-10-24T01:{i:02d}:00+00:00',kind='user_prompt',summary='修复同一接口的后续问题 '+str(i),cwd='',project='archive',raw_ref='private-file:'+str(i),metadata={}))


def result(refs=('e1',),title='修复接口超时'):
    return json.dumps({'summary':'围绕同一问题进行了讨论和修复。','tasks':[{'title':title,'details':'依据对话归纳，不按提问拆分。','project':'archive','category':'Bug 修复','refs':list(refs)}]},ensure_ascii=False)


def test_48_prompts_6_sessions_one_semantic_task(app_client,monkeypatch):
    client,engine=app_client;add_events(engine)
    captured=[]
    def complete(settings,system,text,**kwargs):
        payload=json.loads(text);captured.append(payload)
        return result([e['ref'] for e in payload['conversations']])
    monkeypatch.setattr(model_provider,'complete',scope_model(complete))
    r=client.post('/api/analyze?date=2026-10-24',json={});r.raise_for_status()
    assert r.json()['created']==1 and len(captured)==1
    assert len(captured[0]['conversations'])==48
    overview=client.get('/api/conversations?date=2026-10-24').json()
    assert overview['session_count']==6 and overview['user_messages']==48 and not overview['stale']
    tasks=client.get('/api/tasks?date=2026-10-24').json()
    assert len(tasks)==1 and len(tasks[0]['evidence'])==48
    assert tasks[0]['confirmed_minutes']==0 and tasks[0]['suggested_minutes'] is None
    client.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    assert len(client.get('/api/tasks?date=2026-10-24').json())==1


def test_replace_only_unreviewed_legacy_after_valid_ai_result(app_client,monkeypatch):
    client,engine=app_client;add_events(engine,3)
    ids=[]
    for title,minutes in [('旧规则草稿',0),('已经审核的工作',120),('人工会议',60)]:
        ids.append(client.post('/api/tasks',json={'date':'2026-10-24','title':title,'confirmed_minutes':minutes}).json()['id'])
    with engine.begin() as conn:
        conn.execute(update(db.tasks).where(db.tasks.c.id.in_(ids[:2])).values(origin='auto',edited=False))
    monkeypatch.setattr(model_provider,'complete',scope_model(lambda *a,**kw:result(['e999'])))
    assert client.post('/api/analyze?date=2026-10-24',json={}).status_code==400
    assert len(client.get('/api/tasks?date=2026-10-24').json())==3
    monkeypatch.setattr(model_provider,'complete',scope_model(lambda *a,**kw:result()))
    assert client.post('/api/analyze?date=2026-10-24',json={}).json()['archived']==1
    tasks=client.get('/api/tasks?date=2026-10-24').json()
    assert len(tasks)==3 and sum(t['confirmed_minutes'] for t in tasks)==180
    assert '旧规则草稿' not in [t['title'] for t in tasks]


def test_unselected_claude_and_git_are_not_task_inputs(app_client,monkeypatch):
    client,engine=app_client;add_events(engine,1)
    with engine.begin() as conn:
        for source,kind in [('claude_code','user_prompt'),('git','git_commit')]:
            conn.execute(insert(db.events).values(id=source,source=source,session_id=source,occurred_at='2026-10-24T01:02:00+00:00',kind=kind,summary='不属于本次分析的活动',cwd='',project='other',raw_ref='local',metadata={}))
    def complete(settings,system,text,**kw):
        records=json.loads(text)['conversations']
        assert len(records)==1 and records[0]['session'].startswith('codex:')
        return result()
    monkeypatch.setattr(model_provider,'complete',scope_model(complete))
    client.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    overview=client.get('/api/conversations?date=2026-10-24').json()
    assert overview['session_count']==1 and overview['user_messages']==1
    assert next(s for s in overview['sources'] if s['source']=='claude_code')['sessions']==1


def test_large_day_all_messages_then_final_consolidation(monkeypatch):
    settings=Settings()
    records=[{'ref':'e'+str(i),'kind':'user_prompt','text':'内容'*6000+str(i)} for i in range(12)]
    refs={r['ref']:r for r in records}
    seen=[];final=[]
    def complete(settings,system,text,**kw):
        payload=json.loads(text)
        if 'conversations' in payload:
            seen.extend(r['ref'] for r in payload['conversations'])
            return result([r['ref'] for r in payload['conversations']])
        final.append(payload)
        return result([ref for part in payload['daily_parts'] for task in part['tasks'] for ref in task['refs']])
    monkeypatch.setattr(model_provider,'complete',scope_model(complete))
    analyzed,requests=daily_analysis.infer_day(records,refs,settings,[],date(2026,10,24))
    assert seen==list(refs) and len(final)==1 and requests>2
    assert len(analyzed['tasks'])==1 and len(analyzed['tasks'][0]['refs'])==12


def test_anthropic_ccswitch_current_config_and_secret_never_returned(app_client,tmp_path,monkeypatch):
    client,engine=app_client
    secret='never-print-this-token'
    config=tmp_path/'claude.json';config.write_text(json.dumps({'env':{'ANTHROPIC_BASE_URL':'http://127.0.0.1:15721','ANTHROPIC_AUTH_TOKEN':secret,'ANTHROPIC_MODEL':'glm-test[1M]'}}))
    settings=client.get('/api/settings').json();settings.update(llm_provider='ccswitch',llm_model='',ccswitch_config_path=str(config))
    client.put('/api/settings',json=settings).raise_for_status()
    state=client.get('/api/model/status')
    assert state.json()['model']=='glm-test' and state.json()['endpoint']=='http://127.0.0.1:15721/v1/messages'
    assert secret not in state.text and secret not in client.get('/api/settings').text
    captured=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            captured.append((url,kwargs))
            return type('Response',(),{'raise_for_status':lambda _:None,'json':lambda _:{'content':[{'type':'text','text':'OK'}],'stop_reason':'end_turn'}})()
    monkeypatch.setattr(model_provider.httpx,'Client',FakeClient)
    assert model_provider.test_connection(Settings.model_validate(settings))['ok']
    url,call=captured[0]
    assert call['headers']['Authorization']=='Bearer '+secret
    assert 'system' in call['json'] and secret not in json.dumps(call['json'])
    assert 'private-file' not in json.dumps(call['json'])


@pytest.mark.skipif(os.name!='nt',reason='Windows DPAPI')
def test_api_key_encrypted_and_bound_to_endpoint(app_client):
    client,_=app_client
    secret='test-deepseek-private-key-123456'
    settings=client.get('/api/settings').json()
    settings.update(llm_provider='deepseek',llm_endpoint='https://api.deepseek.com/chat/completions',llm_model='deepseek-flash')
    client.put('/api/settings',json=settings).raise_for_status()
    r=client.post('/api/model/key',json={'api_key':secret});r.raise_for_status()
    assert r.json()['configured'] and secret not in r.text
    stored=(db.data_dir()/'model-credentials.json').read_text()
    assert secret not in stored
    assert model_provider.connection(Settings.model_validate(settings))['key']==secret
    settings['llm_endpoint']='https://different.example/chat/completions'
    client.put('/api/settings',json=settings)
    assert not client.get('/api/model/status').json()['configured']


def test_invalid_config_cannot_leak_credentials_in_error(app_client,tmp_path):
    client,_=app_client
    settings=client.get('/api/settings').json()
    settings.update(llm_provider='ccswitch',ccswitch_config_path=str(tmp_path/'missing.json'))
    client.put('/api/settings',json=settings)
    state=client.get('/api/model/status').json()
    assert not state['configured']


def test_human_edit_during_model_wait_is_not_locked_or_overwritten(app_client,monkeypatch):
    client,engine=app_client;add_events(engine,1)
    monkeypatch.setattr(model_provider,'complete',scope_model(lambda *args,**kwargs:result()))
    client.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    task=client.get('/api/tasks?date=2026-10-24').json()[0]
    def model_wait(*args,**kwargs):
        client.patch('/api/tasks/'+task['id'],json={'title':'用户刚审核的任务','confirmed_minutes':90}).raise_for_status()
        return result()
    monkeypatch.setattr(model_provider,'complete',scope_model(model_wait))
    client.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    tasks=client.get('/api/tasks?date=2026-10-24').json()
    assert len(tasks)==1 and tasks[0]['title']=='用户刚审核的任务' and tasks[0]['confirmed_minutes']==90


def test_analysis_rejects_changed_input_after_model_wait(app_client,monkeypatch):
    client,engine=app_client;add_events(engine,1)
    def model_wait(*args,**kwargs):
        s=client.get('/api/settings').json();s['analysis_sources']=['claude_code']
        client.put('/api/settings',json=s).raise_for_status()
        return result()
    monkeypatch.setattr(model_provider,'complete',scope_model(model_wait))
    r=client.post('/api/analyze?date=2026-10-24',json={})
    assert r.status_code==400 and '范围发生变化' in r.json()['detail']
    assert client.get('/api/tasks?date=2026-10-24').json()==[]


@pytest.mark.parametrize('provider,endpoint',[('deepseek','https://api.deepseek.com/chat/completions'),('glm','https://open.bigmodel.cn/api/paas/v4/chat/completions')])
def test_direct_api_key_chat_completions_transport(app_client,monkeypatch,provider,endpoint):
    monkeypatch.setenv('TEST_MODEL_KEY','secret-transport-key')
    settings=Settings(llm_provider=provider,llm_endpoint=endpoint,llm_model='test-model',llm_key_env='TEST_MODEL_KEY')
    captured=[]
    class FakeClient:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def post(self,url,**kwargs):
            captured.append((url,kwargs))
            return type('Response',(),{'raise_for_status':lambda _:None,'json':lambda _:{'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]}})()
    monkeypatch.setattr(model_provider.httpx,'Client',FakeClient)
    assert model_provider.complete(settings,'system rules','redacted conversation')=='OK'
    url,call=captured[0]
    assert url==endpoint and call['headers']['Authorization']=='Bearer secret-transport-key'
    assert call['json']['messages']==[{'role':'system','content':'system rules'},{'role':'user','content':'redacted conversation'}]
    assert 'secret-transport-key' not in json.dumps(call['json'])


def test_same_day_concurrent_analysis_is_not_sent_twice(app_client,monkeypatch):
    client,engine=app_client;add_events(engine,1)
    calls=[]
    def model_wait(*args,**kwargs):
        calls.append(1)
        running=client.get('/api/conversations?date=2026-10-24').json()
        assert running['running']
        other=client.post('/api/analyze?date=2026-10-24',json={})
        assert other.status_code==409
        return result()
    monkeypatch.setattr(model_provider,'complete',scope_model(model_wait))
    client.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    assert len(calls)==1 and not client.get('/api/conversations?date=2026-10-24').json()['running']


def scope_model(answer):
    def respond(settings,system,text,**kwargs):
        payload=json.loads(text)
        if 'required_refs' in payload:
            return json.dumps({'work_refs':payload['required_refs'],'excluded':[],'uncertain_refs':[]})
        return answer(settings,system,text,**kwargs)
    return respond
