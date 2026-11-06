import { useState, useEffect } from "react";
import {
  Activity,
  ArrowUpRight,
  Check,
  LoaderCircle,
  Plus,
  Settings2,
  Sparkles,
  CalendarRange,
} from "lucide-react";
import type { ConversationOverview, Settings } from "./types";
import { sourceLabels } from "./SourcePicker";
import { SourceIcon } from "./SourceIcon";
import { AnalysisLog } from "./AnalysisLog";

export type AnalyzeOptions = {
  startDate?: string;
  endDate?: string;
  targetMinutes?: number;
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
  analyzing,
  pendingCount,
  legacyCount,
  onAnalyze,
  onSettings,
  onSources,
  onView,
  onViewPending,
}: {
  overview: ConversationOverview;
  settings: Settings;
  currentDay?: string;
  busy: boolean;
  analyzing: boolean;
  pendingCount: number;
  legacyCount: number;
  onAnalyze: (options?: AnalyzeOptions) => void;
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

  useEffect(() => {
    setEndDate(effectiveDay);
    const d = new Date(effectiveDay + "T12:00:00");
    d.setDate(d.getDate() - 1);
    setStartDate(d.toISOString().slice(0, 10));
    setCustomHours(null);
  }, [effectiveDay]);

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

  const messages = overview.user_messages + overview.assistant_messages;
  const gitSource = overview.sources.find((s) => s.source === "git");
  const gitEvents = gitSource?.events ?? 0;
  const isGitChecked = settings.analysis_sources.includes("git");

  // 分离 AI Agent 对话来源与 Git 辅助证据来源
  const agentSources = overview.sources.filter((s) => s.source !== "git");
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
            {detectedAgents.length} 个 Agent 已连接 · {activeAgentCount} 个参与分析
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
            className={showMultiDay ? "primary" : "outline"}
            disabled={busy || overview.running || analyzing}
            onClick={() => setShowMultiDay(!showMultiDay)}
            title="自选统计跨度，例如将4号5号两天（共16小时）合并统计到今天"
          >
            <CalendarRange size={15} style={{ marginRight: "4px" }} />
            {showMultiDay ? "收起多天设置" : "多天合并分析"}
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
                ? "AI 正在分析，请稍候"
                : "AI 分析全天对话"}
            </button>
          )}
        </div>
      </div>
      {showMultiDay && (
        <div
          className="multi-day-banner"
          style={{
            margin: "12px 0 16px 0",
            padding: "14px 16px",
            background: "var(--card-bg, rgba(255, 255, 255, 0.05))",
            border: "1px solid var(--accent, #3b82f6)",
            borderRadius: "8px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "10px" }}>
            <div style={{ fontWeight: 600, display: "flex", alignItems: "center", gap: "6px" }}>
              <Sparkles size={16} style={{ color: "var(--accent, #3b82f6)" }} />
              多天合并统计与一次性提交
            </div>
            <span style={{ fontSize: "12px", color: "var(--muted)" }}>
              跨天范围：共 {diffDays} 天 · 建议总工时 {defaultHours} 小时
            </span>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
            <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
              起始日期：
              <input
                type="date"
                value={startDate}
                max={endDate}
                disabled={busy || analyzing}
                onChange={(e) => {
                  setStartDate(e.target.value);
                  setCustomHours(null);
                }}
                style={{ padding: "4px 8px", borderRadius: "4px", border: "1px solid var(--border)" }}
              />
            </label>
            <span style={{ color: "var(--muted)" }}>至</span>
            <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
              结束日期：
              <input
                type="date"
                value={endDate}
                min={startDate}
                disabled={busy || analyzing}
                onChange={(e) => {
                  setEndDate(e.target.value);
                  setCustomHours(null);
                }}
                style={{ padding: "4px 8px", borderRadius: "4px", border: "1px solid var(--border)" }}
              />
            </label>
            <label style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "13px" }}>
              目标工时（小时）：
              <input
                type="number"
                min={1}
                max={300}
                value={targetHours}
                disabled={busy || analyzing}
                onChange={(e) => setCustomHours(Number(e.target.value))}
                style={{ width: "70px", padding: "4px 8px", borderRadius: "4px", border: "1px solid var(--border)" }}
              />
            </label>
          </div>

          <p style={{ margin: 0, fontSize: "13px", color: "var(--muted)", lineHeight: 1.5 }}>
            💡 AI 将统计 <strong>{startDate}</strong> 至 <strong>{endDate}</strong>（共 {diffDays} 天）的所有 AI 对话与 Git 提交，归纳出的工作项将<strong>统一写入 {effectiveDay} 今日清单</strong>，并将本日目标工时设为 <strong>{targetHours} 小时（{targetHours * 60} 分钟）</strong>。
          </p>

          <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
            <button
              type="button"
              className="primary"
              disabled={busy || overview.running || analyzing || !settings.llm_enabled || targetHours <= 0}
              onClick={() =>
                onAnalyze({
                  startDate,
                  endDate,
                  targetMinutes: targetHours * 60,
                })
              }
            >
              {analyzing || overview.running ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Sparkles size={16} />
              )}
              {analyzing || overview.running
                ? "AI 正在分析多天记录…"
                : `开始多天合并分析（${startDate} ~ ${endDate} · ${targetHours}h）`}
            </button>
            <button
              type="button"
              className="outline"
              disabled={busy || analyzing}
              onClick={() => setShowMultiDay(false)}
            >
              取消
            </button>
          </div>
        </div>
      )}
      <AnalysisLog day={overview.date} active={overview.running || analyzing} />
      {!settings.auto_import_analysis && pendingCount > 0 && (
        <p className="muted">
          本次分析生成了 {pendingCount} 条候选工作项，尚未写入今日清单；点击「分析结果」可查看、修改后导入。
        </p>
      )}
      {noConversationSource && (
        <p className="muted">
          未勾选任何对话来源：采集会跳过这些 Agent，也不会发起 AI 分析。
        </p>
      )}
      {onlyGit && (
        <p className="muted">
          {`已采集到 ${gitEvents} 条 Git 提交，但 AI 归纳需要当天的对话记录。请到「系统设置」配置可读的对话日志目录，或在下方用「添加人工事项」手工记录今天的工作。`}
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
          本次范围：<strong>{overview.session_count} 个会话</strong>，
          {overview.user_messages} 条用户消息 + {overview.assistant_messages} 条
          AI 回复
        </span>
        <span className="conversation-timing">
          跨度 {overview.timing.session_span_minutes}m
          {overview.work_timing &&
            ` · 活跃 ${overview.work_timing.observable_active_minutes}m`}
        </span>
        <button onClick={onSettings}>
          <Settings2 size={14} />
          配置分析模型
        </button>
      </div>
      {legacyCount > 0 && (
        <p className="conversation-hint warning">
          这里仍有 {legacyCount} 项旧规则草稿，不代表实际任务。AI
          分析成功后会替换未审核草稿，保留人工事项与已审核工时。
        </p>
      )}
      {overview.analysis && (
        <div className="analysis-summary">
          <span className="tag">
            {overview.stale ? "对话或分析设置已变化，待重新分析" : "全天 AI 分析结果"}
          </span>
          <p>{overview.analysis.summary}</p>
          {overview.analysis.scope && (
            <p className="scope-counts">
              工作相关提问 {overview.analysis.scope.work_user_messages} 条 ·
              排除私人/无关 {overview.analysis.scope.excluded_user_messages} 条
              · 待判断 {overview.analysis.scope.uncertain_user_messages} 条
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
        <summary>查看本次 {overview.session_count} 个会话</summary>
        {overview.sessions
          .filter((s) => settings.analysis_sources.includes(s.source))
          .map((s) => (
            <article key={s.source + s.id}>
              <div>
                <strong className="source-check-name">
                  <SourceIcon source={s.source} size={15} />
                  {names[s.source]}
                </strong>
                <span>
                  {new Date(s.first_at).toLocaleTimeString("zh-CN", {
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
