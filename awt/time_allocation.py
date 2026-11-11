import json
import re
from decimal import Decimal, ROUND_FLOOR
from sqlalchemy import select, update

from . import activity_time, analysis_progress, db, model_provider, prompts
from .collectors import stable
from .privacy import redact
from .models import AllocationInput, TaskInput

ALLOCATION_STEP = 30


def state_hash(tasks, settings, target_minutes=None):
    eff_target = target_minutes if target_minutes is not None else settings.target_minutes
    return stable(ALLOCATION_STEP,eff_target,settings.step_minutes,settings.analysis_sources,
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


def select_tasks(tasks, task_ids):
    """Return selected and preserved unlocked tasks; missing ids keep the old all-in behavior."""
    unlocked=[t for t in tasks if not t['time_locked']]
    if task_ids is None:
        return unlocked,[]
    wanted=set(task_ids)
    if wanted-{t['id'] for t in tasks}:
        raise ValueError('选择的工作项已不存在，请重新选择')
    selected=[t for t in unlocked if t['id'] in wanted]
    if len(selected)!=len(wanted):
        raise ValueError('固定工时项不能参与AI重新分配，请取消勾选后重试')
    preserved=[t for t in unlocked if t['id'] not in wanted]
    return selected,preserved


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
    selected,preserved=select_tasks(tasks,request.task_ids)
    stored_target = conn.execute(select(db.reports.c.target_minutes).where(db.reports.c.date == str(day))).scalar_one_or_none()
    target_minutes = request.target_minutes or stored_target or settings.target_minutes
    human_fixed=sum(t['confirmed_minutes'] for t in locked)+sum(i.minutes for i in request.manual_items)
    preserved_minutes=sum(t['confirmed_minutes'] for t in preserved)
    retained=human_fixed+preserved_minutes
    if retained>target_minutes:
        raise ValueError('固定或保留工时已超过每日目标，请先调整；未改动任何工作项')
    remaining=target_minutes-retained
    if remaining%ALLOCATION_STEP:
        raise ValueError(f'AI工时只能按0.5小时分配：固定人工工时为{human_fixed}分钟，保留{preserved_minutes}分钟，剩余{remaining}分钟不是30分钟的整数倍；请调整人工工时或每日目标后重试，原记录未改动')
    if not remaining:
        if any(t['confirmed_minutes'] for t in selected):
            raise ValueError('固定工时已占满目标，请先人工核对其余已有分配，系统不会静默清零')
        if not request.manual_items:
            existing_alloc = conn.execute(select(db.allocations.c.snapshot).where(db.allocations.c.date == str(day))).scalar_one_or_none()
            if existing_alloc:
                refreshed_snapshot = {
                    **existing_alloc,
                    'estimated_at': now(),
                    'target_minutes': target_minutes,
                    'fixed_minutes': human_fixed,
                    'preserved_minutes': preserved_minutes,
                    'distributed_minutes': 0,
                    'items': [],
                    'manual_tasks': [],
                    'selected_ids': [],
                    'pending': False,
                    'applied_at': now(),
                    'total_minutes': retained,
                    'warnings': ['固定工时已达到目标，无需AI分配。'],
                    'state_hash': state_hash(tasks, settings, target_minutes),
                }
                upsert(conn, db.allocations, {'date': str(day), 'snapshot': refreshed_snapshot}, 'date')
                upsert(conn, db.reports, {'date': str(day), 'target_minutes': target_minutes, 'confirmed': False}, 'date')
            return {'allocated':0,'fixed_minutes':human_fixed,'total_minutes':retained,'warnings':['固定工时已达到目标，无需AI分配。'],'applied':False,'pending':False}
    if remaining and not selected:
        if request.task_ids is not None:
            raise ValueError('请至少勾选一个需要AI分配工时的工作项')
        raise ValueError('已选工作项均带固定工时，请改选需要AI估算的工作项')
    analysis_progress.emit('开始估算工时，正在整理参与分配的工作项')
    analysis_snap=conn.execute(select(db.analyses.c.snapshot).where(db.analyses.c.date==str(day))).scalar_one_or_none() or {}
    s_day=analysis_snap.get('start_date') or day
    e_day=analysis_snap.get('end_date') or day
    timeline=day_rows(conn,s_day,settings,end_day=e_day)
    initial_hash=state_hash(tasks,settings,target_minutes)
    scope_hash=input_hash(timeline,settings)
    prepared=[]
    if request.manual_items:
        text=model_provider.complete(settings,prompts.prompt(settings,'manual_writer'),json.dumps({'items':[{**i.model_dump(),'name':redact(i.name,settings.redact_patterns)} for i in request.manual_items],'style_examples':settings.title_examples},ensure_ascii=False),max_tokens=4000,purpose='manual_writer')
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
        analysis_progress.emit(f'人工事项已命名，正在估算 {len(selected)} 个工作项的工时')
    payload={'date':str(day),'target_minutes':target_minutes,'manual_minutes':human_fixed,'preserved_minutes':preserved_minutes,'remaining_target_minutes':remaining,'allocation_step_minutes':ALLOCATION_STEP,
             'locked_tasks':[{'id':t['id'],'title':redact(t['title'],settings.redact_patterns),'minutes':t['confirmed_minutes']} for t in locked]+[{'id':f'quick-{i}','title':p.title,'minutes':p.confirmed_minutes} for i,p in enumerate(prepared)],
             'unlocked_tasks':[{'id':t['id'],'title':redact(t['title'],settings.redact_patterns),'details':redact(t['details'],settings.redact_patterns),
                                'project':t['project'],'category':t['category'],'status':t['status'],'source':t['origin'],
                                'evidence_count':len(t['evidence']),'timing_reference':activity_time.profile(t['evidence'],timeline,settings.activity_gap_minutes)} for t in selected]}
    if remaining:
        text=model_provider.complete(settings,prompts.prompt(settings,'time_estimator'),json.dumps(payload,ensure_ascii=False),max_tokens=8000,purpose='time_estimator')
        result=parse_estimates(text,[t['id'] for t in selected])
    else:
        result={'items':[],'note':'人工固定工时已达到目标。'}
    eligible=[i for i in result['items'] if i['eligible']]
    allocation=distribute(remaining,eligible,ALLOCATION_STEP)
    warnings=['这是按目标工时分配的AI建议，并非人工投入的精确测量；请审核后再确认日报。']
    estimates=sum(i['estimated_minutes'] or 0 for i in eligible)
    if estimates and remaining>estimates*1.5:
        warnings.append(f'剩余目标 {remaining} 分钟明显高于AI独立估算 {estimates} 分钟；请按实际投入核对，必要时保留差额。')
    analysis_progress.emit(f'工时估算完成：{len(eligible)} 个工作项参与分配，正在核对工作项是否变化')
    conn.commit();conn.exec_driver_sql('BEGIN IMMEDIATE')
    current_settings=get_settings(conn);current_tasks=list_tasks(conn,day)
    current_snap=conn.execute(select(db.analyses.c.snapshot).where(db.analyses.c.date==str(day))).scalar_one_or_none() or {}
    c_s_day=current_snap.get('start_date') or day
    c_e_day=current_snap.get('end_date') or day
    if state_hash(current_tasks,current_settings,target_minutes)!=initial_hash or input_hash(day_rows(conn,c_s_day,current_settings,end_day=c_e_day),current_settings)!=scope_hash:
        raise ValueError('估算期间工作项、工时或对话范围发生变化，未覆盖人工修改；请重新估算')
    apply_now=settings.auto_apply_allocation
    added=[]
    if apply_now:
        added=[save_task(conn,p) for p in prepared]
        for item in result['items']:
            minutes=allocation.get(item['id'],0)
            reason=redact(item['reason'],settings.redact_patterns)
            conn.execute(update(db.worklogs).where(db.worklogs.c.task_id==item['id']).values(confirmed_minutes=minutes,suggested_minutes=item.get('estimated_minutes'),time_locked=False,time_source='ai_estimate',time_reason=reason,is_confirmed=False))
        invalidate(conn,day)
        analysis_progress.emit('工时已应用到所选工作项')
    else:
        analysis_progress.emit('工时建议已保存为预览，确认后才会写入工作项')
    final_tasks=list_tasks(conn,day)
    snapshot={'estimated_at':now(),'model':model_provider.status(settings)['model'],'target_minutes':target_minutes,'step_minutes':ALLOCATION_STEP,
              'fixed_minutes':human_fixed,'preserved_minutes':preserved_minutes,'distributed_minutes':remaining,'independent_estimate_minutes':estimates,
              'note':redact(str(result.get('note',''))[:3000],settings.redact_patterns),'warnings':warnings,
              'items':[{**i,'reason':redact(i['reason'],settings.redact_patterns),'allocated_minutes':allocation.get(i['id'],0)} for i in result['items']],
              'manual_tasks':[p.model_dump(mode='json') for p in prepared],'selected_ids':[t['id'] for t in selected],
              'pending':not apply_now,'applied_at':now() if apply_now else None,
              'state_hash':state_hash(final_tasks,settings,target_minutes),'scope_hash':scope_hash}
    upsert(conn,db.allocations,{'date':str(day),'snapshot':snapshot},'date')
    upsert(conn,db.reports,{'date':str(day),'target_minutes':target_minutes,'confirmed':False},'date')
    response={'allocated':len(allocation),'added_manual_ids':added,'fixed_minutes':human_fixed,'preserved_minutes':preserved_minutes,
              'total_minutes':sum(t['confirmed_minutes'] for t in final_tasks),'warnings':warnings,'allocation':snapshot,
              'applied':apply_now,'pending':not apply_now}
    if request_key:
        upsert(conn,db.allocation_requests,{'id':request_key,'payload_hash':request_hash,'result':response},'id')
    return response


def apply(conn, day):
    """Write a previously previewed allocation after confirming it is still current."""
    from .service import get_settings,invalidate,list_tasks,now,save_task,upsert
    snapshot=conn.execute(select(db.allocations.c.snapshot).where(db.allocations.c.date==str(day))).scalar_one_or_none()
    if not snapshot:
        raise ValueError('没有可应用的工时建议，请先生成')
    if not snapshot.get('pending'):
        raise ValueError('该工时建议已应用，无需重复应用')
    settings=get_settings(conn)
    tasks=list_tasks(conn,day)
    stored_target=conn.execute(select(db.reports.c.target_minutes).where(db.reports.c.date==str(day))).scalar_one_or_none()
    effective_target=stored_target or snapshot.get('target_minutes') or settings.target_minutes
    if state_hash(tasks,settings,effective_target)!=snapshot.get('state_hash'):
        raise ValueError('工作项、工时或分析设置已变化，请重新分配；本次未写入任何记录')
    selected_ids=set(snapshot.get('selected_ids') or [t['id'] for t in tasks if not t['time_locked']])
    added=[save_task(conn,TaskInput.model_validate(p)) for p in snapshot.get('manual_tasks',[])]
    for item in snapshot.get('items',[]):
        if item['id'] not in selected_ids:
            continue
        reason=item.get('reason','')
        conn.execute(update(db.worklogs).where(db.worklogs.c.task_id==item['id']).values(confirmed_minutes=item.get('allocated_minutes',0),suggested_minutes=item.get('estimated_minutes'),time_locked=False,time_source='ai_estimate',time_reason=reason,is_confirmed=False))
    invalidate(conn,day)
    final=list_tasks(conn,day)
    snapshot={**snapshot,'pending':False,'applied_at':now(),'total_minutes':sum(t['confirmed_minutes'] for t in final),
              'state_hash':state_hash(final,settings,effective_target)}
    upsert(conn,db.allocations,{'date':str(day),'snapshot':snapshot},'date')
    return {'allocated':len(selected_ids),'added_manual_ids':added,'fixed_minutes':snapshot.get('fixed_minutes',0),
            'preserved_minutes':snapshot.get('preserved_minutes',0),'total_minutes':snapshot['total_minutes'],
            'warnings':snapshot.get('warnings',[]),'allocation':snapshot,'applied':True,'pending':False}
