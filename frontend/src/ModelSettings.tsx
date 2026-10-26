import { useEffect, useState } from "react";
import { LockKeyhole, PlugZap, LoaderCircle } from "lucide-react";
import type { Settings } from "./types";
type Status = {
  configured: boolean;
  provider: string;
  endpoint: string;
  model: string;
  error: string;
};
const glmModels = [
  { id: "glm-5.3-flash", label: "GLM-5.3-Flash" },
  { id: "glm-5.3-flashx", label: "GLM-5.3-FlashX" },
  { id: "glm-5.3", label: "GLM-5.3" },
  { id: "glm-5.2", label: "GLM-5.2" },
  { id: "glm-5.1", label: "GLM-5.1" },
  { id: "glm-5", label: "GLM-5" },
  { id: "glm-5-turbo", label: "GLM-5-Turbo" },
  { id: "glm-4.7", label: "GLM-4.7" },
  { id: "glm-4.6", label: "GLM-4.6" },
  { id: "glm-4.5", label: "GLM-4.5" },
  { id: "glm-4.5-air", label: "GLM-4.5-Air" },
  { id: "glm-4.5-flash", label: "GLM-4.5-Flash（免费）" },
];
export const presets: Record<
  Settings["llm_provider"],
  {
    endpoint: string;
    model: string;
    protocol: Settings["llm_protocol"];
    models: { id: string; label: string }[];
  }
> = {
  deepseek: {
    endpoint: "https://api.deepseek.com/chat/completions",
    model: "deepseek-chat",
    protocol: "openai" as const,
    models: [
      { id: "deepseek-chat", label: "DeepSeek Chat" },
      { id: "deepseek-reasoner", label: "DeepSeek Reasoner" },
    ],
  },
  glm: {
    endpoint: "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model: "glm-5.3-flash",
    protocol: "openai" as const,
    models: glmModels,
  },
  "glm-coding": {
    endpoint: "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
    model: "glm-5.3-flash",
    protocol: "openai" as const,
    models: glmModels.filter((item) => item.id !== "glm-4.5-flash"),
  },
  ccswitch: {
    endpoint: "https://api.anthropic.com/v1/messages",
    model: "",
    protocol: "anthropic" as const,
    models: [],
  },
  custom: {
    endpoint: "",
    model: "",
    protocol: "openai" as const,
    models: [],
  },
};

export function ModelSelect({
  provider,
  value,
  onChange,
  label = "模型名称",
}: {
  provider: Settings["llm_provider"];
  value: string;
  onChange: (model: string) => void;
  label?: string;
}) {
  const models = presets[provider].models;
  const matched = models.find(
    (item) => item.id.toLowerCase() === value.trim().toLowerCase(),
  );
  const [custom, setCustom] = useState(!matched);
  if (!models.length)
    return (
      <label>
        {label}
        <input
          required
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    );
  return (
    <>
      <label>
        {label}
        <select
          value={custom ? "__custom__" : (matched?.id ?? value)}
          onChange={(event) => {
            const next = event.target.value;
            if (next === "__custom__") {
              setCustom(true);
              onChange("");
            } else {
              setCustom(false);
              onChange(next);
            }
          }}
        >
          {models.map((item) => (
            <option key={item.id} value={item.id}>
              {item.label}
            </option>
          ))}
          <option value="__custom__">自定义模型名…</option>
        </select>
      </label>
      {custom && (
        <label>
          自定义模型 id
          <input
            required
            value={value}
            onChange={(event) => onChange(event.target.value)}
            placeholder="填写服务商文档中的模型 id"
          />
        </label>
      )}
    </>
  );
}
export function ModelSettings({
  settings: s,
  onChange,
  apiKey,
  onKeyChange,
  busy,
  saved,
  feedback,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
  apiKey: string;
  onKeyChange: (s: string) => void;
  busy: boolean;
  saved: Settings;
  feedback: {
    state: "running" | "success" | "error";
    message: string;
    test?: boolean;
  } | null;
}) {
  const [status, setStatus] = useState<Status | null>(null);
  useEffect(() => {
    fetch("/api/model/status")
      .then((r) => r.json())
      .then(setStatus)
      .catch(() => setStatus(null));
  }, [saved]);
  return (
    <>
      <label>
        AI 分析供应商
        <select
          value={s.llm_provider}
          onChange={(e) => {
            const provider = e.target.value as Settings["llm_provider"];
            onChange({
              ...s,
              llm_provider: provider,
              llm_endpoint: presets[provider].endpoint || s.llm_endpoint,
              llm_model: presets[provider].model,
              llm_protocol: presets[provider].protocol,
            });
            onKeyChange("");
          }}
        >
          <option value="deepseek">DeepSeek API</option>
          <option value="glm">智谱 GLM（标准 API）</option>
          <option value="glm-coding">智谱 GLM Coding Plan</option>
          <option value="ccswitch">CCSwitch / Claude Code 当前接口</option>
          <option value="custom">自定义兼容接口</option>
        </select>
      </label>
      {s.llm_provider === "ccswitch" ? (
        <>
          <div className="privacy-info">
            <PlugZap size={20} />
            <p>
              只读复用 CCSwitch 写入的 Claude Code 当前 API
              配置。支持直连接口或本地代理，每次请求读取最新地址和凭据；不会更改
              CCSwitch。官方订阅登录凭据不会自动导入。
            </p>
          </div>
          <label>
            Claude Code settings.json 路径（留空自动检测）
            <input
              value={s.ccswitch_config_path}
              onChange={(e) =>
                onChange({ ...s, ccswitch_config_path: e.target.value })
              }
              placeholder="默认 ~/.claude/settings.json"
            />
          </label>
          <label>
            覆盖模型名（留空跟随 CCSwitch）
            <input
              value={s.llm_model}
              onChange={(e) => onChange({ ...s, llm_model: e.target.value })}
            />
          </label>
        </>
      ) : (
        <>
          <label>
            完整 API 接口 URL
            <input
              required={s.llm_enabled}
              type="url"
              value={s.llm_endpoint}
              onChange={(e) => onChange({ ...s, llm_endpoint: e.target.value })}
            />
          </label>
          <ModelSelect
            key={s.llm_provider}
            provider={s.llm_provider}
            value={s.llm_model}
            onChange={(model) => onChange({ ...s, llm_model: model })}
          />
          {s.llm_provider === "glm" && (
            <p className="muted">
              GLM-5.x 系列需要账户有可用资源包；余额不足时可选免费的
              GLM-4.5-Flash。
            </p>
          )}
          <label>
            接口格式
            <select
              value={s.llm_protocol}
              onChange={(e) =>
                onChange({
                  ...s,
                  llm_protocol: e.target.value as Settings["llm_protocol"],
                })
              }
            >
              <option value="openai">Chat Completions</option>
              <option value="anthropic">Anthropic Messages</option>
            </select>
          </label>
          <label>
            API Key（留空保留已保存的密钥）
            <input
              type="password"
              autoComplete="new-password"
              value={apiKey}
              onChange={(e) => onKeyChange(e.target.value)}
              placeholder="输入 DeepSeek / GLM API Key"
            />
          </label>
          <label>
            也可使用服务端 API Key 环境变量
            <input
              value={s.llm_key_env}
              onChange={(e) => onChange({ ...s, llm_key_env: e.target.value })}
            />
          </label>
          <p className="muted">
            网页密钥用 Windows DPAPI
            加密保存在本机，不写入设置数据库、不回显。切换地址后需为新地址配置密钥。
          </p>
        </>
      )}
      <div className="model-status">
        <LockKeyhole size={16} />
        <div>
          <strong>
            {status?.provider === s.llm_provider
              ? status.configured
                ? "已检测到连接配置"
                : "连接配置未就绪"
              : "请保存新连接配置"}
          </strong>
          {status?.provider === s.llm_provider && (
            <p>
              {status.configured
                ? `${status.model} · ${status.endpoint}`
                : status.error}
            </p>
          )}
        </div>
        <button
          type="submit"
          name="test_connection"
          className="outline"
          disabled={busy}
          aria-busy={feedback?.state === "running" && feedback.test}
        >
          {feedback?.state === "running" && feedback.test && (
            <LoaderCircle size={16} className="spin" />
          )}
          {feedback?.state === "running" && feedback.test
            ? "正在保存与测试…"
            : "保存并测试连接"}
        </button>
      </div>
      {feedback && (
        <div
          className={
            "alert model-test-feedback" +
            (feedback.state === "error" ? " error" : "")
          }
          role={feedback.state === "error" ? "alert" : "status"}
          aria-label="模型连接反馈"
          aria-live={feedback.state === "error" ? "assertive" : "polite"}
        >
          {feedback.message}
        </div>
      )}
      <p className="muted">
        测试只发送简短探测消息，不发送工作对话。正式分析会发送所选日期脱敏后的用户消息、AI
        回复和工具类型/验证状态；不发送原始工具输出、原始路径引用、环境变量或密钥。
      </p>
    </>
  );
}
