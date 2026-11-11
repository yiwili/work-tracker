import { useEffect, useRef, useState } from "react";
import { Upload, X, ExternalLink } from "lucide-react";
import type {
  GiteeProgram,
  GiteeRemoteIssue,
  GiteeVerifyItem,
} from "./types";
import { enterpriseApi } from "./EnterpriseSettings";

type Link = {
  task_id: string;
  status: string;
  url: string;
  error: string;
  issue_id: string;
  action?: "created" | "replaced" | "skipped_existing";
  verified?: boolean;
  remote_title?: string;
  remote_state?: string;
};
type Item = {
  task_id: string;
  title: string;
  description: string;
  minutes: number;
  prefix: string;
  program_id: number | null;
  match_reason: string;
  existing: Link | null;
  matched_issue?: {
    issue_id: number;
    title: string;
    confidence: number;
    status: string;
    url?: string;
    existing_description?: string;
    existing_minutes?: number;
    will_replace?: boolean;
  } | null;
};
type Preview = {
  batch_id: string;
  enterprise_id: number;
  assignee?: {
    id?: number | null;
    name?: string;
    username?: string;
    email?: string;
  } | null;
  items: Item[];
  programs: GiteeProgram[];
  remote_day_issues?: GiteeRemoteIssue[];
};
type IssueType = { id: number; name: string; category: string };

export function GiteePublishModal({
  day,
  taskIds,
  onClose,
  onSettings,
  onApplied,
}: {
  day: string;
  taskIds: string[];
  onClose: () => void;
  onSettings: () => void;
  onApplied: () => Promise<void>;
}) {
  const [preview, setPreview] = useState<Preview | null>(null),
    [targets, setTargets] = useState<Record<string, number>>({}),
    [types, setTypes] = useState<Record<number, IssueType[]>>({}),
    [chosenTypes, setChosenTypes] = useState<Record<string, number>>({});
  const [selected, setSelected] = useState<Set<string>>(new Set()),
    [results, setResults] = useState<Record<string, Link>>({}),
    [replaceMode, setReplaceMode] = useState<
      Record<string, "replace" | "create">
    >({});
  const [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [ack, setAck] = useState(false);
  const [verifying, setVerifying] = useState(false),
    [verifySummary, setVerifySummary] = useState("");
  const [remoteIds, setRemoteIds] = useState<Record<string, string>>({});
  const initialIds = useRef(taskIds),
    close = useRef<HTMLButtonElement>(null);

  const load = async () => {
    setBusy(true);
    setError("");
    setAck(false);
    setVerifySummary("");
    try {
      const p: Preview = await enterpriseApi("preview", {
        date: day,
        task_ids: initialIds.current,
      });
      setPreview(p);
      setTargets(
        Object.fromEntries(p.items.map((i) => [i.task_id, i.program_id || 0])),
      );
      setSelected(
        new Set(
          p.items
            .filter(
              (i) =>
                !i.existing ||
                i.existing.status === "failed" ||
                Boolean(i.matched_issue),
            )
            .map((i) => i.task_id),
        ),
      );
      setReplaceMode(
        Object.fromEntries(
          p.items.map((i) => [
            i.task_id,
            i.matched_issue?.issue_id ||
            (i.existing?.status === "created" && i.existing?.issue_id)
              ? ("replace" as const)
              : ("create" as const),
          ]),
        ),
      );
      setResults({});
      setTypes({});
      const findTaskTypeId = (list?: IssueType[]) =>
        list?.find((t) => t.name.trim() === "任务" || t.category === "task")
          ?.id || 0;
      const typeLists = await Promise.all(
        [
          ...new Set(
            p.items.map((i) => i.program_id).filter((id): id is number => !!id),
          ),
        ].map(
          async (id) =>
            [
              id,
              await enterpriseApi(
                `issue-types?enterprise_id=${p.enterprise_id}&program_id=${id}`,
                undefined,
                "GET",
              ),
            ] as const,
        ),
      );
      const nextTypes = Object.fromEntries(typeLists) as Record<
        number,
        IssueType[]
      >;
      setTypes(nextTypes);
      setChosenTypes(
        Object.fromEntries(
          p.items.map((i) => [
            i.task_id,
            i.program_id ? findTaskTypeId(nextTypes[i.program_id]) : 0,
          ]),
        ),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    load();
    close.current?.focus();
  }, [day]);

  const current = (item: Item) => results[item.task_id] || item.existing;
  const locked = (item: Item) =>
    ["pending", "uncertain"].includes(current(item)?.status || "");
  const targetsValid = !!preview && [...selected].every((id) => !!targets[id]);
  const hasExistingOrResults =
    Object.keys(results).length > 0 ||
    Boolean(preview?.items.some((i) => Boolean(i.existing?.issue_id)));

  const changeProject = async (id: string, program: number) => {
    setTargets((v) => ({ ...v, [id]: program }));
    const pickTaskType = (list?: IssueType[]) =>
      list?.find((t) => t.name.trim() === "任务" || t.category === "task")
        ?.id || 0;
    setChosenTypes((v) => ({
      ...v,
      [id]: program && types[program] ? pickTaskType(types[program]) : 0,
    }));
    setAck(false);
    if (program && preview && !types[program]) {
      setBusy(true);
      try {
        const values = await enterpriseApi(
          `issue-types?enterprise_id=${preview.enterprise_id}&program_id=${program}`,
          undefined,
          "GET",
        );
        setTypes((v) => ({ ...v, [program]: values }));
        setChosenTypes((v) => ({ ...v, [id]: pickTaskType(values) }));
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    }
  };

  const handleVerifyRemote = async () => {
    setVerifying(true);
    setError("");
    try {
      const res:
        | { items?: GiteeVerifyItem[]; verified_count?: number }
        | GiteeVerifyItem[] = await enterpriseApi(
        "verify?date=" + day,
        undefined,
        "GET",
      );
      const list = Array.isArray(res) ? res : res.items || [];
      setResults((prev) => {
        const next = { ...prev };
        for (const v of list) {
          const base =
            next[v.task_id] ||
            preview?.items.find((i) => i.task_id === v.task_id)?.existing;
          next[v.task_id] = {
            task_id: v.task_id,
            status: base?.status || v.status || "created",
            issue_id: String(v.issue_id || base?.issue_id || ""),
            url: v.url || base?.url || "",
            error: v.verify_error || base?.error || "",
            action: base?.action,
            verified: Boolean(v.verified),
            remote_title: v.remote_title,
            remote_state: v.remote_state,
          };
        }
        return next;
      });
      const verifiedCount =
        (!Array.isArray(res) && typeof res.verified_count === "number"
          ? res.verified_count
          : list.filter((i) => i.verified).length) || 0;
      setVerifySummary(
        `已与 Gitee 远端实时核对：${verifiedCount} 项工作项在远端真实生效`,
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="modal-overlay">
      <section
        className="modal gitee-publish-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="gitee-publish-title"
        onKeyDown={(e) => {
          if (e.key === "Escape" && !busy) onClose();
          if (e.key === "Tab") {
            const items = Array.from(
              e.currentTarget.querySelectorAll<HTMLElement>(
                "button:not(:disabled),input:not(:disabled),select:not(:disabled),a[href],summary",
              ),
            );
            const first = items[0],
              last = items.at(-1);
            if (e.shiftKey && document.activeElement === first) {
              e.preventDefault();
              last?.focus();
            } else if (!e.shiftKey && document.activeElement === last) {
              e.preventDefault();
              first?.focus();
            }
          }
        }}
      >
        <div className="modal-head">
          <div>
            <h2 id="gitee-publish-title">
              <Upload size={20} />
              创建 Gitee 企业工作项
            </h2>
            <p>{day} · 提交前核对项目与内容</p>
          </div>
          <button
            ref={close}
            type="button"
            className="icon-button"
            aria-label="关闭Gitee提交"
            disabled={busy}
            onClick={onClose}
          >
            <X size={20} />
          </button>
        </div>
        {error && (
          <div className="allocation-error" role="alert">
            {error}
          </div>
        )}
        {!preview && !busy && (
          <button type="button" className="outline" onClick={onSettings}>
            前往设置企业与标题映射
          </button>
        )}
        {preview && (
          <>
            <p>
              目标企业 #{preview.enterprise_id} · 共 {preview.items.length} 项 ·
              选中 {selected.size} 项 · 负责人：
              {preview.assignee?.name || "未配置（可在企业设置中指定）"} ·
              推送状态：已完成
            </p>
            {preview.remote_day_issues &&
              preview.remote_day_issues.length > 0 && (
                <details className="gitee-remote-existing-panel" open>
                  <summary>
                    📌 本地已同步 / 服务端同期工作项（{preview.remote_day_issues.length}{" "}
                    项）· 推送前可对比是否重复或替换
                  </summary>
                  <div className="gitee-remote-existing-list">
                    {preview.remote_day_issues.map((remote, idx) => {
                      const remoteHours =
                        typeof remote.hours === "number"
                          ? remote.hours
                          : typeof remote.duration === "number"
                            ? Number((remote.duration / 60).toFixed(2))
                            : 0;
                      return (
                        <div
                          className="gitee-remote-existing-row"
                          key={`${remote.issue_id}-${idx}`}
                        >
                          <span className="gitee-remote-id">
                            #{remote.issue_id}
                          </span>
                          <span className="gitee-remote-title">
                            {remote.title}
                          </span>
                          <span className="gitee-remote-meta">
                            {remoteHours}h · {remote.state || "open"}
                          </span>
                          {remote.url && (
                            <a
                              href={remote.url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              查看 <ExternalLink size={12} />
                            </a>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </details>
              )}
            {preview.remote_day_issues &&
              preview.remote_day_issues.length === 0 && (
                <div className="gitee-remote-empty-pill">
                  ✓ 本地已同步库及服务端暂无该日工作项，本次将直接新建
                </div>
              )}
            <fieldset disabled={busy} className="publish-items">
              {preview.items.map((item, index) => {
                const state = current(item),
                  disabled = locked(item);
                const matchedId =
                  item.matched_issue?.issue_id ||
                  (item.existing?.status === "created" && item.existing.issue_id
                    ? Number(item.existing.issue_id)
                    : null);
                const mode = replaceMode[item.task_id] || "replace";
                return (
                  <article className="publish-item" key={item.task_id}>
                    <label className="check-label">
                      <input
                        aria-label={`提交工作项 ${index + 1}`}
                        type="checkbox"
                        checked={selected.has(item.task_id)}
                        disabled={disabled}
                        onChange={(e) => {
                          const next = new Set(selected);
                          if (e.target.checked) next.add(item.task_id);
                          else next.delete(item.task_id);
                          setSelected(next);
                          setAck(false);
                        }}
                      />
                      <strong>{item.title}</strong>
                    </label>
                    <div className="publish-target">
                      <label>
                        目标企业项目
                        <select
                          aria-label={`提交目标项目 ${index + 1}`}
                          value={targets[item.task_id] || ""}
                          disabled={disabled}
                          onChange={(e) =>
                            changeProject(item.task_id, Number(e.target.value))
                          }
                        >
                          <option value="">请选择项目</option>
                          {preview.programs.map((p) => (
                            <option key={p.id} value={p.id}>
                              {p.name}
                              {p.ident ? " · " + p.ident : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        工作项类型
                        <select
                          aria-label={`提交工作项类型 ${index + 1}`}
                          value={chosenTypes[item.task_id] || ""}
                          disabled={disabled || !targets[item.task_id]}
                          onChange={(e) => {
                            setChosenTypes((v) => ({
                              ...v,
                              [item.task_id]: Number(e.target.value),
                            }));
                            setAck(false);
                          }}
                        >
                          <option value="">使用项目默认任务类型</option>
                          {(types[targets[item.task_id]] || []).map((t) => (
                            <option key={t.id} value={t.id}>
                              {t.name}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                    {selected.has(item.task_id) && !targets[item.task_id] && (
                      <span className="publish-missing-target">
                        ⚠️ 请先选择目标企业项目
                      </span>
                    )}
                    <p className="muted">
                      前缀：{item.prefix || "未提供"} · {item.match_reason} ·
                      预估工时 {Number((item.minutes / 60).toFixed(2))}h
                    </p>
                    {!!matchedId && (
                      <div className="publish-replace-banner">
                        <div className="publish-replace-header">
                          <strong>
                            {mode === "create"
                              ? `➕ 将忽略已有项 #${matchedId}，作为新工作项创建`
                              : `🔄 将替换更新 Gitee 已有工作项 #${matchedId}`}
                          </strong>
                          <select
                            aria-label={`推送方式 ${index + 1}`}
                            value={mode}
                            disabled={disabled}
                            onChange={(e) => {
                              const nextMode = e.target.value as
                                | "replace"
                                | "create";
                              setReplaceMode((v) => ({
                                ...v,
                                [item.task_id]: nextMode,
                              }));
                              setAck(false);
                            }}
                          >
                            <option value="replace">
                              替换更新已有项 (#{matchedId})
                            </option>
                            <option value="create">仍作为新工作项创建</option>
                          </select>
                        </div>
                        <div className="publish-replace-diff">
                          <span>
                            远端原标题：{item.matched_issue?.title || item.title}
                          </span>
                          <span>
                            工时对比：
                            {item.matched_issue?.existing_minutes
                              ? Number(
                                  (
                                    item.matched_issue.existing_minutes / 60
                                  ).toFixed(2),
                                ) + "h"
                              : "已存在"}{" "}
                            → {Number((item.minutes / 60).toFixed(2))}h
                          </span>
                        </div>
                      </div>
                    )}
                    <details>
                      <summary>查看将发送的工作内容</summary>
                      <pre className="publish-description">
                        {item.description.replace(
                          /\n*<!-- awt-work-item:[^>]+ -->$/,
                          "",
                        )}
                      </pre>
                    </details>
                    {state && (
                      <p
                        role="status"
                        className={
                          state.status === "failed" ? "publish-failed" : ""
                        }
                      >
                        {state.status === "created"
                          ? `${
                              state.action === "replaced"
                                ? "✅ 已替换更新 #" + state.issue_id
                                : "✅ 已创建 #" + state.issue_id
                            }${state.verified ? " · 远端已确认生效" : ""}${
                              state.remote_state ? ` (${state.remote_state})` : ""
                            }`
                          : state.status === "failed"
                            ? "创建失败，可重试"
                            : "创建结果待核实，为避免重复不会自动重发"}
                        {state.error ? "：" + state.error : ""}{" "}
                        {state.url && (
                          <a href={state.url} target="_blank" rel="noreferrer">
                            在Gitee查看 <ExternalLink size={12} />
                          </a>
                        )}
                        {state &&
                          ["pending", "uncertain"].includes(state.status) && (
                            <div className="actions">
                              <label>
                                核对Gitee工作项数字ID
                                <input
                                  aria-label={`核对工作项ID ${index + 1}`}
                                  type="number"
                                  min="1"
                                  value={remoteIds[item.task_id] || ""}
                                  onChange={(e) =>
                                    setRemoteIds((v) => ({
                                      ...v,
                                      [item.task_id]: e.target.value,
                                    }))
                                  }
                                />
                              </label>
                              <button
                                type="button"
                                className="outline"
                                disabled={!Number(remoteIds[item.task_id])}
                                onClick={async () => {
                                  setBusy(true);
                                  setError("");
                                  try {
                                    const link = await enterpriseApi(
                                      "resolve",
                                      {
                                        enterprise_id: preview.enterprise_id,
                                        task_id: item.task_id,
                                        issue_id: Number(
                                          remoteIds[item.task_id],
                                        ),
                                      },
                                    );
                                    setResults((v) => ({
                                      ...v,
                                      [item.task_id]: link,
                                    }));
                                  } catch (e) {
                                    setError((e as Error).message);
                                  } finally {
                                    setBusy(false);
                                  }
                                }}
                              >
                                核对并关联已有项
                              </button>
                            </div>
                          )}
                      </p>
                    )}
                  </article>
                );
              })}
            </fieldset>
            {verifySummary && (
              <p role="status" className="publish-verify-summary">
                {verifySummary}
              </p>
            )}
            <label className="check-label">
              <input
                type="checkbox"
                checked={ack}
                disabled={busy || !selected.size}
                onChange={(e) => setAck(e.target.checked)}
              />
              我已核对目标项目与工作内容，确认在Gitee创建上述工作项
            </label>
          </>
        )}
        {busy && (
          <p role="status">
            {preview ? "正在处理Gitee请求，请等待…" : "正在匹配企业项目…"}
          </p>
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
          <button
            type="button"
            className="outline"
            disabled={busy}
            onClick={load}
          >
            重新预览
          </button>
          {preview && hasExistingOrResults && (
            <button
              type="button"
              className="outline"
              disabled={busy || verifying}
              onClick={handleVerifyRemote}
            >
              {verifying ? "正在核验远端…" : "🔄 立即核验 Gitee 远端状态"}
            </button>
          )}
          {preview && (
            <button
              type="button"
              disabled={busy || !ack || !selected.size || !targetsValid}
              onClick={async () => {
                setBusy(true);
                setError("");
                try {
                  const r = await enterpriseApi("publish", {
                    batch_id: preview.batch_id,
                    confirmed: true,
                    targets: [...selected].map((id) => {
                      const item = preview.items.find((i) => i.task_id === id);
                      const matchedId =
                        item?.matched_issue?.issue_id ||
                        (item?.existing?.status === "created" &&
                        item.existing.issue_id
                          ? Number(item.existing.issue_id)
                          : null);
                      const shouldReplace =
                        (replaceMode[id] || "replace") === "replace" &&
                        !!matchedId;
                      return {
                        task_id: id,
                        program_id: targets[id],
                        issue_type_id: chosenTypes[id] || null,
                        existing_issue_id: shouldReplace ? matchedId : null,
                        replace_existing: shouldReplace,
                        force_create: Boolean(
                          !shouldReplace &&
                            (item?.existing?.status === "created" ||
                              item?.matched_issue),
                        ),
                      };
                    }),
                  });
                  const links = Object.fromEntries(
                    r.items.map((link: Link) => [link.task_id, link]),
                  );
                  setResults((v) => ({ ...v, ...links }));
                  setSelected(
                    new Set(
                      r.items
                        .filter((i: Link) => i.status === "failed")
                        .map((i: Link) => i.task_id),
                    ),
                  );
                  setAck(false);
                  await onApplied();
                } catch (e) {
                  setError((e as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              确认创建 {selected.size} 条工作项
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
