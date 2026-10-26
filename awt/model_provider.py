"""Model connections. Credentials never enter settings responses, task prompts, or logs."""
import base64
import ctypes
import json
import os
import re
from pathlib import Path

import httpx

from . import db
from .collectors import stable
from .models import Settings

PRESETS = {
    'deepseek': {'label': 'DeepSeek', 'endpoint': 'https://api.deepseek.com/chat/completions', 'model': 'deepseek-flash', 'protocol': 'openai'},
    'glm': {'label': '智谱 GLM', 'endpoint': 'https://open.bigmodel.cn/api/paas/v4/chat/completions', 'model': 'glm-5.3', 'protocol': 'openai'},
    'ccswitch': {'label': 'CCSwitch / Claude Code 当前接口', 'endpoint': '', 'model': '', 'protocol': 'anthropic'},
    'custom': {'label': '自定义兼容接口', 'endpoint': '', 'model': '', 'protocol': 'openai'},
}


class ModelError(ValueError):
    pass


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


def connection(settings):
    if settings.llm_provider == 'ccswitch':
        return claude_connection(settings)
    key = _saved_key(settings) or os.environ.get(settings.llm_key_env, '')
    if not key:
        raise ModelError('请先保存 API Key，或配置对应的服务端环境变量')
    if not settings.llm_model.strip():
        raise ModelError('请填写模型名称')
    return {'endpoint': settings.llm_endpoint, 'model': settings.llm_model.strip(), 'key': key, 'protocol': settings.llm_protocol, 'auth': 'api_key'}


def status(settings):
    try:
        c = connection(settings)
        return {'configured': True, 'provider': settings.llm_provider, 'endpoint': c['endpoint'], 'model': c['model'], 'protocol': c['protocol'], 'error': ''}
    except ModelError as exc:
        return {'configured': False, 'provider': settings.llm_provider, 'endpoint': settings.llm_endpoint if settings.llm_provider != 'ccswitch' else '', 'model': settings.llm_model, 'error': str(exc)}


def complete(settings, system, text, *, max_tokens=12000):
    c = connection(settings)
    headers = {'Content-Type': 'application/json'}
    if c['protocol'] == 'anthropic':
        headers['anthropic-version'] = '2023-06-01'
        headers['Authorization' if c['auth'] == 'bearer' else 'x-api-key'] = ('Bearer ' if c['auth'] == 'bearer' else '') + c['key']
        body = {'model': c['model'], 'system': system, 'max_tokens': max_tokens, 'messages': [{'role': 'user', 'content': text}], 'stream': False}
    else:
        headers['Authorization'] = 'Bearer ' + c['key']
        body = {'model': c['model'], 'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': text}], 'max_tokens': max_tokens, 'stream': False}
    # Slow reasoning providers may not emit a non-stream response for a while. No automatic paid retry.
    try:
        with httpx.Client(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False) as client:
            result = client.post(c['endpoint'], headers=headers, json=body)
            result.raise_for_status()
            value = result.json()
        if c['protocol'] == 'anthropic':
            if value.get('stop_reason') == 'max_tokens':
                raise ModelError('模型输出被截断；原任务已保留，请调整模型后重试')
            text = '\n'.join(block['text'] for block in value.get('content', []) if block.get('type') == 'text')
        else:
            if value['choices'][0].get('finish_reason') == 'length':
                raise ModelError('模型输出被截断；原任务已保留，请调整模型后重试')
            text = value['choices'][0]['message']['content']
        if not isinstance(text, str) or not text.strip():
            raise ModelError('模型未返回可用文本；原任务已保留')
        return text
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        descriptions = {401:'密钥无效或已过期',403:'供应商拒绝访问或该套餐不支持此接口',404:'接口地址或模型名称不存在',429:'请求限流或额度不足'}
        raise ModelError(f"模型请求失败（HTTP {code}）：{descriptions.get(code, '请检查供应商服务状态')}；原任务已保留") from None
    except httpx.HTTPError:
        raise ModelError('模型连接超时或网络不可用；原任务已保留，可稍后重试') from None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise ModelError('模型响应格式不兼容；原任务已保留') from None


def test_connection(settings):
    complete(settings, 'Reply with OK only. Do not use tools.', 'Connection test. Reply OK.', max_tokens=64)
    return {**status(settings), 'ok': True, 'message': '已完成真实模型请求，连接成功；未发送工作对话。'}
