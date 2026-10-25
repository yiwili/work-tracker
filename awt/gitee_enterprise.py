"""Gitee Enterprise V8: explicit preview, project matching and guarded creation.

API contract: https://gitee.com/api/v8/swagger (host api.gitee.com/enterprises).
Only titles, reviewed descriptions, date and estimated minutes leave the app.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlparse
from uuid import uuid4
import httpx
from sqlalchemy import select
from . import db, gitee
from .collectors import stable
from .privacy import redact

API = 'https://api.gitee.com/enterprises'
publish_lock = Lock()


class EnterpriseError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain=uncertain


def token():
    return gitee.token('gitee-enterprise')


def request(method, path, params=None, payload=None, credential=None):
    key=credential or token()
    if not key:
        raise EnterpriseError('请先在偏好设置保存Gitee企业版Token；企业版凭据与普通仓库Token分开配置')
    try:
        with httpx.Client(timeout=httpx.Timeout(45,connect=15),trust_env=False,follow_redirects=False) as client:
            if method=='GET':
                response=client.get(API+path,params={**(params or {}),'access_token':key})
            else:
                response=client.post(API+path,json={**(payload or {}),'access_token':key})
        if response.status_code not in (200,201):
            descriptions={401:'Token无效或过期',403:'没有该企业、项目或创建工作项的权限',404:'企业或项目不存在',422:'企业工作流或必填字段校验未通过，请在Gitee核对项目要求',429:'请求过于频繁，请稍后重试'}
            raise EnterpriseError(f'Gitee企业版请求失败（HTTP {response.status_code}）：'+descriptions.get(response.status_code,'服务暂时不可用'),uncertain=method=='POST' and (response.status_code>=500 or response.status_code==408))
        return response.json()
    except (httpx.HTTPError,json.JSONDecodeError):
        raise EnterpriseError('Gitee企业版连接失败或返回格式无效；创建请求不会自动重发',uncertain=method=='POST') from None


def pages(path, params=None):
    rows=[];seen=set()
    for page in range(1,201):
        data=request('GET',path,{**(params or {}),'page':page,'per_page':100})
        if not isinstance(data,list) or any(not isinstance(r,dict) for r in data):
            raise EnterpriseError('Gitee企业版列表格式无效，请检查Token类型和企业版接口权限')
        fingerprint=stable(data)
        if data and fingerprint in seen:
            raise EnterpriseError('Gitee企业版返回重复分页，未使用不完整列表自动匹配')
        seen.add(fingerprint);rows.extend(data)
        if len(data)<100:
            return rows
    raise EnterpriseError('Gitee列表超过200页，未使用不完整列表自动匹配')


def enterprises():
    return [{'id':r['id'],'name':str(r.get('name','')),'path':str(r.get('path',''))} for r in pages('/list') if type(r.get('id')) is int and r['id']>0]


def programs(enterprise_id):
    rows=pages(f'/{enterprise_id}/programs',{'status':'0,1','sort':'name','direction':'asc'})
    return [{'id':r['id'],'name':str(r.get('name','')),'ident':str(r.get('ident') or ''),'status':r.get('status',0)} for r in rows if type(r.get('id')) is int and r['id']>0 and r.get('status',0)!=2]


def issue_types(enterprise_id,program_id):
    rows=pages(f'/{enterprise_id}/issue_types',{'program_id':program_id,'scope':'all'})
    return [{'id':r['id'],'name':str(r.get('title','')),'category':str(r.get('category',''))} for r in rows if type(r.get('id')) is int and r['id']>0]


def normalized(text):
    return re.sub(r'[\W_]+','',text.casefold())


def prefix(task):
    match=re.match(r'^\s*[【\[]([^】\]]+)[】\]]',task['title'])
    return match.group(1).strip() if match else task.get('project','').strip()


def match_program(task, catalog, rules):
    key=prefix(task);available={r['id'] for r in catalog};matches=[]
    for rule in rules:
        p=rule.pattern;hit=False
        if rule.mode=='exact': hit=task['title'].strip().casefold()==p.casefold()
        elif rule.mode=='contains': hit=p.casefold() in task['title'].casefold()
        else: hit=task['title'].casefold().startswith(p.casefold()) or normalized(key).startswith(normalized(p))
        if hit:
            matches.append(({'exact':3,'contains':2,'prefix':1}[rule.mode],len(p),rule.program_id))
    if matches:
        top=max((m[0],m[1]) for m in matches)
        ids={m[2] for m in matches if m[:2]==top}
        if len(ids)==1 and next(iter(ids)) in available:
            return next(iter(ids)),'已保存的标题/项目前缀映射'
        return None,'映射冲突或目标项目不可用，请手动选择'
    word=normalized(key)
    if not word:
        return None,'没有项目前缀，请手动选择'
    exact=[r for r in catalog if word in (normalized(r['name']),normalized(r['ident']))]
    candidates=exact or ([r for r in catalog if word in normalized(r['name']) or word in normalized(r['ident'])] if len(word)>=2 else [])
    if len(candidates)==1:
        return candidates[0]['id'],'根据项目前缀唯一匹配'
    return None,'匹配到多个项目，请选择' if candidates else '未匹配到项目，请选择或预设映射'


def task_snapshot(conn,day,ids=None):
    from .service import list_tasks
    rows=list_tasks(conn,day)
    if ids:
        if len(ids)!=len(set(ids)):
            raise ValueError('不能重复选择同一工作项')
        rows=[t for t in rows if t['id'] in ids]
        if len(rows)!=len(ids):
            raise ValueError('所选工作项已删除或不属于当前日期，请刷新')
    if not rows or len(rows)>200:
        raise ValueError('请选择1至200个工作项')
    keys=('id','date','title','details','project','category','status','confirmed_minutes')
    return [{k:t[k] for k in keys} for t in rows]


def status(conn):
    from .service import get_settings
    settings=get_settings(conn)
    common=[]
    for title in settings.title_examples:
        value=prefix({'title':title,'project':''})
        if value and value not in common and value not in ('新增','修复','优化','完善'):
            common.append(value)
    for project in conn.execute(select(db.tasks.c.project).where(db.tasks.c.deleted==False).order_by(db.tasks.c.date.desc()).limit(200)).scalars():
        if project and project!='未归属项目' and project not in common:
            common.append(project)
    cached=conn.execute(select(db.integrations.c.value).where(db.integrations.c.id==f'gitee-enterprise-programs:{settings.gitee_enterprise.enterprise_id}')).scalar() or {}
    return {'configured':bool(token()),'config':settings.gitee_enterprise.model_dump(mode='json'),'common_prefixes':common[:30],'programs':cached.get('items',[])}


def cache_programs(engine,enterprise_id):
    from .service import upsert,now
    catalog=programs(enterprise_id)
    with engine.begin() as conn:
        upsert(conn,db.integrations,{'id':f'gitee-enterprise-programs:{enterprise_id}','value':{'items':catalog,'fetched_at':now()}},'id')
    return catalog


def preview(engine,payload):
    from .service import get_settings,upsert,now
    with engine.connect() as conn:
        settings=get_settings(conn);config=settings.gitee_enterprise
        tasks=task_snapshot(conn,payload.date,payload.task_ids)
    if not config.enterprise_id:
        raise ValueError('请先在偏好设置选择Gitee企业并保存')
    catalog=programs(config.enterprise_id)
    items=[]
    for task in tasks:
        program_id,reason=match_program(task,catalog,config.rules)
        description=f"工作日期：{task['date']}\n预估工时：{task['confirmed_minutes']/60:g}h（待人工核对）"
        if config.include_details and task['details']:
            description+='\n\n'+redact(task['details'],settings.redact_patterns)
        marker=stable('awt-work-item',config.enterprise_id,task['id'])
        description+=f'\n\n<!-- awt-work-item:{marker} -->'
        items.append({'task_id':task['id'],'title':redact(task['title'],settings.redact_patterns),'description':description,
                      'minutes':task['confirmed_minutes'],'prefix':prefix(task),'program_id':program_id,'match_reason':reason})
    snapshot={'date':str(payload.date),'created_at':now(),'enterprise_id':config.enterprise_id,'account':stable(token()),
              'tasks':tasks,'config':config.model_dump(mode='json'),'programs':catalog,'items':items}
    batch_id=uuid4().hex
    with engine.begin() as conn:
        if task_snapshot(conn,payload.date,[t['id'] for t in tasks])!=tasks or get_settings(conn).gitee_enterprise!=config:
            raise ValueError('读取项目期间工作项或映射设置发生变化，请重新预览')
        upsert(conn,db.gitee_batches,{'id':batch_id,'snapshot':snapshot},'id')
        links={r['task_id']:dict(r) for r in conn.execute(select(db.gitee_links).where(db.gitee_links.c.enterprise_id==config.enterprise_id)).mappings()}
    return {'batch_id':batch_id,'date':str(payload.date),'enterprise_id':config.enterprise_id,'programs':catalog,
            'items':[{**i,'existing':links.get(i['task_id'])} for i in items]}


def safe_url(value):
    if not isinstance(value,str):return ''
    parsed=urlparse(value)
    return value if parsed.scheme=='https' and parsed.hostname in ('gitee.com','e.gitee.com') and not parsed.username and not parsed.password else ''


def publish(engine,payload):
    from .service import get_settings,upsert,now
    if not publish_lock.acquire(blocking=False):
        raise ValueError('Gitee创建正在进行，请等待完成；不会重复发送')
    try:
        publish_key=token()
        with engine.connect() as conn:
            snapshot=conn.execute(select(db.gitee_batches.c.snapshot).where(db.gitee_batches.c.id==payload.batch_id)).scalar()
            if not snapshot:
                raise ValueError('预览不存在，请重新预览')
            if datetime.now(timezone.utc)-datetime.fromisoformat(snapshot['created_at'])>timedelta(minutes=30):
                raise ValueError('预览已过期，请重新预览后提交')
            if stable(publish_key)!=snapshot['account'] or get_settings(conn).gitee_enterprise.model_dump(mode='json')!=snapshot['config']:
                raise ValueError('企业凭据或映射设置已变更，请重新预览')
            if task_snapshot(conn,snapshot['date'],[t['id'] for t in snapshot['tasks']])!=snapshot['tasks']:
                raise ValueError('预览后工作内容或工时已修改，请重新预览后提交')
        ids=[t.task_id for t in payload.targets]
        if len(ids)!=len(set(ids)) or any(t not in {i['task_id'] for i in snapshot['items']} for t in ids):
            raise ValueError('提交工作项与预览不一致')
        enterprise_id=snapshot['enterprise_id'];catalog=programs(enterprise_id)
        available={p['id'] for p in catalog}
        if any(t.program_id not in available for t in payload.targets):
            raise ValueError('目标项目不在可访问列表中，请重新选择')
        types={pid:issue_types(enterprise_id,pid) for pid in {t.program_id for t in payload.targets}}
        if any(t.program_id not in available or (t.issue_type_id is not None and t.issue_type_id not in {r['id'] for r in types[t.program_id]}) for t in payload.targets):
            raise ValueError('目标项目或工作项类型不在可访问列表中，请重新选择')
        results=[]
        for target in payload.targets:
            item=next(i for i in snapshot['items'] if i['task_id']==target.task_id)
            identity=stable(enterprise_id,target.task_id)
            with engine.connect() as conn:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
                prior=conn.execute(select(db.gitee_links).where(db.gitee_links.c.id==identity)).mappings().first()
                if prior and prior['status'] in ('created','pending','uncertain'):
                    results.append(dict(prior));conn.commit();continue
                if task_snapshot(conn,snapshot['date'],[t['id'] for t in snapshot['tasks']])!=snapshot['tasks'] or get_settings(conn).gitee_enterprise.model_dump(mode='json')!=snapshot['config'] or stable(token())!=snapshot['account']:
                    raise ValueError('提交期间工作项、凭据或设置发生变化；已创建项保留，其余未继续发送，请重新预览')
                record={'id':identity,'enterprise_id':enterprise_id,'task_id':target.task_id,'program_id':target.program_id,
                        'status':'pending','issue_id':'','url':'','error':'','updated_at':now()}
                upsert(conn,db.gitee_links,record,'id');conn.commit()
            body={'title':item['title'],'description':item['description'],'program_id':target.program_id,
                  'duration':item['minutes'],'plan_started_at':snapshot['date']+'T00:00:00','category':'task'}
            if target.issue_type_id is not None:
                body['issue_type_id']=target.issue_type_id
                body.pop('category',None)
            try:
                value=request('POST',f'/{enterprise_id}/issues',payload=body,credential=publish_key)
                if not isinstance(value,dict) or type(value.get('id')) is not int or value['id']<=0:
                    raise EnterpriseError('创建响应未提供有效工作项ID，请到Gitee核实，系统不会自动重发',uncertain=True)
                record.update(status='created',issue_id=str(value['id']),url=safe_url(value.get('issue_url')))
            except EnterpriseError as exc:
                record.update(status='uncertain' if exc.uncertain else 'failed',error=str(exc))
            record['updated_at']=now()
            with engine.begin() as conn:
                upsert(conn,db.gitee_links,record,'id')
            results.append(record)
        return {'items':results,'created':sum(r['status']=='created' for r in results),'failed':sum(r['status']=='failed' for r in results),
                'uncertain':sum(r['status'] in ('pending','uncertain') for r in results)}
    finally:
        publish_lock.release()


def resolve(engine,payload):
    """Link a verified remote result after an interrupted create; never resend it."""
    from .service import get_settings,upsert,now
    identity=stable(payload.enterprise_id,payload.task_id)
    with engine.connect() as conn:
        if get_settings(conn).gitee_enterprise.enterprise_id!=payload.enterprise_id:
            raise ValueError('企业配置已变更，请重新预览')
        record=conn.execute(select(db.gitee_links).where(db.gitee_links.c.id==identity)).mappings().first()
        if not record or record['status'] not in ('pending','uncertain'):
            raise ValueError('此工作项没有需要核对的创建请求')
        record=dict(record)
    value=request('GET',f'/{payload.enterprise_id}/issues/{payload.issue_id}')
    marker='<!-- awt-work-item:'+stable('awt-work-item',payload.enterprise_id,payload.task_id)+' -->'
    if not isinstance(value,dict) or value.get('id')!=payload.issue_id or value.get('program_id')!=record['program_id'] or not isinstance(value.get('description'),str) or marker not in value['description']:
        raise ValueError('该Gitee工作项与本次提交的项目或来源标记不匹配，未建立关联')
    record.update(status='created',issue_id=str(payload.issue_id),url=safe_url(value.get('issue_url')),error='',updated_at=now())
    with engine.begin() as conn:
        current=conn.execute(select(db.gitee_links).where(db.gitee_links.c.id==identity)).mappings().one()
        if current['status']=='created':return dict(current)
        upsert(conn,db.gitee_links,record,'id')
    return record
