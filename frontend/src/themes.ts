import React, { useEffect, useState } from "react";
import { Check, Palette } from "lucide-react";
import { apiUrl } from "./api";
import "./themes/classic-preserved.css";
import "./themes/apple-hig.css";
import "./themes/apple-dark.css";

export type ThemePreview = {
  bg: string;
  card: string;
  accent: string;
  text: string;
};

export type ThemeItem = {
  id: string;
  name: string;
  badge: string;
  description: string;
  base_mode?: "light" | "dark";
  removable: boolean;
  builtin?: boolean;
  file: string;
  preview?: ThemePreview;
  variables?: Record<string, string>;
};

export type ThemeId = string;

export const THEMES: readonly ThemeItem[] = [
  {
    id: "apple-hig",
    name: "Apple 极简浅色",
    badge: "默认 · 浅色",
    description: "纯净毛玻璃层级与克制留白，沿用当前清爽浅色视觉规范",
    base_mode: "light",
    removable: false,
    builtin: true,
    file: "frontend/src/themes/apple-hig.css",
    preview: {
      bg: "#f5f5f7",
      card: "#ffffff",
      accent: "#0071e3",
      text: "#1d1d1f",
    },
    variables: {},
  },
  {
    id: "apple-dark",
    name: "Apple 静谧深色",
    badge: "深色 · 护眼",
    description: "沿用浅色版布局结构与圆角层级，采用 macOS Sequoia 原生石墨深色与柔光对比度",
    base_mode: "dark",
    removable: true,
    builtin: true,
    file: "frontend/src/themes/apple-dark.css",
    preview: {
      bg: "#131519",
      card: "#1c1f26",
      accent: "#2997ff",
      text: "#f2f4f8",
    },
    variables: {},
  },
  {
    id: "classic-preserved",
    name: "拾时经典版（原版归档）",
    badge: "本地主题库",
    description: "完整保留改版前的经典浅色界面配色与卡片质感，可随时切换或独立移除",
    base_mode: "light",
    removable: true,
    builtin: true,
    file: "frontend/src/themes/classic-preserved.css",
    preview: {
      bg: "#f4f6fa",
      card: "#ffffff",
      accent: "#0a6cff",
      text: "#111827",
    },
    variables: {},
  },
] as const;

const STORAGE_KEY = "awt-theme-id";
const DEFAULT_THEME = "apple-hig";
let appliedCustomVars: string[] = [];

export function getSavedTheme(): string {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && saved.trim()) return saved.trim();
  } catch {
    // localStorage may be unavailable in restricted contexts
  }
  return DEFAULT_THEME;
}

export function applyThemeManifest(
  id: string,
  catalog: readonly ThemeItem[] = THEMES,
): void {
  if (typeof document === "undefined") return;
  const matched = catalog.find((t) => t.id === id) ?? catalog[0] ?? THEMES[0];
  const resolvedId = matched.id;

  // Map custom themes without dedicated CSS files to their base_mode sheet
  const domThemeAttr =
    resolvedId === "apple-hig" ||
    resolvedId === "apple-dark" ||
    resolvedId === "classic-preserved"
      ? resolvedId
      : matched.base_mode === "dark"
        ? "apple-dark"
        : "apple-hig";

  document.documentElement.dataset.theme = domThemeAttr;
  document.documentElement.dataset.themeId = resolvedId;

  // Clear previously injected custom CSS variables
  for (const key of appliedCustomVars) {
    document.documentElement.style.removeProperty(key);
  }
  appliedCustomVars = [];

  if (matched.variables) {
    for (const [k, v] of Object.entries(matched.variables)) {
      if (k.startsWith("--") && typeof v === "string") {
        document.documentElement.style.setProperty(k, v);
        appliedCustomVars.push(k);
      }
    }
  }

  try {
    window.localStorage.setItem(STORAGE_KEY, resolvedId);
  } catch {
    // Ignore storage write errors
  }
}

export function applyTheme(id: string): void {
  applyThemeManifest(id, THEMES);
}

export function useTheme() {
  const [themes, setThemes] = useState<readonly ThemeItem[]>(THEMES);
  const [theme, setThemeState] = useState<string>(() => {
    const initial = getSavedTheme();
    if (typeof document !== "undefined") {
      applyThemeManifest(initial, THEMES);
    }
    return initial;
  });

  useEffect(() => {
    let cancelled = false;
    fetch(apiUrl("/themes"))
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (cancelled || !data || !Array.isArray(data.themes)) return;
        const nextCatalog: ThemeItem[] = data.themes;
        setThemes(nextCatalog);
        const savedLocal = getSavedTheme();
        const candidate =
          nextCatalog.find((t) => t.id === savedLocal)?.id ??
          (typeof data.active_theme === "string" ? data.active_theme : DEFAULT_THEME);
        setThemeState(candidate);
        applyThemeManifest(candidate, nextCatalog);
      })
      .catch(() => {
        // Fallback to local built-in themes when offline
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const setTheme = (nextId: string) => {
    const valid = themes.find((t) => t.id === nextId)?.id ?? DEFAULT_THEME;
    setThemeState(valid);
    applyThemeManifest(valid, themes);
    fetch(apiUrl("/themes/active"), {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ theme_id: valid }),
    }).catch(() => {
      // Ignore network errors; local storage already persisted
    });
  };

  useEffect(() => {
    applyThemeManifest(theme, themes);
  }, [theme, themes]);

  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (event.key === STORAGE_KEY && event.newValue) {
        setThemeState(event.newValue);
        applyThemeManifest(event.newValue, themes);
      }
    };
    window.addEventListener("storage", sync);
    return () => window.removeEventListener("storage", sync);
  }, [themes]);

  return { theme, themes, setTheme };
}

export function ThemeLibrarySection({
  theme,
  onChange,
}: {
  theme: string;
  onChange: (theme: string) => void;
}) {
  const { themes } = useTheme();
  const list = themes.length > 0 ? themes : THEMES;

  return React.createElement(
    "section",
    { className: "settings-section theme-library-section" },
    React.createElement(
      "h2",
      null,
      React.createElement(Palette, { size: 19 }),
      " 界面主题（接口化主题库）",
    ),
    React.createElement(
      "p",
      { className: "muted" },
      "主题已与业务代码解耦并通过独立样式表与 /api/themes 接口挂载。浅色风格保持默认纯净观感，深色风格沿用同等层级结构与低眩光配色。",
    ),
    React.createElement(
      "div",
      { className: "theme-library-grid" },
      list.map((item) => {
        const active = theme === item.id;
        const previewStyle = item.preview
          ? {
              background: item.preview.bg,
              borderColor:
                item.base_mode === "dark"
                  ? "rgba(255,255,255,0.14)"
                  : "rgba(15,23,42,0.1)",
            }
          : undefined;
        return React.createElement(
          "button",
          {
            key: item.id,
            type: "button",
            className: "theme-library-card" + (active ? " active" : ""),
            "aria-pressed": active,
            onClick: () => onChange(item.id),
          },
          React.createElement(
            "span",
            {
              className: `theme-library-preview preview-${item.id}`,
              style: previewStyle,
              "aria-hidden": "true",
            },
            React.createElement("i", {
              style: item.preview ? { background: item.preview.card } : undefined,
            }),
            React.createElement("i", {
              style: item.preview ? { background: item.preview.accent } : undefined,
            }),
            React.createElement("i", {
              style: item.preview ? { background: item.preview.text } : undefined,
            }),
          ),
          React.createElement(
            "span",
            { className: "theme-library-copy" },
            React.createElement(
              "span",
              { className: "theme-card-head" },
              React.createElement("strong", null, item.name),
              React.createElement("span", { className: "theme-badge" }, item.badge),
            ),
            React.createElement("small", null, item.description),
            React.createElement(
              "code",
              { className: "theme-file-hint" },
              `${item.file} · ${item.removable ? "可独立替换/移除" : "默认基准主题"}`,
            ),
          ),
          active ? React.createElement(Check, { size: 16 }) : null,
        );
      }),
    ),
  );
}
