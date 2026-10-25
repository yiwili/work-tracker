import { Activity, ArrowUpRight, Sparkles, Settings2 } from "lucide-react";
import type { ConversationOverview, Settings } from "./types";
const names: Record<string, string> = {
  codex: "Codex",
  claude_code: "Claude Code",
  git: "Git 辅助证据",
};

export function Conversations({
  overview,
  settings,
  busy,
  legacyCount,
  onAnalyze,
  onSettings,
  onSources,
  onView,
}: {
  overview: ConversationOverview;
  settings: Settings;
  busy: boolean;
  legacyCount: number;
  onAnalyze: () => void;
  onSettings: () => void;
  onSources: (sources: string[]) => void;
  onView: (source: string, session: string) => void;
}) {
  return (
    <section className="conversations-panel">
      <div className="section-head">
        <div>
          <div className="eyebrow">CONVERSATIONS FIRST</div>
          <h2>先看全天对话，再归纳工作任务</h2>
          <p>会话数、消息数是采集统计，不等于实际完成的任务数。</p>
        </div>
        <button
          className="primary"
          disabled={
            busy ||
            overview.running ||
            !settings.llm_enabled ||
            overview.user_messages + overview.assistant_messages === 0
          }
          onClick={onAnalyze}
        >
          <Sparkles size={16} />
          {overview.running ? "AI 正在分析，请稍候" : "AI 分析全天对话"}
        </button>
      </div>
      <div className="conversation-sources">
        {overview.sources.map((s) => (
          <label className="check-label" key={s.source}>
            <input
              type="checkbox"
              checked={settings.analysis_sources.includes(s.source)}
              disabled={
                busy ||
                (s.source !== "git" &&
                  settings.analysis_sources.filter((x) => x !== "git")
                    .length === 1 &&
                  settings.analysis_sources.includes(s.source))
              }
              onChange={(e) =>
                onSources(
                  e.target.checked
                    ? [...settings.analysis_sources, s.source]
                    : settings.analysis_sources.filter((x) => x !== s.source),
                )
              }
            />
            <span>
              {names[s.source]}
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
                <strong>{names[s.source]}</strong>
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
