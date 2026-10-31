import io
import json
import socket
import httpx
import pytest
from fastapi.testclient import TestClient
from rich.console import Console
from typer.testing import CliRunner
from awt import db,model_provider,gitee_enterprise as ge
from awt.app import create_app
from awt.cli import cli
from awt.terminal import Terminal,minutes
from awt.terminal_client import LocalApi,Backend,ApiError


class Script:
    def __init__(self,answers=(),confirms=()):
        self.answers=iter(answers);self.confirms=iter(confirms);self.hidden=[]
    def ask(self,label,default='',password=False):
        self.hidden.append(password)
        value=next(self.answers)
        return value if value else default
    def confirm(self,label,default=False):return next(self.confirms)


@pytest.fixture
def context(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path))
    monkeypatch.setattr(ge,'token',lambda:'terminal-fixture-token')
    monkeypatch.setattr(ge,'programs',lambda enterprise_id:[{'id':11,'name':'上海工程档案系统','ident':'SHGC'},{'id':22,'name':'2026档案系统','ident':'2026'}])
    monkeypatch.setattr(ge,'enterprises',lambda:[{'id':7,'name':'测试企业','path':'test'}])
    monkeypatch.setattr(ge,'issue_types',lambda *a:[{'id':9,'name':'任务','category':'task'}])
    engine=db.make_engine(tmp_path/'work.db')
    with TestClient(create_app(engine)) as client:
        settings=client.get('/api/settings').json();settings.update(llm_enabled=True,llm_model='fixture',style_template='按业务功能命名')
        client.put('/api/settings',json=settings).raise_for_status()
        client.put('/api/gitee/enterprise/config',json={'enterprise_id':7,'rules':[]}).raise_for_status()
        yield client,LocalApi(client=client),tmp_path
    engine.dispose()


def terminal(context,script):
    output=io.StringIO()
    return Terminal(context[1],day='2026-10-24',console=Console(file=output,width=120,markup=False,highlight=False),ask=script.ask,confirm=script.confirm),output


def create(c,title='【上海工程】修复附件提取问题',duration=0):
    r=c.post('/api/tasks',json={'date':'2026-10-24','title':title,'confirmed_minutes':duration});r.raise_for_status()
    return r.json()['id']


def model(settings,system,text,**kw):
    data=json.loads(text)
    if 'unlocked_tasks' in data:
        return json.dumps({'items':[{'id':t['id'],'eligible':True,'weight':3,'estimated_minutes':35,'reason':'按功能范围估算'} for t in data['unlocked_tasks']]})
    return json.dumps({'items':[{'index':i,'title':t['category']+t['name']} for i,t in enumerate(data['items'])]})


def test_terminal_multiple_manual_items_preserve_durations(context):
    script=Script(['需求沟通','客户A','沟通','1','确认需求','服务部署','项目B','运维','0.5','验证状态'],[True,False])
    app,output=terminal(context,script);app.add()
    tasks=context[0].get('/api/tasks?date=2026-10-24').json()
    assert len(tasks)==2 and sum(t['confirmed_minutes'] for t in tasks)==90
    assert all(t['time_locked'] for t in tasks)
    assert '已保存' in output.getvalue()


def test_terminal_estimate_with_quick_items_is_half_hour_draft(context,monkeypatch):
    create(context[0]);monkeypatch.setattr(model_provider,'complete',model)
    script=Script(['沟通','附件需求','1','运维','服务部署','0.5'],[True,True,False])
    app,_=terminal(context,script);app.estimate()
    report=context[0].get('/api/reports/2026-10-24').json()
    assert report['total_minutes']==480 and len(report['tasks'])==3 and not report['confirmed']
    assert sorted(t['confirmed_minutes'] for t in report['tasks'] if t['time_locked'])==[30,60]
    assert all(t['confirmed_minutes']%30==0 for t in report['tasks'])


def test_terminal_failed_estimate_retains_same_request_and_inputs(context,monkeypatch):
    create(context[0]);script=Script(['沟通','需求','0.5'],[True,False,True])
    app,_=terminal(context,script)
    monkeypatch.setattr(model_provider,'complete',lambda *a,**kw:'invalid')
    with pytest.raises(ApiError):app.estimate()
    saved=app.estimate_draft
    assert saved and len(context[0].get('/api/tasks?date=2026-10-24').json())==1
    seen=[];original=app.post
    def post(path,body):seen.append(body);return original(path,body)
    app.post=post;monkeypatch.setattr(model_provider,'complete',model)
    app.estimate()
    assert seen[0]==saved[1] and app.estimate_draft is None
    assert len(context[0].get('/api/tasks?date=2026-10-24').json())==2


@pytest.mark.parametrize('accept',[False,True])
def test_terminal_gitee_requires_final_confirmation(context,monkeypatch,accept):
    create(context[0],duration=90);sent=[]
    def create_tool(tool,arguments,credential=None,uncertain=False):
        assert tool=='create_enterprise_issue' and arguments['enterprise_id']==7
        sent.append(arguments)
        return {'id':123,'ident':'I123'}
    monkeypatch.setattr(ge,'_tool',create_tool)
    script=Script([''],[False,False,accept]);app,output=terminal(context,script)
    app.publish()
    assert len(sent)==int(accept)
    assert 'Gitee企业工作项' in output.getvalue()
    if accept:assert sent[0]['duration']==90 and sent[0]['program_id']==11
    else:assert '已取消' in output.getvalue()


def test_terminal_can_preset_keyword_project_mapping(context):
    script=Script(['','1','添加','全文提取','包含','2','保存'])
    app,_=terminal(context,script);app.enterprise_settings()
    config=context[0].get('/api/settings').json()['gitee_enterprise']
    assert config['enterprise_id']==7 and config['rules']==[{'pattern':'全文提取','mode':'contains','program_id':22}]
    assert script.hidden[0] is True


def test_terminal_markdown_export_without_confirming_day(context):
    create(context[0]);path=context[2]/'daily.md'
    app,_=terminal(context,Script([str(path)],[False]));app.export()
    assert path.exists() and '草稿' in path.read_text(encoding='utf-8')
    assert not context[0].get('/api/reports/2026-10-24').json()['confirmed']


def test_terminal_model_key_is_hidden_and_not_printed(context,monkeypatch):
    saved=[];monkeypatch.setattr(model_provider,'save_key',lambda settings,key:saved.append(key))
    secret='terminal-private-test-value'
    script=Script(['模型','1','','',secret],[True,False])
    app,output=terminal(context,script);app.settings()
    assert saved==[secret] and script.hidden[-1] is True
    assert secret not in output.getvalue()


def test_terminal_custom_git_user_is_saved_without_changing_model(context):
    before=context[0].get('/api/settings').json()
    app,output=terminal(context,Script(['Git用户','统一自定义','提交用户','person@example.invalid']))
    app.settings()
    saved=context[0].get('/api/settings').json()
    assert saved['git_author']=={'name':'提交用户','email':'person@example.invalid'}
    assert saved['llm_model']==before['llm_model'] and 'Git用户已保存' in output.getvalue()


def test_terminal_selects_author_for_one_repository(context):
    app,_=terminal(context,Script(['Git用户','按仓库','1','2']))
    original=app.post
    def post(path,body):
        if path=='/git/identities':return [{'root':'D:/fixture-repo','project':'fixture','override_key':None,
            'detected':{'name':'Wrong','email':'wrong@example.invalid'},'selected':{'name':'Wrong','email':'wrong@example.invalid'},
            'authors':[{'name':'Chosen','email':'chosen@example.invalid'}]}]
        return original(path,body)
    app.post=post;app.settings()
    saved=context[0].get('/api/settings').json()
    assert saved['git_author'] is None
    assert saved['git_author_overrides']=={'D:/fixture-repo':{'name':'Chosen','email':'chosen@example.invalid'}}


def test_terminal_menu_catches_validation_error_and_can_exit(context):
    app,output=terminal(context,Script(['12','bad-date','0']))
    app.run()
    assert '操作未完成' in output.getvalue() and app.day=='2026-10-24'


@pytest.mark.parametrize('url',['https://github.com','http://example.com','http://u:p@localhost','http://127.0.0.1:0','http://127.0.0.1/api'])
def test_terminal_refuses_remote_or_credential_urls(url):
    with pytest.raises(ValueError):LocalApi(url)


def test_api_validation_error_never_echoes_token():
    secret='hidden-user-token'
    client=httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(422,json={'detail':[{'loc':['body','api_key'],'msg':'invalid length','input':secret}]})),base_url='http://localhost')
    api=LocalApi(client=client)
    with pytest.raises(ApiError) as exc:api.request('/gitee/enterprise/token','POST',{'api_key':secret})
    assert secret not in str(exc.value) and 'invalid length' in str(exc.value)
    client.close()


def test_existing_backend_is_reused_without_start_or_stop(monkeypatch):
    class Api:
        closed=False
        def request(self,path):return {'application':'ai-work-tracker'}
        def close(self):self.closed=True
    api=Api();backend=Backend(api)
    monkeypatch.setattr('awt.terminal_client.subprocess.Popen',lambda *a,**kw:pytest.fail('existing service must not be relaunched'))
    with backend as connected:assert connected is api and backend.process is None
    assert api.closed


def test_owned_backend_starts_and_stops_without_orphan(tmp_path,monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR',str(tmp_path))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    api=LocalApi(f'http://127.0.0.1:{port}');backend=Backend(api)
    with backend:
        assert api.request('/health')['application']=='ai-work-tracker'
        assert backend.process.poll() is None
    assert backend.process.poll() is not None
    with socket.socket() as sock:assert sock.connect_ex(('127.0.0.1',port))!=0


def test_cli_doctor_serializes_new_date_settings_and_terminal_exits(context):
    runner=CliRunner()
    result=runner.invoke(cli,['doctor'])
    assert result.exit_code==0,result.output
    assert json.loads(result.output)['settings']['gitee']['since']=='2026-09-15'
    assert runner.invoke(cli,['terminal','--help']).exit_code==0


@pytest.mark.parametrize(('value','expected'),[('1.5',90),('1.15',69),('0.5',30),('0',0)])
def test_terminal_hours_preserve_exact_user_minutes(value,expected):
    assert minutes(value)==expected
