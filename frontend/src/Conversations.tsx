import { Activity, ArrowUpRight, LoaderCircle, Sparkles, Settings2 } from "lucide-react";
import type { ConversationOverview, Settings } from "./types";
import { sourceLabels } from "./SourcePicker";
import { SourceIcon } from "./SourceIcon";
const formatTokens = (value: number) => value.toLocaleString("zh-CN");
const names: Record<string, string> = {
  ...sourceLabels,
  workbuddy: "WorkBuddy（旧版合并记录）",
  qoder: "Qoder（旧版合并记录）",
  trae: "Trae（旧版合并记录）",
  git: "Git 辅助证据",
};

export function Conversations({
  overview,
  settings,
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
  busy: boolean;
  analyzing: boolean;
  pendingCount: number;
  legacyCount: number;
  onAnalyze: () => void;
  onSettings: () => void;
  onSources: (sources: string[]) => void;
  onView: (source: string, session: string) => void;
  onViewPending: () => void;
}) {
  const messages = overview.user_messages + overview.assistant_messages;
  const gitEvents = overview.sources.find((s) => s.source === "git")?.events ?? 0;
  // 只有 Git 提交时 AI 分析一定是灰的：后端也要求当天存在对话才归纳。
  // 不解释一句，用户会以为按钮坏了或者自己点漏了。
  const onlyGit = messages === 0 && gitEvents > 0;
  const noConversationSource = !settings.analysis_sources.some(
    (source) => source !== "git",
  );
  return (
    <section className="conversations-panel">
      <div className="section-head">
        <div>
          <div className="eyebrow">CONVERSATIONS FIRST</div>
          <h2>先看全天对话，再归纳工作任务</h2>
          <p>会话数、消息数是采集统计，不等于实际完成的任务数。</p>
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
            className="primary"
            disabled={
              busy ||
              overview.running ||
              !settings.llm_enabled ||
              overview.user_messages + overview.assistant_messages === 0
            }
            onClick={onAnalyze}
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
        </div>
      </div>
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
      <div className="conversation-sources">
        {overview.sources.map((s) => (
          <label className="check-label" key={s.source}>
            <input
              type="checkbox"
              checked={settings.analysis_sources.includes(s.source)}
              disabled={busy}
              onChange={(e) =>
                onSources(
                  e.target.checked
                    ? [...settings.analysis_sources, s.source]
                    : settings.analysis_sources.filter((x) => x !== s.source),
                )
              }
            />
            <span>
              <span className="source-check-name">
                <SourceIcon source={s.source} size={16} />
                {names[s.source]}
              </span>
              <small>
                {s.source === "git"
                  ? `${s.events} 条提交`
                  : `${s.sessions} 个会话 · ${s.user_messages + s.assistant_messages} 条对话消息`}
              </small>
            </span>
          </label>
        ))}
      </div>
      <div className="conversation-total">
        <Activity size={17} />
        <span>
          本次范围：<strong>{overview.session_count} 个会话</strong>，
          {overview.user_messages} 条用户消息 + {overview.assistant_messages} 条
          AI 回复
        </span>
        <button onClick={onSettings}>
          <Settings2 size={14} />
          配置分析模型
        </button>
      </div>
      {!settings.llm_enabled && (
        <p className="conversation-hint">
          AI 尚未开启。采集只统计对话，不会按每次提问或 Git
          提交生成任务。接通模型后，统一归纳全天实际工作目标。
        </p>
      )}
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
      <div className="timing-reference">
        <strong>对话时间参考（非人工实测）</strong>
        <span>会话跨度 {overview.timing.session_span_minutes} 分钟</span>
        <span>
          {overview.work_timing
            ? `工作相关活跃片段 ${overview.work_timing.observable_active_minutes} 分钟`
            : "工作相关时长待AI筛选"}
        </span>
        <small>
          相邻活动超过 {settings.activity_gap_minutes}{" "}
          分钟的空闲不累计，并行片段取并集；私人/无关对话不作为工作时长依据。
        </small>
      </div>
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
