import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  Activity,
  AlertCircle,
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
  GitCompare,
  GitMerge,
  Layers3,
  Link2,
  ListFilter,
  LoaderCircle,
  Palette,
  PieChart,
  Plus,
  ScanLine,
  Send,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Split,
  Trash2,
  X,
} from "lucide-react";
import type { DuplicatePair, GiteeSyncResult, PendingTask, Report, ReviewAlertItem, Settings, SimilarityCheckResponse, Task, WorkEvent } from "./types";
import "./style.css";
import { api, apiUrl } from "./api";
import { Conversations } from "./Conversations";
import { AnalysisLog } from "./AnalysisLog";
import { ModelSettings } from "./ModelSettings";
import { WorkflowSettings } from "./WorkflowSettings";
import { AllocationModal, type AllocationRequest } from "./AllocationModal";
import { AllocationPreviewModal } from "./AllocationPreviewModal";
import { SimilarityMergeModal } from "./SimilarityMergeModal";
import { GiteePublishModal } from "./GiteePublishModal";
import { GiteeIssueBrowserModal } from "./GiteeIssueBrowserModal";
import { GiteeDedupModal } from "./GiteeDedupModal";
import { EnterpriseSettings } from "./EnterpriseSettings";
import { HistoryPanel } from "./HistoryPanel";
import { GitSettings } from "./GitSettings";
import { SetupWizard } from "./SetupWizard";
import { SourcePicker, sourceKeys, sourceLabels } from "./SourcePicker";
import { CollectDetailsModal } from "./CollectDetailsModal";
import { AnalysisResultsModal } from "./AnalysisResultsModal";
import { DeletedRecoveryModal } from "./DeletedRecoveryModal";
import { JobToast, type JobToastState } from "./JobToast";
import { UsageStats } from "./UsageStats";
import { ProjectQuickSelect, StatusQuickSelect, HoursQuickRegister } from "./TaskQuickActions";
import { useTodos, hasBracketPrefix, formatTitleWithPrefix } from "./useTodos";
import { TodoReminderBar, TodoSideDock } from "./TodoWidgets";
import { useTheme, ThemeLibrarySection } from "./themes";

// 与 Gitee 企业版工作项状态对齐；changed 仅为历史数据保留显示。
const statuses: Record<string, string> = {
  requested: "待办的",
  attempted: "进行中",
  delivered: "已完成",
  verified: "已验收",
  rejected: "已拒绝",
  changed: "已修改",
};
const selectableStatuses = ["requested", "attempted", "delivered", "verified", "rejected"];
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
  { key: "enterprise", label: "Gitee 企业版", icon: Building2 },
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
const categories = ["需求", "任务", "缺陷"];
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
  is_todo: boolean;
  confirmed_minutes: number;
  suggested_minutes?: number | null;
  confirmedHoursText?: string;
  suggestedHoursText?: string;
  time_locked: boolean;
  started_at: string;
  ended_at: string;
};
const fromTask = (t?: Task, day = ""): Draft => ({
  date: t?.date || day,
  title: t?.title || "",
  details: t?.details || "",
  project: t?.project || "",
  category: t?.category || "任务",
  status: t?.status || "delivered",
  is_todo: t ? Boolean(t.is_todo) : false,
  confirmed_minutes: t?.confirmed_minutes || 0,
  suggested_minutes: t?.suggested_minutes ?? null,
  confirmedHoursText: hours(t?.confirmed_minutes || 0),
  suggestedHoursText: t?.suggested_minutes != null ? hours(t.suggested_minutes) : "",
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
  const { confirmedHoursText: _c, suggestedHoursText: _s, ...rest } = d;
  return {
    ...rest,
    project: d.project || "未归属项目",
    suggested_minutes: typeof d.suggested_minutes === "number" ? d.suggested_minutes : null,
    started_at: d.started_at ? new Date(d.started_at).toISOString() : null,
    ended_at: d.ended_at ? new Date(d.ended_at).toISOString() : null,
  };
}

function App() {
  const { theme, setTheme } = useTheme();
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
    [allocationPreviewOpen, setAllocationPreviewOpen] = useState(false),
    [allocating, setAllocating] = useState(false),
    [allocationError, setAllocationError] = useState(""),
    [similarityOpen, setSimilarityOpen] = useState(false),
    [similarityPairs, setSimilarityPairs] = useState<DuplicatePair[]>([]),
    [checkingSimilarity, setCheckingSimilarity] = useState(false),
    [proceedToAllocationAfterSimilarity, setProceedToAllocationAfterSimilarity] = useState(false),
    [proceedToGiteeAfterSimilarity, setProceedToGiteeAfterSimilarity] = useState(false),
    [giteeOpen, setGiteeOpen] = useState(false),
    [giteeBrowserOpen, setGiteeBrowserOpen] = useState(false),
    [giteeDedupOpen, setGiteeDedupOpen] = useState(false),
    [giteeSyncResult, setGiteeSyncResult] = useState<GiteeSyncResult | null>(null),
    [setupOpen, setSetupOpen] = useState(false),
    [settingsEpoch, setSettingsEpoch] = useState(0),
    [settingsTab, setSettingsTab] = useState<SettingsTab>("sources"),
    [proof, setProof] = useState<Task | null>(null),
    [selected, setSelected] = useState<string[]>([]),
    [todoDrawerOpen, setTodoDrawerOpen] = useState(false);
  const [collecting, setCollecting] = useState(false),
    [analyzing, setAnalyzing] = useState(false),
    [collectDay, setCollectDay] = useState<string | null>(null),
    [collectRange, setCollectRange] = useState<{
      startDate?: string;
      endDate?: string;
    } | null>(null),
    [multiDayState, setMultiDayState] = useState<{
      enabled: boolean;
      startDate: string;
      endDate: string;
    }>({ enabled: false, startDate: "", endDate: "" }),
    [analysisView, setAnalysisView] = useState<{
      day: string;
      pending: PendingTask[];
      stale: boolean;
    } | null>(null),
    [toast, setToast] = useState<JobToastState | null>(null),
    [reviewAlert, setReviewAlert] = useState<{
      day: string;
      warnings: string[];
      items?: ReviewAlertItem[];
    } | null>(null),
    [recoveryModal, setRecoveryModal] = useState<{
      day: string;
      tasks: PendingTask[];
      warnings: string[];
    } | null>(null);
  const [source, setSource] = useState(""),
    [sessionFilter, setSessionFilter] = useState(""),
    [project, setProject] = useState(""),
    [taskFilter, setTaskFilter] = useState(""),
    [search, setSearch] = useState("");
  const dayRef = useRef(day);
  dayRef.current = day;
  const refreshReportOnly = async (d = dayRef.current) => {
    const [r, e, s] = await Promise.all([
      api<Report>("/reports/" + d),
      api<WorkEvent[]>("/events?date=" + d),
      api<Settings>("/settings"),
    ]);
    setSettings(s);
    if (dayRef.current !== d) return;
    setReport(r);
    setEvents(e);
    setSelected([]);
  };
  const todoModel = useTodos({
    getToday: () => today(settings?.timezone),
    onChanged: () => refreshReportOnly(),
  });
  const refresh = async (d = day) => {
    await Promise.all([refreshReportOnly(d), todoModel.reload()]);
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
    if (!report?.conversations.running && !report?.allocation_running) return;
    const timer = window.setInterval(() => {
      api<Report>("/reports/" + day)
        .then(setReport)
        .catch(() => {});
    }, 4000);
    return () => window.clearInterval(timer);
  }, [day, report?.conversations.running, report?.allocation_running]);
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
  const collect = async (options?: {
    startDate?: string;
    endDate?: string;
    silentToast?: boolean;
  }): Promise<boolean> => {
    if (collecting || analyzing) return false;
    const jobDay = day;
    const sDate = options?.startDate || jobDay;
    const eDate = options?.endDate || jobDay;
    const isRange = sDate !== eDate || sDate !== jobDay;
    setCollecting(true);
    setError("");
    setNotice("");
    setCollectProgress(
      isRange
        ? `正在读取 ${sDate} 至 ${eDate} 的本地日志与 Git 提交，请稍候…`
        : "正在读取本地日志，首次采集会扫描全部历史，请稍候",
    );
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
      const q = new URLSearchParams({ date: jobDay });
      if (options?.startDate) q.set("start_date", options.startDate);
      if (options?.endDate) q.set("end_date", options.endDate);
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
      }>("/collect?" + q.toString(), "POST", {});
      await refresh(jobDay);
      if (!options?.silentToast) {
        const git = c.git_events ?? 0;
        const chat = Math.max(0, c.new_events - git);
        const pieces = [
          c.new_events === 0
            ? isRange
              ? `${sDate} ~ ${eDate} 区间已采集过，没有新增活动记录`
              : "这一天已经采集过了，没有新的活动记录"
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
          title: isRange ? `跨天采集完成（${sDate.slice(5)} ~ ${eDate.slice(5)}）` : "采集完成",
          message: pieces.join("；") + "。",
          actionLabel: isRange ? "查看跨天详情" : "查看采集详情",
          onAction: () => {
            setToast(null);
            setCollectRange(isRange ? { startDate: sDate, endDate: eDate } : null);
            setCollectDay(jobDay);
          },
        });
      }
      return true;
    } catch (e) {
      setError((e as Error).message);
      return false;
    } finally {
      window.clearInterval(timer);
      setCollectProgress("");
      setCollecting(false);
    }
  };
  const analyzeDay = async (options?: {
    startDate?: string;
    endDate?: string;
    targetMinutes?: number;
    autoCollect?: boolean;
  }) => {
    if (analyzing || collecting) return;
    const jobDay = day;
    if (options?.autoCollect) {
      const ok = await collect({
        startDate: options.startDate,
        endDate: options.endDate,
        silentToast: true,
      });
      if (!ok) return;
    }
    setAnalyzing(true);
    setError("");
    setNotice("");
    try {
      const q = new URLSearchParams();
      q.set("date", jobDay);
      if (options?.startDate) q.set("start_date", options.startDate);
      if (options?.endDate) q.set("end_date", options.endDate);
      if (options?.targetMinutes)
        q.set("target_minutes", String(options.targetMinutes));

      const a = await api<{
        created: number;
        archived: number;
        pending: number;
        pending_tasks: PendingTask[];
        auto_imported: boolean;
        warnings: string[];
        analysis_required?: boolean;
        similarity_check?: SimilarityCheckResponse;
        usage?: null | {
          total_tokens: number;
          prompt_tokens: number;
          completion_tokens: number;
          estimated: boolean;
        };
      }>("/analyze?" + q.toString(), "POST", {});
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
      if (a.auto_imported && a.similarity_check?.has_duplicates && a.similarity_check.pairs.length > 0) {
        setSimilarityPairs(a.similarity_check.pairs);
        setProceedToAllocationAfterSimilarity(false);
        setProceedToGiteeAfterSimilarity(false);
        setSimilarityOpen(true);
      }
      const deletedPending = (a.pending_tasks || []).filter(
        (t) => t.previously_deleted,
      );
      const conflictPending = (a.pending_tasks || []).filter(
        (t) => !t.previously_deleted,
      );
      if (a.auto_imported && deletedPending.length > 0 && conflictPending.length === 0) {
        // 如果没有分析结果弹窗，在导入前弹出一个专门的弹窗（相似度匹配的弹窗信息也在这里面，不要弹出多个窗口）
        setRecoveryModal({
          day: jobDay,
          tasks: deletedPending,
          warnings: a.warnings || [],
        });
        return;
      }
      const warnings = a.warnings.length
        ? ` 提示：${a.warnings.join("；")}`
        : "";
      const hasReviewablePending = conflictPending.length > 0;
      setToast({
        title: "AI 分析完成",
        message: a.auto_imported
          ? hasReviewablePending
            ? `已自动写入 ${a.created} 项工作；另有 ${conflictPending.length} 条候选对应已有 AI 项，请确认替换或新建。${usage}${warnings}`
            : `已归纳 ${a.created} 项工作并写入今日清单，替换 ${a.archived} 项未审核草稿。${usage}${warnings}`
          : `已生成 ${a.pending} 条候选工作项，尚未写入今日清单；确认后即可导入。${usage}${warnings}`,
        actionLabel: !a.auto_imported || hasReviewablePending ? "选择替换或新建" : "前往查看",
        onAction: () => {
          setToast(null);
          if (a.auto_imported && !hasReviewablePending) {
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
  const startAllocation = async (request: AllocationRequest) => {
    const jobDay = day;
    setAllocationOpen(false);
    setAllocating(true);
    setAllocationError("");
    setError("");
    setNotice("");
    try {
      const result = await api<{
        allocated: number;
        fixed_minutes: number;
        preserved_minutes?: number;
        total_minutes: number;
        warnings: string[];
        applied: boolean;
        pending: boolean;
      }>("/reports/" + jobDay + "/estimate-time", "POST", request);
      await refresh(jobDay);
      try {
        const draftStr = sessionStorage.getItem(`allocation-draft:${jobDay}`);
        if (draftStr) {
          const draft = JSON.parse(draftStr);
          draft.requestId = crypto.randomUUID();
          sessionStorage.setItem(`allocation-draft:${jobDay}`, JSON.stringify(draft));
        }
      } catch {}
      const pending = result.pending ?? !result.applied;
      const kept = result.fixed_minutes + (result.preserved_minutes || 0);
      const warnings = result.warnings?.length
        ? ` 提示：${result.warnings.join("；")}`
        : "";
      setToast({
        title: pending ? "工时建议已生成" : "AI 工时已分配",
        message: pending
          ? `已生成 ${result.allocated} 个工作项的工时建议，确认后应用；固定和保留 ${hours(kept)}h 不变。${warnings}`
          : `已应用 ${result.allocated} 个工作项的 AI 工时，固定和保留 ${hours(kept)}h。${warnings}`,
        actionLabel: "预览工时",
        onAction: () => {
          setToast(null);
          setAllocationPreviewOpen(true);
        },
      });
    } catch (e) {
      const errMsg = (e as Error).message;
      if (errMsg.includes("高度重复") || errMsg.includes("相似")) {
        void runManualSimilarityCheck();
      }
      if (errMsg.includes("请求编号已使用")) {
        try {
          const draftStr = sessionStorage.getItem(`allocation-draft:${jobDay}`);
          if (draftStr) {
            const draft = JSON.parse(draftStr);
            draft.requestId = crypto.randomUUID();
            sessionStorage.setItem(`allocation-draft:${jobDay}`, JSON.stringify(draft));
          }
        } catch {}
      }
      setAllocationError(errMsg);
    } finally {
      setAllocating(false);
    }
  };
  const handleOpenAllocation = async () => {
    if (!report) return;
    setAllocationError("");
    const sim = report.similarity_status;
    if (sim && sim.checked && !sim.tasks_changed && sim.handled) {
      setAllocationOpen(true);
      return;
    }
    setCheckingSimilarity(true);
    try {
      const res = await api<SimilarityCheckResponse>(`/reports/${day}/check-similarity`, "POST", { force: false });
      if (res.has_duplicates && res.pairs.length > 0) {
        setSimilarityPairs(res.pairs);
        setProceedToAllocationAfterSimilarity(true);
        setProceedToGiteeAfterSimilarity(false);
        setSimilarityOpen(true);
      } else {
        await refresh();
        setAllocationOpen(true);
      }
    } catch (e) {
      setAllocationError((e as Error).message);
    } finally {
      setCheckingSimilarity(false);
    }
  };
  const handleOpenGiteePublish = async () => {
    if (!report) return;
    setError("");
    const sim = report.similarity_status;
    if (sim && sim.checked && !sim.tasks_changed && sim.handled) {
      setGiteeOpen(true);
      return;
    }
    setCheckingSimilarity(true);
    try {
      const res = await api<SimilarityCheckResponse>(`/reports/${day}/check-similarity`, "POST", { force: false });
      if (res.has_duplicates && res.pairs.length > 0) {
        setSimilarityPairs(res.pairs);
        setProceedToGiteeAfterSimilarity(true);
        setProceedToAllocationAfterSimilarity(false);
        setSimilarityOpen(true);
      } else {
        await refresh();
        setGiteeOpen(true);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setCheckingSimilarity(false);
    }
  };
  const runManualSimilarityCheck = async () => {
    if (!report) return;
    setAllocationError("");
    setCheckingSimilarity(true);
    try {
      const res = await api<SimilarityCheckResponse>(`/reports/${day}/check-similarity`, "POST", { force: true });
      await refresh();
      if (res.has_duplicates && res.pairs.length > 0) {
        setSimilarityPairs(res.pairs);
        setProceedToAllocationAfterSimilarity(false);
        setProceedToGiteeAfterSimilarity(false);
        setSimilarityOpen(true);
      } else {
        setToast({
          title: "相似度检测通过",
          message: "人工工作项与 AI 工作项未发现高度重复项，工时分配将不再重复检测。",
        });
      }
    } catch (e) {
      setAllocationError((e as Error).message);
    } finally {
      setCheckingSimilarity(false);
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
      const response = await fetch(apiUrl("/reports/" + day + "/export.md"));
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
                    : "系统设置"}
              </h1>
            </div>
            {(page === "daily" || page === "timeline") && (
              <div className="heading-actions">
                <button
                  className="outline"
                  disabled={!report}
                  onClick={() => {
                    if (page === "daily" && multiDayState.enabled) {
                      setCollectRange({
                        startDate: multiDayState.startDate,
                        endDate: multiDayState.endDate,
                      });
                    } else {
                      setCollectRange(null);
                    }
                    setCollectDay(day);
                  }}
                  title={
                    page === "daily" && multiDayState.enabled
                      ? `查看 ${multiDayState.startDate} ~ ${multiDayState.endDate} 跨天已采集的对话会话与 Git 提交`
                      : "查看当天已采集的对话会话与 Git 提交；可勾选 Git 提交直接添加为工作项"
                  }
                >
                  <FolderGit2 size={16} />
                  {page === "daily" && multiDayState.enabled
                    ? "查看跨天详情"
                    : "查看采集详情"}
                </button>
                <button
                  className="primary"
                  disabled={collecting || analyzing}
                  onClick={() =>
                    page === "daily" && multiDayState.enabled
                      ? void collect({
                          startDate: multiDayState.startDate,
                          endDate: multiDayState.endDate,
                        })
                      : void collect()
                  }
                  title={
                    page === "daily" && multiDayState.enabled
                      ? `读取本地对话日志，并读取 ${multiDayState.startDate} 至 ${multiDayState.endDate} 期间仓库里的本人 Git 提交`
                      : "读取本地对话日志，并读取已配置仓库里你自己那天的 Git 提交"
                  }
                >
                  {collecting ? (
                    <LoaderCircle className="spin" size={17} />
                  ) : (
                    <ScanLine size={17} />
                  )}
                  {collecting
                    ? "正在采集…"
                    : page === "daily" && multiDayState.enabled
                      ? `采集跨天数据 (${multiDayState.startDate.slice(5)}~${multiDayState.endDate.slice(5)})`
                      : "采集当天数据"}
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
              <div className="datebar-actions">
                <span className="day-status">
                  <span
                    className={report?.confirmed ? "green-dot" : "amber-dot"}
                  />
                  {report?.confirmed ? "日报已确认" : "草稿 · 待审核"}
                </span>
                {report?.warnings && report.warnings.length > 0 && (
                  <button
                    type="button"
                    className="warning-badge-pill"
                    title="点击查看待审核提示（定位具体日期、所属区域与问题条目）"
                    onClick={() => {
                      const deletedPending = (
                        report.conversations.analysis?.pending_tasks || []
                      ).filter((t) => t.previously_deleted);
                      if (deletedPending.length > 0) {
                        setRecoveryModal({
                          day,
                          tasks: deletedPending,
                          warnings: [...new Set(report.warnings)],
                        });
                      } else {
                        setReviewAlert({
                          day,
                          warnings: [...new Set(report.warnings)],
                          items: report.review_items || [],
                        });
                      }
                    }}
                  >
                    <AlertCircle size={13} />
                    待审核提示（{report.review_items?.length || report.warnings.length}）
                  </button>
                )}
                <button
                  type="button"
                  className="outline gitee-browser-trigger"
                  onClick={() => setGiteeBrowserOpen(true)}
                  title="浏览已配置 Gitee 服务中的未完成工作项"
                >
                  <ArrowUpRight size={14} />
                  查看 Gitee 工作项
                </button>
              </div>
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
              <TodoReminderBar
                model={todoModel}
                onOpenDrawer={() => setTodoDrawerOpen(true)}
                onEditItem={(item) =>
                  setEditor({
                    task: {
                      ...item,
                      is_todo: item.is_todo ?? true,
                      confidence: "人工录入",
                      evidence_level: "人工说明",
                      suggested_minutes: null,
                      started_at: null,
                      ended_at: null,
                      evidence: [],
                      time_source: "manual",
                      time_reason: "",
                      timing: {
                        session_span_minutes: 0,
                        observable_active_minutes: 0,
                        tool_runtime_minutes: null,
                        gap_minutes: 5,
                        confidence: "",
                        method: "",
                      },
                    },
                  })
                }
              />
              {settings && (
                <Conversations
                  overview={report.conversations}
                  settings={settings}
                  currentDay={day}
                  busy={collecting || analyzing}
                  collecting={collecting}
                  analyzing={analyzing}
                  pendingCount={
                    report.conversations.analysis?.pending_tasks?.length || 0
                  }
                  legacyCount={report.legacy_count}
                  onAnalyze={analyzeDay}
                  onCollect={collect}
                  onOpenCollectDetails={(range) => {
                    setCollectRange(range || null);
                    setCollectDay(day);
                  }}
                  onMultiDayChange={setMultiDayState}
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
                      onClick={() => setEditor({})}
                    >
                      <Plus size={16} />
                      添加人工事项
                    </button>
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
                      disabled={!!busy || !report.tasks.length || checkingSimilarity}
                      onClick={runManualSimilarityCheck}
                      title="对比人工工作项与 AI 工作项的相似度并检查重复"
                    >
                      {checkingSimilarity ? (
                        <LoaderCircle className="spin" size={15} />
                      ) : (
                        <GitCompare size={15} />
                      )}
                      相似度检测
                    </button>
                    {selected.length >= 2 && (
                      <button className="outline" disabled={!!busy} onClick={merge}>
                        <GitMerge size={15} />
                        合并 {selected.length} 项
                      </button>
                    )}
                    <button
                      className="primary"
                      disabled={!!busy || !report.tasks.length || checkingSimilarity}
                      onClick={() => void handleOpenGiteePublish()}
                    >
                      <Send size={15} />
                      推送至 Gitee
                      {selected.length ? `（${selected.length}项）` : ""}
                    </button>
                  </div>
                </div>
                <div
                  className={
                    "allocation-bar" +
                    (allocating || report.allocation_running ? " running" : "")
                  }
                >
                  <div className="allocation-bar-main">
                    <span className="allocation-bar-icon">
                      {allocating || report.allocation_running ? (
                        <LoaderCircle className="spin" size={17} />
                      ) : (
                        <Sparkles size={17} />
                      )}
                    </span>
                    <div className="allocation-bar-copy">
                      <strong>AI 工时分配</strong>
                      <p>
                        {allocating || report.allocation_running
                          ? "正在按每日目标估算所选工作项，思考过程与 token 用量实时显示。"
                          : report.time_allocation?.stale
                            ? "工作项或设置已变化，请重新分配工时。"
                            : report.time_allocation?.pending
                              ? "工时建议已生成，确认后才会写入工作项。"
                              : report.time_allocation
                                ? `已按每日 ${hours(report.target_minutes)}h 目标完成分配，可预览分配明细。`
                                : `按每日 ${hours(report.target_minutes)}h 目标估算所选工作项，分配前可确认范围。`}
                      </p>
                    </div>
                    <div className="allocation-bar-right">
                    <button
                      className="outline"
                      disabled={
                        !!busy ||
                        allocating ||
                        checkingSimilarity ||
                        Boolean(report.conversations.running) ||
                        Boolean(report.allocation_running) ||
                        !settings?.llm_enabled ||
                        !report.tasks.length
                      }
                      title={
                        !settings?.llm_enabled
                          ? "请先在系统设置接通并开启模型分析"
                          : !report.tasks.length
                            ? "请先生成或补录工作项"
                            : undefined
                      }
                      onClick={handleOpenAllocation}
                    >
                      {allocating || report.allocation_running || checkingSimilarity ? (
                        <LoaderCircle className="spin" size={16} />
                      ) : (
                        <Sparkles size={16} />
                      )}
                      {allocating || report.allocation_running
                        ? "AI 正在分配…"
                        : checkingSimilarity
                          ? "正在核对相似度…"
                          : `AI 估算并分配 ${hours(report.target_minutes)}h`}
                    </button>
                    {report.time_allocation && (
                      <button
                        className="outline"
                        disabled={
                          !!busy ||
                          allocating ||
                          Boolean(report.allocation_running)
                        }
                        onClick={() => setAllocationPreviewOpen(true)}
                      >
                        <FileText size={15} />
                        {report.time_allocation.pending
                          ? "预览并应用"
                          : "预览工时"}
                      </button>
                    )}
                    </div>
                  </div>
                  {allocationError && (
                    <div role="alert" className="allocation-error">
                      <strong>AI 工时分配失败</strong>
                      <p>{allocationError}</p>
                      <button
                        type="button"
                        className="outline"
                        onClick={() => setAllocationError("")}
                      >
                        关闭
                      </button>
                    </div>
                  )}
                  <div className="allocation-bar-progress">
                    <AnalysisLog
                      day={report.date}
                      active={
                        allocating || Boolean(report.allocation_running)
                      }
                      endpoint="/api/allocation/progress"
                      label="AI 工时分配思考与用量"
                      runningLabel="AI 正在估算工时"
                      doneLabel="AI 工时估算已完成"
                      prepareLabel="正在准备估算…"
                    />
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
                        {report.tasks.map((t, rowIdx) => {
                          const rowNumber = rowIdx + 1;
                          const matchedAlerts = (report.review_items || []).filter(
                            (ri) => ri.ids && ri.ids.includes(t.id),
                          );
                          return (
                          <tr
                            key={t.id}
                            id={`task-row-${t.id}`}
                            className={matchedAlerts.length > 0 ? "task-row-has-warning" : ""}
                          >
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
                                <span className="task-row-index">#{rowNumber}</span>
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
                                {matchedAlerts.length > 0 && (
                                  <button
                                    type="button"
                                    className="task-row-warning-tag"
                                    title={matchedAlerts.map((m) => m.reason).join("\n")}
                                    onClick={() =>
                                      setReviewAlert({
                                        day,
                                        warnings: [...new Set(report.warnings)],
                                        items: report.review_items || [],
                                      })
                                    }
                                  >
                                    <AlertCircle size={11} />
                                    待核对（与{" "}
                                    {matchedAlerts
                                      .flatMap((m) =>
                                        m.indices.filter((idx) => idx !== rowNumber),
                                      )
                                      .map((idx) => `#${idx}`)
                                      .join("、") || "其他项"}{" "}
                                    {matchedAlerts[0].kind === "similarity"
                                      ? "目标相似"
                                      : "时间重叠"}
                                    ）
                                  </button>
                                )}
                                <button onClick={() => setProof(t)}>
                                  {t.evidence.length} 条证据{" "}
                                  <ArrowUpRight size={12} />
                                </button>
                              </div>
                            </td>
                            <td>
                              <ProjectQuickSelect
                                task={t}
                                onUpdate={async (project) => {
                                  await api(`/tasks/${t.id}`, "PATCH", { project });
                                  await refresh();
                                }}
                              />
                              <span className="category">{t.category}</span>
                            </td>
                            <td>
                              <StatusQuickSelect
                                task={t}
                                todayStr={today(settings?.timezone)}
                                onUpdate={async (status, extra) => {
                                  await api(`/tasks/${t.id}`, "PATCH", { status, ...extra });
                                  await refresh();
                                }}
                              />
                            </td>
                            <td className="hours">
                              <HoursQuickRegister
                                task={t}
                                step={settings?.step_minutes || 15}
                                onUpdate={async (confirmed_minutes, suggested_minutes, time_locked) => {
                                  await api(`/tasks/${t.id}`, "PATCH", {
                                    confirmed_minutes,
                                    suggested_minutes,
                                    time_locked,
                                  });
                                  await refresh();
                                }}
                              />
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
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
              {report.time_allocation && (
                <div className="allocation-note">
                  <strong>
                    {report.time_allocation.stale
                      ? "工作项已变化，工时建议待重新审核"
                      : report.time_allocation.pending
                        ? "AI 工时建议待应用"
                        : "AI 工时分配已应用"}
                  </strong>
                  <p>
                    固定 {hours(report.time_allocation.fixed_minutes)}h
                    {report.time_allocation.preserved_minutes
                      ? ` · 保留 ${hours(report.time_allocation.preserved_minutes)}h`
                      : ""}{" "}
                    · 目标分配{" "}
                    {hours(report.time_allocation.distributed_minutes)}h ·
                    AI独立估算{" "}
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
                        ? "日报已确认"
                        : report.remaining_minutes === 0
                          ? "工时已分配完成，请核对并确认"
                          : report.remaining_minutes > 0
                            ? `距离目标工时还差 ${hours(report.remaining_minutes)}h`
                            : `当前工时超出 ${hours(-report.remaining_minutes)}h`}
                    </h3>
                  </div>
                </div>
                <div className="actions">
                  <button
                    className="outline"
                    disabled={!!busy}
                    onClick={exportReport}
                    title="导出 Markdown 日报"
                  >
                    <ArrowDownToLine size={16} />
                    导出 Markdown
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
          {page === "settings" && settings && (
            <SettingsForm
              key={settingsEpoch}
              initial={settings}
              busy={!!busy}
              tab={settingsTab}
              onTab={setSettingsTab}
              theme={theme}
              onThemeChange={setTheme}
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
          taskIds={selected.filter((id) => report?.tasks.some((t) => t.id === id))}
          onClose={() => setGiteeOpen(false)}
          onSettings={() => {
            setGiteeOpen(false);
            setPage("settings");
            setSettingsTab("enterprise");
          }}
          onApplied={refresh}
        />
      )}
      {giteeBrowserOpen && (
        <GiteeIssueBrowserModal
          initialDate={day}
          onClose={() => setGiteeBrowserOpen(false)}
          onChanged={refresh}
          onSettings={() => {
            setGiteeBrowserOpen(false);
            setPage("settings");
            setSettingsTab("enterprise");
          }}
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
      {similarityOpen && (
        <SimilarityMergeModal
          day={day}
          pairs={similarityPairs}
          onClose={() => setSimilarityOpen(false)}
          onApplied={async () => {
            await refresh();
          }}
          onProceedToAllocation={
            proceedToAllocationAfterSimilarity
              ? () => setAllocationOpen(true)
              : undefined
          }
          onProceedToGitee={
            proceedToGiteeAfterSimilarity
              ? () => setGiteeOpen(true)
              : undefined
          }
        />
      )}
      {allocationOpen && report && (
        <AllocationModal
          report={report}
          onClose={() => setAllocationOpen(false)}
          onStart={startAllocation}
        />
      )}
      {allocationPreviewOpen && report && report.time_allocation && (
        <AllocationPreviewModal
          report={report}
          onClose={() => setAllocationPreviewOpen(false)}
          onApplied={async (message) => {
            setToast(null);
            await refresh();
            setNotice(message);
          }}
        />
      )}
      <TodoSideDock
        model={todoModel}
        open={todoDrawerOpen}
        onToggle={setTodoDrawerOpen}
        onEditItem={(item) => {
          setTodoDrawerOpen(false);
          setEditor({
            task: {
              ...item,
              is_todo: item.is_todo ?? true,
              confidence: "人工录入",
              evidence_level: "人工说明",
              suggested_minutes: null,
              started_at: null,
              ended_at: null,
              evidence: [],
              time_source: "manual",
              time_reason: "",
              timing: {
                session_span_minutes: 0,
                observable_active_minutes: 0,
                tool_runtime_minutes: null,
                gap_minutes: 5,
                confidence: "",
                method: "",
              },
            },
          });
        }}
      />
      {editor && (
        <TaskEditor
          editor={editor}
          day={day}
          todayStr={today(settings?.timezone)}
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
          startDate={collectRange?.startDate}
          endDate={collectRange?.endDate}
          timezone={settings?.timezone || "Asia/Shanghai"}
          onClose={() => {
            setCollectDay(null);
            setCollectRange(null);
          }}
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
          warnings={report?.warnings || []}
          onClose={() => setAnalysisView(null)}
          onImported={async (simCheck) => {
            await refresh(analysisView.day);
            if (simCheck?.has_duplicates && simCheck.pairs.length > 0) {
              setAnalysisView(null);
              setSimilarityPairs(simCheck.pairs);
              setProceedToAllocationAfterSimilarity(false);
              setProceedToGiteeAfterSimilarity(false);
              setSimilarityOpen(true);
            }
          }}
        />
      )}
      {recoveryModal && (
        <DeletedRecoveryModal
          day={recoveryModal.day}
          tasks={recoveryModal.tasks}
          warnings={recoveryModal.warnings}
          onClose={() => setRecoveryModal(null)}
          onImported={async () => {
            await refresh(recoveryModal.day);
          }}
        />
      )}
      {reviewAlert && (
        <ReviewAlertModal
          alert={reviewAlert}
          tasks={report?.tasks || []}
          onClose={() => setReviewAlert(null)}
          onLocateAndSelect={(ids) => {
            setSelected(ids);
            setReviewAlert(null);
            if (ids.length > 0) {
              setTimeout(() => {
                const el = document.getElementById(`task-row-${ids[0]}`);
                el?.scrollIntoView({ behavior: "smooth", block: "center" });
              }, 80);
            }
          }}
          onQuickMerge={async (ids) => {
            try {
              await api("/tasks/merge", "POST", { ids });
              setReviewAlert(null);
              await refresh();
              setNotice("已合并所选重复工作项。");
            } catch (e) {
              setError((e as Error).message);
              setReviewAlert(null);
            }
          }}
          onRunSimilarityCheck={() => {
            setReviewAlert(null);
            void runManualSimilarityCheck();
          }}
        />
      )}
      {toast && (
        <JobToast toast={toast} onClose={() => setToast(null)} />
      )}
    </div>
  );
}

function ReviewAlertModal({
  alert,
  tasks,
  onClose,
  onLocateAndSelect,
  onQuickMerge,
  onRunSimilarityCheck,
}: {
  alert: { day: string; warnings: string[]; items?: ReviewAlertItem[] };
  tasks: Task[];
  onClose: () => void;
  onLocateAndSelect: (ids: string[]) => void;
  onQuickMerge: (ids: string[]) => Promise<void>;
  onRunSimilarityCheck: () => void;
}) {
  const [mergingKey, setMergingKey] = useState<string | null>(null);
  const taskMap = new Map(tasks.map((t, idx) => [t.id, { task: t, index: idx + 1 }]));
  const structuredItems = alert.items && alert.items.length > 0 ? alert.items : null;

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="待审核提示"
      onClick={onClose}
    >
      <div
        className="modal alert-modal alert-modal-structured"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="alert-modal-head-row">
          <div className="alert-modal-badge">
            <AlertCircle size={22} />
          </div>
          <div className="alert-modal-head-copy">
            <h3 className="alert-modal-title">
              待审核提示 · {alert.day}
            </h3>
            <p className="alert-modal-subtitle">
              共发现 {structuredItems ? structuredItems.length : alert.warnings.length} 处需核对的数据项（已标明具体位置、日期与清单序号）
            </p>
          </div>
          <button
            type="button"
            className="alert-modal-close"
            aria-label="关闭待审核提示"
            onClick={onClose}
          >
            <X size={18} />
          </button>
        </div>

        <div className="alert-modal-body">
          {structuredItems ? (
            structuredItems.map((item, i) => {
              const key = `${item.kind}-${i}-${item.ids.join("-")}`;
              const resolvedTasks = item.ids
                .map((id) => taskMap.get(id))
                .filter((x): x is { task: Task; index: number } => Boolean(x));
              const canMerge =
                resolvedTasks.length === 2 &&
                resolvedTasks[0].task.project === resolvedTasks[1].task.project;
              const kindLabel =
                item.kind === "similarity"
                  ? `目标高度相似${item.similarity ? ` (${Math.round(item.similarity * 100)}%)` : ""}`
                  : item.kind === "tool_overlap"
                    ? "工具活动时段重叠"
                    : item.kind === "time_overlap"
                      ? "起止时间重叠"
                      : "AI 分析过滤提示";

              return (
                <div key={key} className="alert-structured-card">
                  <div className="alert-structured-meta">
                    <span className="alert-meta-pill location">{item.location}</span>
                    <span className="alert-meta-pill date">日期：{item.date}</span>
                    {item.evidence_dates &&
                      item.evidence_dates.length > 0 &&
                      item.evidence_dates.join("/") !== item.date && (
                        <span className="alert-meta-pill date">
                          证据日期：{item.evidence_dates.join(" / ")}
                        </span>
                      )}
                    {item.project && (
                      <span className="alert-meta-pill project">项目：{item.project}</span>
                    )}
                    <span className="alert-meta-pill kind">{kindLabel}</span>
                  </div>

                  {resolvedTasks.length > 0 ? (
                    <div className="alert-structured-tasks">
                      {resolvedTasks.map(({ task, index }) => (
                        <div key={task.id} className="alert-task-row">
                          <span className="alert-task-index">第 #{index} 条</span>
                          <span className="alert-task-title">{task.title}</span>
                          <span className="alert-task-info">
                            {task.origin === "manual" ? "人工补录" : "AI / Git"} ·{" "}
                            {hours(task.confirmed_minutes)}h
                          </span>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="alert-modal-warning-item">{item.reason}</div>
                  )}

                  {item.detail && resolvedTasks.length > 0 && (
                    <div className="alert-structured-reason">
                      问题说明：{item.detail} — 建议工时不可直接叠加，请核对是否重复或需合并。
                    </div>
                  )}

                  {item.ids.length > 0 && (
                    <div className="alert-structured-actions">
                      <button
                        type="button"
                        className="outline sm-btn"
                        onClick={() => onLocateAndSelect(item.ids)}
                      >
                        在清单中定位并勾选（#{item.indices.join("、#")}）
                      </button>
                      {canMerge && (
                        <button
                          type="button"
                          className="primary sm-btn"
                          disabled={mergingKey === key}
                          onClick={async () => {
                            setMergingKey(key);
                            try {
                              await onQuickMerge(item.ids);
                            } finally {
                              setMergingKey(null);
                            }
                          }}
                        >
                          <GitMerge size={13} />
                          {mergingKey === key ? "合并中…" : "直接合并这 2 条"}
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          ) : (
            alert.warnings.map((w, i) => (
              <div key={i} className="alert-modal-warning-item">
                {w}
              </div>
            ))
          )}
          <p className="alert-modal-hint">
            提示：清单中每条任务左侧已标明序号（#1、#2…），可直接定位对应行，或使用「相似度检测」交由 AI 比对合并。
          </p>
        </div>
        <div className="alert-modal-actions">
          <button
            className="outline"
            type="button"
            onClick={onRunSimilarityCheck}
          >
            <GitCompare size={15} />
            打开 AI 相似度检测
          </button>
          <button className="primary" type="button" onClick={onClose}>
            我知道了
          </button>
        </div>
      </div>
    </div>
  );
}

type ManualTemplate = {
  id: string;
  name: string;
  title: string;
  details: string;
  project: string;
  category: string;
  status: string;
  confirmed_minutes: number;
  time_locked: boolean;
};
const templateKeys = [
  "title",
  "details",
  "project",
  "category",
  "status",
  "confirmed_minutes",
] as const;
type TemplateKey = (typeof templateKeys)[number];
// 模板中“固定”的字段：有实际内容的字段在快捷创建时隐藏。
function fixedFields(t: ManualTemplate | null): Set<TemplateKey> {
  const s = new Set<TemplateKey>();
  if (!t) return s;
  if (t.title.trim()) s.add("title");
  if (t.details.trim()) s.add("details");
  if (t.project.trim()) s.add("project");
  if (t.category) s.add("category");
  if (t.status) s.add("status");
  if (t.confirmed_minutes > 0) s.add("confirmed_minutes");
  return s;
}
const syncCloud = () => {
  api("/cloud/sync", "POST").catch(() => undefined);
};

function TaskEditor({
  editor,
  day,
  todayStr,
  step,
  onClose,
  onSave,
}: {
  editor: Editor;
  day: string;
  todayStr: string;
  step: number;
  onClose: () => void;
  onSave: (parts: Draft[]) => Promise<void>;
}) {
  const isNew = !editor.task && !editor.split;
  const [parts, setParts] = useState<Draft[]>(() => {
    if (!editor.split) return [fromTask(editor.task, day)];
    const origConfirmed = editor.task!.confirmed_minutes || 0;
    const half1 = Math.floor(origConfirmed / 2);
    const half2 = Math.ceil(origConfirmed / 2);
    const hasSuggested = editor.task!.suggested_minutes != null || origConfirmed > 0;
    return [
      {
        ...fromTask(editor.task, day),
        title: editor.task!.title + "（一）",
        confirmed_minutes: half1,
        confirmedHoursText: hours(half1),
        suggested_minutes: hasSuggested ? half1 : null,
        suggestedHoursText: hasSuggested ? hours(half1) : "",
      },
      {
        ...fromTask(editor.task, day),
        title: editor.task!.title + "（二）",
        confirmed_minutes: half2,
        confirmedHoursText: hours(half2),
        suggested_minutes: hasSuggested ? half2 : null,
        suggestedHoursText: hasSuggested ? hours(half2) : "",
      },
    ];
  });
  const [error, setError] = useState(""),
    [saving, setSaving] = useState(false),
    [projects, setProjects] = useState<string[]>([]),
    [enterpriseConfigured, setEnterpriseConfigured] = useState(false),
    [enterpriseId, setEnterpriseId] = useState(0),
    [refreshingProjects, setRefreshingProjects] = useState(false),
    [templates, setTemplates] = useState<ManualTemplate[]>([]),
    [projectPrefixes, setProjectPrefixes] = useState<Record<string, string>>({}),
    [bracketStyle, setBracketStyle] = useState("【】"),
    [defaultPrefix, setDefaultPrefix] = useState("其他"),
    [active, setActive] = useState<ManualTemplate | null>(null),
    [showAll, setShowAll] = useState(false),
    [templateName, setTemplateName] = useState<string | null>(null);
  const firstTitleRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    firstTitleRef.current?.focus();
  }, []);
  const loadManualOptions = (refresh = false) => {
    if (refresh) setRefreshingProjects(true);
    return api<{
      templates: ManualTemplate[];
      projects: string[];
      enterprise_configured?: boolean;
      enterprise_id?: number;
      project_prefixes?: Record<string, string>;
      bracket_style?: string;
      default_prefix?: string;
    }>(`/manual-options${refresh ? "?refresh=true" : ""}`)
      .then((r) => {
        setTemplates(r.templates || []);
        setProjects(r.projects || []);
        if (r.project_prefixes) setProjectPrefixes(r.project_prefixes);
        if (r.bracket_style) setBracketStyle(r.bracket_style);
        if (r.default_prefix) setDefaultPrefix(r.default_prefix);
        if (typeof r.enterprise_configured === "boolean") {
          setEnterpriseConfigured(r.enterprise_configured);
        }
        if (typeof r.enterprise_id === "number") {
          setEnterpriseId(r.enterprise_id);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (refresh) setRefreshingProjects(false);
      });
  };
  useEffect(() => {
    void loadManualOptions();
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !saving) {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [saving, onClose]);
  const change = (
    i: number,
    key: keyof Draft,
    value: string | number | boolean | null,
  ) =>
    setParts(
      parts.map((p, j) => {
        if (i !== j) return p;
        let nextTitle = p.title;
        if (key === "project") {
          const nextProj = String(value);
          if (p.title.trim() && !hasBracketPrefix(p.title)) {
            nextTitle = formatTitleWithPrefix(
              p.title,
              nextProj,
              projectPrefixes,
              bracketStyle,
              defaultPrefix,
            );
          }
        }
        const extra: Partial<Draft> = {};
        if (key === "confirmed_minutes") {
          const mins = Math.min(1440, Math.max(0, Math.round(Number(value) || 0)));
          extra.confirmed_minutes = mins;
          extra.confirmedHoursText = hours(mins);
          extra.suggested_minutes = mins;
          extra.suggestedHoursText = hours(mins);
          extra.time_locked = mins > 0;
        } else if (key === "suggested_minutes") {
          if (value === null || value === undefined || value === "") {
            extra.suggested_minutes = null;
            extra.suggestedHoursText = "";
          } else {
            const mins = Math.min(1440, Math.max(0, Math.round(Number(value) || 0)));
            extra.suggested_minutes = mins;
            extra.suggestedHoursText = hours(mins);
          }
        }
        return {
          ...p,
          [key]: value,
          ...(key === "project" ? { title: nextTitle } : {}),
          ...extra,
          // 待办设为已完成时，工作日期自动选为当天
          ...(key === "status" && value === "delivered" && p.status === "requested"
            ? { date: todayStr }
            : {}),
        };
      }),
    );
  const applyTemplate = (t: ManualTemplate | null) => {
    setActive(t);
    setShowAll(false);
    const base = fromTask(undefined, parts[0].date || day);
    setParts([
      t
        ? {
            ...base,
            title: t.title,
            details: t.details,
            project: t.project,
            category: t.category || "任务",
            status: t.status || "delivered",
            is_todo: false,
            confirmed_minutes: t.confirmed_minutes,
            confirmedHoursText: hours(t.confirmed_minutes || 0),
            suggested_minutes: t.confirmed_minutes > 0 ? t.confirmed_minutes : null,
            suggestedHoursText: t.confirmed_minutes > 0 ? hours(t.confirmed_minutes) : "",
            time_locked: t.time_locked,
          }
        : base,
    ]);
  };
  const saveTemplates = async (next: ManualTemplate[]) => {
    const saved = await api<ManualTemplate[]>("/manual-templates", "PUT", next);
    setTemplates(saved);
    syncCloud();
    return saved;
  };
  const saveAsTemplate = async () => {
    const name = (templateName || "").trim();
    if (!name) return;
    const p = parts[0];
    const t: ManualTemplate = {
      id: Math.random().toString(36).slice(2, 12) + Date.now().toString(36),
      name: name.slice(0, 100),
      title: p.title,
      details: p.details,
      project: p.project,
      category: p.category,
      status: p.status,
      confirmed_minutes: p.confirmed_minutes,
      time_locked: p.time_locked,
    };
    try {
      await saveTemplates([...templates, t]);
      setTemplateName(null);
      setActive(t);
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const hidden = isNew && !showAll ? fixedFields(active) : new Set<TemplateKey>();
  const statusOptions = (cur: string) =>
    selectableStatuses.includes(cur) ? selectableStatuses : [cur, ...selectableStatuses];
  const categoryOptions = (cur: string) =>
    !cur || categories.includes(cur) ? categories : [cur, ...categories];
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
            const formattedParts = parts.map((p) => ({
              ...p,
              title: hasBracketPrefix(p.title)
                ? p.title
                : formatTitleWithPrefix(
                    p.title,
                    p.project,
                    projectPrefixes,
                    bracketStyle,
                    defaultPrefix,
                  ),
            }));
            await onSave(formattedParts);
            syncCloud();
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
            title="关闭（Esc）"
            disabled={saving}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {isNew && (
          <div className="manual-quick">
            <div className="manual-templates">
              <span className="manual-templates-label">模板</span>
              <button
                type="button"
                className={"template-chip" + (active ? "" : " active")}
                onClick={() => applyTemplate(null)}
              >
                空白
              </button>
              {templates.map((t) => (
                <span
                  key={t.id}
                  className={"template-chip" + (active?.id === t.id ? " active" : "")}
                >
                  <button type="button" onClick={() => applyTemplate(t)}>
                    {t.name}
                  </button>
                  <button
                    type="button"
                    aria-label={"删除模板 " + t.name}
                    onClick={async () => {
                      if (!window.confirm(`删除模板「${t.name}」？`)) return;
                      try {
                        await saveTemplates(templates.filter((x) => x.id !== t.id));
                        if (active?.id === t.id) applyTemplate(null);
                      } catch (e) {
                        setError((e as Error).message);
                      }
                    }}
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
              {templateName === null ? (
                <button
                  type="button"
                  className="template-chip add"
                  onClick={() => setTemplateName("")}
                >
                  <Plus size={12} /> 存为模板
                </button>
              ) : (
                <span className="template-new">
                  <input
                    autoFocus
                    maxLength={100}
                    placeholder="模板名称，回车保存"
                    value={templateName}
                    onChange={(e) => setTemplateName(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") {
                        e.preventDefault();
                        void saveAsTemplate();
                      } else if (e.key === "Escape") {
                        e.stopPropagation();
                        setTemplateName(null);
                      }
                    }}
                  />
                  <button type="button" onClick={() => void saveAsTemplate()}>
                    <Check size={14} />
                  </button>
                </span>
              )}
            </div>
            {active && hidden.size > 0 && (
              <button
                type="button"
                className="link-button"
                onClick={() => setShowAll(true)}
              >
                已隐藏模板固定内容，显示全部字段
              </button>
            )}
          </div>
        )}
        {parts.map((p, i) => (
          <fieldset key={i}>
            {editor.split && <legend>任务 {i + 1}</legend>}
            {!hidden.has("title") && (
              <label>
                任务标题
                <input
                  ref={i === 0 ? firstTitleRef : undefined}
                  autoFocus={i === 0}
                  required
                  maxLength={500}
                  value={p.title}
                  onChange={(e) => change(i, "title", e.target.value)}
                  placeholder="例如：与客户确认接口联调方案"
                />
              </label>
            )}
            {!hidden.has("details") && (
              <label>
                详细工作内容
                <textarea
                  rows={4}
                  value={p.details}
                  onChange={(e) => change(i, "details", e.target.value)}
                  placeholder="做了什么、采取哪些措施、有哪些产出或待处理事项…"
                />
              </label>
            )}
            <div className="form-grid">
              {!hidden.has("project") && (
                <label>
                  <div
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      marginBottom: "4px",
                    }}
                  >
                    <span>所属项目</span>
                    {enterpriseConfigured && enterpriseId > 0 && (
                      <button
                        type="button"
                        className="link-button"
                        disabled={refreshingProjects}
                        onClick={() => void loadManualOptions(true)}
                        style={{
                          fontSize: "12px",
                          padding: 0,
                          cursor: refreshingProjects ? "wait" : "pointer",
                        }}
                      >
                        {refreshingProjects ? "读取中…" : "刷新企业项目"}
                      </button>
                    )}
                  </div>
                  <input
                    list={"projects-" + i}
                    value={p.project}
                    onChange={(e) => change(i, "project", e.target.value)}
                    placeholder={
                      projects.length
                        ? "选择 Gitee 企业项目或手动输入"
                        : enterpriseConfigured && enterpriseId > 0
                        ? "暂未读取到企业项目，可点击右上角刷新"
                        : enterpriseConfigured
                        ? "已配置令牌，请在系统设置中选择企业"
                        : "项目或客户名称，可不绑定 Git"
                    }
                  />
                  <datalist id={"projects-" + i}>
                    {projects.map((c) => (
                      <option key={c} value={c} />
                    ))}
                  </datalist>
                </label>
              )}
              {!hidden.has("category") && (
                <label>
                  工作分类
                  <select
                    value={p.category}
                    onChange={(e) => change(i, "category", e.target.value)}
                  >
                    {categoryOptions(p.category).map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!hidden.has("status") && (
                <label>
                  完成情况
                  <select
                    value={p.status}
                    onChange={(e) => change(i, "status", e.target.value)}
                  >
                    {statusOptions(p.status).map((v) => (
                      <option key={v} value={v}>
                        {statuses[v] || v}
                      </option>
                    ))}
                  </select>
                </label>
              )}
              {!hidden.has("confirmed_minutes") && (
                <>
                  <label>
                    完成工时（小时，自动同步预计工时）
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
                        aria-label={"完成工时小时 " + (i + 1)}
                        type="number"
                        required
                        min="0"
                        max="24"
                        step="any"
                        value={
                          p.confirmedHoursText !== undefined
                            ? p.confirmedHoursText
                            : hours(p.confirmed_minutes)
                        }
                        onChange={(e) => {
                          const raw = e.target.value;
                          setParts((prev) =>
                            prev.map((item, j) => {
                              if (j !== i) return item;
                              if (raw.trim() === "") {
                                return { ...item, confirmedHoursText: raw };
                              }
                              const num = Number(raw);
                              if (!Number.isFinite(num) || num < 0 || num > 24) {
                                return { ...item, confirmedHoursText: raw };
                              }
                              const mins = Math.min(1440, Math.max(0, Math.round(num * 60)));
                              return {
                                ...item,
                                confirmed_minutes: mins,
                                confirmedHoursText: raw,
                                suggested_minutes: mins,
                                suggestedHoursText: hours(mins),
                                time_locked: mins > 0,
                              };
                            }),
                          );
                        }}
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
                      <span>= {p.confirmed_minutes} 分钟</span>
                    </div>
                  </label>
                  <label>
                    预计工时（Gitee 预估工时，小时）
                    <div className="minute-input">
                      <button
                        type="button"
                        aria-label={"减少预计工时 " + (i + 1)}
                        onClick={() =>
                          change(
                            i,
                            "suggested_minutes",
                            Math.max(0, (p.suggested_minutes || 0) - step),
                          )
                        }
                      >
                        −
                      </button>
                      <input
                        aria-label={"预计工时小时 " + (i + 1)}
                        type="number"
                        min="0"
                        max="24"
                        step="any"
                        placeholder="未设置"
                        value={
                          p.suggestedHoursText !== undefined
                            ? p.suggestedHoursText
                            : p.suggested_minutes == null
                              ? ""
                              : hours(p.suggested_minutes)
                        }
                        onChange={(e) => {
                          const raw = e.target.value;
                          setParts((prev) =>
                            prev.map((item, j) => {
                              if (j !== i) return item;
                              if (raw.trim() === "") {
                                return {
                                  ...item,
                                  suggested_minutes: null,
                                  suggestedHoursText: "",
                                };
                              }
                              const num = Number(raw);
                              if (!Number.isFinite(num) || num < 0 || num > 24) {
                                return { ...item, suggestedHoursText: raw };
                              }
                              const mins = Math.min(1440, Math.max(0, Math.round(num * 60)));
                              return {
                                ...item,
                                suggested_minutes: mins,
                                suggestedHoursText: raw,
                              };
                            }),
                          );
                        }}
                      />
                      <button
                        type="button"
                        aria-label={"增加预计工时 " + (i + 1)}
                        onClick={() =>
                          change(
                            i,
                            "suggested_minutes",
                            Math.min(1440, (p.suggested_minutes || 0) + step),
                          )
                        }
                      >
                        +
                      </button>
                      <span>
                        {p.suggested_minutes != null
                          ? `= ${p.suggested_minutes} 分钟`
                          : "未设置"}
                      </span>
                    </div>
                  </label>
                </>
              )}
            </div>
            <label>
              工作日期
              <input
                required
                type="date"
                value={p.date}
                onChange={(e) => change(i, "date", e.target.value)}
              />
            </label>
            <label className="check-label">
              <input
                type="checkbox"
                checked={p.time_locked}
                onChange={(e) => change(i, "time_locked", e.target.checked)}
              />
              固定此项工时，AI分配时保留
            </label>
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
  theme,
  onThemeChange,
}: {
  onImported: () => void;
  initial: Settings;
  busy: boolean;
  tab: SettingsTab;
  onTab: (tab: SettingsTab) => void;
  theme: string;
  onThemeChange: (theme: string) => void;
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
            {tab === "model" && (
              <section className="settings-section">
                <h2>
                  <PieChart size={19} /> AI 用量统计
                </h2>
                <UsageStats />
              </section>
            )}
            {tab === "operation" && (
              <>
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
                  <label className="check-label">
                    <input
                      type="checkbox"
                      checked={s.auto_apply_allocation}
                      onChange={(e) =>
                        setS({
                          ...s,
                          auto_apply_allocation: e.target.checked,
                        })
                      }
                    />
                    AI 分配工时后直接应用到工作项
                  </label>
                  <p className="muted">
                    关闭后先生成工时预览，在「今日工作清单」右侧确认后再应用。
                  </p>
                  <label>
                    任务项详细描述（Details）
                    <select
                      value={s.task_details_mode ?? "standard"}
                      onChange={(e) =>
                        setS({
                          ...s,
                          task_details_mode: e.target.value as
                            | "standard"
                            | "concise"
                            | "none",
                        })
                      }
                    >
                      <option value="standard">标准（完整描述）</option>
                      <option value="concise">精简（一句话，40 字以内）</option>
                      <option value="none">不生成（仅保留标题）</option>
                    </select>
                  </label>
                  <p className="muted">
                    {s.task_details_mode === "none"
                      ? "AI 只生成任务标题，任务的详细描述保持为空。"
                      : s.task_details_mode === "concise"
                        ? "AI 生成的详细描述会精简为一句核心改动与结果，控制在 40 字以内。"
                        : "AI 按完整格式生成任务的详细描述。"}
                  </p>
                </section>
                <ThemeLibrarySection
                  theme={theme}
                  onChange={onThemeChange}
                />
              </>
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
