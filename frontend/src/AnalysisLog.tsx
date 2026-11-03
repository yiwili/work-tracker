import { useEffect, useState } from "react";

type Progress = {
  state: "idle" | "running" | "success" | "error";
  elapsed_seconds: number;
  logs: { seconds: number; message: string }[];
};

export function AnalysisLog({ day, active }: { day: string; active: boolean }) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setProgress(null);
    setUnavailable(false);
    async function poll() {
      try {
        const response = await fetch(`/api/analysis/progress?date=${day}`, { signal: controller.signal });
        if (!response.ok) throw new Error("progress unavailable");
        const value: Progress = await response.json();
        if (!disposed) { setProgress(value); setUnavailable(false); }
      } catch {
        if (!disposed) setUnavailable(true);
      } finally {
        if (!disposed) timer = setTimeout(poll, 1000);
      }
    }
    void poll();
    return () => { disposed = true; controller.abort(); clearTimeout(timer); };
  }, [day, active]);
  if (!active && (!progress || progress.state === "idle")) return null;
  return (
    <div className="analysis-log" aria-label="AI 分析日志">
      <div className="analysis-log-head">
        <span className="analysis-log-lights" aria-hidden="true">
          <span className="light close" />
          <span className="light minimize" />
          <span className="light expand" />
        </span>
        <strong>AI 分析日志 · {progress?.state === "error" ? "失败" : progress?.state === "success" && !active ? "完成" : "进行中"}
          {progress && ` · 已耗时 ${Math.floor(progress.elapsed_seconds)} 秒`}</strong>
        <span className="analysis-log-signal" aria-hidden="true" />
      </div>
      {unavailable && <p role="status">暂时无法获取进度，正在重连；不会重复发送分析请求。</p>}
      {!progress?.logs.length && <p>正在等待分析开始…</p>}
      <ol>
        {progress?.logs.map((row, index) => (
          <li key={index}><time>+{row.seconds.toFixed(1)}s</time> {row.message}</li>
        ))}
      </ol>
    </div>
  );
}
