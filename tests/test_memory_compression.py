import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from awt import db, memory_compression, model_provider
from awt.app import create_app
from awt.models import CONVERSATION_SOURCES, Settings


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine(tmp_path / 'compression.db')
    with TestClient(create_app(engine)) as client:
        settings = client.get('/api/settings').json()
        for key in CONVERSATION_SOURCES:
            settings[key]['paths'] = []
        client.put('/api/settings', json=settings).raise_for_status()
        yield client, engine
    engine.dispose()


def _seed_events(engine, rows):
    with engine.begin() as conn:
        for row in rows:
            conn.execute(db.events.insert().values(**row))


def test_default_compression_sources_select_all_agents(env):
    client, _ = env
    settings = client.get('/api/settings').json()
    mc = settings['memory_compression']
    assert mc['enabled'] is False
    assert set(mc['sources']) == set(CONVERSATION_SOURCES)
    assert len(mc['sources']) == len(CONVERSATION_SOURCES)


def test_compression_stores_locally_respects_per_agent_selection_and_preserves_evidence(env, monkeypatch):
    client, engine = env
    day = '2026-11-19'
    _seed_events(
        engine,
        [
            {
                'id': 'ev-codex-u1',
                'source': 'codex',
                'session_id': 'sess-codex-1',
                'occurred_at': '2026-11-19T01:00:00+00:00',
                'kind': 'user_prompt',
                'summary': '排查登录 token 过期报错：这里有一大段非常冗长的堆栈和环境描述 ' + ('x' * 500),
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:1',
                'metadata': {},
            },
            {
                'id': 'ev-codex-t1',
                'source': 'codex',
                'session_id': 'sess-codex-1',
                'occurred_at': '2026-11-19T01:05:00+00:00',
                'kind': 'tool_call',
                'summary': 'edit auth.py',
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:2',
                'metadata': {'tool': 'edit', 'changed': True},
            },
            {
                'id': 'ev-codex-a1',
                'source': 'codex',
                'session_id': 'sess-codex-1',
                'occurred_at': '2026-11-19T01:08:00+00:00',
                'kind': 'assistant_message',
                'summary': '已定位并修复 auth.py 中的过期校验，附长篇解释 ' + ('y' * 800),
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:3',
                'metadata': {},
            },
            {
                'id': 'ev-codex-u2',
                'source': 'codex',
                'session_id': 'sess-codex-1',
                'occurred_at': '2026-11-19T01:15:00+00:00',
                'kind': 'user_prompt',
                'summary': '顺便运行一下 pytest 看看是否全部通过',
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:4',
                'metadata': {},
            },
            {
                'id': 'ev-codex-t2',
                'source': 'codex',
                'session_id': 'sess-codex-1',
                'occurred_at': '2026-11-19T01:18:00+00:00',
                'kind': 'shell_command',
                'summary': 'pytest passed',
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:5',
                'metadata': {'tool': 'bash', 'verified': True},
            },
            {
                'id': 'ev-claude-u1',
                'source': 'claude_code',
                'session_id': 'sess-claude-1',
                'occurred_at': '2026-11-19T03:00:00+00:00',
                'kind': 'user_prompt',
                'summary': '调整导出报表 CSV 编码为 UTF-8 BOM',
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'claude:1',
                'metadata': {},
            },
            {
                'id': 'ev-claude-a1',
                'source': 'claude_code',
                'session_id': 'sess-claude-1',
                'occurred_at': '2026-11-19T03:05:00+00:00',
                'kind': 'assistant_message',
                'summary': '已调整导出逻辑为 utf-8-sig。',
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'claude:2',
                'metadata': {},
            },
        ],
    )

    monkeypatch.setenv('TEST_MAIN_KEY', 'main-secret')
    settings = client.get('/api/settings').json()
    settings.update(
        llm_enabled=True,
        llm_provider='deepseek',
        llm_endpoint='https://api.deepseek.com/chat/completions',
        llm_model='deepseek-v4-pro',
        llm_key_env='TEST_MAIN_KEY',
        analysis_sources=['codex', 'claude_code'],
        auto_import_analysis=True,
        memory_compression={
            'enabled': True,
            'mode': 'custom',
            'provider': 'local',
            'endpoint': 'http://127.0.0.1:11434/v1/chat/completions',
            'protocol': 'openai',
            'model': 'qwen2.5:7b',
            # Only compress codex; leave claude_code uncompressed
            'sources': ['codex'],
        },
    )
    client.put('/api/settings', json=settings).raise_for_status()

    calls = []

    def fake_complete(proxy_settings, system, text, *, max_tokens=12000, purpose='', require_text=True, key=None):
        calls.append({'purpose': purpose, 'model': proxy_settings.llm_model, 'text': text})
        payload = json.loads(text)
        if purpose == 'memory_compressor':
            assert payload['session'] == 'codex:sess-codex-1'
            assert len(payload['turns']) == 2
            return json.dumps({
                'memories': [
                    {
                        'turns': [1, 2],
                        'kind': 'work',
                        'summary': '修复 auth.py 登录 token 过期校验并跑通 pytest 验证',
                    }
                ]
            }, ensure_ascii=False)
        if purpose == 'scope_filter':
            convs = payload['conversations']
            codex_recs = [c for c in convs if c['session'].startswith('codex:')]
            claude_recs = [c for c in convs if c['session'].startswith('claude_code:')]
            assert len(codex_recs) == 1
            assert codex_recs[0]['compressed'] is True
            assert '修复 auth.py 登录 token 过期校验' in codex_recs[0]['text']
            assert any(not c.get('compressed') and 'UTF-8 BOM' in c.get('text', '') for c in claude_recs)
            return json.dumps({
                'work_refs': payload['required_refs'],
                'learning_groups': [],
                'excluded': [],
                'uncertain_refs': [],
            })
        if purpose == 'task_writer':
            convs = payload['conversations']
            codex_rec = next(c for c in convs if c['session'].startswith('codex:'))
            claude_rec = next(c for c in convs if c['session'].startswith('claude_code:') and c['kind'] == 'user_prompt')
            return json.dumps({
                'summary': '完成认证修复与报表编码调整',
                'tasks': [
                    {
                        'title': '【app】修复登录 token 过期校验问题',
                        'details': '修改 auth.py 并运行 pytest 验证通过。',
                        'project': 'app',
                        'refs': [codex_rec['ref']],
                    },
                    {
                        'title': '【app】调整导出报表 UTF-8 BOM 编码',
                        'details': '修改导出编码为 utf-8-sig。',
                        'project': 'app',
                        'refs': [claude_rec['ref']],
                    },
                ],
            }, ensure_ascii=False)
        raise AssertionError(f'unexpected purpose {purpose}')

    monkeypatch.setattr(model_provider, '_complete', fake_complete)

    res = client.post(f'/api/analyze?date={day}', json={})
    res.raise_for_status()
    body = res.json()
    assert body['created'] == 2
    assert body['compression']['compressed_sessions'] == 1
    assert body['compression']['cached_sessions'] == 0
    assert body['compression']['skipped_sessions'] == 1
    assert body['compression']['compressed_chars'] < body['compression']['raw_chars']

    # Verify original events are untouched and compressed memory is stored locally
    with engine.connect() as conn:
        raw_ev = conn.execute(select(db.events).where(db.events.c.id == 'ev-codex-u1')).mappings().one()
        assert 'x' * 500 in raw_ev['summary']
        stored = conn.execute(select(db.compressed_memories)).mappings().all()
        assert len(stored) == 1
        assert stored[0]['source'] == 'codex'
        assert stored[0]['model'] == 'qwen2.5:7b'

        # Verify all real events from both compressed turns (u1, t1, a1, u2, t2) are linked as task evidence
        tasks = list(conn.execute(select(db.tasks).where(db.tasks.c.deleted == False)).mappings())
        auth_task = next(t for t in tasks if 'token' in t['title'])
        assert auth_task['evidence_level'] == '已有行为证据'
        ev_ids = set(conn.execute(select(db.evidence.c.event_id).where(db.evidence.c.task_id == auth_task['id'])).scalars())
        assert {'ev-codex-u1', 'ev-codex-t1', 'ev-codex-a1', 'ev-codex-u2', 'ev-codex-t2'}.issubset(ev_ids)

    # Re-analyzing the same day reuses local compressed memory without calling compression model again
    calls.clear()
    res2 = client.post(f'/api/analyze?date={day}', json={})
    res2.raise_for_status()
    assert res2.json()['compression']['compressed_sessions'] == 0
    assert res2.json()['compression']['cached_sessions'] == 1
    assert not any(c['purpose'] == 'memory_compressor' for c in calls)


def test_compression_uses_existing_api_model_and_fallback(env, monkeypatch):
    client, engine = env
    monkeypatch.setenv('TEST_CONN_KEY_1', 'secret-1')
    monkeypatch.setenv('TEST_CONN_KEY_2', 'secret-2')
    c1 = client.post('/api/model/connections', json={
        'label': '主力大模型',
        'provider': 'deepseek',
        'endpoint': 'https://api.deepseek.com/chat/completions',
        'protocol': 'openai',
        'key_env': 'TEST_CONN_KEY_1',
        'model': 'deepseek-v4-pro',
        'activate': True,
    }).json()['connection_id']
    c2 = client.post('/api/model/connections', json={
        'label': '备用低额度模型',
        'provider': 'glm',
        'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'protocol': 'openai',
        'key_env': 'TEST_CONN_KEY_2',
        'model': 'glm-4.5-flash',
        'activate': False,
    }).json()['connection_id']

    _seed_events(engine, [
        {
            'id': 'ev-u1',
            'source': 'codex',
            'session_id': 's1',
            'occurred_at': '2026-11-19T02:00:00+00:00',
            'kind': 'user_prompt',
            'summary': '优化 SQL 查询索引',
            'cwd': '/repo/db',
            'project': 'db',
            'raw_ref': '1',
            'metadata': {},
        },
        {
            'id': 'ev-a1',
            'source': 'codex',
            'session_id': 's1',
            'occurred_at': '2026-11-19T02:02:00+00:00',
            'kind': 'assistant_message',
            'summary': '已添加联合索引并验证执行计划',
            'cwd': '/repo/db',
            'project': 'db',
            'raw_ref': '2',
            'metadata': {},
        },
    ])

    settings = client.get('/api/settings').json()
    settings['memory_compression'].update({
        'enabled': True,
        'mode': 'connection',
        'connection_id': c1,
        'connection_model': 'deepseek-flash',
        'fallback_connection_ids': [c2],
    })
    client.put('/api/settings', json=settings).raise_for_status()

    # Main active connection must stay untouched (c1 / deepseek-v4-pro)
    assert client.get('/api/model/status').json()['model'] == 'deepseek-v4-pro'
    st = client.get('/api/memory-compression/status').json()
    assert st['configured'] is True
    assert st['model'] == 'deepseek-flash'

    tried_models = []

    def fake_complete(proxy_settings, system, text, *, max_tokens=12000, purpose='', require_text=True, key=None):
        tried_models.append(proxy_settings.llm_model)
        if proxy_settings.llm_model == 'deepseek-flash':
            raise model_provider.ModelError('请求触发供应商限流')
        return json.dumps({'memories': [{'turns': [1], 'kind': 'work', 'summary': '优化 SQL 查询联合索引'}]})

    monkeypatch.setattr(model_provider, '_complete', fake_complete)
    manual = client.post('/api/memory-compression/compress', json={'date': '2026-11-19'})
    manual.raise_for_status()
    assert tried_models == ['deepseek-flash', 'glm-4.5-flash']
    assert manual.json()['stats']['compressed_sessions'] == 1
    assert manual.json()['storage']['total_records'] == 1

    # Clear local compressed memory cache
    cleared = client.delete('/api/memory-compression/cache')
    cleared.raise_for_status()
    assert cleared.json()['deleted'] == 1
    assert cleared.json()['storage']['total_records'] == 0


def test_compression_key_vault_and_test_endpoint(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(model_provider, '_complete', lambda *a, **kw: 'OK')

    saved = client.post('/api/memory-compression/key', json={'api_key': 'comp-secret-key'})
    saved.raise_for_status()

    tested = client.post('/api/memory-compression/test', json={
        'memory_compression': {
            'enabled': True,
            'mode': 'custom',
            'provider': 'custom',
            'endpoint': 'https://api.example.com/v1/chat/completions',
            'protocol': 'openai',
            'model': 'small-model',
        },
    })
    tested.raise_for_status()
    assert tested.json()['ok'] is True

    deleted = client.delete('/api/memory-compression/key')
    deleted.raise_for_status()


def test_predict_compressed_input_tokens_is_read_only_and_does_not_affect_actual_compression(env, monkeypatch):
    client, engine = env
    day = '2026-11-19'
    _seed_events(
        engine,
        [
            {
                'id': 'pred-u1',
                'source': 'codex',
                'session_id': 'sess-pred-1',
                'occurred_at': '2026-11-19T02:00:00+00:00',
                'kind': 'user_prompt',
                'summary': '排查并修复复杂 SQL 查询超时问题 ' + ('详细日志与表结构说明 ' * 40),
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:1',
                'metadata': {},
            },
            {
                'id': 'pred-a1',
                'source': 'codex',
                'session_id': 'sess-pred-1',
                'occurred_at': '2026-11-19T02:05:00+00:00',
                'kind': 'assistant_message',
                'summary': '分析执行计划并给出修复建议 ' + ('完整 SQL 与解释 ' * 60),
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'codex:2',
                'metadata': {},
            },
            {
                'id': 'pred-u2',
                'source': 'claude_code',
                'session_id': 'sess-pred-2',
                'occurred_at': '2026-11-19T04:00:00+00:00',
                'kind': 'user_prompt',
                'summary': '更新接口文档说明 ' + ('字段列表 ' * 30),
                'cwd': '/repo/app',
                'project': 'app',
                'raw_ref': 'claude:1',
                'metadata': {},
            },
        ],
    )

    settings = client.get('/api/settings').json()
    settings['analysis_sources'] = ['codex', 'claude_code']
    settings['memory_compression'].update({
        'enabled': False,
        'mode': 'custom',
        'provider': 'local',
        'endpoint': 'http://127.0.0.1:11434/v1/chat/completions',
        'model': 'qwen2.5:7b',
        'sources': ['codex', 'claude_code'],
    })
    client.put('/api/settings', json=settings).raise_for_status()

    # Model must NEVER be called during prediction
    def fail_if_called(*args, **kwargs):
        raise AssertionError('prediction must never invoke model_provider._complete')

    monkeypatch.setattr(model_provider, '_complete', fail_if_called)

    # 1) GET /api/conversations includes compression_preview even when compression is disabled
    conv = client.get(f'/api/conversations?date={day}')
    conv.raise_for_status()
    preview = conv.json()['compression_preview']
    assert preview['enabled'] is False
    assert preview['raw_input_tokens'] > 500
    assert preview['predicted_input_tokens'] == preview['raw_input_tokens']
    assert preview['simulated_compressed_tokens'] < preview['raw_input_tokens']
    assert preview['simulated_saved_tokens'] > 0

    # 2) POST /api/memory-compression/predict with unsaved override_cfg (enabled=True, only codex)
    pred_codex_only = client.post('/api/memory-compression/predict', json={
        'date': day,
        'memory_compression': {
            **settings['memory_compression'],
            'enabled': True,
            'sources': ['codex'],
        },
    })
    pred_codex_only.raise_for_status()
    p1 = pred_codex_only.json()
    assert p1['enabled'] is True
    assert p1['will_compress_sessions'] == 1
    assert p1['cached_sessions'] == 0
    assert p1['predicted_input_tokens'] < p1['raw_input_tokens']
    codex_stat = next(s for s in p1['per_source'] if s['source'] == 'codex')
    claude_stat = next(s for s in p1['per_source'] if s['source'] == 'claude_code')
    assert codex_stat['compression_selected'] is True
    assert codex_stat['predicted_tokens'] < codex_stat['raw_tokens']
    assert claude_stat['compression_selected'] is False
    assert claude_stat['predicted_tokens'] == claude_stat['raw_tokens']

    # Verify DB compressed_memories was NOT modified by prediction
    with engine.connect() as conn:
        assert conn.execute(select(db.compressed_memories)).all() == []

    # 3) Run actual compression and verify prediction never altered actual output token count
    settings['memory_compression']['enabled'] = True
    settings['memory_compression']['sources'] = ['codex']
    client.put('/api/settings', json=settings).raise_for_status()

    actual_summary = '排查并修复 SQL 查询超时问题，补充联合索引并优化执行计划。'
    monkeypatch.setattr(
        model_provider,
        '_complete',
        lambda *a, **kw: json.dumps({'memories': [{'turns': [1], 'kind': 'work', 'summary': actual_summary}]}, ensure_ascii=False),
    )
    compressed = client.post('/api/memory-compression/compress', json={'date': day})
    compressed.raise_for_status()
    with engine.connect() as conn:
        row = conn.execute(select(db.compressed_memories)).mappings().one()
        assert row['segments_json'][0]['summary'] == actual_summary
        assert row['compressed_chars'] == len(actual_summary)

    # 4) Once cached, prediction reports exact cached post-compression tokens with zero model calls
    monkeypatch.setattr(model_provider, '_complete', fail_if_called)
    after_cache = client.get(f'/api/memory-compression/predict?date={day}')
    after_cache.raise_for_status()
    p2 = after_cache.json()
    assert p2['cached_sessions'] == 1
    assert p2['predicted_input_tokens'] < p2['raw_input_tokens']
