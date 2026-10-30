"""Isolated browser-test server; never imports the user's real history."""
import json
import os
import sys
import tempfile
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from awt import db, service, model_provider, gitee
from awt import gitee_enterprise
from awt.models import CONVERSATION_SOURCES, Settings, SourceConfig
from awt.app import create_app
from awt.git_tools import resolve_project
import uvicorn

data = Path(tempfile.mkdtemp(prefix='awt-e2e-', dir=os.environ.get('AWT_TEST_TEMP')))
os.environ['AWT_DATA_DIR'] = str(data)
log = data / 'sample.jsonl'
rows = [
    {'type':'session_meta','payload':{'id':'browser-test-session','cwd':str(data/'demo-project')}},
    {'type':'response_item','timestamp':'2026-10-24T01:00:00Z','payload':{'type':'message','role':'user','content':'修复订单查询接口超时'}},
    {'type':'response_item','timestamp':'2026-10-24T01:01:00Z','payload':{'type':'function_call','name':'exec_command','arguments':'pytest','call_id':'test-call'}},
    {'type':'response_item','timestamp':'2026-10-24T01:03:00Z','payload':{'type':'function_call_output','call_id':'test-call','output':'3 passed in 1.23s'}},
]
log.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
engine = db.make_engine()
db.migrate(engine)
with engine.begin() as conn:
    service.save_settings(conn, Settings(codex=SourceConfig(paths=[str(log)]),**{key:SourceConfig(paths=[]) for key in CONVERSATION_SOURCES if key!='codex'},llm_enabled=True,llm_model='browser-test-model'))

# A real, isolated two-author repository for the Git user selection workflow.
git_repo=data/'git-user-project';git_repo.mkdir()
def fixture_git(*args,env=None):
    subprocess.run(['git','-C',str(git_repo),*args],check=True,capture_output=True,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
fixture_git('init');fixture_git('config','user.name','Local User');fixture_git('config','user.email','local@example.invalid')
for i,(name,email) in enumerate([('Local User','local@example.invalid'),('Colleague','coworker@example.invalid')]):
    (git_repo/'work.txt').write_text(str(i),encoding='utf-8');fixture_git('add','work.txt')
    fixture_git('commit','--author',f'{name} <{email}>','-m',f'{name} fixture work',env={**os.environ,'GIT_AUTHOR_DATE':f'2026-10-24T01:2{i}:00Z','GIT_COMMITTER_DATE':f'2026-10-24T01:2{i}:00Z'})
with engine.begin() as conn:service.upsert(conn,db.repositories,resolve_project(str(git_repo),Settings())[1],'id')


def fake_model(settings, system, text, **kwargs):
    value=json.loads(text) if text.startswith('{') else {}
    if 'required_refs' in value:
        return json.dumps({'work_refs':value['required_refs'],'excluded':[],'uncertain_refs':[]})
    if 'unlocked_tasks' in value:
        return json.dumps({'items':[{'id':t['id'],'eligible':True,'weight':3,'estimated_minutes':180,'difficulty':'中','confidence':'中','reason':'基于功能范围及有效活动估算'} for t in value['unlocked_tasks']],'note':'浏览器测试的目标分配建议'},ensure_ascii=False)
    if 'titles' in value:
        return json.dumps({'template':'以修复、优化、新增或沟通开头，按业务功能概括，技术原因放详情。'},ensure_ascii=False)
    if 'items' in value:
        return json.dumps({'items':[{'index':i,'title':item['category']+item['name']} for i,item in enumerate(value['items'])]},ensure_ascii=False)
    messages=value.get('conversations',[])
    prompts=[r for r in messages if r['kind']=='user_prompt']
    return json.dumps({'summary':'全天围绕接口超时问题进行修复和验证。','tasks':[{'title':'修复订单查询接口超时','details':'排查接口超时并执行测试。','project':'demo-project','category':'Bug 修复','refs':[r['ref'] for r in prompts]}] if prompts else []},ensure_ascii=False)


model_provider.complete=fake_model
enterprise_posts=[]
def fake_enterprise(method,path,params=None,payload=None,credential=None):
    if method=='POST':
        assert path=='/7/issues'
        enterprise_posts.append(payload)
        identifier=8000+len(enterprise_posts)
        return {'id':identifier,'issue_url':f'https://gitee.com/enterprises/e2e/issues/{identifier}'}
    if path=='/list':return [{'id':7,'name':'测试公司','path':'test-company'}]
    if path.endswith('/programs'):return [{'id':11,'name':'上海工程技术大学档案系统','ident':'SHGC','status':0},{'id':22,'name':'2026档案系统','ident':'2026','status':0}]
    if path.endswith('/issue_types'):return [{'id':9,'title':'任务','category':'task'}]
    raise AssertionError('Unexpected enterprise test route: '+path)
gitee_enterprise.request=fake_enterprise
gitee_enterprise.token=lambda:'isolated-enterprise-fixture-token'
gitee.fetch=lambda config:([{'id':987,'title':'优化档案检索功能','created_at':'2026-10-20T00:00:00Z','project_path':'tester/repo'}],{'login':'e2e-tester','name':'Test'})
model_provider.status=lambda settings,*_a:{'configured':True,'provider':settings.llm_provider,'endpoint':settings.llm_endpoint,'model':settings.llm_model,'error':''}
uvicorn.run(create_app(engine), host='127.0.0.1', port=8766)
