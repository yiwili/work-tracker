from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, update

from awt import db, usage
from awt.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine(tmp_path / 'test.db')
    with TestClient(create_app(engine)) as app:
        yield app, engine
    engine.dispose()
    usage.bind(None)


def usage_row(identifier, days_ago, model, prompt, completion, estimated=False):
    return {
        'id': identifier,
        'recorded_at': (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(),
        'model': model,
        'purpose': 'task_writer',
        'prompt_tokens': prompt,
        'completion_tokens': completion,
        'estimated': estimated,
    }


def test_usage_stats_group_by_model_and_period(client):
    app, engine = client
    with engine.begin() as conn:
        conn.execute(insert(db.model_usage), [
            usage_row('a', 0, 'glm-5.3', 1000, 200),
            usage_row('b', 0, 'glm-5.3', 500, 100),
            usage_row('c', 0, 'deepseek-chat', 300, 50),
            usage_row('d', 40, 'glm-5.3', 900, 100),
        ])
    day = app.get('/api/usage/stats?period=day').json()
    assert day['total_tokens'] == 2150
    assert day['prompt_tokens'] == 1800 and day['completion_tokens'] == 350
    assert day['models'][0]['model'] == 'glm-5.3'
    assert day['models'][0]['total_tokens'] == 1800
    assert day['models'][1]['model'] == 'deepseek-chat'
    assert abs(day['models'][0]['share'] - 1800 / 2150) < 0.001
    assert day['start'] is not None

    everything = app.get('/api/usage/stats?period=all').json()
    assert everything['total_tokens'] == 3150
    assert everything['calls'] == 4
    assert everything['start'] is None
    assert all(item['share'] <= 1 for item in everything['models'])


def test_usage_stats_marks_estimated_rows(client):
    app, engine = client
    with engine.begin() as conn:
        conn.execute(insert(db.model_usage), usage_row('a', 0, 'glm-5.3', 100, 20, estimated=True))
    report = app.get('/api/usage/stats?period=day').json()
    assert report['estimated'] is True
    assert report['models'][0]['estimated'] is True


def test_usage_ledger_records_calls_for_the_bound_engine(client):
    app, engine = client
    usage.record_call({'purpose': 'scope_filter', 'model': 'glm-5.3',
                       'prompt_tokens': 1200, 'completion_tokens': 80, 'estimated': False})
    report = app.get('/api/usage/stats?period=day').json()
    assert report['total_tokens'] == 1280
    assert report['calls'] == 1
    assert report['models'][0]['model'] == 'glm-5.3'


def test_usage_stats_reject_unknown_period(client):
    app, _ = client
    assert app.get('/api/usage/stats?period=hour').status_code == 422


def test_backfill_counts_saved_analyses_once_and_yields_to_live_calls(client):
    app, engine = client
    today = datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    with engine.begin() as conn:
        conn.execute(insert(db.analyses).values(date=today, snapshot={
            'analyzed_at': datetime.now(timezone.utc).isoformat(),
            'model': 'glm-5.3',
            'usage': {'prompt_tokens': 900, 'completion_tokens': 100,
                      'total_tokens': 1000, 'calls': 3, 'estimated': False},
        }))
    usage.backfill(engine)
    usage.backfill(engine)
    report = app.get('/api/usage/stats?period=day').json()
    assert report['total_tokens'] == 1000
    assert report['calls'] == 3
    assert report['models'][0]['calls'] == 3
    assert report['models'][0]['model'] == 'glm-5.3'

    with engine.begin() as conn:
        conn.execute(update(db.analyses).where(db.analyses.c.date == today).values(snapshot={
            'analyzed_at': datetime.now(timezone.utc).isoformat(),
            'model': 'glm-5.3',
            'usage': {'prompt_tokens': 900, 'completion_tokens': 100,
                      'total_tokens': 1000, 'calls': 4, 'estimated': False},
        }))
    usage.backfill(engine)
    assert app.get('/api/usage/stats?period=day').json()['calls'] == 4

    usage.record_call({'purpose': 'task_writer', 'model': 'deepseek-chat',
                       'prompt_tokens': 150, 'completion_tokens': 50, 'estimated': False})
    report = app.get('/api/usage/stats?period=day').json()
    assert report['total_tokens'] == 200
    assert report['calls'] == 1
    assert report['models'][0]['model'] == 'deepseek-chat'
