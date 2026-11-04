import json
import threading
import time
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from sqlalchemy import insert

from awt import analysis_progress, db, model_provider, service, usage
from awt.models import Settings


def answer_for(body):
    text = body['messages'][-1]['content']
    try:
        value = json.loads(text)
    except ValueError:
        return '{"work_refs":[]}'
    if not isinstance(value, dict):
        return '{"work_refs":[]}'
    if 'required_refs' in value:
        return json.dumps({'work_refs': value['required_refs'], 'excluded': [], 'uncertain_refs': []})
    if 'conversations' in value:
        prompts = [row['ref'] for row in value['conversations'] if row.get('kind') == 'user_prompt']
        return json.dumps({'summary': '修复接口超时。', 'tasks': [{
            'title': '修复订单查询接口超时', 'details': '排查并验证接口超时。',
            'project': 'archive', 'category': 'Bug 修复', 'refs': prompts,
        }]}, ensure_ascii=False)
    if 'daily_parts' in value:
        return json.dumps({'summary': '修复接口超时。', 'tasks': []}, ensure_ascii=False)
    return '{"work_refs":[]}'


class FakeStream(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
        self.seen.append(body)
        answer = answer_for(body)
        chunks = [
            {'choices': [{'delta': {'reasoning_content': '先识别'}}]},
            {'choices': [{'delta': {'reasoning_content': '工作目标。'}}]},
            None,  # pause between reasoning and the final answer
            {'choices': [{'delta': {'content': answer[:len(answer) // 2]}}]},
            {'choices': [{'delta': {'content': answer[len(answer) // 2:]}}]},
            {'choices': [{'delta': {}, 'finish_reason': 'stop'}],
             'usage': {'prompt_tokens': 321, 'completion_tokens': 54}},
            '[DONE]',
        ]
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.end_headers()
        for chunk in chunks:
            if chunk is None:
                time.sleep(0.4)
                continue
            payload = chunk if isinstance(chunk, str) else json.dumps(chunk, ensure_ascii=False)
            self.wfile.write(f'data: {payload}\n\n'.encode())
            self.wfile.flush()

    def log_message(self, *_args):
        pass


def start_fake(tmp_path):
    server = ThreadingHTTPServer(('127.0.0.1', 0), FakeStream)
    server.daemon_threads = True
    FakeStream.seen = []
    threading.Thread(target=server.serve_forever, daemon=True).start()
    engine = db.make_engine(tmp_path / 'usage.db')
    db.migrate(engine)
    usage.bind(engine)
    settings = Settings(
        llm_enabled=True,
        llm_provider='custom',
        llm_endpoint=f'http://127.0.0.1:{server.server_address[1]}/v1/chat/completions',
        llm_model='glm-5.3',
        llm_key_env='AWT_TEST_KEY',
    )
    return server, engine, settings


def stop_fake(server, engine):
    usage.bind(None)
    engine.dispose()
    server.shutdown()
    server.server_close()


def wait_for_thinking(progress, deadline=5):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        snapshot = progress.snapshot()
        if snapshot['thinking']:
            return snapshot
        time.sleep(0.01)
    return progress.snapshot()


def test_streaming_reasoning_and_usage_are_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_TEST_KEY', 'local-test-key')
    server, engine, settings = start_fake(tmp_path)
    progress = analysis_progress.Progress()
    result = {}

    def run():
        with analysis_progress.track(progress):
            result['text'] = model_provider.complete(
                settings, 'system prompt', 'user prompt', purpose='scope_filter',
            )

    worker = threading.Thread(target=run)
    try:
        worker.start()
        live = wait_for_thinking(progress)
        assert live['thinking'].startswith('先识别')
        assert live['thinking_live'] is True
        assert live['model'] == 'glm-5.3'

        worker.join(10)
        assert not worker.is_alive()
        progress.finish('success')
        final = progress.snapshot()
        assert final['thinking_live'] is False
        assert final['thinking'] == '先识别工作目标。'
        assert final['thinking_seconds'] > 0
        assert final['tokens'] == {
            'prompt_tokens': 321,
            'completion_tokens': 54,
            'total_tokens': 375,
            'estimated': False,
        }
        assert result['text'] == '{"work_refs":[]}'
        assert FakeStream.seen[0]['stream'] is True
        assert FakeStream.seen[0]['reasoning_effort'] == 'low'

        with engine.connect() as conn:
            report = usage.report(conn, 'day')
        assert report['total_tokens'] == 375
        assert report['calls'] == 1
        assert report['models'][0]['model'] == 'glm-5.3'
        assert report['estimated'] is False
    finally:
        stop_fake(server, engine)


def test_daily_analysis_streams_thinking_and_records_each_request(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_TEST_KEY', 'local-test-key')
    server, engine, settings = start_fake(tmp_path)
    settings.analysis_sources = ['codex']
    settings.auto_import_analysis = True
    with engine.begin() as conn:
        service.save_settings(conn, settings)
        for i in range(4):
            conn.execute(insert(db.events).values(
                id=f'event-{i}', source='codex', session_id='session-1',
                occurred_at=f'2026-10-24T01:{i:02d}:00+00:00', kind='user_prompt',
                summary=f'修复同一接口的后续问题 {i}', cwd='', project='archive',
                raw_ref=f'private-file:{i}', metadata={},
            ))
    progress = analysis_progress.Progress()
    result = {}

    def run():
        with analysis_progress.track(progress), engine.connect() as conn:
            result['analysis'] = service.analyze(conn, date(2026, 10, 24))
            conn.commit()

    worker = threading.Thread(target=run)
    try:
        worker.start()
        live = wait_for_thinking(progress)
        assert live['thinking'].startswith('先识别')
        assert live['thinking_live'] is True

        worker.join(20)
        assert not worker.is_alive()
        progress.finish('success')
        final = progress.snapshot()
        assert final['thinking'] == '先识别工作目标。'
        assert final['thinking_live'] is False
        assert final['tokens']['estimated'] is False
        assert result['analysis']['summary'] == '修复接口超时。'
        assert all(request['stream'] is True for request in FakeStream.seen)

        with engine.connect() as conn:
            report = usage.report(conn, 'day')
        assert report['calls'] == len(FakeStream.seen) == 2
        assert report['total_tokens'] == 750
        assert report['models'] == [{
            'model': 'glm-5.3', 'prompt_tokens': 642, 'completion_tokens': 108,
            'total_tokens': 750, 'calls': 2, 'estimated': False, 'share': 1.0,
        }]
    finally:
        stop_fake(server, engine)
