"""Model connections. Credentials never enter settings responses, task prompts, or logs."""
import base64
import contextvars
import ctypes
import json
import logging
import os
import re
from contextlib import contextmanager
from time import monotonic
from pathlib import Path

import httpx

from . import db
from . import usage as usage_ledger
from .collectors import stable
from .models import Settings
from .analysis_progress import begin_stream, current, emit, stream_delta, stream_usage

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
    # Only normalized, provider-safe reasons are copied to the live analysis
    # panel; arbitrary upstream text must never be surfaced there.
    user_reason = ''


def _safe_error(text):
    error = ModelError(text)
    error.user_reason = text
    return error


STAGE_LABELS={'scope_filter':'工作相关性筛选','task_writer':'工作项归并','time_estimator':'工时估算','manual_writer':'人工事项命名'}
STREAM_PURPOSES=('scope_filter','task_writer','time_estimator','manual_writer')
_usage_calls=contextvars.ContextVar('awt_usage_calls',default=None)


@contextmanager
def usage_tracker():
    calls=[]
    token=_usage_calls.set(calls)
    try:
        yield calls
    finally:
        _usage_calls.reset(token)


def _estimate_tokens(text):
    """Rough local count when a provider omits usage; CJK is about one token each."""
    text=text or ''
    cjk=sum(1 for char in text if '\u4e00'<=char<='\u9fff')
    return max(1,cjk+(len(text)-cjk)//4)


def _record_usage(purpose, model, system, input_text, output_text, usage):
    prompt=(usage or {}).get('prompt')
    completion=(usage or {}).get('completion')
    estimated=prompt is None or completion is None
    entry={'purpose':purpose or 'general','model':model,
           'prompt_tokens':prompt if prompt is not None else _estimate_tokens(system)+_estimate_tokens(input_text),
           'completion_tokens':completion if completion is not None else _estimate_tokens(output_text),
           'estimated':estimated}
    calls=_usage_calls.get()
    if calls is not None:
        calls.append(entry)
    usage_ledger.record_call(entry)


def usage_summary(calls=None):
    calls=list(calls if calls is not None else (_usage_calls.get() or []))
    if not calls:
        return None
    stages={}
    for call in calls:
        stage=stages.setdefault(call['purpose'],{'stage':call['purpose'],'label':STAGE_LABELS.get(call['purpose'],call['purpose']),
                                                 'calls':0,'prompt_tokens':0,'completion_tokens':0,'estimated':False})
        stage['calls']+=1
        stage['prompt_tokens']+=call['prompt_tokens']
        stage['completion_tokens']+=call['completion_tokens']
        stage['estimated']=stage['estimated'] or call['estimated']
    prompt=sum(call['prompt_tokens'] for call in calls)
    completion=sum(call['completion_tokens'] for call in calls)
    return {'calls':len(calls),'prompt_tokens':prompt,'completion_tokens':completion,'total_tokens':prompt+completion,
            'estimated':any(call['estimated'] for call in calls),'stages':list(stages.values())}


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
    stage=STAGE_LABELS.get(purpose,'模型')
    logging.getLogger('awt.model').warning('模型输出被截断 stage=%s max_tokens=%d',purpose or 'general',budget)
    return _safe_error(f'{stage}输出被截断（本次输出预算{budget} tokens，包含模型思考）；原任务已保留。可减少单次分析范围后重新分析，不会自动重复请求。')


def stream_text(lines, protocol, purpose, budget):
    """Read complete SSE messages and publish thinking/output deltas for the live window."""
    parts=[];data=[];reason=None;done=False;usage={'prompt':None,'completion':None}
    thinking_chars = 0
    output_chars = 0
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
            try:
                value=json.loads(payload)
            except ValueError:
                kind=_classify_error(0,payload[:2000].lower())
                if kind!='unknown':
                    raise _safe_error(_error_description(kind)) from None
                raise
            error=_stream_payload_error(value)
            if error:raise error
            if protocol == 'anthropic':
                delta = value.get('delta', {})
                block = value.get('content_block', {})
                thinking = (delta.get('thinking', '') or '') + (block.get('thinking', '') or '')
                output = (delta.get('text', '') or '') + (block.get('text', '') or '')
            else:
                thinking = ''
                output = ''
                for choice in value.get('choices', []):
                    delta = choice.get('delta', {})
                    thinking += delta.get('reasoning_content', '') or ''
                    output += delta.get('content', '') or ''
            thinking_chars += len(thinking)
            output_chars += len(output)
            stream_delta(thinking, output)
            if protocol=='anthropic':
                kind=value.get('type')
                if kind=='content_block_start' and value.get('content_block',{}).get('type')=='text':
                    parts.append(value['content_block'].get('text',''))
                elif kind=='content_block_delta' and value.get('delta',{}).get('type')=='text_delta':
                    parts.append(value['delta']['text'])
                elif kind=='message_start':
                    reported=value.get('message',{}).get('usage') or {}
                    if reported.get('input_tokens') is not None:usage['prompt']=reported['input_tokens']
                    if reported.get('output_tokens') is not None:usage['completion']=reported['output_tokens']
                    stream_usage(prompt=usage['prompt'],completion=usage['completion'])
                elif kind=='message_delta':
                    reason=value.get('delta',{}).get('stop_reason',reason)
                    reported=value.get('usage') or {}
                    if reported.get('output_tokens') is not None:usage['completion']=reported['output_tokens']
                    stream_usage(completion=usage['completion'])
                elif kind=='message_stop':done=True
                if reason=='max_tokens':raise truncated(purpose,budget)
            else:
                reported=value.get('usage')
                if isinstance(reported,dict):
                    if reported.get('prompt_tokens') is not None:usage['prompt']=reported['prompt_tokens']
                    if reported.get('completion_tokens') is not None:usage['completion']=reported['completion_tokens']
                    stream_usage(prompt=usage['prompt'],completion=usage['completion'])
                for choice in value.get('choices',[]):
                    content=choice.get('delta',{}).get('content')
                    if content is not None:parts.append(content)
                    if choice.get('finish_reason') is not None:reason=choice['finish_reason']
                    if reason=='length':raise truncated(purpose,budget)
        if not done or reason not in ('end_turn','stop_sequence','stop'):
            raise _safe_error('模型流式输出未完整结束；原任务已保留，请稍后重新分析')
        text=''.join(parts)
        if not text.strip():raise _safe_error('模型未返回可用文本；原任务已保留')
        emit(f'模型输出完成：思考 {thinking_chars} 字符，结果 {output_chars} 字符')
        return text,usage
    except (ValueError,KeyError,TypeError,AttributeError) as exc:
        if isinstance(exc,ModelError):raise
        raise _safe_error('模型流式响应格式不兼容；原任务已保留') from None


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


_ERROR_KINDS = {
    'quota_exhausted': (
        '供应商账户额度或余额不足',
        '账户余额、套餐配额或调用额度已用尽，请到供应商控制台充值、续费或更换 API Key/套餐后重试',
    ),
    'rate_limited': (
        '请求触发供应商限流',
        '请求频率或并发超过套餐上限，请稍后重试；持续出现时可减少分析频率或升级套餐',
    ),
    'auth': (
        'API Key 无效或已过期',
        '请在设置中重新填写有效密钥，并确认接口地址与该服务商匹配',
    ),
    'permission': (
        '当前账号无权使用该模型或接口',
        '请确认账号或套餐已开通该模型/接口，或改用账号可用的模型',
    ),
    'model_missing': (
        '模型名称或接口地址不存在',
        '请在设置中核对模型名称和接口地址，必要时重新保存',
    ),
    'context_length': (
        '输入内容超过模型上下文上限',
        '请缩小分析范围（减少对话来源或日期跨度）后重新分析',
    ),
    'content_policy': (
        '请求被供应商内容安全策略拦截',
        '请检查待分析内容是否包含敏感信息，或改用其他模型',
    ),
    'server_error': (
        '供应商服务暂时不可用',
        '供应商返回服务端错误，请稍后重试；反复出现时可查看供应商状态页',
    ),
    'timeout': (
        '供应商响应超时',
        '模型长时间未返回，请稍后重试或减少单次分析范围',
    ),
    'bad_request': (
        '模型请求被供应商拒绝',
        '请检查模型名称、接口地址和参数配置；如持续失败可在设置中测试连接',
    ),
    'unknown': (
        '模型请求失败',
        '请检查设置中的模型、接口地址与密钥，或稍后重试',
    ),
}

# Providers phrase the same failure in very different payloads: OpenAI uses
# error.type/code, Anthropic nests type/message, Gemini adds status, and
# domestic vendors expose top-level code/msg. Match the combined text instead
# of one fixed JSON shape, then let the HTTP status break ties.
_ERROR_PATTERNS = (
    ('quota_exhausted', (
        'insufficient_quota', 'insufficient quota', 'exceeded your current quota',
        'quota exceeded', 'quota_exceeded', 'quotaexceeded', 'billing_hard_limit',
        'billing hard limit', 'insufficient_balance', 'insufficient balance',
        'balance is too low', 'credit balance', 'no credit', 'payment required',
        'in arrears', 'arrears', 'overdue payment', 'insufficient credits',
        'exhausted your quota', 'quota has been exhausted', 'quota is exhausted',
        'out of credits', 'not enough quota', 'quota limit reached',
        '余额不足', '余额用尽', '余额用完', '余额已用尽', '余额已用完',
        '额度不足', '额度用尽', '额度用完', '额度耗尽', '额度已用尽', '额度已用完',
        '额度已耗尽', '配额不足', '配额用尽', '配额用完', '配额耗尽', '配额已用完',
        '配额超限', '欠费', '账户余额', '可用额度', '资源包已用完',
    )),
    ('context_length', (
        'context_length_exceeded', 'maximum context length', 'context window',
        'prompt is too long', 'input is too long', 'too many tokens',
        'maximum number of tokens', 'token limit exceeded', 'reduce the length',
        'input length exceeds', 'exceeds the maximum', 'string too long',
        '上下文长度', '上下文超长', '超过上下文', '超过最大上下文', '超出最大长度',
        '输入过长', '内容过长', '超过模型上限', 'token 数量超过',
    )),
    ('auth', (
        'invalid_api_key', 'invalid api key', 'incorrect api key', 'api key not valid',
        'api_key_invalid', 'authentication_error', 'authentication failed',
        'invalid authentication', 'unauthenticated', 'unauthorized', 'invalid token',
        'token expired', 'invalid x-api-key', 'invalid apikey',
        '密钥无效', '密钥错误', '密钥不正确', '密钥已失效', 'key无效', 'key 无效',
        'api key无效', '鉴权失败', '认证失败', '认证信息无效', '令牌无效', '令牌已过期',
    )),
    ('model_missing', (
        'model_not_found', 'model not found', 'no such model', 'unknown model',
        'invalid model', 'model does not exist', 'model is not available',
        'model unavailable', 'unsupported model',
        '模型不存在', '模型不可用', '模型名称错误', '不支持的模型', '无效的模型',
    )),
    ('content_policy', (
        'content policy', 'content_policy', 'content filter', 'content_filter',
        'safety system', 'safety policy', 'responsible ai', 'moderation', 'flagged',
        'violates', 'violation', 'inappropriate content', 'unsafe content',
        '内容安全', '安全策略', '内容审核', '违规', '敏感内容', '被拦截',
    )),
    ('rate_limited', (
        'rate limit', 'rate_limit', 'ratelimit', 'too many requests', 'throttl',
        'resource_exhausted', 'resource exhausted', 'requests per minute',
        'tokens per minute', 'request frequency', 'concurrent requests',
        'request limit', 'quota per minute',
        '限流', '频率限制', '频率过高', '请求过于频繁', '访问过于频繁',
        '请求太快', '请求过快', '并发', '每分钟请求', '请求次数已达上限',
        '调用次数超限', '调用频率过高',
    )),
    ('permission', (
        'permission_error', 'permission denied', 'forbidden', 'not authorized',
        'access denied', 'no access to', 'not allowed to use', 'does not have access',
        'not enabled for', 'not supported for this account',
        '无权使用', '没有权限', '未开通', '套餐不支持', '账号不支持',
    )),
    ('timeout', (
        'timed out', 'timeout', 'deadline exceeded', '超时',
    )),
    ('server_error', (
        'internal server error', 'service unavailable', 'server overloaded', 'overloaded',
        'bad gateway', 'upstream error', 'temporarily unavailable',
        '服务不可用', '服务器错误', '服务过载', '内部错误', '上游错误',
    )),
)

_GENERIC_CODES = {
    'error', 'failed', 'failure', 'unknown', 'unknown_error', 'invalid_request_error',
    'invalid request', 'bad request', 'success',
}


def _first_text(value, keys, prefer_text=False):
    items = []
    for key in keys:
        item = value.get(key)
        if isinstance(item, (str, int, float)) and str(item).strip():
            items.append(str(item).strip())
    if prefer_text:
        for item in items:
            if not item.replace('.', '', 1).isdigit():
                return item
    return items[0] if items else ''


def _error_parts(value, depth=0):
    """Extract (code, message) from the error envelopes used by providers."""
    if depth > 4:
        return '', ''
    if isinstance(value, str):
        return '', value.strip()
    if isinstance(value, list):
        for item in value[:8]:
            code, message = _error_parts(item, depth + 1)
            if code or message:
                return code, message
        return '', ''
    if not isinstance(value, dict):
        return '', ''
    for key in ('error', 'errors', 'detail'):
        nested = value.get(key)
        if isinstance(nested, (dict, list, str)):
            code, message = _error_parts(nested, depth + 1)
            if code or message:
                code = code or _first_text(value, ('status', 'type', 'error_type', 'code', 'error_code'), prefer_text=True)
                message = message or _first_text(value, ('message', 'msg', 'error_msg', 'detail', 'error_description', 'reason', 'description'))
                return code, message
    code = _first_text(value, ('status', 'type', 'error_type', 'code', 'error_code'), prefer_text=True)
    message = _first_text(value, ('message', 'msg', 'error_msg', 'detail', 'error_description', 'reason', 'description'))
    if not code and not message:
        for key in ('data', 'response', 'body'):
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                code, message = _error_parts(nested, depth + 1)
                if code or message:
                    return code, message
    return code, message


def _probe_text(value, code='', message='', body=''):
    parts = [str(code or ''), str(message or ''), (body or '')[:4000]]

    def walk(item, depth=0):
        if depth > 3:
            return
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, (int, float)):
            parts.append(str(item))
        elif isinstance(item, dict):
            for nested in item.values():
                walk(nested, depth + 1)
        elif isinstance(item, list):
            for nested in item[:8]:
                walk(nested, depth + 1)

    walk(value)
    return ' '.join(parts).lower()


def _classify_error(status, probe):
    for kind, patterns in _ERROR_PATTERNS:
        if any(pattern in probe for pattern in patterns):
            return kind
    try:
        status = int(status or 0)
    except (TypeError, ValueError):
        status = 0
    if status == 402:
        return 'quota_exhausted'
    if status == 401:
        return 'auth'
    if status == 403:
        return 'permission'
    if status == 404:
        return 'model_missing'
    if status in (408, 504):
        return 'timeout'
    if status == 429:
        return 'rate_limited'
    if status >= 500:
        return 'server_error'
    if status >= 400:
        return 'bad_request'
    return 'unknown'


def _clean_code(code, status=None):
    text = re.sub(r'[^0-9A-Za-z_.\-/: ]', '', str(code or '')).strip()[:48]
    if not text or text.lower() in _GENERIC_CODES:
        return ''
    if status is not None and text == str(status):
        return ''
    return text


def _error_description(kind, status=None, code=''):
    title, hint = _ERROR_KINDS.get(kind, _ERROR_KINDS['unknown'])
    where = f'HTTP {status}' if status else '供应商返回错误'
    code = _clean_code(code, status)
    suffix = f'，错误码 {code}' if code else ''
    return f'模型请求失败（{where}{suffix}）：{title}。{hint}；原任务已保留'


def _read_error_body(response):
    # Streaming responses raise before their body is consumed; read it here so
    # 402/429 bodies are not lost (httpx otherwise reports ResponseNotRead).
    try:
        response.read()
    except Exception:
        pass
    try:
        text = (response.text or '')[:4000]
    except Exception:
        return '', None
    try:
        return text, json.loads(text)
    except (TypeError, ValueError):
        return text, None


def _provider_error_from_response(response):
    status = getattr(response, 'status_code', None)
    text, value = _read_error_body(response)
    if isinstance(value, (dict, list)):
        code, message = _error_parts(value)
        probe = _probe_text(value, code, message, text)
    else:
        code, message, probe = '', '', (text or '').lower()
    return _safe_error(_error_description(_classify_error(status, probe), status, code))


def _looks_like_error(value):
    if not isinstance(value, dict):
        return False
    if value.get('error') or value.get('type') == 'error' or value.get('success') is False:
        return True
    if value.get('choices') or value.get('content') or value.get('candidates'):
        return False
    code = value.get('code')
    return code not in (None, '', 0, 200) and bool(value.get('message') or value.get('msg'))


def _payload_status(value):
    if not isinstance(value, dict):
        return 0
    containers = [value]
    if isinstance(value.get('error'), dict):
        containers.append(value['error'])
    for container in containers:
        for key in ('code', 'status'):
            raw = container.get(key)
            try:
                code = int(raw)
            except (TypeError, ValueError):
                continue
            if 400 <= code < 600:
                return code
    return 0


def _provider_error_from_payload(value):
    code, message = _error_parts(value)
    probe = _probe_text(value, code, message)
    return _safe_error(_error_description(_classify_error(_payload_status(value), probe), None, code))


def _stream_payload_error(value):
    if not isinstance(value, dict):
        return None
    if not (value.get('type') == 'error' or value.get('error') is not None):
        if value.get('choices') or value.get('content') or value.get('candidates'):
            return None
        if value.get('code') in (None, '', 0, 200) or not (value.get('message') or value.get('msg')):
            return None
    code, message = _error_parts(value)
    probe = _probe_text(value, code, message)
    kind = _classify_error(_payload_status(value), probe)
    if kind == 'unknown':
        # Generic upstream text can contain user data or request ids; keep the
        # established non-echoing message unless the failure is recognizable.
        return _safe_error('模型流式响应发生错误；原任务已保留，不会自动重复请求')
    return _safe_error(_error_description(kind, None, code))


def _decode_response(result):
    try:
        value = result.json()
    except (TypeError, ValueError):
        status = getattr(result, 'status_code', None)
        try:
            text = (result.text or '')[:4000]
        except Exception:
            text = ''
        kind = _classify_error(status, text.lower())
        if kind != 'unknown':
            raise _safe_error(_error_description(kind, status)) from None
        raise _safe_error('模型接口返回了非 JSON 内容，请检查接口地址、代理或供应商状态；原任务已保留') from None
    if _looks_like_error(value):
        raise _provider_error_from_payload(value)
    return value


def complete(settings, system, text, *, max_tokens=12000, purpose='', require_text=True, key=None):
    c = connection(settings) if key is None else connection(settings, key)
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
    if current.get() is not None and purpose in STREAM_PURPOSES:
        body['stream'] = True
    started = monotonic()
    if body['stream'] and current.get() is not None:
        begin_stream(c['model'], _estimate_tokens(system) + _estimate_tokens(text))
    emit(f'请求模型 {c["model"]}，输入 {len(system) + len(text)} 字符，等待响应')
    # Slow reasoning providers may not emit a non-stream response for a while. No automatic paid retry.
    try:
        with httpx.Client(timeout=httpx.Timeout(300, connect=15), follow_redirects=False, trust_env=False) as client:
            if body['stream'] and hasattr(client, 'stream'):
                with client.stream('POST', c['endpoint'], headers=headers, json=body) as result:
                    result.raise_for_status()
                    emit(f'模型已响应（{monotonic() - started:.1f} 秒），正在接收内容')
                    if result.headers.get('content-type','').startswith('text/event-stream'):
                        answer,usage=stream_text(result.iter_lines(),c['protocol'],purpose,max_tokens)
                        emit(f'本次模型请求完成，耗时 {monotonic() - started:.1f} 秒')
                        _record_usage(purpose,c['model'],system,text,answer,usage)
                        return answer
                    result.read()
                    value = _decode_response(result)
            else:
                result = client.post(c['endpoint'], headers=headers, json=body)
                result.raise_for_status()
                if body['stream'] and result.headers.get('content-type','').startswith('text/event-stream'):
                    answer,usage=stream_text(result.iter_lines(),c['protocol'],purpose,max_tokens)
                    emit(f'本次模型请求完成，耗时 {monotonic() - started:.1f} 秒')
                    _record_usage(purpose,c['model'],system,text,answer,usage)
                    return answer
                value = _decode_response(result)
        if c['protocol'] == 'anthropic':
            if require_text and value.get('stop_reason') == 'max_tokens':
                raise truncated(purpose,max_tokens)
            answer = '\n'.join(block['text'] for block in value.get('content', []) if block.get('type') == 'text')
            reported = value.get('usage') or {}
            usage = {'prompt':reported.get('input_tokens'),'completion':reported.get('output_tokens')}
        else:
            if require_text and value['choices'][0].get('finish_reason') == 'length':
                raise truncated(purpose,max_tokens)
            answer = value['choices'][0]['message'].get('content') or ''
            reported = value.get('usage') or {}
            usage = {'prompt':reported.get('prompt_tokens'),'completion':reported.get('completion_tokens')}
        if require_text and (not isinstance(answer, str) or not answer.strip()):
            raise _safe_error('模型未返回可用文本；原任务已保留')
        if body['stream'] and current.get() is not None:
            stream_delta(output=answer)
            stream_usage(prompt=usage.get('prompt'), completion=usage.get('completion'))
        emit(f'本次模型请求完成，耗时 {monotonic() - started:.1f} 秒，返回 {len(answer)} 字符')
        _record_usage(purpose,c['model'],system,text,answer,usage)
        return answer
    except httpx.HTTPStatusError as exc:
        raise _provider_error_from_response(exc.response) from None
    except httpx.TimeoutException:
        raise _safe_error('模型响应超时，供应商长时间未返回；原任务已保留，可稍后重试') from None
    except httpx.HTTPError:
        raise _safe_error('无法连接模型服务，网络不可用、代理异常或连接被中断；原任务已保留，可稍后重试') from None
    except (AttributeError, KeyError, IndexError, TypeError, ValueError) as exc:
        if isinstance(exc, ModelError):
            raise
        raise _safe_error('模型响应格式不兼容；原任务已保留') from None


def test_connection(settings, key=None):
    complete(settings, 'Reply with OK only. Do not use tools.', 'Connection test. Reply OK.', max_tokens=128, require_text=False, key=key)
    return {**status(settings, key), 'ok': True, 'message': '已完成真实模型请求，连接成功；未发送工作对话。'}
