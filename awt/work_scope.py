import json
import logging
import re
from collections import defaultdict

from . import activity_time, model_provider, prompts

POLICY_VERSION='learning-active-over-60-v1'


def _loads(text):
    for candidate in (text, re.sub(r',(\s*[}\]])',r'\1',text)):
        try:
            value=json.loads(candidate)
            if isinstance(value,dict):return value
        except ValueError:
            pass
    return None


def _json_object(text):
    """Find the model's JSON object even when it adds prose or a code fence."""
    cleaned=text.strip().lstrip('\ufeff').strip()
    cleaned=re.sub(r'^```(?:json)?\s*|\s*```$','',cleaned).strip()
    value=_loads(cleaned)
    if value is not None:return value
    start=depth=0;in_string=escape=False
    for index,char in enumerate(cleaned):
        if in_string:
            if escape:escape=False
            elif char=='\\':escape=True
            elif char=='"':in_string=False
        elif char=='"':in_string=True
        elif char=='{':
            if not depth:start=index
            depth+=1
        elif char=='}' and depth:
            depth-=1
            if not depth:
                value=_loads(cleaned[start:index+1])
                if value is not None:return value
    raise ValueError()


def _classify(value, required):
    """Normalise the model's classification without guessing about people.

    Refs outside required_refs (usually assistant replies) are ignored, duplicates
    inside one category are merged, and a ref claimed by two categories becomes
    uncertain instead of being counted as work. Refs the model forgot are also
    left as uncertain so the day can still be reviewed manually.
    """
    if not isinstance(value, dict):
        raise ValueError()
    a,b,c=value['work_refs'],value['excluded'],value['uncertain_refs']
    groups=value.get('learning_groups',[])
    if not all(isinstance(v,list) for v in (a,b,c,groups)):
        raise ValueError()
    if any(not isinstance(g,dict) or not isinstance(g.get('topic'),str) or not g['topic'].strip()
           or not isinstance(g.get('refs'),list) or not g['refs'] for g in groups):
        raise ValueError()
    if any(not isinstance(i,dict) or not isinstance(i.get('ref'),str) or not isinstance(i.get('reason'),str) for i in b):
        raise ValueError()
    if not all(isinstance(r,str) for r in [*a,*c,*[r for g in groups for r in g['refs']]]):
        raise ValueError()
    membership=defaultdict(set)
    for ref in a:membership[ref].add('work')
    for ref in c:membership[ref].add('uncertain')
    for item in b:membership[item['ref']].add('excluded')
    for group in groups:
        for ref in group['refs']:membership[ref].add('learning')
    ambiguous={ref for ref,cats in membership.items() if len(cats)>1}
    work={ref for ref in a if ref in required and ref not in ambiguous}
    uncertain={ref for ref in c if ref in required and ref not in ambiguous}|ambiguous
    excluded=[];seen=set()
    for item in b:
        ref=item['ref']
        if ref in required and ref not in ambiguous and ref not in seen:
            seen.add(ref);excluded.append(item)
    learning=[];claimed=set()
    for group in groups:
        kept=[]
        for ref in group['refs']:
            if ref in required and ref not in ambiguous and ref not in claimed:
                claimed.add(ref);kept.append(ref)
        if kept:learning.append({**group,'refs':kept})
    classified=required & set(membership)
    unresolved=sorted(required-classified)
    uncertain.update(unresolved)
    return work,uncertain,learning,excluded,unresolved,sorted(ambiguous),classified


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
    learning=[];unresolved_all=set();ambiguous_all=set()
    for chunk in chunks:
        required={r['ref'] for r in chunk if r['ref'] in roots}
        if not required:continue
        text=model_provider.complete(settings,prompts.prompt(settings,'scope_filter'),json.dumps({'required_refs':sorted(required),'conversations':chunk},ensure_ascii=False),max_tokens=6000,purpose='scope_filter')
        requests+=1
        try:
            classified_work,classified_uncertain,classified_learning,classified_excluded,unresolved,ambiguous,classified=_classify(_json_object(text),required)
        except (ValueError,KeyError,TypeError):
            raise model_provider.ModelError('工作相关性筛选返回的 JSON 无法解析或结构不完整，未改动原任务；请重试，如持续失败请在设置中改用更强的模型（如 GLM-5.3 非 Flash、DeepSeek）') from None
        if not classified or len(unresolved)>max(2,len(required)//2):
            missing=','.join(unresolved[:20]) or '无'
            raise model_provider.ModelError(f'工作相关性筛选只覆盖了 {len(classified)}/{len(required)} 条用户请求，未改动原任务；请重试或改用更强的模型。未覆盖：{missing}')
        if unresolved or ambiguous:
            logging.getLogger('awt.scope').warning('scope_filter 未完整归类 required=%d unresolved=%d ambiguous=%d',len(required),len(unresolved),len(ambiguous))
        work.update(classified_work);uncertain.update(classified_uncertain)
        learning.extend(classified_learning);excluded.extend(classified_excluded)
        unresolved_all.update(unresolved);ambiguous_all.update(ambiguous)
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
           'uncertain_user_messages':sum(refs[r]['kind']=='user_prompt' for r in uncertain),'learning_groups':learning_timing,
           'unresolved_refs':sorted(unresolved_all),'ambiguous_refs':sorted(ambiguous_all)}
    return filtered,filtered_refs,scope,requests
