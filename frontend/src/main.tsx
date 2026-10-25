import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  ArrowDownToLine,
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  ChevronRight,
  Clock3,
  FileText,
  FolderGit2,
  GitMerge,
  Layers3,
  ListFilter,
  LoaderCircle,
  LockKeyhole,
  Plus,
  ScanLine,
  Settings2,
  ShieldCheck,
  Sparkles,
  Split,
  Trash2,
  X,
} from "lucide-react";
import type { Report, Settings, Task, WorkEvent } from "./types";
import "./style.css";
import { Conversations } from "./Conversations";
import { ModelSettings } from "./ModelSettings";
import { WorkflowSettings } from "./WorkflowSettings";
import { AllocationModal } from "./AllocationModal";
import { GiteePublishModal } from "./GiteePublishModal";

const statuses: Record<string, string> = {
  requested: "待执行",
  attempted: "尝试中",
  changed: "已修改",
  verified: "已验证",
  delivered: "已交付",
};
const sources: Record<string, string> = {
  codex: "Codex",
  claude_code: "Claude Code",
  git: "Git",
};
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
async function api<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const r = await fetch("/api" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  if (!r.ok) {
    const e = await r.json().catch(() => ({ detail: r.statusText }));
    throw new Error(
      typeof e.detail === "string" ? e.detail : JSON.stringify(e.detail),
    );
  }
  return r.json();
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
    [error, setError] = useState(""),
    [notice, setNotice] = useState(""),
    [editor, setEditor] = useState<Editor | null>(null),
    [allocationOpen, setAllocationOpen] = useState(false),
    [giteeOpen, setGiteeOpen] = useState(false),
    [proof, setProof] = useState<Task | null>(null),
    [selected, setSelected] = useState<string[]>([]);
  const [source, setSource] = useState(""),
    [sessionFilter, setSessionFilter] = useState(""),
    [project, setProject] = useState(""),
    [taskFilter, setTaskFilter] = useState(""),
    [search, setSearch] = useState("");
  const refresh = async (d = day) => {
    const [r, e, s] = await Promise.all([
      api<Report>("/reports/" + d),
      api<WorkEvent[]>("/events?date=" + d),
      api<Settings>("/settings"),
    ]);
    setReport(r);
    setEvents(e);
    setSettings(s);
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
  const collect = () =>
    run("正在采集全天对话", async () => {
      const c = await api<{ new_events: number; warnings: string[] }>(
        "/collect?date=" + day,
        "POST",
        {},
      );
      await refresh();
      setNotice(
        `新增 ${c.new_events} 条活动记录，已更新全天对话统计；请点击 AI 分析归纳实际工作任务。${c.warnings.join("；")}`,
      );
    });
  const analyzeDay = () =>
    run("AI 正在通读全天对话并合并工作目标，长记录可能需要数分钟", async () => {
      const a = await api<{
        created: number;
        archived: number;
        warnings: string[];
        analysis_required?: boolean;
      }>("/analyze?date=" + day, "POST", {});
      await refresh();
      setNotice(
        a.analysis_required
          ? a.warnings.join("；")
          : `全天 AI 分析已完成，归纳 ${a.created} 项工作，替换 ${a.archived} 项未审核草稿。${a.warnings.join("；")}`,
      );
    });
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
        <a className="brand" href="#" onClick={(e) => e.preventDefault()}>
          <span className="brand-mark">
            <Layers3 size={23} />
          </span>
          <span>
            拾时<span className="brand-en">WORK TRACKER</span>
          </span>
        </a>
        <div className="workspace">
          <span className="avatar">L</span>
          <div>
            本地工作空间<small>Personal workspace</small>
          </div>
          <LockKeyhole size={14} />
        </div>
        <div className="nav-label">工作台</div>
        <nav>
          {(
            [
              ["daily", "每日日报", FileText],
              ["timeline", "活动时间线", Activity],
              ["settings", "偏好设置", Settings2],
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
        <div className="side-note">
          <span className="small-label">每一份投入，都有迹可循</span>
          <p>
            把分散的 AI 工作记录，
            <br />
            整理成清晰的每日成果。
          </p>
          <span className="note-symbol">↗</span>
        </div>
        <div className="local-status">
          <span className="green-dot" />
          <span>
            本地优先 · 数据由你掌握
            <small>
              {settings?.llm_enabled ? "模型分析已显式开启" : "云端模型未开启"}
            </small>
          </span>
          <ShieldCheck size={17} />
        </div>
        <div className="version">
          拾时 V1.0 <span>个人工作记录工具</span>
        </div>
      </aside>
      <main>
        <header className="topbar">
          <span>
            工作空间 <ChevronRight size={14} />{" "}
            {page === "daily"
              ? "每日日报"
              : page === "timeline"
                ? "活动时间线"
                : "偏好设置"}
          </span>
          <span className="private">
            <LockKeyhole size={13} /> 本地 SQLite 存储
          </span>
        </header>
        <div className="content">
          <div className="heading">
            <div>
              <div className="eyebrow">
                {page === "daily"
                  ? "YOUR DAY, IN FOCUS"
                  : page === "timeline"
                    ? "FOLLOW THE EVIDENCE"
                    : "MAKE IT YOURS"}
              </div>
              <h1>
                {page === "daily"
                  ? "每一份工作，清晰记录。"
                  : page === "timeline"
                    ? "串起今天的工作脉络。"
                    : "让工具适应你的工作。"}
              </h1>
              <p>
                {page === "daily"
                  ? "从 AI 对话到线下沟通，把真实投入整理为一份可审核的日报。"
                  : page === "timeline"
                    ? "按时间查看会话、工具执行与 Git 活动，回溯任务的每一条依据。"
                    : "配置本地数据来源、项目归属与分析方式。"}
              </p>
            </div>
            {page !== "settings" && (
              <button className="primary" disabled={!!busy} onClick={collect}>
                {busy ? (
                  <LoaderCircle className="spin" size={17} />
                ) : (
                  <ScanLine size={17} />
                )}
                采集全天对话
              </button>
            )}
          </div>
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
          {busy && (
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
                <span className="timezone">
                  {settings?.timezone || "Asia/Shanghai"}
                </span>
              </span>
            </div>
          )}
          {page === "daily" && report && (
            <>
              {settings && (
                <Conversations
                  overview={report.conversations}
                  settings={settings}
                  busy={!!busy}
                  legacyCount={report.legacy_count}
                  onAnalyze={analyzeDay}
                  onSettings={() => setPage("settings")}
                  onSources={(values) =>
                    run("正在更新分析范围", async () => {
                      await api("/settings", "PUT", {
                        ...settings,
                        analysis_sources: values,
                      });
                      await refresh();
                    })
                  }
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
                  <small>AI 提取与人工事项</small>
                </div>
                <div className="stat">
                  <div className="stat-label">
                    关联项目 <FolderGit2 size={17} />
                  </div>
                  <div className="stat-number">
                    {report.projects}
                    <span> 个</span>
                  </div>
                  <small>依据本地路径与 Git 归属</small>
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
                  <small>Claude Code / Codex</small>
                </div>
              </div>
              <section className="task-section">
                <div className="section-head">
                  <div>
                    <h2>
                      今日工作清单{" "}
                      <span className="count">{report.tasks.length}</span>
                    </h2>
                    <p>审核工作内容，按实际投入确认工时。</p>
                  </div>
                  <div className="actions">
                    <button className="outline" disabled={!!busy || !report.tasks.length} onClick={() => setGiteeOpen(true)}>提交到 Gitee{selected.length ? `（${selected.length}项）` : ''}</button>
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
                    <p>
                      先采集本地对话，再用 AI 归纳工作目标，
                      <br />
                      也可以先添加会议、客户沟通等真实事项。
                    </p>
                    <button
                      className="outline"
                      disabled={!!busy}
                      onClick={() => setEditor({})}
                    >
                      <Plus size={16} />
                      记录第一项工作
                    </button>
                    <small>
                      AI估算需主动点击，分配结果为待审核草稿，固定工时不变。
                    </small>
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
                              <p>
                                {t.details || "暂无详细说明，点击任务补充。"}
                              </p>
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
                                    ? "AI 分配 · 待审核"
                                    : "待分配"}
                              </span>
                              <strong title={t.time_reason}>
                                {hours(t.confirmed_minutes)} <small>h</small>
                              </strong>
                              <small>
                                建议{" "}
                                {t.suggested_minutes === null
                                  ? "待估算"
                                  : hours(t.suggested_minutes) + "h · 低置信"}
                              </small>
                              <small>
                                工作活跃片段{" "}
                                {t.timing.observable_active_minutes} 分钟
                              </small>
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
                <div className="table-footer">
                  <ShieldCheck size={15} />
                  <span>
                    自动分析仅提供候选任务，完成状态和工时请依据真实工作审核。
                  </span>
                  <span>{report.tasks.length} 项记录</span>
                </div>
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
                    <p>
                      {report.confirmed
                        ? "修改任务后，日报将恢复草稿并需要重新确认。"
                        : "补充真实工作或调整已有任务；系统不会自动填补差额。"}
                    </p>
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
              <div className="footnote">
                <LockKeyhole size={13} />
                记录保存在你的电脑上。
                {settings?.llm_enabled
                  ? "已开启全天模型分析，发送脱敏后的对话与执行状态。"
                  : "未开启模型：只采集对话，可人工录入和导出。"}
                <span>LOCAL FIRST, ALWAYS.</span>
              </div>
            </>
          )}
          {page === "timeline" && (
            <>
              <div className="timeline-toolbar">
                <ListFilter size={18} />
                <input
                  aria-label="搜索时间线"
                  placeholder="搜索活动内容…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
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
                <span>{filtered.length} 条活动</span>
              </div>
              <section className="timeline">
                {filtered.length === 0 ? (
                  <div className="empty">
                    <Activity size={30} />
                    <h3>暂无匹配的活动记录</h3>
                    <p>点击“采集全天对话”读取本地历史，或调整筛选条件。</p>
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
          {page === "settings" && settings && (
            <SettingsForm
              initial={settings}
              busy={!!busy}
              onImported={() => {
                void refresh();
              }}
              onSave={(s, apiKey, test) =>
                run("正在保存设置", async () => {
                  await api("/settings", "PUT", s);
                  if (apiKey.trim())
                    await api("/model/key", "POST", { api_key: apiKey.trim() });
                  if (test) await api("/model/test", "POST", {});
                  await refresh();
                  setNotice(
                    test
                      ? "设置已保存，真实模型连接测试成功；未发送工作对话。"
                      : "设置已保存。排除规则影响后续采集和分析，已有本地记录仍保留。",
                  );
                })
              }
            />
          )}
        </div>
      </main>
      {giteeOpen && <GiteePublishModal day={day} taskIds={selected} onClose={() => setGiteeOpen(false)} onSettings={() => {setGiteeOpen(false);setPage('settings');}} onApplied={refresh} />}
      {allocationOpen && report && <AllocationModal report={report} onClose={() => setAllocationOpen(false)} onApplied={async message => { await refresh();setNotice(message); }} />}
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
                <div className="eyebrow">TRACEABLE EVIDENCE</div>
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
            <div className="eyebrow">WORK, WITH CONTEXT</div>
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
        <p className="muted">
          {editor.split
            ? "拆分后确认分钟数合计需与原任务一致；继承证据需人工核对。"
            : "记录实际做过的工作，确认工时以整数分钟保存。"}
        </p>
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
  onSave,
  onImported,
}: {
  onImported: () => void;
  initial: Settings;
  busy: boolean;
  onSave: (s: Settings, apiKey: string, test: boolean) => Promise<boolean>;
}) {
  const [apiKey, setApiKey] = useState("");
  const [s, setS] = useState(initial),
    [mapping, setMapping] = useState(
      JSON.stringify(initial.project_mappings, null, 2),
    ),
    [error, setError] = useState("");
  const lines = (value: string) =>
    value
      .split("\n")
      .map((x) => x.trim())
      .filter(Boolean);
  return (
    <form
      className="settings-form"
      onSubmit={async (e) => {
        e.preventDefault();
        const test =
          (e.nativeEvent as SubmitEvent).submitter?.getAttribute("name") ===
          "test_connection";
        try {
          const m = JSON.parse(mapping);
          if (
            typeof m !== "object" ||
            Array.isArray(m) ||
            !m ||
            Object.values(m).some((v) => typeof v !== "string")
          )
            throw new Error("项目映射需为路径到名称的 JSON 对象");
          setError("");
          const saved = await onSave(
            {
              ...s,
              project_mappings: m,
              git_roots: lines(s.git_roots.join("\n")),
              excluded_paths: lines(s.excluded_paths.join("\n")),
              redact_patterns: lines(s.redact_patterns.join("\n")),
              codex: { ...s.codex, paths: lines(s.codex.paths.join("\n")) },
              claude_code: {
                ...s.claude_code,
                paths: lines(s.claude_code.paths.join("\n")),
              },
            },
            apiKey,
            test,
          );
          if (saved) setApiKey("");
        } catch (e) {
          setError((e as Error).message);
        }
      }}
    >
      <section className="settings-section">
        <h2>
          <FolderGit2 size={19} /> 本地数据来源
        </h2>
        <p>
          只读扫描 JSONL
          会话文件。路径位于运行本服务的电脑上，每行填写一个路径。
        </p>
        {(["claude_code", "codex"] as const).map((key) => (
          <div className="source-setting" key={key}>
            <label className="check-label">
              <input
                type="checkbox"
                checked={s[key].enabled}
                onChange={(e) =>
                  setS({
                    ...s,
                    [key]: { ...s[key], enabled: e.target.checked },
                  })
                }
              />
              {sources[key]}
            </label>
            <textarea
              aria-label={sources[key] + " 路径"}
              rows={3}
              value={s[key].paths.join("\n")}
              onChange={(e) =>
                setS({
                  ...s,
                  [key]: { ...s[key], paths: e.target.value.split("\n") },
                })
              }
            />
          </div>
        ))}
        <label>
          Git 仓库根目录（每行一个）
          <textarea
            value={s.git_roots.join("\n")}
            onChange={(e) =>
              setS({ ...s, git_roots: e.target.value.split("\n") })
            }
          />
        </label>
        <label>
          目录到项目名称映射（JSON）
          <textarea
            rows={4}
            value={mapping}
            onChange={(e) => setMapping(e.target.value)}
          />
        </label>
      </section>
      <section className="settings-section">
        <h2>
          <Clock3 size={19} /> 工时与日期
        </h2>
        <div className="form-grid">
          <label>
            展示时区
            <input
              value={s.timezone}
              onChange={(e) => setS({ ...s, timezone: e.target.value })}
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
        <p>
          默认目标为 480
          分钟（8h）；修改目标或时区会撤销已有日报确认，需重新审核。
        </p>
      </section>
      <section className="settings-section">
        <h2>
          <ShieldCheck size={19} /> 隐私与可选模型
        </h2>
        <label>
          排除的敏感目录（每行一个）
          <textarea
            value={s.excluded_paths.join("\n")}
            onChange={(e) =>
              setS({ ...s, excluded_paths: e.target.value.split("\n") })
            }
          />
        </label>
        <label>
          附加脱敏正则（每行一条）
          <textarea
            value={s.redact_patterns.join("\n")}
            onChange={(e) =>
              setS({ ...s, redact_patterns: e.target.value.split("\n") })
            }
          />
        </label>
        <div className="privacy-info">
          <LockKeyhole size={19} />
          <p>
            本地先统计每日会话和消息。开启 AI
            后，按实际工作目标归纳全天对话，追问、修改、测试合并处理，不自动凑满工时。
          </p>
        </div>
        <label className="check-label">
          <input
            type="checkbox"
            checked={s.llm_enabled}
            onChange={(e) => setS({ ...s, llm_enabled: e.target.checked })}
          />
          我了解发送范围，显式开启模型分析
        </label>
        <ModelSettings
          settings={s}
          onChange={setS}
          apiKey={apiKey}
          onKeyChange={setApiKey}
          busy={busy}
          saved={initial}
        />
      </section>
      <WorkflowSettings settings={s} onChange={setS} onImported={onImported} />
      {error && <div className="alert error">{error}</div>}
      <div className="settings-actions">
        <button className="primary" disabled={busy} type="submit">
          <Check size={16} />
          保存设置
        </button>
      </div>
    </form>
  );
}

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
