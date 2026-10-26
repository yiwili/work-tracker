import json
import re
from decimal import Decimal, ROUND_FLOOR
from sqlalchemy import select, update

from . import activity_time, db, model_provider, prompts
from .collectors import stable
from .privacy import redact
from .models import AllocationInput, TaskInput

ALLOCATION_STEP = 30


def state_hash(tasks, settings):
    return stable(ALLOCATION_STEP,settings.target_minutes,settings.step_minutes,settings.analysis_sources,
                  settings.git_author.model_dump() if settings.git_author else None,{k:v.model_dump() for k,v in settings.git_author_overrides.items()},
                  settings.llm_enabled,settings.llm_provider,settings.llm_model,settings.llm_endpoint,settings.llm_protocol,
                  sorted((t['id'],t['title'],t['details'],t['project'],t['category'],t['status'],t['started_at'],t['ended_at'],t['confirmed_minutes'],t['time_locked'],t['time_source'],sorted(e['id'] for e in t['evidence'])) for t in tasks))


def distribute(remaining, items, step):
    """Exact integer apportionment; step-sized units plus an explicit residual-minute remainder."""
    if remaining<0:
        raise ValueError('固定工时已超过每日目标，请先调整')
    if not items:
        if remaining:
            raise ValueError('没有可分配的真实工作项，不能新增虚构事项补足目标')
        return {}
    units,residual=divmod(remaining,step)
    weight_sum=sum(Decimal(str(item['weight'])) for item in items)
    if weight_sum<=0:
        raise ValueError('没有有效估算权重')
    quotas={item['id']:Decimal(units)*Decimal(str(item['weight']))/weight_sum for item in items}
    floors={key:int(value.to_integral_value(rounding=ROUND_FLOOR)) for key,value in quotas.items()}
    priority=sorted(items,key=lambda item:(-(quotas[item['id']]-floors[item['id']]),-item['weight'],item['id']))
    for item in priority[:units-sum(floors.values())]:
        floors[item['id']]+=1
    result={key:value*step for key,value in floors.items()}
    if residual:
        result[priority[0]['id']]+=residual
    return result


def parse_estimates(text, ids):
    try:
        text=re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip())
        data=json.loads(text)
        items=data['items']
        if not isinstance(items,list) or len(items)!=len(ids) or any(not isinstance(i,dict) or not isinstance(i.get('id'),str) for i in items) or {i.get('id') for i in items}!=set(ids):
            raise ValueError()
        for item in items:
            if type(item.get('eligible')) is not bool or type(item.get('weight')) is not int:
                raise ValueError()
            if not 0<=item['weight']<=10 or (item['eligible'] and not item['weight']):
                raise ValueError()
            minutes=item.get('estimated_minutes')
            if minutes is not None and (type(minutes) is not int or not 1<=minutes<=1440):
                raise ValueError()
            item['estimated_minutes']=((minutes+ALLOCATION_STEP-1)//ALLOCATION_STEP)*ALLOCATION_STEP if minutes is not None else None
            if not isinstance(item.get('reason'),str) or len(item['reason'])>3000:
                raise ValueError()
        return data
    except (ValueError,KeyError,TypeError):
        raise model_provider.ModelError('工时估算格式无效，所有工时保持原值；请重试') from None


def estimate(conn, day, request=None):
    from .daily_analysis import day_rows,input_hash
    from .service import get_settings,invalidate,list_tasks,now,upsert,save_task
    request=request or AllocationInput()
    request_key=stable(str(day),request.request_id) if request.request_id else ''
    request_hash=stable(request.model_dump(mode='json'))
    if request_key:
        prior=conn.execute(select(db.allocation_requests).where(db.allocation_requests.c.id==request_key)).mappings().first()
        if prior:
            if prior['payload_hash']!=request_hash:
                raise ValueError('请求编号已使用，请重新打开工时弹窗')
            return prior['result']
    settings=get_settings(conn)
    if not settings.llm_enabled:
        raise ValueError('请先接通并开启AI模型，再估算工时')
    tasks=list_tasks(conn,day)
    if not tasks and not request.manual_items:
        raise ValueError('请先生成或补录真实工作项，再估算工时')
    locked=[t for t in tasks if t['time_locked']]
    unlocked=[t for t in tasks if not t['time_locked']]
    fixed=sum(t['confirmed_minutes'] for t in locked)+sum(i.minutes for i in request.manual_items)
    if fixed>settings.target_minutes:
        raise ValueError('固定工时已超过每日目标，请先调整；未改动任何工作项')
    remaining=settings.target_minutes-fixed
    if remaining%ALLOCATION_STEP:
        raise ValueError(f'AI工时只能按0.5小时分配：固定人工工时为{fixed}分钟，剩余{remaining}分钟不是30分钟的整数倍；请调整人工工时或每日目标后重试，原记录未改动')
    if not remaining:
        if any(t['confirmed_minutes'] for t in unlocked):
            raise ValueError('固定工时已占满目标，请先人工核对其余已有分配，系统不会静默清零')
        if not request.manual_items:
            return {'allocated':0,'fixed_minutes':fixed,'total_minutes':fixed,'warnings':['固定工时已达到目标，无需AI分配。']}
    if remaining and not unlocked:
        raise ValueError('所有工作项均已固定，请补录真实工作或解锁需要AI估算的工作项')
    timeline=day_rows(conn,day,settings)
    initial_hash=state_hash(tasks,settings)
    scope_hash=input_hash(timeline,settings)
    prepared=[]
    if request.manual_items:
        text=model_provider.complete(settings,prompts.prompt(settings,'manual_writer'),json.dumps({'items':[{**i.model_dump(),'name':redact(i.name,settings.redact_patterns)} for i in request.manual_items],'style_examples':settings.title_examples},ensure_ascii=False),max_tokens=4000)
        try:
            named=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip()))['items']
            if not isinstance(named,list) or len(named)!=len(request.manual_items) or any(not isinstance(i,dict) or type(i.get('index')) is not int or not isinstance(i.get('title'),str) for i in named) or {i['index'] for i in named}!=set(range(len(request.manual_items))):
                raise ValueError()
            names={i['index']:i['title'] for i in named}
            for index,item in enumerate(request.manual_items):
                prepared.append(TaskInput(date=day,title=names[index],details=f'人工填写：{item.name}',category=item.category,
                    project=max((t['project'] for t in tasks),key=lambda p:sum(t['project']==p for t in tasks),default='未归属项目'),
                    confirmed_minutes=item.minutes,time_locked=True,status='attempted'))
        except (ValueError,KeyError,TypeError):
            raise model_provider.ModelError('人工事项命名格式无效，所有事项和工时保持原值；请重试') from None
    payload={'date':str(day),'target_minutes':settings.target_minutes,'manual_minutes':fixed,'remaining_target_minutes':remaining,'allocation_step_minutes':ALLOCATION_STEP,
             'locked_tasks':[{'id':t['id'],'title':redact(t['title'],settings.redact_patterns),'minutes':t['confirmed_minutes']} for t in locked]+[{'id':f'quick-{i}','title':p.title,'minutes':p.confirmed_minutes} for i,p in enumerate(prepared)],
             'unlocked_tasks':[{'id':t['id'],'title':redact(t['title'],settings.redact_patterns),'details':redact(t['details'],settings.redact_patterns),
                                'project':t['project'],'category':t['category'],'status':t['status'],'source':t['origin'],
                                'evidence_count':len(t['evidence']),'timing_reference':activity_time.profile(t['evidence'],timeline,settings.activity_gap_minutes)} for t in unlocked]}
    if remaining:
        text=model_provider.complete(settings,prompts.prompt(settings,'time_estimator'),json.dumps(payload,ensure_ascii=False),max_tokens=8000)
        result=parse_estimates(text,[t['id'] for t in unlocked])
    else:
        result={'items':[],'note':'人工固定工时已达到目标。'}
    eligible=[i for i in result['items'] if i['eligible']]
    allocation=distribute(remaining,eligible,ALLOCATION_STEP)
    warnings=['这是按目标工时分配的AI建议，并非人工投入的精确测量；请审核后再确认日报。']
    estimates=sum(i['estimated_minutes'] or 0 for i in eligible)
    if estimates and remaining>estimates*1.5:
        warnings.append(f'剩余目标 {remaining} 分钟明显高于AI独立估算 {estimates} 分钟；请按实际投入核对，必要时保留差额。')
    conn.commit();conn.exec_driver_sql('BEGIN IMMEDIATE')
    current_settings=get_settings(conn);current_tasks=list_tasks(conn,day)
    if state_hash(current_tasks,current_settings)!=initial_hash or input_hash(day_rows(conn,day,current_settings),current_settings)!=scope_hash:
        raise ValueError('估算期间工作项、工时或对话范围发生变化，未覆盖人工修改；请重新估算')
    added=[save_task(conn,p) for p in prepared]
    for item in result['items']:
        minutes=allocation.get(item['id'],0)
        reason=redact(item['reason'],settings.redact_patterns)
        conn.execute(update(db.worklogs).where(db.worklogs.c.task_id==item['id']).values(confirmed_minutes=minutes,suggested_minutes=item.get('estimated_minutes'),time_locked=False,time_source='ai_estimate',time_reason=reason,is_confirmed=False))
    invalidate(conn,day)
    final_tasks=list_tasks(conn,day)
    snapshot={'estimated_at':now(),'model':model_provider.status(settings)['model'],'target_minutes':settings.target_minutes,'step_minutes':ALLOCATION_STEP,
              'fixed_minutes':fixed,'distributed_minutes':remaining,'independent_estimate_minutes':estimates,
              'note':redact(str(result.get('note',''))[:3000],settings.redact_patterns),'warnings':warnings,
              'items':[{**i,'reason':redact(i['reason'],settings.redact_patterns),'allocated_minutes':allocation.get(i['id'],0)} for i in result['items']],
              'state_hash':state_hash(final_tasks,settings),'scope_hash':scope_hash}
    upsert(conn,db.allocations,{'date':str(day),'snapshot':snapshot},'date')
    response={'allocated':len(allocation),'added_manual_ids':added,'fixed_minutes':fixed,'total_minutes':sum(t['confirmed_minutes'] for t in final_tasks),'warnings':warnings,'allocation':snapshot}
    if request_key:
        upsert(conn,db.allocation_requests,{'id':request_key,'payload_hash':request_hash,'result':response},'id')
    return response
