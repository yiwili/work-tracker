from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from awt import db
from awt.app import create_app
from awt.git_tools import scan_repositories


def make_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / '.git').mkdir()
    return path


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('AWT_DATA_DIR', str(tmp_path / 'data'))
    engine = db.make_engine()
    with TestClient(create_app(engine)) as c:
        yield c
    engine.dispose()


def test_scan_finds_repositories_within_three_levels(tmp_path):
    shallow = make_repo(tmp_path / 'work' / 'alpha')
    make_repo(tmp_path / 'work' / 'group' / 'beta')
    make_repo(tmp_path / 'work' / 'a' / 'b' / 'gamma')
    make_repo(tmp_path / 'work' / 'a' / 'b' / 'c' / 'delta')
    result = scan_repositories(tmp_path / 'work')
    roots = {Path(item['root']).name for item in result['repos']}
    assert roots == {'alpha', 'beta', 'gamma'}
    assert shallow.name in roots
    assert result['message'] == ''
    assert result['truncated'] is False


def test_scan_skips_dependencies_and_stops_at_repository_root(tmp_path):
    make_repo(tmp_path / 'work' / 'app')
    make_repo(tmp_path / 'work' / 'app' / 'inner')
    make_repo(tmp_path / 'work' / 'node_modules' / 'pkg')
    result = scan_repositories(tmp_path / 'work')
    assert [item['relative'] for item in result['repos']] == ['app']


def test_scan_reports_invalid_or_empty_directory(tmp_path):
    empty = tmp_path / 'empty'
    empty.mkdir()
    result = scan_repositories(empty)
    assert result['repos'] == []
    assert result['message'] == '请选择正确git项目目录'
    missing = scan_repositories(tmp_path / 'missing')
    assert missing['message'] == '请选择正确git项目目录'


def test_scan_endpoint_returns_repository_tags(client, tmp_path):
    make_repo(tmp_path / 'work' / 'one')
    make_repo(tmp_path / 'work' / 'two')
    response = client.post('/api/git/scan', json={'root': str(tmp_path / 'work')})
    response.raise_for_status()
    data = response.json()
    assert [item['name'] for item in data['repos']] == ['one', 'two']
    assert data['message'] == ''


def test_source_check_accepts_unsaved_paths(client, tmp_path):
    existing = tmp_path / 'codex'
    existing.mkdir()
    missing = tmp_path / 'qoder-missing'
    response = client.post('/api/sources/check', json={'sources': {
        'codex': [str(existing), str(missing)],
        'qoder_intl': [str(missing)],
    }})
    response.raise_for_status()
    data = response.json()
    assert {(item['source'], item['exists']) for item in data['items']} == {
        ('codex', True), ('codex', False), ('qoder_intl', False)
    }
    assert data['detected'] == ['codex']


def test_source_check_rejects_unknown_source(client):
    response = client.post('/api/sources/check', json={'sources': {'unknown': ['C:/tmp']}})
    assert response.status_code in (400, 422)
