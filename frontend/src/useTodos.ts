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

export const HAS_BRACKET_PREFIX_RE = /^\s*([【\[（(])([^】\]）)]+)([】\]）)])/;

export function hasBracketPrefix(title: string | null | undefined): boolean {
  if (!title) return false;
  const match = HAS_BRACKET_PREFIX_RE.exec(title.trim());
  return Boolean(match && match[2].trim());
}

export function formatTitleWithPrefix(
  rawTitle: string,
  project: string,
  projectPrefixes: Record<string, string>,
  bracketStyle: string = "【】",
  defaultPrefix: string = "其他",
): string {
  const trimmed = (rawTitle || "").trim();
  if (!trimmed) return "";
  if (hasBracketPrefix(trimmed)) {
    return trimmed;
  }
  const openB = bracketStyle[0] || "【";
  const closeB = bracketStyle[1] || "】";
  const proj = (project || "").trim();
  let pfx = "";

  if (proj && proj !== "未归属项目" && proj !== "None") {
    pfx = projectPrefixes[proj] || "";
    if (!pfx) {
      for (const [k, v] of Object.entries(projectPrefixes)) {
        if (k && (proj.includes(k) || k.includes(proj))) {
          pfx = v;
          break;
        }
      }
    }
  }

  if (!pfx) {
    if (!proj || proj === "未归属项目") {
      pfx = defaultPrefix || "其他";
    } else {
      const parts = proj.split("/");
      const clean = parts[parts.length - 1].trim();
      pfx = clean.slice(0, 6) || defaultPrefix || "其他";
    }
  }

  let cleanContent = trimmed;
  if (cleanContent.startsWith(pfx)) {
    cleanContent = cleanContent.slice(pfx.length).replace(/^[\s:：\-_—·]+/, "");
  }

  return `${openB}${pfx}${closeB}${cleanContent}`;
}

export type UseTodosResult = {
  todos: TodoItem[];
  count: number;
  loading: boolean;
  syncing: boolean;
  error: string;
  projects: string[];
  projectPrefixes: Record<string, string>;
  bracketStyle: string;
  defaultPrefix: string;
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
  const [projectPrefixes, setProjectPrefixes] = useState<Record<string, string>>({});
  const [bracketStyle, setBracketStyle] = useState("【】");
  const [defaultPrefix, setDefaultPrefix] = useState("其他");
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
        api<{
          projects?: string[];
          project_prefixes?: Record<string, string>;
          bracket_style?: string;
          default_prefix?: string;
        }>("/manual-options").catch((): {
          projects?: string[];
          project_prefixes?: Record<string, string>;
          bracket_style?: string;
          default_prefix?: string;
        } => ({})),
      ]);
      setTodos(items || []);
      setProjects(opts.projects || []);
      if (opts.project_prefixes) setProjectPrefixes(opts.project_prefixes);
      if (opts.bracket_style) setBracketStyle(opts.bracket_style);
      if (opts.default_prefix) setDefaultPrefix(opts.default_prefix);
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
      const rawTitle = draft.title.trim();
      if (!rawTitle) return;
      setError("");
      const project = (draft.project || "").trim() || "未归属项目";
      const title = formatTitleWithPrefix(
        rawTitle,
        project,
        projectPrefixes,
        bracketStyle,
        defaultPrefix,
      );
      await api("/tasks", "POST", {
        date: draft.date || getTodayRef.current(),
        title,
        details: draft.details || "",
        project,
        category: draft.category || "任务",
        status: "requested",
        confirmed_minutes: 0,
        time_locked: false,
      });
      await afterMutate();
    },
    [reload, pushAndPullCloud, projectPrefixes, bracketStyle, defaultPrefix],
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
    projectPrefixes,
    bracketStyle,
    defaultPrefix,
    reload,
    syncFromCloud: pushAndPullCloud,
    addTodo,
    completeTodo,
    updateTodo,
    deleteTodo,
    clearError: () => setError(""),
  };
}
