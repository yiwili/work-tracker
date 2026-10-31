import { useEffect, useState } from "react";
import {
  Check,
  FolderGit2,
  GitMerge,
  LoaderCircle,
  MessageSquare,
  X,
} from "lucide-react";
import type { ConversationOverview, Task, WorkEvent } from "./types";
import { api } from "./api";
import { SourceIcon } from "./SourceIcon";
import { sourceLabels } from "./SourcePicker";

const sourceNames: Record<string, string> = {
  ...sourceLabels,
  workbuddy: "WorkBuddy（旧版合并记录）",
  qoder: "Qoder（旧版合并记录）",
  trae: "Trae（旧版合并记录）",
  git: "Git 本地提交",
};

export function CollectDetailsModal({
  day,
  timezone,
  onClose,
  onAdded,
}: {
  day: string;
  timezone: string;
  onClose: () => void;
  onAdded: (created: number) => Promise<void> | void;
}) {
  const [events, setEvents] = useState<WorkEvent[]>([]);
  const [overview, setOverview] = useState<ConversationOverview | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");

  const load = async () => {
    const [e, o, t] = await Promise.all([
      api<WorkEvent[]>("/events?date=" + day),
      api<ConversationOverview>("/conversations?date=" + day),
      api<Task[]>("/tasks?date=" + day),
    ]);
    setEvents(e);
    setOverview(o);
    setTasks(t);
    setSelected([]);
  };

  useEffect(() => {
    setLoading(true);
    setError("");
    load()
      .catch((e) => setError((e as Error).message))
      .finally(() => setLoading(false));
    // The modal is tied to one date for its lifetime.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [day]);

  const gitEvents = events.filter((e) => e.source === "git").reverse();
  const gitAdded = new Set(
    tasks.flatMap((t) => t.evidence.map((e) => e.id)),
  );
  const selectable = gitEvents.filter((e) => !gitAdded.has(e.id));
  const addSelected = async () => {
    if (!selected.length) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{
        created: number;
        skipped: number;
        task_ids: string[];
      }>("/tasks/from-events", "POST", { date: day, event_ids: selected });
      await load();
      await onAdded(result.created);
      const pieces = [];
      if (result.created) pieces.push(`已添加 ${result.created} 个工作项`);
      if (result.skipped) pieces.push(`跳过 ${result.skipped} 条已添加提交`);
      setMessage(pieces.join("，") + "。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  const conversationSessions = overview?.sessions || [];
  const gitCount = overview?.sources.find((s) => s.source === "git")?.events ?? 0;
  return (
    <div className="modal-overlay">
      <section
        className="modal wide collect-modal"
        role="dialog"
        aria-modal="true"
        aria-label="采集详情"
      >
        <div className="modal-head">
          <div>
            <div className="eyebrow">LOCAL COLLECTION</div>
            <h2>{day} 采集详情</h2>
          </div>
          <button aria-label="关闭采集详情" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <p className="muted">
          这里是本机已采集的原始记录。Git 提交需要你主动勾选后才会变成工作项；对话记录只用于 AI 分析。
        </p>
        {loading ? (
          <div className="collect-loading">
            <LoaderCircle className="spin" size={20} />
            正在读取采集记录…
          </div>
        ) : (
          <>
            <div className="collect-summary">
              <div>
                <strong>{overview?.session_count ?? 0}</strong>
                <span>对话会话</span>
              </div>
              <div>
                <strong>
                  {(overview?.user_messages ?? 0) +
                    (overview?.assistant_messages ?? 0)}
                </strong>
                <span>对话消息</span>
              </div>
              <div>
                <strong>{gitCount}</strong>
                <span>Git 提交</span>
              </div>
              <div>
                <strong>{events.length}</strong>
                <span>活动记录</span>
              </div>
            </div>
            <section className="collect-block">
              <div className="collect-block-head">
                <div>
                  <h3>
                    <GitMerge size={17} /> Git 本地提交
                  </h3>
                  <p>勾选后按提交逐条添加为今日工作项，原始提交仍保留为证据。</p>
                </div>
                {selectable.length > 0 && (
                  <div className="collect-block-actions">
                    <button
                      className="link-button"
                      type="button"
                      onClick={() =>
                        setSelected(
                          selected.length === selectable.length
                            ? []
                            : selectable.map((e) => e.id),
                        )
                      }
                    >
                      {selected.length === selectable.length ? "取消全选" : "全选未添加"}
                    </button>
                    <button
                      className="primary"
                      type="button"
                      disabled={saving || !selected.length}
                      onClick={addSelected}
                    >
                      {saving ? (
                        <LoaderCircle className="spin" size={15} />
                      ) : (
                        <Check size={15} />
                      )}
                      添加选中 {selected.length || ""} 条
                    </button>
                  </div>
                )}
              </div>
              {gitEvents.length === 0 ? (
                <p className="muted">
                  当天没有采集到 Git 提交。可在系统设置中配置 Git 仓库后重新采集。
                </p>
              ) : (
                <div className="collect-git-list">
                  {gitEvents.map((event) => {
                    const added = gitAdded.has(event.id);
                    const changes = String(event.metadata.changes || "");
                    const sha = String(event.metadata.sha || "");
                    return (
                      <label
                        className={"collect-git-item" + (added ? " added" : "")}
                        key={event.id}
                      >
                        <input
                          type="checkbox"
                          aria-label={"选择提交 " + event.summary}
                          disabled={added || saving}
                          checked={selected.includes(event.id)}
                          onChange={(e) =>
                            setSelected(
                              e.target.checked
                                ? [...selected, event.id]
                                : selected.filter((id) => id !== event.id),
                            )
                          }
                        />
                        <div>
                          <strong>{event.summary}</strong>
                          {changes && <p>{changes}</p>}
                          <small>
                            {new Date(event.occurred_at).toLocaleTimeString(
                              "zh-CN",
                              {
                                timeZone: timezone,
                                hour: "2-digit",
                                minute: "2-digit",
                              },
                            )}{" "}
                            · {event.project}
                            {sha && ` · ${sha.slice(0, 8)}`}
                          </small>
                        </div>
                        {added ? (
                          <span className="tag">已加入工作项</span>
                        ) : (
                          <span className="tag git-tag">
                            <FolderGit2 size={12} /> 未添加
                          </span>
                        )}
                      </label>
                    );
                  })}
                </div>
              )}
              {message && (
                <p className="collect-result" role="status">
                  {message}
                </p>
              )}
              {error && (
                <p className="collect-error" role="alert">
                  {error}
                </p>
              )}
            </section>
            <details className="collect-conversations">
              <summary>
                <MessageSquare size={15} /> 对话采集明细（
                {conversationSessions.length} 个会话）
              </summary>
              {conversationSessions.length === 0 ? (
                <p className="muted">当天没有采集到对话记录。</p>
              ) : (
                conversationSessions.map((session) => (
                  <article key={session.source + session.id}>
                    <div>
                      <strong className="source-check-name">
                        <SourceIcon source={session.source} size={15} />
                        {sourceNames[session.source] || session.source}
                      </strong>
                      <span>
                        {session.user_messages} 问 / {session.assistant_messages}{" "}
                        答
                      </span>
                      <span>{session.events} 条活动</span>
                    </div>
                    <p>{session.preview || "该会话当天没有用户请求消息"}</p>
                    <small>
                      {session.projects.join(" / ")} · {session.id}
                    </small>
                  </article>
                ))
              )}
            </details>
          </>
        )}
      </section>
    </div>
  );
}
