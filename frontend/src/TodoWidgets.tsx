import React, { useState } from "react";
import {
  BellRing,
  Check,
  ChevronRight,
  Cloud,
  ListTodo,
  LoaderCircle,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";
import type { TodoItem, UseTodosResult } from "./useTodos";

const TODO_CATEGORIES = ["需求", "任务", "缺陷"] as const;

/**
 * 视图层组件（只消费 UseTodosResult，不包含数据与同步逻辑，便于随时替换 UI 设计）：
 * 1. TodoReminderBar：嵌入首页顶部的高醒目待办提醒卡片，支持一键完成、快速添加与展开抽屉；
 * 2. TodoSideDock：右侧常驻悬浮标签 + 滑出抽屉，任意页面均可随时查看与管理待办。
 */
export function TodoReminderBar({
  model,
  onOpenDrawer,
  onEditItem,
}: {
  model: UseTodosResult;
  onOpenDrawer: () => void;
  onEditItem?: (item: TodoItem) => void;
}) {
  const [title, setTitle] = useState("");
  const [project, setProject] = useState("");
  const [category, setCategory] = useState<string>("任务");
  const [busyId, setBusyId] = useState<string | null>(null);

  const submitQuick = async (e: React.FormEvent) => {
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
    <section
      className={"todo-reminder-card" + (model.count > 0 ? " has-pending" : " all-clear")}
      aria-label="待办事项提醒"
    >
      <div className="todo-reminder-head">
        <div className="todo-reminder-title">
          <span className={"todo-pulse-badge" + (model.count > 0 ? " active" : "")}>
            <BellRing size={16} />
          </span>
          <div>
            <div className="todo-headline-row">
              <strong>待办提醒（To-Do）</strong>
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
              {model.count > 0
                ? "勾选完成会自动将日期设为今天并计入今日工作清单"
                : "记录需要做的事项，多端实时同步，不错过重要安排"}
            </p>
          </div>
        </div>
        <div className="todo-reminder-actions">
          <button
            type="button"
            className="outline todo-drawer-trigger"
            onClick={onOpenDrawer}
          >
            <ListTodo size={15} />
            全部待办 ({model.count})
            <ChevronRight size={14} />
          </button>
        </div>
      </div>

      <form className="todo-quick-form" onSubmit={submitQuick}>
        <input
          className="todo-quick-input"
          value={title}
          maxLength={500}
          placeholder="添加新的待办事项，回车立即保存并同步云端…"
          onChange={(e) => setTitle(e.target.value)}
        />
        <input
          className="todo-quick-project"
          list="todo-projects-bar"
          value={project}
          placeholder="所属项目（可选）"
          onChange={(e) => setProject(e.target.value)}
        />
        <datalist id="todo-projects-bar">
          {model.projects.map((p) => (
            <option key={p} value={p} />
          ))}
        </datalist>
        <select
          className="todo-quick-category"
          value={category}
          aria-label="待办分类"
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
          添加待办
        </button>
      </form>

      {model.error && (
        <div className="todo-inline-error" role="alert">
          <span>{model.error}</span>
          <button type="button" onClick={model.clearError} aria-label="关闭待办错误">
            <X size={14} />
          </button>
        </div>
      )}

      {model.count > 0 && (
        <ul className="todo-inline-list">
          {model.todos.slice(0, 5).map((item) => (
            <li key={item.id} className="todo-inline-item">
              <button
                type="button"
                className="todo-check-btn"
                disabled={busyId === item.id}
                title="标记为已完成（工作日期自动设为今天）"
                onClick={() => void handleComplete(item)}
              >
                {busyId === item.id ? (
                  <LoaderCircle size={14} className="spin" />
                ) : (
                  <Check size={14} />
                )}
              </button>
              <button
                type="button"
                className="todo-item-main"
                onClick={() => (onEditItem ? onEditItem(item) : onOpenDrawer())}
              >
                <span className="todo-item-title">{item.title}</span>
                <span className="todo-item-meta">
                  {item.project && item.project !== "未归属项目" && (
                    <span className="todo-tag project">{item.project}</span>
                  )}
                  <span className="todo-tag category">{item.category || "任务"}</span>
                  <span className="todo-tag date">创建于 {item.date}</span>
                </span>
              </button>
              <button
                type="button"
                className="todo-complete-pill"
                disabled={busyId === item.id}
                onClick={() => void handleComplete(item)}
              >
                完成并归入今日
              </button>
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
          {model.count > 5 && (
            <li className="todo-inline-more">
              <button type="button" className="link-button" onClick={onOpenDrawer}>
                还有 {model.count - 5} 项待办未展示，点击打开右侧待办面板查看全部 →
              </button>
            </li>
          )}
        </ul>
      )}
    </section>
  );
}

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
      <button
        type="button"
        className={
          "todo-side-tab" +
          (model.count > 0 ? " attention" : "") +
          (open ? " open" : "")
        }
        onClick={() => onToggle(!open)}
        aria-label="打开或关闭右侧待办清单"
        title="待办清单（点击展开）"
      >
        <BellRing size={16} className={model.count > 0 ? "ring-anim" : ""} />
        <span className="todo-side-tab-text">待办事项</span>
        <span className="todo-side-tab-badge">{model.count}</span>
      </button>

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
        <div className="todo-drawer-header">
          <div className="todo-drawer-title">
            <ListTodo size={18} />
            <strong>待办清单</strong>
            <span className="todo-count-pill warn">{model.count}</span>
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

        <p className="todo-drawer-hint">
          本地添加、修改或完成都会立即同步到云端；勾选完成时工作日期会自动设为今天。
        </p>

        <form className="todo-drawer-form" onSubmit={handleAdd}>
          <input
            value={title}
            maxLength={500}
            placeholder="输入要做的事，回车添加…"
            onChange={(e) => setTitle(e.target.value)}
          />
          <div className="todo-drawer-form-row">
            <input
              list="todo-projects-drawer"
              value={project}
              placeholder="所属项目（可选）"
              onChange={(e) => setProject(e.target.value)}
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
              <Plus size={15} /> 添加
            </button>
          </div>
        </form>

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
                    onClick={() => onEditItem?.(item)}
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
