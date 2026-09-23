# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for the CodeBuddy2API macOS desktop application.

Build with:
    pyinstaller packaging/Server.spec --noconfirm          # headless instance server
    pyinstaller packaging/CodeBuddy2API.spec --noconfirm   # the GUI app

Produces: dist/CodeBuddy2API.app

The first command is required for 多开实例 (multi-instance): the GUI app cannot
run web.py children on its own, so the headless server is built separately and
copied into the bundle.
"""
import json
import os
import subprocess
from datetime import date

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(os.getcwd()))


def _git(*args):
    try:
        out = subprocess.run(
            ["git", "-C", ROOT, *args],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


# Stamp the build with its commit so the app can tell the user whether a newer
# upstream commit exists. Written next to the bundled code and read at runtime.
_build_info = {
    "version": "1.1.3",
    "commit": _git("rev-parse", "HEAD"),
    "commit_short": _git("rev-parse", "--short", "HEAD"),
    "build_date": date.today().isoformat(),
}
_build_info_path = os.path.join(ROOT, "build_info.json")
with open(_build_info_path, "w", encoding="utf-8") as _fh:
    json.dump(_build_info, _fh, indent=2)
print(f"Build info: {_build_info}")

# HTML pages are served straight off disk by src/frontend_router.py.
datas = [
    (os.path.join(ROOT, "frontend", "admin.html"), "frontend"),
    (_build_info_path, "."),
]

# Multi-instance support spawns a headless server process per instance. A
# frozen .app has no interpreter and no web.py, so that process ships as a
# second executable built from Server.spec. Build it first; if it is missing we
# still produce a working single-instance app.
_server_binary = os.path.join(ROOT, "dist", "CodeBuddy2API-server")
if os.path.isfile(_server_binary):
    datas.append((_server_binary, "."))
else:
    print("WARNING: dist/CodeBuddy2API-server not found — build packaging/Server.spec "
          "first, otherwise 多开实例 will not work in the packaged app.")

# pywebview resolves its GUI backend dynamically, so pull the whole package in.
hiddenimports = collect_submodules("webview")
hiddenimports += collect_submodules("pkg_resources")
hiddenimports += [
    # pkg_resources needs its vendored modules or the packaged app dies at boot.
    "pkg_resources.extern",
    "pkg_resources.extern.appdirs",
    "pkg_resources._vendor",
    "pkg_resources._vendor.appdirs",
    "appdirs",
    "objc",
    "Foundation",
    "AppKit",
    "WebKit",
    "PyObjCTools",
    "PyObjCTools.AppHelper",
    "Quartz",
    "UniformTypeIdentifiers",
    "bottle",
    "proxy_tools",
    # Uvicorn/hypercorn worker bits that FastAPI pulls in indirectly.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    # App modules are imported lazily at runtime.
    "web",
    "config",
    "src",
    "src.auth",
    "src.frontend_router",
    "src.settings_router",
    "src.proxy_state",
    "src.codebuddy_router",
    "src.codebuddy_auth_router",
    "src.anthropic_router",
    "src.codebuddy_api_client",
    "src.codebuddy_token_manager",
    "src.usage_stats_manager",
    "src.keyword_replacer",
    "src.models",
]

a = Analysis(
    [os.path.join(ROOT, "desktop.py")],
    pathex=[ROOT],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "numpy", "pandas", "PyQt5", "PySide2"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CodeBuddy2API",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # no terminal window
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CodeBuddy2API",
)

app = BUNDLE(
    coll,
    name="CodeBuddy2API.app",
    icon=os.path.join(ROOT, "packaging", "icon.icns"),
    bundle_identifier="com.codebuddy2api.desktop",
    info_plist={
        "CFBundleName": "CodeBuddy2API",
        "CFBundleDisplayName": "CodeBuddy2API",
        "CFBundleShortVersionString": "1.1.3",
        "CFBundleVersion": "1.1.3",
        "NSHighResolutionCapable": True,
        # Needed so the app can talk to its own local HTTP server.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        "LSMinimumSystemVersion": "10.15",
        "LSUIElement": False,
    },
)
