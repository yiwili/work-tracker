"""Observable log timing is a reference signal, never a human timesheet measurement."""
import json
import re
from collections import defaultdict
from datetime import datetime, timedelta


def union_seconds(intervals):
    total, start, end = 0, None, None
    for a,b in sorted(intervals):
        if b<=a:
            continue
        if end is None or a>end:
            if end is not None:
                total+=(end-start).total_seconds()
            start,end=a,b
        else:
            end=max(end,b)
    return total+((end-start).total_seconds() if end is not None else 0)


def runtime_seconds(event):
    metadata=event.get('metadata') or {}
    value=metadata.get('duration_seconds')
    if value is None:
        try:
            obj=json.loads(event.get('summary',''))
            value=obj.get('wall_time_seconds') if isinstance(obj,dict) else None
        except (ValueError,TypeError):
            pass
    if value is None:
        found=re.search(r'^Wall time:\s*([\d.]+)\s*seconds\s*$',event.get('summary',''),re.M)
        value=found.group(1) if found else None
    try:
        value=float(value)
        return value if 0<=value<=86400 else None
    except (ValueError,TypeError):
        return None


def profile(events, timeline=None, gap_minutes=5):
    allowed={e['id'] for e in events}
    groups=defaultdict(list)
    for event in timeline if timeline is not None else events:
        if event['source']!='git' and event['kind']!='session_end':
            groups[(event['source'],event['session_id'])].append(event)
    active,spans,runtimes=[],[],[]
    for group in groups.values():
        group.sort(key=lambda e:e['occurred_at'])
        kept=[e for e in group if e['id'] in allowed]
        if len(kept)>1:
            spans.append((datetime.fromisoformat(kept[0]['occurred_at']),datetime.fromisoformat(kept[-1]['occurred_at'])))
        # Use original neighboring events, so filtering a private exchange cannot bridge a work interval.
        for a,b in zip(group,group[1:]):
            x,y=datetime.fromisoformat(a['occurred_at']),datetime.fromisoformat(b['occurred_at'])
            if a['id'] in allowed and b['id'] in allowed and 0<(y-x).total_seconds()<=gap_minutes*60:
                active.append((x,y))
        for event in kept:
            if event['kind']=='tool_result':
                seconds=runtime_seconds(event)
                if seconds is not None:
                    end=datetime.fromisoformat(event['occurred_at'])
                    runtimes.append((end-timedelta(seconds=seconds),end))
    return {'session_span_minutes':round(union_seconds(spans)/60,1),
            'observable_active_minutes':round(union_seconds(active)/60,1),
            'tool_runtime_minutes':round(union_seconds(runtimes)/60,1) if runtimes else None,
            'first_at':min((e['occurred_at'] for e in events if e['source']!='git'),default=None),
            'last_at':max((e['occurred_at'] for e in events if e['source']!='git'),default=None),
            'gap_minutes':gap_minutes,'confidence':'参考值，非人工实测工时',
            'method':'跨度含等待；活跃片段仅累计相邻活动间隔不超过阈值的部分，并行区间取并集；私人/无关活动不计入工作活跃片段。'}
