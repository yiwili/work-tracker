import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

export type TodoItem = {
  id: string;
  date: string;
  title: string;
  details: string;
  project: string;
  category: string;
  status: string;
  origin: string;
  confirmed_minutes: number;
  time_locked: boolean;
};

export type TodoDraft = {
  title: string;
  project?: string;
  category?: string;
  details?: string;
  date?: string;
};

export type UseTodosOptions = {
  /** 获取当天日期（YYYY-MM-DD）；将待办设为已完成时自动归入当天 */
  getToday: () => string;
  /** 本地变更或远端拉取后触发的回调（例如刷新首页日报） */
  onChanged?: () => void | Promise<void>;
};

export type UseTodosResult = {
  todos: TodoItem[];
  count: number;
  loading: boolean;
  syncing: boolean;
  error: string;
  projects: string[];
  reload: () => Promise<void>;
  syncFromCloud: () => Promise<void>;
  addTodo: (draft: TodoDraft) => Promise<void>;
  completeTodo: (id: string) => Promise<void>;
  updateTodo: (
    id: string,
    patch: Partial<Pick<TodoItem, "title" | "project" | "category" | "details" | "date">>,
  ) => Promise<void>;
  deleteTodo: (id: string) => Promise<void>;
  clearError: () => void;
};

/**
 * 待办事项领域逻辑 Hook（与 UI 完全解耦）：
 * - 本地增删改后立即调用 /api/cloud/sync 推送到云端；
 * - 标记“已完成”时自动将状态改为 delivered，并将工作日期设为当天；
 * - 定期从云端同步拉取最新变更，保持多端一致。
 */
export function useTodos({ getToday, onChanged }: UseTodosOptions): UseTodosResult {
  const [todos, setTodos] = useState<TodoItem[]>([]);
  const [projects, setProjects] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState("");
  const onChangedRef = useRef(onChanged);
  onChangedRef.current = onChanged;
  const getTodayRef = useRef(getToday);
  getTodayRef.current = getToday;

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [items, opts] = await Promise.all([
        api<TodoItem[]>("/todos"),
        api<{ projects: string[] }>("/manual-options").catch(() => ({ projects: [] })),
      ]);
      setTodos(items || []);
      setProjects(opts.projects || []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  const pushAndPullCloud = useCallback(async () => {
    setSyncing(true);
    try {
      const res = await api<{ pulled?: number }>("/cloud/sync", "POST");
      if (res && (res.pulled ?? 0) > 0) {
        const items = await api<TodoItem[]>("/todos");
        setTodos(items || []);
        await onChangedRef.current?.();
      }
    } catch {
      // 未绑定云端或离线时不阻断本地待办使用，后台 scheduler 会自动重试
    } finally {
      setSyncing(false);
    }
  }, []);

  useEffect(() => {
    void reload();
    void pushAndPullCloud();
    const timer = window.setInterval(() => {
      void pushAndPullCloud();
    }, 20000);
    return () => window.clearInterval(timer);
  }, [reload, pushAndPullCloud]);

  const afterMutate = async () => {
    await reload();
    await onChangedRef.current?.();
    void pushAndPullCloud();
  };

  const addTodo = useCallback(
    async (draft: TodoDraft) => {
      const title = draft.title.trim();
      if (!title) return;
      setError("");
      await api("/tasks", "POST", {
        date: draft.date || getTodayRef.current(),
        title,
        details: draft.details || "",
        project: (draft.project || "").trim() || "未归属项目",
        category: draft.category || "任务",
        status: "requested",
        confirmed_minutes: 0,
        time_locked: false,
      });
      await afterMutate();
    },
    [reload, pushAndPullCloud],
  );

  const completeTodo = useCallback(
    async (id: string) => {
      setError("");
      // 设为已完成时，日期自动选为当天
      await api(`/tasks/${id}`, "PATCH", {
        status: "delivered",
        date: getTodayRef.current(),
      });
      await afterMutate();
    },
    [reload, pushAndPullCloud],
  );

  const updateTodo = useCallback(
    async (
      id: string,
      patch: Partial<Pick<TodoItem, "title" | "project" | "category" | "details" | "date">>,
    ) => {
      setError("");
      await api(`/tasks/${id}`, "PATCH", patch);
      await afterMutate();
    },
    [reload, pushAndPullCloud],
  );

  const deleteTodo = useCallback(
    async (id: string) => {
      setError("");
      await api(`/tasks/${id}`, "DELETE");
      await afterMutate();
    },
    [reload, pushAndPullCloud],
  );

  return {
    todos,
    count: todos.length,
    loading,
    syncing,
    error,
    projects,
    reload,
    syncFromCloud: pushAndPullCloud,
    addTodo,
    completeTodo,
    updateTodo,
    deleteTodo,
    clearError: () => setError(""),
  };
}
