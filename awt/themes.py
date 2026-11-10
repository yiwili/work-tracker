"""Pluggable theme registry and API interface for AI Work Tracker.

Themes are decoupled from business logic: built-in themes provide baseline
manifests, and user/AI overrides can be persisted in ``<data_dir>/themes.json``
or updated via ``/api/themes`` without touching frontend components.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import db

DEFAULT_THEME_ID = "apple-hig"

BUILTIN_THEMES: list[dict[str, Any]] = [
    {
        "id": "apple-hig",
        "name": "Apple 极简浅色",
        "badge": "默认 · 浅色",
        "description": "纯净毛玻璃层级与克制留白，沿用当前清爽浅色视觉规范",
        "base_mode": "light",
        "removable": False,
        "builtin": True,
        "file": "frontend/src/themes/apple-hig.css",
        "preview": {
            "bg": "#f5f5f7",
            "card": "#ffffff",
            "accent": "#0071e3",
            "text": "#1d1d1f",
        },
        "variables": {},
    },
    {
        "id": "apple-dark",
        "name": "Apple 静谧深色",
        "badge": "深色 · 护眼",
        "description": "沿用浅色版布局结构与圆角层级，采用 macOS Sequoia 原生石墨深色与柔光对比度",
        "base_mode": "dark",
        "removable": True,
        "builtin": True,
        "file": "frontend/src/themes/apple-dark.css",
        "preview": {
            "bg": "#14161a",
            "card": "#1d2026",
            "accent": "#2997ff",
            "text": "#f5f5f7",
        },
        "variables": {},
    },
    {
        "id": "classic-preserved",
        "name": "拾时经典版（原版归档）",
        "badge": "本地主题库",
        "description": "完整保留改版前的经典浅色界面配色与卡片质感，可随时切换或独立移除",
        "base_mode": "light",
        "removable": True,
        "builtin": True,
        "file": "frontend/src/themes/classic-preserved.css",
        "preview": {
            "bg": "#f4f6fa",
            "card": "#ffffff",
            "accent": "#0a6cff",
            "text": "#111827",
        },
        "variables": {},
    },
]


def _store_path() -> Path:
    return db.data_dir() / "themes.json"


def _read_store() -> dict[str, Any]:
    path = _store_path()
    if not path.exists():
        return {"overrides": {}, "removed": [], "custom": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {
                "overrides": data.get("overrides", {}) if isinstance(data.get("overrides"), dict) else {},
                "removed": data.get("removed", []) if isinstance(data.get("removed"), list) else [],
                "custom": data.get("custom", []) if isinstance(data.get("custom"), list) else [],
            }
    except Exception:
        pass
    return {"overrides": {}, "removed": [], "custom": []}


def _write_store(store: dict[str, Any]) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def list_themes(active_id: str | None = None) -> dict[str, Any]:
    store = _read_store()
    removed = set(store.get("removed", []))
    overrides = store.get("overrides", {})
    items: list[dict[str, Any]] = []

    for base in BUILTIN_THEMES:
        tid = base["id"]
        if tid in removed and base.get("removable", True):
            continue
        merged = {**base}
        if tid in overrides and isinstance(overrides[tid], dict):
            ov = overrides[tid]
            for key in ("name", "badge", "description", "base_mode"):
                if isinstance(ov.get(key), str) and ov[key].strip():
                    merged[key] = ov[key].strip()
            if isinstance(ov.get("preview"), dict):
                merged["preview"] = {**merged["preview"], **ov["preview"]}
            if isinstance(ov.get("variables"), dict):
                merged["variables"] = {
                    str(k): str(v)
                    for k, v in ov["variables"].items()
                    if str(k).startswith("--")
                }
        items.append(merged)

    for custom in store.get("custom", []):
        if isinstance(custom, dict) and custom.get("id") and custom["id"] not in removed:
            items.append(custom)

    valid_ids = {t["id"] for t in items}
    resolved_active = active_id if active_id in valid_ids else DEFAULT_THEME_ID
    return {
        "active_theme": resolved_active,
        "default_theme": DEFAULT_THEME_ID,
        "themes": items,
    }


def update_theme(theme_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    store = _read_store()
    builtin_ids = {t["id"] for t in BUILTIN_THEMES}
    clean_vars = {}
    if isinstance(payload.get("variables"), dict):
        clean_vars = {
            str(k).strip(): str(v).strip()
            for k, v in payload["variables"].items()
            if str(k).strip().startswith("--") and len(str(v)) <= 200
        }

    if theme_id in builtin_ids:
        current_ov = store["overrides"].get(theme_id, {})
        if not isinstance(current_ov, dict):
            current_ov = {}
        for k in ("name", "badge", "description", "base_mode"):
            if isinstance(payload.get(k), str) and payload[k].strip():
                current_ov[k] = payload[k].strip()[:200]
        if isinstance(payload.get("preview"), dict):
            current_ov["preview"] = payload["preview"]
        if "variables" in payload:
            current_ov["variables"] = clean_vars
        store["overrides"][theme_id] = current_ov
        if theme_id in store["removed"]:
            store["removed"].remove(theme_id)
        _write_store(store)
    else:
        custom_list = [c for c in store["custom"] if isinstance(c, dict) and c.get("id") != theme_id]
        custom_list.append({
            "id": theme_id,
            "name": str(payload.get("name") or theme_id)[:100],
            "badge": str(payload.get("badge") or "自定义主题")[:50],
            "description": str(payload.get("description") or "通过主题接口注册的自定义主题")[:300],
            "base_mode": "dark" if payload.get("base_mode") == "dark" else "light",
            "removable": True,
            "builtin": False,
            "file": "data/themes.json",
            "preview": payload.get("preview") if isinstance(payload.get("preview"), dict) else {
                "bg": "#f5f5f7",
                "card": "#ffffff",
                "accent": "#0071e3",
                "text": "#1d1d1f",
            },
            "variables": clean_vars,
        })
        store["custom"] = custom_list
        _write_store(store)

    catalog = list_themes()
    return next((t for t in catalog["themes"] if t["id"] == theme_id), catalog["themes"][0])


def delete_theme(theme_id: str) -> dict[str, Any]:
    if theme_id == DEFAULT_THEME_ID:
        raise ValueError("默认浅色主题不可删除")
    store = _read_store()
    if theme_id not in store["removed"]:
        store["removed"].append(theme_id)
    store["custom"] = [c for c in store["custom"] if isinstance(c, dict) and c.get("id") != theme_id]
    _write_store(store)
    return list_themes()
