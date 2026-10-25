import { useRef, useState, useEffect } from "react";
import { Sparkles, X, Plus, Trash2 } from "lucide-react";
import type { Report } from "./types";
import "./workflows.css";

type Quick = { category: string; name: string; hours: string };
const h = (minutes: number) => Number((minutes / 60).toFixed(2));
function readDraft(day: string) {
  try {
    const value = JSON.parse(
      sessionStorage.getItem(`allocation-draft:${day}`) || "null",
    );
    if (
      value &&
      typeof value.enabled === "boolean" &&
      typeof value.requestId === "string" &&
      Array.isArray(value.items) &&
      value.items.length > 0 &&
      value.items.length <= 20 &&
      value.items.every(
        (i: Quick) =>
          i &&
          ["沟通", "运维"].includes(i.category) &&
          typeof i.name === "string" &&
          typeof i.hours === "string",
      )
    )
      return value as {
        enabled: boolean;
        requestId: string;
        items: Quick[];
        error?: string;
      };
  } catch {
    /* A blocked or damaged browser draft must not prevent opening. */
  }
  return {
    enabled: false,
    requestId: crypto.randomUUID(),
    items: [{ category: "沟通", name: "", hours: "1" }],
    error: "",
  };
}

export function AllocationModal({
  report,
  onClose,
  onApplied,
}: {
  report: Report;
  onClose: () => void;
  onApplied: (message: string) => Promise<void>;
}) {
  const [draft] = useState(() => readDraft(report.date));
  const [enabled, setEnabled] = useState(draft.enabled),
    [items, setItems] = useState<Quick[]>(draft.items);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(draft.error || ""),
    [done, setDone] = useState(false);
  const requestId = useRef(draft.requestId);
  useEffect(() => {
    try {
      if (done) sessionStorage.removeItem(`allocation-draft:${report.date}`);
      else
        sessionStorage.setItem(
          `allocation-draft:${report.date}`,
          JSON.stringify({
            enabled,
            items,
            requestId: requestId.current,
            error,
          }),
        );
    } catch {
      /* Inputs remain available in memory when storage is unavailable. */
    }
  }, [enabled, items, error, done, report.date]);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
  }, []);
  const fixed = report.tasks
    .filter((t) => t.time_locked)
    .reduce((s, t) => s + t.confirmed_minutes, 0);
  const quick = enabled
    ? items.reduce((s, t) => s + Math.round(Number(t.hours) * 60), 0)
    : 0;
  const invalid =
    enabled &&
    items.some(
      (t) =>
        !t.name.trim() ||
        !Number.isFinite(Number(t.hours)) ||
        Number(t.hours) <= 0 ||
        Math.abs(Number(t.hours) * 60 - Math.round(Number(t.hours) * 60)) >
          1e-7,
    );
  const remaining = report.target_minutes - fixed - quick;
  const incompatible = remaining >= 0 && remaining % 30 !== 0;
  const timing =
    report.conversations.work_timing || report.conversations.timing;
  const update = (index: number, patch: Partial<Quick>) => {
    requestId.current = crypto.randomUUID();
    setItems(
      items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  };
  return (
    <div className="modal-overlay">
      <section
        className="modal allocation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="allocation-title"
        onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) onClose();
          if (e.key === "Tab") {
            const els = Array.from(
              e.currentTarget.querySelectorAll<HTMLElement>(
                'button:not(:disabled),input:not(:disabled),select:not(:disabled),[tabindex="0"]',
              ),
            );
            const first = els[0],
              last = els.at(-1);
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last?.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <div className="modal-head">
          <div>
            <h2 id="allocation-title">
              <Sparkles size={20} /> AI 分配工时
            </h2>
            <p>
              {report.date} · 每日目标 {h(report.target_minutes)}h
            </p>
          </div>
          <button
            ref={closeButton}
            type="button"
            className="icon-button"
            aria-label="关闭工时分配"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {!done ? (
          <>
            {error && (
              <div role="alert" className="allocation-error">
                <strong>本次操作未完成</strong>
                <p>{error}</p>
                <p>本次输入已保留。直接重试会复用同一请求，避免重复添加。</p>
              </div>
            )}
            <div className="allocation-summary">
              <span>
                已固定 <strong>{h(fixed)}h</strong>
              </span>
              <span>
                人工补充 <strong>{h(quick)}h</strong>
              </span>
              <span>
                AI 分配 <strong>{h(Math.max(0, remaining))}h</strong>
              </span>
            </div>
            <p>
              AI
              将参考相关消息的时间、会话跨度、活跃片段、工具运行时长，以及每项工作的范围、难度与验证情况。
              生成工时按0.5小时递增，例如1h、1.5h、2h；人工固定时长保持不变。
            </p>
            {timing && (
              <p className="allocation-reference">
                {report.conversations.work_timing
                  ? "工作日志参考"
                  : "采集日志参考（待相关性筛选）"}
                ：会话跨度 {timing.session_span_minutes} 分钟 · 活跃片段{" "}
                {timing.observable_active_minutes} 分钟 ·{" "}
                {report.conversations.user_messages}{" "}
                条采集用户消息。时间片段会去重，AI
                估算只使用各工作项关联的有效证据。
              </p>
            )}
            <label className="check">
              <input
                type="checkbox"
                checked={enabled}
                onChange={(e) => {
                  setEnabled(e.target.checked);
                  requestId.current = crypto.randomUUID();
                }}
                disabled={busy}
              />
              添加人工事项（运维 / 沟通）
            </label>
            {enabled && (
              <fieldset disabled={busy} className="quick-items">
                <legend>人工事项（{items.length} 项）· 支持一次添加多项</legend>
                <div className="quick-toolbar">
                  <span>填写简称与实际时长，AI 按历史风格命名</span>
                  <button
                    type="button"
                    className="outline"
                    disabled={items.length >= 20}
                    onClick={() => {
                      requestId.current = crypto.randomUUID();
                      setItems([
                        ...items,
                        { category: "运维", name: "", hours: "0.5" },
                      ]);
                    }}
                  >
                    <Plus size={15} />
                    继续添加人工事项
                  </button>
                </div>
                {items.map((item, i) => (
                  <div className="quick-item" key={i}>
                    <label>
                      类别
                      <select
                        aria-label={`人工事项类别 ${i + 1}`}
                        value={item.category}
                        onChange={(e) =>
                          update(i, { category: e.target.value })
                        }
                      >
                        <option>运维</option>
                        <option>沟通</option>
                      </select>
                    </label>
                    <label>
                      事项名称
                      <input
                        aria-label={`人工事项名称 ${i + 1}`}
                        maxLength={200}
                        value={item.name}
                        placeholder="例如：附件挂接需求"
                        onChange={(e) => update(i, { name: e.target.value })}
                      />
                    </label>
                    <label>
                      时长（小时）
                      <input
                        aria-label={`人工事项时长 ${i + 1}`}
                        type="number"
                        min="0.0166666667"
                        max="24"
                        step="any"
                        value={item.hours}
                        onChange={(e) => update(i, { hours: e.target.value })}
                      />
                    </label>
                    <button
                      type="button"
                      className="icon-button"
                      aria-label={`移除人工事项 ${i + 1}`}
                      disabled={items.length === 1}
                      onClick={() => {
                        requestId.current = crypto.randomUUID();
                        setItems(items.filter((_, j) => j !== i));
                      }}
                    >
                      <Trash2 size={16} />
                    </button>
                  </div>
                ))}
                <p>这些时长固定保留，AI 仅分配剩余工时。</p>
              </fieldset>
            )}
            <details className="agent-prompt">
              <summary>参与分配的 {report.tasks.length} 个工作项</summary>
              {report.tasks.map((t) => (
                <p key={t.id}>
                  {t.title} ·{" "}
                  {t.time_locked
                    ? `固定 ${h(t.confirmed_minutes)}h`
                    : "由 AI 估算"}
                </p>
              ))}
            </details>
            {remaining < 0 && (
              <p role="alert" className="error">
                固定和人工工时已超过每日目标，请调整。
              </p>
            )}
            {incompatible && (
              <p role="alert" className="allocation-error">
                剩余 {remaining}{" "}
                分钟无法按0.5小时分配。请调整人工工时或每日目标，系统会保留原有工时。
              </p>
            )}
            <p>
              分配结果会保存为草稿。日志时间仅作参考，请核对实际投入后确认日报。
            </p>
            <div className="modal-footer">
              <button
                type="button"
                className="outline"
                onClick={onClose}
                disabled={busy}
              >
                取消
              </button>
              <button
                type="button"
                disabled={busy || invalid || remaining < 0 || incompatible}
                onClick={async () => {
                  setBusy(true);
                  setError("");
                  let saved = false;
                  try {
                    const r = await fetch(
                      `/api/reports/${report.date}/estimate-time`,
                      {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                          request_id: requestId.current,
                          manual_items: enabled
                            ? items.map((i) => ({
                                category: i.category,
                                name: i.name,
                                minutes: Math.round(Number(i.hours) * 60),
                              }))
                            : [],
                        }),
                      },
                    );
                    const result = await r.json();
                    if (!r.ok)
                      throw Error(
                        typeof result.detail === "string"
                          ? result.detail
                          : "请检查事项名称与时长",
                      );
                    saved = true;
                    await onApplied(
                      `已生成 ${h(result.total_minutes)}h 工时分配草稿，保留 ${h(result.fixed_minutes)}h 固定工时。${result.warnings.join("；")}`,
                    );
                    setDone(true);
                  } catch (e) {
                    setError(
                      saved
                        ? "分配结果已保存，但界面刷新失败；请重试读取已保存的结果。"
                        : (e as Error).message,
                    );
                  } finally {
                    setBusy(false);
                  }
                }}
              >
                <Sparkles size={16} />
                {busy
                  ? "AI 正在命名和分配…"
                  : enabled
                    ? "生成条目并分配工时"
                    : "生成工时分配草稿"}
              </button>
            </div>
          </>
        ) : (
          <>
            <p role="status">
              分配草稿已保存。以下为各项工时，可关闭弹窗后继续修改。
            </p>
            <div className="allocation-results">
              {report.tasks.map((t) => (
                <div key={t.id}>
                  <span>
                    {t.title}
                    <small>
                      {t.time_locked ? "人工固定" : t.time_reason || "AI建议"}
                    </small>
                  </span>
                  <strong>{h(t.confirmed_minutes)}h</strong>
                </div>
              ))}
            </div>
            <p>合计 {h(report.total_minutes)}h · 日报尚未自动确认</p>
            <div className="modal-footer">
              <button type="button" onClick={onClose}>
                完成，返回审核
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
