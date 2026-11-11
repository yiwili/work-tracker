import json

import httpx
import pytest

from awt import model_provider
from awt.models import Settings


def _complete(monkeypatch, status, body, *, model='test-model'):
    base = httpx.Client
    monkeypatch.setattr(
        model_provider,
        'connection',
        lambda settings, *args: {
            'model': model,
            'protocol': 'openai',
            'endpoint': 'http://localhost/test',
            'auth': 'api_key',
            'key': 'fixture-token',
        },
    )

    def respond(request):
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=body)

    monkeypatch.setattr(
        model_provider.httpx,
        'Client',
        lambda **kwargs: base(transport=httpx.MockTransport(respond), **kwargs),
    )
    return model_provider.complete(Settings(), 'rules', 'input')


@pytest.mark.parametrize(
    ('status', 'body', 'expected'),
    [
        (402, {'error': {'message': 'Insufficient Balance', 'type': 'unknown_error',
                         'param': None, 'code': 'invalid_request_error'}},
         ['HTTP 402', '额度或余额不足']),
        (429, {'error': {'message': 'You exceeded your current quota, please check your plan and billing details.',
                         'type': 'insufficient_quota', 'code': 'insufficient_quota'}},
         ['HTTP 429', '额度或余额不足', 'insufficient_quota']),
        (429, {'error': {'message': 'Rate limit reached for requests',
                         'type': 'rate_limit_error', 'code': 'rate_limit_exceeded'}},
         ['请求触发供应商限流']),
        (401, {'error': {'message': 'Incorrect API key provided',
                         'type': 'invalid_request_error', 'code': 'invalid_api_key'}},
         ['API Key 无效或已过期']),
        (400, {'error': {'message': "This model's maximum context length is 8192 tokens",
                         'type': 'invalid_request_error', 'code': 'context_length_exceeded'}},
         ['输入内容超过模型上下文上限']),
        (404, {'error': {'message': 'The model `missing` does not exist',
                         'type': 'invalid_request_error', 'code': 'model_not_found'}},
         ['模型名称或接口地址不存在']),
        (400, {'error': {'message': 'Your request was rejected by the safety system',
                         'type': 'invalid_request_error', 'code': 'content_policy_violation'}},
         ['内容安全策略']),
        (503, '<html><body>Service Unavailable</body></html>', ['服务暂时不可用']),
        (400, {'error': {'message': 'messages[0].role is invalid'}}, ['请求被供应商拒绝']),
        (402, {'error': {'message': 'request rejected'}}, ['额度或余额不足']),
        (401, {'error': {'message': 'request rejected'}}, ['API Key 无效']),
        (403, {'error': {'message': 'request rejected'}}, ['无权使用']),
        (404, {'error': {'message': 'request rejected'}}, ['模型名称']),
        (408, {'error': {'message': 'request rejected'}}, ['响应超时']),
        (500, {'error': {'message': 'request rejected'}}, ['服务暂时不可用']),
    ],
)
def test_http_error_bodies_are_explained(monkeypatch, status, body, expected):
    with pytest.raises(model_provider.ModelError) as exc:
        _complete(monkeypatch, status, body)
    message = str(exc.value)
    assert '原任务已保留' in message
    for text in expected:
        assert text in message


@pytest.mark.parametrize(
    ('status', 'body', 'expected'),
    [
        (429, {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED',
                         'message': 'You exceeded your current quota, please check your plan and billing details.'}},
         '额度或余额不足'),
        (429, {'error': {'code': 429, 'status': 'RESOURCE_EXHAUSTED',
                         'message': 'Resource exhausted. Please try again later.'}},
         '请求触发供应商限流'),
        (429, {'error': {'code': '1113', 'message': '余额不足或无可用资源包,请充值。'}},
         '额度或余额不足'),
        (429, {'error': {'code': '1302', 'message': '当前并发量过高，请稍后重试'}},
         '请求触发供应商限流'),
        (429, {'code': 'Throttling.RateQuota',
               'message': 'Requests rate limit exceeded, please try again later.',
               'request_id': 'fixture'},
         '请求触发供应商限流'),
        (429, {'code': 'Throttling.AllocationQuota',
               'message': 'Allocated quota exceeded, please increase your quota limit.'},
         '额度或余额不足'),
        (429, {'error': {'type': 'rate_limit_reached_error', 'message': 'Too many requests'}},
         '请求触发供应商限流'),
        (429, {'error': {'code': 'QuotaExceeded', 'message': 'quota exceeded'}},
         '额度或余额不足'),
        (429, {'error_code': 17, 'error_msg': 'Open api daily request limit reached'},
         '请求触发供应商限流'),
        (400, {'error': {'message': '您的额度已用完，请充值'}}, '额度或余额不足'),
        (429, {'error': {'message': '调用次数超限，请稍后再试'}}, '请求触发供应商限流'),
        (529, {'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}},
         '服务暂时不可用'),
    ],
)
def test_vendor_specific_shapes_are_normalized(monkeypatch, status, body, expected):
    with pytest.raises(model_provider.ModelError) as exc:
        _complete(monkeypatch, status, body)
    assert expected in str(exc.value)


def test_http_200_error_body_is_not_treated_as_success(monkeypatch):
    body = {'error': {'message': '余额不足', 'type': 'insufficient_quota'}}
    with pytest.raises(model_provider.ModelError) as exc:
        _complete(monkeypatch, 200, body)
    message = str(exc.value)
    assert '供应商返回错误' in message and '额度或余额不足' in message


def test_streaming_status_error_keeps_provider_reason(monkeypatch):
    with pytest.raises(model_provider.ModelError) as exc:
        _complete(
            monkeypatch,
            402,
            {'error': {'message': 'Insufficient Balance', 'type': 'unknown_error'}},
            model='glm-5.3',
        )
    message = str(exc.value)
    assert 'HTTP 402' in message and '额度或余额不足' in message


def test_non_json_interface_response_is_explained(monkeypatch):
    with pytest.raises(model_provider.ModelError) as exc:
        _complete(monkeypatch, 200, '<html>Welcome to nginx</html>')
    assert '非 JSON' in str(exc.value)


@pytest.mark.parametrize(
    ('payload', 'expected'),
    [
        ({'error': {'code': '1302', 'message': '当前并发量过高'}}, '请求触发供应商限流'),
        ({'error': {'message': 'insufficient_quota', 'type': 'insufficient_quota'}}, '额度或余额不足'),
        ({'type': 'error', 'error': {'type': 'overloaded_error', 'message': 'Overloaded'}}, '服务暂时不可用'),
    ],
)
def test_stream_error_events_are_classified(payload, expected):
    lines = ['data: ' + json.dumps(payload, ensure_ascii=False), '']
    with pytest.raises(model_provider.ModelError) as exc:
        model_provider.stream_text(iter(lines), 'openai', 'task_writer', 1000)
    assert expected in str(exc.value)


def test_plain_text_stream_error_is_classified():
    lines = ['data: 您的额度已用完，请充值', '']
    with pytest.raises(model_provider.ModelError) as exc:
        model_provider.stream_text(iter(lines), 'openai', 'task_writer', 1000)
    assert '额度或余额不足' in str(exc.value)


def test_unknown_stream_error_does_not_echo_upstream_text():
    lines = ['data: ' + json.dumps({'error': {'message': 'must not echo upstream body'}}), '']
    with pytest.raises(model_provider.ModelError) as exc:
        model_provider.stream_text(iter(lines), 'openai', 'task_writer', 1000)
    message = str(exc.value)
    assert '原任务已保留' in message and 'must not echo' not in message