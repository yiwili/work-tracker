import React, { useState, useMemo } from "react";
import {
  ChevronDown,
  ChevronUp,
  Copy,
  Check,
  Search,
  FolderGit2,
  User,
  ArrowUpRight,
  Activity,
  FileCode,
  GitCommit,
  Layers,
  X,
} from "lucide-react";
import type { GitCommitItem } from "./types";

interface GitCommitsViewerProps {
  commits: GitCommitItem[];
  timezone: string;
  onViewTimeline?: (source: string, session: string) => void;
  onClose?: () => void;
}

interface ParsedFile {
  path: string;
  filename: string;
  dir: string;
  added: number;
  deleted: number;
  isBinary: boolean;
}

interface ParsedChanges {
  files: ParsedFile[];
  totalAdded: number;
  totalDeleted: number;
}

function parseChanges(rawChanges?: string): ParsedChanges {
  if (!rawChanges || !rawChanges.trim()) {
    return { files: [], totalAdded: 0, totalDeleted: 0 };
  }

  const files: ParsedFile[] = [];
  let totalAdded = 0;
  let totalDeleted = 0;

  for (const line of rawChanges.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const parts = trimmed.split("\t");
    if (parts.length >= 3) {
      const addStr = parts[0];
      const delStr = parts[1];
      const path = parts.slice(2).join("\t");
      const isBinary = addStr === "-" || delStr === "-";
      const added = isBinary ? 0 : parseInt(addStr, 10) || 0;
      const deleted = isBinary ? 0 : parseInt(delStr, 10) || 0;
      totalAdded += added;
      totalDeleted += deleted;

      const slashIdx = path.lastIndexOf("/");
      const backslashIdx = path.lastIndexOf("\\");
      const lastSep = Math.max(slashIdx, backslashIdx);
      const dir = lastSep >= 0 ? path.slice(0, lastSep + 1) : "";
      const filename = lastSep >= 0 ? path.slice(lastSep + 1) : path;

      files.push({ path, filename, dir, added, deleted, isBinary });
    }
  }

  return { files, totalAdded, totalDeleted };
}

interface GroupedCommit {
  sha: string;
  shortSha: string;
  subject: string;
  occurred_at: string;
  author_name?: string;
  author_email?: string;
  changes?: string;
  projects: string[];
  repoIds: string[];
  sessionIds: string[];
  cwd?: string;
  id: string;
}

export function GitCommitsViewer({
  commits,
  timezone,
  onViewTimeline,
  onClose,
}: GitCommitsViewerProps) {
  const [selectedProject, setSelectedProject] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState("");
  const [dedupBySha, setDedupBySha] = useState(true);
  const [expandedFiles, setExpandedFiles] = useState<Set<string>>(new Set());
  const [copiedSha, setCopiedSha] = useState<string | null>(null);

  // Group commits if dedup is enabled
  const groupedCommits = useMemo(() => {
    if (!dedupBySha) {
      return commits.map((c) => ({
        sha: c.sha,
        shortSha: c.sha ? c.sha.slice(0, 7) : "",
        subject: c.subject,
        occurred_at: c.occurred_at,
        author_name: c.author_name,
        author_email: c.author_email,
        changes: c.changes,
        projects: c.project ? [c.project] : [],
        repoIds: c.repo_id ? [c.repo_id] : [],
        sessionIds: c.session_id ? [c.session_id] : [],
        cwd: c.cwd,
        id: c.id,
      }));
    }

    const map = new Map<string, GroupedCommit>();
    for (const c of commits) {
      const key = c.sha || c.id;
      const existing = map.get(key);
      if (existing) {
        if (c.project && !existing.projects.includes(c.project)) {
          existing.projects.push(c.project);
        }
        if (c.repo_id && !existing.repoIds.includes(c.repo_id)) {
          existing.repoIds.push(c.repo_id);
        }
        if (c.session_id && !existing.sessionIds.includes(c.session_id)) {
          existing.sessionIds.push(c.session_id);
        }
        if (!existing.changes && c.changes) {
          existing.changes = c.changes;
        }
      } else {
        map.set(key, {
          sha: c.sha,
          shortSha: c.sha ? c.sha.slice(0, 7) : "",
          subject: c.subject,
          occurred_at: c.occurred_at,
          author_name: c.author_name,
          author_email: c.author_email,
          changes: c.changes,
          projects: c.project ? [c.project] : [],
          repoIds: c.repo_id ? [c.repo_id] : [],
          sessionIds: c.session_id ? [c.session_id] : [],
          cwd: c.cwd,
          id: c.id,
        });
      }
    }
    return Array.from(map.values());
  }, [commits, dedupBySha]);

  // Available unique projects
  const availableProjects = useMemo(() => {
    const set = new Set<string>();
    commits.forEach((c) => {
      if (c.project) set.add(c.project);
    });
    return Array.from(set).sort();
  }, [commits]);

  // Project count map
  const projectCountMap = useMemo(() => {
    const map = new Map<string, number>();
    for (const c of commits) {
      if (c.project) {
        map.set(c.project, (map.get(c.project) || 0) + 1);
      }
    }
    return map;
  }, [commits]);

  // Filtered commits
  const filteredCommits = useMemo(() => {
    let list = groupedCommits;
    if (selectedProject !== "all") {
      list = list.filter((c) => c.projects.includes(selectedProject));
    }
    if (searchQuery.trim()) {
      const q = searchQuery.toLowerCase().trim();
      list = list.filter((c) => {
        if (c.subject.toLowerCase().includes(q)) return true;
        if (c.sha.toLowerCase().includes(q)) return true;
        if (c.author_name && c.author_name.toLowerCase().includes(q)) return true;
        if (c.projects.some((p) => p.toLowerCase().includes(q))) return true;
        if (c.changes && c.changes.toLowerCase().includes(q)) return true;
        return false;
      });
    }
    return list;
  }, [groupedCommits, selectedProject, searchQuery]);

  const toggleFiles = (id: string) => {
    setExpandedFiles((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const handleCopySha = (sha: string) => {
    if (!sha) return;
    navigator.clipboard.writeText(sha);
    setCopiedSha(sha);
    setTimeout(() => {
      setCopiedSha((cur) => (cur === sha ? null : cur));
    }, 1800);
  };

  const formatTime = (isoString?: string) => {
    if (!isoString) return "";
    try {
      const date = new Date(isoString);
      return date.toLocaleTimeString("zh-CN", {
        timeZone: timezone,
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    } catch {
      return isoString.slice(11, 19);
    }
  };

  const formatDateFull = (isoString?: string) => {
    if (!isoString) return "";
    try {
      return new Date(isoString).toLocaleString("zh-CN", {
        timeZone: timezone,
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    } catch {
      return isoString;
    }
  };

  return (
    <div className="apple-git-commits-panel">
      {/* 顶部工具栏与筛选器 */}
      <div className="apple-git-panel-header">
        <div className="apple-git-header-left">
          <div className="apple-git-panel-title">
            <GitCommit size={15} className="apple-git-icon-accent" />
            <span>今日 Git 提交明细</span>
            <span className="apple-git-total-badge">
              {filteredCommits.length} 项
              {groupedCommits.length !== commits.length && (
                <span className="apple-git-raw-count">
                  （去重前 {commits.length}）
                </span>
              )}
            </span>
          </div>

          {/* 仓库筛选分段控制器 */}
          {availableProjects.length > 1 && (
            <div className="apple-segmented-group apple-git-projects-segmented">
              <button
                type="button"
                className={`apple-segmented-btn ${selectedProject === "all" ? "active" : ""}`}
                onClick={() => setSelectedProject("all")}
              >
                全部仓库 ({commits.length})
              </button>
              {availableProjects.map((proj) => (
                <button
                  key={proj}
                  type="button"
                  className={`apple-segmented-btn ${selectedProject === proj ? "active" : ""}`}
                  onClick={() => setSelectedProject(proj)}
                >
                  {proj} ({projectCountMap.get(proj) || 0})
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="apple-git-header-right">
          {/* 搜索框 */}
          <div className="apple-git-search-wrap">
            <Search size={13} className="apple-git-search-icon" />
            <input
              type="text"
              className="apple-git-search-input"
              placeholder="搜索提交、SHA 或文件…"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
            {searchQuery && (
              <button
                type="button"
                className="apple-git-search-clear"
                onClick={() => setSearchQuery("")}
              >
                <X size={12} />
              </button>
            )}
          </div>

          {/* 去重切换开关 */}
          {commits.length > groupedCommits.length && (
            <button
              type="button"
              className={`apple-git-dedup-toggle ${dedupBySha ? "active" : ""}`}
              onClick={() => setDedupBySha(!dedupBySha)}
              title="合并相同 SHA 的工作树重复记录"
            >
              <Layers size={12} />
              <span>{dedupBySha ? "已合并重复" : "查看全部记录"}</span>
            </button>
          )}

          {/* 活动时间线快捷跳转 */}
          {onViewTimeline && (
            <button
              type="button"
              className="apple-git-timeline-shortcut"
              onClick={() => onViewTimeline("git", "")}
              title="在活动时间线页面查看完整活动事件"
            >
              <Activity size={12} />
              <span>活动时间线</span>
            </button>
          )}

          {/* 收起按钮 */}
          {onClose && (
            <button
              type="button"
              className="apple-git-close-btn"
              onClick={onClose}
              title="收起提交列表"
            >
              <ChevronUp size={14} />
              <span>收起</span>
            </button>
          )}
        </div>
      </div>

      {/* 提交项列表 */}
      <div className="apple-git-commits-list">
        {filteredCommits.length === 0 ? (
          <div className="apple-git-empty-state">
            <GitCommit size={28} className="apple-git-empty-icon" />
            <p className="apple-git-empty-text">
              {searchQuery
                ? `未找到与 “${searchQuery}” 相关的提交`
                : "当前筛选条件下没有 Git 提交记录"}
            </p>
            {searchQuery && (
              <button
                type="button"
                className="apple-button-subtle apple-git-clear-btn"
                onClick={() => setSearchQuery("")}
              >
                清除搜索条件
              </button>
            )}
          </div>
        ) : (
          filteredCommits.map((commit) => {
            const parsed = parseChanges(commit.changes);
            const isExpanded = expandedFiles.has(commit.id);
            const hasChanges = parsed.files.length > 0;
            const primaryRepo = commit.repoIds[0] || commit.sessionIds[0] || "";

            return (
              <article key={commit.id} className="apple-git-commit-card">
                {/* 顶部元数据行 */}
                <div className="apple-git-card-meta">
                  <div className="apple-git-meta-left">
                    <span
                      className="apple-git-time-tag"
                      title={formatDateFull(commit.occurred_at)}
                    >
                      {formatTime(commit.occurred_at)}
                    </span>

                    {commit.shortSha && (
                      <button
                        type="button"
                        className={`apple-git-sha-badge ${copiedSha === commit.sha ? "copied" : ""}`}
                        onClick={() => handleCopySha(commit.sha)}
                        title={`点击复制完整 SHA: ${commit.sha}`}
                      >
                        <code className="apple-git-mono">{commit.shortSha}</code>
                        {copiedSha === commit.sha ? (
                          <Check size={11} className="apple-git-copy-icon" />
                        ) : (
                          <Copy size={11} className="apple-git-copy-icon" />
                        )}
                        {copiedSha === commit.sha && (
                          <span className="apple-git-copied-hint">已复制</span>
                        )}
                      </button>
                    )}

                    {commit.projects.map((proj, idx) => (
                      <span key={proj} className="apple-git-project-tag">
                        <FolderGit2 size={11} />
                        {proj}
                        {idx === 0 && commit.projects.length > 1 && (
                          <span
                            className="apple-git-extra-projects"
                            title={`关联路径：${commit.projects.join(", ")}`}
                          >
                            +{commit.projects.length - 1} 工作树
                          </span>
                        )}
                      </span>
                    ))}

                    {commit.author_name && (
                      <span className="apple-git-author-tag" title={commit.author_email || ""}>
                        <User size={11} />
                        {commit.author_name}
                      </span>
                    )}
                  </div>

                  <div className="apple-git-meta-right">
                    {onViewTimeline && (
                      <button
                        type="button"
                        className="apple-git-jump-btn"
                        onClick={() => onViewTimeline("git", primaryRepo)}
                        title="在时间线查看该仓库所有记录"
                      >
                        <span>时间线</span>
                        <ArrowUpRight size={11} />
                      </button>
                    )}
                  </div>
                </div>

                {/* 提交标题说明 */}
                <div className="apple-git-card-subject">
                  <span className="apple-git-bullet" />
                  <span className="apple-git-subject-text">{commit.subject}</span>
                </div>

                {/* 文件变更统计与展开按钮 */}
                {hasChanges && (
                  <div className="apple-git-changes-summary">
                    <div className="apple-git-changes-stats">
                      <span className="apple-git-stat-files">
                        <FileCode size={12} />
                        {parsed.files.length} 个文件变更
                      </span>
                      {parsed.totalAdded > 0 && (
                        <span className="apple-git-stat-add">
                          +{parsed.totalAdded}
                        </span>
                      )}
                      {parsed.totalDeleted > 0 && (
                        <span className="apple-git-stat-del">
                          -{parsed.totalDeleted}
                        </span>
                      )}
                    </div>

                    <button
                      type="button"
                      className={`apple-git-files-toggle ${isExpanded ? "expanded" : ""}`}
                      onClick={() => toggleFiles(commit.id)}
                    >
                      <span>{isExpanded ? "收起文件明细" : "展开变更文件"}</span>
                      <ChevronDown
                        size={12}
                        className={`apple-git-chevron ${isExpanded ? "rotated" : ""}`}
                      />
                    </button>
                  </div>
                )}

                {/* 展开的文件明细列表 */}
                {hasChanges && isExpanded && (
                  <div className="apple-git-files-list">
                    {parsed.files.map((file, i) => (
                      <div key={file.path + i} className="apple-git-file-row">
                        <div className="apple-git-file-counts">
                          {file.isBinary ? (
                            <span className="apple-git-diff-binary">BIN</span>
                          ) : (
                            <>
                              <span
                                className={`apple-git-diff-add ${file.added === 0 ? "zero" : ""}`}
                              >
                                +{file.added}
                              </span>
                              <span
                                className={`apple-git-diff-del ${file.deleted === 0 ? "zero" : ""}`}
                              >
                                -{file.deleted}
                              </span>
                            </>
                          )}
                        </div>
                        <div className="apple-git-file-path" title={file.path}>
                          {file.dir && (
                            <span className="apple-git-file-dir">{file.dir}</span>
                          )}
                          <span className="apple-git-file-name">{file.filename}</span>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </article>
            );
          })
        )}
      </div>

      {/* 底部摘要信息 */}
      <div className="apple-git-panel-footer">
        <span className="apple-git-footer-info">
          共 {filteredCommits.length} 项提交记录 · 仅包含所选作者的当天代码提交
        </span>
        {onClose && (
          <button
            type="button"
            className="apple-button-subtle apple-git-footer-collapse"
            onClick={onClose}
          >
            <ChevronUp size={13} />
            <span>收起面板</span>
          </button>
        )}
      </div>
    </div>
  );
}
