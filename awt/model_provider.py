"""Model connections. Credentials never enter settings responses, task prompts, or logs."""
import base64
import ctypes
import json
import logging
import os
import re
from pathlib import Path

import httpx

from . import db
from .collectors import stable
from .models import Settings

GLM_MODELS = [
    {'id': 'glm-5.3-flash', 'label': 'GLM-5.3-Flash'},
    {'id': 'glm-5.3-flashx', 'label': 'GLM-5.3-FlashX'},
    {'id': 'glm-5.3', 'label': 'GLM-5.3'},
    {'id': 'glm-5.2', 'label': 'GLM-5.2'},
    {'id': 'glm-5.1', 'label': 'GLM-5.1'},
    {'id': 'glm-5', 'label': 'GLM-5'},
    {'id': 'glm-5-turbo', 'label': 'GLM-5-Turbo'},
    {'id': 'glm-4.7', 'label': 'GLM-4.7'},
    {'id': 'glm-4.6', 'label': 'GLM-4.6'},
    {'id': 'glm-4.5', 'label': 'GLM-4.5'},
    {'id': 'glm-4.5-air', 'label': 'GLM-4.5-Air'},
    {'id': 'glm-4.5-flash', 'label': 'GLM-4.5-Flash（免费）'},
]

PRESETS = {
    'deepseek': {
        'label': 'DeepSeek',
        'endpoint': 'https://api.deepseek.com/chat/completions',
        'model': 'deepseek-chat',
        'protocol': 'openai',
        'models': [
            {'id': 'deepseek-chat', 'label': 'DeepSeek Chat'},
            {'id': 'deepseek-reasoner', 'label': 'DeepSeek Reasoner'},
        ],
    },
    'glm': {
        'label': '智谱 GLM（标准 API）',
        'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions',
        'model': 'glm-5.3-flash',
        'protocol': 'openai',
        'models': GLM_MODELS,
    },
    'glm-coding': {
        'label': '智谱 GLM Coding Plan',
        'endpoint': 'https://open.bigmodel.cn/api/coding/paas/v4/chat/completions',
        'model': 'glm-5.3-flash',
        'protocol': 'openai',
        'models': [item for item in GLM_MODELS if item['id'] != 'glm-4.5-flash'],
    },
    'ccswitch': {'label': 'CCSwitch / Claude Code 当前接口', 'endpoint': '', 'model': '', 'protocol': 'anthropic', 'models': []},
    'custom': {'label': '自定义兼容接口', 'endpoint': '', 'model': '', 'protocol': 'openai', 'models': []},
}


class ModelError(ValueError):
    pass


def daily_reasoning_model(model, purpose):
    name=model.lower().rsplit('/',1)[-1]
    return purpose in ('scope_filter','task_writer') and bool(re.match(r'^glm-5\.3(?:$|[-\[])',name))


def output_budget(model, requested, purpose):
    # GLM-5.3 always reasons; its thinking and final JSON share max_tokens.
    # Only daily analysis reserves more room. Connection probes stay tiny,
    # other providers retain their caller's cap, and we never retry a paid call.
    if daily_reasoning_model(model,purpose):
        return max(requested,32768)
    return requested


def truncated(purpose, budget):
    stage={'scope_filter':'工作相关性筛选','task_writer':'工作项归并'}.get(purpose,'模型')
    logging.getLogger('awt.model').warning('模型输出被截断 stage=%s max_tokens=%d',purpose or 'general',budget)
    return ModelError(f'{stage}输出被截断（本次输出预算{budget} tokens，包含模型思考）；原任务已保留。可减少单次分析范围后重新分析，不会自动重复请求。')


def stream_text(lines, protocol, purpose, budget):
    """Read complete SSE messages; discard thinking and reject partial streams."""
    parts=[];data=[];reason=None;done=False
    def events():
        for line in lines:
            if not line:
                if data:yield '\n'.join(data);data.clear()
            elif line.startswith('data:'):
                data.append(line[5:].lstrip())
        if data:yield '\n'.join(data)
    try:
        for payload in events():
            if payload=='[DONE]':done=True;continue
            value=json.loads(payload)
            if value.get('type')=='error' or 'error' in value:
                raise ModelError('模型流式响应发生错误；原任务已保留，不会自动重复请求')
            if protocol=='anthropic':
                kind=value.get('type')
                if kind=='content_block_start' and value.get('content_block',{}).get('type')=='text':
                    parts.append(value['content_block'].get('text',''))
                elif kind=='content_block_delta' and value.get('delta',{}).get('type')=='text_delta':
                    parts.append(value['delta']['text'])
                elif kind=='message_delta':reason=value.get('delta',{}).get('stop_reason',reason)
                elif kind=='message_stop':done=True
                if reason=='max_tokens':raise truncated(purpose,budget)
            else:
                for choice in value.get('choices',[]):
                    content=choice.get('delta',{}).get('content')
                    if content is not None:parts.append(content)
                    if choice.get('finish_reason') is not None:reason=choice['finish_reason']
                    if reason=='length':raise truncated(purpose,budget)
        if not done or reason not in ('end_turn','stop_sequence','stop'):
            raise ModelError('模型流式输出未完整结束；原任务已保留，请稍后重新分析')
        text=''.join(parts)
        if not text.strip():raise ModelError('模型未返回可用文本；原任务已保留')
        return text
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        if isinstance(exc,ModelError):raise
        raise ModelError('模型流式响应格式不兼容；原任务已保留') from None


def official_model(provider, model):
    for item in PRESETS.get(provider, {}).get('models', []):
        if model.strip().lower() == item['id'].lower():
            return item['id']
    return model.strip()


def normalize(settings):
    settings.llm_model = official_model(settings.llm_provider, settings.llm_model)
    return settings


def _crypt(data: bytes, decrypt=False):
    if os.name != 'nt':
        raise ModelError('当前系统请使用 API Key 环境变量；网页密钥存储需要 Windows DPAPI')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt32 = ctypes.WinDLL('crypt32', use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ModelError('无法访问本机加密的 API Key，请重新保存密钥')
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel32 = ctypes.WinDLL('kernel32')
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree(output.data)


def _key_id(settings):
    return stable(settings.llm_provider, settings.llm_endpoint)


def _vault():
    path = db.data_dir() / 'model-credentials.json'
    if not path.exists():
        return path, {}
    try:
        return path, json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise ModelError('本机密钥存储不可读，请检查数据目录')


def save_key(settings, key):
    path, data = _vault()
    if settings.llm_provider == 'ccswitch':
        raise ModelError('CCSwitch 从当前 Claude Code 配置读取凭据，无需另存 API Key')
    if not key.strip():
        raise ModelError('API Key 不能为空')
    data[_key_id(settings)] = base64.b64encode(_crypt(key.strip().encode())).decode()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data), encoding='utf-8')
    temporary.replace(path)


def remove_key(settings):
    path, data = _vault()
    data.pop(_key_id(settings), None)
    if path.exists():
        path.write_text(json.dumps(data), encoding='utf-8')


def _saved_key(settings):
    _, data = _vault()
    encrypted = data.get(_key_id(settings))
    return _crypt(base64.b64decode(encrypted), decrypt=True).decode() if encrypted else ''


def claude_connection(settings):
    path = Path(settings.ccswitch_config_path).expanduser() if settings.ccswitch_config_path else Path.home() / '.claude' / 'settings.json'
    if not settings.ccswitch_config_path:
        cc = Path.home() / '.cc-switch' / 'settings.json'
        try:
            directory = json.loads(cc.read_text(encoding='utf-8-sig')).get('claudeConfigDir')
            if directory:
                path = Path(directory).expanduser() / 'settings.json'
        except (OSError, ValueError, TypeError):
            pass
    try:
        value = json.loads(path.read_text(encoding='utf-8-sig'))
        env = value.get('env', {})
        endpoint = env.get('ANTHROPIC_BASE_URL', 'https://api.anthropic.com').rstrip('/')
        key = env.get('ANTHROPIC_AUTH_TOKEN') or env.get('ANTHROPIC_API_KEY') or ''
        auth = 'bearer' if env.get('ANTHROPIC_AUTH_TOKEN') else 'api_key'
        model = settings.llm_model or env.get('ANTHROPIC_MODEL') or env.get('ANTHROPIC_DEFAULT_SONNET_MODEL') or value.get('model', '')
        if model in ('sonnet', 'opus', 'haiku'):
            model = env.get('ANTHROPIC_DEFAULT_'+model.upper()+'_MODEL', '')
        model = re.sub(r'\[(?:1m|200k)\]$', '', model, flags=re.I)
        endpoint = endpoint if endpoint.endswith('/messages') else endpoint + ('/messages' if endpoint.endswith('/v1') else '/v1/messages')
        Settings.valid_endpoint(endpoint)
        if not isinstance(key, str) or not model or not key:
            raise ModelError('Claude Code 当前配置缺少 API 凭据或明确模型名；官方登录凭据不自动复用')
        return {'endpoint': endpoint, 'model': model, 'key': key, 'protocol': 'anthropic', 'auth': auth}
    except (OSError, ValueError, TypeError, AttributeError):
        raise ModelError('无法读取 Claude Code 当前接口，请在 CCSwitch 选择 API 供应商，或指定有效 settings.json') from None


def connection(settings, key=None):
    if settings.llm_provider == 'ccswitch':
        return claude_connection(settings)
    key = key or _saved_key(settings) or os.environ.get(settings.llm_key_env, '')
    if not key:
        raise ModelError('请先保存 API Key，或配置对应的服务端环境变量')
    model = official_model(settings.llm_provider, settings.llm_model)
    if not model:
        raise ModelError('请填写模型名称')
    return {'endpoint': settings.llm_endpoint, 'model': model, 'key': key, 'protocol': settings.llm_protocol, 'auth': 'api_key'}


def status(settings, key=None):
    try:
        c = connection(settings, key)
        return {'configured': True, 'provider': settings.llm_provider, 'endpoint': c['endpoint'], 'model': c['model'], 'protocol': c['protocol'], 'error': ''}
    except ModelError as exc:
        return {'configured': False, 'provider': settings.llm_provider, 'endpoint': settings.llm_endpoint if settings.llm_provider != 'ccswitch' else '', 'model': settings.llm_model, 'error': str(exc)}


def _provider_message(response):
    try:
        value = response.json()
    except ValueError:
        return ''
    error = value.get('error') if isinstance(value, dict) else None
    if isinstance(error, dict):
        text = error.get('message') or error.get('msg') or ''
    elif isinstance(error, str):
        text = error
    else:
        text = ''
    return str(text).strip()[:200]


def complete(settings, system, text, *, max_tokens=12000, purpose='', require_text=True, key=None):
    c = connection(settings, key)
    max_tokens=output_budget(c['model'],max_tokens,purpose)
    headers = {'Content-Type': 'application/json'}
    if c['protocol'] == 'anthropic':
        headers['anthropic-version'] = '2023-06-01'
        headers['Authorization' if c['auth'] == 'bearer' else 'x-api-key'] = ('Bearer ' if c['auth'] == 'bearer' else '') + c['key']
        body = {'model': c['model'], 'system': system, 'max_tokens': max_tokens, 'messages': [{'role': 'user', 'content': text}], 'stream': False}
    else:
        headers['Authorization'] = 'Bearer ' + c['key']
        body = {'model': c['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': text}], 'max_tokens': max_tokens, 'stream': False}
    if daily_reasoning_model(c['model'],purpose):
        # GLM defaults to maximum reasoning. Classifying and summarizing logs
        # uses its supported light effort without disabling forced thinking.
        body['reasoning_effort']='low'
        body['stream']=True
    # Slow reasoning providers may not emit a non-stream response for a while. No automatic paid retry.
    try:
        with httpx.Client(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False) as client:
            result = client.post(c['endpoint'], headers=headers, json=body)
            result.raise_for_status()
            if body['stream'] and result.headers.get('content-type','').startswith('text/event-stream'):
                return stream_text(result.iter_lines(),c['protocol'],purpose,max_tokens)
            value = result.json()
        if c['protocol'] == 'anthropic':
            if require_text and value.get('stop_reason') == 'max_tokens':
                raise truncated(purpose,max_tokens)
            text = '\n'.join(block['text'] for block in value.get('content', []) if block.get('type') == 'text')
        else:
            if require_text and value['choices'][0].get('finish_reason') == 'length':
                raise truncated(purpose,max_tokens)
            text = value['choices'][0]['message'].get('content') or ''
        if require_text and (not isinstance(text, str) or not text.strip()):
            raise ModelError('模型未返回可用文本；原任务已保留')
        return text
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        descriptions = {401:'密钥无效或已过期',403:'供应商拒绝访问或该套餐不支持此接口',404:'接口地址或模型名称不存在',429:'请求限流或额度不足'}
        detail = (_provider_message(exc.response) or descriptions.get(code, '请检查供应商服务状态')).rstrip('。.；; ')
        raise ModelError(f'模型请求失败（HTTP {code}）：{detail}；原任务已保留') from None
    except httpx.HTTPError:
        raise ModelError('模型连接超时或网络不可用；原任务已保留，可稍后重试') from None
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError('模型响应格式不兼容；原任务已保留') from None


def test_connection(settings, key=None):
    complete(settings, 'Reply with OK only. Do not use tools.', 'Connection test. Reply OK.', max_tokens=128, require_text=False, key=key)
    return {**status(settings, key), 'ok': True, 'message': '已完成真实模型请求，连接成功；未发送工作对话。'}
