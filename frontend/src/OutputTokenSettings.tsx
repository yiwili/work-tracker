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
}: {
  settings: Settings;
  onChange: (settings: Settings) => void;
  busy: boolean;
}) {
  const budgets = settings.output_token_budgets ?? {};
  const setBudget = (stage: (typeof stages)[number][0], value: number | null) =>
    onChange({
      ...settings,
      output_token_budgets: { ...budgets, [stage]: value },
    });
  return (
    <section aria-label="输出 Token 预算">
      <h3>输出 Token 预算</h3>
      <p className="hint">
        分别设置每次 AI
        请求的输出上限（包含模型思考）。留空沿用自动值，修改后点击“保存设置”生效。
      </p>
      <p className="hint">
        建议先用推荐值；若出现截断，可选择较大预设。这里是应用建议，并非模型容量保证，请按所用服务允许的输出和上下文上限调整。提高上限可能增加耗时和费用，不会自动重试已截断的请求。
      </p>
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
                placeholder="自动"
                value={budgets[stage] ?? ""}
                onChange={(event) =>
                  setBudget(
                    stage,
                    event.target.value === ""
                      ? null
                      : Number(event.target.value),
                  )
                }
              />
            </label>
            <span className="hint token-budget-auto">{automatic}</span>
            <button
              type="button"
              className="outline sm-btn"
              onClick={() => setBudget(stage, recommended)}
              aria-label={`${label}使用推荐值`}
            >
              推荐 {recommended.toLocaleString()}
            </button>
            <button
              type="button"
              className="outline sm-btn"
              onClick={() => setBudget(stage, larger)}
              aria-label={`${label}使用较大预设`}
            >
              较大 {larger.toLocaleString()}
            </button>
            <button
              type="button"
              className="outline sm-btn"
              onClick={() => setBudget(stage, null)}
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
