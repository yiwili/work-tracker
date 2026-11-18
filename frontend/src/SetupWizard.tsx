import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Building2,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Circle,
  ExternalLink,
  FolderGit2,
  GitBranch,
  LoaderCircle,
  ScanLine,
  SkipForward,
  Sparkles,
  X,
} from "lucide-react";
import type { GiteeProgram, Settings } from "./types";
import { api } from "./api";
import { enterpriseApi } from "./EnterpriseSettings";
import { GitSettings } from "./GitSettings";
import { ModelConnections } from "./ModelConnections";
import { SourcePicker, sourceKeys, sourceLabels } from "./SourcePicker";
import { SourceIcon } from "./SourceIcon";

type Doctor = {
  paths: { source: string; path: string; exists: boolean }[];
  llm_enabled: boolean;
};
type ModelStatus = {
  configured: boolean;
  provider: string;
  endpoint: string;
  model: string;
  error: string;
};
type GiteeStatus = { configured: boolean; config: Settings["gitee"] };
type EnterpriseStatus = {
  configured: boolean;
  config: Settings["gitee_enterprise"];
  programs: GiteeProgram[];
  common_prefixes: string[];
};
type Company = { id: number; name: string };
type Feedback = { tone: "ok" | "error" | "info"; text: string };

const stepKeys = ["sources", "model", "git", "gitee", "done"] as const;
type StepKey = (typeof stepKeys)[number];

export function SetupWizard({
  settings,
  onClose,
  onSaved,
}: {
  settings: Settings;
  onClose: () => void;
  onSaved: () => Promise<void> | void;
}) {
  const [step, setStep] = useState(0);
  const [maxStep, setMaxStep] = useState(0);
  const [s, setS] = useState(settings);
  const [detectedSources, setDetectedSources] = useState<string[]>([]);
  const [giteeToken, setGiteeToken] = useState("");
  const [entToken, setEntToken] = useState("");
  const [companies, setCompanies] = useState<Company[]>([]);
  const [busy, setBusy] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [doctor, setDoctor] = useState<Doctor | null>(null);
  const [modelStatus, setModelStatus] = useState<ModelStatus | null>(null);
  const [giteeStatus, setGiteeStatus] = useState<GiteeStatus | null>(null);
  const [entStatus, setEntStatus] = useState<EnterpriseStatus | null>(null);
  const [skipped, setSkipped] = useState<Partial<Record<StepKey, boolean>>>({});
  const [summaryOpen, setSummaryOpen] = useState<
    Partial<Record<StepKey, boolean>>
  >({});

  const loadStatus = async () => {
    const [d, m, g, e] = await Promise.all([
      api<Doctor>("/doctor"),
      api<ModelStatus>("/model/status"),
      api<GiteeStatus>("/gitee/status"),
      api<EnterpriseStatus>("/gitee/enterprise/status"),
    ]);
    setDoctor(d);
    setModelStatus(m);
    setGiteeStatus(g);
    setEntStatus(e);
    return { doctor: d, model: m, gitee: g, enterprise: e };
  };
  useEffect(() => {
    loadStatus().catch((e) =>
      setFeedback({ tone: "error", text: (e as Error).message }),
    );
  }, []);

  const sourceDetected = (key: (typeof sourceKeys)[number]) =>
    detectedSources.includes(key) ||
    !!doctor?.paths.some((item) => item.source === key && item.exists);
  const sourcesReady = sourceKeys.some(
    (key) => s[key].enabled && sourceDetected(key),
  );
  const modelReady = !!(modelStatus?.configured && s.llm_enabled);
  const gitReady = s.git_roots.filter(Boolean).length > 0;
  const giteeReady = !!giteeStatus?.configured;
  const entReady = !!(
    entStatus?.configured && s.gitee_enterprise.enterprise_id > 0
  );
  const ready: Record<StepKey, boolean> = {
    sources: sourcesReady,
    model: modelReady,
    git: gitReady,
    gitee: entReady,
    done: false,
  };
  const hints: Record<StepKey, string> = {
    sources: sourcesReady ? "已就绪" : "待检测",
    model: modelReady ? "已开启" : "唯一必填",
    git: gitReady ? `${s.git_roots.filter(Boolean).length} 个仓库` : "可选",
    gitee: giteeReady || entReady ? "已配置" : "可选",
    done: "查看结果",
  };
  const titles: Record<StepKey, string> = {
    sources: "采集来源",
    model: "AI 模型",
    git: "Git 仓库",
    gitee: "Gitee 接入",
    done: "完成",
  };

  const summaryDetail = (key: StepKey) => {
    switch (key) {
      case "sources": {
        const active = sourceKeys.filter(
          (item) => s[item].enabled && sourceDetected(item),
        );
        if (!active.length) return <p>没有启用任何已检测到的来源。</p>;
        return active.map((item) => (
          <p key={item}>
            <strong className="source-check-name">
              <SourceIcon source={item} size={15} />
              {sourceLabels[item]}
            </strong>
            <code>{s[item].paths.filter(Boolean).join("；") || "默认路径"}</code>
          </p>
        ));
      }
      case "model":
        return (
          <>
            <p>
              <strong>连接</strong>
              <code>
                {modelStatus?.configured
                  ? `${modelStatus.model} · ${modelStatus.endpoint}`
                  : modelStatus?.error || "尚未保存连接配置"}
              </code>
            </p>
            <p>
              <strong>分析开关</strong>
              <span>{s.llm_enabled ? "已开启" : "未开启"}</span>
            </p>
            <p>
              <strong>填入方式</strong>
              <span>
                {s.auto_import_analysis
                  ? "分析后直接写入今日清单"
                  : "先生成候选，确认后导入"}
              </span>
            </p>
            <p>
              <strong>工时分配</strong>
              <span>
                {s.auto_apply_allocation
                  ? "估算后直接应用"
                  : "先生成预览，确认后应用"}
              </span>
            </p>
          </>
        );
      case "git": {
        const roots = s.git_roots.filter(Boolean);
        return (
          <>
            {roots.length ? (
              roots.map((root) => {
                const mapping = Object.entries(s.project_mappings).find(
                  ([path]) =>
                    path.replace(/[\\/]+$/, "").toLowerCase() ===
                    root.replace(/[\\/]+$/, "").toLowerCase(),
                );
                return (
                  <p key={root}>
                    <strong>
                      {mapping?.[1] ||
                        root.split(/[\\/]/).filter(Boolean).at(-1) ||
                        root}
                    </strong>
                    <code>{root}</code>
                  </p>
                );
              })
            ) : (
              <p>未添加 Git 仓库。</p>
            )}
            <p>
              <strong>提交作者</strong>
              <span>
                {s.git_author
                  ? [s.git_author.name, s.git_author.email && `<${s.git_author.email}>`]
                      .filter(Boolean)
                      .join(" ") || "已自定义"
                  : "自动使用各仓库本地 Git 身份"}
              </span>
            </p>
            <p>
              <strong>仓库级覆盖</strong>
              <span>
                {Object.keys(s.git_author_overrides).length
                  ? `${Object.keys(s.git_author_overrides).length} 个仓库已单独指定`
                  : "无"}
              </span>
            </p>
          </>
        );
      }
      case "gitee":
        return (
          <p>
            <strong>Gitee 企业版</strong>
            <span>
              {entReady
                ? `企业 ID ${s.gitee_enterprise.enterprise_id}`
                : "未配置"}
            </span>
          </p>
        );
      default:
        return null;
    }
  };

  const go = (next: number) => {
    setStep(next);
    setMaxStep((current) => Math.max(current, next));
    setFeedback(null);
  };
  const skip = (key: StepKey, next: number) => {
    setSkipped((current) => ({ ...current, [key]: true }));
    go(next);
  };
  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setFeedback(null);
    try {
      await action();
    } catch (e) {
      setFeedback({ tone: "error", text: (e as Error).message });
    } finally {
      setBusy("");
    }
  };
  const saveSettings = async (next: Settings) => {
    await api("/settings", "PUT", next);
    setS(next);
    await onSaved();
  };
  const saveSources = () =>
    run("正在保存采集来源", async () => {
      const enabled = sourceKeys.filter((key) => s[key].enabled);
      if (!enabled.length)
        throw new Error("请至少勾选一个采集来源");
      const missing = enabled.filter((key) => !sourceDetected(key));
      if (missing.length)
        throw new Error(
          `以下来源未检测到路径：${missing.map((key) => sourceLabels[key]).join("、")}。请展开填写路径后重新检测，或取消勾选。`,
        );
      const next = {
        ...s,
        analysis_sources: [
          ...enabled,
          ...(s.analysis_sources.includes("git") ? ["git"] : []),
        ],
      };
      await saveSettings(next);
      setS(next);
      await loadStatus();
      go(1);
      setFeedback({
        tone: "ok",
        text: "采集来源已保存，已勾选的来源会参与 AI 分析。Git 提交证据可在下一步添加。",
      });
    });
  const saveModel = () =>
    run("正在保存并测试模型连接", async () => {
      const status = await loadStatus();
      if (!status.model.configured)
        throw new Error(status.model.error || "请先添加并保存一个模型连接");
      // 先测后存：测试失败时一个字节都不写，避免把原本可用的连接改坏或悄悄关掉模型分析。
      const result = await api<{ ok: boolean; message?: string }>(
        "/model/test",
        "POST",
        {},
      );
      if (!result.ok)
        throw new Error(result.message || "模型未返回连接成功结果");
      const next = { ...s, llm_enabled: true };
      await api("/settings", "PUT", next);
      setS(next);
      await onSaved();
      await loadStatus();
      go(2);
      setFeedback({
        tone: "ok",
        text: "模型连接测试成功，已开启模型分析。",
      });
    });
  const saveGit = () =>
    run("正在保存仓库配置", async () => {
      await saveSettings(s);
      go(3);
      setFeedback({
        tone: "ok",
        text: "Git 仓库与项目名称已保存。提交只作为证据，不按条生成工作项。",
      });
    });
  const saveEntToken = () =>
    run("正在读取企业列表", async () => {
      if (entToken.trim())
        await enterpriseApi("token", { api_key: entToken.trim() });
      const list = (await enterpriseApi(
        "enterprises",
        undefined,
        "GET",
      )) as Company[];
      setCompanies(list);
      setEntToken("");
      await loadStatus();
      setFeedback({
        tone: list.length ? "ok" : "error",
        text: list.length
          ? `已读取 ${list.length} 个可访问企业，请选择目标企业。`
          : "Token 有效但没有可访问的企业，请确认企业成员身份与 API 权限。",
      });
    });
  const saveGitee = () =>
    run("正在保存 Gitee 配置", async () => {
      if (entToken.trim())
        await enterpriseApi("token", { api_key: entToken.trim() });
      await saveSettings(s);
      setEntToken("");
      const status = await loadStatus();
      const configured = status.enterprise.configured;
      go(4);
      setFeedback({
        tone: configured ? "ok" : "info",
        text: configured
          ? "Gitee 企业接入配置已保存，可在系统设置「Gitee 企业版」中继续完善。"
          : "尚未填写 Gitee 企业凭据，可稍后在系统设置中补充。",
      });
    });

  useEffect(() => {
    if (step !== 3 || !entStatus?.configured || companies.length) return;
    enterpriseApi("enterprises", undefined, "GET")
      .then((list) => setCompanies(list as Company[]))
      .catch(() => undefined);
  }, [step, entStatus?.configured, companies.length]);

  const footer = (options: {
    next: number;
    nextLabel?: string;
    onNext?: () => void;
    skippable?: boolean;
    current: StepKey;
  }) => (
    <div className="setup-actions">
      {step > 0 && (
        <button
          type="button"
          className="outline"
          disabled={!!busy}
          onClick={() => go(step - 1)}
        >
          <ArrowLeft size={15} /> 上一步
        </button>
      )}
      {options.skippable && (
        <button
          type="button"
          disabled={!!busy}
          onClick={() => skip(options.current, options.next)}
        >
          <SkipForward size={15} /> 跳过此步
        </button>
      )}
      <button
        type="button"
        className="primary spacer"
        disabled={!!busy}
        onClick={options.onNext ?? (() => go(options.next))}
      >
        {busy ? (
          <LoaderCircle className="spin" size={16} />
        ) : (
          <ArrowRight size={16} />
        )}
        {options.nextLabel || "下一步"}
      </button>
    </div>
  );

  const renderStep = () => {
    switch (step) {
      case 0:
        return (
          <>
            <h3>
              <ScanLine size={18} /> 第 1 步 · 确认采集来源
            </h3>
            <SourcePicker
              settings={s}
              onChange={setS}
              onDetected={setDetectedSources}
            />
          </>
        );
      case 1:
        return (
          <>
            <h3>
              <Bot size={18} /> 第 2 步 · 接通 AI 模型
            </h3>
            <ModelConnections
              settings={s}
              onSettings={setS}
              onChanged={async () => {
                await loadStatus();
              }}
              busy={!!busy}
            />
            <label className="check-label">
              <input
                type="checkbox"
                checked={s.auto_import_analysis}
                onChange={(e) =>
                  setS({ ...s, auto_import_analysis: e.target.checked })
                }
              />
              AI 分析完成后直接填入今日工作清单
            </label>
            <label className="check-label">
              <input
                type="checkbox"
                checked={s.auto_apply_allocation}
                onChange={(e) =>
                  setS({ ...s, auto_apply_allocation: e.target.checked })
                }
              />
              AI 分配工时后直接应用到工作项
            </label>
            <div className="setup-status">
              <div className={modelReady ? "ok" : "warn"}>
                <span>当前状态</span>
                <code>
                  {modelStatus?.configured
                    ? `${modelStatus.model} · ${modelStatus.endpoint}`
                    : modelStatus?.error || "尚未保存连接配置"}
                </code>
                <em>
                  {modelStatus?.configured
                    ? s.llm_enabled
                      ? "已开启"
                      : "已配置未开启"
                    : "未配置"}
                </em>
              </div>
            </div>
          </>
        );
      case 2:
        return (
          <>
            <h3>
              <FolderGit2 size={18} /> 第 3 步 · Git 仓库（可选）
            </h3>
            <GitSettings settings={s} onChange={setS} />
          </>
        );
      case 3:
        return (
          <>
            <h3>
              <Building2 size={18} /> 第 4 步 · Gitee 企业版接入（可选）
            </h3>
            <div className="config-card">
              <h3>
                <Building2 size={16} /> Gitee 企业版配置
              </h3>
              <div className="quick-help">
                <span>
                  统一使用 Gitee 企业令牌获取与提交工作项。进入个人设置 → MCP 企业令牌，选择目标企业并授予项目读取、工作项创建权限。
                </span>
                <a
                  href="https://gitee.com/profile/mcp_gitee_ent_access_tokens"
                  target="_blank"
                  rel="noreferrer"
                >
                  获取 MCP 企业令牌 <ExternalLink size={12} />
                </a>
              </div>
              <label>
                MCP 企业令牌（加密保存于本机）
                <input
                  type="password"
                  autoComplete="new-password"
                  value={entToken}
                  onChange={(e) => setEntToken(e.target.value)}
                  placeholder={
                    entStatus?.configured
                      ? "已保存，留空表示保留"
                      : "粘贴 MCP 企业令牌"
                  }
                />
              </label>
              <div className="setup-inline">
                <button
                  type="button"
                  className="outline small"
                  disabled={
                    !!busy || (!entToken && !entStatus?.configured)
                  }
                  onClick={saveEntToken}
                >
                  保存并读取企业列表
                </button>
                <span className="muted">
                  {entStatus?.configured
                    ? "企业凭据已配置"
                    : "尚未保存企业凭据"}
                </span>
              </div>
              {companies.length > 0 ? (
                <label>
                  目标企业
                  <select
                    value={s.gitee_enterprise.enterprise_id || ""}
                    onChange={(e) => {
                      const entId = Number(e.target.value);
                      setS({
                        ...s,
                        gitee_enterprise: {
                          ...s.gitee_enterprise,
                          enterprise_id: entId,
                        },
                      });
                      if (entId > 0) {
                        void enterpriseApi(
                          `programs?enterprise_id=${entId}`,
                          undefined,
                          "GET",
                        ).catch(() => undefined);
                      }
                    }}
                  >
                    <option value="">请选择企业</option>
                    {companies.map((company) => (
                      <option key={company.id} value={company.id}>
                        {company.name}（{company.id}）
                      </option>
                    ))}
                  </select>
                </label>
              ) : (
                s.gitee_enterprise.enterprise_id > 0 && (
                  <p className="muted">
                    当前目标企业 ID：{s.gitee_enterprise.enterprise_id}
                    。如需切换，请先读取企业列表。
                  </p>
                )
              )}
            </div>
          </>
        );
      default:
        return (
          <>
            <h3>
              <Sparkles size={18} /> 配置完成
            </h3>
            <ul className="setup-summary">
              {(
                [
                  ["sources", sourcesReady, sourcesReady ? "已找到本地对话记录" : "未找到可采集目录"],
                  ["model", modelReady, modelReady ? modelStatus?.model || "已开启" : "未开启，AI 功能暂不可用"],
                  ["git", gitReady, gitReady ? `${s.git_roots.filter(Boolean).length} 个仓库` : "未配置（可选）"],
                  ["gitee", entReady, entReady ? `企业 ID ${s.gitee_enterprise.enterprise_id}` : "未配置（可选）"],
                ] as [StepKey, boolean, string][]
              ).map(([key, ok, text]) => {
                const expanded = !!summaryOpen[key];
                return (
                  <li key={key} className={ok ? "ok" : "todo"}>
                    <button
                      type="button"
                      className="summary-head"
                      aria-expanded={expanded}
                      onClick={() =>
                        setSummaryOpen((current) => ({
                          ...current,
                          [key]: !current[key],
                        }))
                      }
                    >
                      {ok ? (
                        <CheckCircle2 size={17} />
                      ) : (
                        <Circle size={17} />
                      )}
                      <strong>{titles[key]}</strong>
                      <span>{text}</span>
                      <span className="summary-chevron" aria-hidden="true">
                        {expanded ? (
                          <ChevronDown size={15} />
                        ) : (
                          <ChevronRight size={15} />
                        )}
                      </span>
                    </button>
                    {expanded && (
                      <div className="summary-body">{summaryDetail(key)}</div>
                    )}
                  </li>
                );
              })}
            </ul>
          </>
        );
    }
  };

  const renderActions = () => {
    switch (step) {
      case 0:
        return footer({
          current: "sources",
          next: 1,
          nextLabel: "保存并继续",
          onNext: saveSources,
          skippable: true,
        });
      case 1:
        return footer({
          current: "model",
          next: 2,
          nextLabel: "保存并测试连接",
          onNext: saveModel,
          skippable: true,
        });
      case 2:
        return footer({
          current: "git",
          next: 3,
          nextLabel: "保存并继续",
          onNext: saveGit,
          skippable: true,
        });
      case 3:
        return footer({
          current: "gitee",
          next: 4,
          nextLabel: "保存并查看结果",
          onNext: saveGitee,
          skippable: true,
        });
      default:
        return (
          <div className="setup-actions">
            <button
              type="button"
              className="outline"
              disabled={!!busy}
              onClick={() =>
                run("正在重新检查", async () => {
                  await loadStatus();
                  setFeedback({ tone: "ok", text: "状态已刷新。" });
                })
              }
            >
              重新检查
            </button>
            <button
              type="button"
              className="primary spacer"
              onClick={onClose}
            >
              <Check size={16} /> 完成
            </button>
          </div>
        );
    }
  };

  return (
    <div className="modal-overlay">
      <section
        className="modal setup-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="setup-title"
        onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) onClose();
          if (e.key === "Tab") {
            const items = Array.from(
              e.currentTarget.querySelectorAll<HTMLElement>(
                "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href]",
              ),
            );
            const first = items[0],
              last = items.at(-1);
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last?.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <div className="modal-head">
          <div>
            <h2 id="setup-title">
              <Sparkles size={20} /> 配置引导
            </h2>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭配置引导"
            disabled={!!busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        <div className="setup-layout">
          <ol className="setup-steps">
            {stepKeys.map((key, index) => {
              const isDone = ready[key] || skipped[key];
              return (
                <li
                  key={key}
                  className={
                    (step === index ? "active" : "") +
                    (ready[key] ? " done" : "")
                  }
                >
                  <button
                    type="button"
                    disabled={index > maxStep}
                    onClick={() => go(index)}
                  >
                    <span className="setup-index">
                      {ready[key] ? (
                        <Check size={12} />
                      ) : skipped[key] ? (
                        <SkipForward size={11} />
                      ) : (
                        index + 1
                      )}
                    </span>
                    <span>
                      {titles[key]}
                      <small>{isDone && skipped[key] && !ready[key] ? "已跳过" : hints[key]}</small>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
          <div className="setup-main">
            <div className="setup-content" key={step}>
              {renderStep()}
            </div>
            {feedback && (
              <div
                className={
                  "alert setup-feedback" +
                  (feedback.tone === "error" ? " error" : "")
                }
                role={feedback.tone === "error" ? "alert" : "status"}
              >
                {feedback.text}
              </div>
            )}
            {renderActions()}
          </div>
        </div>
      </section>
    </div>
  );
}
