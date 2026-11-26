import { useRef, useState } from "react";
import { api } from "./api";
import type { Settings } from "./types";
import "./OutputTokenSettings.css";

const stages = [
  ["scope_filter", "工作相关性筛选", 32768, 65536, "自动：32,768"],
  ["task_writer", "工作项归并", 32768, 65536, "自动：32,768"],
  ["time_estimator", "工时估算", 16384, 32768, "自动：12,000"],
  [
    "manual_writer",
    "人工事项命名",
    4096,
    8192,
    "自动：按调用使用 200 或 4,000",
  ],
  ["task_merger", "相似工作项合并", 4096, 8192, "自动：1,000"],
  ["style_template", "风格模板生成", 8192, 16384, "自动：4,000"],
  [
    "memory_compressor",
    "对话记忆压缩",
    32768,
    65536,
    "自动：32,768；本地 OpenAI 兼容服务不指定上限",
  ],
] as const;

export function OutputTokenSettings({
  settings,
  onChange,
  busy,
  onSaved,
}: {
  settings: Settings;
  onChange: (settings: Settings) => void;
  busy: boolean;
  onSaved: (budgets: NonNullable<Settings["output_token_budgets"]>) => void;
}) {
  const budgets = settings.output_token_budgets ?? {};
  type Stage = (typeof stages)[number][0];
  type Limit = number | "unlimited" | null;
  const [feedback, setFeedback] = useState({
    state: "idle",
    message: "修改完成后自动保存",
  });
  const [failed, setFailed] = useState<{ stage: Stage; value: Limit }[]>([]);
  const failures = useRef(new Map<Stage, Limit>());
  const queue = useRef(Promise.resolve());
  const sequence = useRef(0);
  const update = (stage: Stage, value: Limit) =>
    onChange({
      ...settings,
      output_token_budgets: { ...budgets, [stage]: value },
    });
  const save = (stage: Stage, value: Limit) => {
    const version = ++sequence.current;
    setFeedback({ state: "saving", message: "正在自动保存…" });

    queue.current = queue.current.then(async () => {
      try {
        const saved = await api<NonNullable<Settings["output_token_budgets"]>>(
          "/settings/output-token-budgets",
          "PATCH",
          { [stage]: value },
        );
        onSaved(saved);
        failures.current.delete(stage);
        setFailed(
          [...failures.current].map(([stage, value]) => ({ stage, value })),
        );
        if (version === sequence.current)
          setFeedback(
            failures.current.size
              ? { state: "error", message: "部分 Token 预算保存失败，请重试。" }
              : { state: "saved", message: "Token 预算已自动保存" },
          );
      } catch (error) {
        failures.current.set(stage, value);
        setFailed(
          [...failures.current].map(([stage, value]) => ({ stage, value })),
        );
        if (version === sequence.current)
          setFeedback({
            state: "error",
            message: `自动保存失败：${(error as Error).message}`,
          });
      }
    });
  };
  const choose = (stage: Stage, value: Limit) => {
    update(stage, value);
    save(stage, value);
  };
  const protocol = (stage: Stage) => {
    if (stage !== "memory_compressor") return settings.llm_protocol;
    const cfg = settings.memory_compression;
    if (cfg?.mode === "connection")
      return settings.llm_connections?.find((c) => c.id === cfg.connection_id)
        ?.protocol;
    return cfg?.protocol;
  };
  return (
    <section aria-label="输出 Token 预算">
      <h3>输出 Token 预算</h3>
      <p className="hint">
        分别设置每次 AI
        请求的输出上限（包含模型思考）。输入完成离开输入框时自动保存，快捷选项立即保存；留空沿用自动值。
      </p>
      <p className="hint">
        建议先用推荐值；若出现截断，可选择较大预设。这里是应用建议，并非模型容量保证，请按所用服务允许的输出和上下文上限调整。提高上限可能增加耗时和费用，不会自动重试已截断的请求。
      </p>
      <p className="hint">
        “无上限”表示应用不指定输出上限，仍受模型和服务端限制。Anthropic
        协议要求具体上限，请使用自动或数值。
      </p>
      <p
        role={feedback.state === "error" ? "alert" : "status"}
        aria-label="Token 预算保存状态"
      >
        {feedback.message}
      </p>
      {failed.length > 0 && (
        <button
          type="button"
          className="outline sm-btn"
          onClick={() => failed.forEach((item) => save(item.stage, item.value))}
        >
          重试保存 Token 预算
        </button>
      )}
      <fieldset className="token-budget-fields" disabled={busy}>
        {stages.map(([stage, label, recommended, larger, automatic]) => (
          <div className="token-budget-row" key={stage}>
            <label>
              {label}输出上限
              <input
                type="number"
                min={128}
                max={262144}
                step={1}
                aria-label={`${label}输出 Token 上限`}
                placeholder={budgets[stage] === "unlimited" ? "无上限" : "自动"}
                value={
                  budgets[stage] === "unlimited" ? "" : (budgets[stage] ?? "")
                }
                onChange={(event) =>
                  update(
                    stage,
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                  )
                }
                onBlur={(event) => {
                  const field = event.currentTarget;
                  if (!field.validity.valid) {
                    setFeedback({
                      state: "error",
                      message: "请输入 128～262144 的整数；当前输入尚未保存。",
                    });
                    return;
                  }
                  if (field.value === "" && budgets[stage] === "unlimited")
                    return;
                  save(stage, field.value === "" ? null : Number(field.value));
                }}
              />
            </label>
            <span className="hint token-budget-auto">{automatic}</span>
            <button
              type="button"
              className="outline sm-btn"
              onClick={() => choose(stage, recommended)}
              aria-label={`${label}使用推荐值`}
            >
              推荐 {recommended.toLocaleString()}
            </button>
            <button
              type="button"
              className="outline sm-btn"
              onClick={() => choose(stage, larger)}
              aria-label={`${label}使用较大预设`}
            >
              较大 {larger.toLocaleString()}
            </button>
            <button
              type="button"
              className="outline sm-btn"
              disabled={protocol(stage) === "anthropic"}
              title={
                protocol(stage) === "anthropic"
                  ? "Anthropic 协议要求具体输出上限"
                  : "由模型服务决定输出上限"
              }
              aria-pressed={budgets[stage] === "unlimited"}
              aria-label={`${label}设为无上限`}
              onClick={() => choose(stage, "unlimited")}
            >
              无上限
            </button>
            <button
              type="button"
              className="outline sm-btn"
              onClick={() => choose(stage, null)}
              aria-label={`${label}恢复自动`}
            >
              自动
            </button>
          </div>
        ))}
      </fieldset>
    </section>
  );
}
