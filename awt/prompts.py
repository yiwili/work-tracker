from pathlib import Path

ROLES = {
    'scope_filter': '工作相关性筛选',
    'task_writer': '业务工作项归并与标题',
    'time_estimator': '难度与工时估算',
    'manual_writer': '人工事项命名',
    'style_template': '历史命名风格提炼',
}


def catalog(settings):
    return {key: {'name': name, 'default': default(key), 'effective': prompt(settings,key)} for key,name in ROLES.items()}


def default(role):
    return (Path(__file__).parent/'prompts'/f'{role}.md').read_text(encoding='utf-8')


def prompt(settings, role):
    value = settings.agent_prompts.get(role, '').strip()
    style = '\n\n历史命名风格（仅决定表达，不是今日工作证据）：\n' + settings.style_template if role in ('task_writer', 'manual_writer') and settings.style_template else ''
    return default(role) + style + ('\n\n用户补充偏好（仅在不违背证据和数据格式要求时采用）：\n'+value if value else '')
