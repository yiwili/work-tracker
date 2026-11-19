import { useEffect, useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  Database,
  KeyRound,
  LoaderCircle,
  RefreshCw,
  Trash2,
  Zap,
} from "lucide-react";
import type {
  CompressionPrediction,
  LlmProtocol,
  LlmProvider,
  MemoryCompressionConfig,
  MemoryCompressionStatus,
  Settings,
} from "./types";
import { api } from "./api";
import { modelLabel, presets, providerLabels } from "./modelPresets";
import { SourceIcon } from "./SourceIcon";
import { sourceKeys, sourceLabels } from "./SourcePicker";

export const defaultMemoryCompression = (): MemoryCompressionConfig => ({
  enabled: false,
  mode: "custom",
  connection_id: "",
  connection_model: "",
  fallback_connection_ids: [],
  provider: "local",
  endpoint: "http://127.0.0.1:11434/v1/chat/completions",
  protocol: "openai",
  key_env: "AWT_COMPRESSION_API_KEY",
  ccswitch_config_path: "",
  model: "",
  sources: [...sourceKeys],
});

export function normalizeMemoryCompression(
  raw?: Partial<MemoryCompressionConfig> | null,
): MemoryCompressionConfig {
  const base = defaultMemoryCompression();
  if (!raw) return base;
  return {
    ...base,
    ...raw,
    sources: Array.isArray(raw.sources) ? raw.sources : [...sourceKeys],
    fallback_connection_ids: Array.isArray(raw.fallback_connection_ids)
      ? raw.fallback_connection_ids
      : [],
  };
}

export function MemoryCompressionSettings({
  settings,
  onChange,
  busy = false,
  apiKey,
  onApiKeyChange,
  predictDate,
}: {
  settings: Settings;
  onChange: (next: Settings) => void;
  busy?: boolean;
  apiKey: string;
  onApiKeyChange: (val: string) => void;
  predictDate?: string;
}) {
  const cfg = normalizeMemoryCompression(settings.memory_compression);
  const connections = settings.llm_connections ?? [];

  const [status, setStatus] = useState<MemoryCompressionStatus | null>(null);
  const [prediction, setPrediction] = useState<CompressionPrediction | null>(
    null,
  );
  const [predicting, setPredicting] = useState(false);
  const [agentsExpanded, setAgentsExpanded] = useState(true);
  const [memoriesExpanded, setMemoriesExpanded] = useState(false);
  const [discoveredModels, setDiscoveredModels] = useState<string[]>([]);
  const [manualModel, setManualModel] = useState(false);
  const [connModels, setConnModels] = useState<Record<string, string[]>>({});
  const [manualConnModel, setManualConnModel] = useState(false);
  const [pending, setPending] = useState("");
  const [notice, setNotice] = useState<{
    state: "success" | "error";
    message: string;
  } | null>(null);

  const updateCfg = (patch: Partial<MemoryCompressionConfig>) => {
    onChange({
      ...settings,
      memory_compression: { ...cfg, ...patch },
    });
  };

  const refreshStatus = async () => {
    try {
      const data = await api<MemoryCompressionStatus>(
        "/memory-compression/status",
      );
      setStatus(data);
    } catch {
      setStatus(null);
    }
  };

  useEffect(() => {
    void refreshStatus();
  }, []);

  const targetDate =
    predictDate || new Date().toLocaleDateString("sv-SE");
  const sourcesKey = cfg.sources.join(",");
  const analysisKey = settings.analysis_sources.join(",");

  useEffect(() => {
    const controller = new AbortController();
    setPredicting(true);
    const timer = window.setTimeout(() => {
      api<CompressionPrediction>("/memory-compression/predict", "POST", {
        date: targetDate,
        memory_compression: cfg,
        analysis_sources: settings.analysis_sources,
      })
        .then((res) => {
          if (!controller.signal.aborted) setPrediction(res);
        })
        .catch(() => {
          if (!controller.signal.aborted) setPrediction(null);
        })
        .finally(() => {
          if (!controller.signal.aborted) setPredicting(false);
        });
    }, 120);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    targetDate,
    cfg.enabled,
    cfg.mode,
    cfg.connection_id,
    cfg.connection_model,
    cfg.provider,
    cfg.model,
    sourcesKey,
    analysisKey,
  ]);

  const selectedSources = new Set(cfg.sources);
  const allSelected = sourceKeys.every((k) => selectedSources.has(k));
  const selectedCount = sourceKeys.filter((k) => selectedSources.has(k)).length;

  const toggleSource = (key: string, checked: boolean) => {
    const next = checked
      ? Array.from(new Set([...cfg.sources, key]))
      : cfg.sources.filter((item) => item !== key);
    updateCfg({ sources: next });
  };

  const toggleFallback = (connId: string, checked: boolean) => {
    const next = checked
      ? Array.from(new Set([...cfg.fallback_connection_ids, connId]))
      : cfg.fallback_connection_ids.filter((id) => id !== connId);
    updateCfg({ fallback_connection_ids: next });
  };

  const discoverCustomModels = async () => {
    setPending("discover-custom");
    setNotice(null);
    try {
      const result = await api<{
        models: string[];
        source: string;
        warning?: string;
      }>("/model/models/discover", "POST", {
        provider: cfg.provider,
        endpoint: cfg.provider === "ccswitch" ? "" : cfg.endpoint.trim(),
        protocol: cfg.protocol,
        key_env: cfg.key_env.trim() || "AWT_COMPRESSION_API_KEY",
        ccswitch_config_path: cfg.ccswitch_config_path.trim(),
        api_key: apiKey.trim() || undefined,
      });
      setDiscoveredModels(result.models);
      setManualModel(false);
      if (!cfg.model.trim() && result.models.length > 0) {
        updateCfg({ model: result.models[0] });
      }
      setNotice({
        state: "success",
        message:
          result.warning ||
          `已获取 ${result.models.length} 个模型，请在下方选择。`,
      });
    } catch (e) {
      setNotice({ state: "error", message: (e as Error).message });
    } finally {
      setPending("");
    }
  };

  const discoverExistingConnectionModels = async (connId: string) => {
    if (!connId) return;
    setPending("discover-conn");
    setNotice(null);
    try {
      const result = await api<{
        models: string[];
        source: string;
        warning?: string;
      }>("/model/models/discover", "POST", {
        connection_id: connId,
        save_to_connection: true,
      });
      setConnModels((prev) => ({ ...prev, [connId]: result.models }));
      setManualConnModel(false);
      setNotice({
        state: "success",
        message:
          result.warning ||
          `已从所选 API 获取 ${result.models.length} 个模型，可直接选取。`,
      });
    } catch (e) {
      setNotice({ state: "error", message: (e as Error).message });
    } finally {
      setPending("");
    }
  };

  const saveCompressionKey = async () => {
    if (!apiKey.trim()) {
      setNotice({ state: "error", message: "请先输入压缩模型的 API Key。" });
      return;
    }
    setPending("save-key");
    setNotice(null);
    try {
      const st = await api<MemoryCompressionStatus>(
        "/memory-compression/key",
        "POST",
        { api_key: apiKey.trim() },
      );
      setStatus(st);
      onApiKeyChange("");
      setNotice({
        state: "success",
        message: "压缩模型 API Key 已本机加密保存。",
      });
    } catch (e) {
      setNotice({ state: "error", message: (e as Error).message });
    } finally {
      setPending("");
    }
  };

  const testCompression = async () => {
    setPending("test");
    setNotice(null);
    try {
      const res = await api<{
        ok: boolean;
        message?: string;
        model?: string;
        status?: MemoryCompressionStatus;
      }>("/memory-compression/test", "POST", {
        memory_compression: cfg,
        connections,
        api_key: apiKey.trim() || undefined,
      });
      if (res.status) setStatus(res.status);
      setNotice({
        state: "success",
        message: res.message || "压缩模型连接测试成功。",
      });
    } catch (e) {
      setNotice({ state: "error", message: (e as Error).message });
    } finally {
      setPending("");
    }
  };

  const clearCache = async () => {
    setPending("clear-cache");
    setNotice(null);
    try {
      const res = await api<{
        deleted: number;
        storage: MemoryCompressionStatus["storage"];
      }>("/memory-compression/cache", "DELETE");
      setStatus((prev) => (prev ? { ...prev, storage: res.storage } : prev));
      setNotice({
        state: "success",
        message: `已清理 ${res.deleted} 条本地压缩记忆缓存；原始对话日志未受影响。`,
      });
    } catch (e) {
      setNotice({ state: "error", message: (e as Error).message });
    } finally {
      setPending("");
    }
  };

  const customPreset = presets[cfg.provider] ?? presets.local;
  const customModelOptions = Array.from(
    new Set([
      ...(cfg.model.trim() ? [cfg.model.trim()] : []),
      ...discoveredModels,
      ...customPreset.models.map((item) => item.id),
    ]),
  );

  const selectedConn = connections.find((c) => c.id === cfg.connection_id);
  const selectedConnOptions = selectedConn
    ? Array.from(
        new Set([
          ...(cfg.connection_model.trim() ? [cfg.connection_model.trim()] : []),
          ...(selectedConn.model ? [selectedConn.model] : []),
          ...(connModels[selectedConn.id] ?? selectedConn.available_models ?? []),
          ...(presets[selectedConn.provider]?.models.map((m) => m.id) ?? []),
        ]),
      )
    : [];

  const disabled = busy || !!pending;
  const storage = status?.storage;

  return (
    <section className="settings-section compression-section">
      <h2>
        <Database size={19} /> 对话记忆语义压缩（本地 / 低额度模型）
      </h2>
      <p className="muted">
        使用本地模型或低消费额度模型先将长对话压缩为高密度语义记忆并存储在本地，再传给工时分析
        Agent；原始对话记录、事件引用与工时证据完整保留。
      </p>

      <label className="check-label">
        <input
          type="checkbox"
          checked={cfg.enabled}
          disabled={disabled}
          onChange={(e) => updateCfg({ enabled: e.target.checked })}
        />
        启用对话记忆语义压缩（统一开关）
      </label>

      <div className={`compression-agents-box ${cfg.enabled ? "" : "dimmed"}`}>
        <div className="compression-agents-head">
          <button
            type="button"
            className="compression-collapse-btn"
            onClick={() => setAgentsExpanded((v) => !v)}
          >
            {agentsExpanded ? (
              <ChevronDown size={15} />
            ) : (
              <ChevronRight size={15} />
            )}
            <span>
              按 Agent 细化是否压缩（默认全选 · 已选 {selectedCount}/
              {sourceKeys.length}）
            </span>
          </button>
          <div className="compression-agents-actions">
            <button
              type="button"
              className="source-quick-btn"
              disabled={disabled || allSelected}
              onClick={() => updateCfg({ sources: [...sourceKeys] })}
            >
              全选
            </button>
            <button
              type="button"
              className="source-quick-btn"
              disabled={disabled || selectedCount === 0}
              onClick={() => updateCfg({ sources: [] })}
            >
              全不选
            </button>
          </div>
        </div>
        {agentsExpanded && (
          <div className="compression-agents-grid">
            {sourceKeys.map((key) => {
              const checked = selectedSources.has(key);
              return (
                <label
                  key={key}
                  className={`compression-agent-chip ${checked ? "active" : ""}`}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    disabled={disabled}
                    onChange={(e) => toggleSource(key, e.target.checked)}
                  />
                  <SourceIcon source={key} />
                  <span>{sourceLabels[key]}</span>
                </label>
              );
            })}
          </div>
        )}
      </div>

      <div className="compression-mode-tabs" role="tablist" aria-label="压缩模型来源">
        <button
          type="button"
          role="tab"
          aria-selected={cfg.mode === "custom"}
          className={cfg.mode === "custom" ? "active" : ""}
          disabled={disabled}
          onClick={() => updateCfg({ mode: "custom" })}
        >
          本地 / 独立轻量模型
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={cfg.mode === "connection"}
          className={cfg.mode === "connection" ? "active" : ""}
          disabled={disabled}
          onClick={() => {
            const firstId = cfg.connection_id || connections[0]?.id || "";
            updateCfg({ mode: "connection", connection_id: firstId });
          }}
        >
          从已有 API 选取模型
        </button>
      </div>

      {cfg.mode === "custom" ? (
        <div className="compression-model-panel">
          <div className="form-grid">
            <label>
              模型来源 / 类型
              <select
                value={cfg.provider}
                disabled={disabled}
                onChange={(e) => {
                  const provider = e.target.value as LlmProvider;
                  const preset = presets[provider];
                  setDiscoveredModels([]);
                  setManualModel(false);
                  updateCfg({
                    provider,
                    endpoint: preset?.endpoint ?? "",
                    protocol: preset?.protocol ?? "openai",
                    model: preset?.model ?? "",
                  });
                }}
              >
                {Object.entries(providerLabels).map(([value, text]) => (
                  <option key={value} value={value}>
                    {text}
                  </option>
                ))}
              </select>
            </label>
            <label>
              接口格式
              <select
                value={cfg.protocol}
                disabled={disabled || cfg.provider === "ccswitch"}
                onChange={(e) =>
                  updateCfg({ protocol: e.target.value as LlmProtocol })
                }
              >
                <option value="openai">Chat Completions</option>
                <option value="anthropic">Anthropic Messages</option>
              </select>
            </label>
          </div>

          {cfg.provider === "ccswitch" ? (
            <div className="form-grid">
              <label>
                Claude Code settings.json 路径（留空自动检测）
                <input
                  value={cfg.ccswitch_config_path}
                  disabled={disabled}
                  onChange={(e) =>
                    updateCfg({ ccswitch_config_path: e.target.value })
                  }
                  placeholder="默认 ~/.claude/settings.json"
                />
              </label>
              <label>
                模型名称（留空跟随配置）
                <input
                  value={cfg.model}
                  disabled={disabled}
                  onChange={(e) => updateCfg({ model: e.target.value })}
                  placeholder="例如 claude-3-5-haiku-latest"
                />
              </label>
            </div>
          ) : (
            <>
              <label>
                压缩模型接口 URL
                <input
                  type="url"
                  value={cfg.endpoint}
                  disabled={disabled}
                  onChange={(e) => updateCfg({ endpoint: e.target.value })}
                  placeholder="http://127.0.0.1:11434/v1/chat/completions"
                />
              </label>
              <div className="form-grid">
                <label>
                  <span className="model-field-head">
                    <span>压缩模型名称</span>
                    <span className="model-field-tools">
                      <button
                        type="button"
                        className="source-quick-btn"
                        disabled={disabled || !cfg.endpoint.trim()}
                        onClick={() => void discoverCustomModels()}
                      >
                        {pending === "discover-custom" ? (
                          <LoaderCircle size={12} className="spin" />
                        ) : (
                          <RefreshCw size={12} />
                        )}
                        获取模型列表
                      </button>
                      {customModelOptions.length > 0 && (
                        <button
                          type="button"
                          className="source-quick-btn"
                          onClick={() => setManualModel((v) => !v)}
                        >
                          {manualModel ? "列表选择" : "手动输入"}
                        </button>
                      )}
                    </span>
                  </span>
                  {!manualModel && customModelOptions.length > 0 ? (
                    <select
                      value={cfg.model}
                      disabled={disabled}
                      onChange={(e) => updateCfg({ model: e.target.value })}
                    >
                      {!cfg.model && <option value="">请选择模型</option>}
                      {customModelOptions.map((id) => (
                        <option key={id} value={id}>
                          {modelLabel(cfg.provider, id)}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      value={cfg.model}
                      disabled={disabled}
                      onChange={(e) => updateCfg({ model: e.target.value })}
                      placeholder={
                        cfg.provider === "local"
                          ? "例如 qwen2.5:7b 或 qwen3:8b"
                          : "例如 glm-4.5-flash / deepseek-flash"
                      }
                    />
                  )}
                </label>
                <label>
                  服务端环境变量名（可选）
                  <input
                    value={cfg.key_env}
                    disabled={disabled}
                    onChange={(e) => updateCfg({ key_env: e.target.value })}
                    placeholder="AWT_COMPRESSION_API_KEY"
                  />
                </label>
              </div>
              <div className="compression-key-row">
                <label>
                  压缩模型 API Key（
                  {cfg.provider === "local"
                    ? "本地服务通常无需填写"
                    : status?.key_configured
                      ? "已保存密钥，留空保留"
                      : "云接口需填写或使用环境变量"}
                  ）
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={apiKey}
                    disabled={disabled}
                    onChange={(e) => onApiKeyChange(e.target.value)}
                    placeholder={
                      cfg.provider === "local"
                        ? "本地 Ollama / LM Studio 可留空"
                        : "粘贴压缩模型 API Key；本机加密保存"
                    }
                  />
                </label>
                {apiKey.trim() && (
                  <button
                    type="button"
                    className="outline small"
                    disabled={disabled}
                    onClick={() => void saveCompressionKey()}
                  >
                    <KeyRound size={14} />
                    保存密钥
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      ) : (
        <div className="compression-model-panel">
          {connections.length === 0 ? (
            <p className="muted">
              当前还没有已配置的 API 连接；请先在上方「已配置的模型」中新增 API，或切回「本地 / 独立轻量模型」。
            </p>
          ) : (
            <div className="form-grid">
              <label>
                选择已有 API 连接
                <select
                  value={cfg.connection_id}
                  disabled={disabled}
                  onChange={(e) => {
                    setManualConnModel(false);
                    updateCfg({
                      connection_id: e.target.value,
                      connection_model: "",
                    });
                  }}
                >
                  <option value="">请选择 API 连接</option>
                  {connections.map((conn) => (
                    <option key={conn.id} value={conn.id}>
                      {(conn.label || providerLabels[conn.provider]) +
                        (conn.model ? `（默认 ${conn.model}）` : "")}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                <span className="model-field-head">
                  <span>从该 API 选取压缩模型</span>
                  <span className="model-field-tools">
                    <button
                      type="button"
                      className="source-quick-btn"
                      disabled={disabled || !cfg.connection_id}
                      onClick={() =>
                        void discoverExistingConnectionModels(cfg.connection_id)
                      }
                    >
                      {pending === "discover-conn" ? (
                        <LoaderCircle size={12} className="spin" />
                      ) : (
                        <RefreshCw size={12} />
                      )}
                      拉取模型
                    </button>
                    <button
                      type="button"
                      className="source-quick-btn"
                      onClick={() => setManualConnModel((v) => !v)}
                    >
                      {manualConnModel ? "列表选择" : "手动指定"}
                    </button>
                  </span>
                </span>
                {!manualConnModel && selectedConnOptions.length > 0 ? (
                  <select
                    value={cfg.connection_model}
                    disabled={disabled || !cfg.connection_id}
                    onChange={(e) =>
                      updateCfg({ connection_model: e.target.value })
                    }
                  >
                    <option value="">
                      跟随该 API 当前模型（
                      {selectedConn?.model || "未指定"}）
                    </option>
                    {selectedConnOptions.map((id) => (
                      <option key={id} value={id}>
                        {selectedConn
                          ? modelLabel(selectedConn.provider, id)
                          : id}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    value={cfg.connection_model}
                    disabled={disabled || !cfg.connection_id}
                    onChange={(e) =>
                      updateCfg({ connection_model: e.target.value })
                    }
                    placeholder="留空则跟随该 API 默认模型"
                  />
                )}
              </label>
            </div>
          )}
        </div>
      )}

      {connections.length > 0 && (
        <div className="compression-fallbacks">
          <span className="compression-fallbacks-label">
            备用压缩 API（多 API 容灾，主压缩模型不可用或限流时自动按序尝试）：
          </span>
          <div className="compression-fallbacks-list">
            {connections
              .filter(
                (conn) =>
                  !(
                    cfg.mode === "connection" && cfg.connection_id === conn.id
                  ),
              )
              .map((conn) => {
                const checked = cfg.fallback_connection_ids.includes(conn.id);
                return (
                  <label key={conn.id} className="compression-fallback-chip">
                    <input
                      type="checkbox"
                      checked={checked}
                      disabled={disabled}
                      onChange={(e) => toggleFallback(conn.id, e.target.checked)}
                    />
                    <span>
                      {conn.label || providerLabels[conn.provider]}
                      {conn.model ? ` · ${conn.model}` : ""}
                    </span>
                  </label>
                );
              })}
          </div>
        </div>
      )}

      <div className="compression-actions-bar">
        <button
          type="button"
          className="outline small"
          disabled={disabled}
          onClick={() => void testCompression()}
        >
          {pending === "test" ? (
            <LoaderCircle size={14} className="spin" />
          ) : (
            <Zap size={14} />
          )}
          测试压缩模型连接
        </button>
        <button
          type="button"
          className="outline small"
          disabled={disabled}
          onClick={() => void refreshStatus()}
        >
          <RefreshCw size={14} />
          刷新存储状态
        </button>
        {storage && storage.total_records > 0 && (
          <button
            type="button"
            className="outline small danger"
            disabled={disabled}
            onClick={() => void clearCache()}
          >
            <Trash2 size={14} />
            清空本地记忆缓存
          </button>
        )}
      </div>

      {notice && (
        <div
          className={`alert ${notice.state === "error" ? "error" : ""}`}
          role={notice.state === "error" ? "alert" : "status"}
        >
          {notice.message}
        </div>
      )}

      {storage && (
        <div className="compression-storage-box">
          <div className="compression-storage-summary">
            <span>
              本地已存储 <strong>{storage.total_records}</strong> 条压缩记忆
              {storage.total_raw_chars > 0 && (
                <>
                  {" "}
                  · 原文{" "}
                  {storage.total_raw_chars.toLocaleString("zh-CN")} 字符 → 压缩后{" "}
                  {storage.total_compressed_chars.toLocaleString("zh-CN")} 字符
                  （节省 {Math.round(storage.saved_ratio * 100)}%）
                </>
              )}
            </span>
            {storage.recent.length > 0 && (
              <button
                type="button"
                className="source-quick-btn"
                onClick={() => setMemoriesExpanded((v) => !v)}
              >
                {memoriesExpanded ? "收起明细" : "查看最近记忆"}
              </button>
            )}
          </div>
          {memoriesExpanded && storage.recent.length > 0 && (
            <ul className="compression-recent-list">
              {storage.recent.map((item) => (
                <li key={item.id}>
                  <div className="compression-recent-head">
                    <strong>
                      {sourceLabels[item.source as keyof typeof sourceLabels] ||
                        item.source}
                    </strong>
                    <span>
                      {item.date_key} · {item.model || "未知模型"} ·{" "}
                      {item.raw_chars.toLocaleString("zh-CN")} →{" "}
                      {item.compressed_chars.toLocaleString("zh-CN")} 字符
                    </span>
                  </div>
                  <p>{item.summary_text}</p>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {prediction && (
        <div className="compression-predict-box">
          <div className="compression-predict-head">
            <strong>
              输入 Token 预测（{prediction.date_key}）
              {predicting && " · 计算中…"}
            </strong>
            <span className="compression-predict-note">{prediction.note}</span>
          </div>
          {prediction.raw_input_tokens === 0 ? (
            <p className="muted">当前日期暂无可分析的对话输入记录。</p>
          ) : (
            <>
              <div className="compression-predict-metrics">
                <span>
                  压缩前对话输入约{" "}
                  <strong>
                    {prediction.raw_input_tokens.toLocaleString("zh-CN")}
                  </strong>{" "}
                  tokens
                </span>
                <span>→</span>
                <span>
                  {cfg.enabled ? "预测压缩后输入约 " : "若开启压缩预计输入约 "}
                  <strong>
                    {(cfg.enabled
                      ? prediction.predicted_input_tokens
                      : prediction.simulated_compressed_tokens
                    ).toLocaleString("zh-CN")}
                  </strong>{" "}
                  tokens
                </span>
                <span className="compression-predict-saving">
                  {(() => {
                    const saved = cfg.enabled
                      ? prediction.saved_tokens
                      : prediction.simulated_saved_tokens;
                    const ratio = cfg.enabled
                      ? prediction.saved_ratio
                      : prediction.simulated_saved_ratio;
                    if (saved > 0) {
                      return `预计节省 ${saved.toLocaleString("zh-CN")} tokens（${Math.round(ratio * 100)}%）`;
                    }
                    if (saved < 0) {
                      return `当前文本较短（预计增加约 ${Math.abs(saved).toLocaleString("zh-CN")} tokens 封装开销）`;
                    }
                    return "预计无变化";
                  })()}
                </span>
              </div>
              {prediction.per_source.length > 0 && (
                <div className="compression-predict-sources">
                  {prediction.per_source.map((item) => (
                    <span
                      key={item.source}
                      className={`compression-predict-pill ${item.compression_selected ? "compressed" : ""}`}
                    >
                      {sourceLabels[item.source as keyof typeof sourceLabels] ||
                        (item.source === "git" ? "Git" : item.source)}
                      ：{item.raw_tokens.toLocaleString("zh-CN")} →{" "}
                      {(cfg.enabled
                        ? item.predicted_tokens
                        : item.simulated_tokens
                      ).toLocaleString("zh-CN")}{" "}
                      tokens
                      {item.cached_sessions > 0 ? "（含本地缓存）" : ""}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </section>
  );
}
