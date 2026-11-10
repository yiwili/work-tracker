import { useEffect, useState } from "react";
import "./themes/classic-preserved.css";
import "./themes/apple-hig.css";

export type ThemeItem = {
  id: "apple-hig" | "apple-dark" | "classic-preserved";
  name: string;
  badge: string;
  description: string;
  removable: boolean;
  file: string;
};

export type ThemeId = ThemeItem["id"];

export const THEMES: readonly ThemeItem[] = [
  {
    id: "apple-hig",
    name: "Apple 极简浅色",
    badge: "默认 · macOS",
    description: "纯净毛玻璃层级与克制留白，符合 Apple HIG 视觉规范",
    removable: false,
    file: "frontend/src/themes/apple-hig.css",
  },
  {
    id: "apple-dark",
    name: "Apple 深邃暗夜",
    badge: "暗色 · macOS",
    description: "低眩光深色毛玻璃界面，适合夜间沉浸式核对",
    removable: true,
    file: "frontend/src/themes/apple-hig.css",
  },
  {
    id: "classic-preserved",
    name: "拾时经典版（原版归档）",
    badge: "本地主题库",
    description: "完整保留改版前的经典界面配色与卡片质感，可随时切换或由 AI 清理移除",
    removable: true,
    file: "frontend/src/themes/classic-preserved.css",
  },
] as const;

const STORAGE_KEY = "awt-theme-id";
const DEFAULT_THEME: ThemeId = "apple-hig";

function isThemeId(value: string | null): value is ThemeId {
  return value !== null && THEMES.some((t) => t.id === value);
}

export function getSavedTheme(): string {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (isThemeId(saved)) return saved;
  } catch {
    // localStorage may be unavailable in restricted contexts
  }
  return DEFAULT_THEME;
}

export function applyTheme(id: string): void {
  const validId: ThemeId = isThemeId(id) ? id : DEFAULT_THEME;
  document.documentElement.dataset.theme = validId;
  try {
    window.localStorage.setItem(STORAGE_KEY, validId);
  } catch {
    // Ignore storage write errors
  }
}

export function useTheme() {
  const [theme, setThemeState] = useState<string>(() => {
    const initial = getSavedTheme();
    if (typeof document !== "undefined") {
      document.documentElement.dataset.theme = initial;
    }
    return initial;
  });

  const setTheme = (nextId: string) => {
    const validId = isThemeId(nextId) ? nextId : DEFAULT_THEME;
    setThemeState(validId);
    applyTheme(validId);
  };

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY && isThemeId(event.newValue)) {
        setThemeState(event.newValue);
        document.documentElement.dataset.theme = event.newValue;
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, []);

  return { theme, setTheme };
}
