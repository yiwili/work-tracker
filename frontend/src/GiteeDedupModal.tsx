import { useState } from "react";
import { X, CheckCircle, HelpCircle, AlertCircle, ExternalLink, Link2, PlusCircle, Ban } from "lucide-react";
import type { GiteeSyncResult, GiteeMatchDecision } from "./types";
import { api } from "./api";

export function GiteeDedupModal({
  day,
  syncResult,
  onClose,
  onApplied,
}: {
  day: string;
  syncResult: GiteeSyncResult;
  onClose: () => void;
  onApplied: () => Promise<void>;
}) {
  // Map remote_issue_id to user decision
  const [decisions, setDecisions] = useState<Record<string, { action: "match" | "create" | "ignore"; targetTaskId?: string }>>(() => {
    const init: Record<string, { action: "match" | "create" | "ignore"; targetTaskId?: string }> = {};
    for (const item of syncResult.ambiguous) {
      const topCand = item.candidates[0];
      init[item.id] = {
        action: topCand ? "match" : "create",
        targetTaskId: topCand?.local_task_id || topCand?.local_task?.id,
      };
    }
    return init;
  });

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const handleActionChange = (remoteId: string, action: "match" | "create" | "ignore", targetTaskId?: string) => {
    setDecisions((prev) => ({
      ...prev,
      [remoteId]: { action, targetTaskId: targetTaskId ?? prev[remoteId]?.targetTaskId },
    }));
  };

  const handleTargetChange = (remoteId: string, targetTaskId: string) => {
    setDecisions((prev) => ({
      ...prev,
      [remoteId]: { action: "match", targetTaskId },
    }));
  };

  const handleSubmit = async () => {
    setBusy(true);
    setError("");
    try {
      const payloadDecisions: GiteeMatchDecision[] = Object.entries(decisions).map(([remoteId, dec]) => ({
        remote_issue_id: remoteId,
        action: dec.action,
        target_task_id: dec.action === "match" ? dec.targetTaskId : undefined,
      }));

      await api(`/reports/${day}/confirm-gitee-match`, "POST", {
        date: day,
        decisions: payloadDecisions,
      });

      await onApplied();
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
        className="modal gitee-dedup-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gitee-dedup-title"
        style={{ maxWidth: "760px", maxHeight: "88vh", display: "flex", flexDirection: "column" }}
      >
        <div className="modal-head">
          <div>
            <h2 id="gitee-dedup-title" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <Link2 size={20} />
              Gitee 云端未完成工作项匹配与去重
            </h2>
            <p>{day} · 以云端工作项为主，继承本地工时与详情</p>
          </div>
          <button
            type="button"
            className="icon-button"
            aria-label="关闭"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>

        {error && (
          <div className="allocation-error" role="alert" style={{ margin: "12px 0" }}>
            {error}
          </div>
        )}

        <div style={{ padding: "0 4px", fontSize: "13px", color: "var(--muted)", marginBottom: "12px" }}>
          <span>
            共检测到 <strong>{syncResult.total_remote}</strong> 项云端未完成项：
            已自动通过 <strong style={{ color: "var(--success)" }}>{syncResult.auto_matched.length}</strong> 项，
            模糊待确认 <strong style={{ color: "var(--primary)" }}>{syncResult.ambiguous.length}</strong> 项，
            低置信度丢弃 <strong>{syncResult.discarded_count}</strong> 项。
          </span>
        </div>

        {syncResult.auto_matched.length > 0 && (
          <div style={{ marginBottom: "16px", padding: "12px", background: "var(--surface-elevated, #f8f9fa)", borderRadius: "8px", border: "1px solid var(--border)" }}>
            <h4 style={{ margin: "0 0 8px 0", fontSize: "14px", display: "flex", alignItems: "center", gap: "6px", color: "var(--success)" }}>
              <CheckCircle size={16} /> 已高置信度自动通过并合并工时 ({syncResult.auto_matched.length}项)
            </h4>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", maxHeight: "120px", overflowY: "auto", fontSize: "12px" }}>
              {syncResult.auto_matched.map((m) => (
                <div key={m.task_id} style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                  <span>{m.title}</span>
                  <span style={{ color: "var(--muted)" }}>
                    {m.confirmed_minutes} 分钟 · 置信度 {(m.confidence * 100).toFixed(0)}%
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        <div style={{ flex: 1, overflowY: "auto", paddingRight: "4px" }}>
          {syncResult.ambiguous.length === 0 ? (
            <p style={{ textAlign: "center", color: "var(--muted)", padding: "24px 0" }}>
              没有需要手动确认的模糊匹配项。
            </p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "14px" }}>
              {syncResult.ambiguous.map((item) => {
                const currentDec = decisions[item.id] || { action: "match" };
                const confPercent = Math.round(item.confidence * 100);

                return (
                  <div
                    key={item.id}
                    style={{
                      border: "1px solid var(--border)",
                      borderRadius: "8px",
                      padding: "14px",
                      background: "var(--surface)",
                      display: "flex",
                      flexDirection: "column",
                      gap: "10px",
                    }}
                  >
                    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: "12px" }}>
                      <div style={{ flex: 1 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                          <span style={{ fontWeight: 600, fontSize: "14px" }}>{item.title}</span>
                          {item.url && (
                            <a href={item.url} target="_blank" rel="noreferrer" style={{ display: "inline-flex", alignItems: "center", color: "var(--primary)" }}>
                              <ExternalLink size={14} />
                            </a>
                          )}
                          <span
                            style={{
                              fontSize: "11px",
                              padding: "2px 8px",
                              borderRadius: "12px",
                              background: confPercent >= 70 ? "#e6f4ea" : "#fef7e0",
                              color: confPercent >= 70 ? "#137333" : "#b06000",
                              fontWeight: 600,
                            }}
                          >
                            置信度 {confPercent}%
                          </span>
                        </div>
                        <div style={{ fontSize: "12px", color: "var(--muted)", marginTop: "4px" }}>
                          {item.program || item.project || "未归属项目"} · {item.date || "无日期"}
                        </div>
                      </div>
                    </div>

                    {/* Candidate local tasks */}
                    {item.candidates.length > 0 && (
                      <div style={{ background: "var(--surface-hover, #f1f3f4)", padding: "10px 12px", borderRadius: "6px", fontSize: "13px" }}>
                        <div style={{ fontSize: "12px", color: "var(--muted)", marginBottom: "6px" }}>
                          建议匹配的本地工作项：
                        </div>
                        {item.candidates.map((cand) => {
                          const candId = cand.local_task_id || cand.local_task?.id;
                          const isSelected = currentDec.action === "match" && currentDec.targetTaskId === candId;

                          return (
                            <label
                              key={candId}
                              style={{
                                display: "flex",
                                alignItems: "center",
                                gap: "8px",
                                cursor: "pointer",
                                padding: "4px 0",
                              }}
                            >
                              <input
                                type="radio"
                                name={`match-${item.id}`}
                                checked={isSelected}
                                onChange={() => handleTargetChange(item.id, candId)}
                              />
                              <span style={{ flex: 1 }}>{cand.local_task?.title}</span>
                              <span style={{ color: "var(--muted)", fontSize: "12px" }}>
                                {cand.local_task?.confirmed_minutes ?? 0} 分钟 ({(cand.confidence * 100).toFixed(0)}%)
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    )}

                    {/* Action buttons */}
                    <div style={{ display: "flex", gap: "10px", marginTop: "4px" }}>
                      <button
                        type="button"
                        className={currentDec.action === "match" ? "primary-small" : "outline-small"}
                        disabled={item.candidates.length === 0}
                        onClick={() => handleActionChange(item.id, "match")}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "12px" }}
                      >
                        <Link2 size={14} /> 确认关联并继承工时
                      </button>
                      <button
                        type="button"
                        className={currentDec.action === "create" ? "primary-small" : "outline-small"}
                        onClick={() => handleActionChange(item.id, "create")}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "12px" }}
                      >
                        <PlusCircle size={14} /> 作为独立新项导入
                      </button>
                      <button
                        type="button"
                        className={currentDec.action === "ignore" ? "danger-small" : "outline-small"}
                        onClick={() => handleActionChange(item.id, "ignore")}
                        style={{ display: "inline-flex", alignItems: "center", gap: "4px", fontSize: "12px" }}
                      >
                        <Ban size={14} /> 忽略 / 丢弃
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        <div className="modal-actions" style={{ marginTop: "16px", paddingTop: "12px", borderTop: "1px solid var(--border)", display: "flex", justifyContent: "flex-end", gap: "10px" }}>
          <button type="button" className="outline" disabled={busy} onClick={onClose}>
            取消
          </button>
          <button type="button" className="primary" disabled={busy} onClick={handleSubmit}>
            {busy ? "应用中..." : "保存匹配并更新清单"}
          </button>
        </div>
      </section>
    </div>
  );
}
