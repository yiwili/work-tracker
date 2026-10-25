import { useEffect, useState } from "react";
import { BookOpen, Download, Upload } from "lucide-react";
import type { Settings } from "./types";
type Historical = {
  id: string;
  title: string;
  project: string;
  work_date: string;
  source: string;
};
type Status = {
  configured: boolean;
  account?: { login: string };
  last_synced_at?: string;
  error?: string;
};
async function api(path: string, body?: unknown, method = "POST") {
  const r = await fetch("/api/" + path, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  const data = await r.json();
  if (!r.ok)
    throw Error(
      typeof data.detail === "string"
        ? data.detail
        : "请求格式无效，请检查填写内容",
    );
  return data;
}

export function HistoryPanel({
  settings: s,
  onChange,
  onImported,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
  onImported: () => void;
}) {
  const [file, setFile] = useState<File | null>(null),
    [path, setPath] = useState(s.history_reference.file || ""),
    [since, setSince] = useState(s.history_reference.since || "2026-09-15");
  const [busy, setBusy] = useState(""),
    [message, setMessage] = useState(""),
    [history, setHistory] = useState<{ total: number; items: Historical[] }>({
      total: 0,
      items: [],
    }),
    [offset, setOffset] = useState(0);
  const [config, setConfig] = useState(s.gitee),
    [token, setToken] = useState(""),
    [status, setStatus] = useState<Status>({ configured: false });
  const refresh = async (start = offset) => {
    const [hist, remote] = await Promise.all([
      api(`history?offset=${start}`, undefined, "GET"),
      api("gitee/status", undefined, "GET"),
    ]);
    setHistory(hist);
    setStatus(remote);
  };
  useEffect(() => {
    refresh(offset).catch((e) => setMessage(e.message));
  }, [offset]);
  const loadStyle = async () => {
    const fresh = await api("settings", undefined, "GET");
    onChange({
      ...s,
      title_examples: fresh.title_examples,
      history_reference: fresh.history_reference,
      style_template: fresh.style_template,
      gitee: config,
    });
    onImported();
    await refresh(0);
    setOffset(0);
  };
  const run = async (name: string, fn: () => Promise<void>) => {
    setBusy(name);
    setMessage("");
    try {
      await fn();
    } catch (e) {
      setMessage((e as Error).message);
    } finally {
      setBusy("");
    }
  };
  return (
    <section className="history-panel">
      <h3>
        <BookOpen size={18} /> 历史工作项与命名风格
      </h3>
      <p>
        导入记录保存在本机 SQLite 历史库，作为命名参考。首次导入后由 AI
        提炼风格模板，后续导入沿用现有模板。
      </p>
      <label>
        历史文件（JSON / CSV / Excel，最大20MB）
        <input
          type="file"
          accept=".json,.csv,.xlsx,.xls"
          disabled={!!busy}
          onChange={(e) => setFile(e.target.files?.[0] || null)}
        />
      </label>
      <details className="agent-prompt">
        <summary>或使用本机文件路径</summary>
        <label>
          历史文件路径（服务电脑）
          <input
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="JSON、CSV、XLSX 或 XLS 完整路径"
          />
        </label>
      </details>
      <label>
        风格参考起始日期
        <input
          type="date"
          value={since}
          onChange={(e) => setSince(e.target.value)}
        />
      </label>
      <p>
        全部有效记录入库；指定日期后的标题参与风格提炼。表头支持
        title/标题、project/项目、date/日期及 Gitee 导出字段。Excel
        读取第一张工作表。
      </p>
      <button
        type="button"
        className="outline"
        disabled={!!busy || (!file && !path) || !since}
        onClick={() =>
          run("导入历史工作项", async () => {
            let payload: Record<string, unknown> = {
              path,
              since,
              include_git: true,
            };
            if (file) {
              if (file.size > 20_000_000) throw Error("文件不能超过20MB");
              const content = await new Promise<string>((resolve, reject) => {
                const reader = new FileReader();
                reader.onload = () =>
                  resolve(String(reader.result).split(",")[1]);
                reader.onerror = () => reject(Error("文件读取失败"));
                reader.readAsDataURL(file);
              });
              payload = {
                filename: file.name,
                content_base64: content,
                since,
                include_git: true,
              };
            }
            const result = await api("style/import", payload);
            await loadStyle();
            setMessage(
              `历史库 ${result.stored_records} 条，本次新增 ${result.added} 条，重复更新 ${result.updated} 条，跳过 ${result.skipped} 条。${result.style.message}`,
            );
          })
        }
      >
        <Upload size={16} />
        导入历史工作项
      </button>
      <details className="agent-prompt">
        <summary>查看历史库（{history.total} 条）</summary>
        <div className="history-list">
          {history.items.map((item) => (
            <div key={item.id}>
              <strong>{item.title}</strong>
              <small>
                {item.work_date || "日期未提供"} ·{" "}
                {item.project || "未标记项目"} ·{" "}
                {item.source === "gitee" ? "Gitee" : "文件导入"}
              </small>
            </div>
          ))}
        </div>
        <div className="actions">
          <button
            type="button"
            className="outline"
            disabled={!offset || !!busy}
            onClick={() => setOffset(Math.max(0, offset - 20))}
          >
            上一页
          </button>
          <span>
            {history.total
              ? `${offset + 1}–${Math.min(offset + 20, history.total)}`
              : "暂无记录"}
          </span>
          <button
            type="button"
            className="outline"
            disabled={offset + 20 >= history.total || !!busy}
            onClick={() => setOffset(offset + 20)}
          >
            下一页
          </button>
        </div>
      </details>
      <label>
        命名风格提示模板（随设置保存）
        <textarea
          rows={7}
          maxLength={12000}
          value={s.style_template}
          onChange={(e) => onChange({ ...s, style_template: e.target.value })}
          placeholder="首次导入时由AI自动生成；也可手动填写。用于全天工作项与人工事项命名。"
        />
      </label>
      <button
        type="button"
        className="outline"
        disabled={!!busy || !s.llm_enabled || !s.title_examples.length}
        onClick={() =>
          run("重新提炼命名风格", async () => {
            const r = await api("style/generate", {});
            await loadStyle();
            setMessage(r.message);
          })
        }
      >
        根据历史重新生成模板
      </button>
      <details className="agent-prompt">
        <summary>查看与编辑标题范例（{s.title_examples.length} 条）</summary>
        <textarea
          rows={7}
          aria-label="标题风格范例"
          value={s.title_examples.join("\n")}
          onChange={(e) =>
            onChange({
              ...s,
              title_examples: e.target.value
                .split("\n")
                .filter(Boolean)
                .slice(0, 80),
            })
          }
        />
      </details>
      <h3>
        <Download size={18} /> Gitee API 自动抓取
      </h3>
      <p>
        读取本人负责或创建的 Gitee V5
        Issues，支持所有状态与分页。记录进入历史库并参与命名参考。自动抓取仅在本地服务运行时执行。
      </p>
      <label>
        Gitee Token（留空保留）
        <input
          type="password"
          autoComplete="new-password"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder={
            status.configured
              ? "已配置，本机加密保存"
              : "填写具备 Issues 读取权限的私人令牌"
          }
        />
      </label>
      <label>
        工作项范围
        <select
          value={config.filter}
          onChange={(e) => setConfig({ ...config, filter: e.target.value })}
        >
          <option value="all">本人负责或创建</option>
          <option value="assigned">本人负责</option>
          <option value="created">本人创建</option>
        </select>
      </label>
      <label>
        限定仓库（可选，owner/repo）
        <input
          value={config.repository}
          onChange={(e) => setConfig({ ...config, repository: e.target.value })}
          placeholder="留空读取上述范围的所有仓库"
        />
      </label>
      <label>
        抓取起始日期
        <input
          type="date"
          value={config.since}
          onChange={(e) => setConfig({ ...config, since: e.target.value })}
        />
      </label>
      <label className="check">
        <input
          type="checkbox"
          checked={config.enabled}
          onChange={(e) => setConfig({ ...config, enabled: e.target.checked })}
        />
        启用自动抓取
      </label>
      <label>
        抓取间隔（小时）
        <input
          type="number"
          min={1}
          max={168}
          value={config.interval_hours}
          onChange={(e) =>
            setConfig({ ...config, interval_hours: Number(e.target.value) })
          }
        />
      </label>
      <div className="actions">
        <button
          type="button"
          className="outline"
          disabled={!!busy}
          onClick={() =>
            run("保存Gitee配置", async () => {
              if (token) {
                await api("gitee/token", { api_key: token });
                setToken("");
              }
              const value = await api("gitee/config", config, "PUT");
              setStatus(value);
              onChange({ ...s, gitee: config });
              setMessage(
                "Gitee配置已保存" +
                  (config.enabled ? "，本地服务将定时抓取。" : "。"),
              );
            })
          }
        >
          保存 Gitee 配置
        </button>
        <button
          type="button"
          className="outline"
          disabled={!!busy || (!status.configured && !token)}
          onClick={() =>
            run("抓取Gitee工作项", async () => {
              if (token) {
                await api("gitee/token", { api_key: token });
                setToken("");
              }
              await api("gitee/config", config, "PUT");
              const r = await api("gitee/sync", {});
              await loadStyle();
              setMessage(
                `已抓取 ${r.records} 条，新增 ${r.added} 条。${r.style.message}`,
              );
            })
          }
        >
          立即抓取工作项
        </button>
        {status.configured && (
          <button
            type="button"
            className="outline"
            disabled={!!busy}
            onClick={() =>
              run("移除Gitee凭据", async () => {
                await api("gitee/token", undefined, "DELETE");
                await refresh();
                setMessage("已移除本机保存的Token；已有历史保留。");
              })
            }
          >
            移除本机 Token
          </button>
        )}
      </div>
      <p>
        凭据{status.configured ? "已配置" : "未配置"}
        {status.account ? ` · ${status.account.login}` : ""}
        {status.last_synced_at
          ? ` · 上次成功 ${new Date(status.last_synced_at).toLocaleString()}`
          : ""}
      </p>
      {status.error && <p role="alert">上次自动抓取失败：{status.error}</p>}
      {busy && <p role="status">{busy}中…</p>}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
