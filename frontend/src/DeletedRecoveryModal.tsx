import React, { useState } from "react";
import { AlertCircle, Check, History, LoaderCircle, X } from "lucide-react";
import type { PendingTask } from "./types";
import { api } from "./api";

export function DeletedRecoveryModal({
  day,
  tasks,
  warnings = [],
  onClose,
  onImported,
}: {
  day: string;
  tasks: PendingTask[];
  warnings?: string[];
  onClose: () => void;
  onImported: () => Promise<void> | void;
}) {
  // decisions[fingerprint]: "restore" (打勾) | "discard" (打叉) | undefined (未决定)
  const [decisions, setDecisions] = useState<Record<string, "restore" | "discard">>(() => ({}));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const restoreCount = tasks.filter((t) => decisions[t.fingerprint] === "restore").length;
  const discardCount = tasks.filter((t) => decisions[t.fingerprint] === "discard").length;

  const toggle = (fingerprint: string, choice: "restore" | "discard") => {
    setDecisions((prev) => {
      const current = prev[fingerprint];
      if (current === choice) {
        const next = { ...prev };
        delete next[fingerprint];
        return next;
      }
      return { ...prev, [fingerprint]: choice };
    });
  };

  const handleConfirm = async () => {
    const toRestore = tasks.filter((t) => decisions[t.fingerprint] === "restore");
    if (!toRestore.length) {
      onClose();
      return;
    }
    setSaving(true);
    setError("");
    try {
      await api("/analysis/import", "POST", {
        date: day,
        items: toRestore.map((t) => ({
          fingerprint: t.fingerprint,
          title: t.title,
          details: t.details,
          project: t.project,
          category: t.category,
        })),
      });
      await onImported();
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" role="dialog" aria-modal="true" aria-label="历史删除工作项确认" onClick={onClose}>
      <div className="modal deleted-recovery-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div className="recovery-head-title">
            <span className="recovery-head-icon">
              <History size={18} />
            </span>
            <div>
              <h2>历史删除工作项确认</h2>
              <p className="recovery-head-desc">
                识别到以下工作项在过去曾被删除，但当天存在确凿实施行为。只能查看信息，请在右侧便捷打勾或打叉：
              </p>
            </div>
          </div>
          <button aria-label="关闭" className="close-btn" onClick={onClose} disabled={saving}>
            <X size={18} />
          </button>
        </div>

        {error && (
          <div className="alert error" role="alert">
            {error}
          </div>
        )}

        {warnings.length > 0 && (
          <div className="recovery-warnings-card">
            <div className="recovery-warnings-title">
              <AlertCircle size={15} />
              <span>待审核提示（相似度核对）</span>
            </div>
            <div className="recovery-warnings-content">
              {warnings.map((w, idx) => (
                <p key={idx}>{w}</p>
              ))}
              <p className="recovery-warnings-tip">
                可在清单中勾选重复任务后合并；合并会保留确认工时，请人工核对。
              </p>
            </div>
          </div>
        )}

        <div className="recovery-todo-list">
          {tasks.map((task) => {
            const decision = decisions[task.fingerprint];
            return (
              <div
                key={task.fingerprint}
                className={`recovery-todo-card ${
                  decision === "restore" ? "is-restore" : decision === "discard" ? "is-discard" : ""
                }`}
              >
                <div className="recovery-todo-info">
                  <div className="recovery-todo-badges">
                    <span className="project-name">{task.project}</span>
                    <span className="category">{task.category}</span>
                    <span className="badge-muted">{task.evidence_ids.length} 条实施证据</span>
                  </div>
                  <h4 className="recovery-todo-title">{task.title}</h4>
                  {task.details && <p className="recovery-todo-desc">{task.details}</p>}
                </div>
                <div className="recovery-todo-actions">
                  <button
                    type="button"
                    className={`todo-action-btn check ${decision === "restore" ? "active" : ""}`}
                    title="打勾：恢复并导入今日工作清单"
                    aria-label={`恢复 ${task.title}`}
                    disabled={saving}
                    onClick={() => toggle(task.fingerprint, "restore")}
                  >
                    <Check size={16} strokeWidth={2.6} />
                  </button>
                  <button
                    type="button"
                    className={`todo-action-btn cross ${decision === "discard" ? "active" : ""}`}
                    title="打叉：保持删除，不导入"
                    aria-label={`保持删除 ${task.title}`}
                    disabled={saving}
                    onClick={() => toggle(task.fingerprint, "discard")}
                  >
                    <X size={16} strokeWidth={2.6} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>

        <div className="modal-foot recovery-modal-foot">
          <div className="recovery-stat">
            已勾选恢复 <strong>{restoreCount}</strong> 项 / 保持删除 <strong>{discardCount}</strong> 项
          </div>
          <div className="actions">
            <button type="button" className="outline" onClick={onClose} disabled={saving}>
              取消
            </button>
            <button type="button" className="primary" disabled={saving} onClick={handleConfirm}>
              {saving && <LoaderCircle size={15} className="spin" />}
              {restoreCount > 0 ? `确认导入（${restoreCount}项）` : "确认（不恢复）"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
