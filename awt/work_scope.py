import json
import re
from . import activity_time, model_provider, prompts

POLICY_VERSION='learning-active-over-60-v1'


def filter_work(records, refs, segments, settings):
    roots={r['ref'] for r in records if r['kind'] in ('user_prompt','observed_activity')}
    if not roots:
        return [],{}, {'work_refs':[],'excluded':[],'uncertain_refs':[],'work_event_ids':[]},0
    chunks,current,size=[],[],0
    limit=120000 if settings.llm_provider in ('deepseek','glm','glm-coding','ccswitch') else 65000
    for record in records:
        length=len(json.dumps(record,ensure_ascii=False))
        if current and size+length>limit:
            chunks.append(current);current,size=[],0
        current.append(record);size+=length
    if current:chunks.append(current)
    if len(chunks)>30:
        raise model_provider.ModelError('对话过多，未发送不完整的工作筛选，请缩小采集范围')
    work,excluded,uncertain,requests=set(),[],set(),0
    learning=[]
    for chunk in chunks:
        required={r['ref'] for r in chunk if r['ref'] in roots}
        if not required:continue
        text=model_provider.complete(settings,prompts.prompt(settings,'scope_filter'),json.dumps({'required_refs':sorted(required),'conversations':chunk},ensure_ascii=False),max_tokens=6000,purpose='scope_filter')
        requests+=1
        try:
            data=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip()))
            a,b,c=data['work_refs'],data['excluded'],data['uncertain_refs']
            groups=data.get('learning_groups',[])
            if not all(isinstance(v,list) for v in (a,b,c,groups)):
                raise ValueError()
            if any(not isinstance(g,dict) or not isinstance(g.get('topic'),str) or not g['topic'].strip() or not isinstance(g.get('refs'),list) or not g['refs'] for g in groups):
                raise ValueError()
            excluded_refs=[i['ref'] for i in b if isinstance(i,dict) and isinstance(i.get('reason'),str)]
            all_refs=a+c+excluded_refs+[r for g in groups for r in g['refs']]
            if len(excluded_refs)!=len(b) or not all(isinstance(r,str) for r in all_refs) or len(all_refs)!=len(set(all_refs)) or set(all_refs)!=required:
                raise ValueError()
            work.update(a);excluded.extend(b);uncertain.update(c);learning.extend(groups)
        except (ValueError,KeyError,TypeError):
            raise model_provider.ModelError('工作相关性筛选未完整覆盖用户请求，未改动原任务；请重试') from None
    qualified={};learning_timing=[]
    conversation_timeline=[e for e in refs.values() if e['kind'] in ('user_prompt','assistant_message')]
    for group in learning:
        events={e['id']:e for r in group['refs'] for e in [refs[r],*segments.get(r,[])] if e['kind'] in ('user_prompt','assistant_message')}
        # Adjacent activity only: a long answer, idle gap or unrelated exchange
        # cannot turn a short knowledge question into an hour of human work.
        timing=activity_time.profile(list(events.values()),conversation_timeline,min(settings.activity_gap_minutes,5))
        admitted=timing['observable_active_minutes']>60 and sum(e['kind']=='user_prompt' for e in events.values())>1
        learning_timing.append({**group,'timing':timing,'eligible':admitted})
        if admitted:
            work.update(group['refs'])
            for r in group['refs']:qualified[r]={'topic':group['topic'],'observable_active_minutes':timing['observable_active_minutes'],'threshold_minutes':60}
        else:
            excluded.extend({'ref':r,'reason':'知识了解/学习梳理的活跃讨论未超过60分钟，不生成工作项、不计工时'} for r in group['refs'])
    allowed=set()
    for ref in work:
        allowed.add(refs[ref]['id'])
        allowed.update(e['id'] for e in segments.get(ref,[]))
        record=next(r for r in records if r['ref']==ref)
        for activity in record.get('activities',[]):
            allowed.update(refs[r]['id'] for r in activity['refs'])
    if work:
        allowed.update(e['id'] for e in refs.values() if e['source']=='git')
    filtered_refs={r:e for r,e in refs.items() if e['id'] in allowed}
    filtered=[{**r,**({'learning_evidence':qualified[r['ref']]} if r['ref'] in qualified else {})} for r in records if r['ref'] in filtered_refs]
    scope={'work_refs':sorted(work),'excluded':excluded,'uncertain_refs':sorted(uncertain),'work_event_ids':sorted(allowed),
           'work_user_messages':sum(refs[r]['kind']=='user_prompt' for r in work),
           'excluded_user_messages':sum(refs[i['ref']]['kind']=='user_prompt' for i in excluded),
           'uncertain_user_messages':sum(refs[r]['kind']=='user_prompt' for r in uncertain),'learning_groups':learning_timing}
    return filtered,filtered_refs,scope,requests
