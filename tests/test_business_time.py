import json
from datetime import datetime,timedelta,timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert,select,update

from awt import activity_time,db,model_provider,time_allocation,daily_analysis,work_scope
from awt.app import create_app
from awt.models import Settings


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path/'data'))
    engine=db.make_engine(tmp_path/'test.db')
    with TestClient(create_app(engine)) as c:
        s=c.get('/api/settings').json();s.update(llm_enabled=True,llm_model='test')
        s['codex']['paths']=[];s['claude_code']['paths']=[]
        c.put('/api/settings',json=s).raise_for_status()
        yield c,engine
    engine.dispose()


def add_task(client,title,minutes=0,**kw):
    return client.post('/api/tasks',json={'date':'2026-10-24','title':title,'confirmed_minutes':minutes,**kw}).json()['id']


def weights(monkeypatch,change=None):
    def model(settings,system,text,**kw):
        payload=json.loads(text)
        assert 'unlocked_tasks' in payload and 'locked_tasks' in payload
        if change:change(payload)
        return json.dumps({'items':[{'id':t['id'],'eligible':True,'weight':i+1,'estimated_minutes':120,'reason':'按实际业务目标和难度估算','confidence':'中','difficulty':'中'} for i,t in enumerate(payload['unlocked_tasks'])],'note':'待审核建议'},ensure_ascii=False)
    monkeypatch.setattr(model_provider,'complete',model)


def test_ai_fills_remaining_7h_preserving_1h_manual_and_draft(client,monkeypatch):
    c,_=client
    fixed=add_task(c,'客户沟通',60)
    add_task(c,'修复全文提取问题');add_task(c,'优化归档章显示')
    weights(monkeypatch)
    r=c.post('/api/reports/2026-10-24/estimate-time',json={});r.raise_for_status()
    assert r.json()['total_minutes']==480 and r.json()['fixed_minutes']==60
    report=c.get('/api/reports/2026-10-24').json()
    assert not report['confirmed']
    manual=next(t for t in report['tasks'] if t['id']==fixed)
    assert manual['confirmed_minutes']==60 and manual['time_locked'] and manual['time_source']=='manual'
    assert sum(t['confirmed_minutes'] for t in report['tasks'] if t['time_source']=='ai_estimate')==420
    assert 'AI目标工时分配建议' in c.get('/api/reports/2026-10-24/export.md').text
    assert not report['time_allocation']['stale']


def test_half_hour_allocation_and_explicit_pins(client,monkeypatch):
    c,_=client
    fixed=add_task(c,'会议',120)
    first=add_task(c,'业务修复A');add_task(c,'业务修复B')
    weights(monkeypatch)
    c.post('/api/reports/2026-10-24/estimate-time',json={}).raise_for_status()
    r=c.get('/api/reports/2026-10-24').json()
    assert r['total_minutes']==480
    assert all(t['confirmed_minutes']%30==0 for t in r['tasks'])
    assert next(t for t in r['tasks'] if t['id']==fixed)['confirmed_minutes']==120
    c.patch('/api/tasks/'+first,json={'confirmed_minutes':90}).raise_for_status()
    task=next(t for t in c.get('/api/tasks?date=2026-10-24').json() if t['id']==first)
    assert task['time_locked'] and task['time_source']=='manual'
    c.post('/api/reports/2026-10-24/estimate-time',json={}).raise_for_status()
    assert c.get('/api/reports/2026-10-24').json()['total_minutes']==480


def test_non_half_hour_remainder_rejected_without_model_or_mutation(client,monkeypatch):
    c,_=client
    fixed=add_task(c,'会议',119)
    add_task(c,'业务修复')
    def forbidden(*a,**kw):raise AssertionError('must reject before calling model')
    monkeypatch.setattr(model_provider,'complete',forbidden)
    result=c.post('/api/reports/2026-10-24/estimate-time',json={})
    assert result.status_code==400 and '0.5小时' in result.json()['detail']
    report=c.get('/api/reports/2026-10-24').json()
    assert report['total_minutes']==119
    assert next(t for t in report['tasks'] if t['id']==fixed)['confirmed_minutes']==119


@pytest.mark.parametrize('editor_step',[15,20,45])
def test_ai_half_hour_units_independent_of_manual_editor_step(client,monkeypatch,editor_step):
    c,_=client
    settings=c.get('/api/settings').json();settings['step_minutes']=editor_step
    c.put('/api/settings',json=settings).raise_for_status()
    add_task(c,'客户沟通',60)
    for title in ['附件修复','表格优化','功能新增']:
        add_task(c,title)
    def estimate(settings,system,text,**kw):
        payload=json.loads(text)
        assert payload['allocation_step_minutes']==30
        return json.dumps({'items':[{'id':t['id'],'eligible':True,'weight':i+1,'estimated_minutes':20+i*25,'reason':'按工作范围估算'} for i,t in enumerate(payload['unlocked_tasks'])]})
    monkeypatch.setattr(model_provider,'complete',estimate)
    c.post('/api/reports/2026-10-24/estimate-time',json={}).raise_for_status()
    report=c.get('/api/reports/2026-10-24').json()
    assert report['total_minutes']==480
    for task in report['tasks']:
        assert task['confirmed_minutes']%30==0
        if task['suggested_minutes'] is not None:
            assert task['suggested_minutes']%30==0


def test_time_changes_during_ai_wait_are_never_overwritten(client,monkeypatch):
    c,_=client
    task=add_task(c,'修复业务功能')
    def update_manual(payload):c.patch('/api/tasks/'+task,json={'confirmed_minutes':75}).raise_for_status()
    weights(monkeypatch,update_manual)
    r=c.post('/api/reports/2026-10-24/estimate-time',json={})
    assert r.status_code==400 and '未覆盖人工修改' in r.json()['detail']
    assert c.get('/api/reports/2026-10-24').json()['total_minutes']==75


def test_fixed_over_target_or_no_real_eligible_work_cannot_invent_items(client,monkeypatch):
    c,_=client
    fixed=add_task(c,'现场沟通',481)
    def forbidden(*a,**kw):raise AssertionError('no model call needed')
    monkeypatch.setattr(model_provider,'complete',forbidden)
    assert c.post('/api/reports/2026-10-24/estimate-time',json={}).status_code==400
    c.delete('/api/tasks/'+fixed)
    private=add_task(c,'个人娱乐问题')
    monkeypatch.setattr(model_provider,'complete',lambda s,p,t,**kw:json.dumps({'items':[{'id':private,'eligible':False,'weight':0,'estimated_minutes':None,'reason':'私人问题不能计入工时'}]}))
    assert c.post('/api/reports/2026-10-24/estimate-time',json={}).status_code==400
    assert c.get('/api/reports/2026-10-24').json()['total_minutes']==0
    assert len(c.get('/api/tasks?date=2026-10-24').json())==1


def test_private_topic_in_repo_is_excluded_from_tasks_and_time(client,monkeypatch):
    c,engine=client
    data=[('work','user_prompt','修复附件全文提取',0),('result','assistant_message','正在排查',1),('private','user_prompt','帮我推荐晚餐和约会地点',2),('reply','assistant_message','私人问题的回答',3),('work2','user_prompt','优化归档章显示',4),('done','assistant_message','讨论显示问题',5)]
    with engine.begin() as conn:
        for eid,kind,text,minute in data:
            conn.execute(insert(db.events).values(id=eid,source='codex',session_id='same-repo-session',occurred_at=f'2026-10-24T01:0{minute}:00+00:00',kind=kind,summary=text,cwd='/project/archive',project='archive',raw_ref='local',metadata={}))
    def model(settings,system,text,**kw):
        p=json.loads(text)
        if 'required_refs' in p:return json.dumps({'work_refs':['e1','e5'],'excluded':[{'ref':'e3','reason':'私人问题'}],'uncertain_refs':[]})
        assert '推荐晚餐' not in text and '私人问题的回答' not in text
        return json.dumps({'summary':'处理附件及归档章功能问题','tasks':[{'title':'修复档案附件与归档章功能问题','details':'按业务目标合并处理','project':'archive','refs':['e1','e5']}]},ensure_ascii=False)
    monkeypatch.setattr(model_provider,'complete',model)
    c.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    report=c.get('/api/reports/2026-10-24').json()
    assert report['conversations']['analysis']['scope']['excluded_user_messages']==1
    assert report['conversations']['work_timing']['observable_active_minutes']==2
    assert {e['id'] for e in report['tasks'][0]['evidence']}=={'work','result','work2','done'}
    assert report['tasks'][0]['timing']['observable_active_minutes']==2


def test_scope_failure_keeps_existing_tasks(client,monkeypatch):
    c,engine=client
    with engine.begin() as conn:
        conn.execute(insert(db.events).values(id='work',source='codex',session_id='s',occurred_at='2026-10-24T01:00:00+00:00',kind='user_prompt',summary='修复业务问题',cwd='',project='archive',raw_ref='local',metadata={}))
    old=add_task(c,'已有人工记录',60)
    monkeypatch.setattr(model_provider,'complete',lambda *a,**kw:json.dumps({'work_refs':[],'excluded':[],'uncertain_refs':[]}))
    assert c.post('/api/analyze?date=2026-10-24',json={}).status_code==400
    assert c.get('/api/tasks?date=2026-10-24').json()[0]['id']==old


def knowledge_event(index,minute,kind='user_prompt',text='继续了解挂接功能的区别'):
    return {'id':f'knowledge-{index}','source':'codex','session_id':'knowledge-session',
            'occurred_at':(datetime(2026,10,24,1,tzinfo=timezone.utc)+timedelta(minutes=minute)).isoformat(),
            'kind':kind,'summary':text,'cwd':'/archive','project':'2026','raw_ref':'local','metadata':{}}


@pytest.mark.parametrize(('last_minute','admitted'),[(8,False),(60,False),(64,True)])
def test_learning_requires_strictly_over_hour_of_active_topic(last_minute,admitted,monkeypatch):
    rows=[knowledge_event(i,m) for i,m in enumerate(range(0,last_minute+1,4))]
    settings=Settings(activity_gap_minutes=30)
    records,refs,segments=daily_analysis.transcript(rows,settings)
    def classify(s,system,text,**kw):
        p=json.loads(text)
        return json.dumps({'work_refs':[],'learning_groups':[{'topic':'挂接方式区别','refs':p['required_refs']}],'excluded':[],'uncertain_refs':[]})
    monkeypatch.setattr(model_provider,'complete',classify)
    kept,_,scope,_=work_scope.filter_work(records,refs,segments,settings)
    assert bool(kept)==admitted
    assert scope['learning_groups'][0]['timing']['observable_active_minutes']==last_minute
    assert scope['learning_groups'][0]['eligible']==admitted
    if admitted:assert all(r['learning_evidence']['observable_active_minutes']==64 for r in kept)
    else:assert len(scope['excluded'])==len(rows) and not scope['work_event_ids']


def test_learning_does_not_count_idle_long_answers_or_bridge_other_topics(monkeypatch):
    rows=[knowledge_event(0,0),knowledge_event(1,1,'assistant_message','源码解释'*2000),
          knowledge_event(2,2,text='私人问题'),knowledge_event(3,3),
          knowledge_event(4,64,'assistant_message','补充解释'),knowledge_event(5,65)]
    settings=Settings(activity_gap_minutes=30)
    records,refs,segments=daily_analysis.transcript(rows,settings)
    monkeypatch.setattr(model_provider,'complete',lambda *a,**kw:json.dumps({'work_refs':[],
        'learning_groups':[{'topic':'挂接方式','refs':['e1','e4','e6']}],
        'excluded':[{'ref':'e3','reason':'私人问题'}],'uncertain_refs':[]}))
    kept,_,scope,_=work_scope.filter_work(records,refs,segments,settings)
    timing=scope['learning_groups'][0]['timing']
    assert not kept and timing['session_span_minutes']==65 and timing['observable_active_minutes']==2


def test_single_knowledge_question_with_hour_of_tool_activity_is_not_learning_work(monkeypatch):
    rows=[knowledge_event(0,0)]+[knowledge_event(i,m,'tool_result','工具解释源码') for i,m in enumerate(range(1,66,4),1)]
    settings=Settings()
    records,refs,segments=daily_analysis.transcript(rows,settings)
    monkeypatch.setattr(model_provider,'complete',lambda *a,**kw:json.dumps({'work_refs':[],
        'learning_groups':[{'topic':'源码知识','refs':['e1']}],'excluded':[],'uncertain_refs':[]}))
    kept,_,scope,_=work_scope.filter_work(records,refs,segments,settings)
    assert not kept and scope['learning_groups'][0]['timing']['observable_active_minutes']==0


def test_short_knowledge_excluded_but_actual_fix_and_pinned_manual_survive(client,monkeypatch):
    c,engine=client
    rows=[knowledge_event(0,0,text='仅了解有条目挂接与无条目挂接的功能区别与适用场景'),
          knowledge_event(1,2,'assistant_message','结合源码解释两种方式，并未实施修改'),
          knowledge_event(2,3,text='修复无条目挂接创建记录失败，并验证ZIP导入结果'),
          knowledge_event(3,5,'assistant_message','排查并验证实际问题')]
    with engine.begin() as conn:conn.execute(insert(db.events),rows)
    manual=add_task(c,'人工会议',60)
    calls=[]
    def model(settings,system,text,**kw):
        p=json.loads(text);calls.append(p)
        if 'required_refs' in p:
            return json.dumps({'work_refs':['e3'],'learning_groups':[{'topic':'挂接功能区别','refs':['e1']}],'excluded':[],'uncertain_refs':[]})
        assert {r['ref'] for r in p['conversations']}=={'e3','e4'}
        return json.dumps({'tasks':[{'title':'修复无条目挂接创建记录失败的问题','details':'处理并验证故障','project':'2026','refs':['e3']}]})
    monkeypatch.setattr(model_provider,'complete',model)
    c.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    r=c.get('/api/reports/2026-10-24').json()
    assert len(r['tasks'])==2 and r['total_minutes']==60 and len(calls)==2
    assert next(t for t in r['tasks'] if t['id']==manual)['time_locked']
    scope=r['conversations']['analysis']['scope']
    assert scope['excluded_user_messages']==1 and scope['work_user_messages']==1
    assert r['conversations']['work_timing']['observable_active_minutes']==2


def test_only_short_knowledge_never_calls_task_generation(client,monkeypatch):
    c,engine=client
    rows=[knowledge_event(0,0),knowledge_event(1,2,'assistant_message','详细源码说明')]
    with engine.begin() as conn:conn.execute(insert(db.events),rows)
    calls=[]
    def model(s,system,text,**kw):
        p=json.loads(text);calls.append(p)
        assert 'required_refs' in p
        return json.dumps({'work_refs':[],'learning_groups':[{'topic':'知识梳理','refs':['e1']}],'excluded':[],'uncertain_refs':[]})
    monkeypatch.setattr(model_provider,'complete',model)
    c.post('/api/analyze?date=2026-10-24',json={}).raise_for_status()
    r=c.get('/api/reports/2026-10-24').json()
    assert len(calls)==1 and not r['tasks'] and r['total_minutes']==0


def test_history_is_style_only_and_source_stays_byte_identical(client,tmp_path):
    c,_=client
    path=tmp_path/'history.json'
    path.write_text(json.dumps([{'created_at':'2026-09-01','title':'旧记录'},{'created_at':'2026-09-15','title':'修复附件全文提取问题','content':'私密评论不能作为写作范例'},{'plan_started_at':'2026-10-20','title':'优化档案查询功能'}],ensure_ascii=False),encoding='utf-8')
    before=path.read_bytes()
    r=c.post('/api/style/import',json={'path':str(path),'since':'2026-09-15','include_git':False});r.raise_for_status()
    settings=c.get('/api/settings').json()
    assert settings['title_examples']==['修复附件全文提取问题','优化档案查询功能']
    assert path.read_bytes()==before and '私密评论' not in json.dumps(settings,ensure_ascii=False)
    assert c.get('/api/tasks?date=2026-10-24').json()==[]


def test_timing_unions_parallel_intervals_and_ignores_long_idle():
    def event(id,session,minute,kind='tool_call',summary=''):
        return {'id':id,'source':'codex','session_id':session,'kind':kind,'occurred_at':(datetime(2026,10,24,tzinfo=timezone.utc)+timedelta(minutes=minute)).isoformat(),'metadata':{},'summary':summary}
    events=[event('a','s1',0),event('b','s1',5),event('c','s1',60),event('d','s2',2),event('e','s2',7)]
    p=activity_time.profile(events)
    assert p['observable_active_minutes']==7 and p['session_span_minutes']==60
    assert activity_time.profile([events[0],events[2]],events)['observable_active_minutes']==0


def test_distribute_exact_integer_minutes_across_many_weights():
    for remaining in [1,14,15,119,361,420,480]:
        items=[{'id':str(i),'weight':i+1} for i in range(7)]
        result=time_allocation.distribute(remaining,items,15)
        assert sum(result.values())==remaining and all(type(v)is int and v>=0 for v in result.values())
