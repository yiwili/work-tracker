import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  BellRing,
  Check,
  Cloud,
  LoaderCircle,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import {
  hasBracketPrefix,
  formatTitleWithPrefix,
  type TodoItem,
  type UseTodosResult,
} from "./useTodos";

const TODO_CATEGORIES = ["需求", "任务", "缺陷"] as const;

type DropMode = "free" | "left" | "right" | "top" | "bottom";

const FREE_SIZE = { w: 84, h: 104 };
const SIDE_SIZE = { w: 52, h: 128 };
const TB_SIZE = { w: 152, h: 46 };
// 只有真正贴死屏幕边缘才切换贴边形态，其余位置保持自由水滴
const SNAP_THRESHOLD = 4;
const TENSION_THRESHOLD = 12;
const LONG_PRESS_MS = 180;
const MOVE_CANCEL = 6;
const DROP_STORAGE_KEY = "awt-todo-drop";

const clamp = (v: number, min: number, max: number) =>
  Math.min(max, Math.max(min, v));

const sizeFor = (mode: DropMode) =>
  mode === "left" || mode === "right"
    ? SIDE_SIZE
    : mode === "top" || mode === "bottom"
      ? TB_SIZE
      : FREE_SIZE;

function loadDropState(): { x: number; y: number; mode: DropMode } | null {
  try {
    const raw = window.localStorage.getItem(DROP_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { x?: unknown; y?: unknown; mode?: unknown };
    if (typeof parsed.x !== "number" || typeof parsed.y !== "number") return null;
    const modes: DropMode[] = ["free", "left", "right", "top", "bottom"];
    const mode = modes.includes(parsed.mode as DropMode)
      ? (parsed.mode as DropMode)
      : "right";
    return { x: parsed.x, y: parsed.y, mode };
  } catch {
    return null;
  }
}

/**
 * 待办水滴：长按 180ms 后可自由拖到任意位置，贴死屏幕边缘才吸附成胶囊；
 * 松手带弹簧回弹，靠近边缘时有拉伸张力与引力波纹。
 * 单击（未拖动）打开右侧待办抽屉，抽屉内含提醒、添加、完成归入今日与删除。
 */
export function TodoSideDock({
  model,
  open,
  onToggle,
  onEditItem,
}: {
  model: UseTodosResult;
  open: boolean;
  onToggle: (next: boolean) => void;
  onEditItem?: (item: TodoItem) => void;
}) {
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");
  const [category, setCategory] = useState<string>("任务");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [mode, setMode] = useState<DropMode>(() => loadDropState()?.mode ?? "right");
  const [dragging, setDragging] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const dropRef = useRef<HTMLButtonElement>(null);
  const auraRef = useRef<HTMLDivElement>(null);
  const posRef = useRef({ x: 0, y: 0 });
  const scaleRef = useRef({ x: 1, y: 1 });
  const dragVelRef = useRef({ x: 0, y: 0 });
  const modeRef = useRef<DropMode>(mode);
  const draggingRef = useRef(false);
  const pointerIdRef = useRef<number | null>(null);
  const pressRef = useRef({ x: 0, y: 0 });
  const lastRef = useRef({ x: 0, y: 0, t: 0 });
  const longPressRef = useRef<number | null>(null);
  const movedRef = useRef(false);
  const rafRef = useRef(0);

  const renderFrame = () => {
    if (containerRef.current) {
      containerRef.current.style.transform = `translate3d(${posRef.current.x}px, ${posRef.current.y}px, 0)`;
    }
    if (dropRef.current) {
      dropRef.current.style.transform = `translate(-50%, -50%) scale(${scaleRef.current.x.toFixed(4)}, ${scaleRef.current.y.toFixed(4)})`;
    }
  };

  const applyMode = (next: DropMode) => {
    if (modeRef.current === next) return;
    modeRef.current = next;
    setMode(next);
  };

  const saveState = () => {
    try {
      window.localStorage.setItem(
        DROP_STORAGE_KEY,
        JSON.stringify({ ...posRef.current, mode: modeRef.current }),
      );
    } catch {
      // 无痕模式等场景下忽略存储失败
    }
  };

  const snapTo = (target: DropMode) => {
    const { w, h } = sizeFor(target);
    const W = window.innerWidth;
    const H = window.innerHeight;
    const p = posRef.current;
    if (target === "left") p.x = w / 2;
    if (target === "right") p.x = W - w / 2;
    if (target === "top") p.y = h / 2;
    if (target === "bottom") p.y = H - h / 2;
    p.x = clamp(p.x, w / 2, W - w / 2);
    p.y = clamp(p.y, h / 2, H - h / 2);
    renderFrame();
  };

  const clampFree = () => {
    posRef.current.x = clamp(
      posRef.current.x,
      FREE_SIZE.w / 2 + 4,
      window.innerWidth - FREE_SIZE.w / 2 - 4,
    );
    posRef.current.y = clamp(
      posRef.current.y,
      FREE_SIZE.h / 2 + 4,
      window.innerHeight - FREE_SIZE.h / 2 - 4,
    );
  };

  const detectEdge = (x: number, y: number): DropMode => {
    const halfW = FREE_SIZE.w / 2;
    const halfH = FREE_SIZE.h / 2;
    const dLeft = x - halfW;
    const dRight = window.innerWidth - (x + halfW);
    const dTop = y - halfH;
    const dBottom = window.innerHeight - (y + halfH);
    const min = Math.min(dLeft, dRight, dTop, dBottom);
    if (min > SNAP_THRESHOLD) return "free";
    if (min === dLeft) return "left";
    if (min === dRight) return "right";
    if (min === dTop) return "top";
    return "bottom";
  };

  const updateAura = (edge: DropMode) => {
    const aura = auraRef.current;
    if (!aura) return;
    if (edge === "free") {
      aura.classList.remove("active");
      return;
    }
    const W = window.innerWidth;
    const H = window.innerHeight;
    const { x, y } = posRef.current;
    aura.classList.add("active");
    if (edge === "left") {
      aura.style.cssText = `left:0;top:${y - 72}px;width:14px;height:144px;border-radius:0 20px 20px 0;`;
    } else if (edge === "right") {
      aura.style.cssText = `left:${W - 14}px;top:${y - 72}px;width:14px;height:144px;border-radius:20px 0 0 20px;`;
    } else if (edge === "top") {
      aura.style.cssText = `left:${x - 72}px;top:0;width:144px;height:14px;border-radius:0 0 20px 20px;`;
    } else {
      aura.style.cssText = `left:${x - 72}px;top:${H - 14}px;width:144px;height:14px;border-radius:20px 20px 0 0;`;
    }
  };

  // 靠近边缘时沿着贴边方向拉长，产生液滴被吸住的张力
  const edgeTension = (x: number, y: number) => {
    const halfW = FREE_SIZE.w / 2;
    const halfH = FREE_SIZE.h / 2;
    const dLeft = x - halfW;
    const dRight = window.innerWidth - (x + halfW);
    const dTop = y - halfH;
    const dBottom = window.innerHeight - (y + halfH);
    const min = Math.min(dLeft, dRight, dTop, dBottom);
    const gap = Math.max(0, min);
    if (gap >= TENSION_THRESHOLD) {
      updateAura("free");
      return { x: 0, y: 0 };
    }
    const t = clamp((TENSION_THRESHOLD - gap) / TENSION_THRESHOLD, 0, 1);
    const intensity = Math.pow(t, 3.5);
    if (intensity < 0.02) {
      updateAura("free");
      return { x: 0, y: 0 };
    }
    updateAura(detectEdge(x, y));
    if (min === dLeft || min === dRight) {
      return { x: -intensity * 0.16, y: intensity * 0.22 };
    }
    return { x: intensity * 0.24, y: -intensity * 0.16 };
  };

  const updateDragVisual = () => {
    const vx = dragVelRef.current.x;
    const vy = dragVelRef.current.y;
    const speed = Math.hypot(vx, vy);
    const stretch = clamp(speed / 60, 0, 0.3);
    const cos2 = Math.cos(Math.atan2(vy, vx) * 2);
    const tension = edgeTension(posRef.current.x, posRef.current.y);
    scaleRef.current = {
      x: (1 + stretch * cos2) * 1.05 + tension.x,
      y: (1 - stretch * cos2) * 1.05 + tension.y,
    };
    renderFrame();
  };

  const startSpring = (vx: number, vy: number, edge: DropMode | null) => {
    cancelAnimationFrame(rafRef.current);
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      scaleRef.current = { x: 1, y: 1 };
      renderFrame();
      return;
    }
    let impulse = clamp(Math.hypot(vx, vy) / 35, 0, 0.42);
    let vxSpring: number;
    let vySpring: number;
    if (edge) {
      impulse = Math.max(impulse, 0.32);
      if (edge === "left" || edge === "right") {
        vxSpring = -impulse * 0.9;
        vySpring = impulse * 0.7;
      } else {
        vxSpring = impulse * 0.8;
        vySpring = -impulse * 0.9;
      }
    } else {
      vxSpring = impulse;
      vySpring = -impulse * 0.75;
    }
    const step = () => {
      const s = scaleRef.current;
      vxSpring = (vxSpring + (1 - s.x) * 0.22) * 0.68;
      s.x += vxSpring;
      vySpring = (vySpring + (1 - s.y) * 0.22) * 0.68;
      s.y += vySpring;
      renderFrame();
      if (
        Math.abs(vxSpring) > 0.0008 ||
        Math.abs(s.x - 1) > 0.0008 ||
        Math.abs(vySpring) > 0.0008 ||
        Math.abs(s.y - 1) > 0.0008
      ) {
        rafRef.current = requestAnimationFrame(step);
      } else {
        scaleRef.current = { x: 1, y: 1 };
        renderFrame();
      }
    };
    step();
  };

  const clearLongPress = () => {
    if (longPressRef.current !== null) {
      window.clearTimeout(longPressRef.current);
      longPressRef.current = null;
    }
  };

  useLayoutEffect(() => {
    const saved = loadDropState();
    const W = window.innerWidth;
    const H = window.innerHeight;
    posRef.current = saved
      ? { x: saved.x, y: saved.y }
      : { x: W - SIDE_SIZE.w / 2, y: H * 0.45 };
    const initialMode = saved?.mode ?? "right";
    modeRef.current = initialMode;
    setMode(initialMode);
    if (initialMode === "free") {
      clampFree();
      renderFrame();
    } else {
      snapTo(initialMode);
    }
  }, []);

  useEffect(() => {
    const onResize = () => {
      if (modeRef.current === "free") {
        clampFree();
        renderFrame();
      } else {
        snapTo(modeRef.current);
      }
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  useEffect(
    () => () => {
      clearLongPress();
      cancelAnimationFrame(rafRef.current);
    },
    [],
  );

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onToggle(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onToggle]);

  const handlePointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (pointerIdRef.current !== null) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    pointerIdRef.current = e.pointerId;
    try {
      e.currentTarget.setPointerCapture(e.pointerId);
    } catch {
      // 忽略不支持指针捕获的环境
    }
    pressRef.current = { x: e.clientX, y: e.clientY };
    lastRef.current = { x: e.clientX, y: e.clientY, t: performance.now() };
    dragVelRef.current = { x: 0, y: 0 };
    movedRef.current = false;
    clearLongPress();
    longPressRef.current = window.setTimeout(() => {
      longPressRef.current = null;
      draggingRef.current = true;
      setDragging(true);
      cancelAnimationFrame(rafRef.current);
      scaleRef.current = { x: 1.05, y: 1.05 };
      renderFrame();
      if (navigator.vibrate) navigator.vibrate(16);
    }, LONG_PRESS_MS);
  };

  const handlePointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (pointerIdRef.current !== e.pointerId) return;
    const dx = e.clientX - lastRef.current.x;
    const dy = e.clientY - lastRef.current.y;

    if (!draggingRef.current) {
      if (
        Math.hypot(e.clientX - pressRef.current.x, e.clientY - pressRef.current.y) >
        MOVE_CANCEL
      ) {
        clearLongPress();
        movedRef.current = true;
      }
      return;
    }

    const now = performance.now();
    const dt = Math.max(now - lastRef.current.t, 1);
    dragVelRef.current = {
      x: dragVelRef.current.x * 0.55 + (dx / dt) * 16 * 0.45,
      y: dragVelRef.current.y * 0.55 + (dy / dt) * 16 * 0.45,
    };
    posRef.current.x = clamp(
      posRef.current.x + dx,
      10,
      window.innerWidth - 10,
    );
    posRef.current.y = clamp(
      posRef.current.y + dy,
      10,
      window.innerHeight - 10,
    );
    lastRef.current = { x: e.clientX, y: e.clientY, t: now };
    applyMode(detectEdge(posRef.current.x, posRef.current.y));
    updateDragVisual();
  };

  const handlePointerUp = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (pointerIdRef.current !== e.pointerId) return;
    clearLongPress();
    const wasDragging = draggingRef.current;
    draggingRef.current = false;
    setDragging(false);
    if (wasDragging) {
      const target = detectEdge(posRef.current.x, posRef.current.y);
      applyMode(target);
      snapTo(target);
      startSpring(dragVelRef.current.x, dragVelRef.current.y, target === "free" ? null : target);
      dragVelRef.current = { x: 0, y: 0 };
      auraRef.current?.classList.remove("active");
      saveState();
    } else if (!movedRef.current) {
      onToggle(true);
    }
    try {
      e.currentTarget.releasePointerCapture(e.pointerId);
    } catch {
      // 忽略释放失败
    }
    pointerIdRef.current = null;
  };

  const handleAdd = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    setBusyId("__add__");
    try {
      await model.addTodo({ title, project, category });
      setTitle("");
    } finally {
      setBusyId(null);
    }
  };

  const handleComplete = async (item: TodoItem) => {
    setBusyId(item.id);
    try {
      await model.completeTodo(item.id);
    } finally {
      setBusyId(null);
    }
  };

  return (
    <>
      <div ref={auraRef} className="todo-drop-aura" aria-hidden="true" />
      <div ref={containerRef} className="todo-drop-layer">
        <button
          ref={dropRef}
          type="button"
          className={
            "todo-drop mode-" +
            mode +
            (mode !== "free" ? " docked" : "") +
            (dragging ? " dragging" : "") +
            (model.count > 0 ? " has-pending" : " all-clear") +
            (open ? " drawer-open" : "")
          }
          aria-label={
            "待办事项，共 " +
            model.count +
            " 项待处理。长按可拖动，单击打开待办清单"
          }
          title="长按拖动，单击打开待办清单"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerUp}
          onContextMenu={(e) => e.preventDefault()}
          onClick={(e) => {
            if (e.detail === 0) onToggle(true);
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              onToggle(true);
            }
          }}
        >
          <span className="todo-drop-seal" aria-hidden="true" />
          <span className="todo-drop-content drop-content-free">
            <span className="todo-drop-icon-row">
              <BellRing size={16} />
              <span className="todo-drop-badge">{model.count}</span>
            </span>
            <span className="todo-drop-word">待办</span>
            <span className="todo-drop-word">事项</span>
          </span>
          <span className="todo-drop-content drop-content-side">
            <span className="todo-drop-icon-row">
              <BellRing size={15} />
              <span className="todo-drop-badge">{model.count}</span>
            </span>
            <span className="todo-drop-side-word">待办事项</span>
          </span>
          <span className="todo-drop-content drop-content-tb">
            <BellRing size={15} />
            <span className="todo-drop-tb-word">待办事项</span>
            <span className="todo-drop-badge">{model.count}</span>
          </span>
        </button>
      </div>

      {open && (
        <div
          className="todo-drawer-backdrop"
          onClick={() => onToggle(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={"todo-side-drawer" + (open ? " open" : "")}
        aria-label="待办事项侧边栏"
      >
        <div className="todo-reminder-head todo-drawer-reminder-head">
          <div className="todo-reminder-title">
            <span className={"todo-pulse-badge" + (model.count > 0 ? " active" : "")}>
              <BellRing size={16} />
            </span>
            <div>
              <div className="todo-headline-row">
                <strong>待办提醒</strong>
                <span className={"todo-count-pill" + (model.count > 0 ? " warn" : "")}>
                  {model.count > 0 ? `${model.count} 项待处理` : "全部已完成"}
                </span>
                <span className="todo-sync-hint" title="本地修改后自动实时同步至云端">
                  {model.syncing ? (
                    <>
                      <LoaderCircle size={12} className="spin" /> 云端同步中…
                    </>
                  ) : (
                    <>
                      <Cloud size={12} /> 实时云同步
                    </>
                  )}
                </span>
              </div>
              <p className="todo-subtext">
                勾选完成会自动将日期设为今天并计入今日工作清单
              </p>
            </div>
          </div>
          <div className="todo-drawer-head-actions">
            <button
              type="button"
              className="icon-btn"
              title="立即与云端同步"
              disabled={model.syncing}
              onClick={() => void model.syncFromCloud()}
            >
              <RefreshCw size={15} className={model.syncing ? "spin" : ""} />
            </button>
            <button
              type="button"
              className="icon-btn"
              aria-label="关闭待办侧边栏"
              onClick={() => onToggle(false)}
            >
              <X size={18} />
            </button>
          </div>
        </div>

        <form className="todo-drawer-form" onSubmit={handleAdd}>
          <input
            value={title}
            maxLength={500}
            placeholder="添加新的待办事项，回车立即保存并同步云端…"
            onChange={(e) => setTitle(e.target.value)}
          />
          <div className="todo-drawer-form-row">
            <input
              list="todo-projects-drawer"
              value={project}
              placeholder="所属项目（可选）"
              onChange={(e) => {
                const nextProj = e.target.value;
                setProject(nextProj);
                if (title.trim() && !hasBracketPrefix(title)) {
                  setTitle(
                    formatTitleWithPrefix(
                      title,
                      nextProj,
                      model.projectPrefixes,
                      model.bracketStyle,
                      model.defaultPrefix,
                    ),
                  );
                }
              }}
            />
            <datalist id="todo-projects-drawer">
              {model.projects.map((p) => (
                <option key={p} value={p} />
              ))}
            </datalist>
            <select
              value={category}
              aria-label="待办工作分类"
              onChange={(e) => setCategory(e.target.value)}
            >
              {TODO_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {c}
                </option>
              ))}
            </select>
            <button
              type="submit"
              className="primary"
              disabled={busyId === "__add__" || !title.trim()}
            >
              {busyId === "__add__" ? (
                <LoaderCircle size={15} className="spin" />
              ) : (
                <Plus size={15} />
              )}
              添加
            </button>
          </div>
        </form>

        {model.error && (
          <div className="todo-inline-error" role="alert">
            <span>{model.error}</span>
            <button type="button" onClick={model.clearError} aria-label="关闭待办错误">
              <X size={14} />
            </button>
          </div>
        )}

        <div className="todo-drawer-body">
          {model.todos.length === 0 ? (
            <div className="todo-drawer-empty">
              <Check size={24} />
              <p>当前没有未完成的待办事项</p>
            </div>
          ) : (
            <ul className="todo-drawer-list">
              {model.todos.map((item) => (
                <li key={item.id} className="todo-drawer-item">
                  <button
                    type="button"
                    className="todo-check-btn"
                    disabled={busyId === item.id}
                    title="设为已完成（自动归入今天）"
                    onClick={() => void handleComplete(item)}
                  >
                    {busyId === item.id ? (
                      <LoaderCircle size={14} className="spin" />
                    ) : (
                      <Check size={14} />
                    )}
                  </button>
                  <div
                    className="todo-drawer-item-content"
                    role="button"
                    tabIndex={0}
                    title="点击编辑这条待办"
                    onClick={() => onEditItem?.(item)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onEditItem?.(item);
                      }
                    }}
                  >
                    <div className="todo-item-title">{item.title}</div>
                    {item.details && (
                      <div className="todo-item-details">{item.details}</div>
                    )}
                    <div className="todo-item-meta">
                      {item.project && item.project !== "未归属项目" && (
                        <span className="todo-tag project">{item.project}</span>
                      )}
                      <span className="todo-tag category">{item.category || "任务"}</span>
                      <span className="todo-tag date">{item.date}</span>
                    </div>
                    <span className="todo-drawer-item-actions">
                      <button
                        type="button"
                        className="todo-complete-pill"
                        disabled={busyId === item.id}
                        onClick={(e) => {
                          e.stopPropagation();
                          void handleComplete(item);
                        }}
                      >
                        完成并归入今日
                      </button>
                    </span>
                  </div>
                  <button
                    type="button"
                    className="todo-delete-btn"
                    aria-label={"删除待办 " + item.title}
                    disabled={busyId === item.id}
                    onClick={() => void model.deleteTodo(item.id)}
                  >
                    <Trash2 size={14} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </aside>
    </>
  );
}
