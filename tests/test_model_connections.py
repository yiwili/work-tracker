import pytest
from fastapi.testclient import TestClient

from awt import db, model_provider
from awt.app import create_app
from awt.models import CONVERSATION_SOURCES, Settings

GLM = {
    'provider': 'glm-coding',
    'endpoint': 'https://open.bigmodel.cn/api/coding/paas/v4/chat/completions',
    'protocol': 'openai',
    'key_env': 'TEST_GLM_KEY',
    'model': 'glm-5.3-flash',
}
DEEPSEEK = {
    'provider': 'deepseek',
    'endpoint': 'https://api.deepseek.com/chat/completions',
    'protocol': 'openai',
    'key_env': 'TEST_DEEPSEEK_KEY',
    'model': 'deepseek-chat',
}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine(tmp_path / 'model.db')
    with TestClient(create_app(engine)) as c:
        settings = c.get('/api/settings').json()
        for key in CONVERSATION_SOURCES:
            settings[key]['paths'] = []
        c.put('/api/settings', json=settings).raise_for_status()
        yield c
    engine.dispose()


def add(client, payload, **extra):
    response = client.post('/api/model/connections', json={**payload, 'activate': True, **extra})
    response.raise_for_status()
    return response.json()


def read_settings(client):
    return Settings.model_validate(client.get('/api/settings').json())


def test_multiple_connections_one_model_each_and_active_switch(client, monkeypatch):
    monkeypatch.setenv('TEST_GLM_KEY', 'secret-glm')
    monkeypatch.setenv('TEST_DEEPSEEK_KEY', 'secret-deepseek')
    glm = add(client, GLM)
    assert glm['status']['configured'] is True
    assert glm['status']['active'] == {'connection_id': glm['connection_id'], 'model': 'glm-5.3-flash'}
    assert [item['model'] for item in glm['status']['options']] == ['glm-5.3-flash']
    assert glm['settings']['llm_provider'] == 'glm-coding'
    assert glm['settings']['llm_model'] == 'glm-5.3-flash'

    deepseek = add(client, DEEPSEEK)
    catalog = client.get('/api/model/connections').json()
    assert len(catalog['connections']) == 2
    assert catalog['active'] == {'connection_id': deepseek['connection_id'], 'model': 'deepseek-chat'}

    switched = client.post('/api/model/active', json={'connection_id': glm['connection_id']})
    switched.raise_for_status()
    body = switched.json()
    assert body['settings']['llm_provider'] == 'glm-coding'
    assert body['settings']['llm_model'] == 'glm-5.3-flash'
    assert body['status']['configured'] is True

    missing = client.post('/api/model/active', json={'connection_id': 'missing'})
    assert missing.status_code == 400
    assert '不存在' in missing.json()['detail']


def test_connection_test_failure_is_stored_as_unavailable(client, monkeypatch):
    monkeypatch.setenv('TEST_GLM_KEY', 'secret-glm')
    created = add(client, GLM)
    connection_id = created['connection_id']

    def broken(*args, **kwargs):
        raise model_provider.ModelError('模型接口返回 401，API Key 无效或已过期')

    monkeypatch.setattr(model_provider, '_complete', broken)
    failed = client.post(f'/api/model/connections/{connection_id}/test', json={})
    assert failed.status_code == 400
    assert '401' in failed.json()['detail']

    status = client.get('/api/model/connections').json()['connections'][0]['status']
    assert status['state'] == 'error'
    assert status['last_test']['ok'] is False
    assert '401' in status['last_test']['message']


def test_latest_use_result_overrides_test_and_marks_red_dot(client, monkeypatch):
    monkeypatch.setenv('TEST_GLM_KEY', 'secret-glm')
    created = add(client, GLM)
    connection_id = created['connection_id']
    model_provider.record_status(connection_id, 'test', True, '连接成功')
    status = client.get('/api/model/connections').json()['connections'][0]['status']
    assert status['state'] == 'ok'
    assert status['last_test']['ok'] is True
    assert status['last_use'] is None

    def broken(*args, **kwargs):
        raise model_provider.ModelError('供应商账户额度或余额不足')

    monkeypatch.setattr(model_provider, '_complete', broken)
    with pytest.raises(model_provider.ModelError):
        model_provider.complete(read_settings(client), 'system', 'hello')
    status = client.get('/api/model/connections').json()['connections'][0]['status']
    assert status['state'] == 'error'
    assert '额度' in status['last_use']['message']


def test_delete_connection_removes_key_legacy_mirror_and_status(client, monkeypatch):
    monkeypatch.setenv('TEST_GLM_KEY', 'secret-glm')
    created = add(client, GLM)
    connection_id = created['connection_id']
    model_provider.record_status(connection_id, 'test', True, '连接成功')

    deleted = client.delete(f'/api/model/connections/{connection_id}')
    deleted.raise_for_status()
    body = deleted.json()
    assert body['settings']['llm_connections'] == []
    assert body['settings']['llm_model'] == ''
    assert body['status']['configured'] is False
    assert client.get('/api/model/connections').json()['options'] == []
    assert model_provider.connection_status(connection_id)['state'] == 'unknown'


def test_legacy_settings_still_synthesize_connection(client, monkeypatch):
    monkeypatch.setenv('AWT_LLM_API_KEY', 'secret-env')
    settings = client.get('/api/settings').json()
    settings.update(
        llm_provider='deepseek',
        llm_endpoint='https://api.deepseek.com/chat/completions',
        llm_model='deepseek-chat',
        llm_key_env='AWT_LLM_API_KEY',
    )
    client.put('/api/settings', json=settings).raise_for_status()
    status = client.get('/api/model/status').json()
    assert status['configured'] is True
    assert [item['model'] for item in status['options']] == ['deepseek-chat']
    assert status['connections'][0]['model'] == 'deepseek-chat'
