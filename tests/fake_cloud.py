"""In-memory fake Work Tracker Server mounted via httpx.MockTransport.

Implements protocol §2, §3, §4, §5, §6 for fast integration testing.
"""
from datetime import datetime, timezone
import json
from uuid import uuid4
import httpx


class FakeCloudServer:
    def __init__(self):
        self.accounts = {
            'user@example.com': {
                'id': 'acc-1',
                'email': 'user@example.com',
                'password': 'password123',
                'display_name': 'Test User',
                'change_seq': 0,
                'min_retained_seq': 0,
                'unified_publish_enabled': False,
            }
        }
        self.devices = {}
        self.sessions = {}
        self.entities = {}  # (acc_id, etype, eid) -> dict
        self.changes = []   # list of {account_id, seq, entity_type, entity_id, version, device_id}
        self.op_results = {}  # (acc_id, op_id) -> result dict
        self.batches = {}
        self.requests = {}
        self.cursor_expired = False

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        method = request.method
        body = json.loads(request.content.decode()) if request.content else {}

        # Auth
        if path == '/api/v1/auth/login' and method == 'POST':
            email = body['email']
            password = body['password']
            acc = self.accounts.get(email)
            if not acc or acc['password'] != password:
                return httpx.Response(401, json={'error': {'code': 'unauthorized', 'message': 'bad credentials'}})

            dev_id = str(uuid4())
            token = f"wts_{uuid4().hex}"
            self.sessions[token] = {'account_id': acc['id'], 'device_id': dev_id}
            dev = {'id': dev_id, 'name': body['device']['name'], 'platform': body['device']['platform'], 'current': True}
            return httpx.Response(200, json={'token': token, 'account': acc, 'device': dev})

        token = request.headers.get('Authorization', '').replace('Bearer ', '').strip()
        sess = self.sessions.get(token)
        if not sess:
            return httpx.Response(401, json={'error': {'code': 'unauthorized', 'message': 'invalid token'}})
        acc_id = sess['account_id']
        dev_id = sess['device_id']

        # Sync Push
        if path == '/api/v1/sync/push' and method == 'POST':
            results = []
            acc = next(a for a in self.accounts.values() if a['id'] == acc_id)

            for op in body.get('ops', []):
                op_id = op['op_id']
                if (acc_id, op_id) in self.op_results:
                    results.append(self.op_results[(acc_id, op_id)])
                    continue

                etype = op['entity_type']
                eid = op['entity_id']
                base_ver = op['base_version']
                fields = op['fields']

                key = (acc_id, etype, eid)
                current = self.entities.get(key)

                if not current:
                    if base_ver > 0:
                        res = {'op_id': op_id, 'status': 'rejected', 'error': {'code': 'not_found', 'message': 'not found'}}
                        self.op_results[(acc_id, op_id)] = res
                        results.append(res)
                        continue

                    # Create
                    acc['change_seq'] += 1
                    ver = 1
                    f_vers = {k: ver for k in fields}
                    ent = {
                        'entity_type': etype,
                        'entity_id': eid,
                        'version': ver,
                        'field_versions': f_vers,
                        'fields': fields,
                        'updated_at': datetime.now(timezone.utc).isoformat(),
                        'updated_by_device': dev_id,
                    }
                    self.entities[key] = ent
                    self.changes.append({'account_id': acc_id, 'seq': acc['change_seq'], 'entity_type': etype, 'entity_id': eid, 'version': ver, 'device_id': dev_id})

                    res = {'op_id': op_id, 'status': 'applied', 'entity': ent}
                    self.op_results[(acc_id, op_id)] = res
                    results.append(res)
                    continue

                # Conflict detection
                conflict_fields = []
                for f, val in fields.items():
                    if current['field_versions'].get(f, 0) > base_ver and current['fields'].get(f) != val:
                        conflict_fields.append(f)

                # Delete vs edit
                if fields.get('deleted') and not current['fields'].get('deleted'):
                    for g, g_ver in current['field_versions'].items():
                        if g_ver > base_ver and g != 'deleted':
                            conflict_fields.append('deleted')
                            conflict_fields.append(g)

                if conflict_fields:
                    res = {'op_id': op_id, 'status': 'conflict', 'entity': current, 'conflict_fields': sorted(set(conflict_fields))}
                    self.op_results[(acc_id, op_id)] = res
                    results.append(res)
                    continue

                # Apply changes
                changed = False
                next_ver = current['version'] + 1
                for f, val in fields.items():
                    if current['fields'].get(f) != val:
                        changed = True
                        current['fields'][f] = val
                        current['field_versions'][f] = next_ver

                if changed:
                    acc['change_seq'] += 1
                    current['version'] = next_ver
                    current['updated_at'] = datetime.now(timezone.utc).isoformat()
                    self.changes.append({'account_id': acc_id, 'seq': acc['change_seq'], 'entity_type': etype, 'entity_id': eid, 'version': next_ver, 'device_id': dev_id})

                res = {'op_id': op_id, 'status': 'applied', 'entity': current}
                self.op_results[(acc_id, op_id)] = res
                results.append(res)

            return httpx.Response(200, json={'results': results, 'cursor': acc['change_seq']})

        # Sync Pull
        if path == '/api/v1/sync/pull' and method == 'GET':
            if self.cursor_expired:
                return httpx.Response(410, json={'error': {'code': 'cursor_expired', 'message': 'cursor expired'}})

            cursor = int(request.url.params.get('cursor', 0))
            limit = int(request.url.params.get('limit', 500))

            relevant = [c for c in self.changes if c['account_id'] == acc_id and c['seq'] > cursor]
            seen = set()
            distinct = []
            for c in relevant:
                k = (c['entity_type'], c['entity_id'])
                if k not in seen:
                    seen.add(k)
                    distinct.append(c)

            has_more = len(distinct) > limit
            batch = distinct[:limit]
            entities = [self.entities[(acc_id, c['entity_type'], c['entity_id'])] for c in batch if (acc_id, c['entity_type'], c['entity_id']) in self.entities]
            new_cursor = max([c['seq'] for c in batch], default=cursor)

            return httpx.Response(200, json={'changes': entities, 'cursor': new_cursor, 'has_more': has_more})

        # Sync Snapshot
        if path == '/api/v1/sync/snapshot' and method == 'GET':
            acc = next(a for a in self.accounts.values() if a['id'] == acc_id)
            all_ents = [e for (aid, _, _), e in self.entities.items() if aid == acc_id]
            return httpx.Response(200, json={'entities': all_ents, 'cursor': acc['change_seq']})

        # Migration Status
        if path == '/api/v1/migration/status' and method == 'GET':
            acc = next(a for a in self.accounts.values() if a['id'] == acc_id)
            wi_cnt = len([e for (aid, etype, _), e in self.entities.items() if aid == acc_id and etype == 'work_item' and not e['fields'].get('deleted')])
            lnk_cnt = len([e for (aid, etype, _), e in self.entities.items() if aid == acc_id and etype == 'gitee_link' and not e['fields'].get('deleted')])
            return httpx.Response(200, json={
                'unified_publish_enabled': acc['unified_publish_enabled'],
                'counts': {'work_items': wi_cnt, 'links': lnk_cnt, 'uncertain': 0},
            })

        # Migration Links
        if path == '/api/v1/migration/links' and method == 'POST':
            acc = next(a for a in self.accounts.values() if a['id'] == acc_id)
            for link in body.get('links', []):
                key = (acc_id, 'gitee_link', link['work_item_id'])
                acc['change_seq'] += 1
                self.entities[key] = {
                    'entity_type': 'gitee_link',
                    'entity_id': link['work_item_id'],
                    'version': 1,
                    'field_versions': {'remote_id': 1},
                    'fields': link,
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'updated_by_device': dev_id,
                }
            return httpx.Response(200, json={
                'links_upserted': len(body.get('links', [])),
                'links_unchanged': 0,
                'pending_created': len(body.get('pending', [])),
                'pending_skipped': 0,
                'conflicts': [],
            })

        # Migration Complete
        if path == '/api/v1/migration/complete' and method == 'POST':
            acc = next(a for a in self.accounts.values() if a['id'] == acc_id)
            exp = body.get('expected', {})
            wi_cnt = len([e for (aid, etype, _), e in self.entities.items() if aid == acc_id and etype == 'work_item' and not e['fields'].get('deleted')])
            if exp.get('work_items') != wi_cnt:
                return httpx.Response(409, json={'error': {'code': 'count_mismatch', 'message': 'count mismatch'}})

            acc['unified_publish_enabled'] = True
            return httpx.Response(200, json={
                'unified_publish_enabled': True,
                'counts': {'work_items': wi_cnt, 'links': exp.get('links', 0), 'uncertain': exp.get('uncertain', 0)},
            })

        # Publish Batches
        if path == '/api/v1/publish/batches' and method == 'POST':
            batch_id = body.get('idempotency_key') or body.get('batch_id')
            items = []
            for it in body.get('items', []):
                req_id = it.get('request_id') or it.get('work_item_id') or str(uuid4())
                req = {
                    'request_id': req_id,
                    'batch_id': batch_id,
                    'account_id': acc_id,
                    'work_item_id': it.get('work_item_id'),
                    'target_scope': it.get('target_scope'),
                    'status': 'registered',
                    'claimed_by_device': None,
                    'created_at': datetime.now(timezone.utc).isoformat(),
                }
                self.requests[req_id] = req
                items.append({'request_id': req_id, 'status': 'registered', 'target_scope': it.get('target_scope')})
            self.batches[batch_id] = {'batch_id': batch_id, 'account_id': acc_id, 'status': 'active'}
            return httpx.Response(200, json={'batch_id': batch_id, 'status': 'active', 'items': items})

        # Publish Request Claim
        if path.startswith('/api/v1/publish/requests/') and path.endswith('/claim') and method == 'POST':
            req_id = path.split('/')[5]
            req = self.requests.get(req_id)
            if not req:
                req = {
                    'request_id': req_id,
                    'account_id': acc_id,
                    'status': 'registered',
                    'claimed_by_device': None,
                }
                self.requests[req_id] = req

            if req['status'] == 'succeeded':
                return httpx.Response(409, json={'error': {'code': 'already_succeeded', 'message': 'request already succeeded'}})
            if req['claimed_by_device'] and req['claimed_by_device'] != dev_id and req['status'] == 'sending':
                return httpx.Response(409, json={'error': {'code': 'already_claimed', 'message': 'request claimed by another device'}})

            req['claimed_by_device'] = dev_id
            req['status'] = 'sending'
            return httpx.Response(200, json={
                'request_id': req_id,
                'status': 'sending',
                'claimed_by_device': dev_id,
                'lease_expires_at': datetime.now(timezone.utc).isoformat(),
            })

        # Publish Request Result
        if path.startswith('/api/v1/publish/requests/') and path.endswith('/result') and method == 'POST':
            req_id = path.split('/')[5]
            req = self.requests.get(req_id)
            if not req:
                req = {'request_id': req_id, 'account_id': acc_id}
                self.requests[req_id] = req

            status = body.get('outcome') or body.get('status')
            req['status'] = status
            req['remote_id'] = body.get('remote_id', '')
            req['url'] = body.get('url', '')
            req['error'] = body.get('error', '')

            if status == 'succeeded':
                acc = next(a for a in self.accounts.values() if a['id'] == acc_id)
                acc['change_seq'] += 1
                key = (acc_id, 'gitee_link', req.get('work_item_id', req_id))
                self.entities[key] = {
                    'entity_type': 'gitee_link',
                    'entity_id': req.get('work_item_id', req_id),
                    'version': 1,
                    'field_versions': {'remote_id': 1},
                    'fields': {
                        'work_item_id': req.get('work_item_id', req_id),
                        'target_scope': req.get('target_scope', ''),
                        'remote_id': body.get('remote_id', ''),
                        'url': body.get('url', ''),
                    },
                    'updated_at': datetime.now(timezone.utc).isoformat(),
                    'updated_by_device': dev_id,
                }
                self.changes.append({'account_id': acc_id, 'seq': acc['change_seq'], 'entity_type': 'gitee_link', 'entity_id': req.get('work_item_id', req_id), 'version': 1, 'device_id': dev_id})

            return httpx.Response(200, json={'request_id': req_id, 'status': status})

        return httpx.Response(404, json={'error': {'code': 'not_found', 'message': 'not found'}})
