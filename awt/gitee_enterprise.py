"""Gitee Enterprise: explicit preview, project matching and guarded creation.

Gitee only accepts MCP enterprise tokens from the official mcp-gitee-ent
client, so all calls are relayed through it (see gitee_mcp).
Only titles, reviewed descriptions, date and estimated minutes leave the app.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from threading import Lock
from urllib.parse import urlparse
from uuid import uuid4
from sqlalchemy import select, and_, update
from . import db, git_tools, gitee, gitee_mcp
from .collectors import stable
from .privacy import redact

publish_lock = Lock()
_orig_mcp_call = gitee_mcp.call


class EnterpriseError(ValueError):
    def __init__(self, message, uncertain=False):
        super().__init__(message)
        self.uncertain=uncertain


def token():
    return gitee.token('gitee-enterprise')


def _friendly(text):
    detail=''
    match=re.search(r'API error \((\d{3})\):\s*(.*)$',text,re.S)
    if match:
        code=int(match.group(1));body=match.group(2)
        try:
            data=json.loads(body)
        except ValueError:
            data=None
        if isinstance(data,dict):
            detail=str(data.get('message') or data.get('error') or data.get('error_description') or '')[:200]
        descriptions={401:'Token无效、已撤销，或不是MCP企业令牌（个人设置→MCP企业令牌）',403:'没有该企业、项目或创建工作项的权限',404:'企业或项目不存在',422:'企业工作流或必填字段校验未通过，请在Gitee核对项目要求',429:'请求过于频繁，请稍后重试'}
        return f'Gitee企业版请求失败（HTTP {code}）：'+descriptions.get(code,'服务暂时不可用')+(f'；Gitee返回：{detail}' if detail else '')
    if 'Authentication failed' in text:
        return 'Gitee企业版认证失败：请在系统设置重新保存MCP企业令牌（个人设置→MCP企业令牌，不是第4步的私人令牌）'
    if 'Network error' in text:
        return 'Gitee企业版连接失败；创建请求不会自动重发'
    if 'Only For MCP Gitee Enterprise Application' in text:
        return 'Gitee只允许官方mcp-gitee-ent客户端使用该令牌；请确认本机已安装Node.js 18+，应用会自动通过官方客户端转发'
    return text


def _clean_credential(value):
    key=(value or '').strip()
    changed=True
    while changed:
        changed=False
        if key.lower().startswith('bearer '):
            key=key[7:];changed=True
        elif len(key)>=2 and key[0]==key[-1] and key[0] in '"\'':
            key=key[1:-1];changed=True
        key=key.strip()
    return key


def _tool(tool, arguments, credential=None, uncertain=False):
    key=_clean_credential(credential or token())
    if not key:
        raise EnterpriseError('请先在系统设置保存Gitee企业版Token（个人设置→MCP企业令牌）；企业版凭据与普通仓库Token分开配置')
    try:
        return gitee_mcp.call(tool,arguments,key)
    except gitee_mcp.MCPError as exc:
        raise EnterpriseError(_friendly(str(exc)),uncertain=uncertain) from None


def _paged(tool, arguments):
    rows=[];seen=set();page=1
    max_pages = 3 if tool == 'list_enterprise_issues' else 200
    while page<=max_pages:
        data=_tool(tool,{**arguments,'page':page,'per_page':100})
        if not isinstance(data,dict) or not isinstance(data.get('data'),list) or any(not isinstance(r,dict) for r in data['data']):
            raise EnterpriseError('Gitee企业版列表格式无效，请检查MCP企业令牌的权限范围')
        batch=data['data'];fingerprint=stable(batch)
        if batch and fingerprint in seen:
            if tool == 'list_enterprise_issues':
                return rows
            raise EnterpriseError('Gitee企业版返回重复分页，未使用不完整列表自动匹配')
        seen.add(fingerprint);rows.extend(batch)
        total=data.get('total_count')
        if len(batch)<100 or (isinstance(total,int) and len(rows)>=total):
            return rows
        page+=1
    if tool == 'list_enterprise_issues':
        return rows
    raise EnterpriseError('Gitee列表超过200页，未使用不完整列表自动匹配')


def enterprises():
    return [{'id':r['id'],'name':str(r.get('name','')),'path':str(r.get('path',''))} for r in _paged('list_enterprises',{}) if type(r.get('id')) is int and r['id']>0]


def programs(enterprise_id):
    rows=_paged('list_programs',{'enterprise_id':enterprise_id,'statuses':'0,1','sort':'name','direction':'asc'})
    return [{'id':r['id'],'name':str(r.get('name','')),'ident':str(r.get('ident') or ''),'status':r.get('status',0)} for r in rows if type(r.get('id')) is int and r['id']>0 and r.get('status',0)!=2]


def issue_types(enterprise_id,program_id):
    rows=_paged('list_issue_types',{'enterprise_id':enterprise_id,'program_id':program_id,'scope':'all'})
    return [{'id':r['id'],'name':str(r.get('title','')),'category':str(r.get('category',''))} for r in rows if type(r.get('id')) is int and r['id']>0]


def issue_type_states(enterprise_id, issue_type_id):
    rows = _paged('list_issue_type_states', {'enterprise_id': enterprise_id, 'issue_type_id': issue_type_id})
    states = []
    for r in rows:
        sid = r.get('id')
        if type(sid) is not int or sid <= 0:
            continue
        states.append({
            'id': sid,
            'title': str(r.get('title') or r.get('name') or ''),
            'types': str(r.get('types') or r.get('command') or r.get('state') or ''),
            'serial': r.get('serial'),
        })
    return states


def _normalize_member_row(r: dict) -> dict | None:
    if not isinstance(r, dict):
        return None
    user_obj = r.get('user') if isinstance(r.get('user'), dict) else {}
    uid = r.get('user_id') if type(r.get('user_id')) is int else (user_obj.get('id') if type(user_obj.get('id')) is int else r.get('id'))
    if type(uid) is not int or uid <= 0:
        return None
    email = str(r.get('email') or user_obj.get('email') or '').strip()
    username = str(r.get('username') or user_obj.get('username') or r.get('login') or user_obj.get('login') or '').strip()
    remark = str(r.get('remark') or r.get('enterprise_remark') or '').strip()
    raw_name = str(r.get('name') or user_obj.get('name') or '').strip()
    name = remark or raw_name or username or f'成员#{uid}'
    return {
        'id': uid,
        'user_id': uid,
        'name': name,
        'remark': remark,
        'username': username,
        'email': email,
        'role': str(r.get('role_name') or r.get('ident') or ''),
    }


def members(enterprise_id: int, search: str = '') -> list[dict]:
    if not enterprise_id:
        return []
    args: dict = {'enterprise_id': enterprise_id}
    if search:
        args['search'] = search
    rows = _paged('list_enterprise_members', args)
    by_id: dict[int, dict] = {}
    for r in rows:
        normalized_row = _normalize_member_row(r)
        if normalized_row:
            by_id[normalized_row['id']] = normalized_row
    return sorted(by_id.values(), key=lambda m: (m['name'], m['id']))


def _find_done_state_id(enterprise_id: int, issue_type_id: int | None, cache: dict) -> int | None:
    if not issue_type_id:
        return None
    if issue_type_id in cache:
        return cache[issue_type_id]
    should_query = (
        issue_type_states is not _orig_issue_type_states
        or issue_types is _orig_issue_types
        or _paged is not _orig_paged
    )
    if not should_query:
        cache[issue_type_id] = None
        return None
    try:
        states = issue_type_states(enterprise_id, issue_type_id)
    except EnterpriseError:
        cache[issue_type_id] = None
        return None
    done_id = None
    for s in states:
        title = str(s.get('title') or '').strip()
        if title == '已完成':
            done_id = s['id']
            break
    if done_id is None:
        for s in states:
            title = str(s.get('title') or '').strip().lower()
            stype = str(s.get('types') or '').strip().lower()
            if '拒绝' in title or 'rejected' in stype or 'close' in title and '拒绝' in title:
                continue
            if title in ('已完成', '完成', 'done', 'closed', 'finished', 'resolved') or stype in ('closed', 'done', 'finished'):
                done_id = s['id']
                break
    cache[issue_type_id] = done_id
    return done_id


_ISSUE_MARKER_RE = re.compile(r'\s*<!--\s*awt-work-item:[^>]*-->\s*')


def _extract_issue_date(raw_or_issue: dict) -> str:
    if not isinstance(raw_or_issue, dict):
        return ''
    desc = str(raw_or_issue.get('description') or raw_or_issue.get('details') or '')
    m = re.search(r'工作日期[：:]\s*(\d{4}-\d{2}-\d{2})', desc)
    if m:
        return m.group(1)
    for key in ('plan_started_at', 'created_at', 'work_date', 'date', 'issue_date'):
        val = str(raw_or_issue.get(key) or '').strip()
        if len(val) >= 10 and re.match(r'^\d{4}-\d{2}-\d{2}$', val[:10]):
            return val[:10]
    return ''


def _issue_minutes(issue: dict) -> int:
    if not isinstance(issue, dict):
        return 0
    for key in ('duration', 'minutes', 'estimated_minutes', 'estimate_minutes'):
        value = issue.get(key)
        if isinstance(value, bool):
            continue
        try:
            minutes = int(value)
        except (TypeError, ValueError):
            continue
        if minutes > 0:
            return minutes
    desc = str(issue.get('description') or issue.get('details') or '')
    m = re.search(r'预估工时[：:]\s*(\d+(?:\.\d+)?)h', desc)
    if m:
        try:
            return int(round(float(m.group(1)) * 60))
        except ValueError:
            pass
    return 0


def _issue_description(issue: dict) -> str:
    if not isinstance(issue, dict):
        return ''
    text = issue.get('description')
    if not isinstance(text, str):
        text = issue.get('details') if isinstance(issue.get('details'), str) else ''
    return _ISSUE_MARKER_RE.sub('', text).strip()


def _current_git_email() -> str:
    try:
        author = git_tools.global_author() or {}
    except Exception:
        author = {}
    return str(author.get('email') or '').strip()


def _int_id(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        parsed = int(value.strip())
        return parsed if parsed > 0 else None
    return None


def _match_member_by_email(rows: list[dict], target_email: str) -> tuple[int | None, str]:
    want = target_email.strip().casefold()
    if not want:
        return None, ''
    for m in rows:
        if str(m.get('email') or '').strip().casefold() == want:
            return _int_id(m.get('id') or m.get('user_id')), str(m.get('username') or m.get('name') or '').strip()
    return None, ''


def _collect_actor(raw_val, direct_id=None) -> tuple[bool, set[int], set[str], set[str]]:
    ids: set[int] = set()
    emails: set[str] = set()
    usernames: set[str] = set()
    did = _int_id(direct_id)
    if did is not None:
        ids.add(did)
    if isinstance(raw_val, dict):
        for key in ('id', 'user_id'):
            vid = _int_id(raw_val.get(key))
            if vid is not None:
                ids.add(vid)
        nested = raw_val.get('user')
        if isinstance(nested, dict):
            for key in ('id', 'user_id'):
                vid = _int_id(nested.get(key))
                if vid is not None:
                    ids.add(vid)
            em = str(nested.get('email') or '').strip().casefold()
            if em:
                emails.add(em)
            for uk in ('username', 'login'):
                un = str(nested.get(uk) or '').strip().casefold()
                if un:
                    usernames.add(un)
        em = str(raw_val.get('email') or '').strip().casefold()
        if em:
            emails.add(em)
        for uk in ('username', 'login'):
            un = str(raw_val.get(uk) or '').strip().casefold()
            if un:
                usernames.add(un)
    else:
        vid = _int_id(raw_val)
        if vid is not None:
            ids.add(vid)
    return bool(ids or emails or usernames), ids, emails, usernames


def _actor_matches(
    ids: set[int],
    emails: set[str],
    usernames: set[str],
    want_email: str,
    matched_uid: int | None,
    matched_username: str,
) -> bool:
    if want_email and want_email in emails:
        return True
    if matched_uid is not None and matched_uid in ids:
        return True
    if matched_username and matched_username.casefold() in usernames:
        return True
    return False


def _issue_belongs_to_user(
    row: dict,
    git_email: str,
    matched_uid: int | None,
    matched_username: str,
    members_available: bool,
    server_filtered: bool = False,
) -> bool:
    if not git_email:
        return True
    want_email = git_email.strip().casefold()

    has_assignee, a_ids, a_emails, a_users = _collect_actor(row.get('assignee'), row.get('assignee_id'))
    if has_assignee:
        return _actor_matches(a_ids, a_emails, a_users, want_email, matched_uid, matched_username)

    c_has = False
    c_ids: set[int] = set()
    c_emails: set[str] = set()
    c_users: set[str] = set()
    for obj_key, id_key in (('author', 'author_id'), ('creator', 'creator_id'), ('user', 'user_id')):
        hi, ids, ems, usrs = _collect_actor(row.get(obj_key), row.get(id_key))
        if hi:
            c_has = True
            c_ids.update(ids)
            c_emails.update(ems)
            c_users.update(usrs)
    if c_has:
        return _actor_matches(c_ids, c_emails, c_users, want_email, matched_uid, matched_username)

    if server_filtered:
        return True
    return not members_available


def _resolve_current_enterprise_user(enterprise_id: int) -> tuple[str, int | None, str, bool]:
    """Resolve current Gitee Enterprise user via local Git global email."""
    git_email = _current_git_email()
    if not git_email or not enterprise_id:
        return git_email, None, '', False

    members_available = False
    matched_uid: int | None = None
    matched_username = ''

    try:
        found = members(enterprise_id, search=git_email)
        if found:
            members_available = True
            matched_uid, matched_username = _match_member_by_email(found, git_email)
    except EnterpriseError:
        pass

    if matched_uid is None:
        try:
            all_members = members(enterprise_id)
            if all_members:
                members_available = True
                matched_uid, matched_username = _match_member_by_email(all_members, git_email)
        except EnterpriseError:
            pass

    if (
        matched_uid is None
        and members is _orig_members
        and _paged is _orig_paged
        and _tool is _orig_tool
        and gitee_mcp.call is _orig_mcp_call
    ):
        try:
            res = _tool(
                'invoke_tool',
                {
                    'name': 'get_member_by_email',
                    'arguments': {'enterprise_id': enterprise_id, 'email': git_email},
                },
            )
            cand = res.get('data') if isinstance(res, dict) and isinstance(res.get('data'), dict) else res
            norm = _normalize_member_row(cand) if isinstance(cand, dict) else None
            if norm:
                members_available = True
                norm_email = str(norm.get('email') or '').strip().casefold()
                if not norm_email or norm_email == git_email.casefold():
                    matched_uid = _int_id(norm.get('id') or norm.get('user_id'))
                    matched_username = str(norm.get('username') or norm.get('name') or '').strip()
        except EnterpriseError:
            pass

    return git_email, matched_uid, matched_username, members_available


def unfinished_issues(enterprise_id, program_id=None):
    if not enterprise_id:
        return []
    git_email, matched_uid, matched_username, members_available = _resolve_current_enterprise_user(enterprise_id)
    base_args: dict = {'enterprise_id': enterprise_id}
    if program_id:
        base_args['program_ids'] = str(program_id)
    server_filtered = False
    if matched_uid is not None:
        base_args['assignee_id'] = str(matched_uid)
        server_filtered = True
    try:
        rows = _paged('list_enterprise_issues', {**base_args, 'state': 'open'})
    except EnterpriseError:
        try:
            rows = _paged('list_enterprise_issues', dict(base_args))
            rows = [
                r for r in rows
                if str(r.get('state') or '').lower()
                not in ('closed', 'finished', 'resolved', 'done', 'rejected', '已关闭', '已解决', '已完成', '已拒绝')
            ]
        except EnterpriseError:
            return []
    items = []
    seen_ids: set[int] = set()
    for r in rows:
        if type(r.get('id')) is int and r['id'] > 0:
            if r['id'] in seen_ids:
                continue
            if not _issue_belongs_to_user(
                r,
                git_email,
                matched_uid,
                matched_username,
                members_available,
                server_filtered=server_filtered,
            ):
                continue
            seen_ids.add(r['id'])
            ident = str(r.get('ident') or r['id'])
            date_val = _extract_issue_date(r) or str(r.get('plan_started_at') or r.get('created_at') or '')[:10]
            dur = _issue_minutes(r)
            items.append({
                'id': str(r['id']),
                'issue_id': str(r['id']),
                'ident': ident,
                'title': str(r.get('title') or ''),
                'description': str(r.get('description') or ''),
                'state': str(r.get('state') or 'open'),
                'program_id': r.get('program_id'),
                'program': str(r.get('program_name') or ''),
                'duration': dur,
                'minutes': dur,
                'date': date_val,
                'url': safe_url(r.get('issue_url')),
                'source': 'gitee_enterprise',
            })
    return items


def fetch_all_issues(enterprise_id, since=None, state='all', raise_on_error=False):
    if not enterprise_id:
        return []
    git_email, matched_uid, matched_username, members_available = _resolve_current_enterprise_user(enterprise_id)
    base_args: dict = {'enterprise_id': enterprise_id}
    server_filtered = False
    if matched_uid is not None:
        base_args['assignee_id'] = str(matched_uid)
        server_filtered = True
    args = {**base_args, 'state': state} if (state and state != 'all') else dict(base_args)
    try:
        rows = _paged('list_enterprise_issues', args)
    except EnterpriseError as first_exc:
        if raise_on_error and any(k in str(first_exc) for k in ('Token', '认证', '超时', '连接失败', 'npx', 'Node.js', 'HTTP 401', 'HTTP 403', 'HTTP 404')):
            raise
        try:
            open_rows = _paged('list_enterprise_issues', {**base_args, 'state': 'open'})
            closed_rows = _paged('list_enterprise_issues', {**base_args, 'state': 'closed'})
            rows = open_rows + closed_rows
        except EnterpriseError:
            try:
                rows = _paged('list_enterprise_issues', dict(base_args))
            except EnterpriseError as third_exc:
                if raise_on_error:
                    raise third_exc from None
                return []
    items = []
    seen_ids: set[int] = set()
    for r in rows:
        if type(r.get('id')) is int and r['id'] > 0:
            if r['id'] in seen_ids:
                continue
            if not _issue_belongs_to_user(
                r,
                git_email,
                matched_uid,
                matched_username,
                members_available,
                server_filtered=server_filtered,
            ):
                continue
            seen_ids.add(r['id'])
            created_at = _extract_issue_date(r) or str(r.get('plan_started_at') or r.get('created_at') or '')[:10]
            if since and created_at and created_at < str(since):
                continue
            prog_name = str(r.get('program_name') or '')
            if not prog_name and isinstance(r.get('program'), dict):
                prog_name = str(r['program'].get('name', ''))
            prog_id = r.get('program_id')
            if prog_id is None and isinstance(r.get('program'), dict):
                prog_id = r['program'].get('id')
            dur = _issue_minutes(r)
            items.append({
                'id': str(r['id']),
                'issue_id': str(r['id']),
                'ident': str(r.get('ident') or r['id']),
                'title': str(r.get('title') or ''),
                'details': str(r.get('description') or ''),
                'description': str(r.get('description') or ''),
                'state': str(r.get('state') or 'open'),
                'program_id': prog_id,
                'duration': dur,
                'minutes': dur,
                'plan_started_at': str(r.get('plan_started_at') or ''),
                'created_at': str(r.get('created_at') or ''),
                'project': prog_name,
                'program_name': prog_name,
                'date': created_at,
                'work_date': created_at,
                'url': safe_url(r.get('issue_url')),
                'source': 'gitee_enterprise',
                'source_id': str(r.get('ident') or r['id']),
            })
    return items


_orig_programs = programs
_orig_members = members
_orig_paged = _paged
_orig_issue_types = issue_types
_orig_issue_type_states = issue_type_states
_orig_fetch_all_issues = fetch_all_issues
_orig_unfinished_issues = unfinished_issues
_orig_tool = _tool


def _save_local_enterprise_issues(conn, enterprise_id: int, issues: list[dict], prog_map: dict | None = None) -> tuple[list[dict], str]:
    from .service import upsert, now
    prog_map = prog_map or {}
    cache_key = f'gitee-enterprise-issues:{enterprise_id}'
    existing_blob = conn.execute(
        select(db.integrations.c.value).where(db.integrations.c.id == cache_key)
    ).scalar() or {}
    by_id: dict[str, dict] = {}
    for old in existing_blob.get('items', []):
        if isinstance(old, dict):
            oid = str(old.get('issue_id') or old.get('id') or '').strip()
            if oid:
                by_id[oid] = dict(old)

    ts = now()
    for item in issues:
        if not isinstance(item, dict):
            continue
        iid = str(item.get('issue_id') or item.get('id') or '').strip()
        if not iid:
            continue
        ext_date = _extract_issue_date(item) or str(item.get('issue_date') or item.get('date') or item.get('work_date') or '')[:10]
        dur = _issue_minutes(item)
        prog_id = item.get('program_id')
        prog_name = (
            prog_map.get(prog_id, '')
            or str(item.get('program_name') or item.get('project') or '')
        )
        normalized_item = {
            **by_id.get(iid, {}),
            **item,
            'id': iid,
            'issue_id': iid,
            'title': str(item.get('title') or ''),
            'description': str(item.get('description') or item.get('details') or ''),
            'details': str(item.get('description') or item.get('details') or ''),
            'state': str(item.get('state') or 'open'),
            'program_id': prog_id,
            'program_name': prog_name,
            'project': prog_name,
            'duration': dur,
            'minutes': dur,
            'hours': round(dur / 60.0, 2),
            'issue_date': ext_date,
            'date': ext_date,
            'work_date': ext_date,
            'url': safe_url(item.get('url') or item.get('issue_url')),
            'source': 'gitee_enterprise',
            'synced_at': ts,
        }
        by_id[iid] = normalized_item
        upsert(
            conn,
            db.history,
            {
                'id': f'gitee-ent:{enterprise_id}:{iid}',
                'title': normalized_item['title'],
                'details': _issue_description(normalized_item),
                'project': prog_name,
                'work_date': ext_date,
                'source': 'gitee_enterprise',
                'source_id': iid,
                'url': normalized_item['url'],
                'imported_at': ts,
            },
            'id',
        )

    merged_list = sorted(
        by_id.values(),
        key=lambda x: (str(x.get('issue_date') or ''), str(x.get('issue_id') or '')),
        reverse=True,
    )[:2000]
    upsert(
        conn,
        db.integrations,
        {'id': cache_key, 'value': {'items': merged_list, 'synced_at': ts}},
        'id',
    )
    return merged_list, ts


def _load_local_enterprise_issues(conn, enterprise_id: int, prog_map: dict | None = None) -> tuple[list[dict], str | None]:
    prog_map = prog_map or {}
    cache_key = f'gitee-enterprise-issues:{enterprise_id}'
    cached_blob = conn.execute(
        select(db.integrations.c.value).where(db.integrations.c.id == cache_key)
    ).scalar() or {}
    synced_at = cached_blob.get('synced_at')
    by_id: dict[str, dict] = {}

    for item in cached_blob.get('items', []):
        if isinstance(item, dict):
            iid = str(item.get('issue_id') or item.get('id') or '').strip()
            if iid:
                by_id[iid] = dict(item)

    # Merge any locally published/linked tasks from gitee_links + tasks + worklogs
    linked_rows = conn.execute(
        select(
            db.gitee_links,
            db.tasks.c.date.label('task_date'),
            db.tasks.c.title.label('task_title'),
            db.tasks.c.details.label('task_details'),
            db.tasks.c.project.label('task_project'),
            db.tasks.c.status.label('task_status'),
            db.worklogs.c.confirmed_minutes.label('task_minutes'),
        )
        .select_from(
            db.gitee_links.outerjoin(db.tasks, db.gitee_links.c.task_id == db.tasks.c.id)
            .outerjoin(db.worklogs, db.tasks.c.id == db.worklogs.c.task_id)
        )
        .where(db.gitee_links.c.enterprise_id == enterprise_id)
    ).mappings().all()

    for r in linked_rows:
        iid = str(r.get('issue_id') or '').strip()
        if not iid or r.get('status') != 'created':
            continue
        existing = by_id.get(iid, {})
        dur = int(existing.get('duration') or r.get('task_minutes') or 0)
        prog_id = existing.get('program_id') or r.get('program_id')
        prog_name = (
            prog_map.get(prog_id, '')
            or existing.get('program_name')
            or str(r.get('task_project') or '')
        )
        t_date = existing.get('issue_date') or str(r.get('task_date') or '')
        t_status = str(r.get('task_status') or '')
        state_val = existing.get('state') or (
            'closed' if t_status in ('delivered', 'verified')
            else 'progressing' if t_status in ('attempted', 'changed')
            else 'open'
        )
        by_id[iid] = {
            **existing,
            'id': iid,
            'issue_id': iid,
            'title': existing.get('title') or str(r.get('task_title') or ''),
            'description': existing.get('description') or str(r.get('task_details') or ''),
            'details': existing.get('details') or str(r.get('task_details') or ''),
            'state': state_val,
            'program_id': prog_id,
            'program_name': prog_name,
            'project': prog_name,
            'duration': dur,
            'minutes': dur,
            'hours': round(dur / 60.0, 2),
            'issue_date': t_date,
            'date': t_date,
            'work_date': t_date,
            'url': safe_url(existing.get('url') or r.get('url') or ''),
            'linked_task_id': str(r.get('task_id') or ''),
            'source': 'gitee_enterprise',
        }

    # Also merge history table records from gitee / gitee_enterprise
    hist_rows = conn.execute(
        select(db.history)
        .where(db.history.c.source.in_(['gitee_enterprise', 'gitee']))
        .order_by(db.history.c.work_date.desc())
        .limit(500)
    ).mappings().all()
    for hr in hist_rows:
        iid = str(hr.get('source_id') or '').strip()
        if not iid or iid in by_id:
            continue
        w_date = str(hr.get('work_date') or '')[:10]
        by_id[iid] = {
            'id': iid,
            'issue_id': iid,
            'title': str(hr.get('title') or ''),
            'description': str(hr.get('details') or ''),
            'details': str(hr.get('details') or ''),
            'state': 'open',
            'program_id': None,
            'program_name': str(hr.get('project') or ''),
            'project': str(hr.get('project') or ''),
            'duration': 0,
            'minutes': 0,
            'hours': 0.0,
            'issue_date': w_date,
            'date': w_date,
            'work_date': w_date,
            'url': safe_url(hr.get('url') or ''),
            'linked_task_id': '',
            'source': 'gitee_enterprise',
        }

    items = sorted(
        by_id.values(),
        key=lambda x: (str(x.get('issue_date') or ''), str(x.get('issue_id') or '')),
        reverse=True,
    )
    return items, synced_at


def _state_matches(issue_state: str, state_filter: str) -> bool:
    if not state_filter or state_filter == 'all':
        return True
    s = (issue_state or 'open').strip().lower()
    if state_filter == 'closed':
        return s in ('closed', 'finished', 'resolved', 'done', '已完成', '已关闭', '已解决')
    if state_filter == 'progressing':
        return s in ('progressing', 'in_progress', 'doing', '进行中')
    if state_filter == 'open':
        return s not in ('closed', 'finished', 'resolved', 'done', '已完成', '已关闭', '已解决', 'progressing', 'in_progress', 'doing', '进行中')
    return True


def issues_for_date(engine, target_date: str, program_id: int | None = None, state: str = 'all', source: str = 'auto') -> dict:
    from .service import get_settings
    with engine.connect() as conn:
        settings = get_settings(conn)
        enterprise_id = settings.gitee_enterprise.enterprise_id
        if not enterprise_id:
            if source != 'local':
                raise ValueError('请先在系统设置选择Gitee企业并保存')
            catalog = []
            links_by_issue = {}
            task_dates = {
                r['id']: r['date']
                for r in conn.execute(select(db.tasks.c.id, db.tasks.c.date).where(db.tasks.c.deleted == False)).mappings()
            }
            prog_map = {}
            local_issues, synced_at = _load_local_enterprise_issues(conn, 0, prog_map)
        else:
            cached = conn.execute(
                select(db.integrations.c.value).where(
                    db.integrations.c.id == f'gitee-enterprise-programs:{enterprise_id}'
                )
            ).scalar() or {}
            catalog = cached.get('items', [])
            links_by_issue = {
                str(r['issue_id']): dict(r)
                for r in conn.execute(
                    select(db.gitee_links).where(db.gitee_links.c.enterprise_id == enterprise_id)
                ).mappings()
                if r['issue_id']
            }
            task_dates = {
                r['id']: r['date']
                for r in conn.execute(select(db.tasks.c.id, db.tasks.c.date).where(db.tasks.c.deleted == False)).mappings()
            }
            prog_map = {p['id']: p['name'] for p in catalog if isinstance(p, dict) and 'id' in p}
            local_issues, synced_at = _load_local_enterprise_issues(conn, enterprise_id, prog_map)

    if source == 'local':
        all_issues = local_issues
    else:
        if not catalog:
            try:
                catalog = programs(enterprise_id)
                prog_map = {p['id']: p['name'] for p in catalog if isinstance(p, dict) and 'id' in p}
            except Exception:
                catalog = []
        remote_issues = fetch_all_issues(
            enterprise_id,
            since=target_date if (target_date != 'all' and source == 'auto') else None,
            state=state if source == 'auto' else 'all',
            raise_on_error=(source == 'remote'),
        )
        with engine.begin() as conn:
            merged_local, synced_at = _save_local_enterprise_issues(conn, enterprise_id, remote_issues, prog_map)
            local_issues, _ = _load_local_enterprise_issues(conn, enterprise_id, prog_map)
        # Keep any remote_issues order/items while enriching with local persistence
        remote_ids = {str(r.get('issue_id') or r.get('id') or '') for r in remote_issues}
        all_issues = list(remote_issues) + [
            li for li in local_issues
            if str(li.get('issue_id') or li.get('id') or '') not in remote_ids
        ]

    filtered = []
    for issue in all_issues:
        issue_id_str = str(issue.get('issue_id') or issue.get('id') or '')
        ext_date = _extract_issue_date(issue) or str(issue.get('issue_date') or '')
        linked_info = links_by_issue.get(issue_id_str)
        linked_task_date = task_dates.get(linked_info['task_id']) if linked_info else None
        date_matched = (
            target_date == 'all'
            or ext_date == str(target_date)
            or (linked_info is not None and linked_task_date == str(target_date))
        )
        if not date_matched:
            continue
        if program_id is not None and issue.get('program_id') != program_id:
            continue
        if not _state_matches(str(issue.get('state') or 'open'), state):
            continue
        enriched = dict(issue)
        enriched['issue_id'] = issue_id_str
        enriched['issue_date'] = ext_date or linked_task_date or (str(target_date) if target_date != 'all' else '')
        enriched['program_name'] = prog_map.get(issue.get('program_id'), '') or issue.get('program_name', '') or issue.get('project', '')
        dur = _issue_minutes(issue)
        enriched['duration'] = dur
        enriched['hours'] = round(dur / 60.0, 2)
        linked_tid = linked_info.get('task_id', '') if linked_info else str(issue.get('linked_task_id') or '')
        enriched['linked_task_id'] = linked_tid if linked_tid in task_dates else ''
        enriched['saved_locally'] = True
        filtered.append(enriched)
    return {
        'date': str(target_date),
        'enterprise_id': enterprise_id,
        'enterprise_configured': bool(enterprise_id),
        'total': len(filtered),
        'local_total': len(local_issues),
        'synced_at': synced_at,
        'source': source,
        'items': filtered,
        'programs': catalog,
    }


def retain_issues_locally(engine, target_date: str, issue_ids: list[str] | None = None) -> dict:
    """Ensure remote Gitee issues are retained both on Gitee server and as local work_tasks."""
    from .service import get_settings, upsert, invalidate, now
    from sqlalchemy import insert
    with engine.begin() as conn:
        settings = get_settings(conn)
        enterprise_id = settings.gitee_enterprise.enterprise_id
        if not enterprise_id:
            raise ValueError('请先在系统设置选择Gitee企业并保存')
        cached = conn.execute(
            select(db.integrations.c.value).where(
                db.integrations.c.id == f'gitee-enterprise-programs:{enterprise_id}'
            )
        ).scalar() or {}
        prog_map = {p['id']: p['name'] for p in cached.get('items', []) if isinstance(p, dict) and 'id' in p}
        local_issues, _ = _load_local_enterprise_issues(conn, enterprise_id, prog_map)
        links_by_issue = {
            str(r['issue_id']): dict(r)
            for r in conn.execute(
                select(db.gitee_links).where(db.gitee_links.c.enterprise_id == enterprise_id)
            ).mappings()
            if r['issue_id']
        }
        active_task_ids = set(
            conn.execute(select(db.tasks.c.id).where(db.tasks.c.deleted == False)).scalars()
        )

        wanted_ids = {str(i).strip() for i in (issue_ids or []) if str(i).strip()}
        created_tasks = []
        already_linked = []
        affected_dates = set()

        for issue in local_issues:
            iid = str(issue.get('issue_id') or issue.get('id') or '').strip()
            if not iid:
                continue
            if wanted_ids and iid not in wanted_ids:
                continue
            issue_day = _extract_issue_date(issue) or str(issue.get('issue_date') or '') or (
                str(target_date) if target_date and target_date != 'all' else datetime.now(timezone.utc).strftime('%Y-%m-%d')
            )
            if not wanted_ids and target_date != 'all' and issue_day != str(target_date):
                continue

            existing_link = links_by_issue.get(iid)
            if existing_link and existing_link.get('task_id') in active_task_ids:
                already_linked.append(iid)
                continue

            new_task_id = uuid4().hex
            dur = _issue_minutes(issue)
            proj = str(issue.get('program_name') or issue.get('project') or '未归属项目')
            state_str = str(issue.get('state') or 'open').lower()
            local_status = (
                'delivered' if state_str in ('closed', 'finished', 'resolved', 'done', '已完成', '已关闭')
                else 'attempted' if state_str in ('progressing', 'in_progress', 'doing', '进行中')
                else 'requested'
            )
            conn.execute(
                insert(db.tasks).values(
                    id=new_task_id,
                    date=issue_day,
                    title=str(issue.get('title') or f'Gitee 工作项 #{iid}')[:500],
                    details=_issue_description(issue),
                    project=proj,
                    category='任务',
                    status=local_status,
                    origin='gitee',
                    confidence='Gitee 企业远端同步保留',
                    evidence_level='Gitee 企业工作项',
                    fingerprint=f'gitee-ent-retain:{enterprise_id}:{iid}',
                    edited=True,
                    deleted=False,
                )
            )
            conn.execute(
                insert(db.worklogs).values(
                    task_id=new_task_id,
                    suggested_minutes=dur if dur > 0 else None,
                    confirmed_minutes=dur,
                    time_locked=bool(dur > 0),
                    time_source='manual',
                    time_reason='从 Gitee 企业工作项同步保留',
                    is_confirmed=False,
                )
            )
            link_id = stable(enterprise_id, new_task_id)
            upsert(
                conn,
                db.gitee_links,
                {
                    'id': link_id,
                    'enterprise_id': enterprise_id,
                    'task_id': new_task_id,
                    'program_id': int(issue.get('program_id') or 0),
                    'status': 'created',
                    'issue_id': iid,
                    'url': safe_url(issue.get('url')),
                    'error': '',
                    'updated_at': now(),
                },
                'id',
            )
            created_tasks.append({'issue_id': iid, 'task_id': new_task_id, 'date': issue_day, 'title': issue.get('title')})
            affected_dates.add(issue_day)

        for d in affected_dates:
            invalidate(conn, d)

    return {
        'created_count': len(created_tasks),
        'already_linked_count': len(already_linked),
        'created_tasks': created_tasks,
    }


def normalized(text):
    return re.sub(r'[\W_]+','',text.casefold())


def prefix(task):
    match=re.match(r'^\s*[【\[]([^】\]]+)[】\]]',task['title'])
    return match.group(1).strip() if match else task.get('project','').strip()


def _blank_program(catalog):
    for r in catalog:
        name = str(r.get('name') or '').strip()
        ident = str(r.get('ident') or '').strip()
        if name == '空' or ident == '空':
            return r['id']
    return None


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
    blank_id=_blank_program(catalog)
    if not word or word in ('其他','其它','未归属项目','空','none','null'):
        if blank_id is not None:
            return blank_id,'默认匹配企业「空」项目'
        return None,'没有项目前缀，请手动选择'
    exact=[r for r in catalog if word in (normalized(r['name']),normalized(r['ident']))]
    candidates=exact or ([r for r in catalog if word in normalized(r['name']) or word in normalized(r['ident'])] if len(word)>=2 else [])
    if '上海工程' in key and len(candidates)>1:
        non_2026=[r for r in candidates if '2026' not in normalized(r['name'])]
        if non_2026:
            candidates=non_2026
    if len(candidates)==1:
        return candidates[0]['id'],'根据项目前缀唯一匹配'
    if not candidates:
        if blank_id is not None:
            return blank_id,'未匹配到指定项目，默认归入企业「空」项目'
        return None,'未匹配到项目，请选择或预设映射'
    return None,'匹配到多个项目，请选择'


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
    keys=('id','date','title','details','project','category','confirmed_minutes')
    return [{k:t[k] for k in keys} for t in rows]


def _matched_issue_payload(issue, confidence, status):
    try:
        issue_id = int(issue.get('issue_id') or issue.get('id'))
    except (TypeError, ValueError):
        return None
    if issue_id <= 0:
        return None
    return {
        'issue_id': issue_id,
        'title': str(issue.get('title') or ''),
        'confidence': float(confidence),
        'status': str(status),
        'url': safe_url(issue.get('url')),
        'existing_description': _issue_description(issue),
        'existing_minutes': int(_issue_minutes(issue)),
        'will_replace': True,
    }


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
    cached_members=conn.execute(select(db.integrations.c.value).where(db.integrations.c.id==f'gitee-enterprise-members:{settings.gitee_enterprise.enterprise_id}')).scalar() or {}
    return {'configured':bool(token()),'config':settings.gitee_enterprise.model_dump(mode='json'),'common_prefixes':common[:30],'programs':cached.get('items',[]),'members':cached_members.get('items',[])}


def cache_members(engine, enterprise_id: int) -> list[dict]:
    from .service import upsert, now
    member_list = members(enterprise_id)
    with engine.begin() as conn:
        upsert(
            conn,
            db.integrations,
            {'id': f'gitee-enterprise-members:{enterprise_id}', 'value': {'items': member_list, 'fetched_at': now()}},
            'id',
        )
    return member_list


def cache_programs(engine,enterprise_id):
    from .service import upsert,now,get_settings,save_settings
    catalog=programs(enterprise_id)
    with engine.begin() as conn:
        upsert(conn,db.integrations,{'id':f'gitee-enterprise-programs:{enterprise_id}','value':{'items':catalog,'fetched_at':now()}},'id')
        # 同步保存为人工事项可选项目；设置变更会触发云同步写入服务器。
        settings=get_settings(conn)
        names=list(dict.fromkeys(r.get('name', '') for r in catalog if r.get('name')))[:500]
        if names and names!=settings.project_options:
            settings.project_options=names
            save_settings(conn,settings)
    return catalog


def preview(engine,payload,cloud_client=None):
    from .service import get_settings,upsert,now
    from . import multiday
    c_client = cloud_client
    server_sync_enabled = False
    try:
        from . import cloud_sync
        from .cloud_client import CloudClient
        with engine.connect() as conn:
            bound = bool(cloud_sync.get_meta(conn, 'bound', False))
            server_url = cloud_sync.get_meta(conn, 'server_url', '')
        server_sync_enabled = bool(bound or c_client is not None)
        if not c_client and bound and server_url:
            c_token = gitee.token(service='cloud-session')
            if c_token:
                c_client = CloudClient(base_url=server_url, token=c_token)
        if c_client and server_sync_enabled:
            cloud_sync.sync_once(engine, c_client)
    except Exception:
        pass
    with engine.connect() as conn:
        settings=get_settings(conn);config=settings.gitee_enterprise
        tasks=task_snapshot(conn,payload.date,payload.task_ids)
        existing_links={r['task_id']:dict(r) for r in conn.execute(select(db.gitee_links).where(db.gitee_links.c.enterprise_id==config.enterprise_id)).mappings()}
        binding = multiday.get_active_binding(conn, payload.date)
        range_start = binding['start_date'] if binding else str(payload.date)
        range_end = binding['end_date'] if binding else str(payload.date)
        cached_prog_blob = conn.execute(
            select(db.integrations.c.value).where(
                db.integrations.c.id == f'gitee-enterprise-programs:{config.enterprise_id}'
            )
        ).scalar() or {}
        cached_catalog = cached_prog_blob.get('items', []) if isinstance(cached_prog_blob, dict) else []
        local_prog_map = {
            p['id']: p['name']
            for p in cached_catalog
            if isinstance(p, dict) and 'id' in p
        }
        local_cached_issues, _ = _load_local_enterprise_issues(conn, config.enterprise_id, local_prog_map)
    if not config.enterprise_id:
        raise ValueError('请先在系统设置选择Gitee企业并保存')
    if programs is not _orig_programs or _paged is not _orig_paged or not cached_catalog:
        catalog=programs(config.enterprise_id)
    else:
        catalog=cached_catalog
    prog_map = {p['id']: p['name'] for p in catalog if isinstance(p, dict) and 'id' in p}
    items=[]
    dedup=settings.gitee_dedup
    matched_issues={}

    # 只匹配本地已拉取/已留存的工作项，以及启用的服务端同步记录；不实时请求 gitee.com。
    candidate_by_id: dict[str, dict] = {}
    for li in local_cached_issues:
        rid = str(li.get('issue_id') or li.get('id') or '').strip()
        if not rid:
            continue
        item_copy = dict(li)
        if not item_copy.get('program_name') and item_copy.get('program_id') in prog_map:
            item_copy['program_name'] = prog_map[item_copy['program_id']]
            item_copy['project'] = item_copy['program_name']
        candidate_by_id[rid] = item_copy

    if c_client and server_sync_enabled:
        try:
            hist_res = c_client.list_cloud_history(
                page=1,
                limit=100,
                date_from=range_start,
                date_to=range_end,
            )
            for hr in hist_res.get('items', []):
                if str(hr.get('source') or '') not in ('gitee_enterprise', 'gitee', 'gitee_v5'):
                    continue
                sid = str(hr.get('source_id') or '').strip()
                if not sid or not sid.isdigit():
                    continue
                w_date = str(hr.get('work_date') or '')[:10]
                proj = str(hr.get('project') or '')
                matched_pid = next((pid for pid, pname in prog_map.items() if pname == proj), None)
                prev_entry = candidate_by_id.get(sid, {})
                candidate_by_id[sid] = {
                    **prev_entry,
                    'id': sid,
                    'issue_id': sid,
                    'title': prev_entry.get('title') or str(hr.get('title') or ''),
                    'description': prev_entry.get('description') or str(hr.get('details') or ''),
                    'details': prev_entry.get('details') or str(hr.get('details') or ''),
                    'state': prev_entry.get('state') or 'closed',
                    'program_id': prev_entry.get('program_id') or matched_pid,
                    'program_name': prev_entry.get('program_name') or proj,
                    'project': prev_entry.get('project') or proj,
                    'duration': int(prev_entry.get('duration') or 0),
                    'minutes': int(prev_entry.get('minutes') or 0),
                    'issue_date': prev_entry.get('issue_date') or w_date,
                    'date': prev_entry.get('date') or w_date,
                    'work_date': prev_entry.get('work_date') or w_date,
                    'url': safe_url(prev_entry.get('url') or hr.get('url') or ''),
                    'source': prev_entry.get('source') or 'server_history',
                }
        except Exception:
            pass

        try:
            reqs = c_client.list_publish_requests(limit=100)
            scope_prefix = f'ent:{config.enterprise_id}'
            for req in reqs:
                if req.get('target_scope') != scope_prefix:
                    continue
                req_status = str(req.get('status') or req.get('state') or '').lower()
                if req_status in ('failed', 'cancelled'):
                    continue
                rid = str(req.get('remote_id') or '').strip()
                if not rid or not rid.isdigit():
                    continue
                p_obj = req.get('payload') if isinstance(req.get('payload'), dict) else {}
                t_obj = req.get('target') if isinstance(req.get('target'), dict) else {}
                pid = t_obj.get('program_id')
                pname = prog_map.get(pid, '')
                dur = int(p_obj.get('minutes') or p_obj.get('duration') or 0)
                w_date = _extract_issue_date(p_obj) or str(req.get('updated_at') or req.get('created_at') or '')[:10]
                prev_entry = candidate_by_id.get(rid, {})
                candidate_by_id[rid] = {
                    **prev_entry,
                    'id': rid,
                    'issue_id': rid,
                    'title': prev_entry.get('title') or str(p_obj.get('title') or ''),
                    'description': prev_entry.get('description') or str(p_obj.get('description') or ''),
                    'details': prev_entry.get('details') or str(p_obj.get('description') or ''),
                    'state': prev_entry.get('state') or ('closed' if req_status == 'succeeded' else 'progressing'),
                    'program_id': prev_entry.get('program_id') or pid,
                    'program_name': prev_entry.get('program_name') or pname,
                    'project': prev_entry.get('project') or pname,
                    'duration': int(prev_entry.get('duration') or dur),
                    'minutes': int(prev_entry.get('minutes') or dur),
                    'issue_date': prev_entry.get('issue_date') or w_date,
                    'date': prev_entry.get('date') or w_date,
                    'work_date': prev_entry.get('work_date') or w_date,
                    'url': safe_url(prev_entry.get('url') or req.get('url') or ''),
                    'source': prev_entry.get('source') or 'server_publish',
                }
        except Exception:
            pass

    linked_issue_ids = {
        str(existing_links[t['id']]['issue_id'])
        for t in tasks
        if t['id'] in existing_links and existing_links[t['id']].get('issue_id')
    }
    remote_by_id = dict(candidate_by_id)
    remote_day_issues = []
    for r in candidate_by_id.values():
        rid = str(r.get('issue_id') or r.get('id') or '')
        issue_day = _extract_issue_date(r) or str(r.get('issue_date') or '')[:10]
        if (issue_day and range_start <= issue_day <= range_end) or (rid and rid in linked_issue_ids):
            remote_day_issues.append(r)

    if remote_day_issues and dedup.enabled and dedup.dedup_on_publish:
        from . import matching
        try:
            match_res=matching.match_tasks_with_remote(
                tasks,
                remote_day_issues,
                threshold_high=dedup.threshold_high,
                threshold_low=dedup.threshold_low,
                date_weight=dedup.date_weight,
            )
            for m in match_res['auto_matched']:
                matched = _matched_issue_payload(m['remote_issue'], m['confidence'], 'auto_matched')
                if matched:
                    matched_issues[m['local_task']['id']] = matched
            for amb in match_res['ambiguous']:
                for cand in amb.get('candidates',[]):
                    tid=cand['local_task_id']
                    if tid not in matched_issues:
                        matched = _matched_issue_payload(amb['remote_issue'], cand['confidence'], 'ambiguous')
                        if matched:
                            matched_issues[tid] = matched
        except Exception:
            pass

    for task in tasks:
        tid = task['id']
        if tid not in matched_issues and remote_day_issues:
            norm_title = normalized(task['title'])
            for r in remote_day_issues:
                if norm_title and normalized(r.get('title', '')) == norm_title:
                    matched = _matched_issue_payload(r, 1.0, 'exact_day_match')
                    if matched:
                        matched_issues[tid] = matched
                        break
        link = existing_links.get(tid)
        if tid not in matched_issues and link and link.get('status') == 'created' and str(link.get('issue_id') or '').isdigit():
            rem = remote_by_id.get(str(link['issue_id']))
            if rem:
                matched = _matched_issue_payload(rem, 1.0, 'linked')
                if matched:
                    matched_issues[tid] = matched
            else:
                matched_issues[tid] = {
                    'issue_id': int(link['issue_id']),
                    'title': task['title'],
                    'confidence': 1.0,
                    'status': 'linked',
                    'url': safe_url(link.get('url', '')),
                    'existing_description': '',
                    'existing_minutes': int(task['confirmed_minutes']),
                    'will_replace': True,
                }

    for task in tasks:
        program_id,reason=match_program(task,catalog,config.rules)
        description=f"工作日期：{task['date']}\n预估工时：{task['confirmed_minutes']/60:g}h（待人工核对）"
        if config.include_details and task['details']:
            description+='\n\n'+redact(task['details'],settings.redact_patterns)
        marker=stable('awt-work-item',config.enterprise_id,task['id'])
        description+=f'\n\n<!-- awt-work-item:{marker} -->'
        items.append({'task_id':task['id'],'title':redact(task['title'],settings.redact_patterns),'description':description,
                      'minutes':task['confirmed_minutes'],'prefix':prefix(task),'program_id':program_id,'match_reason':reason,
                      'matched_issue':matched_issues.get(task['id'])})
    snapshot={'date':str(payload.date),'created_at':now(),'enterprise_id':config.enterprise_id,'account':stable(token()),
              'tasks':tasks,'config':config.model_dump(mode='json'),'programs':catalog,'items':items}
    batch_id=uuid4().hex
    with engine.begin() as conn:
        if task_snapshot(conn,payload.date,[t['id'] for t in tasks])!=tasks or get_settings(conn).gitee_enterprise!=config:
            raise ValueError('读取项目期间工作项或映射设置发生变化，请重新预览')
        upsert(conn,db.gitee_batches,{'id':batch_id,'snapshot':snapshot},'id')
        links={r['task_id']:dict(r) for r in conn.execute(select(db.gitee_links).where(db.gitee_links.c.enterprise_id==config.enterprise_id)).mappings()}

    assignee_summary = (
        {
            'id': config.assignee_id,
            'name': config.assignee_name,
            'username': config.assignee_username,
            'email': config.assignee_email,
        }
        if config.assignee_id
        else None
    )
    return {'batch_id':batch_id,'date':str(payload.date),'enterprise_id':config.enterprise_id,'programs':catalog,
            'assignee':assignee_summary,
            'remote_day_issues':remote_day_issues,
            'items':[{**i,'existing':links.get(i['task_id'])} for i in items]}


def safe_url(value):
    if not isinstance(value,str):return ''
    parsed=urlparse(value)
    return value if parsed.scheme=='https' and parsed.hostname in ('gitee.com','e.gitee.com') and not parsed.username and not parsed.password else ''


def publish(engine,payload,cloud_client=None):
    from .service import get_settings,upsert,now
    if not publish_lock.acquire(blocking=False):
        raise ValueError('Gitee创建正在进行，请等待完成；不会重复发送')
    try:
        from . import cloud_sync
        from .cloud_client import CloudClient, CloudUnavailable, CloudError
        with engine.connect() as conn:
            bound = cloud_sync.get_meta(conn, 'bound', False)
            unified = cloud_sync.get_meta(conn, 'unified_publish_enabled', False)
            server_url = cloud_sync.get_meta(conn, 'server_url', '')

        is_unified = bound and unified
        c_client = cloud_client

        if is_unified:
            c_token = gitee.token(service='cloud-session')
            if not c_token and not c_client:
                raise EnterpriseError('云端会话已失效，请重新登录')
            if not c_client and server_url and c_token:
                c_client = CloudClient(base_url=server_url, token=c_token)
            if not c_client:
                raise EnterpriseError('云端客户端不可用')

            try:
                cloud_sync.sync_once(engine, c_client)
            except CloudUnavailable as exc:
                raise EnterpriseError(f'云端服务不可用，已取消提交: {exc}') from None
            except CloudError as exc:
                if exc.status == 401:
                    raise EnterpriseError('云端会话已失效，请重新登录') from None
                raise EnterpriseError(f'发布前云端同步失败: {exc}') from None

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

        req_by_wi = {}
        if is_unified and c_client:
            register_items = []
            for target in payload.targets:
                item = next(i for i in snapshot['items'] if i['task_id'] == target.task_id)
                with engine.connect() as conn:
                    shadow_row = conn.execute(
                        select(db.sync_shadow).where(and_(
                            db.sync_shadow.c.entity_type == 'work_item',
                            db.sync_shadow.c.entity_id == target.task_id,
                        ))
                    ).mappings().first()
                wi_version = max(1, shadow_row['version']) if shadow_row else 1
                register_items.append({
                    'work_item_id': target.task_id,
                    'work_item_version': wi_version,
                    'target_scope': f"ent:{enterprise_id}",
                    'operation': 'update' if target.existing_issue_id else 'create',
                    'remote_id': str(target.existing_issue_id) if target.existing_issue_id else '',
                    'target': {
                        'enterprise_id': enterprise_id,
                        'program_id': target.program_id,
                        'issue_type_id': target.issue_type_id,
                    },
                    'payload': {
                        'title': item['title'],
                        'description': item['description'],
                        'duration': item['minutes'],
                        'minutes': item['minutes'],
                    },
                    'config_versions': {},
                })

            try:
                batch_res = c_client.register_publish_batch(
                    idempotency_key=payload.batch_id,
                    executor='device',
                    items=register_items,
                )
            except CloudUnavailable as exc:
                raise EnterpriseError(f'云端连接失败，已暂停发送: {exc}') from None
            except CloudError as exc:
                if exc.status == 401:
                    raise EnterpriseError('云端会话已失效，请重新登录') from None
                raise EnterpriseError(f'云端发布登记失败: {exc.message}') from None

            for r in batch_res.get('requests', batch_res.get('items', [])):
                req_by_wi[r.get('work_item_id')] = r

        assignee_id = snapshot['config'].get('assignee_id')
        if type(assignee_id) is not int or assignee_id <= 0:
            assignee_id = None
        done_states_cache: dict[int, int | None] = {}
        succeeded_task_ids: list[str] = []
        results=[]
        for target in payload.targets:
            item=next(i for i in snapshot['items'] if i['task_id']==target.task_id)
            identity=stable(enterprise_id,target.task_id)
            req = req_by_wi.get(target.task_id)
            req_id = (req.get('id') or req.get('request_id')) if req else identity
            if req and req.get('operation_adjusted') and not target.existing_issue_id:
                raise ValueError(f"工作项已在远端存在({req.get('remote_id')})，服务端已调整为更新，请重新预览确认")

            with engine.connect() as conn:
                conn.exec_driver_sql('BEGIN IMMEDIATE')
                prior=conn.execute(select(db.gitee_links).where(db.gitee_links.c.id==identity)).mappings().first()
                wants_replace = bool(target.existing_issue_id or getattr(target, 'replace_existing', False))
                force_create = bool(getattr(target, 'force_create', False))
                if prior and prior['status'] in ('pending', 'uncertain'):
                    results.append(dict(prior));conn.commit();continue
                if prior and prior['status'] == 'created' and not wants_replace and not force_create:
                    succeeded_task_ids.append(target.task_id)
                    results.append({**dict(prior), 'action': 'skipped_existing', 'verified': True, 'remote_state': '已完成'});conn.commit();continue
                if task_snapshot(conn,snapshot['date'],[t['id'] for t in snapshot['tasks']])!=snapshot['tasks'] or get_settings(conn).gitee_enterprise.model_dump(mode='json')!=snapshot['config'] or stable(token())!=snapshot['account']:
                    raise ValueError('提交期间工作项、凭据或设置发生变化；已创建项保留，其余未继续发送，请重新预览')
                record={'id':identity,'enterprise_id':enterprise_id,'task_id':target.task_id,'program_id':target.program_id,
                        'status':'pending','issue_id':'','url':'','error':'','updated_at':now(),'publish_req_id':req_id if is_unified else None}
                upsert(conn,db.gitee_links,record,'id');conn.commit()

            if is_unified and c_client and req_id:
                try:
                    c_client.claim_publish_request(req_id)
                except Exception as exc:
                    err_msg = f'云端声明冲突或已被认领: {exc}'
                    record.update(status='failed', error=err_msg, updated_at=now())
                    with engine.begin() as conn:
                        upsert(conn, db.gitee_links, record, 'id')
                    results.append(record)
                    continue

            resolved_type_id = target.issue_type_id
            if resolved_type_id is None:
                prog_types = types.get(target.program_id, [])
                task_type = next(
                    (
                        t for t in prog_types
                        if str(t.get('name') or '').strip() == '任务' or t.get('category') == 'task'
                    ),
                    None,
                )
                if task_type is not None:
                    resolved_type_id = task_type['id']
            done_state_id = _find_done_state_id(enterprise_id, resolved_type_id, done_states_cache)

            matched_id = (item.get('matched_issue') or {}).get('issue_id')
            replace_issue_id = target.existing_issue_id or (
                int(prior['issue_id'])
                if (prior and prior.get('issue_id') and str(prior['issue_id']).isdigit() and wants_replace)
                else (int(matched_id) if (matched_id and wants_replace) else None)
            )
            remote_state_label = ''
            if replace_issue_id:
                action_taken = 'replaced'
                update_body={
                    'enterprise_id':enterprise_id,
                    'issue_id':str(replace_issue_id),
                    'title':item['title'],
                    'duration':item['minutes'],
                    'estimated_duration':round(item['minutes']/60.0,2),
                    'description':item['description'],
                }
                if assignee_id is not None:
                    update_body['assignee_id'] = assignee_id
                if done_state_id is not None:
                    update_body['issue_state_id'] = done_state_id
                try:
                    value=_tool('update_enterprise_issue',update_body,credential=publish_key,uncertain=True)
                    url_val=safe_url(value.get('issue_url') if isinstance(value,dict) else '') or (prior.get('url','') if prior else '')
                    record.update(status='created',issue_id=str(replace_issue_id),url=url_val,error='')
                    remote_state_label = '已完成'
                except EnterpriseError as exc:
                    record.update(status='uncertain' if exc.uncertain else 'failed',error=str(exc))
            else:
                action_taken = 'created'
                body={'title':item['title'],'description':item['description'],'program_id':target.program_id,
                      'duration':item['minutes'],'plan_started_at':snapshot['date']+'T00:00:00','category':'task'}
                if resolved_type_id is not None:
                    body['issue_type_id']=resolved_type_id
                    body.pop('category',None)
                if assignee_id is not None:
                    body['assignee_id'] = assignee_id
                try:
                    value=_tool('create_enterprise_issue',{'enterprise_id':enterprise_id,**body},credential=publish_key,uncertain=True)
                    if not isinstance(value,dict) or type(value.get('id')) is not int or value['id']<=0:
                        raise EnterpriseError('创建响应未提供有效工作项ID，请到Gitee核实，系统不会自动重发',uncertain=True)
                    record.update(status='created',issue_id=str(value['id']),url=safe_url(value.get('issue_url')))
                    eff_type_id = resolved_type_id
                    if not eff_type_id and isinstance(value, dict):
                        issue_type_obj = value.get('issue_type') if isinstance(value.get('issue_type'), dict) else {}
                        raw_tid = value.get('issue_type_id') or issue_type_obj.get('id')
                        if type(raw_tid) is int and raw_tid > 0:
                            eff_type_id = raw_tid
                    eff_done_id = _find_done_state_id(enterprise_id, eff_type_id, done_states_cache) if eff_type_id else None
                    if eff_done_id is not None:
                        try:
                            _tool(
                                'update_enterprise_issue',
                                {'enterprise_id': enterprise_id, 'issue_id': str(value['id']), 'issue_state_id': eff_done_id},
                                credential=publish_key,
                                uncertain=False,
                            )
                            remote_state_label = '已完成'
                        except EnterpriseError:
                            remote_state_label = '已创建（待确认已完成状态）'
                    else:
                        remote_state_label = '已完成'
                except EnterpriseError as exc:
                    record.update(status='uncertain' if exc.uncertain else 'failed',error=str(exc))
            record['updated_at']=now()
            with engine.begin() as conn:
                upsert(conn,db.gitee_links,record,'id')
                if record.get('status') == 'created' and record.get('issue_id'):
                    succeeded_task_ids.append(target.task_id)
                    _save_local_enterprise_issues(
                        conn,
                        enterprise_id,
                        [
                            {
                                'issue_id': str(record['issue_id']),
                                'title': item['title'],
                                'description': item['description'],
                                'state': 'closed',
                                'program_id': target.program_id,
                                'duration': item['minutes'],
                                'issue_date': str(snapshot['date']),
                                'url': record.get('url', ''),
                                'linked_task_id': target.task_id,
                            }
                        ],
                    )

            if is_unified and c_client and req_id:
                try:
                    c_stat = 'succeeded' if record['status'] == 'created' else record['status']
                    c_client.report_publish_result(
                        req_id,
                        outcome=c_stat,
                        remote_id=record['issue_id'],
                        url=record['url'],
                        error=record['error'],
                    )
                except Exception:
                    pass

            result_item = {
                **record,
                'action': action_taken,
                'verified': record['status'] == 'created',
                'remote_state': remote_state_label if record['status'] == 'created' else '',
            }
            results.append(result_item)
        if succeeded_task_ids:
            from .service import invalidate
            with engine.begin() as conn:
                conn.execute(
                    update(db.tasks)
                    .where(db.tasks.c.id.in_(list(dict.fromkeys(succeeded_task_ids))))
                    .values(status='delivered')
                )
                affected_dates = set(
                    conn.execute(select(db.tasks.c.date).where(db.tasks.c.id.in_(succeeded_task_ids))).scalars()
                )
                affected_dates.add(str(snapshot['date']))
                for d in affected_dates:
                    invalidate(conn, d)
        return {
            'items':results,
            'created':sum(r['status']=='created' for r in results),
            'replaced':sum(r.get('action')=='replaced' and r['status']=='created' for r in results),
            'failed':sum(r['status']=='failed' for r in results),
            'uncertain':sum(r['status'] in ('pending','uncertain') for r in results),
        }
    finally:
        publish_lock.release()


def verify_day_publish(engine, target_date: str) -> dict:
    from .service import get_settings, list_tasks, now
    with engine.connect() as conn:
        enterprise_id = get_settings(conn).gitee_enterprise.enterprise_id
        tasks = list_tasks(conn, target_date)
        links = {
            r['task_id']: dict(r)
            for r in conn.execute(
                select(db.gitee_links).where(db.gitee_links.c.enterprise_id == enterprise_id)
            ).mappings()
        }
    if not enterprise_id:
        raise ValueError('请先在系统设置选择Gitee企业并保存')
    verified_items = []
    for t in tasks:
        link = links.get(t['id'])
        if not link or not link.get('issue_id'):
            continue
        issue_id_str = str(link['issue_id'])
        item_info = {
            'task_id': t['id'],
            'title': t['title'],
            'issue_id': issue_id_str,
            'status': link.get('status', ''),
            'url': link.get('url', ''),
            'verified': False,
        }
        try:
            detail = _tool('get_enterprise_issue_detail', {'enterprise_id': enterprise_id, 'issue_id': issue_id_str})
            if isinstance(detail, dict) and detail.get('id'):
                item_info.update(
                    verified=True,
                    remote_state=str(detail.get('state') or ''),
                    remote_title=str(detail.get('title') or ''),
                    remote_duration=detail.get('duration', 0) or 0,
                    url=safe_url(detail.get('issue_url')) or link.get('url', ''),
                )
            else:
                item_info['verify_error'] = '远端未找到对应工作项'
        except EnterpriseError as exc:
            item_info['verified'] = False
            item_info['verify_error'] = str(exc)
        verified_items.append(item_info)
    return {
        'date': str(target_date),
        'enterprise_id': enterprise_id,
        'verified_at': now(),
        'items': verified_items,
    }


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
    value=_tool('get_enterprise_issue_detail',{'enterprise_id':payload.enterprise_id,'issue_id':str(payload.issue_id)})
    marker='<!-- awt-work-item:'+stable('awt-work-item',payload.enterprise_id,payload.task_id)+' -->'
    if not isinstance(value,dict) or value.get('id')!=payload.issue_id or value.get('program_id')!=record['program_id'] or not isinstance(value.get('description'),str) or marker not in value['description']:
        raise ValueError('该Gitee工作项与本次提交的项目或来源标记不匹配，未建立关联')
    record.update(status='created',issue_id=str(payload.issue_id),url=safe_url(value.get('issue_url')),error='',updated_at=now())
    with engine.begin() as conn:
        current=conn.execute(select(db.gitee_links).where(db.gitee_links.c.id==identity)).mappings().one()
        if current['status']=='created':return dict(current)
        upsert(conn,db.gitee_links,record,'id')

    req_id = record.get('publish_req_id')
    if req_id:
        try:
            from . import cloud_sync
            from .cloud_client import CloudClient
            with engine.connect() as conn:
                bound = cloud_sync.get_meta(conn, 'bound', False)
                server_url = cloud_sync.get_meta(conn, 'server_url', '')
            if bound and server_url:
                c_token = gitee.token(service='cloud-session')
                if c_token:
                    c = CloudClient(base_url=server_url, token=c_token)
                    c.report_publish_result(req_id, outcome='succeeded', remote_id=str(payload.issue_id), url=record['url'])
        except Exception:
            pass

    return record
