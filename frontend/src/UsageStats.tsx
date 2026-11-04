import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";

type Period = "day" | "week" | "month" | "year" | "all";

type UsageModel = {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  estimated: boolean;
  share: number;
};

type UsageStatsData = {
  period: Period;
  start: string | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  calls: number;
  estimated: boolean;
  models: UsageModel[];
};

const periods: [Period, string][] = [
  ["day", "今天"],
  ["week", "本周"],
  ["month", "本月"],
  ["year", "今年"],
  ["all", "总计"],
];

const colors = [
  "#0a84ff",
  "#af52de",
  "#30d158",
  "#ff9f0a",
  "#ff375f",
  "#64d2ff",
  "#5e5ce6",
  "#bf5af2",
];

const number = (value: number) => value.toLocaleString("zh-CN");

function compact(value: number) {
  if (value >= 100000000) return `${(value / 100000000).toFixed(1).replace(/\.0$/, "")}亿`;
  if (value >= 10000) return `${(value / 10000).toFixed(1).replace(/\.0$/, "")}万`;
  return number(value);
}

function donutBackground(models: UsageModel[]) {
  if (!models.length) return "conic-gradient(#e9e9ee 0% 100%)";
  let cursor = 0;
  const stops = models.map((item, index) => {
    const from = cursor;
    cursor += item.share * 100;
    return `${colors[index % colors.length]} ${from}% ${cursor}%`;
  });
  return `conic-gradient(${stops.join(", ")})`;
}

export function UsageStats() {
  const [period, setPeriod] = useState<Period>("day");
  const [data, setData] = useState<UsageStatsData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    fetch(`/api/usage/stats?period=${period}`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("usage unavailable");
        return response.json() as Promise<UsageStatsData>;
      })
      .then((value) => {
        setData(value);
        setError("");
      })
      .catch(() => {
        if (!controller.signal.aborted) setError("暂时无法读取用量统计，请稍后重试。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [period, reload]);

  useEffect(() => setData(null), [period]);

  return (
    <div className="usage-panel">
      <div className="usage-head">
        <span>按模型统计 token，周期按设置时区划分</span>
        <button
          className="usage-icon"
          type="button"
          aria-label="刷新用量"
          title="刷新"
          onClick={() => setReload((value) => value + 1)}
        >
          <RefreshCw className={loading ? "spin" : ""} size={14} />
        </button>
      </div>
      <div className="usage-periods" role="tablist" aria-label="用量统计周期">
        {periods.map(([key, label]) => (
          <button
            key={key}
            type="button"
            role="tab"
            aria-selected={period === key}
            className={period === key ? "active" : ""}
            onClick={() => setPeriod(key)}
          >
            {label}
          </button>
        ))}
      </div>
      {error ? (
        <p className="usage-note">{error}</p>
      ) : !data ? (
        <p className="usage-note">正在读取用量…</p>
      ) : data.total_tokens === 0 ? (
        <p className="usage-note">这个时间段还没有模型调用记录。</p>
      ) : (
        <>
          <div className="usage-summary">
            <strong>{compact(data.total_tokens)}</strong>
            <span>tokens</span>
          </div>
          <p className="usage-split">
            输入 {number(data.prompt_tokens)} · 输出 {number(data.completion_tokens)} ·{" "}
            {data.calls} 次请求{data.estimated ? "（含估算）" : ""}
          </p>
          <div className="usage-breakdown">
            <div className="usage-donut" style={{ background: donutBackground(data.models) }}>
              <div className="usage-donut-hole">
                <strong>{compact(data.total_tokens)}</strong>
                <span>tokens</span>
              </div>
            </div>
            <ul className="usage-models">
              {data.models.map((item, index) => (
                <li key={item.model}>
                  <i style={{ background: colors[index % colors.length] }} />
                  <div>
                    <span title={item.model}>{item.model}</span>
                    <small>
                      {number(item.total_tokens)} tokens · {Math.round(item.share * 100)}%
                    </small>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </>
      )}
    </div>
  );
}
