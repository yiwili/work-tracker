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
  KeyRound,
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
import { ModelSelect, presets } from "./ModelSettings";
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
  const [apiKey, setApiKey] = useState("");
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
    gitee: giteeReady || entReady,
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
          <>
            <p>
              <strong>历史 Issue</strong>
              <span>{giteeReady ? "私人令牌已保存" : "未配置"}</span>
            </p>
            <p>
              <strong>企业工作项</strong>
              <span>
                {entReady
                  ? `企业 ID ${s.gitee_enterprise.enterprise_id}`
                  : "未配置"}
              </span>
            </p>
          </>
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
      if (
        s.llm_provider !== "ccswitch" &&
        (!s.llm_endpoint.trim() || !s.llm_model.trim())
      )
        throw new Error("请先填写接口地址和模型名称");
      // 先测后存：测试失败时一个字节都不写，避免把原本可用的连接改坏或悄悄关掉模型分析。
      const result = await api<{ ok: boolean; message?: string }>(
        "/model/test",
        "POST",
        {
          settings: { ...s, llm_enabled: true },
          api_key: apiKey.trim() || undefined,
        },
      );
      if (!result.ok)
        throw new Error(result.message || "模型未返回连接成功结果");
      const next = { ...s, llm_enabled: true };
      await api("/settings", "PUT", next);
      if (apiKey.trim())
        await api("/model/key", "POST", { api_key: apiKey.trim() });
      setS(next);
      setApiKey("");
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
      if (giteeToken.trim())
        await api("/gitee/token", "POST", { api_key: giteeToken.trim() });
      if (entToken.trim())
        await enterpriseApi("token", { api_key: entToken.trim() });
      await saveSettings(s);
      setGiteeToken("");
      setEntToken("");
      const status = await loadStatus();
      const configured =
        status.gitee.configured || status.enterprise.configured;
      go(4);
      setFeedback({
        tone: configured ? "ok" : "info",
        text: configured
          ? "Gitee 接入配置已保存，可在系统设置或企业工作项页面继续完善。"
          : "尚未填写 Gitee 凭据，可稍后在系统设置中补充。",
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
            <p>
              工具只读取本机已有的 AI 对话记录，不会修改源文件。所有 Agent
              都会列出，勾选要采集的来源；未检测到的展开后可填写路径。
            </p>
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
            <p>
              这是唯一必需的配置。接通后才会把对话归纳成工作项、估算工时；未配置时只能采集和手工录入。
            </p>
            <label>
              AI 分析供应商
              <select
                value={s.llm_provider}
                onChange={(e) => {
                  const provider = e.target
                    .value as Settings["llm_provider"];
                  setS({
                    ...s,
                    llm_provider: provider,
                    llm_endpoint: presets[provider].endpoint || s.llm_endpoint,
                    llm_model: presets[provider].model,
                    llm_protocol: presets[provider].protocol,
                  });
                  setApiKey("");
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
                <div className="setup-help">
                  <strong>CCSwitch 不需要单独填 Key</strong>
                  <ul>
                    <li>直接复用 CCSwitch 写入的 Claude Code 接口地址和凭据</li>
                    <li>如果 Claude Code 配置里没有模型名，在这里补一个</li>
                  </ul>
                </div>
                <label>
                  settings.json 路径（留空自动检测）
                  <input
                    value={s.ccswitch_config_path}
                    onChange={(e) =>
                      setS({ ...s, ccswitch_config_path: e.target.value })
                    }
                    placeholder="默认 ~/.claude/settings.json"
                  />
                </label>
                <label>
                  模型名称
                  <input
                    value={s.llm_model}
                    onChange={(e) => setS({ ...s, llm_model: e.target.value })}
                    placeholder="例如 claude-sonnet-4-5"
                  />
                </label>
              </>
            ) : (
              <>
                <div className="setup-help">
                  <strong>去哪里获取 API Key？</strong>
                  {s.llm_provider === "deepseek" && (
                    <>
                      <ul>
                        <li>登录 DeepSeek 开放平台</li>
                        <li>进入「API keys」页面，创建并复制 Key</li>
                      </ul>
                      <a
                        href="https://platform.deepseek.com/api_keys"
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开 DeepSeek API keys <ExternalLink size={13} />
                      </a>
                    </>
                  )}
                  {s.llm_provider === "glm" || s.llm_provider === "glm-coding" ? (
                    <>
                      <ul>
                        <li>登录智谱开放平台</li>
                        <li>进入「用户中心 → API Keys」创建并复制</li>
                        {s.llm_provider === "glm" && (
                          <li>
                            付费模型需账户有可用资源包；未充值可选免费的
                            GLM-4.5-Flash
                          </li>
                        )}
                        {s.llm_provider === "glm-coding" && (
                          <li>
                            这里使用 Coding Plan 专用接口；填标准 API 地址会提示“余额不足或无可用资源包”
                          </li>
                        )}
                      </ul>
                      <a
                        href="https://open.bigmodel.cn/usercenter/apikeys"
                        target="_blank"
                        rel="noreferrer"
                      >
                        打开智谱 API Keys <ExternalLink size={13} />
                      </a>
                    </>
                  ) : null}
                  {s.llm_provider === "custom" && (
                    <ul>
                      <li>填写兼容 OpenAI Chat Completions 的完整地址</li>
                      <li>远程地址必须使用 HTTPS；本机代理可用 127.0.0.1</li>
                    </ul>
                  )}
                </div>
                <label>
                  完整 API 接口 URL
                  <input
                    type="url"
                    value={s.llm_endpoint}
                    onChange={(e) =>
                      setS({ ...s, llm_endpoint: e.target.value })
                    }
                  />
                </label>
                <div className="form-grid">
                  <ModelSelect
                    key={s.llm_provider}
                    provider={s.llm_provider}
                    value={s.llm_model}
                    onChange={(model) => setS({ ...s, llm_model: model })}
                  />
                  <label>
                    接口格式
                    <select
                      value={s.llm_protocol}
                      onChange={(e) =>
                        setS({
                          ...s,
                          llm_protocol: e.target
                            .value as Settings["llm_protocol"],
                        })
                      }
                    >
                      <option value="openai">Chat Completions</option>
                      <option value="anthropic">Anthropic Messages</option>
                    </select>
                  </label>
                </div>
                {s.llm_provider === "glm" && (
                  <p className="muted">
                    GLM-5.x 系列需要账户有可用资源包；余额不足时可选免费的
                    GLM-4.5-Flash。
                  </p>
                )}
                <label>
                  <span className="setup-key-label">
                    <KeyRound size={14} /> API Key（本机加密保存）
                  </span>
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={apiKey}
                    onChange={(e) => setApiKey(e.target.value)}
                    placeholder="粘贴 API Key；留空表示沿用已保存的密钥"
                  />
                </label>
              </>
            )}
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
            <p className="muted">
              默认关闭：分析只生成候选，可在每日日报的「分析结果」中查看、修改后再导入。
            </p>
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
            <p className="muted">
              保存并测试只发送一句简短探测消息，不发送工作对话。测试成功后自动开启模型分析。
            </p>
          </>
        );
      case 2:
        return (
          <>
            <h3>
              <FolderGit2 size={18} /> 第 3 步 · Git 仓库（可选）
            </h3>
            <p>
              选择项目上级目录后自动扫描 Git 仓库，提交会作为工作证据的佐证。Git
              提交不会按条生成工作项，也不会推送到任何远端。
            </p>
            <GitSettings settings={s} onChange={setS} />
          </>
        );
      case 3:
        return (
          <>
            <h3>
              <GitBranch size={18} /> 第 4 步 · Gitee 接入（可选）
            </h3>
            <p>
              历史 Issue 用于学习标题风格，企业工作项用于把日报推送到公司项目。两项凭据分开保存，可以只配置其中一项。
            </p>
            <div className="gitee-cards">
              <div className="config-card">
                <h3>
                  <GitBranch size={16} /> 历史 Issue
                </h3>
                <p>
                  只读抓取你负责或创建的历史 Issue，不会创建、修改或关闭任何
                  Issue。
                </p>
                <div className="quick-help">
                  <span>
                    头像 → 账号设置 → 安全设置 → 私人令牌，勾选 projects
                    只读权限后生成并复制。
                  </span>
                  <a
                    href="https://gitee.com/profile/personal_access_tokens"
                    target="_blank"
                    rel="noreferrer"
                  >
                    获取私人令牌 <ExternalLink size={12} />
                  </a>
                </div>
                <label>
                  Gitee 私人令牌
                  <input
                    type="password"
                    autoComplete="new-password"
                    value={giteeToken}
                    onChange={(e) => setGiteeToken(e.target.value)}
                    placeholder={
                      giteeReady ? "已保存，留空表示保留" : "粘贴私人令牌"
                    }
                  />
                </label>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={s.gitee.enabled}
                    onChange={(e) =>
                      setS({
                        ...s,
                        gitee: { ...s.gitee, enabled: e.target.checked },
                      })
                    }
                  />
                  启用服务运行期间的定时同步
                </label>
              </div>
              <div className="config-card">
                <h3>
                  <Building2 size={16} /> 企业工作项
                </h3>
                <p>
                  把日报里审核过的工作项创建到公司企业项目，需要企业版成员账号和已开通的工作项
                  API 权限。
                </p>
                <div className="quick-help">
                  <span>
                    个人设置 → MCP 企业令牌，选择目标企业并授予项目读取、工作项创建权限。与私人令牌不通用。
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
                  MCP 企业令牌（与私人令牌分开保存）
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
                      onChange={(e) =>
                        setS({
                          ...s,
                          gitee_enterprise: {
                            ...s.gitee_enterprise,
                            enterprise_id: Number(e.target.value),
                          },
                        })
                      }
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
            </div>
          </>
        );
      default:
        return (
          <>
            <h3>
              <Sparkles size={18} /> 配置完成
            </h3>
            <p>
              下面是当前状态，点击每一项可以展开查看实际配置。未配置的可选项可以随时回到引导或系统设置补充。
            </p>
            <ul className="setup-summary">
              {(
                [
                  ["sources", sourcesReady, sourcesReady ? "已找到本地对话记录" : "未找到可采集目录"],
                  ["model", modelReady, modelReady ? modelStatus?.model || "已开启" : "未开启，AI 功能暂不可用"],
                  ["git", gitReady, gitReady ? `${s.git_roots.filter(Boolean).length} 个仓库` : "未配置（可选）"],
                  ["gitee", giteeReady || entReady, giteeReady && entReady ? "私人令牌与企业凭据已保存" : giteeReady ? "私人令牌已保存" : entReady ? `企业 ID ${s.gitee_enterprise.enterprise_id}` : "未配置（可选）"],
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
            <p className="muted">
              下一步：到「每日日报」选择日期并采集，然后点击 AI 分析全天对话。
            </p>
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
            <p>按步骤接通功能；每一步都可以跳过，之后随时回来补。</p>
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
