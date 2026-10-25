import base64
import io
import json
from datetime import date
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from openpyxl import Workbook
from awt import db, service, model_provider, prompts, gitee, style_reference
from awt.app import create_app
from awt.models import GiteeConfig


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path))
    engine=db.make_engine(tmp_path/'work.db')
    with TestClient(create_app(engine)) as c:
        settings=c.get('/api/settings').json()
        settings.update(llm_enabled=True,llm_model='test-model')
        c.put('/api/settings',json=settings).raise_for_status()
        yield c,engine
    engine.dispose()


def model(settings,system,text,**kwargs):
    data=json.loads(text)
    if 'titles' in data:
        return json.dumps({'template':'按业务功能概括，以修复、优化、沟通开头。'})
    if 'unlocked_tasks' in data:
        return json.dumps({'items':[{'id':t['id'],'eligible':True,'weight':2,'estimated_minutes':120,'reason':'参考消息时间、有效活动与复杂度'} for t in data['unlocked_tasks']]})
    assert '历史命名风格' in system
    return json.dumps({'items':[{'index':i,'title':item['category']+item['name']} for i,item in enumerate(data['items'])]})


def create(c,title='修复档案查询问题',minutes=0):
    r=c.post('/api/tasks',json={'date':'2026-10-24','title':title,'confirmed_minutes':minutes})
    r.raise_for_status()
    return r.json()['id']


def setup_style(c):
    settings=c.get('/api/settings').json();settings['style_template']='按业务功能概括命名'
    c.put('/api/settings',json=settings).raise_for_status()


def test_quick_manual_atomic_idempotent_preserves_pins(client,monkeypatch):
    c,engine=client
    setup_style(c)
    create(c);create(c,'原有沟通',30)
    monkeypatch.setattr(model_provider,'complete',model)
    body={'request_id':'quick-batch','manual_items':[{'category':'运维','name':'服务器部署','minutes':60},{'category':'沟通','name':'附件挂接需求','minutes':30}]}
    url='/api/reports/2026-10-24/estimate-time'
    result=c.post(url,json=body);result.raise_for_status()
    assert result.json()['fixed_minutes']==120
    assert c.post(url,json=body).json()==result.json()
    report=c.get('/api/reports/2026-10-24').json()
    assert len(report['tasks'])==4 and report['total_minutes']==480 and not report['confirmed']
    task=next(t for t in report['tasks'] if t['title']=='运维服务器部署')
    assert task['confirmed_minutes']==60 and task['time_locked'] and task['origin']=='manual'
    body['manual_items'][0]['minutes']=90
    assert c.post(url,json=body).status_code==400


def test_quick_failure_and_target_overflow_leave_no_new_rows(client,monkeypatch):
    c,_=client;setup_style(c);create(c)
    monkeypatch.setattr(model_provider,'complete',lambda *a,**k:'invalid JSON')
    url='/api/reports/2026-10-24/estimate-time'
    body={'request_id':'failure','manual_items':[{'category':'沟通','name':'客户需求','minutes':60}]}
    assert c.post(url,json=body).status_code==400
    assert len(c.get('/api/tasks?date=2026-10-24').json())==1
    body['manual_items'][0]['minutes']=481
    assert c.post(url,json=body).status_code==400
    assert c.get('/api/reports/2026-10-24').json()['total_minutes']==0


def test_quick_failed_estimation_after_successful_naming_is_atomic(client,monkeypatch):
    c,_=client;setup_style(c);create(c)
    def fail(settings,system,text,**kwargs):
        if 'unlocked_tasks' in json.loads(text):
            raise model_provider.ModelError('服务暂时不可用')
        return model(settings,system,text,**kwargs)
    monkeypatch.setattr(model_provider,'complete',fail)
    r=c.post('/api/reports/2026-10-24/estimate-time',json={'request_id':'fail-estimate','manual_items':[{'category':'运维','name':'部署','minutes':60}]})
    assert r.status_code==400
    assert len(c.get('/api/tasks?date=2026-10-24').json())==1


def test_quick_stale_model_does_not_overwrite_human(client,monkeypatch):
    c,_=client;setup_style(c);task=create(c)
    def edit(settings,system,text,**kwargs):
        if 'unlocked_tasks' in json.loads(text):
            c.patch('/api/tasks/'+task,json={'confirmed_minutes':75}).raise_for_status()
        return model(settings,system,text,**kwargs)
    monkeypatch.setattr(model_provider,'complete',edit)
    r=c.post('/api/reports/2026-10-24/estimate-time',json={'request_id':'stale','manual_items':[{'category':'沟通','name':'需求','minutes':60}]})
    assert r.status_code==400
    tasks=c.get('/api/tasks?date=2026-10-24').json()
    assert len(tasks)==1 and tasks[0]['confirmed_minutes']==75


def upload(c,name,data):
    return c.post('/api/style/import',json={'filename':name,'content_base64':base64.b64encode(data).decode(),'since':'2026-09-15','include_git':False})


def test_json_csv_excel_history_dedup_first_template_persistence(client,monkeypatch):
    c,engine=client;calls=[]
    def tracked(*a,**kw):
        calls.append(json.loads(a[2]));return model(*a,**kw)
    monkeypatch.setattr(model_provider,'complete',tracked)
    rows=[{'title':'修复档案查询问题','project':'2026','date':'2026-10-20'}, {'title':'旧记录','project':'2026','date':'2026-09-01'}]
    r=upload(c,'history.json',json.dumps(rows,ensure_ascii=False).encode());r.raise_for_status()
    assert r.json()['stored_records']==2 and r.json()['selected_records']==1 and r.json()['style']['generated']
    r=upload(c,'history.csv','标题,项目,日期\n修复档案查询问题,2026,2026-10-20\n'.encode('utf-8-sig'));r.raise_for_status()
    assert r.json()['added']==0 and r.json()['updated']==1
    workbook=Workbook();sheet=workbook.active;sheet.append(['标题','项目','日期']);sheet.append(['修复档案查询问题','2026','2026-10-20']);buffer=io.BytesIO();workbook.save(buffer)
    r=upload(c,'history.xlsx',buffer.getvalue());r.raise_for_status()
    assert r.json()['added']==0 and r.json()['stored_records']==2
    assert len(calls)==1
    with engine.connect() as conn:
        settings=service.get_settings(conn)
        assert '按业务功能' in prompts.prompt(settings,'task_writer')
        assert conn.execute(select(db.history.c.title)).fetchall()
    assert c.get('/api/history').json()['total']==2
    assert c.get('/api/tasks?date=2026-10-24').json()==[]
    assert c.get('/api/history?offset=-1').status_code==422


def test_style_generation_failure_keeps_import_and_manual_template(client,monkeypatch):
    c,_=client
    monkeypatch.setattr(model_provider,'complete',lambda *a,**kw:'bad response')
    r=upload(c,'history.json',b'[{"title":"Fix business","date":"2026-10-20"}]');r.raise_for_status()
    assert not r.json()['style']['generated'] and c.get('/api/history').json()['total']==1
    setup_style(c)
    upload(c,'history.json',b'[{"title":"Fix another","date":"2026-10-21"}]').raise_for_status()
    assert c.get('/api/settings').json()['style_template']=='按业务功能概括命名'
    monkeypatch.setattr(model_provider,'complete',model)
    c.post('/api/style/generate',json={}).raise_for_status()
    assert '修复、优化' in c.get('/api/settings').json()['style_template']


def test_external_record_update_not_duplicate(client,monkeypatch):
    c,_=client;setup_style(c)
    for title in ('原始标题','更新后的标题'):
        upload(c,'history.json',json.dumps([{'id':42,'project':'owner/repo','title':title,'date':'2026-10-20'}]).encode()).raise_for_status()
    result=c.get('/api/history').json()
    assert result['total']==1 and result['items'][0]['title']=='更新后的标题'


def test_gitee_fetch_pagination_filter_and_error_atomic(client,monkeypatch):
    c,engine=client;setup_style(c)
    original=httpx.Client
    monkeypatch.setattr(gitee,'token',lambda:'secret-test-token')
    pages=[]
    def handler(request):
        assert request.url.params['access_token']=='secret-test-token'
        if request.url.path.endswith('/user'):
            return httpx.Response(200,json={'login':'tester'})
        assert request.url.params['state']=='all' and request.url.params['filter']=='all'
        page=int(request.url.params['page']);pages.append(page)
        records=[{'id':i,'title':f'修复问题{i}','created_at':'2026-10-20T00:00:00Z','repository':{'full_name':'tester/repo'}} for i in range(100)] if page==1 else []
        return httpx.Response(200,json=records)
    monkeypatch.setattr(gitee.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    # Direct integration avoids patching TestClient's own httpx.Client construction.
    r=gitee.sync(engine)
    assert pages==[1,2] and r['added']==100 and r['account']['login']=='tester'
    assert gitee.sync(engine)['added']==0
    with engine.connect() as conn:
        assert style_count(conn)==100
        assert not gitee.due(conn)
    def denied(request):
        return httpx.Response(401,json={'message':'contains secret-test-token'})
    monkeypatch.setattr(gitee.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(denied),**kw))
    with pytest.raises(ValueError,match='HTTP 401') as err:
        gitee.sync(engine)
    assert 'secret-test-token' not in str(err.value)
    with engine.connect() as conn:
        assert style_count(conn)==100


def style_count(conn):
    return style_reference.listing(conn)['total']


def test_gitee_repository_filter_and_repeated_page_rejects(monkeypatch):
    monkeypatch.setattr(gitee,'token',lambda:'token')
    original=httpx.Client
    def handler(request):
        if request.url.path.endswith('/user'):
            return httpx.Response(200,json={'login':'tester'})
        return httpx.Response(200,json=[{'id':1,'title':'one','repository':{'full_name':'other/repo'}}])
    monkeypatch.setattr(gitee.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    rows,user=gitee.fetch(GiteeConfig(repository='tester/repo'))
    assert not rows and user['login']=='tester'


def test_bad_upload_does_not_wipe_history(client):
    c,_=client
    for name,content in [('a.csv',b'not,a,header\n1,2,3'),('a.xlsx',b'invalid'),('a.xls',b'invalid')]:
        r=upload(c,name,content)
        assert r.status_code in (200,400)
    assert c.get('/api/history').json()['total']==0


def test_gitee_partial_failure_never_commits_and_logs_redact(client,monkeypatch,caplog):
    import logging
    c,engine=client;setup_style(c)
    original=httpx.Client
    monkeypatch.setattr(gitee,'token',lambda:'never-log-this')
    def handler(request):
        if request.url.path.endswith('/user'):
            return httpx.Response(200,json={'login':'tester'})
        if request.url.params['page']=='2':
            return httpx.Response(429,json={'message':'rate limited'})
        return httpx.Response(200,json=[{'id':i,'title':str(i)} for i in range(100)])
    monkeypatch.setattr(gitee.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with caplog.at_level(logging.INFO,logger='httpx'):
        with pytest.raises(ValueError,match='HTTP 429'):
            gitee.sync(engine)
    assert 'never-log-this' not in caplog.text and '[redacted]' in caplog.text
    with engine.connect() as conn:
        assert style_count(conn)==0


def test_gitee_repeated_full_page_aborts(monkeypatch):
    original=httpx.Client
    monkeypatch.setattr(gitee,'token',lambda:'token')
    def handler(request):
        if request.url.path.endswith('/user'):
            return httpx.Response(200,json={'login':'tester'})
        return httpx.Response(200,json=[{'id':i,'title':str(i)} for i in range(100)])
    monkeypatch.setattr(gitee.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with pytest.raises(ValueError,match='重复分页'):
        gitee.fetch(GiteeConfig())


def test_auto_sync_due_and_failure_backoff(client,monkeypatch):
    c,engine=client
    monkeypatch.setattr(gitee,'token',lambda:'configured')
    with engine.begin() as conn:
        settings=service.get_settings(conn);settings.gitee.enabled=True
        service.save_settings(conn,settings)
        assert gitee.due(conn)
    def fail(config):
        raise ValueError('Gitee HTTP 503')
    monkeypatch.setattr(gitee,'fetch',fail)
    gitee.scheduled_sync(engine)
    with engine.connect() as conn:
        assert not gitee.due(conn)
        assert 'HTTP 503' in gitee.status(conn)['error']
