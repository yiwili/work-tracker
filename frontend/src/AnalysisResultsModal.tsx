import { useState } from "react";
import {
  Check,
  FolderInput,
  LoaderCircle,
  Sparkles,
  X,
} from "lucide-react";
import type { PendingTask } from "./types";
import { api } from "./api";

type Row = PendingTask & { selected: boolean };

export function AnalysisResultsModal({
  day,
  pending,
  stale,
  onClose,
  onImported,
}: {
  day: string;
  pending: PendingTask[];
  stale: boolean;
  onClose: () => void;
  onImported: () => Promise<void> | void;
}) {
  const [rows, setRows] = useState<Row[]>(() =>
    pending.map((item) => ({ ...item, selected: true })),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const selected = rows.filter((row) => row.selected);
  const allSelected = rows.length > 0 && selected.length === rows.length;
  const change = (fingerprint: string, patch: Partial<Row>) =>
    setRows(
      rows.map((row) =>
        row.fingerprint === fingerprint ? { ...row, ...patch } : row,
      ),
    );
  const submit = async () => {
    if (!selected.length) return;
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const result = await api<{
        created: number;
        archived: number;
        remaining: number;
        imported: string[];
        warnings: string[];
      }>("/analysis/import", "POST", {
        date: day,
        items: selected.map((row) => ({
          fingerprint: row.fingerprint,
          title: row.title,
          details: row.details,
          project: row.project,
          category: row.category,
        })),
      });
      const imported = new Set(result.imported || []);
      setRows(rows.filter((row) => !imported.has(row.fingerprint)));
      await onImported();
      const pieces = [`已导入 ${result.created} 项工作`];
      if (result.remaining) pieces.push(`还剩 ${result.remaining} 条候选`);
      if (result.archived)
        pieces.push(`替换 ${result.archived} 项未审核草稿`);
      if (result.warnings.length) pieces.push(...result.warnings);
      setMessage(pieces.join("；") + "。");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="modal-overlay">
      <section
        className="modal wide analysis-results"
        role="dialog"
        aria-modal="true"
        aria-label="AI 分析结果"
      >
        <div className="modal-head">
          <div>
            <div className="eyebrow">REVIEW BEFORE IMPORT</div>
            <h2>AI 分析结果 · {day}</h2>
          </div>
          <button aria-label="关闭分析结果" onClick={onClose}>
            <X size={20} />
          </button>
        </div>
        <p className="muted">
          默认不会自动写入今日工作清单。勾选需要导入的候选，可直接修改标题、项目、分类和详情后再导入。
        </p>
        {stale && (
          <div className="alert error" role="alert">
            对话或分析设置已变化，这批候选可能不是最新结果；建议关闭后重新分析。
          </div>
        )}
        {rows.length === 0 ? (
          <div className="empty analysis-empty">
            <Check size={28} />
            <h3>没有待导入的分析结果</h3>
            <p>
              可以关闭窗口；之后重新分析全天对话，再在“分析结果”里确认导入。
            </p>
          </div>
        ) : (
          <>
            <div className="analysis-list-head">
              <label className="check-label">
                <input
                  type="checkbox"
                  checked={allSelected}
                  onChange={(e) =>
                    setRows(
                      rows.map((row) => ({ ...row, selected: e.target.checked })),
                    )
                  }
                />
                全选（{selected.length}/{rows.length}）
              </label>
              <span>
                共 {rows.length} 条候选，依据 {rows.reduce((n, r) => n + r.evidence_ids.length, 0)} 条活动记录
              </span>
            </div>
            <div className="analysis-list">
              {rows.map((row) => (
                <article
                  className={
                    "analysis-item" + (row.selected ? " selected" : "")
                  }
                  key={row.fingerprint}
                >
                  <input
                    type="checkbox"
                    aria-label={"导入 " + row.title}
                    checked={row.selected}
                    disabled={saving}
                    onChange={(e) =>
                      change(row.fingerprint, { selected: e.target.checked })
                    }
                  />
                  <div className="analysis-item-body">
                    <label>
                      工作项标题
                      <input
                        value={row.title}
                        maxLength={500}
                        disabled={saving}
                        onChange={(e) =>
                          change(row.fingerprint, { title: e.target.value })
                        }
                      />
                    </label>
                    <div className="form-grid">
                      <label>
                        项目
                        <input
                          value={row.project}
                          disabled={saving}
                          onChange={(e) =>
                            change(row.fingerprint, { project: e.target.value })
                          }
                        />
                      </label>
                      <label>
                        分类
                        <input
                          value={row.category}
                          disabled={saving}
                          onChange={(e) =>
                            change(row.fingerprint, {
                              category: e.target.value,
                            })
                          }
                        />
                      </label>
                    </div>
                    <label>
                      工作详情
                      <textarea
                        rows={3}
                        value={row.details}
                        disabled={saving}
                        onChange={(e) =>
                          change(row.fingerprint, { details: e.target.value })
                        }
                      />
                    </label>
                    <small>
                      {row.evidence_level} · {row.evidence_ids.length} 条依据 ·{" "}
                      {row.confidence}
                    </small>
                  </div>
                </article>
              ))}
            </div>
            <div className="analysis-actions">
              <span className="tag">
                <Sparkles size={12} /> 导入后进入今日清单，可继续审核工时
              </span>
              <button
                className="primary"
                type="button"
                disabled={saving || !selected.length}
                onClick={submit}
              >
                {saving ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <FolderInput size={16} />
                )}
                导入选中的 {selected.length} 项
              </button>
            </div>
          </>
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
    </div>
  );
}
