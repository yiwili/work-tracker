import { useEffect, useRef, useState } from "react";
import {
  Building2,
  ChevronDown,
  ChevronRight,
  FolderSearch,
  LoaderCircle,
  RefreshCw,
  Trash2,
} from "lucide-react";
import type { GitAuthor, GitIdentity, Settings } from "./types";
import { api } from "./api";

type Repo = { root: string; name: string; relative: string };
type ScanResult = {
  root: string;
  repos: Repo[];
  truncated: boolean;
  message: string;
};
type GitVerify = {
  root: string;
  project: string;
  identity: GitAuthor & { source: string };
  scanned: number;
  matched: number;
  latest: string;
  samples: string[];
};
type IdentityState = { loading: boolean; row?: GitIdentity; error?: string };

const samePath = (a: string, b: string) =>
  a.replace(/[\\/]+$/, "").toLowerCase() ===
  b.replace(/[\\/]+$/, "").toLowerCase();
const pathName = (root: string) =>
  root.split(/[\\/]/).filter(Boolean).at(-1) || root;
const identityText = (a: GitAuthor) =>
  [a.name, a.email && `<${a.email}>`].filter(Boolean).join(" ") ||
  "未检测到用户";

function AuthorFields({
  author,
  onChange,
  prefix,
}: {
  author: GitAuthor;
  onChange: (a: GitAuthor) => void;
  prefix: string;
}) {
  return (
    <div className="form-grid">
      <label>
        {prefix}姓名
        <input
          value={author.name}
          maxLength={200}
          required={!author.email.trim()}
          onChange={(e) => onChange({ ...author, name: e.target.value })}
        />
      </label>
      <label>
        {prefix}邮箱
        <input
          value={author.email}
          maxLength={320}
          required={!author.name.trim()}
          onChange={(e) => onChange({ ...author, email: e.target.value })}
        />
      </label>
    </div>
  );
}

export function GitSettings({
  settings,
  onChange,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
}) {
  const [parent, setParent] = useState("");
  const [scanned, setScanned] = useState<string[]>([]);
  const [found, setFound] = useState<Repo[]>([]);
  const [scanning, setScanning] = useState(false);
  const [scanError, setScanError] = useState("");
  const [scanNotice, setScanNotice] = useState("");
  const [open, setOpen] = useState<Record<string, boolean>>({});
  const [identities, setIdentities] = useState<Record<string, IdentityState>>(
    {},
  );
  const [globalIdentity, setGlobalIdentity] = useState<
    (GitAuthor & { source: string }) | null
  >(null);
  const [customRoots, setCustomRoots] = useState<string[]>([]);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState("");
  const [results, setResults] = useState<GitVerify[] | null>(null);
  const loaded = useRef(new Set<string>());
  const loading = useRef(new Set<string>());
  const repoRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    fetch("/api/git/global-identity")
      .then((r) => (r.ok ? r.json() : null))
      .then(setGlobalIdentity)
      .catch(() => setGlobalIdentity(null));
  }, []);

  const repoList: Repo[] = [];
  const seen = new Set<string>();
  for (const root of settings.git_roots) {
    const key = root.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      repoList.push({ root, name: pathName(root), relative: "" });
    }
  }
  for (const repo of found) {
    const key = repo.root.toLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      repoList.push(repo);
    }
  }

  const selected = (root: string) =>
    settings.git_roots.some((item) => samePath(item, root));

  const update = (patch: Partial<Settings>) =>
    onChange({ ...settings, ...patch });

  const toggleRepo = (repo: Repo, checked: boolean) => {
    if (checked)
      update({ git_roots: [...settings.git_roots, repo.root] });
    else
      update({
        git_roots: settings.git_roots.filter(
          (root) => !samePath(root, repo.root),
        ),
      });
  };

  const nameFor = (root: string) =>
    Object.entries(settings.project_mappings).find(([path]) =>
      samePath(path, root),
    )?.[1] || "";

  const setName = (root: string, value: string) => {
    const mappings: Record<string, string> = {};
    for (const [path, name] of Object.entries(settings.project_mappings))
      if (!samePath(path, root)) mappings[path] = name;
    if (value.trim()) mappings[root] = value.trim();
    update({ project_mappings: mappings });
  };

  const overrideFor = (root: string): GitAuthor | undefined =>
    Object.entries(settings.git_author_overrides).find(([path]) =>
      samePath(path, root),
    )?.[1];

  const setOverride = (root: string, author: GitAuthor | null) => {
    const overrides: Record<string, GitAuthor> = {};
    for (const [path, value] of Object.entries(
      settings.git_author_overrides,
    ))
      if (!samePath(path, root)) overrides[path] = value;
    if (author) overrides[root] = author;
    setResults(null);
    setTestError("");
    update({ git_author_overrides: overrides });
  };

  const removeRepo = (repo: Repo) => {
    const overrides: Record<string, GitAuthor> = {};
    for (const [path, value] of Object.entries(
      settings.git_author_overrides,
    ))
      if (!samePath(path, repo.root)) overrides[path] = value;
    const mappings: Record<string, string> = {};
    for (const [path, name] of Object.entries(settings.project_mappings))
      if (!samePath(path, repo.root)) mappings[path] = name;
    setFound((items) =>
      items.filter((item) => !samePath(item.root, repo.root)),
    );
    setOpen((current) => ({ ...current, [repo.root]: false }));
    update({
      git_roots: settings.git_roots.filter(
        (root) => !samePath(root, repo.root),
      ),
      git_author_overrides: overrides,
      project_mappings: mappings,
    });
  };

  const scan = async () => {
    const root = parent.trim();
    if (!root) return;
    setScanning(true);
    setScanError("");
    setScanNotice("");
    try {
      const result = await api<ScanResult>("/git/scan", "POST", { root });
      if (!result.repos.length) {
        setScanError(result.message || "请选择正确git项目目录");
        return;
      }
      setScanned((current) =>
        current.some((item) => samePath(item, result.root))
          ? current
          : [...current, result.root],
      );
      setFound((current) => {
        const merged = [...current];
        for (const repo of result.repos)
          if (!merged.some((item) => samePath(item.root, repo.root)))
            merged.push(repo);
        return merged;
      });
      const additions = result.repos
        .map((repo) => repo.root)
        .filter(
          (root) =>
            !settings.git_roots.some((item) => samePath(item, root)),
        );
      if (additions.length)
        update({ git_roots: [...settings.git_roots, ...additions] });
      setParent("");
      setScanNotice(
        `已找到 ${result.repos.length} 个仓库` +
          (result.truncated ? "；结果已截断，请选择更具体的目录" : ""),
      );
    } catch (e) {
      setScanError((e as Error).message);
    } finally {
      setScanning(false);
    }
  };

  const ensureIdentity = async (root: string, force = false) => {
    const key = root.toLowerCase();
    if (force) loaded.current.delete(key);
    if (loaded.current.has(key) || loading.current.has(key)) return;
    loading.current.add(key);
    setIdentities((current) => ({
      ...current,
      [key]: { loading: true, row: current[key]?.row },
    }));
    try {
      const rows = await api<GitIdentity[]>("/git/identities", "POST", {
        roots: [root],
      });
      const row = rows.find((item) => samePath(item.root, root));
      loaded.current.add(key);
      setIdentities((current) => ({
        ...current,
        [key]: { loading: false, row },
      }));
    } catch (e) {
      setIdentities((current) => ({
        ...current,
        [key]: { loading: false, error: (e as Error).message },
      }));
    } finally {
      loading.current.delete(key);
    }
  };

  const expand = (repo: Repo) => {
    const next = !open[repo.root];
    setOpen((current) => ({ ...current, [repo.root]: next }));
    if (next) {
      void ensureIdentity(repo.root);
      requestAnimationFrame(() =>
        repoRefs.current[repo.root]?.scrollIntoView({
          block: "nearest",
          behavior: "smooth",
        }),
      );
    }
  };

  const refreshIdentities = async () => {
    setResults(null);
    setTestError("");
    await Promise.all(
      settings.git_roots.map((root) => ensureIdentity(root, true)),
    );
  };

  const detectedDefaults = (): GitAuthor =>
    globalIdentity && (globalIdentity.name || globalIdentity.email)
      ? { name: globalIdentity.name, email: globalIdentity.email }
      : { name: "", email: "" };

  const verify = async () => {
    setTesting(true);
    setTestError("");
    setResults(null);
    try {
      const rows = await api<GitVerify[]>("/git/verify-author", "POST", {
        roots: settings.git_roots,
        author: settings.git_author,
      });
      setResults(rows);
    } catch (e) {
      setTestError((e as Error).message);
    } finally {
      setTesting(false);
    }
  };

  const canTest =
    !settings.git_author ||
    !!(settings.git_author.name.trim() || settings.git_author.email.trim());

  return (
    <div className="git-picker">
      <div className="config-card">
        <h3>
          <Building2 size={17} /> Git 提交用户
        </h3>
        <p>
          自动检测本机 Git 身份，只读取、不修改。按邮箱精确匹配；只填姓名时按姓名精确匹配。无法确定用户时暂停该仓库的提交采集。
        </p>
        <label>
          Git 默认用户策略
          <select
            value={settings.git_author ? "custom" : "auto"}
            onChange={(e) => {
              setResults(null);
              update({
                git_author:
                  e.target.value === "auto"
                    ? null
                    : settings.git_author || detectedDefaults(),
              });
            }}
          >
            <option value="auto">自动检测每个仓库的本地用户（优先）</option>
            <option value="custom">统一自定义用户</option>
          </select>
        </label>
        {!settings.git_author && (
          <p className="muted">
            本机全局 Git 身份：
            {globalIdentity &&
            (globalIdentity.name || globalIdentity.email)
              ? identityText(globalIdentity)
              : "未检测到（仓库有本地配置时仍会使用仓库自己的身份）"}
          </p>
        )}
        {settings.git_author && (
          <AuthorFields
            prefix="默认 Git 用户"
            author={settings.git_author}
            onChange={(author) => {
              setResults(null);
              update({ git_author: author });
            }}
          />
        )}
        <div className="git-actions">
          <button
            type="button"
            className="outline small"
            onClick={() => void refreshIdentities()}
          >
            <RefreshCw size={14} /> 重新检测Git身份
          </button>
          <button
            type="button"
            className="outline small"
            disabled={testing || !canTest}
            onClick={() => void verify()}
          >
            {testing ? (
              <LoaderCircle className="spin" size={14} />
            ) : null}
            {testing ? "正在测试提交匹配…" : "测试提交匹配"}
          </button>
        </div>
        {testError && (
          <div className="alert error" role="alert">
            {testError}
          </div>
        )}
        {results && (
          <div
            className={
              "alert compact" +
              (results.some((row) => row.matched) ? "" : " error")
            }
            role="status"
          >
            <strong>
              {!results.length
                ? "没有可测试的仓库：请先扫描并勾选 Git 项目。"
                : results.some((row) => row.matched)
                  ? `测试通过：最近 ${results.reduce((n, row) => n + row.scanned, 0)} 条提交中匹配 ${results.reduce((n, row) => n + row.matched, 0)} 条`
                  : `没有匹配到提交：最近 ${results.reduce((n, row) => n + row.scanned, 0)} 条提交里没有该身份`}
            </strong>
            {results.map((row) => (
              <p key={row.root}>
                {row.project}：匹配 {row.matched} / {row.scanned} 条
                {row.matched
                  ? `，最近 ${row.latest.slice(0, 10)}${row.samples.length ? `，如“${row.samples[0]}”` : ""}`
                  : "（请检查邮箱是否与提交时一致）"}
              </p>
            ))}
          </div>
        )}
      </div>

      <div className="config-card">
        <h3>
          <FolderSearch size={17} /> 扫描 Git 项目
        </h3>
        <p>
          选择一个项目上级目录，系统向下检查 3 层，自动列出找到的 Git
          仓库。找到后会默认加入采集，可单独取消。
        </p>
        <div className="manual-row">
          <input
            aria-label="Git 项目上级目录"
            value={parent}
            placeholder={"例如 D:\\work 或 D:\\projects"}
            onChange={(e) => setParent(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                void scan();
              }
            }}
          />
          <button
            type="button"
            className="outline small"
            disabled={scanning || !parent.trim()}
            onClick={() => void scan()}
          >
            {scanning ? (
              <LoaderCircle className="spin" size={14} />
            ) : (
              <FolderSearch size={14} />
            )}
            {scanning ? "正在扫描…" : "扫描目录"}
          </button>
        </div>
        {scanned.length > 0 && (
          <p className="muted">已扫描：{scanned.join("、")}</p>
        )}
        {scanNotice && <p className="muted">{scanNotice}</p>}
        {scanError && (
          <div className="alert error" role="alert">
            {scanError}
          </div>
        )}
        {repoList.length ? (
          <div className="repo-tags">
            {repoList.map((repo) => {
              const checked = selected(repo.root);
              const expanded = !!open[repo.root];
              const display = nameFor(repo.root);
              const state = identities[repo.root.toLowerCase()];
              const row = state?.row;
              const override = overrideFor(repo.root);
              const authorIndex = override && row
                ? row.authors.findIndex(
                    (a) =>
                      a.name === override.name &&
                      a.email === override.email,
                  )
                : -1;
              const isCustom =
                !!override &&
                (customRoots.includes(repo.root) || authorIndex < 0);
              const value = !override
                ? "inherit"
                : isCustom
                  ? "custom"
                  : `author:${authorIndex}`;
              return (
                <div
                  className={"repo-tag" + (checked ? " selected" : "")}
                  key={repo.root}
                  ref={(node) => {
                    repoRefs.current[repo.root] = node;
                  }}
                >
                  <div
                    className="repo-tag-head"
                    role="button"
                    tabIndex={0}
                    aria-expanded={expanded}
                    aria-label={`展开 ${display || repo.name} 设置`}
                    onClick={() => expand(repo)}
                    onKeyDown={(e) => {
                      if (e.target !== e.currentTarget) return;
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        expand(repo);
                      }
                    }}
                  >
                    <input
                      type="checkbox"
                      className="tag-check"
                      checked={checked}
                      aria-label={`采集 ${display || repo.name}`}
                      onClick={(e) => e.stopPropagation()}
                      onKeyDown={(e) => e.stopPropagation()}
                      onChange={(e) => toggleRepo(repo, e.target.checked)}
                    />
                    <strong>{display || repo.name}</strong>
                    {display && <em>{repo.name}</em>}
                    <div className="tag-head-actions">
                      <button
                        type="button"
                        className="tag-delete"
                        aria-label={`移除 ${display || repo.name}`}
                        title="移除仓库"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeRepo(repo);
                        }}
                      >
                        <Trash2 size={14} />
                      </button>
                      <span className="tag-chevron" aria-hidden="true">
                        {expanded ? (
                          <ChevronDown size={15} />
                        ) : (
                          <ChevronRight size={15} />
                        )}
                      </span>
                    </div>
                  </div>
                  {expanded && (
                    <div className="repo-tag-body">
                      <code>{repo.root}</code>
                      <label>
                        项目显示名（可选）
                        <input
                          value={display}
                          maxLength={500}
                          placeholder={repo.name}
                          onChange={(e) =>
                            setName(repo.root, e.target.value)
                          }
                        />
                      </label>
                      {state?.loading && (
                        <p className="muted">正在检测该仓库的 Git 用户…</p>
                      )}
                      {state?.error && (
                        <div className="alert error" role="alert">
                          {state.error}
                        </div>
                      )}
                      {row && (
                        <>
                          <p className="muted">
                            检测身份：{identityText(row.detected)} ·{" "}
                            {row.detected.source === "local"
                              ? "仓库本地配置"
                              : row.detected.source === "inherited"
                                ? "继承的Git配置"
                                : "需选择或自定义"}
                          </p>
                          <label>
                            {row.project} 提交作者
                            <select
                              aria-label={`Git 提交作者 ${repo.root}`}
                              value={value}
                              onChange={(e) => {
                                const choice = e.target.value;
                                setCustomRoots((current) =>
                                  current.filter(
                                    (path) => path !== repo.root,
                                  ),
                                );
                                if (choice === "inherit")
                                  setOverride(repo.root, null);
                                else if (choice === "custom") {
                                  setCustomRoots((current) => [
                                    ...current,
                                    repo.root,
                                  ]);
                                  const detected =
                                    row.detected.name ||
                                    row.detected.email
                                      ? {
                                          name: row.detected.name,
                                          email: row.detected.email,
                                        }
                                      : detectedDefaults();
                                  setOverride(
                                    repo.root,
                                    override || detected,
                                  );
                                } else {
                                  const author =
                                    row.authors[
                                      Number(choice.split(":")[1])
                                    ];
                                  setOverride(repo.root, {
                                    name: author.name,
                                    email: author.email,
                                  });
                                }
                              }}
                            >
                              <option value="inherit">
                                继承默认：
                                {identityText(
                                  settings.git_author || row.detected,
                                )}
                              </option>
                              {row.authors.map((author, index) => (
                                <option
                                  key={index}
                                  value={`author:${index}`}
                                >
                                  {identityText(author)} ·{" "}
                                  {author.commits}条提交
                                </option>
                              ))}
                              <option value="custom">
                                自定义此仓库用户
                              </option>
                            </select>
                          </label>
                          {isCustom && override && (
                            <AuthorFields
                              prefix={`${row.project} 作者`}
                              author={override}
                              onChange={(author) =>
                                setOverride(repo.root, author)
                              }
                            />
                          )}
                        </>
                      )}
                      {!state?.loading && !state?.error && !row && (
                        <p className="muted">
                          未读取到该仓库的 Git 身份，请检查路径或排除规则。
                        </p>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="muted">
            尚未添加 Git 仓库。选择项目上级目录后扫描，最多向下 3
            层；找不到时会提示“请选择正确git项目目录”。
          </p>
        )}
      </div>
    </div>
  );
}
