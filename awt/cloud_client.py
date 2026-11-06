"""HTTP client for Work Tracker Server (OpenAPI contract).

Platform independent, strictly maps error envelopes and handles timeouts/unavailability.
"""
from typing import Any
import httpx


class CloudError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None):
        super().__init__(f"Cloud error ({status} {code}): {message}")
        self.status = status
        self.code = code
        self.message = message
        self.details = details


class CloudUnavailable(Exception):
    """Network connection failure, DNS failure, or server timeout."""
    pass


class CloudClient:
    def __init__(self, base_url: str, token: str = '', timeout: float = 15.0, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip('/')
        self.token = token
        self.timeout = timeout
        self.transport = transport

    def _client(self) -> httpx.Client:
        headers = {'Accept': 'application/json'}
        if self.token:
            headers['Authorization'] = f'Bearer {self.token}'
        return httpx.Client(base_url=self.base_url, headers=headers, timeout=self.timeout, transport=self.transport)

    def _request(self, method: str, path: str, json: Any = None, params: dict | None = None) -> Any:
        try:
            with self._client() as client:
                res = client.request(method, f"/api/v1{path}", json=json, params=params)
        except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CloudUnavailable(f"无法连接到云端服务: {type(exc).__name__}") from None

        if res.status_code == 204:
            return None

        try:
            data = res.json()
        except ValueError:
            data = {'error': {'code': f'http_{res.status_code}', 'message': res.text}}

        if res.is_error:
            err = data.get('error', {})
            raise CloudError(
                status=res.status_code,
                code=err.get('code', f'http_{res.status_code}'),
                message=err.get('message', res.reason_phrase or 'Request failed'),
                details=err.get('details'),
            )

        return data

    # --- Auth ---

    def login(self, email: str, password: str, device: dict) -> dict:
        data = self._request('POST', '/auth/login', json={'email': email, 'password': password, 'device': device})
        self.token = data.get('token', '')
        return data

    def logout(self) -> None:
        self._request('POST', '/auth/logout')
        self.token = ''

    def me(self) -> dict:
        return self._request('GET', '/me')

    def list_devices(self) -> list[dict]:
        res = self._request('GET', '/devices')
        return res.get('devices', [])

    def revoke_device(self, device_id: str) -> None:
        self._request('DELETE', f'/devices/{device_id}')

    # --- Sync ---

    def push(self, ops: list[dict]) -> dict:
        return self._request('POST', '/sync/push', json={'ops': ops})

    def pull(self, cursor: int, limit: int = 500) -> dict:
        return self._request('GET', '/sync/pull', params={'cursor': cursor, 'limit': limit})

    def snapshot(self) -> dict:
        return self._request('GET', '/sync/snapshot')

    # --- Publish ---

    def register_publish_batch(self, idempotency_key: str, executor: str, items: list[dict]) -> dict:
        return self._request('POST', '/publish/batches', json={
            'idempotency_key': idempotency_key,
            'executor': executor,
            'items': items,
        })

    def get_publish_batch(self, batch_id: str) -> dict:
        return self._request('GET', f'/publish/batches/{batch_id}')

    def list_publish_requests(self, state: str | None = None, work_item_id: str | None = None, limit: int = 100) -> list[dict]:
        params = {'limit': limit}
        if state:
            params['state'] = state
        if work_item_id:
            params['work_item_id'] = work_item_id
        res = self._request('GET', '/publish/requests', params=params)
        return res.get('requests', [])

    def claim_publish_request(self, request_id: str) -> dict:
        return self._request('POST', f'/publish/requests/{request_id}/claim')

    def report_publish_result(self, request_id: str, outcome: str, remote_id: str = '', url: str = '', error: str = '') -> dict:
        body = {'outcome': outcome}
        if remote_id:
            body['remote_id'] = remote_id
        if url:
            body['url'] = url
        if error:
            body['error'] = error
        return self._request('POST', f'/publish/requests/{request_id}/result', json=body)

    def reconcile_publish_request(self, request_id: str, found: bool, remote_id: str = '', url: str = '') -> dict:
        body = {'found': found}
        if remote_id:
            body['remote_id'] = remote_id
        if url:
            body['url'] = url
        return self._request('POST', f'/publish/requests/{request_id}/reconcile', json=body)

    def cancel_publish_request(self, request_id: str) -> dict:
        return self._request('POST', f'/publish/requests/{request_id}/cancel')

    # --- Migration ---

    def import_links(self, links: list[dict], pending: list[dict]) -> dict:
        return self._request('POST', '/migration/links', json={'links': links, 'pending': pending})

    def migration_status(self) -> dict:
        return self._request('GET', '/migration/status')

    def complete_migration(self, expected: dict) -> dict:
        return self._request('POST', '/migration/complete', json={'expected': expected})
