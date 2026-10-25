import { useEffect, useRef, useState } from "react";
import { Upload, X, ExternalLink } from "lucide-react";
import type { GiteeProgram } from "./types";
import { enterpriseApi } from "./EnterpriseSettings";

type Link = {
  task_id: string;
  status: string;
  url: string;
  error: string;
  issue_id: string;
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
};
type Preview = {
  batch_id: string;
  enterprise_id: number;
  items: Item[];
  programs: GiteeProgram[];
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
    [results, setResults] = useState<Record<string, Link>>({});
  const [busy, setBusy] = useState(true),
    [error, setError] = useState(""),
    [ack, setAck] = useState(false);
  const [remoteIds, setRemoteIds] = useState<Record<string, string>>({});
  const initialIds = useRef(taskIds),
    close = useRef<HTMLButtonElement>(null);
  const load = async () => {
    setBusy(true);
    setError("");
    setAck(false);
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
            .filter((i) => !i.existing || i.existing.status === "failed")
            .map((i) => i.task_id),
        ),
      );
      setResults({});
      setChosenTypes({});
      setTypes({});
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
      setTypes(Object.fromEntries(typeLists));
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
    ["created", "pending", "uncertain"].includes(current(item)?.status || "");
  const targetsValid = !!preview && [...selected].every((id) => !!targets[id]);
  const changeProject = async (id: string, program: number) => {
    setTargets((v) => ({ ...v, [id]: program }));
    setChosenTypes((v) => ({ ...v, [id]: 0 }));
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
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
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
        <p>
          优先使用预设的标题与项目映射，再按项目前缀唯一匹配。创建记录会保存在本机，重复提交会跳过已创建项。
        </p>
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
              选中 {selected.size} 项
            </p>
            <fieldset disabled={busy} className="publish-items">
              {preview.items.map((item, index) => {
                const state = current(item),
                  disabled = locked(item);
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
                    <p className="muted">
                      前缀：{item.prefix || "未提供"} · {item.match_reason} ·
                      预估工时 {Number((item.minutes / 60).toFixed(2))}h
                    </p>
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
                          ? `已创建 #${state.issue_id}`
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
            <p>
              工时写入“预估工时”，工作日期写入计划开始日期；不登记实际工时，不自动关闭任务。只发送预览内容，不上传原始对话或日志。
            </p>
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
                    targets: [...selected].map((id) => ({
                      task_id: id,
                      program_id: targets[id],
                      issue_type_id: chosenTypes[id] || null,
                    })),
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
