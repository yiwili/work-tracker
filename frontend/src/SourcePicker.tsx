import { useEffect, useState } from "react";
import {
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleAlert,
  LoaderCircle,
  RefreshCw,
  Search,
} from "lucide-react";
import type { Settings } from "./types";
import { api } from "./api";
import { SourceIcon } from "./SourceIcon";

export const sourceKeys = [
  "claude_code",
  "codex",
  "workbuddy_cn",
  "workbuddy_intl",
  "deepseek_harness",
  "qoder_cn",
  "qoder_intl",
  "trae_cn",
  "trae_intl",
] as const;
export type SourceKey = (typeof sourceKeys)[number];

export const sourceLabels: Record<SourceKey, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  workbuddy_cn: "WorkBuddy 国内版",
  workbuddy_intl: "WorkBuddy 国际版",
  deepseek_harness: "DeepSeek Harness",
  qoder_cn: "Qoder 国内版",
  qoder_intl: "Qoder 国际版",
  trae_cn: "Trae 国内版（会话摘要）",
  trae_intl: "Trae 国际版（会话摘要）",
};

type PathStatus = { source: SourceKey; path: string; exists: boolean };
type CheckResult = { items: PathStatus[]; detected: SourceKey[] };

export function SourcePicker({
  settings,
  onChange,
  onDetected,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
  onDetected?: (keys: SourceKey[]) => void;
}) {
  const [status, setStatus] = useState<PathStatus[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<Partial<Record<SourceKey, boolean>>>({});
  const [notice, setNotice] = useState("");

  const check = async (candidate: Settings = settings, focus?: SourceKey) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api<CheckResult>("/sources/check", "POST", {
        sources: Object.fromEntries(
          sourceKeys.map((key) => [
            key,
            candidate[key].paths.map((path) => path.trim()).filter(Boolean),
          ]),
        ),
      });
      setStatus(result.items);
      onDetected?.(result.detected);
      let next = candidate;
      let changed = false;
      for (const key of sourceKeys) {
        const found = result.detected.includes(key);
        if (focus === key && found && !next[key].enabled) {
          next = { ...next, [key]: { ...next[key], enabled: true } };
          changed = true;
          setNotice(`${sourceLabels[key]} 路径有效，已加入采集。`);
        }
        // 首次检测时，路径不存在的来源默认不勾选，避免保存后采不到数据。
        if (next[key].enabled && !found) {
          next = { ...next, [key]: { ...next[key], enabled: false } };
          changed = true;
        }
      }
      if (focus && !result.detected.includes(focus))
        setNotice(`${sourceLabels[focus]} 仍未检测到有效路径，请检查路径。`);
      if (changed) onChange(next);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void check(settings);
    // Detection runs once with the settings loaded by the page.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const detected = (key: SourceKey) =>
    !!status?.some((item) => item.source === key && item.exists);
  const toggle = (key: SourceKey) =>
    setOpen((current) => ({ ...current, [key]: !current[key] }));
  const updatePaths = (key: SourceKey, value: string) =>
    onChange({
      ...settings,
      [key]: { ...settings[key], paths: value.split(/\r?\n/) },
    });
  const enable = (key: SourceKey, enabled: boolean) =>
    onChange({ ...settings, [key]: { ...settings[key], enabled } });

  return (
    <div className="source-picker">
      <div className="source-picker-head">
        <button
          type="button"
          className="outline"
          disabled={busy}
          onClick={() => void check(settings)}
        >
          {busy ? (
            <LoaderCircle className="spin" size={15} />
          ) : (
            <RefreshCw size={15} />
          )}
          {busy ? "正在检测…" : "重新检测全部"}
        </button>
      </div>
      {error && (
        <div className="alert error" role="alert">
          {error}
        </div>
      )}
      {notice && <p className="muted source-notice">{notice}</p>}
      {status === null ? (
        <p className="muted">
          {error ? "检测失败，请点击“重新检测全部”。" : "正在检测默认路径…"}
        </p>
      ) : (
        <div className="source-tags">
          {sourceKeys.map((key) => {
            const rows = status.filter((item) => item.source === key);
            const found = rows.filter((item) => item.exists);
            const expanded = !!open[key];
            const ok = found.length > 0;
            return (
              <div
                className={
                  "source-tag" +
                  (settings[key].enabled ? " selected" : "") +
                  (ok ? "" : " missing")
                }
                key={key}
              >
                <div
                  className="source-tag-head"
                  role="button"
                  tabIndex={0}
                  aria-expanded={expanded}
                  aria-label={`展开 ${sourceLabels[key]} 路径`}
                  onClick={() => toggle(key)}
                  onKeyDown={(e) => {
                    if (e.target !== e.currentTarget) return;
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggle(key);
                    }
                  }}
                >
                  <input
                    type="checkbox"
                    className="tag-check"
                    checked={settings[key].enabled}
                    aria-label={`采集 ${sourceLabels[key]}`}
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                    onChange={(e) => {
                      enable(key, e.target.checked);
                      if (e.target.checked && !ok)
                        setOpen((current) => ({ ...current, [key]: true }));
                    }}
                  />
                  <SourceIcon source={key} />
                  <strong>{sourceLabels[key]}</strong>
                  <em className={ok ? "" : "missing"}>
                    {ok ? (
                      <>
                        <CheckCircle2 size={12} /> 已找到 {found.length} 个路径
                      </>
                    ) : (
                      <>
                        <CircleAlert size={12} /> 未检测到
                      </>
                    )}
                  </em>
                  <span className="tag-chevron" aria-hidden="true">
                    {expanded ? (
                      <ChevronDown size={15} />
                    ) : (
                      <ChevronRight size={15} />
                    )}
                  </span>
                </div>
                {expanded && (
                  <div className="source-tag-body">
                    {rows.length ? (
                      <ul className="path-list">
                        {rows.map((item) => (
                          <li
                            key={item.path}
                            className={item.exists ? "ok" : "warn"}
                          >
                            <code>{item.path}</code>
                            <span>
                              {item.exists ? "已找到" : "路径不存在"}
                            </span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="muted">尚未配置路径。</p>
                    )}
                    <label>
                      采集路径（每行一个）
                      <textarea
                        rows={Math.min(4, Math.max(2, rows.length))}
                        value={settings[key].paths.join("\n")}
                        placeholder="填写该版本的默认目录或 JSONL 文件路径"
                        onChange={(e) => updatePaths(key, e.target.value)}
                      />
                    </label>
                    <button
                      type="button"
                      className="outline small"
                      disabled={busy}
                      onClick={() => void check(settings, key)}
                    >
                      <Search size={14} /> 保存路径并检测
                    </button>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
