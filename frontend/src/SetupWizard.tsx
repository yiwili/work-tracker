import { useEffect, useState } from "react";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  Building2,
  Check,
  CheckCircle2,
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
import { ModelSelect, presets } from "./ModelSettings";

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

const stepKeys = [
  "sources",
  "model",
  "git",
  "gitee",
  "enterprise",
  "done",
] as const;
type StepKey = (typeof stepKeys)[number];

const sourceLabels: Record<string, string> = {
  codex: "Codex",
  claude_code: "Claude Code",
};

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
  const [gitText, setGitText] = useState(settings.git_roots.join("\n"));
  const [mapText, setMapText] = useState(
    Object.entries(settings.project_mappings)
      .map(([path, name]) => `${path}=${name}`)
      .join("\n"),
  );
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

  const sourcesReady = !!doctor?.paths.some((p) => p.exists);
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
    gitee: giteeReady,
    enterprise: entReady,
    done: false,
  };
  const hints: Record<StepKey, string> = {
    sources: sourcesReady ? "已就绪" : "待确认",
    model: modelReady ? "已开启" : "唯一必填",
    git: gitReady ? `${s.git_roots.filter(Boolean).length} 个仓库` : "可选",
    gitee: giteeReady ? "已配置" : "可选",
    enterprise: entReady ? "已配置" : "可选",
    done: "查看结果",
  };
  const titles: Record<StepKey, string> = {
    sources: "采集来源",
    model: "AI 模型",
    git: "Git 仓库",
    gitee: "Gitee 历史",
    enterprise: "企业工作项",
    done: "完成",
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
      await saveSettings(s);
      await loadStatus();
      go(1);
      setFeedback({
        tone: "ok",
        text: "采集来源已保存。Git 提交证据可在下一步添加。",
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
  const parseMappings = () => {
    const mappings: Record<string, string> = {};
    for (const raw of mapText.split("\n")) {
      const line = raw.trim();
      const at = line.indexOf("=");
      if (at <= 0) continue;
      const path = line.slice(0, at).trim(),
        name = line.slice(at + 1).trim();
      if (path && name) mappings[path] = name;
    }
    return mappings;
  };
  const saveGit = () =>
    run("正在保存仓库配置", async () => {
      const next = {
        ...s,
        git_roots: gitText
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
        project_mappings: parseMappings(),
      };
      await saveSettings(next);
      go(3);
      setFeedback({
        tone: "ok",
        text: "Git 仓库与项目映射已保存。提交只作为证据，不按条生成工作项。",
      });
    });
  const saveGitee = () =>
    run("正在保存 Gitee 凭据", async () => {
      if (giteeToken.trim())
        await api("/gitee/token", "POST", { api_key: giteeToken.trim() });
      await saveSettings(s);
      setGiteeToken("");
      const status = await loadStatus();
      const configured = !!giteeToken.trim() || !!status.gitee.configured;
      if (configured) go(4);
      setFeedback({
        tone: configured ? "ok" : "error",
        text: configured
          ? "Gitee 历史抓取配置已保存。"
          : "尚未保存 Token，历史 Issue 导入仍不可用。",
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
  const saveEnterprise = () =>
    run("正在保存企业配置", async () => {
      await saveSettings(s);
      await loadStatus();
      go(5);
      setFeedback({
        tone: "ok",
        text: "企业配置已保存；标题与项目映射可在「企业工作项」页面继续完善。",
      });
    });

  const pathText = (source: "codex" | "claude_code") =>
    s[source].paths.join("\n");
  const setPaths = (source: "codex" | "claude_code", value: string) =>
    setS({
      ...s,
      [source]: {
        ...s[source],
        paths: value
          .split("\n")
          .map((x) => x.trim())
          .filter(Boolean),
      },
    });

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
              工具只读取本机已有的 AI 对话记录，不会修改源文件。默认路径已经填好，确认目录存在即可；没有用到的来源可以关闭。
            </p>
            {doctor?.paths.length ? (
              <div className="setup-status">
                {doctor.paths.map((item) => (
                  <div
                    key={item.source + item.path}
                    className={item.exists ? "ok" : "warn"}
                  >
                    <span>{sourceLabels[item.source] || item.source}</span>
                    <code>{item.path}</code>
                    <em>{item.exists ? "已找到" : "路径不存在"}</em>
                  </div>
                ))}
              </div>
            ) : (
              <p className="muted">正在检查默认路径…</p>
            )}
            {(["codex", "claude_code"] as const).map((source) => (
              <div key={source} className="setup-source">
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={s[source].enabled}
                    onChange={(e) =>
                      setS({
                        ...s,
                        [source]: { ...s[source], enabled: e.target.checked },
                      })
                    }
                  />
                  采集 {sourceLabels[source]} 会话
                </label>
                <label>
                  {sourceLabels[source]} 记录目录（每行一个）
                  <textarea
                    rows={2}
                    value={pathText(source)}
                    onChange={(e) => setPaths(source, e.target.value)}
                  />
                </label>
              </div>
            ))}
            <p className="muted">
              路径不存在不会影响其他来源；Git 提交作为证据在第 3 步配置。
            </p>
            {footer({
              current: "sources",
              next: 1,
              nextLabel: "保存并继续",
              onNext: saveSources,
              skippable: true,
            })}
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
            {footer({
              current: "model",
              next: 2,
              nextLabel: "保存并测试连接",
              onNext: saveModel,
              skippable: true,
            })}
          </>
        );
      case 2:
        return (
          <>
            <h3>
              <FolderGit2 size={18} /> 第 3 步 · Git 仓库（可选）
            </h3>
            <p>
              填写本地仓库根目录后，采集会把当天提交作为工作证据的佐证。Git
              提交不会按条生成工作项，也不会推送到任何远端。
            </p>
            <label>
              仓库根目录（每行一个）
              <textarea
                rows={4}
                value={gitText}
                onChange={(e) => setGitText(e.target.value)}
                placeholder={"D:\\work\\project-a\nD:\\work\\project-b"}
              />
            </label>
            <label>
              项目名称映射（可选，每行「路径=项目名」）
              <textarea
                rows={3}
                value={mapText}
                onChange={(e) => setMapText(e.target.value)}
                placeholder={"D:\\work\\project-a=项目 A"}
              />
            </label>
            <p className="muted">
              只读取当前分支、提交摘要和文件增删行；不会读取完整 diff，也不会修改仓库。
            </p>
            {footer({
              current: "git",
              next: 3,
              nextLabel: "保存并继续",
              onNext: saveGit,
              skippable: true,
            })}
          </>
        );
      case 3:
        return (
          <>
            <h3>
              <GitBranch size={18} /> 第 4 步 · Gitee 历史 Issue（可选）
            </h3>
            <p>
              只读抓取你负责或创建的历史 Issue，用于学习标题风格。不会创建、修改或关闭任何
              Issue。
            </p>
            <div className="setup-help">
              <strong>获取私人令牌</strong>
              <ul>
                <li>登录 Gitee → 头像 → 账号设置</li>
                <li>左侧「安全设置 → 私人令牌」→「生成新令牌」</li>
                <li>勾选 projects 等只读权限，复制生成的令牌</li>
              </ul>
              <a
                href="https://gitee.com/profile/personal_access_tokens"
                target="_blank"
                rel="noreferrer"
              >
                打开 Gitee 私人令牌页面 <ExternalLink size={13} />
              </a>
            </div>
            <label>
              Gitee 私人令牌（GITEE_ACCESS_TOKEN）
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
            <p className="muted">
              企业工作项使用单独的企业版凭据，不使用这里的私人令牌。
            </p>
            {footer({
              current: "gitee",
              next: 4,
              nextLabel: "保存并继续",
              onNext: saveGitee,
              skippable: true,
            })}
          </>
        );
      case 4:
        return (
          <>
            <h3>
              <Building2 size={18} /> 第 5 步 · Gitee 企业工作项（可选）
            </h3>
            <p>
              把日报里审核过的工作项创建到公司企业项目。需要企业版成员账号，并且该企业已开通工作项 API
              权限。
            </p>
            <div className="setup-help">
              <strong>获取企业版 Token</strong>
              <ul>
                <li>登录 Gitee 后打开企业版令牌页面</li>
                <li>选择目标企业，生成具有项目读取与工作项创建权限的令牌</li>
                <li>令牌必须属于该企业成员；个人账号无法读取企业列表</li>
              </ul>
              <a
                href="https://gitee.com/profile/mcp_gitee_ent_access_tokens"
                target="_blank"
                rel="noreferrer"
              >
                打开企业版令牌页面 <ExternalLink size={13} />
              </a>
            </div>
            <label>
              企业版 Token（与私人令牌分开保存）
              <input
                type="password"
                autoComplete="new-password"
                value={entToken}
                onChange={(e) => setEntToken(e.target.value)}
                placeholder={
                  entStatus?.configured ? "已保存，留空表示保留" : "粘贴企业版 Token"
                }
              />
            </label>
            <div className="setup-inline">
              <button
                type="button"
                className="outline"
                disabled={!!busy || (!entToken && !entStatus?.configured)}
                onClick={saveEntToken}
              >
                保存并读取企业列表
              </button>
              <span className="muted">
                {entStatus?.configured ? "企业凭据已配置" : "尚未保存企业凭据"}
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
            <p className="muted">
              标题与项目映射、工作项类型和发布预览在左侧「企业工作项」页面完成；创建前始终需要预览并确认。
            </p>
            {footer({
              current: "enterprise",
              next: 5,
              nextLabel: "保存并查看结果",
              onNext: saveEnterprise,
              skippable: true,
            })}
          </>
        );
      default:
        return (
          <>
            <h3>
              <Sparkles size={18} /> 配置完成
            </h3>
            <p>下面是当前状态。未配置的可选项可以随时回到引导或偏好设置补充。</p>
            <ul className="setup-summary">
              {(
                [
                  ["sources", sourcesReady, sourcesReady ? "已找到本地对话记录" : "未找到可采集目录"],
                  ["model", modelReady, modelReady ? modelStatus?.model || "已开启" : "未开启，AI 功能暂不可用"],
                  ["git", gitReady, gitReady ? `${s.git_roots.filter(Boolean).length} 个仓库` : "未配置（可选）"],
                  ["gitee", giteeReady, giteeReady ? "私人令牌已保存" : "未配置（可选）"],
                  ["enterprise", entReady, entReady ? `企业 ID ${s.gitee_enterprise.enterprise_id}` : "未配置（可选）"],
                ] as [StepKey, boolean, string][]
              ).map(([key, ok, text]) => (
                <li key={key} className={ok ? "ok" : "todo"}>
                  {ok ? <CheckCircle2 size={17} /> : <Circle size={17} />}
                  <strong>{titles[key]}</strong>
                  <span>{text}</span>
                </li>
              ))}
            </ul>
            <p className="muted">
              下一步：到「每日日报」选择日期并采集，然后点击 AI 分析全天对话。
            </p>
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
          </>
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
            {renderStep()}
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
          </div>
        </div>
      </section>
    </div>
  );
}
