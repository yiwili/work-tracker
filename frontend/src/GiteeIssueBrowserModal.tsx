import { useEffect, useMemo, useRef, useState } from "react";
import {
  X,
  ChevronLeft,
  ChevronRight,
  Search,
  RefreshCw,
  ExternalLink,
  ListFilter,
  Settings as SettingsIcon,
  Download,
  Check,
  Database,
  Cloud,
} from "lucide-react";
import type { GiteeRemoteIssue } from "./types";
import { apiUrl } from "./api";
import { enterpriseApi } from "./EnterpriseSettings";

type Mode = "date" | "all";
type StateFilter = "all" | "open" | "progressing" | "closed";

type IssueListResponse = {
  date?: string;
  enterprise_id?: number;
  total?: number;
  local_total?: number;
  synced_at?: string | null;
  source?: string;
  items?: GiteeRemoteIssue[];
  issues?: GiteeRemoteIssue[];
};

const shiftDate = (isoDate: string, deltaDays: number) => {
  if (!isoDate) return isoDate;
  const d = new Date(`${isoDate}T12:00:00`);
  if (Number.isNaN(d.getTime())) return isoDate;
  d.setDate(d.getDate() + deltaDays);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};

const formatStateLabel = (state?: string) => {
  const s = (state || "open").trim().toLowerCase();
  if (["closed", "finished", "resolved", "done", "已完成", "已关闭"].includes(s))
    return { key: "closed", label: "已完成" };
  if (["progressing", "in_progress", "doing", "进行中"].includes(s))
    return { key: "progressing", label: "进行中" };
  return { key: "open", label: "待办" };
};

const computeIssueHours = (issue: GiteeRemoteIssue) => {
  if (typeof issue.hours === "number") return Number(issue.hours.toFixed(2));
  if (typeof issue.duration === "number")
    return Number((issue.duration / 60).toFixed(2));
  return 0;
};

export function GiteeIssueBrowserModal({
  initialDate,
  onClose,
  onSettings,
  onChanged,
}: {
  initialDate: string;
  onClose: () => void;
  onSettings: () => void;
  onChanged?: () => Promise<void> | void;
}) {
  const [selectedDate, setSelectedDate] = useState(initialDate);
  const [mode, setMode] = useState<Mode>("date");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [projectFilter, setProjectFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [issues, setIssues] = useState<GiteeRemoteIssue[]>([]);
  const [localTotal, setLocalTotal] = useState<number>(0);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [loadingLocal, setLoadingLocal] = useState(false);
  const [syncingRemote, setSyncingRemote] = useState(false);
  const [retainingId, setRetainingId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);
  const reqSeqRef = useRef(0);
  const syncAbortRef = useRef<AbortController | null>(null);

  const loadLocalIssues = async () => {
    const seq = ++reqSeqRef.current;
    setLoadingLocal(true);
    setError("");
    try {
      const data: IssueListResponse | GiteeRemoteIssue[] = await enterpriseApi(
        `issues?date=${mode === "all" ? "all" : selectedDate}&state=${stateFilter}&source=local`,
        undefined,
        "GET",
      );
      if (seq !== reqSeqRef.current) return;
      if (Array.isArray(data)) {
        setIssues(data);
      } else {
        setIssues(data.items || data.issues || []);
        if (typeof data.local_total === "number") setLocalTotal(data.local_total);
        if (data.synced_at !== undefined) setSyncedAt(data.synced_at ?? null);
      }
    } catch (e) {
      if (seq !== reqSeqRef.current) return;
      setError((e as Error).message);
      setIssues([]);
    } finally {
      if (seq === reqSeqRef.current) {
        setLoadingLocal(false);
      }
    }
  };

  const handleManualSyncRemote = async () => {
    if (syncAbortRef.current) {
      syncAbortRef.current.abort();
    }
    const controller = new AbortController();
    syncAbortRef.current = controller;
    const seq = ++reqSeqRef.current;
    setSyncingRemote(true);
    setError("");
    setNotice("");
    try {
      const res = await fetch(
        apiUrl(
          `/gitee/enterprise/issues?date=${mode === "all" ? "all" : selectedDate}&state=${stateFilter}&source=remote`,
        ),
        {
          method: "GET",
          signal: controller.signal,
        },
      );
      const data: IssueListResponse | GiteeRemoteIssue[] | { detail?: string } =
        await res.json();
      if (!res.ok) {
        const msg =
          !Array.isArray(data) && "detail" in data && typeof data.detail === "string"
            ? data.detail
            : "手动同步 Gitee 远端失败";
        throw new Error(msg);
      }
      if (seq !== reqSeqRef.current) return;
      const list = Array.isArray(data)
        ? data
        : (data as IssueListResponse).items ||
          (data as IssueListResponse).issues ||
          [];
      setIssues(list);
      if (!Array.isArray(data)) {
        const resp = data as IssueListResponse;
        if (typeof resp.local_total === "number") setLocalTotal(resp.local_total);
        if (resp.synced_at !== undefined) setSyncedAt(resp.synced_at ?? null);
      }
      setNotice(
        `已从 Gitee 远端同步并写入本地历史缓存（当前筛选共 ${list.length} 项）。`,
      );
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      if (seq !== reqSeqRef.current) return;
      setError((e as Error).message);
    } finally {
      if (syncAbortRef.current === controller) {
        syncAbortRef.current = null;
      }
      if (seq === reqSeqRef.current) {
        setSyncingRemote(false);
      }
    }
  };

  useEffect(() => {
    return () => {
      syncAbortRef.current?.abort();
    };
  }, []);

  const handleRetainToLocalReport = async (issueIds?: string[]) => {
    const key = issueIds && issueIds.length === 1 ? issueIds[0] : "batch";
    setRetainingId(key);
    setError("");
    setNotice("");
    try {
      const res: {
        created_count: number;
        already_linked_count: number;
      } = await enterpriseApi(
        "issues/retain",
        {
          date: mode === "all" ? selectedDate : selectedDate,
          issue_ids: issueIds,
        },
        "POST",
      );
      await loadLocalIssues();
      if (onChanged) {
        await onChanged();
      }
      if (res.created_count > 0) {
        setNotice(
          `已将 ${res.created_count} 项 Gitee 工作项同步保留至本地日报，双端（服务器 + 本地）均已留存。`,
        );
      } else {
        setNotice("所选工作项均已关联保存在本地日报中。");
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setRetainingId(null);
    }
  };

  useEffect(() => {
    void loadLocalIssues();
  }, [selectedDate, mode, stateFilter]);

  useEffect(() => {
    closeRef.current?.focus();
  }, []);

  const projects = useMemo(
    () =>
      [
        ...new Set(
          issues.map((i) => i.program_name || "").filter((p) => Boolean(p)),
        ),
      ].sort((a, b) => a.localeCompare(b, "zh-CN")),
    [issues],
  );

  const filteredIssues = useMemo(() => {
    const q = keyword.trim().toLowerCase();
    return issues.filter((issue) => {
      if (projectFilter && (issue.program_name || "") !== projectFilter) {
        return false;
      }
      if (q) {
        const text = [
          String(issue.issue_id),
          issue.title,
          issue.description || "",
          issue.program_name || "",
          issue.issue_date || "",
        ]
          .join(" ")
          .toLowerCase();
        if (!text.includes(q)) return false;
      }
      return true;
    });
  }, [issues, projectFilter, keyword]);

  const unlinkedIssues = useMemo(
    () => filteredIssues.filter((i) => !i.linked_task_id),
    [filteredIssues],
  );

  const totalHours = useMemo(
    () =>
      Number(
        filteredIssues
          .reduce((acc, item) => acc + computeIssueHours(item), 0)
          .toFixed(2),
      ),
    [filteredIssues],
  );

  return (
    <div className="modal-overlay" onClick={onClose}>
      <section
        className="modal gitee-issue-browser-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gitee-issue-browser-title"
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
        }}
      >
        <div className="modal-head">
          <div>
            <h2 id="gitee-issue-browser-title">
              <ListFilter size={20} />
              Gitee 工作项历史浏览与双端同步
            </h2>
            <p>
              {mode === "all"
                ? `浏览全部历史记录 · 本地已缓存 ${localTotal} 条 Gitee 工作项`
                : `${selectedDate} · 即时浏览本地已存记录，可手动从 Gitee 服务器同步并保留到本地`}
              {syncedAt
                ? ` · 上次远端同步：${new Date(syncedAt).toLocaleString("zh-CN")}`
                : ""}
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="outline"
              onClick={onSettings}
            >
              <SettingsIcon size={14} />
              企业配置
            </button>
            <button
              ref={closeRef}
              type="button"
              className="icon-button"
              aria-label="关闭Gitee工作项浏览"
              onClick={onClose}
            >
              <X size={20} />
            </button>
          </div>
        </div>

        {error && (
          <div className="allocation-error" role="alert">
            {error}
          </div>
        )}
        {notice && (
          <div className="gitee-browser-notice" role="status">
            <Check size={14} />
            <span>{notice}</span>
          </div>
        )}

        <div className="gitee-browser-toolbar">
          <div className="gitee-browser-date-bar">
            <div className="gitee-date-nav-group">
              <button
                type="button"
                className="outline"
                aria-label="前一天"
                onClick={() => {
                  setMode("date");
                  setSelectedDate((d) => shiftDate(d || initialDate, -1));
                }}
              >
                <ChevronLeft size={15} />
                前一天
              </button>
              <input
                type="date"
                aria-label="选择工作项日期"
                value={selectedDate}
                onChange={(e) => {
                  setMode("date");
                  setSelectedDate(e.target.value);
                }}
              />
              <button
                type="button"
                className="outline"
                aria-label="后一天"
                onClick={() => {
                  setMode("date");
                  setSelectedDate((d) => shiftDate(d || initialDate, 1));
                }}
              >
                后一天
                <ChevronRight size={15} />
              </button>
              <button
                type="button"
                className={mode === "all" ? "primary" : "outline"}
                onClick={() =>
                  setMode((prev) => (prev === "all" ? "date" : "all"))
                }
              >
                <Database size={14} />
                {mode === "all" ? "正在查看全部历史记录" : "查看全部历史记录"}
              </button>
            </div>

            <div className="gitee-sync-action-group">
              <button
                type="button"
                className="primary"
                disabled={syncingRemote}
                onClick={handleManualSyncRemote}
                title="从 Gitee 企业服务器拉取最新工作项并持久保存在本地数据库"
              >
                <RefreshCw
                  size={14}
                  className={syncingRemote ? "spin" : ""}
                />
                {syncingRemote ? "正在从 Gitee 同步…" : "手动同步远端"}
              </button>
              {unlinkedIssues.length > 0 && (
                <button
                  type="button"
                  className="outline"
                  disabled={retainingId !== null}
                  onClick={() =>
                    handleRetainToLocalReport(
                      unlinkedIssues.map((i) => String(i.issue_id)),
                    )
                  }
                  title="将当前列表中的远端工作项同步写入本地日报清单（服务器与本地同时保留）"
                >
                  <Download size={14} />
                  {retainingId === "batch"
                    ? "正在保留到本地…"
                    : `同步保留到本地日报（${unlinkedIssues.length}项）`}
                </button>
              )}
            </div>
          </div>

          <div className="gitee-browser-filter-bar">
            <label className="gitee-filter-item">
              <span>项目</span>
              <select
                aria-label="按项目筛选"
                value={projectFilter}
                onChange={(e) => setProjectFilter(e.target.value)}
              >
                <option value="">全部项目</option>
                {projects.map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </label>
            <label className="gitee-filter-item">
              <span>状态</span>
              <select
                aria-label="按状态筛选"
                value={stateFilter}
                onChange={(e) =>
                  setStateFilter(e.target.value as StateFilter)
                }
              >
                <option value="all">全部状态</option>
                <option value="open">待办 (open)</option>
                <option value="progressing">进行中 (progressing)</option>
                <option value="closed">已完成 (closed)</option>
              </select>
            </label>
            <label className="gitee-browser-search">
              <Search size={14} />
              <input
                type="search"
                aria-label="搜索工作项"
                placeholder="搜索标题、#ID 或项目…"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
            </label>
          </div>
        </div>

        <div className="gitee-browser-summary" role="status">
          <span>
            当前筛选共 <strong>{filteredIssues.length}</strong> 项工作项 · 合计{" "}
            <strong>{totalHours.toFixed(2)}</strong> 小时
          </span>
          <span className="gitee-storage-pill">
            <Cloud size={13} /> 服务器 + 本地双端保留（本地库共 {localTotal} 条）
          </span>
        </div>

        {syncingRemote && (
          <div className="gitee-browser-loading-bar" role="status">
            <RefreshCw size={14} className="spin" />
            <span>正在从 Gitee 企业版拉取最新工作项并保存到本地（可随时切换日期或关闭窗口）…</span>
          </div>
        )}

        {!loadingLocal && !syncingRemote && !error && filteredIssues.length === 0 && (
          <div className="gitee-browser-empty">
            <p>
              {mode === "all"
                ? "本地暂无已缓存的 Gitee 工作项记录。"
                : `${selectedDate} 暂无本地已保存的 Gitee 工作项记录。`}
            </p>
            <div className="gitee-empty-actions">
              {mode !== "all" && localTotal > 0 && (
                <button
                  type="button"
                  className="outline"
                  onClick={() => setMode("all")}
                >
                  <Database size={14} />
                  查看本地全部历史记录（{localTotal}条）
                </button>
              )}
              <button
                type="button"
                className="primary"
                disabled={syncingRemote}
                onClick={handleManualSyncRemote}
              >
                <RefreshCw size={14} />
                立即手动同步远端
              </button>
            </div>
          </div>
        )}

        {filteredIssues.length > 0 && (
          <div className="gitee-browser-list">
            {filteredIssues.map((issue, idx) => {
              const st = formatStateLabel(issue.state);
              const hrs = computeIssueHours(issue);
              const iid = String(issue.issue_id);
              return (
                <article
                  className="gitee-browser-card"
                  key={`${iid}-${idx}`}
                >
                  <div className="gitee-browser-card-header">
                    <span className="gitee-issue-id-badge">
                      #{iid}
                    </span>
                    <strong className="gitee-issue-title">{issue.title}</strong>
                    {issue.program_name && (
                      <span className="gitee-issue-program-badge">
                        {issue.program_name}
                      </span>
                    )}
                    <span className={`gitee-issue-state-pill state-${st.key}`}>
                      {st.label}
                    </span>
                  </div>
                  <div className="gitee-browser-card-meta">
                    <span>工时：{hrs}h</span>
                    <span>
                      日期：{issue.issue_date || issue.created_at?.slice(0, 10) || selectedDate}
                    </span>
                    <span
                      className={
                        issue.linked_task_id
                          ? "gitee-link-tag linked"
                          : "gitee-link-tag remote-only"
                      }
                    >
                      {issue.linked_task_id
                        ? "已保留在服务器与本地日报"
                        : "已缓存本地历史 · 未写入日报清单"}
                    </span>
                    {!issue.linked_task_id && (
                      <button
                        type="button"
                        className="gitee-retain-inline-btn"
                        disabled={retainingId !== null}
                        onClick={() => handleRetainToLocalReport([iid])}
                      >
                        <Download size={12} />
                        {retainingId === iid ? "保存中…" : "同步至本地日报"}
                      </button>
                    )}
                    {issue.url && (
                      <a href={issue.url} target="_blank" rel="noreferrer">
                        在 Gitee 打开 <ExternalLink size={12} />
                      </a>
                    )}
                  </div>
                  {issue.description && (
                    <details className="gitee-browser-card-desc">
                      <summary>查看工作项详情描述</summary>
                      <pre>
                        {issue.description.replace(
                          /\n*<!-- awt-work-item:[^>]+ -->$/,
                          "",
                        )}
                      </pre>
                    </details>
                  )}
                </article>
              );
            })}
          </div>
        )}

        <div className="modal-footer">
          <button
            type="button"
            className="outline"
            onClick={onClose}
          >
            关闭
          </button>
        </div>
      </section>
    </div>
  );
}
