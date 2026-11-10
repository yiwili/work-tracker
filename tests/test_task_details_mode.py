import json

import pytest
from pydantic import ValidationError

from awt import daily_analysis, prompts
from awt.models import Settings


CONCISE_TEXT = '详细描述（details）要求（精简模式）'
NONE_TEXT = '详细描述（details）要求（不生成详细描述）'


def test_settings_defaults_to_standard():
    assert Settings().task_details_mode == 'standard'


def test_settings_accepts_each_mode():
    for mode in ('standard', 'concise', 'none'):
        assert Settings(task_details_mode=mode).task_details_mode == mode


def test_settings_rejects_unknown_mode():
    with pytest.raises(ValidationError):
        Settings(task_details_mode='verbose')


def test_prompt_appends_concise_instruction():
    text = prompts.prompt(Settings(task_details_mode='concise'), 'task_writer')
    assert CONCISE_TEXT in text and NONE_TEXT not in text
    assert text.startswith(prompts.default('task_writer'))


def test_prompt_appends_none_instruction():
    text = prompts.prompt(Settings(task_details_mode='none'), 'task_writer')
    assert NONE_TEXT in text and CONCISE_TEXT not in text


def test_prompt_standard_has_no_details_instruction():
    text = prompts.prompt(Settings(task_details_mode='standard'), 'task_writer')
    assert CONCISE_TEXT not in text and NONE_TEXT not in text


def test_prompt_detailed_modes_only_apply_to_task_writer():
    for role in ('scope_filter', 'time_estimator', 'manual_writer', 'style_template'):
        text = prompts.prompt(Settings(task_details_mode='none'), role)
        assert NONE_TEXT not in text


def test_prompt_keeps_user_supplement_after_details_instruction():
    settings = Settings(task_details_mode='concise', agent_prompts={'task_writer': '优先使用中文术语'})
    text = prompts.prompt(settings, 'task_writer')
    assert text.index(CONCISE_TEXT) < text.index('优先使用中文术语')


def test_format_details_none_returns_empty():
    assert daily_analysis._format_task_details('任意详细描述', Settings(task_details_mode='none')) == ''


def test_format_details_concise_extracts_first_sentence():
    settings = Settings(task_details_mode='concise')
    assert daily_analysis._format_task_details('修复接口超时问题。随后补充了日志。', settings) == '修复接口超时问题'
    assert daily_analysis._format_task_details('第一句。第二句', settings) == '第一句'


def test_format_details_concise_limits_to_60_chars():
    settings = Settings(task_details_mode='concise')
    formatted = daily_analysis._format_task_details('很长' * 100, settings)
    assert len(formatted) == 60


def test_format_details_standard_redacts():
    settings = Settings(redact_patterns=['secretword'])
    formatted = daily_analysis._format_task_details('修复 secretword 泄露问题', settings)
    assert 'secretword' not in formatted and '[REDACTED]' in formatted


def test_parse_result_fills_missing_details_with_empty_string():
    payload = json.dumps({'summary': '', 'tasks': [
        {'title': '修复接口超时', 'details': None, 'project': 'archive', 'category': 'Bug 修复', 'refs': ['e1']},
    ]}, ensure_ascii=False)
    parsed = daily_analysis.parse_result(payload, {'e1': {}})
    assert parsed['tasks'][0]['details'] == ''
