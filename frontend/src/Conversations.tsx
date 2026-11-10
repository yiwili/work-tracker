import { useState, useEffect, useCallback } from "react";
import {
  Activity,
  ArrowUpRight,
  Check,
  LoaderCircle,
  Plus,
  Settings2,
  Sparkles,
  CalendarRange,
  ScanLine,
  FolderGit2,
  RotateCcw,
} from "lucide-react";
import type { ConversationOverview, Settings } from "./types";
import { api } from "./api";
import { sourceLabels } from "./SourcePicker";
import { SourceIcon } from "./SourceIcon";
import { AnalysisLog } from "./AnalysisLog";

export type AnalyzeOptions = {
  startDate?: string;
  endDate?: string;
  targetMinutes?: number;
  autoCollect?: boolean;
};

export type CollectOptions = {
  startDate?: string;
  endDate?: string;
  silentToast?: boolean;
};

const formatTokens = (value: number) => value.toLocaleString("zh-CN");

const names: Record<string, string> = {
  ...sourceLabels,
  trae_cn: "Trae 国内版",
  trae_intl: "Trae 国际版",
  workbuddy: "WorkBuddy（旧版）",
  qoder: "Qoder（旧版）",
  trae: "Trae（旧版）",
  git: "Git 辅助证据",
};

export function Conversations({
  overview,
  settings,
  currentDay,
  busy,
  collecting,
  analyzing,
  pendingCount,
  legacyCount,
  onAnalyze,
  onCollect,
  onOpenCollectDetails,
  onMultiDayChange,
  onSettings,
  onSources,
  onView,
  onViewPending,
}: {
  overview: ConversationOverview;
  settings: Settings;
  currentDay?: string;
  busy: boolean;
  collecting?: boolean;
  analyzing: boolean;
  pendingCount: number;
  legacyCount: number;
  onAnalyze: (options?: AnalyzeOptions) => void;
  onCollect?: (options?: CollectOptions) => Promise<boolean | void> | void;
  onOpenCollectDetails?: (options?: { startDate?: string; endDate?: string }) => void;
  onMultiDayChange?: (state: { enabled: boolean; startDate: string; endDate: string }) => void;
  onSettings: () => void;
  onSources: (sources: string[]) => void;
  onView: (source: string, session: string) => void;
  onViewPending: () => void;
}) {
  const [sourceTab, setSourceTab] = useState<"ready" | "all">("ready");
  const effectiveDay = currentDay || overview.date || new Date().toISOString().slice(0, 10);
  const [showMultiDay, setShowMultiDay] = useState(false);
  const [startDate, setStartDate] = useState(() => {
    const d = new Date(effectiveDay + "T12:00:00");
    d.setDate(d.getDate() - 1);
    return d.toISOString().slice(0, 10);
  });
  const [endDate, setEndDate] = useState(effectiveDay);
  const [customHours, setCustomHours] = useState<number | null>(null);
  const [autoCollectBeforeAnalyze, setAutoCollectBeforeAnalyze] = useState(true);
  const [multiOverview, setMultiOverview] = useState<ConversationOverview | null>(null);
  const [loadingRange, setLoadingRange] = useState(false);

  useEffect(() => {
    setEndDate(effectiveDay);
    const d = new Date(effectiveDay + "T12:00:00");
    d.setDate(d.getDate() - 1);
    setStartDate(d.toISOString().slice(0, 10));
    setCustomHours(null);
  }, [effectiveDay]);

  useEffect(() => {
    onMultiDayChange?.({
      enabled: showMultiDay,
      startDate,
      endDate,
    });
  }, [showMultiDay, startDate, endDate, onMultiDayChange]);

  const fetchRangeOverview = useCallback(async () => {
    if (!showMultiDay) {
      setMultiOverview(null);
      return;
    }
    setLoadingRange(true);
    try {
      const q = new URLSearchParams({
        date: effectiveDay,
        start_date: startDate,
        end_date: endDate,
      });
      const res = await api<ConversationOverview>("/conversations?" + q.toString());
      setMultiOverview(res);
    } catch {
      // Fall back to single-day overview if range query fails
    } finally {
      setLoadingRange(false);
    }
  }, [showMultiDay, effectiveDay, startDate, endDate]);

  useEffect(() => {
    void fetchRangeOverview();
  }, [fetchRangeOverview, overview, settings.analysis_sources]);

  const applyPreset = (preset: "2d" | "3d" | "5d" | "week") => {
    const base = new Date(effectiveDay + "T12:00:00");
    setEndDate(effectiveDay);
    setCustomHours(null);
    if (preset === "2d") {
      base.setDate(base.getDate() - 1);
      setStartDate(base.toISOString().slice(0, 10));
    } else if (preset === "3d") {
      base.setDate(base.getDate() - 2);
      setStartDate(base.toISOString().slice(0, 10));
    } else if (preset === "5d") {
      base.setDate(base.getDate() - 4);
      setStartDate(base.toISOString().slice(0, 10));
    } else if (preset === "week") {
      const dayOfWeek = base.getDay(); // 0 is Sun, 1 is Mon
      const diffToMonday = dayOfWeek === 0 ? 6 : dayOfWeek - 1;
      base.setDate(base.getDate() - diffToMonday);
      setStartDate(base.toISOString().slice(0, 10));
    }
  };

  const diffDays = Math.max(
    1,
    Math.round(
      (new Date(endDate + "T12:00:00").getTime() -
        new Date(startDate + "T12:00:00").getTime()) /
        (1000 * 3600 * 24),
    ) + 1,
  );
  const defaultHours = diffDays * 8;
  const targetHours = customHours !== null ? customHours : defaultHours;

  const activeOverview = showMultiDay && multiOverview ? multiOverview : overview;

  const messages = activeOverview.user_messages + activeOverview.assistant_messages;
  const gitSource = activeOverview.sources.find((s) => s.source === "git");
  const gitEvents = gitSource?.events ?? 0;
  const isGitChecked = settings.analysis_sources.includes("git");

  // 分离 AI Agent 对话来源与 Git 辅助证据来源
  const agentSources = activeOverview.sources.filter((s) => s.source !== "git");
  const detectedAgents = agentSources.filter((s) => s.detected ?? false);
  const undetectedAgents = agentSources.filter((s) => !(s.detected ?? false));
  const activeAgentCount = detectedAgents.filter((s) =>
    settings.analysis_sources.includes(s.source),
  ).length;

  const displayedAgents = sourceTab === "ready" ? detectedAgents : agentSources;

  // 只有 Git 提交时 AI 分析一定是灰的：后端也要求当天存在对话才归纳。
  // 不解释一句，用户会以为按钮坏了或者自己点漏了。
  const onlyGit = messages === 0 && gitEvents > 0;
  const noConversationSource = !settings.analysis_sources.some(
    (source) => source !== "git",
  );

  const dailyCounts = activeOverview.daily_counts || [];
  const emptyDaysCount = dailyCounts.filter(
    (d) => d.user_messages + d.assistant_messages === 0 && d.git_events === 0,
  ).length;

  const handleSelectAll = () => {
    const detectedKeys = detectedAgents.map((s) => s.source);
    const gitPart = isGitChecked ? ["git"] : [];
    onSources([...gitPart, ...detectedKeys]);
  };

  const handleSelectWithData = () => {
    const withData = detectedAgents
      .filter((s) => s.sessions > 0)
      .map((s) => s.source);
    const gitPart = isGitChecked && gitEvents > 0 ? ["git"] : [];
    onSources([...gitPart, ...withData]);
  };

  const handleClearAll = () => {
    const gitPart = isGitChecked ? ["git"] : [];
    onSources(gitPart);
  };

  const handleToggleAgent = (sourceKey: string, checked: boolean) => {
    const detectedKeys = new Set(detectedAgents.map((s) => s.source));
    if (!detectedKeys.has(sourceKey) && checked) {
      return; // 未检测到的 Agent 不可勾选参与分析
    }
    const updated = checked
      ? [...settings.analysis_sources, sourceKey]
      : settings.analysis_sources.filter((x) => x !== sourceKey);
    onSources(updated.filter((x) => x === "git" || detectedKeys.has(x)));
  };

  return (
    <section className="conversations-panel">
      <div className="section-head">
        <div className="section-head-title-wrap">
          <h2>对话与来源</h2>
          <span className="source-summary-pill">
            {detectedAgents.length} 已连接 · {activeAgentCount} 参与分析
          </span>
        </div>
        <div className="conversation-actions">
          {!settings.auto_import_analysis && (
            <button
              className="outline"
              disabled={busy}
              onClick={onViewPending}
              title="查看本次 AI 分析生成、尚未导入今日清单的候选工作项"
            >
              分析结果{pendingCount ? `（${pendingCount}）` : ""}
            </button>
          )}
          <button
            type="button"
            className={`outline multiday-toggle-btn ${showMultiDay ? "active" : ""}`}
            disabled={busy || overview.running || analyzing}
            onClick={() => setShowMultiDay(!showMultiDay)}
            title="自选统计跨度，支持跨天一键采集并将多日工作合并统计到今天"
          >
            <CalendarRange size={15} />
            {showMultiDay ? `跨天合并 (${diffDays}天)` : "多天合并"}
          </button>
          {!showMultiDay && (
            <button
              className="primary"
              disabled={
                busy ||
                overview.running ||
                !settings.llm_enabled ||
                overview.user_messages + overview.assistant_messages === 0
              }
              onClick={() => onAnalyze()}
              title={
                messages === 0
                  ? "AI 归纳需要当天的对话记录；只有 Git 提交时请手工添加事项"
                  : undefined
              }
            >
              {analyzing || overview.running ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Sparkles size={16} />
              )}
              {analyzing || overview.running
                ? "AI 正在分析…"
                : "AI 分析全天对话"}
            </button>
          )}
        </div>
      </div>
      {showMultiDay && (
        <div className="multi-day-workbench">
          <div className="multi-day-controls-row">
            <div className="multi-day-date-group">
              <span className="multi-day-inline-label">统计区间</span>
              <input
                aria-label="起始日期"
                className="multi-day-date-input"
                type="date"
                value={startDate}
                max={endDate}
                disabled={busy || analyzing}
                onChange={(e) => {
                  if (e.target.value) {
                    setStartDate(e.target.value);
                    setCustomHours(null);
                  }
                }}
              />
              <span className="multi-day-date-sep">至</span>
              <input
                aria-label="结束日期"
                className="multi-day-date-input"
                type="date"
                value={endDate}
                min={startDate}
                disabled={busy || analyzing}
                onChange={(e) => {
                  if (e.target.value) {
                    setEndDate(e.target.value);
                    setCustomHours(null);
                  }
                }}
              />
              <div className="multi-day-presets" role="group" aria-label="快捷日期范围">
                <button
                  type="button"
                  className={`preset-pill ${diffDays === 2 && endDate === effectiveDay ? "active" : ""}`}
                  disabled={busy || analyzing}
                  onClick={() => applyPreset("2d")}
                >
                  近 2 天
                </button>
                <button
                  type="button"
                  className={`preset-pill ${diffDays === 3 && endDate === effectiveDay ? "active" : ""}`}
                  disabled={busy || analyzing}
                  onClick={() => applyPreset("3d")}
                >
                  近 3 天
                </button>
                <button
                  type="button"
                  className={`preset-pill ${diffDays === 5 && endDate === effectiveDay ? "active" : ""}`}
                  disabled={busy || analyzing}
                  onClick={() => applyPreset("5d")}
                >
                  近 5 天
                </button>
                <button
                  type="button"
                  className="preset-pill"
                  disabled={busy || analyzing}
                  onClick={() => applyPreset("week")}
                >
                  本周
                </button>
              </div>
            </div>

            <div className="multi-day-hours-group">
              <span className="multi-day-inline-label">目标工时</span>
              <div className="multi-day-hours-box">
                <input
                  aria-label="合并目标工时"
                  type="number"
                  min={1}
                  max={300}
                  value={targetHours}
                  disabled={busy || analyzing}
                  onChange={(e) => setCustomHours(Number(e.target.value))}
                  className="multi-day-hours-input"
                />
                <span className="multi-day-hours-unit">h</span>
              </div>
              {customHours !== null && customHours !== defaultHours && (
                <button
                  type="button"
                  className="multi-day-reset-hours"
                  title={`恢复默认建议工时 (${defaultHours}h)`}
                  onClick={() => setCustomHours(null)}
                >
                  <RotateCcw size={12} />
                  {defaultHours}h
                </button>
              )}
              <span className="multi-day-target-badge">
                归入 {effectiveDay.slice(5)} 日报
              </span>
            </div>
          </div>

          <div className="multi-day-bottom-row">
            <div className="multi-day-day-pills">
              {loadingRange && <LoaderCircle className="spin" size={13} />}
              {dailyCounts.map((item) => {
                const msgCount = item.user_messages + item.assistant_messages;
                const hasChat = msgCount > 0;
                const hasGit = item.git_events > 0;
                const statusClass = hasChat
                  ? "ready"
                  : hasGit
                    ? "git-only"
                    : "empty";
                return (
                  <div
                    key={item.date}
                    className={`day-status-pill ${statusClass}`}
                    title={
                      hasChat || hasGit
                        ? `${item.date}：${item.sessions} 个会话（${msgCount} 条对话），${item.git_events} 条 Git 提交`
                        : `${item.date}：尚未采集到对话或 Git 提交`
                    }
                  >
                    <span className="day-pill-dot" />
                    <strong className="day-pill-date">{item.date.slice(5)}</strong>
                    <span className="day-pill-info">
                      {hasChat
                        ? `${item.sessions}会话${hasGit ? ` · ${item.git_events}提交` : ""}`
                        : hasGit
                          ? `${item.git_events}提交`
                          : "待采集"}
                    </span>
                  </div>
                );
              })}
              {emptyDaysCount > 0 && (
                <span className="multi-day-empty-hint">
                  {emptyDaysCount} 天无记录
                </span>
              )}
            </div>

            <div className="multi-day-actions-bar">
              <label
                className="multi-day-autocollect-toggle"
                title="开启后，点击「AI 合并分析」时会先自动采集所选跨天区间数据"
              >
                <input
                  type="checkbox"
                  checked={autoCollectBeforeAnalyze}
                  disabled={busy || analyzing}
                  onChange={(e) => setAutoCollectBeforeAnalyze(e.target.checked)}
                />
                <span>分析前自动补采</span>
              </label>
              {onOpenCollectDetails && (
                <button
                  type="button"
                  className="outline"
                  disabled={busy || collecting || analyzing}
                  onClick={() => onOpenCollectDetails({ startDate, endDate })}
                  title="查看所选跨天区间内已采集的对话会话与 Git 提交"
                >
                  <FolderGit2 size={14} />
                  跨天详情
                </button>
              )}
              {onCollect && (
                <button
                  type="button"
                  className="outline multi-day-collect-btn"
                  disabled={busy || collecting || analyzing || overview.running}
                  onClick={() => void onCollect({ startDate, endDate })}
                  title={`扫描本地日志并拉取 ${startDate} 至 ${endDate} 期间仓库的本人 Git 提交`}
                >
                  {collecting ? (
                    <LoaderCircle className="spin" size={14} />
                  ) : (
                    <ScanLine size={14} />
                  )}
                  {collecting ? "采集中…" : "采集区间"}
                </button>
              )}
              <button
                type="button"
                className="primary"
                disabled={
                  busy ||
                  collecting ||
                  overview.running ||
                  analyzing ||
                  !settings.llm_enabled ||
                  targetHours <= 0 ||
                  (!autoCollectBeforeAnalyze && messages === 0)
                }
                onClick={() =>
                  onAnalyze({
                    startDate,
                    endDate,
                    targetMinutes: targetHours * 60,
                    autoCollect: autoCollectBeforeAnalyze,
                  })
                }
                title={
                  !autoCollectBeforeAnalyze && messages === 0
                    ? "跨天区间暂无可分析对话，请先采集或勾选自动补采"
                    : undefined
                }
              >
                {analyzing || overview.running ? (
                  <LoaderCircle className="spin" size={15} />
                ) : (
                  <Sparkles size={15} />
                )}
                {analyzing || overview.running
                  ? "正在合并分析…"
                  : `AI 合并分析 (${targetHours}h)`}
              </button>
            </div>
          </div>
        </div>
      )}
      <AnalysisLog day={overview.date} active={overview.running || analyzing} />
      {noConversationSource && (
        <p className="muted">
          未选择对话来源：采集会跳过这些 Agent，也不会发起 AI 分析。
        </p>
      )}
      {onlyGit && (
        <p className="muted">
          {`仅有 ${gitEvents} 条 Git 提交。AI 归纳还需要对话记录，请在设置中配置日志目录或手工添加事项。`}
        </p>
      )}

      {/* 顶部控制栏：苹果风格分段切换与快捷操作 */}
      <div className="source-segmented-toolbar">
        <div className="apple-segmented-group" role="tablist">
          <button
            type="button"
            className={`apple-segmented-btn ${sourceTab === "ready" ? "active" : ""}`}
            onClick={() => setSourceTab("ready")}
          >
            本机就绪 ({detectedAgents.length})
          </button>
          <button
            type="button"
            className={`apple-segmented-btn ${sourceTab === "all" ? "active" : ""}`}
            onClick={() => setSourceTab("all")}
          >
            全部支持 ({agentSources.length})
          </button>
        </div>

        <div className="source-quick-actions">
          <button
            type="button"
            className="source-quick-btn"
            disabled={busy || detectedAgents.length === 0}
            onClick={handleSelectAll}
          >
            全选
          </button>
          <span className="source-quick-sep">·</span>
          <button
            type="button"
            className="source-quick-btn"
            disabled={busy}
            onClick={handleSelectWithData}
          >
            仅选有数据
          </button>
          <span className="source-quick-sep">·</span>
          <button
            type="button"
            className="source-quick-btn"
            disabled={busy}
            onClick={handleClearAll}
          >
            清空
          </button>
        </div>
      </div>

      {/* AI Agent 网格展示 */}
      <div className="conversation-sources-grid">
        {displayedAgents.map((s) => {
          const isDetected = s.detected ?? false;
          const isChecked = settings.analysis_sources.includes(s.source);
          const count = s.sessions;

          if (!isDetected) {
            return (
              <div
                className="agent-card unconfigured"
                key={s.source}
                onClick={onSettings}
                role="button"
                tabIndex={0}
                title="本机未检测到该 Agent 日志路径，点击前往系统设置配置"
              >
                <div className="apple-checkbox-circle disabled" />
                <span className="agent-icon-tile">
                  <SourceIcon source={s.source} size={18} />
                </span>
                <span className="agent-card-title">{names[s.source] || s.source}</span>
                <span className="unconfigured-tag">
                  未配置 <Settings2 size={11} />
                </span>
              </div>
            );
          }

          return (
            <label
              className={`agent-card ${isChecked ? "active" : ""} ${count > 0 ? "has-data" : ""}`}
              key={s.source}
            >
              <input
                type="checkbox"
                checked={isChecked}
                disabled={busy}
                onChange={(e) => handleToggleAgent(s.source, e.target.checked)}
              />
              <div className="apple-checkbox-circle">
                {isChecked && <Check size={11} strokeWidth={3} />}
              </div>
              <span className="agent-icon-tile">
                <SourceIcon source={s.source} size={18} />
              </span>
              <span className="agent-card-title">{names[s.source] || s.source}</span>
              <span className={`count-tag ${count > 0 ? "has-data" : ""}`}>
                {count > 0 ? (
                  <>
                    <span className="live-dot" />
                    {count} 会话
                  </>
                ) : (
                  "0"
                )}
              </span>
            </label>
          );
        })}

        {sourceTab === "ready" && undetectedAgents.length > 0 && (
          <div
            className="agent-card discover-more"
            onClick={() => setSourceTab("all")}
            role="button"
            tabIndex={0}
            title="查看全部支持的 Agent"
          >
            <div className="discover-icon-wrap">
              <Plus size={14} />
            </div>
            <span className="agent-card-title">更多 Agent</span>
            <span className="discover-tag">{undetectedAgents.length} 个未配置</span>
          </div>
        )}
      </div>

      {/* 辅助证据：Git 提交 */}
      {gitSource && (gitSource.detected ?? true) && (
        <div className="auxiliary-evidence-section">
          <label
            className={`git-evidence-card ${isGitChecked ? "active" : ""} ${gitEvents > 0 ? "has-data" : ""}`}
          >
            <input
              type="checkbox"
              checked={isGitChecked}
              disabled={busy}
              onChange={(e) => {
                const next = e.target.checked
                  ? [...settings.analysis_sources, "git"]
                  : settings.analysis_sources.filter((x) => x !== "git");
                onSources(next);
              }}
            />
            <div className="apple-checkbox-circle">
              {isGitChecked && <Check size={11} strokeWidth={3} />}
            </div>
            <SourceIcon source="git" size={17} />
            <div className="git-evidence-text">
              <strong className="git-title">Git 仓库代码提交</strong>
              <span className="git-sub">代码变更辅助归纳证据，不单独作为工作会话</span>
            </div>
            <span className={`count-tag ${gitEvents > 0 ? "has-data" : ""}`}>
              {gitEvents > 0 ? (
                <>
                  <span className="live-dot" />
                  {gitEvents} 提交
                </>
              ) : showMultiDay ? (
                "区间无提交"
              ) : (
                "今日无提交"
              )}
            </span>
          </label>
        </div>
      )}

      <div className="conversation-total apple-metrics-bar">
        <Activity size={17} />
        <span>
          {showMultiDay
            ? `跨天 ${startDate.slice(5)} ~ ${endDate.slice(5)}：`
            : "本次范围："}
          <strong>{activeOverview.session_count} 个会话</strong> ·
          {activeOverview.user_messages} 问 · {activeOverview.assistant_messages}{" "}
          答
        </span>
        <span className="conversation-timing">
          跨度 {activeOverview.timing.session_span_minutes}m
          {activeOverview.work_timing &&
            ` · 活跃 ${activeOverview.work_timing.observable_active_minutes}m`}
        </span>
        <button onClick={onSettings}>
          <Settings2 size={14} />
          模型设置
        </button>
      </div>
      {legacyCount > 0 && (
        <p className="conversation-hint warning">
          仍有 {legacyCount} 项旧规则草稿；AI 分析后会替换，人工事项与已审核工时保留。
        </p>
      )}
      {overview.analysis && (
        <div className="analysis-summary">
          <span className="tag">
            {overview.stale ? "设置已变化，待重分析" : "全天分析结果"}
          </span>
          <p>{overview.analysis.summary}</p>
          {overview.analysis.scope && (
            <p className="scope-counts">
              工作相关 {overview.analysis.scope.work_user_messages} · 排除{" "}
              {overview.analysis.scope.excluded_user_messages} · 待判断{" "}
              {overview.analysis.scope.uncertain_user_messages}
            </p>
          )}
          <small>
            {overview.analysis.model} ·{" "}
            {new Date(overview.analysis.analyzed_at).toLocaleString("zh-CN", {
              timeZone: settings.timezone,
            })}{" "}
            · {overview.analysis.model_requests} 次模型请求
            {overview.analysis.usage && (
              <span
                title={overview.analysis.usage.stages
                  .map(
                    (stage) =>
                      `${stage.label}：输入 ${formatTokens(stage.prompt_tokens)} / 输出 ${formatTokens(stage.completion_tokens)}`,
                  )
                  .join("\n")}
              >
                {" "}
                · 本次用量{overview.analysis.usage.estimated ? "约 " : " "}
                {formatTokens(overview.analysis.usage.total_tokens)} tokens（输入{" "}
                {formatTokens(overview.analysis.usage.prompt_tokens)} / 输出{" "}
                {formatTokens(overview.analysis.usage.completion_tokens)}）
              </span>
            )}
          </small>
        </div>
      )}
      <details className="session-list">
        <summary>查看本次 {activeOverview.session_count} 个会话</summary>
        {activeOverview.sessions
          .filter((s) => settings.analysis_sources.includes(s.source))
          .map((s) => (
            <article key={s.source + s.id}>
              <div>
                <strong className="source-check-name">
                  <SourceIcon source={s.source} size={15} />
                  {names[s.source]}
                </strong>
                <span>
                  {showMultiDay
                    ? new Date(s.first_at).toLocaleString("zh-CN", {
                        timeZone: settings.timezone,
                        month: "2-digit",
                        day: "2-digit",
                        hour: "2-digit",
                        minute: "2-digit",
                      })
                    : new Date(s.first_at).toLocaleTimeString("zh-CN", {
                        timeZone: settings.timezone,
                        hour: "2-digit",
                        minute: "2-digit",
                      })}{" "}
                  –{" "}
                  {new Date(s.last_at).toLocaleTimeString("zh-CN", {
                    timeZone: settings.timezone,
                    hour: "2-digit",
                    minute: "2-digit",
                  })}
                </span>
                <span>
                  {s.user_messages} 问 / {s.assistant_messages} 答
                </span>
                <button onClick={() => onView(s.source, s.id)}>
                  查看对话
                  <ArrowUpRight size={13} />
                </button>
              </div>
              <p>{s.preview || "该会话当天没有用户请求消息"}</p>
              <p className="session-timing">
                跨度 {s.timing.session_span_minutes} 分钟 · 可观察活跃片段{" "}
                {s.timing.observable_active_minutes}{" "}
                分钟（此处未筛选私人话题，不直接计入工时）
              </p>
              <small>
                {s.projects.join(" / ")} · {s.id}
              </small>
            </article>
          ))}
      </details>
    </section>
  );
}
