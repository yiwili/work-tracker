import { useState, useRef, useEffect } from "react";
import { AlertTriangle, X, Check, GitMerge, ExternalLink, Sparkles } from "lucide-react";
import type { DuplicatePair, SimilarityResolution } from "./types";
import { api } from "./api";

export function SimilarityMergeModal({
  day,
  pairs,
  onClose,
  onApplied,
  onProceedToAllocation,
}: {
  day: string;
  pairs: DuplicatePair[];
  onClose: () => void;
  onApplied: () => Promise<void>;
  onProceedToAllocation?: () => void;
}) {
  const [decisions, setDecisions] = useState<Record<string, SimilarityResolution>>(() => {
    const init: Record<string, SimilarityResolution> = {};
    for (const p of pairs) {
      init[p.pair_id] = {
        pair_id: p.pair_id,
        action: "ai_merge",
        target_task_id: p.manual_task.id,
        title: p.recommended_title,
        details: p.recommended_details,
        project: p.recommended_project,
        category: p.recommended_category,
        confirmed_minutes: p.recommended_minutes,
        status: p.recommended_status,
      };
    }
    return init;
  });

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    closeButton.current?.focus();
  }, []);

  const handleActionChange = (
    pair: DuplicatePair,
    action: "ai_merge" | "keep_manual" | "keep_ai" | "ignore"
  ) => {
    setDecisions((prev) => {
      const current = prev[pair.pair_id];
      let updated: SimilarityResolution = {
        ...current,
        action,
      };

      if (action === "ai_merge") {
        updated = {
          ...updated,
          target_task_id: pair.manual_task.id,
          title: pair.recommended_title,
          details: pair.recommended_details,
          project: pair.recommended_project,
          category: pair.recommended_category,
          confirmed_minutes: pair.recommended_minutes,
        };
      } else if (action === "keep_manual") {
        updated = {
          ...updated,
          target_task_id: pair.manual_task.id,
          title: pair.manual_task.title,
          details: pair.manual_task.details,
          project: pair.manual_task.project,
          category: pair.manual_task.category,
          confirmed_minutes: pair.manual_task.confirmed_minutes,
        };
      } else if (action === "keep_ai") {
        updated = {
          ...updated,
          target_task_id: pair.ai_task.id,
          title: pair.ai_task.title,
          details: pair.ai_task.details,
          project: pair.ai_task.project,
          category: pair.ai_task.category,
          confirmed_minutes: pair.manual_task.confirmed_minutes || pair.ai_task.confirmed_minutes,
        };
      }

      return { ...prev, [pair.pair_id]: updated };
    });
  };

  const handleFieldChange = (
    pairId: string,
    field: keyof SimilarityResolution,
    value: string | number
  ) => {
    setDecisions((prev) => ({
      ...prev,
      [pairId]: {
        ...prev[pairId],
        [field]: value,
      },
    }));
  };

  const handleSubmit = async (overrideResolutions?: SimilarityResolution[]) => {
    setBusy(true);
    setError("");
    try {
      const resolutions = overrideResolutions || Object.values(decisions);
      await api(`/reports/${day}/resolve-similarity`, "POST", {
        resolutions,
      });
      await onApplied();
      if (onProceedToAllocation) {
        onProceedToAllocation();
      }
      onClose();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleIgnoreAll = async () => {
    const allIgnore: SimilarityResolution[] = pairs.map((p) => ({
      pair_id: p.pair_id,
      action: "ignore",
    }));
    await handleSubmit(allIgnore);
  };

  return (
    <div className="modal-overlay">
      <section
        className="modal similarity-merge-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="similarity-modal-title"
        style={{
          maxWidth: "880px",
          width: "95vw",
          maxHeight: "90vh",
          display: "flex",
          flexDirection: "column",
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) onClose();
        }}
      >
        <div className="modal-head">
          <div>
            <h2
              id="similarity-modal-title"
              style={{ display: "flex", alignItems: "center", gap: "8px", color: "#e37400" }}
            >
              <AlertTriangle size={22} />
              人工与 AI 工作项相似度提醒与合并推荐
            </h2>
            <p>{day} · 检测到 {pairs.length} 组高度重复的工作项，请核对处理</p>
          </div>
          <button
            ref={closeButton}
            type="button"
            className="icon-button"
            aria-label="关闭相似度检测"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>

        {error && (
          <div className="allocation-error" role="alert" style={{ margin: "10px 0" }}>
            {error}
          </div>
        )}

        <div
          style={{
            padding: "10px 14px",
            background: "#fef7e0",
            border: "1px solid #f9ab00",
            borderRadius: "6px",
            fontSize: "13px",
            color: "#7a4100",
            marginBottom: "14px",
            lineHeight: "1.5",
          }}
        >
          <strong>注意：</strong>
          系统在计算工时前强制对比了人工工作项与 AI 工作项。发现以下高度重复项，
          已由 AI 为您生成合并推荐选项（支持修改）。处理完成后继续计算工时将不再重复提醒。
        </div>

        <div
          style={{
            flex: 1,
            overflowY: "auto",
            display: "flex",
            flexDirection: "column",
            gap: "16px",
            paddingRight: "4px",
          }}
        >
          {pairs.map((pair, idx) => {
            const dec = decisions[pair.pair_id] || {
              pair_id: pair.pair_id,
              action: "ai_merge",
              title: pair.recommended_title,
              details: pair.recommended_details,
              project: pair.recommended_project,
              category: pair.recommended_category,
              confirmed_minutes: pair.recommended_minutes,
            };

            return (
              <div
                key={pair.pair_id}
                style={{
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                  padding: "14px",
                  background: "var(--surface)",
                }}
              >
                {/* Header bar of pair */}
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    alignItems: "center",
                    flexWrap: "wrap",
                    gap: "8px",
                    marginBottom: "12px",
                    borderBottom: "1px solid var(--border)",
                    paddingBottom: "8px",
                  }}
                >
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span
                      style={{
                        background: "#fce8e6",
                        color: "#c5221f",
                        fontWeight: "bold",
                        fontSize: "12px",
                        padding: "2px 8px",
                        borderRadius: "12px",
                      }}
                    >
                      重复组 #{idx + 1} · 相似度 {Math.round(pair.similarity_score * 100)}%
                    </span>
                    <span style={{ fontSize: "12px", color: "var(--muted)" }}>
                      {pair.reason}
                    </span>
                  </div>

                  {/* Actions Radio / Buttons */}
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: "6px",
                      fontSize: "13px",
                    }}
                  >
                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "4px",
                        cursor: "pointer",
                        fontWeight: dec.action === "ai_merge" ? "bold" : "normal",
                        color: dec.action === "ai_merge" ? "var(--primary)" : "inherit",
                      }}
                    >
                      <input
                        type="radio"
                        name={`action_${pair.pair_id}`}
                        checked={dec.action === "ai_merge"}
                        onChange={() => handleActionChange(pair, "ai_merge")}
                        disabled={busy}
                      />
                      <Sparkles size={14} /> AI 智能合并
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "4px",
                        cursor: "pointer",
                        fontWeight: dec.action === "keep_manual" ? "bold" : "normal",
                      }}
                    >
                      <input
                        type="radio"
                        name={`action_${pair.pair_id}`}
                        checked={dec.action === "keep_manual"}
                        onChange={() => handleActionChange(pair, "keep_manual")}
                        disabled={busy}
                      />
                      保留人工项
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "4px",
                        cursor: "pointer",
                        fontWeight: dec.action === "keep_ai" ? "bold" : "normal",
                      }}
                    >
                      <input
                        type="radio"
                        name={`action_${pair.pair_id}`}
                        checked={dec.action === "keep_ai"}
                        onChange={() => handleActionChange(pair, "keep_ai")}
                        disabled={busy}
                      />
                      保留 AI 项
                    </label>

                    <label
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "4px",
                        cursor: "pointer",
                        fontWeight: dec.action === "ignore" ? "bold" : "normal",
                        color: dec.action === "ignore" ? "var(--muted)" : "inherit",
                      }}
                    >
                      <input
                        type="radio"
                        name={`action_${pair.pair_id}`}
                        checked={dec.action === "ignore"}
                        onChange={() => handleActionChange(pair, "ignore")}
                        disabled={busy}
                      />
                      保持原样
                    </label>
                  </div>
                </div>

                {/* Comparison Columns */}
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "1fr 1fr",
                    gap: "12px",
                    marginBottom: "12px",
                    background: "var(--surface-elevated, #f8f9fa)",
                    padding: "10px",
                    borderRadius: "6px",
                  }}
                >
                  {/* Manual Task */}
                  <div style={{ fontSize: "12px", borderRight: "1px solid var(--border)", paddingRight: "10px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
                      <span className="tag manual" style={{ fontSize: "11px" }}>人工补录</span>
                      <strong>{pair.manual_task.title}</strong>
                    </div>
                    <div style={{ color: "var(--muted)", marginBottom: "4px" }}>
                      项目: {pair.manual_task.project || "未归属"} · 分类: {pair.manual_task.category} · 工时: {pair.manual_task.confirmed_minutes} 分钟
                    </div>
                    {pair.manual_task.details && (
                      <div
                        style={{
                          color: "var(--text)",
                          whiteSpace: "pre-wrap",
                          maxHeight: "60px",
                          overflowY: "auto",
                          background: "var(--surface)",
                          padding: "4px 6px",
                          borderRadius: "4px",
                        }}
                      >
                        {pair.manual_task.details}
                      </div>
                    )}
                  </div>

                  {/* AI Task */}
                  <div style={{ fontSize: "12px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", marginBottom: "4px" }}>
                      <span className="tag" style={{ fontSize: "11px" }}>AI / Git 提取</span>
                      <strong>{pair.ai_task.title}</strong>
                    </div>
                    <div style={{ color: "var(--muted)", marginBottom: "4px" }}>
                      项目: {pair.ai_task.project || "未归属"} · 分类: {pair.ai_task.category} · 证据: {pair.ai_task.evidence?.length || 0} 条
                    </div>
                    {pair.ai_task.details && (
                      <div
                        style={{
                          color: "var(--text)",
                          whiteSpace: "pre-wrap",
                          maxHeight: "60px",
                          overflowY: "auto",
                          background: "var(--surface)",
                          padding: "4px 6px",
                          borderRadius: "4px",
                        }}
                      >
                        {pair.ai_task.details}
                      </div>
                    )}
                  </div>
                </div>

                {/* Editable Preview Section */}
                {dec.action !== "ignore" && (
                  <div
                    style={{
                      background: dec.action === "ai_merge" ? "#f4f8fe" : "var(--surface)",
                      border: "1px dashed var(--border)",
                      borderRadius: "6px",
                      padding: "10px",
                    }}
                  >
                    <div
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                        marginBottom: "8px",
                        fontSize: "12px",
                        fontWeight: "bold",
                        color: "var(--primary)",
                      }}
                    >
                      <GitMerge size={14} />
                      {dec.action === "ai_merge"
                        ? "AI 推荐合并结果（支持直接修改）："
                        : dec.action === "keep_manual"
                          ? "以人工项为主合并（将挂载 AI 证据）："
                          : "以 AI 项为主合并（将继承人工工时）："}
                    </div>

                    <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                      <div>
                        <label style={{ fontSize: "12px", display: "block", marginBottom: "2px" }}>
                          合并后标题：
                        </label>
                        <input
                          type="text"
                          style={{ width: "100%", padding: "6px 8px", fontSize: "13px" }}
                          value={dec.title || ""}
                          disabled={busy}
                          onChange={(e) => handleFieldChange(pair.pair_id, "title", e.target.value)}
                        />
                      </div>

                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "8px" }}>
                        <div>
                          <label style={{ fontSize: "12px", display: "block", marginBottom: "2px" }}>
                            所属项目：
                          </label>
                          <input
                            type="text"
                            style={{ width: "100%", padding: "4px 6px", fontSize: "12px" }}
                            value={dec.project || ""}
                            disabled={busy}
                            onChange={(e) => handleFieldChange(pair.pair_id, "project", e.target.value)}
                          />
                        </div>

                        <div>
                          <label style={{ fontSize: "12px", display: "block", marginBottom: "2px" }}>
                            事项分类：
                          </label>
                          <select
                            style={{ width: "100%", padding: "4px 6px", fontSize: "12px" }}
                            value={dec.category || "开发"}
                            disabled={busy}
                            onChange={(e) => handleFieldChange(pair.pair_id, "category", e.target.value)}
                          >
                            <option value="开发">开发</option>
                            <option value="测试">测试</option>
                            <option value="运维">运维</option>
                            <option value="设计">设计</option>
                            <option value="文档">文档</option>
                            <option value="沟通">沟通</option>
                            <option value="其他">其他</option>
                          </select>
                        </div>

                        <div>
                          <label style={{ fontSize: "12px", display: "block", marginBottom: "2px" }}>
                            确认工时（分钟）：
                          </label>
                          <input
                            type="number"
                            min="0"
                            max="1440"
                            step="15"
                            style={{ width: "100%", padding: "4px 6px", fontSize: "12px" }}
                            value={dec.confirmed_minutes ?? 0}
                            disabled={busy}
                            onChange={(e) =>
                              handleFieldChange(pair.pair_id, "confirmed_minutes", Number(e.target.value))
                            }
                          />
                        </div>
                      </div>

                      <div>
                        <label style={{ fontSize: "12px", display: "block", marginBottom: "2px" }}>
                          合并后详情内容：
                        </label>
                        <textarea
                          rows={3}
                          style={{ width: "100%", padding: "6px 8px", fontSize: "12px", resize: "vertical" }}
                          value={dec.details || ""}
                          disabled={busy}
                          onChange={(e) => handleFieldChange(pair.pair_id, "details", e.target.value)}
                        />
                      </div>
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="modal-footer" style={{ marginTop: "14px", display: "flex", justifyContent: "space-between" }}>
          <div>
            <button
              type="button"
              className="outline"
              disabled={busy}
              onClick={handleIgnoreAll}
              title="不进行合并，保持现有工作项并继续"
            >
              全部保持原样并继续
            </button>
          </div>

          <div style={{ display: "flex", gap: "8px" }}>
            <button
              type="button"
              className="outline"
              disabled={busy}
              onClick={onClose}
            >
              取消
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => handleSubmit()}
            >
              {busy ? "正在处理…" : `确认合并与处理（${pairs.length}组）`}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}
