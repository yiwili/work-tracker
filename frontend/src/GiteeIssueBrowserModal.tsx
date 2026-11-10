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
} from "lucide-react";
import type { GiteeRemoteIssue } from "./types";
import { enterpriseApi } from "./EnterpriseSettings";

type Mode = "date" | "all";
type StateFilter = "all" | "open" | "progressing" | "closed";

type IssueListResponse = {
  date?: string;
  enterprise_id?: number;
  total?: number;
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
}: {
  initialDate: string;
  onClose: () => void;
  onSettings: () => void;
}) {
  const [selectedDate, setSelectedDate] = useState(initialDate);
  const [mode, setMode] = useState<Mode>("date");
  const [stateFilter, setStateFilter] = useState<StateFilter>("all");
  const [projectFilter, setProjectFilter] = useState("");
  const [keyword, setKeyword] = useState("");
  const [issues, setIssues] = useState<GiteeRemoteIssue[]>([]);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState("");
  const closeRef = useRef<HTMLButtonElement>(null);

  const loadIssues = async () => {
    setBusy(true);
    setError("");
    try {
      const data: IssueListResponse | GiteeRemoteIssue[] = await enterpriseApi(
        `issues?date=${mode === "all" ? "all" : selectedDate}&state=${stateFilter}`,
        undefined,
        "GET",
      );
      const list = Array.isArray(data)
        ? data
        : data.items || data.issues || [];
      setIssues(list);
    } catch (e) {
      setError((e as Error).message);
      setIssues([]);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void loadIssues();
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
    <div className="modal-overlay">
      <section
        className="modal gitee-issue-browser-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gitee-issue-browser-title"
        onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) onClose();
        }}
      >
        <div className="modal-head">
          <div>
            <h2 id="gitee-issue-browser-title">
              <ListFilter size={20} />
              Gitee 远端工作项浏览与核对
            </h2>
            <p>
              {mode === "all"
                ? "查看全部近期项 · 实时读取 Gitee 企业远端登记情况"
                : `${selectedDate} · 按日期查看 Gitee 远端已登记工作项`}
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="outline"
              onClick={onSettings}
              disabled={busy}
            >
              <SettingsIcon size={14} />
              企业配置
            </button>
            <button
              ref={closeRef}
              type="button"
              className="icon-button"
              aria-label="关闭Gitee工作项浏览"
              disabled={busy}
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

        <div className="gitee-browser-toolbar">
          <div className="gitee-browser-date-bar">
            <button
              type="button"
              className="outline"
              aria-label="前一天"
              disabled={busy}
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
              disabled={busy}
              onChange={(e) => {
                setMode("date");
                setSelectedDate(e.target.value);
              }}
            />
            <button
              type="button"
              className="outline"
              aria-label="后一天"
              disabled={busy}
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
              disabled={busy}
              onClick={() =>
                setMode((prev) => (prev === "all" ? "date" : "all"))
              }
            >
              查看全部近期项
            </button>
          </div>

          <div className="gitee-browser-filter-bar">
            <label>
              项目
              <select
                aria-label="按项目筛选"
                value={projectFilter}
                disabled={busy}
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
            <label>
              状态
              <select
                aria-label="按状态筛选"
                value={stateFilter}
                disabled={busy}
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
                aria-label="搜索远端工作项"
                placeholder="搜索标题、#ID 或项目…"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
            </label>
            <button
              type="button"
              className="outline"
              disabled={busy}
              onClick={loadIssues}
            >
              <RefreshCw size={14} />
              刷新远端
            </button>
          </div>
        </div>

        <div className="gitee-browser-summary" role="status">
          <span>
            共 <strong>{filteredIssues.length}</strong> 项工作项 · 合计{" "}
            <strong>{totalHours.toFixed(2)}</strong> 小时
          </span>
        </div>

        {busy && <p role="status">正在从 Gitee 企业版获取工作项列表…</p>}

        {!busy && !error && filteredIssues.length === 0 && (
          <div className="gitee-browser-empty">
            该筛选条件下暂无 Gitee 远端工作项记录。
          </div>
        )}

        {!busy && filteredIssues.length > 0 && (
          <div className="gitee-browser-list">
            {filteredIssues.map((issue, idx) => {
              const st = formatStateLabel(issue.state);
              const hrs = computeIssueHours(issue);
              return (
                <article
                  className="gitee-browser-card"
                  key={`${issue.issue_id}-${idx}`}
                >
                  <div className="gitee-browser-card-header">
                    <span className="gitee-issue-id-badge">
                      #{issue.issue_id}
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
                      {issue.linked_task_id ? "已关联本地日报" : "仅在 Gitee 远端"}
                    </span>
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
            disabled={busy}
            onClick={onClose}
          >
            关闭
          </button>
        </div>
      </section>
    </div>
  );
}
