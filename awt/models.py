from datetime import date as Date, datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class SourceConfig(BaseModel):
    enabled: bool = True
    paths: list[str] = Field(default_factory=list)


CONVERSATION_SOURCES = ('codex', 'claude_code', 'workbuddy_cn', 'workbuddy_intl',
                        'deepseek_harness', 'qoder_cn', 'qoder_intl', 'trae_cn', 'trae_intl')

# 旧版把国内外版合并成一个来源；读取旧设置时按路径拆到两个新来源。
SOURCE_FAMILIES = {
    'workbuddy': ('workbuddy_cn', 'workbuddy_intl'),
    'qoder': ('qoder_cn', 'qoder_intl'),
    'trae': ('trae_cn', 'trae_intl'),
}


def _source(path):
    return SourceConfig(paths=[str(path)])


def _workbuddy_cn_source():
    return _source(Path.home() / ".workbuddy" / "projects")


def _workbuddy_intl_source():
    return _source(Path.home() / ".workbuddy-ai" / "projects")


def _deepseek_harness_source():
    return _source(Path.home() / ".dsh" / "sessions")


def _qoder_cn_source():
    return _source(Path.home() / ".qoder-cn" / "projects")


def _qoder_intl_source():
    return _source(Path.home() / ".qoder" / "projects")


def _trae_cn_source():
    # Trae 完整会话库加密，这里读取它落盘的会话摘要。
    return _source(Path.home() / ".trae-cn" / "memory" / "projects")


def _trae_intl_source():
    return _source(Path.home() / ".trae" / "memory" / "projects")


def _is_cn_path(family, path):
    normalized = path.replace('\\', '/')
    if family == 'workbuddy':
        return '.workbuddy-ai' not in normalized
    return f'.{family}-cn' in normalized


class GiteeConfig(BaseModel):
    enabled: bool = False
    interval_hours: int = Field(default=24, ge=1, le=168)
    filter: Literal['assigned', 'created', 'all'] = 'all'
    repository: str = Field(default='', max_length=200, pattern=r'^(?:[\w.-]+/[\w.-]+)?$')
    since: Date = Date(2026, 9, 15)


class GiteeProjectRule(BaseModel):
    pattern: str = Field(min_length=1, max_length=500)
    mode: Literal['prefix', 'contains', 'exact'] = 'prefix'
    program_id: int = Field(ge=1, strict=True)

    @field_validator('pattern')
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError('映射规则不能为空')
        return value.strip()

    @model_validator(mode='after')
    def valid_prefix(self):
        if self.mode=='prefix' and not any(c.isalnum() for c in self.pattern):
            raise ValueError('项目前缀需包含文字或数字')
        return self


class GiteeEnterpriseConfig(BaseModel):
    enterprise_id: int = Field(default=0, ge=0, strict=True)
    rules: list[GiteeProjectRule] = Field(default_factory=list, max_length=100)
    include_details: bool = True


class GiteePreviewInput(BaseModel):
    date: Date
    task_ids: list[str] = Field(default_factory=list, max_length=200)


class GiteeExportTarget(BaseModel):
    task_id: str
    program_id: int = Field(ge=1, strict=True)
    issue_type_id: int | None = Field(default=None, ge=1, strict=True)


class GiteePublishInput(BaseModel):
    batch_id: str
    confirmed: Literal[True]
    targets: list[GiteeExportTarget] = Field(min_length=1, max_length=200)


class GiteeResolveInput(BaseModel):
    enterprise_id: int = Field(ge=1, strict=True)
    task_id: str
    issue_id: int = Field(ge=1, strict=True)


class GitAuthor(BaseModel):
    name: str = Field(default='', max_length=200)
    email: str = Field(default='', max_length=320)

    @model_validator(mode='after')
    def valid_identity(self):
        self.name=self.name.strip();self.email=self.email.strip()
        if not (self.name or self.email) or any(ord(c)<32 for c in self.name+self.email):
            raise ValueError('Git用户需填写姓名或邮箱，且不能包含控制字符')
        return self


class SourceCheckInput(BaseModel):
    sources: dict[str, list[str]] = Field(default_factory=dict)

    @field_validator('sources')
    @classmethod
    def valid_sources(cls, values):
        if any(key not in CONVERSATION_SOURCES for key in values):
            raise ValueError('未知的对话来源')
        for paths in values.values():
            if len(paths) > 50:
                raise ValueError('单个来源的路径不能超过 50 条')
            if any(not p.strip() or len(p) > 4096 or any(ord(c) < 32 for c in p) for p in paths):
                raise ValueError('路径为空、过长或包含控制字符')
        return values


class GitScanInput(BaseModel):
    root: str = Field(min_length=1, max_length=4096)

    @field_validator('root')
    @classmethod
    def valid_root(cls, value):
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError('请选择正确git项目目录')
        return value


class GitIdentityQuery(BaseModel):
    roots: list[str] = Field(default_factory=list,max_length=50)

    @field_validator('roots')
    @classmethod
    def valid_roots(cls,values):
        if any(len(v)>4096 or any(ord(c)<32 for c in v) for v in values):
            raise ValueError('Git仓库路径过长或包含控制字符')
        return values


class GitVerifyQuery(GitIdentityQuery):
    author: GitAuthor | None = None


class Settings(BaseModel):
    timezone: str = "Asia/Shanghai"
    target_minutes: int = Field(default=480, ge=1, le=1440)
    step_minutes: int = Field(default=15, ge=1, le=120)
    claude_code: SourceConfig = Field(default_factory=SourceConfig)
    codex: SourceConfig = Field(default_factory=SourceConfig)
    workbuddy_cn: SourceConfig = Field(default_factory=_workbuddy_cn_source)
    workbuddy_intl: SourceConfig = Field(default_factory=_workbuddy_intl_source)
    deepseek_harness: SourceConfig = Field(default_factory=_deepseek_harness_source)
    qoder_cn: SourceConfig = Field(default_factory=_qoder_cn_source)
    qoder_intl: SourceConfig = Field(default_factory=_qoder_intl_source)
    trae_cn: SourceConfig = Field(default_factory=_trae_cn_source)
    trae_intl: SourceConfig = Field(default_factory=_trae_intl_source)
    git_roots: list[str] = Field(default_factory=list)
    git_author: GitAuthor | None = None
    git_author_overrides: dict[str,GitAuthor] = Field(default_factory=dict,max_length=100)
    project_mappings: dict[str, str] = Field(default_factory=dict)
    excluded_paths: list[str] = Field(default_factory=list)
    redact_patterns: list[str] = Field(default_factory=list)
    llm_enabled: bool = False
    llm_endpoint: str = "https://api.openai.com/v1/chat/completions"
    llm_model: str = ""
    llm_key_env: str = "AWT_LLM_API_KEY"
    llm_provider: Literal['deepseek', 'glm', 'glm-coding', 'ccswitch', 'custom'] = 'custom'
    llm_protocol: Literal['openai', 'anthropic'] = 'openai'
    ccswitch_config_path: str = ''
    # 默认只生成候选，用户在“分析结果”中确认后再写入今日工作清单。
    auto_import_analysis: bool = False
    analysis_sources: list[Literal['codex', 'claude_code', 'workbuddy_cn', 'workbuddy_intl', 'deepseek_harness', 'qoder_cn', 'qoder_intl', 'trae_cn', 'trae_intl', 'git']] = Field(default_factory=lambda: ['codex'])
    agent_prompts: dict[str, str] = Field(default_factory=dict)
    title_examples: list[str] = Field(default_factory=list, max_length=80)
    history_reference: dict = Field(default_factory=dict)
    style_template: str = Field(default='', max_length=12000)
    gitee: GiteeConfig = Field(default_factory=GiteeConfig)
    gitee_enterprise: GiteeEnterpriseConfig = Field(default_factory=GiteeEnterpriseConfig)
    activity_gap_minutes: int = Field(default=5, ge=1, le=30)

    @model_validator(mode='before')
    @classmethod
    def migrate_legacy_sources(cls, data):
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for legacy, (cn_key, intl_key) in SOURCE_FAMILIES.items():
            if legacy not in data:
                continue
            old = data.pop(legacy) or {}
            paths = [p for p in old.get('paths', []) if isinstance(p, str) and p.strip()]
            enabled = bool(old.get('enabled', True))
            data.setdefault(cn_key, {
                'enabled': enabled,
                'paths': [p for p in paths if _is_cn_path(legacy, p)],
            })
            data.setdefault(intl_key, {
                'enabled': enabled,
                'paths': [p for p in paths if not _is_cn_path(legacy, p)],
            })
        sources = data.get('analysis_sources')
        if isinstance(sources, list):
            mapped = []
            for value in sources:
                if value in SOURCE_FAMILIES:
                    mapped.extend(SOURCE_FAMILIES[value])
                else:
                    mapped.append(value)
            data['analysis_sources'] = list(dict.fromkeys(mapped))
        return data

    @field_validator('git_author_overrides')
    @classmethod
    def valid_git_overrides(cls,values):
        if any(not v.strip() or len(v)>4096 or any(ord(c)<32 for c in v) for v in values):
            raise ValueError('Git用户覆盖的仓库路径无效')
        return values

    @field_validator('agent_prompts')
    @classmethod
    def valid_prompts(cls, values):
        if any(k not in ('scope_filter','task_writer','time_estimator','manual_writer','style_template') or len(v)>16000 for k,v in values.items()):
            raise ValueError('提示词角色无效，或超过16000字符')
        return values

    @field_validator('title_examples')
    @classmethod
    def valid_examples(cls, values):
        if any(len(v)>500 for v in values):
            raise ValueError('标题范例不能超过500字符')
        return values

    @field_validator('analysis_sources')
    @classmethod
    def valid_sources(cls, values):
        # 允许全部取消：此时不分析、不采集新对话，由界面提示用户。
        return list(dict.fromkeys(values))

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("无效时区")
        return value

    @field_validator("redact_patterns")
    @classmethod
    def valid_patterns(cls, values):
        import re
        for value in values:
            try:
                re.compile(value)
            except re.error:
                raise ValueError("脱敏正则表达式无效")
        return values

    @field_validator("llm_endpoint")
    @classmethod
    def valid_endpoint(cls, value):
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.username or parsed.password or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError("模型地址不能包含凭据、查询参数或片段")
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1", "::1")):
            raise ValueError("远程模型必须使用 HTTPS")
        return value


class TaskInput(BaseModel):
    date: Date
    title: str = Field(min_length=1, max_length=500)
    details: str = Field(default="", max_length=20000)
    project: str = Field(default="未归属项目", max_length=500)
    category: str = Field(default="其他", min_length=1, max_length=100)
    status: Literal["requested", "attempted", "changed", "verified", "delivered"] = "requested"
    confirmed_minutes: int = Field(default=0, ge=0, le=1440, strict=True)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    time_locked: bool | None = None

    @field_validator("title")
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError("标题不能为空")
        return value.strip()

    @model_validator(mode="after")
    def interval(self):
        if bool(self.started_at) != bool(self.ended_at):
            raise ValueError("开始和结束时间需同时填写")
        if self.started_at:
            if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:
                raise ValueError("时间需要包含时区")
            if self.ended_at <= self.started_at:
                raise ValueError("结束时间必须晚于开始时间")
        return self


class MergeInput(BaseModel):
    ids: list[str] = Field(min_length=2, max_length=100)


class TaskPatch(BaseModel):
    date: Date | None = None
    title: str | None = Field(default=None, min_length=1, max_length=500)
    details: str | None = Field(default=None, max_length=20000)
    project: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["requested", "attempted", "changed", "verified", "delivered"] | None = None
    confirmed_minutes: int | None = Field(default=None, ge=0, le=1440, strict=True)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    time_locked: bool | None = None


class TaskFromEventsInput(BaseModel):
    date: Date
    event_ids: list[str] = Field(min_length=1, max_length=200)


class AnalysisImportItem(BaseModel):
    fingerprint: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    details: str | None = Field(default=None, max_length=20000)
    project: str | None = Field(default=None, max_length=500)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    status: Literal["requested", "attempted", "changed", "verified", "delivered"] | None = None


class AnalysisImportInput(BaseModel):
    date: Date
    items: list[AnalysisImportItem] = Field(min_length=1, max_length=100)

    @field_validator('items')
    @classmethod
    def unique_items(cls, values):
        fingerprints = [item.fingerprint for item in values]
        if len(set(fingerprints)) != len(fingerprints):
            raise ValueError('同一分析结果不能重复导入')
        return values


class SplitInput(BaseModel):
    id: str
    parts: list[TaskInput] = Field(min_length=2, max_length=20)


class ModelKeyInput(BaseModel):
    api_key: SecretStr = Field(min_length=1, max_length=8192)


class ModelProbeInput(BaseModel):
    """Optional unsaved candidate, so a failed test writes nothing to the store."""
    settings: Settings | None = None
    api_key: SecretStr | None = None


class StyleImportInput(BaseModel):
    path: str = ''
    filename: str = ''
    content_base64: str | None = Field(default=None, max_length=28_000_000)
    since: Date = Date(2026, 9, 15)
    include_git: bool = True


class QuickItem(BaseModel):
    category: Literal['运维', '沟通']
    name: str = Field(min_length=1, max_length=200)
    minutes: int = Field(ge=1, le=1440, strict=True)

    @field_validator('name')
    @classmethod
    def non_blank(cls, value):
        if not value.strip():
            raise ValueError('人工事项名称不能为空')
        return value.strip()


class AllocationInput(BaseModel):
    request_id: str = Field(default='', max_length=100, pattern=r'^[\w-]*$')
    manual_items: list[QuickItem] = Field(default_factory=list, max_length=20)

    @model_validator(mode='after')
    def require_id(self):
        if self.manual_items and not self.request_id:
            raise ValueError('添加人工事项需要请求编号')
        return self
