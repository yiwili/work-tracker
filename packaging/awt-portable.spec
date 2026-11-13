# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
FRONTEND_DIST = ROOT / "frontend" / "dist"
if not (FRONTEND_DIST / "index.html").is_file():
    raise FileNotFoundError(
        f"Built frontend dist not found at {FRONTEND_DIST}. Run 'npm run build' in frontend/ first."
    )

icon_path = ROOT / "src-tauri" / "icons" / "icon.ico"

datas = [
    (str(ROOT / "awt" / "prompts"), "awt/prompts"),
    (str(ROOT / "awt" / "migrations"), "awt/migrations"),
    (str(FRONTEND_DIST), "frontend/dist"),
]
if icon_path.is_file():
    datas.append((str(icon_path), "icons"))
datas += collect_data_files("tzdata")
datas += collect_data_files("certifi")

hiddenimports = [
    "alembic",
    "alembic.command",
    "alembic.config",
    "alembic.runtime.migration",
    "alembic.script",
    "sqlalchemy.dialects.sqlite",
    "sqlalchemy.sql.default_comparator",
    "zstandard",
    "openpyxl",
    "xlrd",
]
hiddenimports += collect_submodules("awt")
hiddenimports += collect_submodules("uvicorn")

a = Analysis(
    [str(Path(SPECPATH) / "portable_entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "playwright", "tkinter"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-work-tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.is_file() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="ai-work-tracker-portable",
)
