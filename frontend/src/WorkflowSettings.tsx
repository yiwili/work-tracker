import { useEffect, useState } from "react";
import { Bot } from "lucide-react";
import type { Settings } from "./types";
type Agent = { name: string; default: string; effective: string };

export function WorkflowSettings({
  settings: s,
  onChange,
}: {
  settings: Settings;
  onChange: (s: Settings) => void;
}) {
  const [agents, setAgents] = useState<Record<string, Agent>>({});
  useEffect(() => {
    fetch("/api/agents")
      .then((r) => r.json())
      .then(setAgents)
      .catch(() => {});
  }, []);
  return (
    <section className="settings-section">
      <h2>
        <Bot size={19} />
        AI 工作流程与提示词
      </h2>
      <p>
        专用角色分别负责相关性筛选、业务工作项归并、工时估算、人工事项命名与风格提炼。可补充各自偏好；引用校验、隐私过滤和工时守恒仍由程序执行。
      </p>
      {Object.entries(agents).map(([key, agent]) => (
        <details className="agent-prompt" key={key}>
          <summary>{agent.name}</summary>
          <label>
            默认角色提示词
            <textarea readOnly rows={7} value={agent.default} />
          </label>
          <label>
            补充偏好（随设置保存，留空使用默认）
            <textarea
              rows={4}
              aria-label={agent.name + "补充提示词"}
              value={s.agent_prompts[key] || ""}
              onChange={(e) =>
                onChange({
                  ...s,
                  agent_prompts: { ...s.agent_prompts, [key]: e.target.value },
                })
              }
            />
          </label>
        </details>
      ))}
    </section>
  );
}
