import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

import pytest

from awt.collectors import (AntigravityAdapter, CollectionCursor, CursorAdapter, DeepSeekHarnessAdapter,
                            DoubaoAdapter, GeminiAdapter, QoderAdapter, SourceLocation,
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
    assert any('.workbuddy-ai' in path for path in settings.workbuddy_intl.paths)
    assert any(path.endswith('workbuddy\\projects') or path.endswith('workbuddy/projects') for path in settings.workbuddy_cn.paths)
    assert any('.qoder-cn' in path for path in settings.qoder_cn.paths)
    assert any(path.endswith('.qoder\\projects') or path.endswith('.qoder/projects') for path in settings.qoder_intl.paths)
    assert any('.trae-cn' in path for path in settings.trae_cn.paths)
    assert any(path.endswith('.trae\\memory\\projects') or path.endswith('.trae/memory/projects') for path in settings.trae_intl.paths)
    assert any(path.endswith('.dsh\\sessions') or path.endswith('.dsh/sessions') for path in settings.deepseek_harness.paths)
    assert any(path.endswith('.cursor\\projects') or path.endswith('.cursor/projects') for path in settings.cursor.paths)
    assert any('globalStorage' in path and 'Cursor' in path for path in settings.cursor.paths)


def test_legacy_merged_sources_split_into_domestic_and_international():
    settings = Settings.model_validate({
        'workbuddy': {'enabled': True, 'paths': ['C:/Users/me/.workbuddy-ai/projects', 'C:/Users/me/.workbuddy/projects']},
        'qoder': {'enabled': False, 'paths': ['C:/Users/me/.qoder/projects', 'C:/Users/me/.qoder-cn/projects']},
        'analysis_sources': ['workbuddy', 'qoder', 'git'],
    })
    assert settings.workbuddy_intl.paths == ['C:/Users/me/.workbuddy-ai/projects']
    assert settings.workbuddy_cn.paths == ['C:/Users/me/.workbuddy/projects']
    assert settings.qoder_intl.enabled is False
    assert settings.qoder_cn.paths == ['C:/Users/me/.qoder-cn/projects']
    assert settings.analysis_sources == ['workbuddy_cn', 'workbuddy_intl', 'qoder_cn', 'qoder_intl', 'git']


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


def test_antigravity_reads_transcript_and_parses_tools_and_prompts(tmp_path):
    brain = tmp_path / 'brain'
    conv_id = 'conv-uuid-1'
    transcript_path = brain / conv_id / '.system_generated' / 'logs' / 'transcript.jsonl'
    write(transcript_path, [
        {
            'step_index': 0, 'source': 'USER_EXPLICIT', 'type': 'USER_INPUT',
            'created_at': '2026-11-04T01:00:00Z',
            'content': '<USER_REQUEST>\n更新日报组件并测试\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime\n</ADDITIONAL_METADATA>'
        },
        {
            'step_index': 1, 'source': 'MODEL', 'type': 'PLANNER_RESPONSE',
            'created_at': '2026-11-04T01:01:00Z',
            'tool_calls': [
                {'name': 'run_command', 'args': {'CommandLine': 'pytest', 'WaitMsBeforeAsync': 5000}},
                {'name': 'read_file', 'args': {'Path': str(tmp_path / 'file.txt')}}
            ]
        },
        {
            'step_index': 2, 'source': 'MODEL', 'type': 'GENERIC',
            'created_at': '2026-11-04T01:02:00Z',
            'content': 'pytest 1 passed'
        },
        {
            'step_index': 3, 'source': 'MODEL', 'type': 'PLANNER_RESPONSE',
            'created_at': '2026-11-04T01:03:00Z',
            'content': '已完成日报组件更新'
        }
    ])
    # SQLite conversation_summaries.db
    import sqlite3
    db_file = tmp_path / 'conversation_summaries.db'
    with sqlite3.connect(db_file) as conn:
        conn.execute('CREATE TABLE conversation_summaries (conversation_id TEXT PRIMARY KEY, workspace_uris TEXT)')
        conn.execute('INSERT INTO conversation_summaries VALUES (?, ?)',
                     (conv_id, json.dumps(['file:///d:/Projects/ai-work-tracker'])))

    adapter = AntigravityAdapter([brain])
    locations = adapter.discover()
    assert len(locations) == 1
    events = collect(adapter, transcript_path)

    assert [e.kind for e in events] == ['user_prompt', 'shell_command', 'tool_call', 'tool_result', 'assistant_message']
    assert events[0].summary == '更新日报组件并测试'
    assert events[0].session_id == conv_id
    assert 'Projects' in events[0].cwd
    assert events[1].kind == 'shell_command'
    assert events[1].metadata['tool'] == 'run_command'
    assert events[2].metadata['tool'] == 'read_file'
    assert events[3].summary == 'pytest 1 passed'
    assert events[4].summary == '已完成日报组件更新'


def test_gemini_reads_session_events(tmp_path):
    session_file = tmp_path / 'sessions' / 'gemini_test.jsonl'
    write(session_file, [
        {
            'role': 'user', 'session_id': 'gem-1', 'cwd': str(tmp_path),
            'timestamp': '2026-11-04T02:00:00Z',
            'parts': [{'text': '接入 Gemini 数据源'}]
        },
        {
            'role': 'model', 'session_id': 'gem-1',
            'timestamp': '2026-11-04T02:01:00Z',
            'parts': [
                {'text': '开始接入 Gemini'},
                {'functionCall': {'name': 'run_command', 'args': {'cmd': 'npm test'}}}
            ]
        },
        {
            'type': 'function_response', 'session_id': 'gem-1',
            'timestamp': '2026-11-04T02:02:00Z',
            'output': 'All tests passed'
        }
    ])
    adapter = GeminiAdapter([tmp_path / 'sessions'])
    events = collect(adapter, session_file)

    assert [e.kind for e in events] == ['user_prompt', 'shell_command', 'assistant_message', 'tool_result']
    assert events[0].summary == '接入 Gemini 数据源'
    assert events[0].session_id == 'gem-1'
    assert events[1].metadata['tool'] == 'run_command'
    assert events[2].summary == '开始接入 Gemini'
    assert events[3].summary == 'All tests passed'


def test_doubao_reads_session_events(tmp_path):
    session_file = tmp_path / 'Doubao' / 'session_1.jsonl'
    write(session_file, [
        {
            'role': 'user', 'session_id': 'db-1', 'cwd': str(tmp_path),
            'timestamp': '2026-11-04T03:00:00Z',
            'content': '总结今日开发进展'
        },
        {
            'role': 'assistant', 'session_id': 'db-1',
            'timestamp': '2026-11-04T03:01:00Z',
            'content': '今日已完成豆包桌面端接入',
            'tool_calls': [
                {'name': 'run_command', 'args': {'CommandLine': 'pytest tests/test_collectors.py'}}
            ]
        },
        {
            'type': 'tool_result', 'session_id': 'db-1',
            'timestamp': '2026-11-04T03:02:00Z',
            'output': '8 passed'
        }
    ])
    adapter = DoubaoAdapter([tmp_path / 'Doubao'])
    events = collect(adapter, session_file)

    assert [e.kind for e in events] == ['user_prompt', 'shell_command', 'assistant_message', 'tool_result']
    assert events[0].summary == '总结今日开发进展'
    assert events[0].session_id == 'db-1'
    assert events[1].kind == 'shell_command'
    assert events[1].metadata['tool'] == 'run_command'
    assert events[2].summary == '今日已完成豆包桌面端接入'
    assert events[3].summary == '8 passed'


def test_cursor_reads_agent_transcript_jsonl(tmp_path):
    transcript = tmp_path / '.cursor' / 'projects' / 'd-Projects-demo' / 'agent-transcripts' / 'cur-1.jsonl'
    write(transcript, [
        {
            'role': 'user', 'session_id': 'cur-1', 'cwd': str(tmp_path / 'demo'),
            'timestamp': '2026-11-16T07:00:00Z',
            'message': {'role': 'user', 'content': [{'type': 'text', 'text': '新增 Cursor 采集器'}]},
        },
        {
            'role': 'assistant', 'session_id': 'cur-1',
            'timestamp': '2026-11-16T07:01:00Z',
            'message': {
                'role': 'assistant',
                'content': [
                    {'type': 'tool_use', 'name': 'run_terminal_cmd', 'input': {'command': 'pytest'}},
                    {'type': 'text', 'text': '已完成 Cursor 采集器接入'},
                ],
            },
        },
        {
            'type': 'tool_result', 'session_id': 'cur-1',
            'timestamp': '2026-11-16T07:02:00Z',
            'output': 'all passed',
        },
    ])
    adapter = CursorAdapter([tmp_path / '.cursor' / 'projects'])
    locations = adapter.discover()
    assert [loc.path for loc in locations] == [transcript]
    events = collect(adapter, transcript)
    assert [e.kind for e in events] == ['user_prompt', 'shell_command', 'assistant_message', 'tool_result']
    assert events[0].summary == '新增 Cursor 采集器'
    assert events[0].cwd == str(tmp_path / 'demo')
    assert events[1].metadata['tool'] == 'run_terminal_cmd'
    assert events[2].summary == '已完成 Cursor 采集器接入'
    assert events[3].summary == 'all passed'


def test_cursor_reads_global_vscdb_cursor_disk_kv(tmp_path):
    import sqlite3

    vscdb = tmp_path / 'globalStorage' / 'state.vscdb'
    vscdb.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(vscdb) as conn:
        conn.execute('CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute(
            'INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)',
            ('composerData:comp-1', json.dumps({
                'composerId': 'comp-1',
                'workspacePath': str(tmp_path / 'repo'),
                'createdAt': 1763276400000,
            })),
        )
        conn.execute(
            'INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)',
            ('bubbleId:comp-1:b1', json.dumps({
                'type': 1,
                'text': '检查登录接口报错',
                'createdAt': '2026-11-16T08:00:00Z',
            })),
        )
        conn.execute(
            'INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)',
            ('bubbleId:comp-1:b2', json.dumps({
                'type': 2,
                'text': '已定位并修复 token 校验逻辑',
                'createdAt': '2026-11-16T08:01:00Z',
                'toolFormerData': {
                    'name': 'edit_file',
                    'rawArgs': json.dumps({'file_path': str(tmp_path / 'repo' / 'auth.py')}),
                },
            })),
        )

    adapter = CursorAdapter([tmp_path / 'globalStorage'])
    locations = adapter.discover()
    assert [loc.path for loc in locations] == [vscdb]
    cursor = CollectionCursor()
    events = list(adapter.collect(locations[0], cursor))
    assert [e.kind for e in events] == ['user_prompt', 'tool_call', 'assistant_message']
    assert events[0].session_id == 'comp-1'
    assert events[0].cwd == str(tmp_path / 'repo')
    assert events[0].summary == '检查登录接口报错'
    assert events[1].metadata['tool'] == 'edit_file'
    assert events[2].summary == '已定位并修复 token 校验逻辑'
    assert cursor.offset == vscdb.stat().st_size


def test_cursor_reads_workspace_vscdb_item_table_and_workspace_json(tmp_path):
    import sqlite3

    ws_dir = tmp_path / 'workspaceStorage' / 'abc123'
    ws_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = tmp_path / 'my-project'
    (ws_dir / 'workspace.json').write_text(
        json.dumps({'folder': repo_dir.as_uri()}),
        encoding='utf-8',
    )
    vscdb = ws_dir / 'state.vscdb'
    with sqlite3.connect(vscdb) as conn:
        conn.execute('CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)')
        conn.execute(
            'INSERT INTO ItemTable (key, value) VALUES (?, ?)',
            ('workbench.panel.aichat.view.aichat.chatdata', json.dumps({
                'tabs': [{
                    'tabId': 'tab-1',
                    'bubbles': [
                        {'type': 'user', 'text': '补充单元测试', 'timestamp': '2026-11-16T09:00:00Z'},
                        {'type': 'ai', 'text': '已新增 3 个测试用例', 'timestamp': '2026-11-16T09:01:00Z'},
                    ],
                }],
            })),
        )

    adapter = CursorAdapter([tmp_path / 'workspaceStorage'])
    locations = adapter.discover()
    assert [loc.path for loc in locations] == [vscdb]
    events = collect(adapter, vscdb)
    assert [e.kind for e in events] == ['user_prompt', 'assistant_message']
    assert events[0].session_id == 'tab-1'
    assert Path(events[0].cwd) == repo_dir
    assert events[0].summary == '补充单元测试'
    assert events[1].summary == '已新增 3 个测试用例'
