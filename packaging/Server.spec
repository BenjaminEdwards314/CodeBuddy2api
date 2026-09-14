# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for CodeBuddy2API-server — the headless instance process.

The desktop .app cannot spawn `web.py` children (its bundle contains neither a
standalone interpreter nor the script file), so multi-instance support needs a
second, self-contained executable that the GUI copies into its bundle.

Build with:
    pyinstaller packaging/Server.spec --noconfirm

Produces: dist/CodeBuddy2API-server
"""
import os

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(os.getcwd()))

datas = [
    (os.path.join(ROOT, "frontend", "admin.html"), "frontend"),
]

# Same runtime needs as the GUI, minus the GUI backend itself.
hiddenimports = collect_submodules("pkg_resources")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "pkg_resources.extern",
    "pkg_resources.extern.appdirs",
    "pkg_resources._vendor",
    "pkg_resources._vendor.appdirs",
    "appdirs",
    "hypercorn",
    "hypercorn.asyncio",
    "hypercorn.config",
    "hypercorn.protocol.h11",
    "h11",
    "h2",
    "wsproto",
    "priority",
    "httpx",
    "httpcore",
    "fastapi",
    "starlette",
    "pydantic",
    "dotenv",
    "json5",
    "aiofiles",
    "multipart",
    "anyio",
    "sniffio",
    "certifi",
    "idna",
    "charset_normalizer",
    "web",
    "config",
    "src.codebuddy_router",
    "src.codebuddy_auth_router",
    "src.codebuddy_token_manager",
    "src.codebuddy_api_client",
    "src.settings_router",
    "src.instances_router",
    "src.instance_manager",
    "src.frontend_router",
    "src.anthropic_router",
    "src.auth",
    "src.keyword_replacer",
    "src.usage_stats_manager",
    "src.models",
]

a = Analysis(
    [os.path.join(ROOT, "packaging", "server_entry.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["webview", "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="CodeBuddy2API-server",
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
)
