import { useEffect, useState } from "react";
import { Bot } from "lucide-react";
import type { Settings } from "./types";
import { apiUrl } from "./api";
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
    fetch(apiUrl("/agents"))
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
