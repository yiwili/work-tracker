"""Read-only Gitee V5 Issues import. No remote writes or destructive sync."""
import base64
import json
import os
import logging
import re
from datetime import datetime, timedelta, timezone
from threading import Lock
import httpx
from sqlalchemy import select
from . import db, model_provider, style_reference, gitee_enterprise
from .collectors import stable

lock=Lock()
API='https://gitee.com/api/v5'


class _CredentialFilter(logging.Filter):
    def filter(self, record):
        message=record.getMessage()
        if 'gitee.com/api/' in message or 'api.gitee.com/enterprises' in message:
            record.msg=re.sub(r'(access_token=)[^&\s"\']+',r'\1[redacted]',message)
            record.args=()
        return True


# Gitee's documented V5 authentication uses the access_token query parameter.
# httpx otherwise includes complete request URLs in its INFO messages.
logging.getLogger('httpx').addFilter(_CredentialFilter())


def token(service='gitee'):
    path=db.data_dir()/(service+'-credential.json')
    if path.exists():
        try:
            return model_provider._crypt(base64.b64decode(json.loads(path.read_text())['encrypted']),decrypt=True).decode()
        except (ValueError,KeyError,OSError):
            raise ValueError('Gitee本机凭据无法读取，请重新保存Token') from None
    if service=='gitee':
        ent_path=db.data_dir()/'gitee-enterprise-credential.json'
        if ent_path.exists():
            try:
                return model_provider._crypt(base64.b64decode(json.loads(ent_path.read_text())['encrypted']),decrypt=True).decode()
            except (ValueError,KeyError,OSError):
                pass
    val = os.environ.get('GITEE_ENTERPRISE_TOKEN' if service=='gitee-enterprise' else 'GITEE_ACCESS_TOKEN','')
    if not val and service=='gitee':
        val = os.environ.get('GITEE_ENTERPRISE_TOKEN','')
    return val


def save_token(value,service='gitee'):
    if not value.strip():
        raise ValueError('Token不能为空')
    services = [service, 'gitee-enterprise'] if service == 'gitee' else [service]
    for svc in services:
        path=db.data_dir()/(svc+'-credential.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        temporary=path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'encrypted':base64.b64encode(model_provider._crypt(value.strip().encode())).decode()}))
        temporary.replace(path)


def status(conn):
    from .service import get_settings
    state=conn.execute(select(db.integrations.c.value).where(db.integrations.c.id=='gitee')).scalar() or {}
    has_token = False
    try:
        has_token = bool(token())
    except Exception:
        pass
    if not has_token:
        try:
            has_token = bool(token('gitee-enterprise'))
        except Exception:
            pass
    return {'configured': has_token, 'config': get_settings(conn).gitee.model_dump(mode='json'), **state}


def fetch(config):
    key=token()
    if not key:
        raise ValueError('请先保存Gitee Token，或设置服务端GITEE_ACCESS_TOKEN')
    def get(client,path,params=None):
        try:
            response=client.get(API+path,params={**(params or {}),'access_token':key})
            if response.status_code!=200:
                raise ValueError(f'Gitee请求失败（HTTP {response.status_code}），请检查Token权限或稍后重试；历史未改动')
            return response.json()
        except (httpx.HTTPError,json.JSONDecodeError):
            raise ValueError('Gitee网络请求失败或返回格式无效，历史未改动') from None
    rows=[]
    with httpx.Client(headers={'Accept':'application/json'},timeout=30,follow_redirects=False,trust_env=False) as client:
        user=get(client,'/user')
        if not isinstance(user,dict) or not isinstance(user.get('login'),str) or not user.get('login'):
            raise ValueError('Gitee用户资料格式无效')
        seen=set()
        for page in range(1,501):
            items=get(client,'/user/issues',{'filter':config.filter,'state':'all','sort':'created','direction':'desc',
                      'since':str(config.since)+'T00:00:00+08:00','page':page,'per_page':100})
            if not isinstance(items,list) or any(not isinstance(i,dict) or not isinstance(i.get('title'),str) for i in items):
                raise ValueError('Gitee工作项列表格式无效，历史未改动')
            signature=stable(items)
            if items and signature in seen:
                raise ValueError('Gitee返回重复分页，请稍后重试，历史未改动')
            seen.add(signature)
            for item in items:
                repo=item.get('repository') or {}
                if not isinstance(repo,dict):
                    raise ValueError('Gitee仓库信息格式无效，历史未改动')
                repository=repo.get('full_name') or repo.get('path_with_namespace') or ''
                url=item.get('html_url') or ''
                if not isinstance(repository,str) or not isinstance(url,str):
                    raise ValueError('Gitee仓库地址格式无效，历史未改动')
                if not repository and url.startswith('https://gitee.com/'):
                    repository='/'.join(url.split('/')[3:5])
                if not config.repository or repository.lower()==config.repository.lower():
                    rows.append({**item,'project_path':repository})
            if len(items)<100:
                return rows,{'login':user['login'],'name':user.get('name','')}
        raise ValueError('工作项超过500页，请缩小日期范围后重试；未保存不完整结果')


def fetch_unfinished(config=None):
    key = token()
    if not key:
        return []
    def get(client, path, params=None):
        try:
            response = client.get(API + path, params={**(params or {}), 'access_token': key})
            if response.status_code != 200:
                return []
            return response.json()
        except (httpx.HTTPError, json.JSONDecodeError):
            return []
    rows = []
    with httpx.Client(headers={'Accept': 'application/json'}, timeout=30, follow_redirects=False, trust_env=False) as client:
        seen = set()
        filter_mode = config.filter if config else 'all'
        repo_filter = config.repository if config else ''
        for page in range(1, 20):
            items = get(client, '/user/issues', {'filter': filter_mode, 'state': 'open', 'sort': 'updated', 'direction': 'desc',
                                                 'page': page, 'per_page': 100})
            if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
                break
            sig = stable(items)
            if sig in seen:
                break
            seen.add(sig)
            for item in items:
                repo = item.get('repository') or {}
                repo_name = repo.get('full_name') or repo.get('path_with_namespace') or ''
                if repo_filter and repo_name.lower() != repo_filter.lower():
                    continue
                url = item.get('html_url') or ''
                created_at = item.get('created_at') or ''
                rows.append({
                    'id': str(item.get('id')),
                    'issue_id': str(item.get('number') or item.get('id')),
                    'title': str(item.get('title') or ''),
                    'description': str(item.get('body') or ''),
                    'state': str(item.get('state') or 'open'),
                    'project': repo_name,
                    'program': repo_name,
                    'date': created_at[:10] if created_at else '',
                    'url': url,
                    'source': 'gitee_v5',
                })
            if len(items) < 100:
                break
    return rows


def sync(engine):
    from .service import get_settings,upsert,now
    if not lock.acquire(blocking=False):
        raise ValueError('历史导入或Gitee同步正在进行，请稍后重试')
    try:
        with engine.connect() as conn:
            settings = get_settings(conn)
            config = settings.gitee
            ent_config = settings.gitee_enterprise

        # If enterprise is configured, fetch enterprise issues using Enterprise Gitee!
        has_ent_token = False
        try:
            has_ent_token = bool(token('gitee-enterprise'))
        except TypeError:
            has_ent_token = bool(token())
        except Exception:
            pass
        if not has_ent_token:
            try:
                has_ent_token = bool(token())
            except Exception:
                pass
        if ent_config.enterprise_id and has_ent_token:
            issues = gitee_enterprise.fetch_all_issues(ent_config.enterprise_id, since=config.since)
            with engine.begin() as conn:
                result = style_reference.ingest(conn, issues, config.since, source='gitee', label=f'Gitee Enterprise / #{ent_config.enterprise_id}')
                state = {'last_synced_at': now(), 'account': {'login': f'Enterprise #{ent_config.enterprise_id}'}, 'records': len(issues), 'error': ''}
                upsert(conn, db.integrations, {'id': 'gitee', 'value': state}, 'id')
            with engine.connect() as conn:
                result['style'] = style_reference.initial_template(conn)
                conn.commit()
            return {**result, **state}

        rows,user=fetch(config)
        with engine.begin() as conn:
            if get_settings(conn).gitee!=config:
                raise ValueError('抓取期间Gitee配置发生变化，请重试')
            result=style_reference.ingest(conn,rows,config.since,source='gitee',label='Gitee / '+user['login'])
            state={'last_synced_at':now(),'account':user,'records':len(rows),'error':''}
            upsert(conn,db.integrations,{'id':'gitee','value':state},'id')
        with engine.connect() as conn:
            result['style']=style_reference.initial_template(conn)
            conn.commit()
        return {**result,**state}
    finally:
        lock.release()


def due(conn):
    from .service import get_settings
    config=get_settings(conn).gitee
    if not config.enabled or not token():
        return False
    state=conn.execute(select(db.integrations.c.value).where(db.integrations.c.id=='gitee')).scalar() or {}
    stamp=state.get('last_attempt_at') or state.get('last_synced_at')
    try:
        return not stamp or datetime.now(timezone.utc)-datetime.fromisoformat(stamp)>timedelta(hours=config.interval_hours)
    except (TypeError,ValueError):
        return True


def scheduled_sync(engine):
    from .service import now,upsert
    with engine.connect() as conn:
        if not due(conn) or lock.locked():
            return
    try:
        sync(engine)
    except ValueError as exc:
        with engine.begin() as conn:
            state=conn.execute(select(db.integrations.c.value).where(db.integrations.c.id=='gitee')).scalar() or {}
            upsert(conn,db.integrations,{'id':'gitee','value':{**state,'last_attempt_at':now(),'error':str(exc)[:500]}},'id')
