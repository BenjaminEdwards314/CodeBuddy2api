"""
CodeBuddy2API Desktop Application

A native desktop shell around the existing FastAPI service:
  - Runs the hypercorn server on a background thread (in the same process).
  - Shows the existing admin panel inside a native WebView window.
  - Lets the user configure port / API Key / password from the desktop window.
  - Restarts the server automatically when the port changes.

Usage:
    python desktop.py
"""
import asyncio
import json
import logging
import os
import socket
import sys
import threading
import time
from typing import Optional

# --- Runtime data directory ------------------------------------------------
# Must run BEFORE `import config`, because config.py loads its settings at
# import time from relative paths (config/config.json, .codebuddy_creds).
# Inside a frozen .app the bundle is read-only with an unpredictable working
# directory, so we relocate to a writable per-user directory and keep using
# relative paths from there.
APP_NAME = "CodeBuddy2API"

# --- Version / update check ------------------------------------------------
APP_VERSION = "1.0.0"

# Where update information is read from. The project publishes no GitHub
# releases or tags, so the latest commit on the default branch is the only
# meaningful signal.
UPSTREAM_REPO = "Sliverkiss/CodeBuddy2api"
UPSTREAM_BRANCH = "main"
UPSTREAM_COMMITS_API = (
    f"https://api.github.com/repos/{UPSTREAM_REPO}/commits/{UPSTREAM_BRANCH}"
)
UPSTREAM_REPO_URL = f"https://github.com/{UPSTREAM_REPO}"
UPDATE_TIMEOUT = 8.0


def get_build_info() -> dict:
    """Version metadata for this build, written at packaging time."""
    info = {
        "version": APP_VERSION,
        "commit": "",
        "commit_short": "",
        "build_date": "",
    }
    path = _version_file()
    if path and os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                info.update(json.load(fh))
        except Exception:
            logging.getLogger("desktop").debug(
                "Could not read build info", exc_info=True
            )
    return info


def _version_file() -> Optional[str]:
    """Locate build_info.json, both frozen and from source."""
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = os.path.join(bundle, "build_info.json")
        if os.path.isfile(candidate):
            return candidate
    # Running from source: read the file next to this module.
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_info.json")


def _is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def get_data_dir() -> str:
    """Writable directory holding config.json and credentials."""
    if _is_frozen():
        base = os.path.expanduser(f"~/Library/Application Support/{APP_NAME}")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(os.path.join(base, "config"), exist_ok=True)
    return base


def _bootstrap_data_dir() -> None:
    """Point the process at the writable data directory."""
    data_dir = get_data_dir()
    os.chdir(data_dir)
    # Make the bundled frontend files reachable when running from source.
    if _is_frozen() and getattr(sys, "_MEIPASS", None):
        os.environ.setdefault("CODEBUDDY_BUNDLE_DIR", sys._MEIPASS)


# Settings that are worth carrying over from an existing .env when the desktop
# app starts with no configuration of its own.
_IMPORTABLE_KEYS = (
    "CODEBUDDY_PASSWORD",
    "CODEBUDDY_API_KEY",
    "CODEBUDDY_AUTH_MODE",
    "CODEBUDDY_INTERNET_ENVIRONMENT",
    "CODEBUDDY_API_ENDPOINT",
    "CODEBUDDY_MODELS",
    "CODEBUDDY_LOG_LEVEL",
    "CODEBUDDY_ROTATION_COUNT",
)


def _import_env_if_needed() -> None:
    """Seed the app's config from an existing .env on first run.

    The packaged app keeps its settings in its own data directory, so a user
    who already configured the source checkout would otherwise face an empty
    setup form. Importing once saves them re-typing the password and API key.
    A port is deliberately NOT imported: the .env port may be the one the
    source service is already using.
    """
    data_dir = get_data_dir()
    if os.path.exists(os.path.join(data_dir, "config", "config.json")):
        return  # already configured, never overwrite the user's settings

    env_path = _find_source_env()
    if not env_path:
        return

    try:
        values = _parse_env_file(env_path)
    except Exception:
        logging.getLogger("desktop").warning(
            f"Could not read {env_path}", exc_info=True
        )
        return

    imported = {k: v for k, v in values.items() if k in _IMPORTABLE_KEYS and v}
    if not imported:
        return

    os.makedirs(os.path.join(data_dir, "config"), exist_ok=True)
    with open(os.path.join(data_dir, "config", "config.json"), "w",
              encoding="utf-8") as fh:
        json.dump(imported, fh, indent=4, ensure_ascii=False)
    logging.getLogger("desktop").info(
        f"Imported {len(imported)} setting(s) from {env_path}"
    )


def _find_source_env() -> Optional[str]:
    """Look for a .env next to the bundle or in common checkout locations."""
    candidates = []
    if _is_frozen():
        # sys.executable is <...>/CodeBuddy2API.app/Contents/MacOS/CodeBuddy2API.
        # Walk up to the directory holding the .app, and also check beside it.
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        app_dir = os.path.abspath(os.path.join(exe_dir, "..", "..", ".."))
        parent = os.path.dirname(app_dir)
        candidates.append(os.path.join(app_dir, ".env"))
        candidates.append(os.path.join(parent, ".env"))
    else:
        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        )
    candidates.append(os.path.expanduser("~/.codebuddy2api.env"))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def _parse_env_file(path: str) -> dict:
    """Minimal KEY=VALUE parser (avoids importing dotenv this early)."""
    values = {}
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    return values


_bootstrap_data_dir()
_import_env_if_needed()

_bootstrap_data_dir()

import config
from config import (
    get_server_host,
    get_server_port,
    get_server_password,
    get_codebuddy_api_key,
    update_settings,
)

logger = logging.getLogger("desktop")

# --- Tunables ---
STARTUP_TIMEOUT = 20.0   # seconds to wait for the server to answer
SHUTDOWN_TIMEOUT = 5.0   # seconds to wait for a graceful stop
POLL_INTERVAL = 0.15


# --------------------------------------------------------------------------
# Embedded server
# --------------------------------------------------------------------------

class EmbeddedServer:
    """Runs the FastAPI app with hypercorn on a dedicated background thread.

    The server can be started, stopped and restarted so that a port change
    takes effect without relaunching the desktop application.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._shutdown_event: Optional[asyncio.Event] = None
        self._ready = threading.Event()
        self._error: Optional[BaseException] = None
        self.host = get_server_host()
        self.port = get_server_port()

    # -- state -------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def error(self) -> Optional[BaseException]:
        return self._error

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def check_port_available(host: str, port: int) -> bool:
        """Return True when the port can be bound for listening."""
        bind_host = "" if host in ("0.0.0.0", "::") else host
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((bind_host, port))
                return True
            except OSError:
                return False

    def wait_until_ready(self, timeout: float = STARTUP_TIMEOUT) -> bool:
        """Wait for the server to accept connections."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._error is not None:
                return False
            try:
                with socket.create_connection((self.host, self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(POLL_INTERVAL)
        return False

    # -- lifecycle ---------------------------------------------------------
    def start(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Start the server thread. Returns True once it accepts connections."""
        if self.running:
            return True

        if host is not None:
            self.host = host
        if port is not None:
            self.port = port

        if not self.check_port_available(self.host, self.port):
            self._error = OSError(
                f"端口 {self.port} 已被占用，请更换端口后重试。"
            )
            return False

        self._error = None
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="codebuddy-server", daemon=True
        )
        self._thread.start()

        if not self.wait_until_ready():
            self.stop()
            return False
        return True

    def _run(self) -> None:
        """Thread entry point: own an event loop and serve until asked to stop."""
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:  # surfaced to the caller via .error
            self._error = exc
            logger.error(f"Server thread stopped with error: {exc}")
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                pass
            loop.close()
            self._loop = None

    async def _serve(self) -> None:
        from hypercorn.asyncio import serve
        from hypercorn.config import Config as HypercornConfig

        import web  # imported here so the server owns its FastAPI app

        self._shutdown_event = asyncio.Event()

        hc_config = HypercornConfig()
        hc_config.bind = [f"{self.host}:{self.port}"]
        hc_config.accesslog = None
        hc_config.errorlog = "-"
        hc_config.loglevel = "INFO"
        hc_config.use_colors = False

        logger.info(f"Server listening on {self.base_url}")
        self._ready.set()
        await serve(web.app, hc_config, shutdown_trigger=self._shutdown_event.wait)

    def stop(self) -> None:
        """Ask the server to shut down and wait for the thread to exit."""
        if self._loop is not None and self._shutdown_event is not None:
            try:
                self._loop.call_soon_threadsafe(self._shutdown_event.set)
            except RuntimeError:
                pass

        if self._thread is not None:
            self._thread.join(timeout=SHUTDOWN_TIMEOUT)
            if self._thread.is_alive():
                logger.warning("Server thread did not stop within the timeout.")
            self._thread = None

    def restart(self, host: Optional[str] = None, port: Optional[int] = None) -> bool:
        """Stop the server and start it again, optionally on a new port."""
        self.stop()
        # Give the OS a moment to release the socket.
        time.sleep(0.3)
        return self.start(host=host, port=port)


# --------------------------------------------------------------------------
# Desktop bridge (exposed to JavaScript as window.pywebview.api)
# --------------------------------------------------------------------------

class DesktopApi:
    """Configuration API consumed by the bootstrap page and the admin panel.

    NOTE: pywebview reflects over *public* attributes and exposes every one of
    them to JavaScript. `server` and `window` are therefore kept private —
    exposing them breaks the JS bridge, because pywebview cannot serialize the
    live server object.
    """

    def __init__(self, server: EmbeddedServer, window=None) -> None:
        self._server = server
        self._window = window

    # -- reads -------------------------------------------------------------
    def get_config(self) -> dict:
        """Return the settings relevant to the desktop window.

        Port/host come from the *live* server so the form always shows the
        address actually being served, even if config and socket disagree.
        """
        api_key = get_codebuddy_api_key() or ""
        return {
            "host": self._server.host,
            "port": self._server.port,
            "password": get_server_password() or "",
            "api_key": api_key,
            "api_key_masked": _mask(api_key),
            "auth_mode": config.get_codebuddy_auth_mode(),
            "configured": is_configured(),
            "running": self._server.running,
            "url": self._server.base_url,
        }

    def get_status(self) -> dict:
        """Lightweight status poll used by the admin panel header."""
        return {
            "running": self._server.running,
            "url": self._server.base_url,
            "port": self._server.port,
        }

    # -- writes ------------------------------------------------------------
    def save_config(self, port, api_key=None, password=None, auth_mode=None) -> dict:
        """Validate and persist settings, then restart if the port changed.

        Returns {"ok": bool, "message": str, "restarted": bool, "url": str}
        """
        # --- validate port ---
        try:
            port_int = int(str(port).strip())
        except (TypeError, ValueError):
            return self._fail("端口必须是数字。")

        if not (1 <= port_int <= 65535):
            return self._fail("端口范围必须在 1 - 65535 之间。")

        api_key = (api_key or "").strip()
        password = (password or "").strip()

        if not password:
            return self._fail("访问密码不能为空。")

        mode = (auth_mode or config.get_codebuddy_auth_mode() or "api_key").strip().lower()
        if mode not in ("api_key", "token"):
            return self._fail("启动模式只能是 api_key 或 token。")
        if mode == "api_key" and not api_key:
            return self._fail("API Key 模式下必须填写 API Key。")
        if mode == "token" and not _token_credential_count():
            return self._fail("Token 模式下至少需要一个凭证，请先到「凭证管理」添加。")

        # Reject a port that someone else already holds (only when changing it).
        port_changed = port_int != self._server.port
        if port_changed and not self._server.check_port_available(
            self._server.host, port_int
        ):
            return self._fail(f"端口 {port_int} 已被占用，请更换端口。")

        # Switching auth mode changes how requests are authenticated, so the
        # service has to come back up with the new setting.
        mode_changed = mode != config.get_codebuddy_auth_mode()


        # --- persist ---
        try:
            update_settings({
                "CODEBUDDY_PORT": port_int,
                "CODEBUDDY_API_KEY": api_key,
                "CODEBUDDY_PASSWORD": password,
                "CODEBUDDY_AUTH_MODE": mode,
            })
        except Exception as exc:
            logger.exception("Failed to persist settings")
            return self._fail(f"保存失败：{exc}")

        # --- restart when the listener has to move ---
        restarted = False
        if port_changed or mode_changed:
            logger.info(
                f"Restarting (port {self._server.port} -> {port_int}, "
                f"mode {'changed' if mode_changed else 'same'})"
            )
            if not self._server.restart(port=port_int):
                reason = self._server.error or "未知错误"
                return self._fail(f"服务在端口 {port_int} 上启动失败：{reason}")
            restarted = True

        # Navigation back to the panel is driven by the page itself, *after* it
        # receives this result. Reloading here would tear down the page while
        # the JS promise callback is still pending, which pywebview reports as
        # a console error.
        url = self._server.base_url
        return {
            "ok": True,
            "restarted": restarted,
            "url": url,
            "message": (
                f"设置已保存，服务已在新端口 {port_int} 重启。"
                if restarted else "设置已保存。"
            ),
        }

    def restart_server(self) -> dict:
        """Manually restart the service on the current port."""
        if not self._server.restart():
            reason = self._server.error or "未知错误"
            return self._fail(f"重启失败：{reason}")
        return {
            "ok": True,
            "restarted": True,
            "url": self._server.base_url,
            "message": "服务已重启。",
        }

    def open_admin(self) -> dict:
        """Navigate the window to the admin panel."""
        if self._window is not None:
            self._window.load_url(f"{self._server.base_url}/")
        return {"ok": True, "url": self._server.base_url}

    def get_version(self) -> dict:
        """Version info for this build."""
        info = get_build_info()
        info["repo_url"] = UPSTREAM_REPO_URL
        return info

    def check_update(self) -> dict:
        """Compare this build against the latest upstream commit.

        Reports only; nothing is downloaded or replaced. This project has no
        GitHub releases or tags, so the newest commit on the default branch is
        the only available signal.

        A plain inequality test is not enough: a fork may legitimately be
        *ahead* of upstream. Direction is determined with the compare API, and
        when that is inconclusive we say so rather than guessing.
        """
        local = get_build_info()
        local_commit = (local.get("commit") or "").strip().lower()
        result = {
            "ok": True,
            "current": local,
            "repo_url": UPSTREAM_REPO_URL,
            "compare_url": f"{UPSTREAM_REPO_URL}/commits/{UPSTREAM_BRANCH}",
        }

        remote = self._fetch_upstream_head()
        if remote is None:
            result.update({
                "ok": False,
                "status": "error",
                "message": "无法连接 GitHub 检查更新，请确认网络后重试。",
            })
            return result

        result["remote"] = remote
        remote_commit = remote["commit"]

        if not local_commit:
            result.update({
                "status": "unknown",
                "message": (
                    f"此构建未记录版本信息，无法比较。"
                    f"上游最新提交：{remote['commit_short']}（{remote['date']}）"
                ),
            })
            return result

        if remote_commit == local_commit:
            result.update({
                "status": "latest",
                "message": f"已是最新版本（{local.get('commit_short')}）。",
            })
            return result

        relation = self._compare_commits(local_commit, remote_commit)
        if relation == "behind":
            result.update({
                "status": "outdated",
                "message": (
                    f"发现新版本：{remote['commit_short']}"
                    f"（{remote['date']}）{remote['message']}"
                ),
            })
        elif relation == "ahead":
            result.update({
                "status": "ahead",
                "message": (
                    f"当前构建（{local.get('commit_short')}）比上游更新，"
                    f"无需更新。上游最新：{remote['commit_short']}。"
                ),
            })
        elif relation == "diverged":
            result.update({
                "status": "outdated",
                "message": (
                    f"本地与上游已分叉。上游最新：{remote['commit_short']}"
                    f"（{remote['date']}）"
                ),
            })
        else:
            result.update({
                "status": "unknown",
                "message": (
                    f"与上游提交不同，但无法判断新旧方向。"
                    f"上游最新：{remote['commit_short']}（{remote['date']}）"
                ),
            })
        return result

    def _fetch_upstream_head(self) -> Optional[dict]:
        """Latest commit on the upstream default branch, or None on failure."""
        try:
            import urllib.request

            request = urllib.request.Request(
                UPSTREAM_COMMITS_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request, timeout=UPDATE_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            logger.warning(f"Update check failed: {exc}")
            return None

        commit_info = payload.get("commit") or {}
        sha = (payload.get("sha") or "").strip().lower()
        return {
            "commit": sha,
            "commit_short": sha[:7],
            "message": (commit_info.get("message") or "").splitlines()[0],
            "date": ((commit_info.get("committer") or {}).get("date") or "")[:10],
        }

    def _compare_commits(self, base: str, head: str) -> str:
        """Return 'ahead', 'behind', 'diverged' or 'unknown'.

        GitHub's compare API 404s when the base commit is not published
        anywhere (a local-only build). In that case fall back to a local git
        check when the source checkout is available, and otherwise admit that
        the direction cannot be determined.
        """
        url = f"https://api.github.com/repos/{UPSTREAM_REPO}/compare/{base}...{head}"
        try:
            import urllib.request

            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"{APP_NAME}/{APP_VERSION}",
                },
            )
            with urllib.request.urlopen(request, timeout=UPDATE_TIMEOUT) as resp:
                payload = json.loads(resp.read().decode("utf-8"))

            status = (payload.get("status") or "").lower()
            if status in ("behind", "ahead", "diverged"):
                return status
            return "unknown"
        except Exception as exc:
            logger.info(f"GitHub comparison unavailable: {exc}")

        return self._compare_commits_locally(base, head)

    def _compare_commits_locally(self, base: str, head: str) -> str:
        """Use the local git checkout to decide direction, if we have one."""
        repo = os.path.dirname(os.path.abspath(__file__))
        if _is_frozen() or not os.path.isdir(os.path.join(repo, ".git")):
            return "unknown"
        try:
            import subprocess

            def run(*args):
                return subprocess.run(
                    ["git", "-C", repo, *args],
                    capture_output=True, text=True, timeout=10,
                )

            # head is an ancestor of base -> we are ahead of upstream.
            if run("merge-base", "--is-ancestor", head, base).returncode == 0:
                return "ahead"
            # base is an ancestor of head -> we are behind upstream.
            if run("merge-base", "--is-ancestor", base, head).returncode == 0:
                return "behind"
        except Exception as exc:
            logger.info(f"Local git comparison failed: {exc}")
        return "unknown"

    def open_url(self, url: str) -> dict:
        """Open a link in the user's default browser.

        Restricted to the project repo and the CodeBuddy login domains, so a
        compromised page cannot use this as a generic "open anything" bridge.
        """
        allowed = (
            UPSTREAM_REPO_URL,
            "https://github.com/",
            "https://www.codebuddy.ai/",
            "https://www.codebuddy.cn/",
            "https://copilot.tencent.com/",
        )
        if not isinstance(url, str) or not url.startswith(allowed):
            return {"ok": False, "message": "该链接不在允许打开的范围内。"}
        try:
            import webbrowser

            webbrowser.open(url)
            return {"ok": True}
        except Exception as exc:
            logger.warning(f"Could not open URL: {exc}")
            return {"ok": False, "message": "无法打开浏览器。"}

    def _fail(self, message: str) -> dict:
        return {"ok": False, "message": message, "restarted": False,
                "url": self._server.base_url}


def _mask(value: str) -> str:
    """Mask a secret, keeping just enough to recognise it."""
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * 6}{value[-4:]}"


def _token_credential_count() -> int:
    """How many credentials the token mode would have to rotate through."""
    try:
        from src.instance_manager import list_pool_credentials
        return len(list_pool_credentials())
    except Exception:
        logger.warning("Could not read the credential pool", exc_info=True)
        return 0


def is_configured() -> bool:
    """A usable setup needs a password; the API key is required in api_key mode."""
    if not get_server_password():
        return False
    if config.get_codebuddy_auth_mode() == "api_key" and not get_codebuddy_api_key():
        return False
    return True


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main() -> int:
    logging.basicConfig(
        level=getattr(logging, config.get_log_level().upper(), logging.INFO),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    try:
        import webview
    except ImportError:
        print(
            "缺少依赖 pywebview，无法启动桌面应用。\n"
            "请先安装：pip install -r requirements.txt",
            file=sys.stderr,
        )
        return 1

    server = EmbeddedServer()
    logger.info(f"Starting CodeBuddy2API desktop (port {server.port})")

    if not server.start():
        # On a fresh install the default port may already be taken by another
        # service. Rather than dead-ending the user on an error screen, move to
        # a free port; the setup page will show the port actually in use.
        if not _has_saved_config():
            fallback = _find_free_port(server.host)
            if fallback:
                logger.info(f"Port {server.port} busy, falling back to {fallback}")
                server.start(port=fallback)

    if not server.running:
        reason = server.error or "未知错误"
        # Fail loudly rather than opening a window that points at nothing.
        try:
            webview.create_window(
                "CodeBuddy2API - 启动失败",
                html=_error_page(str(reason)),
                width=520,
                height=340,
            )
            webview.start()
        except Exception:
            print(f"服务启动失败：{reason}", file=sys.stderr)
        return 1

    api = DesktopApi(server)
    # The admin panel is the only UI. Its 工作台 tab also carries the main
    # app's configuration, so an unconfigured first run lands there too.
    start_url = f"{server.base_url}/"

    window = webview.create_window(
        "CodeBuddy2API",
        start_url,
        js_api=api,
        width=1280,
        height=860,
        min_size=(900, 600),
    )
    # Assign privately: a public attribute would be exposed to JS by pywebview
    # and break the bridge (the window object is not serializable).
    api._window = window

    # Bring the app to the front so it is not hidden behind other windows.
    # AppKit must only be touched on the main thread: calling it from a worker
    # thread aborts the process with SIGTRAP ("Must only be used from the main
    # thread"). PyObjCTools.AppHelper.callAfter schedules the work onto the
    # GUI thread, which is what pywebview itself uses internally.
    def _activate_app() -> None:
        try:
            from AppKit import NSApplication
            NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        except Exception:
            logger.debug("Could not activate the app", exc_info=True)

    if sys.platform == "darwin":
        def _after_shown() -> None:
            try:
                from PyObjCTools import AppHelper
                AppHelper.callAfter(_activate_app)
            except Exception:
                logger.debug("callAfter unavailable", exc_info=True)

        window.events.shown += _after_shown

    try:
        webview.start()
    finally:
        logger.info("Shutting down desktop application")
        server.stop()

    return 0


def _has_saved_config() -> bool:
    """True when the user has already saved settings (so we must not silently
    move their configured port to a different one)."""
    return os.path.exists(os.path.join("config", "config.json"))


def _find_free_port(host: str) -> Optional[int]:
    """Return the first free port at or after the configured default."""
    start = get_server_port()
    for candidate in range(start, min(start + 100, 65536)):
        if EmbeddedServer.check_port_available(host, candidate):
            return candidate
    return None


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>启动失败</title>
<style>
 body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
        background:#1e293b; color:#f1f5f9; display:flex; align-items:center;
        justify-content:center; height:100vh; margin:0; text-align:center; }}
 .box {{ max-width: 420px; }}
 h1 {{ font-size: 18px; margin-bottom: 12px; }}
 p  {{ color:#94a3b8; line-height:1.6; font-size: 14px; }}
 code {{ background:#0f172a; padding:2px 6px; border-radius:4px; }}
</style></head>
<body><div class="box">
  <h1>CodeBuddy2API 服务启动失败</h1>
  <p>{message}</p>
  <p>请检查端口是否被占用，或修改 <code>config/config.json</code> 中的
     <code>CODEBUDDY_PORT</code> 后重新启动。</p>
</div></body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
