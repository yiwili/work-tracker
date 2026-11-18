import type { LlmProtocol, LlmProvider } from "./types";

export const glmModels = [
  { id: "glm-5.3-flash", label: "GLM-5.3-Flash" },
  { id: "glm-5.3-flashx", label: "GLM-5.3-FlashX" },
  { id: "glm-5.3", label: "GLM-5.3" },
  { id: "glm-5.2", label: "GLM-5.2" },
  { id: "glm-5.1", label: "GLM-5.1" },
  { id: "glm-5", label: "GLM-5" },
  { id: "glm-5-turbo", label: "GLM-5-Turbo" },
  { id: "glm-4.7", label: "GLM-4.7" },
  { id: "glm-4.6", label: "GLM-4.6" },
  { id: "glm-4.5", label: "GLM-4.5" },
  { id: "glm-4.5-air", label: "GLM-4.5-Air" },
  { id: "glm-4.5-flash", label: "GLM-4.5-Flash（免费）" },
];

export type ModelPreset = {
  endpoint: string;
  model: string;
  protocol: LlmProtocol;
  models: { id: string; label: string }[];
};

export const presets: Record<LlmProvider, ModelPreset> = {
  deepseek: {
    endpoint: "https://api.deepseek.com/chat/completions",
    model: "deepseek-flash",
    protocol: "openai",
    models: [
      { id: "deepseek-flash", label: "DeepSeek Flash（V4.1）" },
      { id: "deepseek-v4-pro", label: "DeepSeek V4 Pro" },
    ],
  },
  glm: {
    endpoint: "https://open.bigmodel.cn/api/paas/v4/chat/completions",
    model: "glm-5.3-flash",
    protocol: "openai",
    models: glmModels,
  },
  "glm-coding": {
    endpoint: "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
    model: "glm-5.3-flash",
    protocol: "openai",
    models: glmModels.filter((item) => item.id !== "glm-4.5-flash"),
  },
  ccswitch: {
    endpoint: "",
    model: "",
    protocol: "anthropic",
    models: [],
  },
  custom: {
    endpoint: "",
    model: "",
    protocol: "openai",
    models: [],
  },
};

export const providerLabels: Record<LlmProvider, string> = {
  deepseek: "DeepSeek API",
  glm: "智谱 GLM（标准 API）",
  "glm-coding": "智谱 GLM Coding Plan",
  ccswitch: "CCSwitch / Claude Code 当前接口",
  custom: "自定义兼容接口",
};

export function modelLabel(provider: LlmProvider, id: string) {
  return presets[provider].models.find((item) => item.id === id)?.label || id;
}
