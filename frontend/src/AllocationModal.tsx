import { useRef, useState, useEffect } from "react";
import { Sparkles, X, Plus, Trash2, LockKeyhole } from "lucide-react";
import type { Report } from "./types";
import "./workflows.css";

type Quick = { category: string; name: string; hours: string };
export type AllocationRequest = {
  request_id: string;
  task_ids: string[];
  manual_items: { category: string; name: string; minutes: number }[];
  target_minutes?: number;
};
const h = (minutes: number) => Number((minutes / 60).toFixed(2));
function unlockedIds(report: Report) {
  return report.tasks.filter((t) => !t.time_locked).map((t) => t.id);
}
function readDraft(day: string, report: Report) {
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
      ) &&
      (value.selected === null ||
        (Array.isArray(value.selected) &&
          value.selected.every((id: unknown) => typeof id === "string")))
    ) {
      const isConsumed = Boolean(report.time_allocation);
      return {
        enabled: value.enabled as boolean,
        requestId: isConsumed ? crypto.randomUUID() : (value.requestId as string),
        items: value.items as Quick[],
        selected:
          value.selected === null
            ? null
            : (value.selected as string[]).filter((id) =>
                unlockedIds(report).includes(id),
              ),
      };
    }
  } catch {
    /* A blocked or damaged browser draft must not prevent opening. */
  }
  return {
    enabled: false,
    requestId: crypto.randomUUID(),
    items: [{ category: "沟通", name: "", hours: "1" }] as Quick[],
    selected: null,
  };
}

export function AllocationModal({
  report,
  onClose,
  onStart,
}: {
  report: Report;
  onClose: () => void;
  onStart: (request: AllocationRequest) => void;
}) {
  const [draft] = useState(() => readDraft(report.date, report));
  const [enabled, setEnabled] = useState(draft.enabled),
    [items, setItems] = useState<Quick[]>(draft.items),
    [selected, setSelected] = useState<string[] | null>(draft.selected);
  const [error, setError] = useState("");
  const requestId = useRef(draft.requestId);
  const available = unlockedIds(report);
  const chosen = selected === null ? available : selected;
  useEffect(() => {
    try {
      sessionStorage.setItem(
        `allocation-draft:${report.date}`,
        JSON.stringify({
          enabled,
          items,
          requestId: requestId.current,
          selected,
        }),
      );
    } catch {
      /* Inputs remain available in memory when storage is unavailable. */
    }
  }, [enabled, items, selected, report.date]);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeButton.current?.focus();
  }, []);
  const locked = report.tasks.filter((t) => t.time_locked);
  const lockedMinutes = locked.reduce((s, t) => s + t.confirmed_minutes, 0);
  const preserved = report.tasks
    .filter((t) => !t.time_locked && !chosen.includes(t.id))
    .reduce((s, t) => s + t.confirmed_minutes, 0);
  const fixed = lockedMinutes + preserved;
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
  const update = (index: number, patch: Partial<Quick>) => {
    requestId.current = crypto.randomUUID();
    setItems(
      items.map((item, i) => (i === index ? { ...item, ...patch } : item)),
    );
  };
  const toggle = (id: string) => {
    requestId.current = crypto.randomUUID();
    setSelected(
      chosen.includes(id)
        ? chosen.filter((value) => value !== id)
        : [...chosen, id],
    );
  };
  const submit = () => {
    if (remaining > 0 && chosen.length === 0) {
      setError("请至少勾选一个需要 AI 分配工时的工作项。");
      return;
    }
    onStart({
      request_id: requestId.current,
      task_ids: chosen,
      target_minutes: report.target_minutes,
      manual_items: enabled
        ? items.map((i) => ({
            category: i.category,
            name: i.name,
            minutes: Math.round(Number(i.hours) * 60),
          }))
        : [],
    });
  };
  return (
    <div className="modal-overlay">
      <section
        className="modal allocation-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="allocation-title"
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
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
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {error && (
          <div role="alert" className="allocation-error">
            <strong>请先补全选择</strong>
            <p>{error}</p>
          </div>
        )}
        <div className="allocation-summary">
          <span>
            固定 / 保留 <strong>{h(fixed)}h</strong>
          </span>
          <span>
            人工补充 <strong>{h(quick)}h</strong>
          </span>
          <span>
            AI 分配 <strong>{h(Math.max(0, remaining))}h</strong>
          </span>
        </div>
        <fieldset className="allocation-picker">
          <legend>
            选择由 AI 分配工时的工作项（{chosen.length}/{available.length}）
          </legend>
          <div className="allocation-picker-head">
            <label className="check">
              <input
                type="checkbox"
                checked={
                  available.length > 0 && chosen.length === available.length
                }
                disabled={!available.length}
                onChange={(e) => {
                  requestId.current = crypto.randomUUID();
                  setSelected(e.target.checked ? available : []);
                }}
              />
              全选未固定工作项
            </label>
            <span>未勾选的工作项保持当前工时不变</span>
          </div>
          {available.length === 0 ? (
            <p className="muted">
              当前工作项均已固定，没有需要 AI 分配的项目。
            </p>
          ) : (
            <div className="allocation-picker-list">
              {report.tasks
                .filter((t) => !t.time_locked)
                .map((t) => (
                  <label key={t.id} className="allocation-pick">
                    <input
                      type="checkbox"
                      aria-label={`选择 ${t.title}`}
                      checked={chosen.includes(t.id)}
                      onChange={() => toggle(t.id)}
                    />
                    <span>{t.title}</span>
                    <small>
                      {t.confirmed_minutes
                        ? `当前 ${h(t.confirmed_minutes)}h`
                        : "待分配"}
                    </small>
                  </label>
                ))}
            </div>
          )}
          {locked.length > 0 && (
            <p className="allocation-locked">
              <LockKeyhole size={14} />
              {locked.length} 个固定工时项共 {h(lockedMinutes)}h，不参与 AI
              重新分配。
            </p>
          )}
        </fieldset>
        <label className="check">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(e) => {
              setEnabled(e.target.checked);
              requestId.current = crypto.randomUUID();
            }}
          />
          添加人工事项（运维 / 沟通）
        </label>
        {enabled && (
          <fieldset className="quick-items">
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
              </fieldset>
            )}
        {remaining < 0 && (
          <p role="alert" className="error">
            固定和人工工时已超过每日目标，请调整。
          </p>
        )}
        {incompatible && (
          <p role="alert" className="allocation-error">
            剩余 {remaining}{" "}
            分钟无法按0.5小时分配。请调整人工工时、勾选项或每日目标，系统会保留原有工时。
          </p>
        )}
        <div className="modal-footer">
          <button type="button" className="outline" onClick={onClose}>
            取消
          </button>
          <button
            type="button"
            disabled={invalid || remaining < 0 || incompatible}
            onClick={submit}
          >
            <Sparkles size={16} />
            {enabled ? "生成条目并分配工时" : "开始 AI 分配"}
          </button>
        </div>
      </section>
    </div>
  );
}
