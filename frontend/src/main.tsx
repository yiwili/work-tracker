import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Bot,
  Building2,
  Check,
  CheckCheck,
  ChevronRight,
  Clock3,
  FileText,
  FolderGit2,
  GitMerge,
  Layers3,
  Link2,
  ListFilter,
  LoaderCircle,
  Plus,
  ScanLine,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Split,
  Trash2,
  X,
} from "lucide-react";
import type { GiteeSyncResult, PendingTask, Report, Settings, Task, WorkEvent } from "./types";
import "./style.css";
import { api } from "./api";
import { Conversations } from "./Conversations";
import { ModelSettings } from "./ModelSettings";
import { WorkflowSettings } from "./WorkflowSettings";
import { AllocationModal } from "./AllocationModal";
import { GiteePublishModal } from "./GiteePublishModal";
import { GiteeDedupModal } from "./GiteeDedupModal";
import { EnterpriseSettings } from "./EnterpriseSettings";
import { HistoryPanel } from "./HistoryPanel";
import { GitSettings } from "./GitSettings";
import { SetupWizard } from "./SetupWizard";
import { SourcePicker, sourceKeys, sourceLabels } from "./SourcePicker";
import { CollectDetailsModal } from "./CollectDetailsModal";
import { AnalysisResultsModal } from "./AnalysisResultsModal";
import { JobToast, type JobToastState } from "./JobToast";

const statuses: Record<string, string> = {
  requested: "待执行",
  attempted: "尝试中",
  changed: "已修改",
  verified: "已验证",
  delivered: "已交付",
};
const sources: Record<string, string> = {
  ...sourceLabels,
  workbuddy: "WorkBuddy（旧版合并记录）",
  qoder: "Qoder（旧版合并记录）",
  trae: "Trae（旧版合并记录）",
  git: "Git",
};
const settingsTabs = [
  { key: "sources", label: "数据来源", icon: FolderGit2 },
  { key: "time", label: "时间与隐私", icon: Clock3 },
  { key: "model", label: "模型分析", icon: Sparkles },
  { key: "operation", label: "操作设置", icon: SlidersHorizontal },
  { key: "workflow", label: "提示词与风格", icon: Bot },
  { key: "enterprise", label: "企业同步", icon: Building2 },
] as const;
type SettingsTab = (typeof settingsTabs)[number]["key"];
const kinds: Record<string, string> = {
  user_prompt: "用户请求",
  assistant_message: "AI 回复",
  tool_call: "工具调用",
  tool_result: "执行结果",
  shell_command: "命令执行",
  git_commit: "Git 提交",
  session_end: "会话结束",
};
const categories = [
  "开发",
  "Bug 修复",
  "测试",
  "代码审查",
  "运维",
  "部署",
  "技术研究",
  "需求沟通",
  "客户沟通",
  "会议",
  "方案设计",
  "文档",
  "日常事务",
  "其他",
];
const hours = (n: number) => Number((n / 60).toFixed(2)).toString();
function initialDay() {
  const value = new URLSearchParams(window.location.search).get("date");
  if (value && /^\d{4}-\d{2}-\d{2}$/.test(value)) {
    const parsed = new Date(value + "T12:00:00Z");
    if (
      Number.isFinite(parsed.getTime()) &&
      parsed.toISOString().slice(0, 10) === value
    )
      return value;
  }
  return today();
}
function today(tz = "Asia/Shanghai") {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: tz,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}
type Editor = { task?: Task; split?: boolean };
type Draft = {
  date: string;
  title: string;
  details: string;
  project: string;
  category: string;
  status: string;
  confirmed_minutes: number;
  time_locked: boolean;
  started_at: string;
  ended_at: string;
};
const fromTask = (t?: Task, day = ""): Draft => ({
  date: t?.date || day,
  title: t?.title || "",
  details: t?.details || "",
  project: t?.project || "",
  category: t?.category || "其他",
  status: t?.status || "requested",
  confirmed_minutes: t?.confirmed_minutes || 0,
  time_locked: t?.time_locked || false,
  started_at: t?.started_at ? toLocal(t.started_at) : "",
  ended_at: t?.ended_at ? toLocal(t.ended_at) : "",
});
function toLocal(iso: string) {
  const d = new Date(iso);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
function payload(d: Draft) {
  return {
    ...d,
    project: d.project || "未归属项目",
    started_at: d.started_at ? new Date(d.started_at).toISOString() : null,
    ended_at: d.ended_at ? new Date(d.ended_at).toISOString() : null,
  };
}

function App() {
  const [page, setPage] = useState("daily"),
    [day, setDay] = useState(initialDay),
    [report, setReport] = useState<Report | null>(null),
    [events, setEvents] = useState<WorkEvent[]>([]),
    [settings, setSettings] = useState<Settings | null>(null);
  const [busy, setBusy] = useState(""),
    [showBusy, setShowBusy] = useState(false),
    [collectProgress, setCollectProgress] = useState(""),
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [editor, setEditor] = useState<Editor | null>(null),
    [allocationOpen, setAllocationOpen] = useState(false),
    [giteeOpen, setGiteeOpen] = useState(false),
    [giteeDedupOpen, setGiteeDedupOpen] = useState(false),
    [giteeSyncResult, setGiteeSyncResult] = useState<GiteeSyncResult | null>(null),
    [setupOpen, setSetupOpen] = useState(false),
    [settingsEpoch, setSettingsEpoch] = useState(0),
    [settingsTab, setSettingsTab] = useState<SettingsTab>("sources"),
    [proof, setProof] = useState<Task | null>(null),
    [selected, setSelected] = useState<string[]>([]);
  const [collecting, setCollecting] = useState(false),
    [analyzing, setAnalyzing] = useState(false),
    [collectDay, setCollectDay] = useState<string | null>(null),
    [analysisView, setAnalysisView] = useState<{
      day: string;
      pending: PendingTask[];
      stale: boolean;
    } | null>(null),
    [toast, setToast] = useState<JobToastState | null>(null);
  const [source, setSource] = useState(""),
    [sessionFilter, setSessionFilter] = useState(""),
    [project, setProject] = useState(""),
    [taskFilter, setTaskFilter] = useState(""),
    [search, setSearch] = useState("");
  const dayRef = useRef(day);
  dayRef.current = day;
  const refresh = async (d = day) => {
    const [r, e, s] = await Promise.all([
      api<Report>("/reports/" + d),
      api<WorkEvent[]>("/events?date=" + d),
      api<Settings>("/settings"),
    ]);
    setSettings(s);
    // A background job for an older date must not overwrite the date now on screen.
    if (dayRef.current !== d) return;
    setReport(r);
    setEvents(e);
    setSelected([]);
  };
  useEffect(() => {
    const url = new URL(window.location.href);
    url.searchParams.set("date", day);
    window.history.replaceState(null, "", url);
    let active = true;
    setError("");
    setBusy("正在读取日报");
    Promise.all([
      api<Report>("/reports/" + day),
      api<WorkEvent[]>("/events?date=" + day),
      api<Settings>("/settings"),
    ])
      .then(([r, e, s]) => {
        if (active) {
          setReport(r);
          setEvents(e);
          setSettings(s);
          setSelected([]);
        }
      })
      .catch((e) => active && setError(e.message))
      .finally(() => active && setBusy(""));
    return () => {
      active = false;
    };
  }, [day]);
  useEffect(() => {
    if (!busy) {
      setShowBusy(false);
      return;
    }
    const timer = window.setTimeout(() => setShowBusy(true), 220);
    return () => window.clearTimeout(timer);
  }, [busy]);
  useEffect(() => {
    if (!report?.conversations.running) return;
    const timer = window.setInterval(() => {
      api<Report>("/reports/" + day)
        .then(setReport)
        .catch(() => {});
    }, 4000);
    return () => window.clearInterval(timer);
  }, [day, report?.conversations.running]);
  const run = async (label: string, action: () => Promise<void>) => {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      setBusy("");
    }
  };
  const collect = async () => {
    if (collecting || analyzing) return;
    const jobDay = day;
    setCollecting(true);
    setError("");
    setNotice("");
    setCollectProgress("正在读取本地日志，首次采集会扫描全部历史，请稍候");
    const timer = window.setInterval(() => {
      api<{
        running: boolean;
        source?: string;
        files_done?: number;
        files_total?: number;
        new_events?: number;
      }>("/collect/status?date=" + jobDay)
        .then((s) => {
          if (!s.running) return;
          const label = s.source ? sources[s.source] || s.source : "准备中";
          setCollectProgress(
            s.files_total
              ? `已读取 ${label} ${s.files_done}/${s.files_total} 个文件，新增 ${s.new_events ?? 0} 条活动记录`
              : `正在读取 ${label}`,
          );
        })
        .catch(() => {});
    }, 800);
    try {
      const c = await api<{
        new_events: number;
        git_events?: number;
        sources?: {
          source: string;
          enabled: boolean;
          files: number;
          new_events: number;
        }[];
        warnings: string[];
      }>("/collect?date=" + jobDay, "POST", {});
      await refresh(jobDay);
      const git = c.git_events ?? 0;
      const chat = Math.max(0, c.new_events - git);
      const pieces = [
        c.new_events === 0
          ? "这一天已经采集过了，没有新的活动记录"
          : `新增 ${c.new_events} 条活动记录`,
        `Git 提交 ${git} 条 / 对话 ${chat} 条`,
      ];
      // 采集路径不存在时对话永远是 0，直接说清楚，别让人以为点漏了。
      const unreadable = (c.sources || []).filter((s) => !s.files).length;
      if (unreadable && chat === 0)
        pieces.push(
          `${unreadable} 个对话目录没读到文件，请到系统设置检查采集路径`,
        );
      pieces.push(...c.warnings);
      if (c.new_events > 0) pieces.push("可继续 AI 分析归纳实际工作任务");
      setToast({
        title: "采集完成",
        message: pieces.join("；") + "。",
        actionLabel: "查看采集详情",
        onAction: () => {
          setToast(null);
          setCollectDay(jobDay);
        },
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      window.clearInterval(timer);
      setCollectProgress("");
      setCollecting(false);
    }
  };
  const analyzeDay = async () => {
    if (analyzing || collecting) return;
    const jobDay = day;
    setAnalyzing(true);
    setError("");
    setNotice("");
    try {
      const a = await api<{
        created: number;
        archived: number;
        pending: number;
        pending_tasks: PendingTask[];
        auto_imported: boolean;
        warnings: string[];
        analysis_required?: boolean;
        usage?: null | {
          total_tokens: number;
          prompt_tokens: number;
          completion_tokens: number;
          estimated: boolean;
        };
      }>("/analyze?date=" + jobDay, "POST", {});
      await refresh(jobDay);
      if (a.analysis_required) {
        setNotice(a.warnings.join("；"));
        return;
      }
      const usage = a.usage
        ? `本次模型用量${a.usage.estimated ? "约 " : " "}${a.usage.total_tokens.toLocaleString("zh-CN")} tokens（输入 ${a.usage.prompt_tokens.toLocaleString("zh-CN")} / 输出 ${a.usage.completion_tokens.toLocaleString("zh-CN")}）。`
        : "";
      if (!a.created && !a.pending && !a.archived) {
        setNotice(`AI 分析已完成，未发现可归纳的工作项。${usage}`);
        return;
      }
      const warnings = a.warnings.length
        ? ` 提示：${a.warnings.join("；")}`
        : "";
      setToast({
        title: "AI 分析完成",
        message: a.auto_imported
          ? `已归纳 ${a.created} 项工作并写入今日清单，替换 ${a.archived} 项未审核草稿。${usage}${warnings}`
          : `已生成 ${a.pending} 条候选工作项，尚未写入今日清单；确认后即可导入。${usage}${warnings}`,
        actionLabel: "前往查看",
        onAction: () => {
          setToast(null);
          if (a.auto_imported) {
            setPage("daily");
          } else {
            setAnalysisView({
              day: jobDay,
              pending: a.pending_tasks || [],
              stale: false,
            });
          }
        },
      });
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setAnalyzing(false);
    }
  };
  const openAnalysisResults = () => {
    if (!report) return;
    setAnalysisView({
      day: report.date,
      pending: report.conversations.analysis?.pending_tasks || [],
      stale: report.conversations.stale,
    });
  };
  const moveDay = (delta: number) => {
    const d = new Date(day + "T12:00:00");
    d.setDate(d.getDate() + delta);
    setDay(
      `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`,
    );
  };
  const merge = () =>
    run("正在合并任务", async () => {
      await api("/tasks/merge", "POST", { ids: selected });
      await refresh();
      setNotice("任务已合并，确认工时已保留；请核对重复投入。");
    });
  const remove = (t: Task) => {
    if (window.confirm(`删除“${t.title}”？重新分析时会保留此删除决定。`))
      void run("正在删除", async () => {
        await api("/tasks/" + t.id, "DELETE");
        await refresh();
      });
  };
  const filtered = events.filter(
    (e) =>
      (!source || e.source === source) &&
      (!sessionFilter || e.session_id === sessionFilter) &&
      (!project || e.project === project) &&
      (!taskFilter ||
        report?.tasks
          .find((t) => t.id === taskFilter)
          ?.evidence.some((p) => p.id === e.id)) &&
      (!search || e.summary.toLowerCase().includes(search.toLowerCase())),
  );
  const exportReport = () =>
    run("正在导出日报", async () => {
      const response = await fetch("/api/reports/" + day + "/export.md");
      if (!response.ok) throw new Error("导出失败");
      const url = URL.createObjectURL(await response.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `worklog-${day}.md`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      setNotice("Markdown 日报已导出。");
    });
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="window-controls" aria-hidden="true">
          <span className="dot close" />
          <span className="dot minimize" />
          <span className="dot expand" />
        </div>
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          <span>
            拾时<span className="brand-en">WORK TRACKER</span>
          </span>
        </a>
        <nav>
          {(
            [
              ["daily", "每日日报", FileText],
              ["timeline", "活动时间线", Activity],
              ["enterprise", "企业工作项", Building2],
              ["settings", "系统设置", Settings2],
            ] as const
          ).map(([key, label, Icon]) => (
            <button
              key={key as string}
              className={page === key ? "nav-item active" : "nav-item"}
              onClick={() => setPage(key as string)}
            >
              <Icon size={19} />
              {label as string}
              {page === key && <span className="nav-dot" />}
            </button>
          ))}
        </nav>
        <button
          className={
            "setup-launch" + (settings && !settings.llm_enabled ? " attention" : "")
          }
          onClick={() => setSetupOpen(true)}
        >
          <Sparkles size={18} />
          <span>配置引导</span>
          <ChevronRight size={15} />
        </button>
      </aside>
      <main>
        <div className="content">
          <div className="heading">
            <div>
              <h1>
                {page === "daily"
                  ? "每日日报"
                  : page === "timeline"
                    ? "活动时间线"
                    : page === "enterprise"
                      ? "企业工作项"
                      : "系统设置"}
              </h1>
            </div>
            {(page === "daily" || page === "timeline") && (
              <div className="heading-actions">
                <button
                  className="outline"
                  disabled={!report}
                  onClick={() => setCollectDay(day)}
                  title="查看当天已采集的对话会话与 Git 提交；可勾选 Git 提交直接添加为工作项"
                >
                  <FolderGit2 size={16} />
                  查看采集详情
                </button>
                <button
                  className="primary"
                  disabled={collecting || analyzing}
                  onClick={collect}
                  title="读取本地对话日志，并读取已配置仓库里你自己那天的 Git 提交"
                >
                  {collecting ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : (
                    <ScanLine size={17} />
                  )}
                  {collecting ? "正在采集…" : "采集当天数据"}
                </button>
              </div>
            )}
          </div>
          {collectProgress && (
            <p className="muted" role="status">
              {collectProgress}
            </p>
          )}
          {error && (
            <div className="alert error" role="alert">
              {error}
              <button onClick={() => setError("")} aria-label="关闭错误">
                <X size={16} />
              </button>
            </div>
          )}
          {notice && (
            <div className="alert" role="status">
              {notice}
              <button onClick={() => setNotice("")} aria-label="关闭提示">
                <X size={16} />
              </button>
            </div>
          )}
          {busy && showBusy && (
            <div className="busy" role="status">
              <LoaderCircle className="spin" size={14} />
              {busy}…
            </div>
          )}
          {page !== "settings" && (
            <div className="datebar">
              <div className="date-control">
                <button
                  aria-label="前一天"
                  disabled={!!busy}
                  onClick={() => moveDay(-1)}
                >
                  <ArrowLeft size={16} />
                </button>
                <input
                  aria-label="工作日期"
                  type="date"
                  value={day}
                  disabled={!!busy}
                  onChange={(e) => e.target.value && setDay(e.target.value)}
                />
                <button
                  aria-label="后一天"
                  disabled={!!busy}
                  onClick={() => moveDay(1)}
                >
                  <ArrowRight size={16} />
                </button>
                <button
                  className="today"
                  disabled={!!busy}
                  onClick={() => setDay(today(settings?.timezone))}
                >
                  今天
                </button>
              </div>
              <span className="day-status">
                <span
                  className={report?.confirmed ? "green-dot" : "amber-dot"}
                />
                {report?.confirmed ? "日报已确认" : "草稿 · 待审核"}
              </span>
            </div>
          )}
          {page === "daily" && report && (
            <>
              <div className="stats">
                <div className="stat time-stat">
                  <div className="stat-label">
                    已分配工时 <Clock3 size={16} />
                  </div>
                  <div className="stat-number">
                    {hours(report.total_minutes)}
                    <span> / {hours(report.target_minutes)} h</span>
                  </div>
                  <div className="mini-track">
                    <i
                      style={{
                        width:
                          Math.min(
                            100,
                            (report.total_minutes / report.target_minutes) *
                              100,
                          ) + "%",
                      }}
                    />
                  </div>
                  <small>
                    {report.remaining_minutes > 0
                      ? `还需分配 ${hours(report.remaining_minutes)} 小时`
                      : report.remaining_minutes < 0
                        ? `超出目标 ${hours(-report.remaining_minutes)} 小时`
                        : "已达到目标，等待最终审核"}
                  </small>
                </div>
                <div className="stat">
                  <div className="stat-label">
                    {report.legacy_count ? "待替换的候选记录" : "今日工作任务"}{" "}
                    <CheckCheck size={17} />
                  </div>
                  <div className="stat-number">
                    {report.tasks.length}
                    <span> 项</span>
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-label">
                    关联项目 <FolderGit2 size={17} />
                  </div>
                  <div className="stat-number">
                    {report.projects}
                    <span> 个</span>
                  </div>
                </div>
                <div className="stat">
                  <div className="stat-label">
                    AI 数据来源 <Sparkles size={17} />
                  </div>
                  <div className="stat-number">
                    {
                      report.conversations.sources.filter(
                        (s) =>
                          s.selected && s.source !== "git" && s.sessions > 0,
                      ).length
                    }
                    <span> 种</span>
                  </div>
                </div>
              </div>
              {settings && (
                <Conversations
                  overview={report.conversations}
                  settings={settings}
                  busy={collecting || analyzing}
                  analyzing={analyzing}
                  pendingCount={
                    report.conversations.analysis?.pending_tasks?.length || 0
                  }
                  legacyCount={report.legacy_count}
                  onAnalyze={analyzeDay}
                  onSettings={() => setPage("settings")}
                  onViewPending={openAnalysisResults}
                  onSources={async (values) => {
                    const prev = settings;
                    const next: Settings = {
                      ...settings,
                      analysis_sources: values,
                    };
                    for (const key of sourceKeys)
                      next[key] = {
                        ...next[key],
                        enabled: values.includes(key),
                      };
                    setSettings(next);
                    setError("");
                    try {
                      await api("/settings", "PUT", next);
                      await refresh();
                    } catch (e) {
                      setSettings(prev);
                      setError((e as Error).message);
                    }
                  }}
                  onView={(source, session) => {
                    setSource(source);
                    setSessionFilter(session);
                    setTaskFilter("");
                    setProject("");
                    setSearch("");
                    setPage("timeline");
                  }}
                />
              )}
              <section className="task-section">
                <div className="section-head">
                  <div>
                    <h2>
                      今日工作清单{" "}
                      <span className="count">{report.tasks.length}</span>
                    </h2>
                  </div>
                  <div className="actions">
                    <button
                      className="outline"
                      disabled={!!busy}
                      onClick={async () => {
                        setBusy("正在同步 Gitee 未完成工作项并去重…");
                        try {
                          const res = await api<GiteeSyncResult>(
                            `/reports/${day}/sync-gitee`,
                            "POST",
                            {}
                          );
                          setGiteeSyncResult(res);
                          if (res.ambiguous.length > 0) {
                            setGiteeDedupOpen(true);
                          } else if (res.auto_matched.length > 0) {
                            setNotice(
                              `已自动通过并关联 ${res.auto_matched.length} 项云端未完成工作项，保留本地工时`
                            );
                            await refresh();
                          } else {
                            setNotice(
                              `已检测 ${res.total_remote} 项云端工作项，未发现可匹配的未完成项（${res.discarded_count} 项低置信度已丢弃）`
                            );
                          }
                        } catch (e) {
                          setError((e as Error).message);
                        } finally {
                          setBusy("");
                        }
                      }}
                    >
                      <Link2 size={15} />
                      同步 Gitee 未完成项
                    </button>
                    <button
                      className="outline"
                      disabled={!!busy || !report.tasks.length}
                      onClick={() => setGiteeOpen(true)}
                    >
                      提交到 Gitee
                      {selected.length ? `（${selected.length}项）` : ""}
                    </button>
                    <button
                      className="outline"
                      disabled={
                        !!busy ||
                        !!report.conversations.running ||
                        !settings?.llm_enabled ||
                        !report.tasks.length
                      }
                      onClick={() => setAllocationOpen(true)}
                    >
                      <Sparkles size={16} />
                      AI 估算并分配 {hours(report.target_minutes)}h
                    </button>
                    {selected.length >= 2 && (
                      <button disabled={!!busy} onClick={merge}>
                        <GitMerge size={15} />
                        合并 {selected.length} 项
                      </button>
                    )}
                    <button
                      className="outline"
                      disabled={!!busy}
                      onClick={() => setEditor({})}
                    >
                      <Plus size={17} />
                      添加人工事项
                    </button>
                  </div>
                </div>
                {report.tasks.length === 0 ? (
                  <div className="empty">
                    <div className="empty-icon">
                      <FileText size={28} />
                      <span>
                        <Plus size={12} />
                      </span>
                    </div>
                    <h3>今天的工作，等你来记录</h3>
                    <button
                      className="outline"
                      disabled={!!busy}
                      onClick={() => setEditor({})}
                    >
                      <Plus size={16} />
                      记录第一项工作
                    </button>
                  </div>
                ) : (
                  <div className="table-wrap">
                    <table>
                      <thead>
                        <tr>
                          <th className="checkbox-cell">
                            <input
                              aria-label="选择全部任务"
                              type="checkbox"
                              checked={selected.length === report.tasks.length}
                              onChange={(e) =>
                                setSelected(
                                  e.target.checked
                                    ? report.tasks.map((t) => t.id)
                                    : [],
                                )
                              }
                            />
                          </th>
                          <th>任务 / 工作内容</th>
                          <th>项目与分类</th>
                          <th>完成情况</th>
                          <th>工时</th>
                          <th>操作</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.tasks.map((t) => (
                          <tr key={t.id}>
                            <td>
                              <input
                                type="checkbox"
                                aria-label={"选择 " + t.title}
                                checked={selected.includes(t.id)}
                                onChange={(e) =>
                                  setSelected(
                                    e.target.checked
                                      ? [...selected, t.id]
                                      : selected.filter((id) => id !== t.id),
                                  )
                                }
                              />
                            </td>
                            <td className="task-main">
                              <button
                                className="task-title"
                                onClick={() => setEditor({ task: t })}
                              >
                                {t.title}
                              </button>
                              {t.details && <p>{t.details}</p>}
                              <div className="task-meta">
                                <span
                                  className={
                                    t.origin === "manual" ? "tag manual" : "tag"
                                  }
                                >
                                  {t.origin === "manual"
                                    ? "人工补录"
                                    : "AI / Git 提取"}
                                </span>
                                <button onClick={() => setProof(t)}>
                                  {t.evidence.length} 条证据{" "}
                                  <ArrowUpRight size={12} />
                                </button>
                              </div>
                            </td>
                            <td>
                              <span className="project-name">{t.project}</span>
                              <span className="category">{t.category}</span>
                            </td>
                            <td>
                              <span className={"state " + t.status}>
                                {statuses[t.status]}
                              </span>
                            </td>
                            <td className="hours">
                              <span className="tag">
                                {t.time_locked
                                  ? "固定工时"
                                  : t.time_source === "ai_estimate"
                                    ? "AI 分配"
                                    : "待分配"}
                              </span>
                              <strong title={t.time_reason}>
                                {hours(t.confirmed_minutes)} <small>h</small>
                              </strong>
                              {t.time_reason && (
                                <details>
                                  <summary>估算依据</summary>
                                  <p>{t.time_reason}</p>
                                </details>
                              )}
                            </td>
                            <td>
                              <div className="row-actions">
                                <button
                                  aria-label={"编辑 " + t.title}
                                  onClick={() => setEditor({ task: t })}
                                >
                                  <FileText size={15} />
                                </button>
                                <button
                                  aria-label={"拆分 " + t.title}
                                  onClick={() =>
                                    setEditor({ task: t, split: true })
                                  }
                                >
                                  <Split size={15} />
                                </button>
                                <button
                                  aria-label={"删除 " + t.title}
                                  disabled={!!busy}
                                  onClick={() => remove(t)}
                                >
                                  <Trash2 size={15} />
                                </button>
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
              {report.warnings.length > 0 && (
                <div className="review-warning">
                  <strong>待审核提示</strong>
                  {[...new Set(report.warnings)].map((w, i) => (
                    <p key={i}>{w}</p>
                  ))}
                  <small>
                    可在清单中勾选重复任务后合并；合并会保留确认工时，请人工核对。
                  </small>
                </div>
              )}
              {report.time_allocation && (
                <div className="allocation-note">
                  <strong>
                    {report.time_allocation.stale
                      ? "工作项已变化，工时建议待重新审核"
                      : "AI工时分配草稿"}
                  </strong>
                  <p>
                    固定 {hours(report.time_allocation.fixed_minutes)}h ·
                    目标分配 {hours(report.time_allocation.distributed_minutes)}
                    h · AI独立估算{" "}
                    {hours(report.time_allocation.independent_estimate_minutes)}
                    h
                  </p>
                  {report.time_allocation.warnings.map((w, i) => (
                    <p key={i}>{w}</p>
                  ))}
                </div>
              )}
              <section className="confirmation">
                <div className="confirmation-copy">
                  <span
                    className={
                      "confirmation-icon " + (report.confirmed ? "done" : "")
                    }
                  >
                    <Clock3 size={22} />
                  </span>
                  <div>
                    <h3>
                      {report.confirmed
                        ? "这一天，已经记录妥当。"
                        : report.remaining_minutes === 0
                          ? "工时已分配完成，请核对并确认。"
                          : report.remaining_minutes > 0
                            ? `距离完整日报，还差 ${hours(report.remaining_minutes)}h`
                            : `当前工时超出 ${hours(-report.remaining_minutes)}h`}
                    </h3>
                  </div>
                </div>
                <div className="actions">
                  <button
                    className="outline"
                    disabled={!!busy}
                    onClick={exportReport}
                  >
                    <ArrowDownToLine size={16} />
                    导出 Markdown
                  </button>
                  <button
                    className="primary"
                    disabled={
                      !!busy ||
                      report.remaining_minutes !== 0 ||
                      report.confirmed
                    }
                    onClick={() =>
                      run("正在确认日报", async () => {
                        await api("/reports/" + day + "/confirm", "POST", {});
                        await refresh();
                        setNotice("日报已确认。");
                      })
                    }
                  >
                    <Check size={17} />
                    {report.confirmed
                      ? "已确认"
                      : `确认 ${hours(report.target_minutes)}h 日报`}
                  </button>
                </div>
              </section>
            </>
          )}
          {page === "timeline" && (
            <>
              <div className="timeline-toolbar">
                <div className="timeline-search">
                  <ListFilter size={18} />
                  <input
                    aria-label="搜索时间线"
                    placeholder="搜索活动内容…"
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                  />
                </div>
                <div className="timeline-filters">
                  <select
                    aria-label="来源筛选"
                    value={source}
                    onChange={(e) => setSource(e.target.value)}
                  >
                    <option value="">全部来源</option>
                    {Object.entries(sources).map(([v, l]) => (
                      <option key={v} value={v}>
                        {l}
                      </option>
                    ))}
                  </select>
                  <select
                    aria-label="会话筛选"
                    value={sessionFilter}
                    onChange={(e) => setSessionFilter(e.target.value)}
                  >
                    <option value="">全部会话</option>
                    {report?.conversations.sessions
                      .filter((s) => !source || s.source === source)
                      .map((s) => (
                        <option key={s.source + s.id} value={s.id}>
                          {s.preview.slice(0, 25) || s.id}
                        </option>
                      ))}
                  </select>
                  <select
                    aria-label="项目筛选"
                    value={project}
                    onChange={(e) => setProject(e.target.value)}
                  >
                    <option value="">全部项目</option>
                    {[...new Set(events.map((e) => e.project))].map((p) => (
                      <option key={p}>{p}</option>
                    ))}
                  </select>
                  <select
                    aria-label="任务筛选"
                    value={taskFilter}
                    onChange={(e) => setTaskFilter(e.target.value)}
                  >
                    <option value="">全部任务</option>
                    {report?.tasks.map((t) => (
                      <option key={t.id} value={t.id}>
                        {t.title}
                      </option>
                    ))}
                  </select>
                  <span className="timeline-count">{filtered.length} 条活动</span>
                </div>
              </div>
              <section className="timeline">
                {filtered.length === 0 ? (
                  <div className="empty">
                    <Activity size={30} />
                    <h3>暂无匹配的活动记录</h3>
                    <p>点击“采集当天数据”读取本地对话与 Git 提交，或调整筛选条件。</p>
                  </div>
                ) : (
                  filtered.map((e) => (
                    <article className="timeline-event" key={e.id}>
                      <time>
                        {new Date(e.occurred_at).toLocaleTimeString("zh-CN", {
                          timeZone: settings?.timezone,
                          hour: "2-digit",
                          minute: "2-digit",
                          second: "2-digit",
                        })}
                      </time>
                      <div className={"timeline-dot " + e.source} />
                      <div className="event-card">
                        <div>
                          <span className="tag">
                            {sources[e.source] || e.source}
                          </span>
                          <strong>{kinds[e.kind] || e.kind}</strong>
                          <span className="event-project">{e.project}</span>
                        </div>
                        <pre>{e.summary}</pre>
                        {Boolean(e.metadata.changes) && (
                          <pre>{String(e.metadata.changes)}</pre>
                        )}
                        <details>
                          <summary>来源引用</summary>
                          <code>{e.raw_ref}</code>
                          <p>工作目录：{e.cwd || "未记录"}</p>
                          <p>会话：{e.session_id}</p>
                          <p>UTC：{e.occurred_at}</p>
                        </details>
                      </div>
                    </article>
                  ))
                )}
              </section>
            </>
          )}
          {page === "enterprise" && settings && report && (
            <>
              <section className="settings-section enterprise-publish-entry">
                <h2>
                  <Building2 size={20} />
                  推送到 Gitee 企业工作项
                </h2>
                <p>共 {report.tasks.length} 个本地工作项</p>
                <button
                  className="primary"
                  disabled={!!busy || !report.tasks.length}
                  onClick={() => setGiteeOpen(true)}
                >
                  预览并推送工作项
                  {selected.length ? `（${selected.length}项）` : ""}
                </button>
                {!report.tasks.length && (
                  <p>
                    当天暂无工作项，请切换日期，或先到每日日报生成、添加事项。
                  </p>
                )}
              </section>
              <EnterpriseSettings settings={settings} onChange={setSettings} />
            </>
          )}
          {page === "settings" && settings && (
            <SettingsForm
              key={settingsEpoch}
              initial={settings}
              busy={!!busy}
              tab={settingsTab}
              onTab={setSettingsTab}
              onImported={() => {
                void refresh();
              }}
              onSave={async (s, apiKey, test) => {
                setBusy(test ? "正在保存并测试模型连接" : "正在保存设置");
                setError("");
                setNotice("");
                let saved = false;
                try {
                  await api("/settings", "PUT", s);
                  if (apiKey.trim())
                    await api("/model/key", "POST", { api_key: apiKey.trim() });
                  saved = true;
                  if (test) {
                    setBusy("正在测试模型连接");
                    const result = await api<{ ok: boolean; message?: string }>(
                      "/model/test",
                      "POST",
                      {},
                    );
                    if (!result.ok)
                      throw new Error(
                        result.message || "模型未返回连接成功结果",
                      );
                  }
                  let message = test
                    ? "设置已保存，真实模型连接测试成功；未发送工作对话。"
                    : "设置已保存。排除规则影响后续采集和分析，已有本地记录仍保留。";
                  try {
                    await refresh();
                  } catch {
                    message += " 页面数据刷新失败，请刷新页面查看已保存设置。";
                  }
                  setNotice(message);
                  return { ok: true, saved: true, message };
                } catch (e) {
                  const message =
                    (test
                      ? saved
                        ? "设置已保存，连接测试失败："
                        : "设置保存未完成，尚未测试连接："
                      : "设置保存未完成：") + (e as Error).message;
                  setError(message);
                  return { ok: false, saved, message };
                } finally {
                  setBusy("");
                }
              }}
            />
          )}
        </div>
      </main>
      {giteeOpen && (
        <GiteePublishModal
          day={day}
          taskIds={selected}
          onClose={() => setGiteeOpen(false)}
          onSettings={() => {
            setGiteeOpen(false);
            setPage("enterprise");
          }}
          onApplied={refresh}
        />
      )}
      {giteeDedupOpen && giteeSyncResult && (
        <GiteeDedupModal
          day={day}
          syncResult={giteeSyncResult}
          onClose={() => setGiteeDedupOpen(false)}
          onApplied={async () => {
            await refresh();
          }}
        />
      )}
      {setupOpen && settings && (
        <SetupWizard
          settings={settings}
          onClose={() => setSetupOpen(false)}
          onSaved={async () => {
            await refresh();
            setSettingsEpoch((value) => value + 1);
          }}
        />
      )}
      {allocationOpen && report && (
        <AllocationModal
          report={report}
          onClose={() => setAllocationOpen(false)}
          onApplied={async (message) => {
            await refresh();
            setNotice(message);
          }}
        />
      )}
      {editor && (
        <TaskEditor
          editor={editor}
          day={day}
          step={settings?.step_minutes || 15}
          onClose={() => setEditor(null)}
          onSave={async (parts) => {
            setError("");
            if (editor.split) {
              await api("/tasks/split", "POST", {
                id: editor.task!.id,
                parts: parts.map(payload),
              });
            } else {
              await api(
                editor.task ? "/tasks/" + editor.task.id : "/tasks",
                editor.task ? "PATCH" : "POST",
                payload(parts[0]),
              );
            }
            setEditor(null);
            await refresh();
          }}
        />
      )}
      {proof && (
        <div className="modal-overlay">
          <section
            className="modal evidence-modal"
            role="dialog"
            aria-modal="true"
            aria-label="任务证据"
          >
            <div className="modal-head">
              <div>
                <h2>{proof.title}</h2>
              </div>
              <button aria-label="关闭证据" onClick={() => setProof(null)}>
                <X size={20} />
              </button>
            </div>
            <p>
              {proof.evidence_level} · {proof.confidence}
            </p>
            {proof.evidence.length === 0 ? (
              <div className="empty">
                <p>人工事项未关联 AI 日志，以用户填写内容为依据。</p>
              </div>
            ) : (
              proof.evidence.map((e) => (
                <div className="proof-item" key={e.id}>
                  <span className="tag">{sources[e.source]}</span>{" "}
                  <strong>{kinds[e.kind]}</strong>
                  <time>
                    {new Date(e.occurred_at).toLocaleString("zh-CN", {
                      timeZone: settings?.timezone,
                    })}
                  </time>
                  <pre>{e.summary}</pre>
                  <code>{e.raw_ref}</code>
                </div>
              ))
            )}
          </section>
        </div>
      )}
      {collectDay && (
        <CollectDetailsModal
          day={collectDay}
          timezone={settings?.timezone || "Asia/Shanghai"}
          onClose={() => setCollectDay(null)}
          onAdded={async (created) => {
            await refresh(collectDay);
            if (created) setNotice(`已从 Git 提交添加 ${created} 个工作项。`);
          }}
        />
      )}
      {analysisView && (
        <AnalysisResultsModal
          day={analysisView.day}
          pending={analysisView.pending}
          stale={analysisView.stale}
          onClose={() => setAnalysisView(null)}
          onImported={async () => {
            await refresh(analysisView.day);
          }}
        />
      )}
      {toast && (
        <JobToast toast={toast} onClose={() => setToast(null)} />
      )}
    </div>
  );
}

function TaskEditor({
  editor,
  day,
  step,
  onClose,
  onSave,
}: {
  editor: Editor;
  day: string;
  step: number;
  onClose: () => void;
  onSave: (parts: Draft[]) => Promise<void>;
}) {
  const [parts, setParts] = useState<Draft[]>(() =>
    editor.split
      ? [
          {
            ...fromTask(editor.task, day),
            title: editor.task!.title + "（一）",
            confirmed_minutes: Math.floor(editor.task!.confirmed_minutes / 2),
          },
          {
            ...fromTask(editor.task, day),
            title: editor.task!.title + "（二）",
            confirmed_minutes: Math.ceil(editor.task!.confirmed_minutes / 2),
          },
        ]
      : [fromTask(editor.task, day)],
  );
  const [error, setError] = useState(""),
    [saving, setSaving] = useState(false);
  const change = (
    i: number,
    key: keyof Draft,
    value: string | number | boolean,
  ) =>
    setParts(
      parts.map((p, j) =>
        i === j
          ? {
              ...p,
              [key]: value,
              ...(key === "confirmed_minutes" ? { time_locked: true } : {}),
            }
          : p,
      ),
    );
  return (
    <div className="modal-overlay">
      <form
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={
          editor.split ? "拆分任务" : editor.task ? "编辑任务" : "添加人工事项"
        }
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          setSaving(true);
          try {
            await onSave(parts);
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setSaving(false);
          }
        }}
      >
        <div className="modal-head">
          <div>
            <h2>
              {editor.split
                ? "拆分工作任务"
                : editor.task
                  ? "编辑工作记录"
                  : "添加人工事项"}
            </h2>
          </div>
          <button
            type="button"
            aria-label="关闭编辑"
            disabled={saving}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {parts.map((p, i) => (
          <fieldset key={i}>
            {editor.split && <legend>任务 {i + 1}</legend>}
            <label>
              任务标题
              <input
                required
                maxLength={500}
                value={p.title}
                onChange={(e) => change(i, "title", e.target.value)}
                placeholder="例如：与客户确认接口联调方案"
              />
            </label>
            <label>
              详细工作内容
              <textarea
                rows={4}
                value={p.details}
                onChange={(e) => change(i, "details", e.target.value)}
                placeholder="做了什么、采取哪些措施、有哪些产出或待处理事项…"
              />
            </label>
            <div className="form-grid">
              <label>
                所属项目
                <input
                  value={p.project}
                  onChange={(e) => change(i, "project", e.target.value)}
                  placeholder="项目或客户名称，可不绑定 Git"
                />
              </label>
              <label>
                工作分类
                <input
                  list={"categories-" + i}
                  value={p.category}
                  required
                  onChange={(e) => change(i, "category", e.target.value)}
                />
                <datalist id={"categories-" + i}>
                  {categories.map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </datalist>
              </label>
              <label>
                完成情况
                <select
                  value={p.status}
                  onChange={(e) => change(i, "status", e.target.value)}
                >
                  {Object.entries(statuses).map(([v, l]) => (
                    <option key={v} value={v}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                工作日期
                <input
                  required
                  type="date"
                  value={p.date}
                  onChange={(e) => change(i, "date", e.target.value)}
                />
              </label>
            </div>
            <label>
              确认工时（分钟）
              <div className="minute-input">
                <button
                  type="button"
                  aria-label={"减少工时 " + (i + 1)}
                  onClick={() =>
                    change(
                      i,
                      "confirmed_minutes",
                      Math.max(0, p.confirmed_minutes - step),
                    )
                  }
                >
                  −
                </button>
                <input
                  aria-label={"确认工时分钟 " + (i + 1)}
                  type="number"
                  required
                  min="0"
                  max="1440"
                  step="1"
                  value={p.confirmed_minutes}
                  onChange={(e) =>
                    change(i, "confirmed_minutes", Number(e.target.value))
                  }
                />
                <button
                  type="button"
                  aria-label={"增加工时 " + (i + 1)}
                  onClick={() =>
                    change(
                      i,
                      "confirmed_minutes",
                      Math.min(1440, p.confirmed_minutes + step),
                    )
                  }
                >
                  +
                </button>
                <span>= {hours(p.confirmed_minutes)} h</span>
              </div>
            </label>
            <label className="check-label">
              <input
                type="checkbox"
                checked={p.time_locked}
                onChange={(e) => change(i, "time_locked", e.target.checked)}
              />
              固定此项工时，AI分配时保留
            </label>
            <div className="form-grid">
              <label>
                开始时间（可选，本机时区）
                <input
                  type="datetime-local"
                  value={p.started_at}
                  onChange={(e) => change(i, "started_at", e.target.value)}
                />
              </label>
              <label>
                结束时间（可选，本机时区）
                <input
                  type="datetime-local"
                  value={p.ended_at}
                  onChange={(e) => change(i, "ended_at", e.target.value)}
                />
              </label>
            </div>
          </fieldset>
        ))}
        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}
        <div className="modal-footer">
          <button
            className="outline"
            type="button"
            disabled={saving}
            onClick={onClose}
          >
            取消
          </button>
          <button className="primary" disabled={saving} type="submit">
            {saving ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <Check size={16} />
            )}
            保存记录
          </button>
        </div>
      </form>
    </div>
  );
}

function SettingsForm({
  initial,
  busy,
  tab,
  onTab,
  onSave,
  onImported,
}: {
  onImported: () => void;
  initial: Settings;
  busy: boolean;
  tab: SettingsTab;
  onTab: (tab: SettingsTab) => void;
  onSave: (
    s: Settings,
    apiKey: string,
    test: boolean,
  ) => Promise<{ ok: boolean; saved: boolean; message: string }>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [feedback, setFeedback] = useState<{
    state: "running" | "success" | "error";
    message: string;
    test?: boolean;
  } | null>(null);
  const [s, setS] = useState(initial),
    [error, setError] = useState("");
  const tabsRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });
  useEffect(() => {
    const container = tabsRef.current;
    const active = container?.querySelector<HTMLElement>(`[data-tab="${tab}"]`);
    const update = () => {
      if (!active) return;
      setIndicator({ left: active.offsetLeft, width: active.offsetWidth });
    };
    update();
    const observer = new ResizeObserver(update);
    if (container) observer.observe(container);
    if (active) observer.observe(active);
    return () => observer.disconnect();
  }, [tab]);
  const lines = (value: string) =>
    value
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
  return (
    <form
      className="settings-form"
      onInvalidCapture={(e) => {
        const field = e.target as HTMLInputElement;
        setFeedback({
          state: "error",
          message:
            "尚未保存或测试，请检查设置中的无效字段：" +
            field.validationMessage,
        });
      }}
      onSubmit={async (e) => {
        e.preventDefault();
        if (busy) return;
        const test =
          (e.nativeEvent as SubmitEvent).submitter?.getAttribute("name") ===
          "test_connection";
        setFeedback({
          state: "running",
          message: test
            ? "正在保存并测试模型连接，请稍候；本次不发送工作对话。"
            : "正在保存设置…",
          test,
        });
        try {
          setError("");
          const result = await onSave(
            {
              ...s,
              git_roots: lines(s.git_roots.join("\n")),
              project_mappings: s.project_mappings,
              excluded_paths: lines(s.excluded_paths.join("\n")),
              redact_patterns: lines(s.redact_patterns.join("\n")),
              analysis_sources: [
                ...sourceKeys.filter((key) => s[key].enabled),
                ...(s.analysis_sources.includes("git") ? ["git"] : []),
              ],
              codex: { ...s.codex, paths: lines(s.codex.paths.join("\n")) },
              claude_code: {
                ...s.claude_code,
                paths: lines(s.claude_code.paths.join("\n")),
              },
              workbuddy_cn: {
                ...s.workbuddy_cn,
                paths: lines(s.workbuddy_cn.paths.join("\n")),
              },
              workbuddy_intl: {
                ...s.workbuddy_intl,
                paths: lines(s.workbuddy_intl.paths.join("\n")),
              },
              deepseek_harness: {
                ...s.deepseek_harness,
                paths: lines(s.deepseek_harness.paths.join("\n")),
              },
              qoder_cn: {
                ...s.qoder_cn,
                paths: lines(s.qoder_cn.paths.join("\n")),
              },
              qoder_intl: {
                ...s.qoder_intl,
                paths: lines(s.qoder_intl.paths.join("\n")),
              },
              trae_cn: {
                ...s.trae_cn,
                paths: lines(s.trae_cn.paths.join("\n")),
              },
              trae_intl: {
                ...s.trae_intl,
                paths: lines(s.trae_intl.paths.join("\n")),
              },
            },
            apiKey,
            test,
          );
          if (result.saved) setApiKey("");
          setFeedback({
            state: result.ok ? "success" : "error",
            message: result.message,
          });
        } catch (e) {
          setError((e as Error).message);
          setFeedback({
            state: "error",
            message: "尚未完成保存或测试：" + (e as Error).message,
          });
        }
      }}
    >
      <div
        className="settings-tabs"
        role="tablist"
        aria-label="系统设置分类"
        ref={tabsRef}
      >
        {settingsTabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            role="tab"
            id={`settings-tab-${key}`}
            data-tab={key}
            className="settings-tab"
            aria-selected={tab === key}
            aria-controls={`settings-pane-${key}`}
            onClick={() => onTab(key)}
          >
            <Icon size={15} />
            {label}
          </button>
        ))}
        <span
          className="settings-tab-indicator"
          style={{ left: indicator.left, width: indicator.width }}
          aria-hidden="true"
        />
      </div>
      <fieldset disabled={busy} className="settings-fields">
        <div className="settings-content">
          <div
            className="settings-pane"
            key={tab}
            id={`settings-pane-${tab}`}
            role="tabpanel"
            aria-labelledby={`settings-tab-${tab}`}
          >
            {tab === "sources" && (
              <>
                <section className="settings-section">
                  <h2>
                    <FolderGit2 size={19} /> 本地数据来源
                  </h2>
                  <SourcePicker settings={s} onChange={setS} />
                </section>
                <section className="settings-section">
                  <h2>
                    <FolderGit2 size={19} /> Git 仓库
                  </h2>
                  <GitSettings settings={s} onChange={setS} />
                </section>
              </>
            )}
            {tab === "time" && (
              <>
                <section className="settings-section">
                  <h2>
                    <Clock3 size={19} /> 工时与日期
                  </h2>
                  <div className="form-grid">
                    <label>
                      展示时区
                      <input
                        value={s.timezone}
                        onChange={(e) =>
                          setS({ ...s, timezone: e.target.value })
                        }
                      />
                    </label>
                    <label>
                      每日目标（分钟）
                      <input
                        required
                        min="1"
                        max="1440"
                        type="number"
                        value={s.target_minutes}
                        onChange={(e) =>
                          setS({ ...s, target_minutes: Number(e.target.value) })
                        }
                      />
                    </label>
                    <label>
                      增减步长（分钟）
                      <input
                        required
                        min="1"
                        max="120"
                        type="number"
                        value={s.step_minutes}
                        onChange={(e) =>
                          setS({ ...s, step_minutes: Number(e.target.value) })
                        }
                      />
                    </label>
                  </div>
                </section>
                <section className="settings-section">
                  <h2>
                    <Activity size={19} /> 活跃时间口径
                  </h2>
                  <label>
                    活跃片段的相邻消息阈值（分钟）
                    <input
                      type="number"
                      min="1"
                      max="30"
                      value={s.activity_gap_minutes}
                      onChange={(e) =>
                        setS({
                          ...s,
                          activity_gap_minutes: Number(e.target.value),
                        })
                      }
                    />
                  </label>
                </section>
                <section className="settings-section">
                  <h2>
                    <ShieldCheck size={19} /> 隐私与脱敏
                  </h2>
                  <label>
                    排除的敏感目录（每行一个）
                    <textarea
                      value={s.excluded_paths.join("\n")}
                      onChange={(e) =>
                        setS({
                          ...s,
                          excluded_paths: e.target.value.split("\n"),
                        })
                      }
                    />
                  </label>
                  <label>
                    附加脱敏正则（每行一条）
                    <textarea
                      value={s.redact_patterns.join("\n")}
                      onChange={(e) =>
                        setS({
                          ...s,
                          redact_patterns: e.target.value.split("\n"),
                        })
                      }
                    />
                  </label>
                </section>
              </>
            )}
            {tab === "model" && (
              <section className="settings-section">
                <h2>
                  <Sparkles size={19} /> 模型分析
                </h2>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={s.llm_enabled}
                    onChange={(e) =>
                      setS({ ...s, llm_enabled: e.target.checked })
                    }
                  />
                  我了解发送范围，显式开启模型分析
                </label>
                <ModelSettings
                  settings={s}
                  onChange={(next) => {
                    setS(next);
                    setFeedback(null);
                  }}
                  apiKey={apiKey}
                  onKeyChange={(value) => {
                    setApiKey(value);
                    setFeedback(null);
                  }}
                  busy={busy}
                  saved={initial}
                  feedback={feedback}
                />
              </section>
            )}
            {tab === "operation" && (
              <section className="settings-section">
                <h2>
                  <SlidersHorizontal size={19} /> 操作设置
                </h2>
                <label className="check-label">
                  <input
                    type="checkbox"
                    checked={s.auto_import_analysis}
                    onChange={(e) =>
                      setS({
                        ...s,
                        auto_import_analysis: e.target.checked,
                      })
                    }
                  />
                  AI 分析完成后直接填入今日工作清单
                </label>
              </section>
            )}
            {tab === "workflow" && (
              <>
                <WorkflowSettings settings={s} onChange={setS} />
                <HistoryPanel
                  settings={s}
                  onChange={setS}
                  onImported={onImported}
                />
              </>
            )}
            {tab === "enterprise" && (
              <EnterpriseSettings settings={s} onChange={setS} />
            )}
          </div>
        </div>
        {error && <div className="alert error">{error}</div>}
        {feedback && tab !== "model" && (
          <div
            className={
              "alert settings-save-feedback" +
              (feedback.state === "error" ? " error" : "")
            }
            role={feedback.state === "error" ? "alert" : "status"}
            aria-label="设置保存反馈"
            aria-live={feedback.state === "error" ? "assertive" : "polite"}
          >
            {feedback.message}
          </div>
        )}
        <div className="settings-actions">
          <button className="primary" disabled={busy} type="submit">
            <Check size={16} />
            保存设置
          </button>
        </div>
      </fieldset>
    </form>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
