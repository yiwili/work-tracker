import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from awt.collectors import (CollectionCursor, DeepSeekHarnessAdapter, QoderAdapter, SourceLocation,
                            TraeAdapter, WorkBuddyAdapter, zstandard)
from awt.models import Settings


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), encoding='utf-8')


def collect(adapter, path):
    adapter.start_offset = 0
    return list(adapter.collect(SourceLocation(path, adapter.name), CollectionCursor()))


def test_default_sources_cover_domestic_and_international_editions(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    settings = Settings()
    assert any('.workbuddy-ai' in path for path in settings.workbuddy.paths)
    assert any(path.endswith('workbuddy\\projects') or path.endswith('workbuddy/projects') for path in settings.workbuddy.paths)
    assert any('.qoder-cn' in path for path in settings.qoder.paths)
    assert any(path.endswith('.qoder\\projects') or path.endswith('.qoder/projects') for path in settings.qoder.paths)
    assert any('.trae-cn' in path for path in settings.trae.paths)
    assert any(path.endswith('.trae\\memory\\projects') or path.endswith('.trae/memory/projects') for path in settings.trae.paths)
    assert any(path.endswith('.dsh\\sessions') or path.endswith('.dsh/sessions') for path in settings.deepseek_harness.paths)


def test_workbuddy_reads_domestic_session_and_strips_system_reminder(tmp_path):
    path = tmp_path / 'projects' / 'c-work' / 'session.jsonl'
    write(path, [
        {'type': 'message', 'sessionId': 'wb-1', 'cwd': str(tmp_path), 'timestamp': 1793345413869, 'role': 'user',
         'content': [{'type': 'input_text', 'text': '<system-reminder data-role="user-ctx">\n目录与环境\n</system-reminder>\n修复订单接口超时'}]},
        {'type': 'message', 'sessionId': 'wb-1', 'timestamp': 1793345415000, 'role': 'assistant',
         'content': [{'type': 'output_text', 'text': '已定位到慢查询'}]},
        {'type': 'function_call', 'sessionId': 'wb-1', 'timestamp': 1793345416000, 'name': 'Bash',
         'callId': 'call-1', 'arguments': '{"command":"pytest"}'},
        {'type': 'function_call_result', 'sessionId': 'wb-1', 'timestamp': 1793345417000, 'callId': 'call-1',
         'name': 'Bash', 'status': 'completed', 'output': {'type': 'text', 'text': '3 passed'}},
    ])
    events = collect(WorkBuddyAdapter([tmp_path / 'projects']), path)

    assert [event.kind for event in events] == ['user_prompt', 'assistant_message', 'shell_command', 'tool_result']
    assert events[0].summary == '修复订单接口超时'
    assert events[0].session_id == 'wb-1'
    assert str(tmp_path) in events[0].cwd
    assert events[2].metadata['tool'] == 'Bash'
    assert events[3].summary == '3 passed'


def test_qoder_reads_claude_style_session(tmp_path):
    path = tmp_path / 'projects' / 'd-work' / 'session.jsonl'
    write(path, [
        {'type': 'workspace-directories', 'sessionId': 'q-1', 'directories': [str(tmp_path)]},
        {'type': 'user', 'sessionId': 'q-1', 'cwd': str(tmp_path), 'timestamp': '2026-10-30T07:30:13.937Z',
         'message': {'role': 'user', 'content': [{'type': 'text', 'text': '补上导出按钮'}]}},
        {'type': 'assistant', 'sessionId': 'q-1', 'cwd': str(tmp_path), 'timestamp': '2026-10-30T07:31:00Z',
         'message': {'role': 'assistant', 'content': [
             {'type': 'text', 'text': '已添加导出按钮'},
             {'type': 'tool_use', 'id': 'tool-1', 'name': 'Bash', 'input': {'command': 'npm run build'}},
         ]}},
        {'type': 'user', 'sessionId': 'q-1', 'cwd': str(tmp_path), 'timestamp': '2026-10-30T07:32:00Z',
         'message': {'role': 'user', 'content': [
             {'type': 'tool_result', 'tool_use_id': 'tool-1', 'content': 'build ok'},
         ]}},
        {'type': 'active-leaf', 'sessionId': 'q-1', 'timestamp': 1793345417000},
    ])
    events = collect(QoderAdapter([tmp_path / 'projects']), path)

    assert [event.kind for event in events] == ['user_prompt', 'assistant_message', 'shell_command', 'tool_result']
    assert events[0].session_id == 'q-1'
    assert events[1].summary == '已添加导出按钮'
    assert events[2].metadata['tool'] == 'Bash'
    assert events[3].summary == 'build ok'


def framed(rows):
    if zstandard is None:
        pytest.skip('zstandard 未安装')
    return zstandard.ZstdCompressor().compress(
        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows).encode())


def test_deepseek_harness_reads_frames_and_resumes_partial_frame(tmp_path):
    path = tmp_path / 'sessions' / 'session-dsh' / 'session.v4.jsonl.zstd'
    path.parent.mkdir(parents=True)
    first = framed([
        {'type': 'session', 'time': 1793345413000, 'data': {'id': 'dsh-1', 'cwd': str(tmp_path)}},
        {'type': 'user/message', 'time': 1793345414000, 'data': {'content': [{'type': 'text', 'text': '修复登录跳转'}], 'source': {'kind': 'user'}}},
    ])
    second = framed([
        {'type': 'assistant/message', 'time': 1793345415000, 'data': {'message': {'role': 'assistant', 'content': [
            {'type': 'reasoning', 'text': '先看路由'}, {'type': 'text', 'text': '已修复登录跳转'}]}}},
        {'type': 'tool/call', 'time': 1793345416000, 'data': {'callId': 'c1', 'name': 'shell', 'arguments': '{"command":"pytest"}'}},
        {'type': 'tool/result', 'time': 1793345417000, 'data': {'toolCallId': 'c1', 'message': {'content': [{'type': 'text', 'text': '5 passed'}]}}},
    ])
    path.write_bytes(first + second[:len(second) // 2])

    adapter = DeepSeekHarnessAdapter([tmp_path / 'sessions'])
    cursor = CollectionCursor()
    location = SourceLocation(path, adapter.name)
    adapter.start_offset = 0
    events = list(adapter.collect(location, cursor))
    assert [event.kind for event in events] == ['user_prompt']
    assert cursor.offset == len(first)

    path.write_bytes(first + second)
    adapter.start_offset = cursor.offset
    events = list(adapter.collect(location, cursor))
    assert [event.kind for event in events] == ['assistant_message', 'shell_command', 'tool_result']
    assert events[0].summary == '已修复登录跳转'
    assert events[1].metadata['tool'] == 'shell'
    assert events[2].summary == '5 passed'
    assert cursor.offset == path.stat().st_size


def test_trae_reads_session_memory_with_local_timezone(tmp_path):
    base = Path(f'{tmp_path.drive}\\') / f'awt-trae-{uuid4().hex[:6]}'
    cwd = base / 'w'
    memory = base / 'm' / 'projects'
    try:
        cwd.mkdir(parents=True)
        resolved = str(cwd.resolve())
        drive, rest = resolved[0], resolved[3:]
        key = '-' + drive.lower() + '-' + re.sub(r'[^a-z0-9]+', '-', rest.lower()).strip('-')
        path = memory / (key + '--p2-deadbeef') / '20261030' / 'session_memory_sess-1.jsonl'
        write(path, [{
            'intent': '修复报销单导出乱码',
            'actions': ['定位编码问题', '修改导出逻辑'],
            'outcome': '导出文件恢复为 UTF-8',
            'learned': ['导出前需统一编码'],
            'message_summary_time': '2026-10-30 09:15:00',
            'message_id': 'm1',
        }])
        adapter = TraeAdapter([memory], timezone_name='Asia/Shanghai')
        events = collect(adapter, path)

        assert [event.kind for event in events] == ['user_prompt', 'assistant_message']
        assert events[0].summary == '修复报销单导出乱码'
        assert '导出文件恢复为 UTF-8' in events[1].summary
        assert events[0].occurred_at == '2026-10-30T01:15:00+00:00'
        assert events[0].session_id == 'sess-1'
        assert events[0].cwd == resolved
    finally:
        shutil.rmtree(base, ignore_errors=True)
