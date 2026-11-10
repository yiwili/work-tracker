import { useEffect, useRef, useState } from "react";
import { Brain, ChevronDown, LoaderCircle } from "lucide-react";
import { apiUrl } from "./api";

type Tokens = {
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated: boolean;
};

type Progress = {
  state: "idle" | "running" | "success" | "error";
  elapsed_seconds: number;
  logs: { seconds: number; message: string }[];
  thinking?: string;
  thinking_live?: boolean;
  model?: string;
  thinking_seconds?: number;
  tokens?: Tokens;
};

const number = (value: number) => value.toLocaleString("zh-CN");

export function AnalysisLog({
  day,
  active,
  endpoint = "/api/analysis/progress",
  label = "AI 思考与输出",
  runningLabel = "AI 正在分析",
  doneLabel = "AI 分析已完成",
  prepareLabel = "正在准备分析…",
}: {
  day: string;
  active: boolean;
  endpoint?: string;
  label?: string;
  runningLabel?: string;
  doneLabel?: string;
  prepareLabel?: string;
}) {
  const [progress, setProgress] = useState<Progress | null>(null);
  const [unavailable, setUnavailable] = useState(false);
  const [manual, setManual] = useState<boolean | null>(null);
  const thinkingBody = useRef<HTMLDivElement>(null);
  useEffect(() => {
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    setProgress(null);
    setUnavailable(false);
    async function poll() {
      let running = false;
      try {
        const response = await fetch(apiUrl(`${endpoint}?date=${day}`), {
          signal: controller.signal,
        });
        if (!response.ok) throw new Error("progress unavailable");
        const value: Progress = await response.json();
        if (disposed) return;
        setProgress(value);
        setUnavailable(false);
        running = value.state === "running";
      } catch {
        if (!disposed) setUnavailable(true);
      } finally {
        // Keep following a server-side run even if the page has not yet
        // refreshed its own "running" flag; stop after the final snapshot.
        if (!disposed && (active || running))
          timer = setTimeout(poll, 500);
      }
    }
    void poll();
    return () => {
      disposed = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [day, active, endpoint]);
  // A new day or a new run starts with the automatic expand/collapse behavior.
  useEffect(() => setManual(null), [day]);
  const previousState = useRef(progress?.state);
  useEffect(() => {
    if (previousState.current !== "running" && progress?.state === "running")
      setManual(null);
    previousState.current = progress?.state;
  }, [progress?.state]);

  const thinking = progress?.thinking || "";
  const running = active || progress?.state === "running";
  const thinkingLive = Boolean(progress?.thinking_live);
  // Open while a model request is reasoning; fold up when that thinking ends.
  // Previous thinking stays until the next request actually starts reasoning.
  const expanded = manual ?? (thinkingLive && Boolean(thinking));
  useEffect(() => {
    if (!expanded) return;
    const element = thinkingBody.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [thinking, expanded]);
  if (!active && (!progress || progress.state === "idle")) return null;
  const tokens = progress?.tokens;
  const thinkingSeconds = progress?.thinking_seconds ?? 0;
  return (
    <div
      className={"analysis-live" + (running ? " running" : "")}
      aria-label={label}
    >
      <div className="analysis-live-head">
        <span className="analysis-live-state">
          {running ? (
            <LoaderCircle className="spin" size={15} />
          ) : (
            <Brain size={15} />
          )}
          {running ? runningLabel : doneLabel}
        </span>
        {tokens && tokens.total_tokens > 0 && (
          <span className="analysis-token-inline">
            输入 {number(tokens.prompt_tokens)} · 输出{" "}
            {number(tokens.completion_tokens)} · 合计{" "}
            {number(tokens.total_tokens)} tokens
            {tokens.estimated && <em>实时估算</em>}
          </span>
        )}
        {progress?.model && (
          <span className="analysis-live-model">{progress.model}</span>
        )}
      </div>
      {(thinking || running) && (
        <button
          className={"thinking-toggle" + (expanded ? " open" : "")}
          type="button"
          disabled={!thinking}
          aria-expanded={expanded}
          onClick={() => setManual(!expanded)}
        >
          {thinkingLive ? (
            <LoaderCircle className="spin" size={14} />
          ) : (
            <Brain size={14} />
          )}
          <span>
            {thinking
              ? thinkingLive
                ? `思考中… ${Math.max(1, Math.ceil(thinkingSeconds))} 秒`
                : `已思考（用时 ${Math.max(1, Math.round(thinkingSeconds))} 秒）`
              : prepareLabel}
          </span>
          {thinking && (
            <ChevronDown className="thinking-chevron" size={15} />
          )}
        </button>
      )}
      {expanded && thinking && (
        <div className="thinking-body" ref={thinkingBody}>
          {thinking}
        </div>
      )}
      {unavailable && (
        <p className="analysis-live-note">
          暂时无法获取进度，正在重连；不会重复发送分析请求。
        </p>
      )}
      {progress?.state === "error" && progress.logs.length > 0 && (
        <p className="analysis-live-note">
          {progress.logs.at(-1)?.message}
        </p>
      )}
    </div>
  );
}
