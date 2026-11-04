import { useState } from "react";
import { Check, Sparkles, X } from "lucide-react";
import type { Report } from "./types";
import "./workflows.css";

const h = (minutes: number) => Number((minutes / 60).toFixed(2));

export function AllocationPreviewModal({
  report,
  onClose,
  onApplied,
}: {
  report: Report;
  onClose: () => void;
  onApplied: (message: string) => Promise<void>;
}) {
  const snapshot = report.time_allocation;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  if (!snapshot) return null;
  const titles = new Map(report.tasks.map((task) => [task.id, task.title]));
  const items = snapshot.items || [];
  const manual = snapshot.manual_tasks || [];
  const pending = Boolean(snapshot.pending);
  const apply = async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(
        `/api/reports/${report.date}/apply-allocation`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        },
      );
      const result = await response.json();
      if (!response.ok)
        throw Error(
          typeof result.detail === "string"
            ? result.detail
            : "应用工时失败，请重试",
        );
      await onApplied(
        `已应用 ${result.allocated} 个工作项的 AI 工时，保留 ${h(
          result.fixed_minutes,
        )}h 固定工时。${(result.warnings || []).join("；")}`,
      );
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="modal-overlay">
      <section
        className="modal allocation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="allocation-preview-title"
      >
        <div className="modal-head">
          <div>
            <h2 id="allocation-preview-title">
              <Sparkles size={20} /> {pending ? "预览并应用 AI 工时" : "AI 工时预览"}
            </h2>
            <p>
              {report.date} · 每日目标 {h(report.target_minutes)}h
            </p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭工时预览"
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {snapshot.stale && (
          <p role="alert" className="allocation-error">
            工作项或分析设置已经变化，当前建议已过期；请重新分配后再应用。
          </p>
        )}
        {error && (
          <p role="alert" className="allocation-error">
            {error}
          </p>
        )}
        <div className="allocation-summary">
          <span>
            固定 <strong>{h(snapshot.fixed_minutes)}h</strong>
          </span>
          <span>
            保留 <strong>{h(snapshot.preserved_minutes || 0)}h</strong>
          </span>
          <span>
            AI 分配 <strong>{h(snapshot.distributed_minutes)}h</strong>
          </span>
        </div>
        {items.length === 0 && manual.length === 0 ? (
          <p className="muted">本次没有需要写入的工作项。</p>
        ) : (
          <div className="allocation-results">
            {items.map((item, index) => (
              <div key={item.id || index}>
                <span>
                  {titles.get(item.id) || item.id}
                  <small>{item.reason || (item.eligible ? "AI建议" : "不参与分配")}</small>
                </span>
                <strong>{h(item.allocated_minutes || 0)}h</strong>
              </div>
            ))}
            {manual.map((task, index) => (
              <div key={`manual-${index}`}>
                <span>
                  {task.title}
                  <small>人工事项 · {task.category || "其他"}</small>
                </span>
                <strong>{h(task.confirmed_minutes || 0)}h</strong>
              </div>
            ))}
          </div>
        )}
        <p style={{ margin: "8px 0 0" }}>
          {pending
            ? "这些工时尚未写入工作项，确认后才会应用。"
            : `已应用${snapshot.applied_at ? " · " + new Date(snapshot.applied_at).toLocaleString("zh-CN") : ""}`}
        </p>
        {snapshot.warnings.length > 0 && (
          <div className="allocation-note" style={{ marginTop: 10 }}>
            {snapshot.warnings.map((warning, index) => (
              <p key={index}>{warning}</p>
            ))}
          </div>
        )}
        <div className="modal-footer">
          <button type="button" className="outline" onClick={onClose}>
            {pending ? "取消" : "关闭"}
          </button>
          {pending && (
            <button
              type="button"
              disabled={busy || snapshot.stale}
              onClick={apply}
            >
              <Check size={16} />
              {busy ? "正在应用…" : "应用这些工时"}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
