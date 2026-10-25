import json
from datetime import datetime,timedelta,timezone
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select,update
from awt import db,service,gitee_enterprise as ge
from awt.app import create_app
from awt.models import GiteeProjectRule


CATALOG=[{'id':11,'name':'上海工程技术大学档案系统','ident':'SHGC','status':0},
         {'id':22,'name':'2026档案系统','ident':'2026','status':0},
         {'id':33,'name':'公共接口集成','ident':'API','status':0}]


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(ge,'token',lambda:'enterprise-test-token')
    monkeypatch.setattr(ge,'programs',lambda enterprise_id:CATALOG)
    monkeypatch.setattr(ge,'issue_types',lambda enterprise_id,program_id:[{'id':9,'name':'任务','category':'task'}])
    engine=db.make_engine(tmp_path/'enterprise.db')
    with TestClient(create_app(engine)) as c:
        c.put('/api/gitee/enterprise/config',json={'enterprise_id':7,'rules':[],'include_details':True}).raise_for_status()
        yield c,engine
    engine.dispose()


def task(c,title='【上海工程】修复附件管理全文提取问题',minutes=90):
    r=c.post('/api/tasks',json={'date':'2026-10-24','title':title,'details':'修复并验证附件全文提取功能','confirmed_minutes':minutes,'project':'archive-system-2026'})
    r.raise_for_status();return r.json()['id']


def preview(c,ids=None):
    r=c.post('/api/gitee/enterprise/preview',json={'date':'2026-10-24','task_ids':ids or []})
    r.raise_for_status();return r.json()


def publish(c,p,ids=None,program=11,**extra):
    return c.post('/api/gitee/enterprise/publish',json={'batch_id':p['batch_id'],'confirmed':True,'targets':[{'task_id':i['task_id'],'program_id':program,'issue_type_id':9} for i in p['items'] if ids is None or i['task_id'] in ids],**extra})


def test_project_matching_prefix_exact_keyword_and_ambiguity():
    value={'title':'【上海工程】修复附件全文提取问题','project':'local'}
    assert ge.match_program(value,CATALOG,[])[0]==11
    rules=[GiteeProjectRule(pattern='上海工程',mode='prefix',program_id=11),
           GiteeProjectRule(pattern='全文提取',mode='contains',program_id=33),
           GiteeProjectRule(pattern=value['title'],mode='exact',program_id=22)]
    assert ge.match_program(value,CATALOG,rules)[0]==22
    assert ge.match_program(value,CATALOG,rules[:2])[0]==33
    ambiguous=CATALOG+[{'id':44,'name':'上海工程技术大学二期','ident':'SHGC2'}]
    assert ge.match_program(value,ambiguous,[])[0] is None
    assert ge.match_program(value,ambiguous,rules[:1])[0]==11
    assert ge.match_program(value,CATALOG,[GiteeProjectRule(pattern='上海工程',program_id=99)])[0] is None
    assert ge.match_program(value,CATALOG,[GiteeProjectRule(pattern='上海工程',program_id=11),GiteeProjectRule(pattern='上海工程',program_id=22)])[0] is None
    assert ge.match_program({'title':'测试','project':''},CATALOG,[])[0] is None


def test_save_mapping_preview_and_publish_once_without_changing_worklog(client,monkeypatch):
    c,engine=client
    first=task(c);second=task(c,'【2026】新增附件批量打印功能',120)
    config={'enterprise_id':7,'include_details':True,'rules':[{'pattern':'2026','mode':'prefix','program_id':11}]}
    c.put('/api/gitee/enterprise/config',json=config).raise_for_status()
    posted=[]
    def create(method,path,params=None,payload=None,credential=None):
        assert method=='POST' and path=='/7/issues'
        assert payload['program_id']==11 and payload['issue_type_id']==9
        assert payload['duration'] in (90,120)
        assert payload['plan_started_at']=='2026-10-24T00:00:00'
        assert 'awt-work-item:' in payload['description'] and 'raw_ref' not in payload
        assert 'state' not in payload and 'finished_at' not in payload
        posted.append(payload)
        return {'id':100+len(posted),'issue_url':f'https://gitee.com/enterprises/demo/issues/{100+len(posted)}'}
    monkeypatch.setattr(ge,'request',create)
    p=preview(c)
    assert all(i['program_id']==11 for i in p['items']) and posted==[]
    r=publish(c,p);r.raise_for_status()
    assert r.json()['created']==2 and len(posted)==2
    publish(c,p).raise_for_status()
    assert len(posted)==2
    q=preview(c)
    assert all(i['existing']['status']=='created' for i in q['items'])
    report=c.get('/api/reports/2026-10-24').json()
    assert report['total_minutes']==210 and not report['confirmed'] and len(report['tasks'])==2


def test_publish_requires_confirmation_and_rejects_tampered_targets(client,monkeypatch):
    c,_=client;task(c);p=preview(c)
    monkeypatch.setattr(ge,'request',lambda *a,**kw:pytest.fail('must not create'))
    assert publish(c,p,confirmed=False).status_code==422
    assert publish(c,p,program=999).status_code==400
    assert c.post('/api/gitee/enterprise/publish',json={'batch_id':p['batch_id'],'confirmed':True,'targets':[{'task_id':'unrelated','program_id':11}]}).status_code==400
    t=p['items'][0]['task_id']
    assert c.post('/api/gitee/enterprise/publish',json={'batch_id':p['batch_id'],'confirmed':True,'targets':[{'task_id':t,'program_id':11},{'task_id':t,'program_id':11}]}).status_code==400


def test_human_edit_and_credential_change_invalidate_preview(client,monkeypatch):
    c,_=client;identifier=task(c);p=preview(c)
    monkeypatch.setattr(ge,'request',lambda *a,**kw:pytest.fail('must not create'))
    c.patch('/api/tasks/'+identifier,json={'confirmed_minutes':120}).raise_for_status()
    assert publish(c,p).status_code==400
    q=preview(c)
    monkeypatch.setattr(ge,'token',lambda:'different-credential')
    assert publish(c,q).status_code==400


def test_preview_expiry_and_type_validation(client,monkeypatch):
    c,engine=client;task(c);p=preview(c)
    with engine.begin() as conn:
        snap=conn.execute(select(db.gitee_batches.c.snapshot).where(db.gitee_batches.c.id==p['batch_id'])).scalar_one()
        snap['created_at']=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        conn.execute(update(db.gitee_batches).where(db.gitee_batches.c.id==p['batch_id']).values(snapshot=snap))
    assert publish(c,p).status_code==400
    q=preview(c)
    monkeypatch.setattr(ge,'issue_types',lambda *a:[])
    assert publish(c,q).status_code==400


def test_uncertain_write_is_never_resent_even_in_new_preview(client,monkeypatch):
    c,_=client;task(c);posted=[]
    def timeout(*a,**kw):
        posted.append(a)
        raise ge.EnterpriseError('读取超时，结果未知',uncertain=True)
    monkeypatch.setattr(ge,'request',timeout)
    p=preview(c);r=publish(c,p);r.raise_for_status()
    assert r.json()['uncertain']==1
    publish(c,p).raise_for_status();publish(c,preview(c)).raise_for_status()
    assert len(posted)==1


def test_partial_success_retry_only_failed_item(client,monkeypatch):
    c,_=client;task(c);task(c,'【上海工程】沟通附件需求',30);posted=[]
    def create(method,path,params=None,payload=None,credential=None):
        posted.append(payload['title'])
        if len(posted)==2:raise ge.EnterpriseError('HTTP 422：缺少必填字段')
        return {'id':100+len(posted),'issue_url':'https://gitee.com/enterprises/demo/issues/100'}
    monkeypatch.setattr(ge,'request',create)
    p=preview(c);r=publish(c,p).json()
    assert r['created']==1 and r['failed']==1
    r=publish(c,p).json()
    assert r['created']==2 and len(posted)==3


def test_network_wait_does_not_hold_sqlite_writer_lock(client,monkeypatch):
    c,_=client;identifier=task(c);p=preview(c)
    def create(*a,**kw):
        c.patch('/api/tasks/'+identifier,json={'title':'人工修改后的标题'}).raise_for_status()
        return {'id':8,'issue_url':'https://gitee.com/enterprises/demo/issues/8'}
    monkeypatch.setattr(ge,'request',create)
    publish(c,p).raise_for_status()
    assert c.get('/api/tasks?date=2026-10-24').json()[0]['title']=='人工修改后的标题'


def test_http_enterprise_contract_paging_post_and_secret_redaction(monkeypatch,caplog):
    import logging
    monkeypatch.setattr(ge,'token',lambda:'enterprise-private-secret')
    original=httpx.Client;seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.host=='api.gitee.com'
        if request.method=='GET':
            assert request.url.params['access_token']=='enterprise-private-secret'
            return httpx.Response(200,json=[{'id':1,'name':'Company','path':'company'}])
        data=json.loads(request.content)
        assert data['access_token']=='enterprise-private-secret' and data['program_id']==11
        return httpx.Response(201,json={'id':9})
    monkeypatch.setattr(ge.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    with caplog.at_level(logging.INFO,logger='httpx'):
        assert ge.enterprises()[0]['id']==1
        assert ge.request('POST','/1/issues',payload={'title':'work','program_id':11})['id']==9
    assert 'enterprise-private-secret' not in caplog.text
    assert len(seen)==2


def test_http_server_failure_is_uncertain_and_auth_failure_is_retryable(monkeypatch):
    monkeypatch.setattr(ge,'token',lambda:'token')
    original=httpx.Client
    for code,uncertain in [(503,True),(403,False),(422,False)]:
        monkeypatch.setattr(ge.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(lambda req:httpx.Response(code,json={})),**kw))
        with pytest.raises(ge.EnterpriseError) as error:
            ge.request('POST','/1/issues',payload={'title':'work'})
        assert error.value.uncertain is uncertain


def test_catalog_excludes_closed_and_supports_pagination(monkeypatch):
    pages=[]
    def request(method,path,params=None,payload=None):
        pages.append(params['page'])
        if params['page']==1:return [{'id':i+1,'name':f'Project {i}','status':0,'ident':str(i)} for i in range(100)]
        return [{'id':200,'name':'Closed','status':2}]
    monkeypatch.setattr(ge,'request',request)
    catalog=ge.programs(7)
    assert pages==[1,2] and len(catalog)==100


def test_uncertain_result_can_link_only_matching_remote_work_item(client,monkeypatch):
    c,_=client;identifier=task(c);p=preview(c)
    def timeout(*a,**kw):raise ge.EnterpriseError('结果未知',uncertain=True)
    monkeypatch.setattr(ge,'request',timeout)
    publish(c,p).raise_for_status()
    marker='<!-- awt-work-item:'+ge.stable('awt-work-item',7,identifier)+' -->'
    def lookup(method,path,params=None,payload=None):
        assert method=='GET' and path=='/7/issues/123'
        return {'id':123,'program_id':11,'description':marker,'issue_url':'https://gitee.com/enterprises/demo/issues/123'}
    monkeypatch.setattr(ge,'request',lookup)
    r=c.post('/api/gitee/enterprise/resolve',json={'enterprise_id':7,'task_id':identifier,'issue_id':123})
    r.raise_for_status();assert r.json()['status']=='created'
    monkeypatch.setattr(ge,'request',lambda *a,**kw:pytest.fail('already linked, must not post'))
    publish(c,p).raise_for_status()


def test_invalid_mapping_prefix_rejected_without_overwriting_config(client):
    c,_=client
    r=c.put('/api/gitee/enterprise/config',json={'enterprise_id':7,'rules':[{'pattern':'【】','program_id':11}]})
    assert r.status_code==422
    assert c.get('/api/settings').json()['gitee_enterprise']['rules']==[]
