import { useEffect, useState } from "react";
import { LockKeyhole, PlugZap } from "lucide-react";
import type { Settings } from "./types";
type Status = {
  configured: boolean;
  provider: string;
  endpoint: string;
  model: string;
  error: string;
};
const presets = {
  deepseek: {
    endpoint: "https://api.deepseek.com/chat/completions",
    model: "deepseek-flash",
    protocol: "openai" as const,
  },
  glm: {
    endpoint: "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model: "glm-5.3",
    protocol: "openai" as const,
  },
  ccswitch: {
    endpoint: "https://api.anthropic.com/v1/messages",
    model: "",
    protocol: "anthropic" as const,
  },
  custom: { endpoint: "", model: "", protocol: "openai" as const },
};
export function ModelSettings({
  settings: s,
  onChange,
  apiKey,
  onKeyChange,
  busy,
  saved,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
  apiKey: string;
  onKeyChange: (s: string) => void;
  busy: boolean;
  saved: Settings;
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
          <option value="glm">智谱 GLM API</option>
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
          <div className="form-grid">
            <label>
              模型名称
              <input
                required={s.llm_enabled}
                value={s.llm_model}
                onChange={(e) => onChange({ ...s, llm_model: e.target.value })}
              />
            </label>
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
          </div>
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
        >
          保存并测试连接
        </button>
      </div>
      <p className="muted">
        测试只发送简短探测消息，不发送工作对话。正式分析会发送所选日期脱敏后的用户消息、AI
        回复和工具类型/验证状态；不发送原始工具输出、原始路径引用、环境变量或密钥。
      </p>
    </>
  );
}
