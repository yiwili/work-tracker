import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  KeyRound,
  LoaderCircle,
  Pencil,
  Plus,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import type {
  LlmProvider,
  ModelConnection,
  ModelConnectionState,
  ModelStatus,
  Settings,
} from "./types";
import { api } from "./api";
import { modelLabel, presets, providerLabels } from "./modelPresets";

type Draft = {
  id: string;
  label: string;
  provider: LlmProvider | "";
  endpoint: string;
  protocol: "openai" | "anthropic";
  key_env: string;
  ccswitch_config_path: string;
  model: string;
};

type Feedback = { state: "success" | "error"; message: string } | null;

const blankDraft = (): Draft => ({
  id: "",
  label: "",
  provider: "",
  endpoint: "",
  protocol: "openai",
  key_env: "AWT_LLM_API_KEY",
  ccswitch_config_path: "",
  model: "",
});

const draftFrom = (conn: ModelConnection): Draft => ({
  id: conn.id,
  label: conn.label,
  provider: conn.provider,
  endpoint: conn.endpoint,
  protocol: conn.protocol,
  key_env: conn.key_env,
  ccswitch_config_path: conn.ccswitch_config_path,
  model: conn.model,
});

const formatTime = (value?: string) => {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
};

const latestCheck = (state?: ModelConnectionState) => {
  if (!state) return null;
  const checks = [
    state.last_test ? { kind: "测试" as const, ...state.last_test } : null,
    state.last_use ? { kind: "调用" as const, ...state.last_use } : null,
  ].filter(Boolean) as ({ kind: "测试" | "调用" } & {
    ok: boolean;
    message: string;
    at: string;
  })[];
  if (!checks.length) return null;
  return checks.sort((a, b) => b.at.localeCompare(a.at))[0];
};

const stateText = (state?: ModelConnectionState) => {
  const latest = latestCheck(state);
  if (!latest) return "尚未测试或调用";
  const time = formatTime(latest.at);
  if (!latest.ok)
    return `不可用 · 最近${latest.kind} ${time}${latest.message ? `：${latest.message}` : ""}`;
  return `可用 · 最近${latest.kind} ${time}`;
};

export function ModelToast({
  state,
  message,
  onClose,
}: {
  state: "running" | "success" | "error";
  message: string;
  onClose?: () => void;
}) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    setVisible(true);
    if (state === "running") return;
    const timer = window.setTimeout(
      () => {
        setVisible(false);
        onClose?.();
      },
      state === "error" ? 7000 : 4000,
    );
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, message]);
  if (!visible) return null;
  return createPortal(
    <div
      className={`model-toast ${state}`}
      role={state === "error" ? "alert" : "status"}
      aria-live={state === "error" ? "assertive" : "polite"}
    >
      {state === "error" ? (
        <AlertCircle size={17} />
      ) : state === "success" ? (
        <CheckCircle2 size={17} />
      ) : (
        <LoaderCircle size={17} className="spin" />
      )}
      <span>{message}</span>
      <button
        type="button"
        className="model-toast-close"
        aria-label="关闭提示"
        onClick={() => {
          setVisible(false);
          onClose?.();
        }}
      >
        <X size={14} />
      </button>
    </div>,
    document.body,
  );
}

export function ModelConnections({
  settings,
  onSettings,
  busy = false,
  onChanged,
}: {
  settings: Settings;
  onSettings: (next: Settings) => void;
  busy?: boolean;
  onChanged?: () => void | Promise<void>;
}) {
  const [status, setStatus] = useState<ModelStatus | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [pending, setPending] = useState("");
  const [feedback, setFeedback] = useState<Feedback>(null);
  const connections = settings.llm_connections ?? [];
  const disabled = busy || !!pending;
  const draftPreset = draft?.provider ? presets[draft.provider] : null;

  const refresh = async () => {
    try {
      setStatus(await api<ModelStatus>("/model/status"));
    } catch {
      setStatus(null);
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const apply = async (
    key: string,
    path: string,
    method: string,
    body: unknown,
    message: string,
  ) => {
    setPending(key);
    setFeedback(null);
    try {
      const result = await api<{ settings: Settings; status: ModelStatus }>(
        path,
        method,
        body,
      );
      onSettings(result.settings);
      setStatus(result.status);
      setFeedback({ state: "success", message });
      await onChanged?.();
      return true;
    } catch (e) {
      setFeedback({ state: "error", message: (e as Error).message });
      return false;
    } finally {
      setPending("");
    }
  };

  const switchTo = (conn: ModelConnection) => {
    if (settings.llm_active_connection === conn.id) return;
    void apply(
      `switch:${conn.id}`,
      "/model/active",
      "POST",
      { connection_id: conn.id },
      `已切换到「${conn.label || providerLabels[conn.provider]}」，之后的 AI 分析都会使用这个模型。`,
    );
  };

  const deleteConnection = (conn: ModelConnection) => {
    if (
      !window.confirm(
        `删除「${conn.label || providerLabels[conn.provider]}」及其保存的 API Key？该模型将不再出现在列表里。`,
      )
    )
      return;
    if (draft?.id === conn.id) setDraft(null);
    void apply(
      `delete:${conn.id}`,
      `/model/connections/${conn.id}`,
      "DELETE",
      undefined,
      "模型连接已删除。",
    );
  };

  const testSaved = async (conn: ModelConnection) => {
    setPending(`test:${conn.id}`);
    setFeedback(null);
    try {
      const result = await api<{ ok: boolean; message?: string; status: ModelStatus }>(
        `/model/connections/${conn.id}/test`,
        "POST",
      );
      setStatus(result.status);
      setFeedback({
        state: "success",
        message: result.message || "连接测试成功；未发送工作对话。",
      });
    } catch (e) {
      setFeedback({ state: "error", message: (e as Error).message });
      await refresh();
    } finally {
      setPending("");
    }
  };

  const testDraft = async (draftValue: Draft) => {
    const provider = draftValue.provider;
    if (!provider) throw new Error("请先选择供应商。");
    const probe: Settings = {
      ...settings,
      llm_provider: provider,
      llm_endpoint:
        provider === "ccswitch"
          ? settings.llm_endpoint
          : draftValue.endpoint.trim(),
      llm_protocol: draftValue.protocol,
      llm_model: draftValue.model.trim(),
      llm_key_env: draftValue.key_env.trim() || "AWT_LLM_API_KEY",
      ccswitch_config_path: draftValue.ccswitch_config_path.trim(),
      llm_connections: [
        {
          id: draftValue.id || "__draft__",
          label: draftValue.label.trim(),
          provider,
          endpoint: provider === "ccswitch" ? "" : draftValue.endpoint.trim(),
          protocol: draftValue.protocol,
          key_env: draftValue.key_env.trim() || "AWT_LLM_API_KEY",
          ccswitch_config_path: draftValue.ccswitch_config_path.trim(),
          model: draftValue.model.trim(),
        },
      ],
      llm_active_connection: draftValue.id || "__draft__",
      llm_active_model: draftValue.model.trim(),
    };
    const result = await api<{ ok: boolean; message?: string }>(
      "/model/test",
      "POST",
      { settings: probe, api_key: apiKey.trim() || undefined },
    );
    if (!result.ok)
      throw new Error(result.message || "模型未返回连接成功结果");
    return result.message || "连接测试成功；未发送工作对话。";
  };

  const saveDraft = async (test: boolean) => {
    if (!draft) return;
    const provider = draft.provider;
    if (!provider) {
      setFeedback({ state: "error", message: "请先选择 AI 分析供应商。" });
      return;
    }
    const model = draft.model.trim();
    if (provider !== "ccswitch" && !model) {
      setFeedback({ state: "error", message: "请选择或填写模型名称。" });
      return;
    }
    if (provider !== "ccswitch" && !draft.endpoint.trim()) {
      setFeedback({ state: "error", message: "请填写完整 API 接口 URL。" });
      return;
    }
    setPending("save");
    setFeedback(null);
    try {
      if (test) await testDraft(draft);
      const result = await api<{ settings: Settings; status: ModelStatus }>(
        "/model/connections",
        "POST",
        {
          id: draft.id,
          label: draft.label.trim(),
          provider,
          endpoint: provider === "ccswitch" ? "" : draft.endpoint.trim(),
          protocol: draft.protocol,
          key_env: draft.key_env.trim() || "AWT_LLM_API_KEY",
          ccswitch_config_path: draft.ccswitch_config_path.trim(),
          model,
          api_key:
            provider === "ccswitch" ? undefined : apiKey.trim() || undefined,
          activate: true,
        },
      );
      onSettings(result.settings);
      setStatus(result.status);
      setDraft(null);
      setApiKey("");
      setFeedback({
        state: "success",
        message: test
          ? "模型已保存并切换使用，真实连接测试成功；未发送工作对话。"
          : "模型已保存并切换使用。",
      });
      await onChanged?.();
    } catch (e) {
      setFeedback({ state: "error", message: (e as Error).message });
    } finally {
      setPending("");
    }
  };

  return (
    <div className="model-connections">
      <div className="model-connections-head">
        <strong>已配置的模型</strong>
        <button
          type="button"
          className="outline small"
          disabled={disabled || !!draft}
          onClick={() => {
            setDraft(blankDraft());
            setApiKey("");
            setFeedback(null);
          }}
        >
          <Plus size={14} />
          新增模型配置
        </button>
      </div>

      {connections.length === 0 && !draft && (
        <div className="model-connection-empty">
          <KeyRound size={18} />
          <div>
            <strong>还没有可用模型</strong>
            <span>点击「新增模型配置」添加供应商、Key 和模型。</span>
          </div>
        </div>
      )}

      {connections.length > 0 && (
        <div className="model-connection-list">
          {connections.map((conn) => {
            const info = status?.connections?.find(
              (item) => item.id === conn.id,
            );
            const state = info?.status?.state ?? "unknown";
            const active = settings.llm_active_connection === conn.id;
            const latest = latestCheck(info?.status);
            return (
              <article
                className={`model-connection-card ${active ? "active" : ""} ${state}`}
                key={conn.id}
              >
                <div className="model-connection-info">
                  <span className="model-connection-title">
                    <strong>{providerLabels[conn.provider]}</strong>
                    {conn.label &&
                      conn.label !== providerLabels[conn.provider] && (
                        <span className="model-provider-tag">{conn.label}</span>
                      )}
                    {active && (
                      <span className="model-active-badge">当前使用</span>
                    )}
                  </span>
                  <span className="model-connection-model">
                    {modelLabel(conn.provider, conn.model) ||
                      "跟随 Claude Code 配置"}
                  </span>
                </div>
                <div className="model-connection-side">
                  <span
                    className={`model-status-dot ${state}`}
                    role="img"
                    aria-label={
                      state === "ok"
                        ? "可用"
                        : state === "error"
                          ? "不可用"
                          : "未测试"
                    }
                    title={
                      latest
                        ? stateText(info?.status)
                        : "还没有测试或调用过这个模型"
                    }
                  />
                  <div className="model-connection-actions">
                    <button
                      type="button"
                      className="source-quick-btn"
                      disabled={disabled}
                      onClick={() => void testSaved(conn)}
                      title="用该连接发送一条测试请求，不会发送工作对话；结果会记录为可用状态"
                    >
                      {pending === `test:${conn.id}` ? (
                        <LoaderCircle size={13} className="spin" />
                      ) : (
                        <Zap size={13} />
                      )}
                      测试
                    </button>
                    <button
                      type="button"
                      className="source-quick-btn"
                      disabled={disabled}
                      onClick={() => {
                        setDraft(draftFrom(conn));
                        setApiKey("");
                        setFeedback(null);
                      }}
                    >
                      <Pencil size={13} />
                      编辑
                    </button>
                    <button
                      type="button"
                      className="source-quick-btn danger"
                      disabled={disabled}
                      onClick={() => deleteConnection(conn)}
                    >
                      <Trash2 size={13} />
                      删除
                    </button>
                  </div>
                  {active ? (
                    <span className="model-switch-btn current">
                      <Check size={15} />
                      使用中
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="model-switch-btn"
                      disabled={disabled}
                      onClick={() => switchTo(conn)}
                      title="切换到这个模型，之后的 AI 分析都会使用它"
                    >
                      {pending === `switch:${conn.id}` ? (
                        <LoaderCircle size={15} className="spin" />
                      ) : (
                        <Zap size={15} />
                      )}
                      切换
                    </button>
                  )}
                </div>
              </article>
            );
          })}
        </div>
      )}

      {draft &&
        createPortal(
          <div
            className="modal-overlay model-connection-overlay"
            role="presentation"
            onClick={() => {
              if (!disabled) setDraft(null);
            }}
          >
          <div
            className="modal model-connection-modal"
            role="dialog"
            aria-modal="true"
            aria-label={draft.id ? "编辑模型连接" : "新增模型连接"}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (
                e.key === "Enter" &&
                !(e.target instanceof HTMLTextAreaElement)
              )
                e.preventDefault();
            }}
          >
            <div className="modal-head">
              <div>
                <h2>{draft.id ? "编辑模型连接" : "新增模型连接"}</h2>
              </div>
              <button
                type="button"
                aria-label="关闭模型配置"
                disabled={disabled}
                onClick={() => setDraft(null)}
              >
                <X size={16} />
              </button>
            </div>
            <div className="form-grid">
              <label>
                连接名称（可选）
                <input
                  value={draft.label}
                  onChange={(e) =>
                    setDraft({ ...draft, label: e.target.value })
                  }
                  placeholder="例如 智谱 Coding Plan"
                />
              </label>
              <label>
                AI 分析供应商
                <select
                  value={draft.provider}
                  onChange={(e) => {
                    const provider = e.target.value as LlmProvider | "";
                    const preset = provider ? presets[provider] : null;
                    setDraft({
                      ...draft,
                      provider,
                      endpoint: preset?.endpoint ?? "",
                      protocol: preset?.protocol ?? "openai",
                      model: preset?.model ?? "",
                      key_env: "AWT_LLM_API_KEY",
                      ccswitch_config_path: "",
                    });
                    setApiKey("");
                  }}
                >
                  <option value="" disabled>
                    请选择供应商
                  </option>
                  {Object.entries(providerLabels).map(([value, text]) => (
                    <option key={value} value={value}>
                      {text}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {!draft.provider && (
              <p className="model-connection-hint">
                先选择供应商，再填写接口与模型。
              </p>
            )}

            {draft.provider === "ccswitch" && (
              <>
                <label>
                  Claude Code settings.json 路径（留空自动检测）
                  <input
                    value={draft.ccswitch_config_path}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        ccswitch_config_path: e.target.value,
                      })
                    }
                    placeholder="默认 ~/.claude/settings.json"
                  />
                </label>
                <label>
                  模型名称（留空跟随 Claude Code 配置）
                  <input
                    value={draft.model}
                    onChange={(e) =>
                      setDraft({ ...draft, model: e.target.value })
                    }
                    placeholder="例如 claude-sonnet-4-5"
                  />
                </label>
              </>
            )}

            {draft.provider && draft.provider !== "ccswitch" && draftPreset && (
              <>
                <label>
                  完整 API 接口 URL
                  <input
                    required
                    type="url"
                    value={draft.endpoint}
                    onChange={(e) =>
                      setDraft({ ...draft, endpoint: e.target.value })
                    }
                    placeholder="https://…/chat/completions"
                  />
                </label>
                <div className="form-grid">
                  <label>
                    模型
                    {draftPreset.models.length ? (
                      <select
                        value={draft.model}
                        onChange={(e) =>
                          setDraft({ ...draft, model: e.target.value })
                        }
                      >
                        {draftPreset.models.map((item) => (
                          <option key={item.id} value={item.id}>
                            {item.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <input
                        value={draft.model}
                        onChange={(e) =>
                          setDraft({ ...draft, model: e.target.value })
                        }
                        placeholder="例如 vendor-model-large"
                      />
                    )}
                  </label>
                  <label>
                    接口格式
                    <select
                      value={draft.protocol}
                      onChange={(e) =>
                        setDraft({
                          ...draft,
                          protocol: e.target.value as "openai" | "anthropic",
                        })
                      }
                    >
                      <option value="openai">Chat Completions</option>
                      <option value="anthropic">Anthropic Messages</option>
                    </select>
                  </label>
                </div>
                <label>
                  也可使用服务端 API Key 环境变量
                  <input
                    value={draft.key_env}
                    onChange={(e) =>
                      setDraft({ ...draft, key_env: e.target.value })
                    }
                  />
                </label>
                <label>
                  API Key（留空保留已保存的密钥）
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="粘贴 API Key；本机加密保存"
                  />
                </label>
              </>
            )}

            <div className="modal-footer model-connection-form-actions">
              <button
                type="button"
                className="outline"
                disabled={disabled}
                onClick={() => setDraft(null)}
              >
                取消
              </button>
              <button
                type="button"
                className="outline"
                disabled={disabled || !draft.provider}
                onClick={() => void saveDraft(true)}
              >
                {pending === "save" ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Zap size={14} />
                )}
                保存并测试
              </button>
              <button
                className="primary"
                type="button"
                disabled={disabled || !draft.provider}
                onClick={() => void saveDraft(false)}
              >
                {pending === "save" ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Check size={14} />
                )}
                保存连接
              </button>
            </div>
          </div>
        </div>,
          document.body,
        )}

      {feedback && (
        <ModelToast
          state={feedback.state}
          message={feedback.message}
          onClose={() => setFeedback(null)}
        />
      )}
    </div>
  );
}
