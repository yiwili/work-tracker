import { useEffect, useState } from "react";
import type { GitAuthor, GitIdentity, Settings } from "./types";

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
  const [rows, setRows] = useState<GitIdentity[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [customRoots, setCustomRoots] = useState<string[]>([]);
  const load = async () => {
    setPending(true);
    setError("");
    try {
      const response = await fetch("/api/git/identities", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          roots: settings.git_roots.map((p) => p.trim()).filter(Boolean),
        }),
      });
      const result = await response.json();
      if (!response.ok)
        throw new Error(
          typeof result.detail === "string"
            ? result.detail
            : "读取Git用户失败，请检查仓库路径",
        );
      setRows(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setPending(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const setOverride = (row: GitIdentity, author: GitAuthor | null) => {
    const overrides = { ...settings.git_author_overrides };
    if (row.override_key) delete overrides[row.override_key];
    delete overrides[row.root];
    if (author) overrides[row.root] = author;
    onChange({ ...settings, git_author_overrides: overrides });
  };
  return (
    <div className="git-user-settings">
      <h3>Git 提交用户</h3>
      <p>
        默认优先读取仓库本地 user.name /
        user.email，再读取继承配置。按作者邮箱精确匹配；只填姓名时按姓名精确匹配。无法确定用户时暂停该仓库的提交采集。
      </p>
      <label>
        Git 默认用户策略
        <select
          value={settings.git_author ? "custom" : "auto"}
          onChange={(e) =>
            onChange({
              ...settings,
              git_author:
                e.target.value === "auto" ? null : { name: "", email: "" },
            })
          }
        >
          <option value="auto">自动检测每个仓库的本地用户（优先）</option>
          <option value="custom">统一自定义用户</option>
        </select>
      </label>
      {settings.git_author && (
        <AuthorFields
          prefix="默认 Git 用户"
          author={settings.git_author}
          onChange={(git_author) => onChange({ ...settings, git_author })}
        />
      )}
      <button
        type="button"
        className="outline"
        disabled={pending}
        onClick={() => void load()}
      >
        {pending ? "正在读取Git用户…" : "读取仓库用户"}
      </button>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {!pending && !rows.length && (
        <p>
          填写 Git 仓库根目录后点击“读取仓库用户”，也可先设置统一自定义用户。
        </p>
      )}
      {rows.map((row) => {
        const author =
          settings.git_author_overrides[row.root] ||
          (row.override_key &&
            settings.git_author_overrides[row.override_key]) ||
          null;
        const selected = author
          ? row.authors.findIndex(
              (a) => a.name === author.name && a.email === author.email,
            )
          : -1;
        const value = !author
          ? "inherit"
          : customRoots.includes(row.root) || selected < 0
            ? "custom"
            : `author:${selected}`;
        return (
          <div className="git-repo-user" key={row.root}>
            <strong>{row.project}</strong>
            <code>{row.root}</code>
            <p>
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
                aria-label={`Git 提交作者 ${row.root}`}
                value={value}
                onChange={(e) => {
                  const choice = e.target.value;
                  setCustomRoots(customRoots.filter((p) => p !== row.root));
                  if (choice === "inherit") setOverride(row, null);
                  else if (choice === "custom") {
                    setCustomRoots([...customRoots, row.root]);
                    setOverride(row, author || { name: "", email: "" });
                  } else {
                    const found = row.authors[Number(choice.split(":")[1])];
                    setOverride(row, { name: found.name, email: found.email });
                  }
                }}
              >
                <option value="inherit">
                  继承默认：{identityText(settings.git_author || row.detected)}
                </option>
                {row.authors.map((a, i) => (
                  <option key={i} value={`author:${i}`}>
                    {identityText(a)} · {a.commits}条提交
                  </option>
                ))}
                <option value="custom">自定义此仓库用户</option>
              </select>
            </label>
            {value === "custom" && author && (
              <AuthorFields
                prefix={`${row.project} 作者`}
                author={author}
                onChange={(next) => setOverride(row, next)}
              />
            )}
          </div>
        );
      })}
      <p>
        仓库单独设置优先于统一用户。作者列表来自最近1000条提交。保存后，旧采集记录也按新用户过滤；已审核工作项和人工工时保留，未审核草稿可重新分析更新。
      </p>
    </div>
  );
}
