import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Check, ChevronDown, Clock3, Plus, X } from "lucide-react";
import { api } from "./api";
import type { Task } from "./types";

type DropdownPosition = {
  left: number;
  top?: number;
  bottom?: number;
  minWidth: number;
  maxHeight: number;
  placement: "bottom" | "top";
};

function useFloatingDropdown(
  open: boolean,
  setOpen: React.Dispatch<React.SetStateAction<boolean>>,
  rootRef: React.RefObject<HTMLDivElement | null>,
  triggerRef: React.RefObject<HTMLButtonElement | null>,
  panelRef: React.RefObject<HTMLDivElement | null>,
  estimatedHeight: number,
  estimatedWidth = 220,
) {
  const [pos, setPos] = useState<DropdownPosition>({
    left: 0,
    top: 0,
    minWidth: 190,
    maxHeight: 280,
    placement: "bottom",
  });

  const updatePosition = useCallback(() => {
    const btn = triggerRef.current;
    if (!btn) return;
    const rect = btn.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const margin = 8;
    const gap = 4;
    const panelEl = panelRef.current;
    const minWidth = Math.max(190, Math.min(280, Math.round(rect.width + 24)));
    const measuredW = panelEl ? panelEl.offsetWidth : Math.max(minWidth, estimatedWidth);
    const measuredH = panelEl ? Math.min(panelEl.scrollHeight, 340) : estimatedHeight;
    const spaceBelow = Math.max(0, vh - rect.bottom - margin - gap);
    const spaceAbove = Math.max(0, rect.top - margin - gap);
    const openUpward = spaceBelow < measuredH && spaceAbove > spaceBelow;
    const maxHeight = Math.max(120, Math.min(340, openUpward ? spaceAbove : spaceBelow));
    const left = Math.max(
      margin,
      Math.min(rect.left, vw - Math.max(minWidth, measuredW) - margin),
    );
    setPos(
      openUpward
        ? {
            left,
            bottom: vh - rect.top + gap,
            minWidth,
            maxHeight,
            placement: "top",
          }
        : {
            left,
            top: rect.bottom + gap,
            minWidth,
            maxHeight,
            placement: "bottom",
          },
    );
  }, [triggerRef, panelRef, estimatedHeight, estimatedWidth]);

  useLayoutEffect(() => {
    if (!open) return;
    updatePosition();
    const raf = window.requestAnimationFrame(updatePosition);
    return () => window.cancelAnimationFrame(raf);
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;
    const onDocClick = (e: MouseEvent) => {
      const target = e.target as Node;
      if (
        rootRef.current?.contains(target) ||
        panelRef.current?.contains(target)
      ) {
        return;
      }
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onViewportChange = () => updatePosition();
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onViewportChange);
    window.addEventListener("scroll", onViewportChange, true);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onViewportChange);
      window.removeEventListener("scroll", onViewportChange, true);
    };
  }, [open, setOpen, rootRef, panelRef, updatePosition]);

  return pos;
}

export const hours = (n: number | null | undefined): string => {
  if (typeof n !== "number" || isNaN(n) || n < 0) return "0";
  return Number((n / 60).toFixed(2)).toString();
};

export const STATUS_MAP: Record<string, { label: string; dotClass: string }> = {
  requested: { label: "待办的", dotClass: "dot-requested" },
  attempted: { label: "进行中", dotClass: "dot-attempted" },
  delivered: { label: "已完成", dotClass: "dot-delivered" },
  verified: { label: "已完成", dotClass: "dot-delivered" },
  changed: { label: "已修改", dotClass: "dot-changed" },
  rejected: { label: "已拒绝", dotClass: "dot-rejected" },
};

export const STATUS_LIST = [
  "requested",
  "attempted",
  "delivered",
  "rejected",
];

let cachedProjects: string[] | null = null;
let projectFetchPromise: Promise<string[]> | null = null;

async function fetchProjects(): Promise<string[]> {
  if (cachedProjects) return cachedProjects;
  if (!projectFetchPromise) {
    projectFetchPromise = api<{ projects?: string[] }>("/manual-options")
      .then((res) => {
        const list = res.projects || [];
        cachedProjects = list;
        return list;
      })
      .catch(() => [])
      .finally(() => {
        projectFetchPromise = null;
      });
  }
  return projectFetchPromise;
}

// -------------------------------------------------------------
// 1. 项目便捷选择
// -------------------------------------------------------------
export function ProjectQuickSelect({
  task,
  projects: propProjects,
  onUpdate,
}: {
  task: Task;
  projects?: string[];
  onUpdate: (project: string) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [options, setOptions] = useState<string[]>(() => propProjects || cachedProjects || []);
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const pos = useFloatingDropdown(open, setOpen, rootRef, triggerRef, panelRef, 300, 240);

  useEffect(() => {
    if (propProjects && propProjects.length > 0) {
      setOptions(propProjects);
    } else {
      void fetchProjects().then((list) => {
        if (list.length > 0) setOptions(list);
      });
    }
  }, [propProjects]);

  useEffect(() => {
    if (open) {
      setSearch("");
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  const select = async (proj: string) => {
    const val = proj.trim() || "未归属项目";
    if (val === task.project) {
      setOpen(false);
      return;
    }
    setSaving(true);
    try {
      await onUpdate(val);
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const allProjects = Array.from(
    new Set(["未归属项目", ...options, ...(task.project ? [task.project] : [])]),
  ).filter(Boolean);

  const filtered = search.trim()
    ? allProjects.filter((p) => p.toLowerCase().includes(search.trim().toLowerCase()))
    : allProjects;

  const showCustomOption =
    search.trim() &&
    !allProjects.some((p) => p.toLowerCase() === search.trim().toLowerCase());

  return (
    <div className="quick-select-wrapper" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className="quick-project-btn"
        title="点击快捷切换项目"
        onClick={() => setOpen((prev) => !prev)}
        disabled={saving}
        aria-expanded={open}
      >
        <span className="project-name-text">{task.project || "未归属项目"}</span>
        <ChevronDown size={13} className={`quick-chevron ${open ? "open" : ""}`} />
      </button>

      {open &&
        createPortal(
          <div
            ref={panelRef}
            className={`quick-dropdown-panel quick-project-dropdown placement-${pos.placement}`}
            style={{
              left: pos.left,
              top: pos.top,
              bottom: pos.bottom,
              minWidth: pos.minWidth,
              maxHeight: pos.maxHeight,
            }}
          >
            <div className="quick-dropdown-header">
              <span className="quick-dropdown-title">选择所属项目</span>
            </div>
            <div className="quick-search-box">
              <input
                ref={inputRef}
                type="text"
                value={search}
                placeholder="搜索或输入新项目..."
                maxLength={200}
                onChange={(e) => setSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && search.trim()) {
                    e.preventDefault();
                    void select(search.trim());
                  }
                }}
              />
            </div>
            <div className="quick-dropdown-list">
              {showCustomOption && (
                <button
                  type="button"
                  className="quick-dropdown-item custom-create"
                  onClick={() => void select(search.trim())}
                >
                  <Plus size={14} />
                  <span>使用新项目：<strong>{search.trim()}</strong></span>
                </button>
              )}
              {filtered.map((p) => {
                const isSelected = (task.project || "未归属项目") === p;
                return (
                  <button
                    key={p}
                    type="button"
                    className={`quick-dropdown-item ${isSelected ? "selected" : ""}`}
                    onClick={() => void select(p)}
                  >
                    <span className="item-text">{p}</span>
                    {isSelected && <Check size={14} className="item-check" />}
                  </button>
                );
              })}
              {filtered.length === 0 && !showCustomOption && (
                <div className="quick-dropdown-empty">暂无匹配项目</div>
              )}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

// -------------------------------------------------------------
// 2. 完成情况便捷选择
// -------------------------------------------------------------
export function StatusQuickSelect({
  task,
  todayStr,
  onUpdate,
}: {
  task: Task;
  todayStr: string;
  onUpdate: (status: string, extra?: { date?: string }) => Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const pos = useFloatingDropdown(open, setOpen, rootRef, triggerRef, panelRef, 220, 190);

  const select = async (statusKey: string) => {
    if (statusKey === task.status) {
      setOpen(false);
      return;
    }
    setSaving(true);
    try {
      const extra =
        statusKey === "delivered" && task.status === "requested"
          ? { date: todayStr }
          : undefined;
      await onUpdate(statusKey, extra);
      setOpen(false);
    } finally {
      setSaving(false);
    }
  };

  const currentInfo = STATUS_MAP[task.status] || {
    label: task.status,
    dotClass: "dot-default",
  };

  return (
    <div className="quick-select-wrapper" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        className={`quick-status-btn state ${task.status}`}
        title="点击快捷切换完成情况"
        onClick={() => setOpen((prev) => !prev)}
        disabled={saving}
        aria-expanded={open}
      >
        <span className={`status-dot ${currentInfo.dotClass}`} />
        <span className="status-label">{currentInfo.label}</span>
        <ChevronDown size={13} className={`quick-chevron ${open ? "open" : ""}`} />
      </button>

      {open &&
        createPortal(
          <div
            ref={panelRef}
            className={`quick-dropdown-panel quick-status-dropdown placement-${pos.placement}`}
            style={{
              left: pos.left,
              top: pos.top,
              bottom: pos.bottom,
              minWidth: pos.minWidth,
              maxHeight: pos.maxHeight,
            }}
          >
            <div className="quick-dropdown-header">
              <span className="quick-dropdown-title">选择完成情况</span>
            </div>
            <div className="quick-dropdown-list">
              {STATUS_LIST.map((key) => {
                const info = STATUS_MAP[key];
                const isSelected = task.status === key;
                return (
                  <button
                    key={key}
                    type="button"
                    className={`quick-dropdown-item ${isSelected ? "selected" : ""}`}
                    onClick={() => void select(key)}
                  >
                    <span className={`status-dot ${info.dotClass}`} />
                    <span className="item-text">{info.label}</span>
                    {isSelected && <Check size={14} className="item-check" />}
                  </button>
                );
              })}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
}

// -------------------------------------------------------------
// 3. 工时登记（完成工时即预计工时）
// -------------------------------------------------------------
export const HOUR_TEMPLATES = [
  { label: "0.5h", minutes: 30 },
  { label: "1h", minutes: 60 },
  { label: "1.5h", minutes: 90 },
  { label: "2h", minutes: 120 },
  { label: "3h", minutes: 180 },
  { label: "4h", minutes: 240 },
  { label: "8h", minutes: 480 },
] as const;

export function HoursQuickRegister({
  task,
  step = 15,
  onUpdate,
}: {
  task: Task;
  step?: number;
  onUpdate: (
    confirmedMinutes: number,
    suggestedMinutes: number | null,
    timeLocked: boolean,
  ) => Promise<void>;
}) {
  const [modalOpen, setModalOpen] = useState(false);

  return (
    <div className="quick-hours-cell">
      <div className="quick-hours-meta">
        <span className="tag">
          {task.time_locked
            ? "固定工时"
            : task.time_source === "ai_estimate"
              ? "AI 分配"
              : "待分配"}
        </span>
        <button
          type="button"
          className="quick-hours-entry-btn"
          title="点击登记完成工时（即预计工时）"
          onClick={() => setModalOpen(true)}
        >
          <Clock3 size={13} />
          <span>工时登记</span>
        </button>
      </div>

      <div
        className="quick-hours-display"
        onClick={() => setModalOpen(true)}
        title="点击快速登记工时"
      >
        <div className="quick-hours-primary">
          <span className="hours-kind-label">完成</span>
          <strong title={task.time_reason}>
            {hours(task.confirmed_minutes)} <small>h</small>
          </strong>
        </div>
      </div>

      {modalOpen && (
        <HoursRegisterModal
          task={task}
          step={step}
          onClose={() => setModalOpen(false)}
          onSave={async (confirmed, suggested, locked) => {
            await onUpdate(confirmed, suggested, locked);
            setModalOpen(false);
          }}
        />
      )}
    </div>
  );
}

// -------------------------------------------------------------
// 工时登记专用弹窗
// -------------------------------------------------------------
function HoursRegisterModal({
  task,
  step = 15,
  onClose,
  onSave,
}: {
  task: Task;
  step: number;
  onClose: () => void;
  onSave: (
    confirmedMinutes: number,
    suggestedMinutes: number | null,
    timeLocked: boolean,
  ) => Promise<void>;
}) {
  const [confirmed, setConfirmed] = useState<number>(task.confirmed_minutes || 0);
  const [confirmedInput, setConfirmedInput] = useState<string>(() =>
    hours(task.confirmed_minutes || 0),
  );
  const [locked, setLocked] = useState<boolean>(
    task.time_locked || Boolean(task.confirmed_minutes),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const applyConfirmedMinutes = (nextMins: number) => {
    const clamped = Math.min(1440, Math.max(0, Math.round(nextMins)));
    setConfirmed(clamped);
    setConfirmedInput(hours(clamped));
    setLocked(clamped > 0);
  };

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    const cNum = Number(confirmedInput.trim());
    if (confirmedInput.trim() === "" || !Number.isFinite(cNum) || cNum < 0 || cNum > 24) {
      setError("完成工时请输入 0 至 24 之间的小时数");
      return;
    }
    const finalConfirmed = Math.min(1440, Math.max(0, Math.round(cNum * 60)));
    const finalSuggested = finalConfirmed > 0 ? finalConfirmed : null;
    setSaving(true);
    try {
      await onSave(finalConfirmed, finalSuggested, locked);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="modal-overlay" role="presentation">
      <div
        className="modal hours-register-modal"
        role="dialog"
        aria-modal="true"
        aria-label="工时登记"
      >
        <div className="modal-head">
          <div>
            <h2>工时登记</h2>
            <p className="task-title-sub">
              {task.title} · {task.project || "未归属项目"}
            </p>
          </div>
          <button
            type="button"
            aria-label="关闭工时登记"
            onClick={onClose}
            disabled={saving}
          >
            <X size={20} />
          </button>
        </div>

        <form onSubmit={handleSave} className="hours-register-form">
          <div className="hours-tip-box">
            按<strong>小时</strong>登记<strong>完成工时</strong>，保存后与 Gitee 工时自动保持一致。
          </div>

          {/* 完成工时 */}
          <div className="hours-input-group">
            <div className="hours-group-header">
              <label htmlFor="confirmed-hours-input">
                <strong>完成工时</strong>
                <span className="hours-sub-label">（单位：小时）</span>
              </label>
              <div className="hours-converted">
                = {confirmed} <small>分钟</small>
              </div>
            </div>
            <div className="minute-input">
              <button
                type="button"
                aria-label="减少完成工时"
                onClick={() => applyConfirmedMinutes(confirmed - step)}
              >
                −
              </button>
              <input
                id="confirmed-hours-input"
                type="number"
                min="0"
                max="24"
                step="any"
                required
                value={confirmedInput}
                onChange={(e) => {
                  const raw = e.target.value;
                  setConfirmedInput(raw);
                  if (raw.trim() === "") return;
                  const num = Number(raw);
                  if (!Number.isFinite(num) || num < 0 || num > 24) return;
                  const mins = Math.min(1440, Math.max(0, Math.round(num * 60)));
                  setConfirmed(mins);
                  setLocked(mins > 0);
                }}
              />
              <span className="unit-label">小时</span>
              <button
                type="button"
                aria-label="增加完成工时"
                onClick={() => applyConfirmedMinutes(confirmed + step)}
              >
                +
              </button>
            </div>
            <div className="hours-quick-chips" role="group" aria-label="工时模板">
              <span className="chips-label">工时模板：</span>
              {HOUR_TEMPLATES.map((p) => (
                <button
                  key={p.minutes}
                  type="button"
                  className={`chip ${confirmed === p.minutes ? "active" : ""}`}
                  onClick={() => applyConfirmedMinutes(p.minutes)}
                >
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                className={`chip reset ${confirmed === 0 ? "active" : ""}`}
                onClick={() => applyConfirmedMinutes(0)}
              >
                0h
              </button>
            </div>
          </div>

          {/* 固定工时 */}
          <label className="check-label hours-lock-label">
            <input
              type="checkbox"
              checked={locked}
              onChange={(e) => setLocked(e.target.checked)}
            />
            <span>固定此项工时，后续 AI 分配与重新计算时保留不被覆盖</span>
          </label>

          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}

          <div className="modal-footer">
            <button
              className="outline"
              type="button"
              disabled={saving}
              onClick={onClose}
            >
              取消
            </button>
            <button className="primary" type="submit" disabled={saving}>
              {saving ? "保存中…" : "保存工时"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
