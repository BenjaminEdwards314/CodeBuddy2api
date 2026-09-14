"""
Instance Manager for CodeBuddy2API

Manages multiple independent proxy instances ("多开"). Each instance is a
separate server process with:

  - its own port
  - its own access password
  - its own auth mode (API key or token rotation)
  - its own selection of credentials (for token mode)

Why separate processes: `config.py` and the token manager keep their state in
module-level globals, so two instances inside one interpreter would fight over
the same settings. A child process per instance reuses the existing server
unchanged and keeps the instances genuinely isolated.
"""
import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from typing import Dict, List, Optional

logger = logging.getLogger("instances")

APP_DIR_NAME = "CodeBuddy2API"
INSTANCES_DIRNAME = "instances"
REGISTRY_FILENAME = "instances.json"
SHARED_CREDS_DIRNAME = "shared_creds"

DEFAULT_HOST = "127.0.0.1"
START_TIMEOUT = 25.0
STOP_TIMEOUT = 10.0
POLL_INTERVAL = 0.2

VALID_AUTH_MODES = ("api_key", "token")


# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

def project_root() -> str:
    """Repository / bundle root — the directory containing web.py."""
    if getattr(sys, "frozen", False):
        bundle = getattr(sys, "_MEIPASS", None)
        if bundle:
            return bundle
    # This file lives in <root>/src/, so the root is one level up.
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def instances_root() -> str:
    """Directory holding all instance data.

    CODEBUDDY_INSTANCES_DIR overrides the location (used by tests). Otherwise
    instance data lives under the frozen app's data dir, or the repo root when
    running from source.
    """
    override = os.environ.get("CODEBUDDY_INSTANCES_DIR")
    if override:
        os.makedirs(override, exist_ok=True)
        return override
    if getattr(sys, "frozen", False):
        base = os.path.expanduser(f"~/Library/Application Support/{APP_DIR_NAME}")
    else:
        base = project_root()
    path = os.path.join(base, INSTANCES_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def registry_path() -> str:
    return os.path.join(instances_root(), REGISTRY_FILENAME)


def shared_creds_dir() -> str:
    """Central pool of credential files that instances draw from."""
    path = os.path.join(instances_root(), SHARED_CREDS_DIRNAME)
    os.makedirs(path, exist_ok=True)
    return path


def instance_dir(instance_id: str) -> str:
    return os.path.join(instances_root(), instance_id)


# --------------------------------------------------------------------------
# Credential pool
# --------------------------------------------------------------------------

def _read_token_meta(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if "bearer_token" not in data:
            return None
        token = data.get("bearer_token") or ""
        created = data.get("created_at") or 0
        expires_in = data.get("expires_in") or 0
        try:
            created = int(created)
            expires_in = int(expires_in)
        except (TypeError, ValueError):
            created, expires_in = 0, 0
        expires_at = (created + expires_in) if (created and expires_in) else 0
        return {
            "name": os.path.basename(path),
            "user_id": data.get("user_id") or "(未知账号)",
            "domain": data.get("domain") or "",
            "token_preview": f"{token[:10]}...{token[-6:]}" if len(token) > 20 else "***",
            "expires_at": expires_at,
            "expired": bool(expires_at and expires_at < time.time()),
        }
    except Exception:
        logger.warning(f"Unreadable credential file: {path}", exc_info=True)
        return None


def list_pool_credentials() -> List[dict]:
    """All credentials available for instances to use."""
    out = []
    for name in sorted(os.listdir(shared_creds_dir())):
        if not name.endswith(".json"):
            continue
        meta = _read_token_meta(os.path.join(shared_creds_dir(), name))
        if meta:
            out.append(meta)
    return out


def import_credentials_from(paths: List[str]) -> int:
    """Copy credential files into the shared pool. Returns count imported."""
    count = 0
    for src in paths:
        try:
            if not os.path.isfile(src):
                continue
            meta = _read_token_meta(src)
            if not meta:
                continue
            shutil.copy2(src, os.path.join(shared_creds_dir(), os.path.basename(src)))
            count += 1
        except Exception:
            logger.warning(f"Could not import credential {src}", exc_info=True)
    return count


def seed_pool_from(path: str) -> int:
    """Seed the pool from a directory on first use (e.g. the repo's creds)."""
    if not os.path.isdir(path):
        return 0
    if list_pool_credentials():
        return 0  # already populated; never re-seed over the user's pool
    files = [
        os.path.join(path, n) for n in os.listdir(path) if n.endswith(".json")
    ]
    return import_credentials_from(files)


def write_instance_credentials(instance_id: str, names: List[str]) -> int:
    """Sync an instance's own creds dir to contain exactly `names`."""
    creds_dir = os.path.join(instance_dir(instance_id), ".codebuddy_creds")
    os.makedirs(creds_dir, exist_ok=True)

    # Remove files that are no longer selected.
    wanted = set(names)
    for existing in os.listdir(creds_dir):
        if existing.endswith(".json") and existing not in wanted:
            try:
                os.remove(os.path.join(creds_dir, existing))
            except OSError:
                pass

    written = 0
    for name in names:
        src = os.path.join(shared_creds_dir(), name)
        if os.path.isfile(src):
            try:
                shutil.copy2(src, os.path.join(creds_dir, name))
                written += 1
            except OSError:
                logger.warning(f"Could not copy {name} into {instance_id}", exc_info=True)
    return written


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

def load_registry() -> List[dict]:
    path = registry_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        logger.error("Instance registry is corrupt; starting empty", exc_info=True)
        return []


def save_registry(instances: List[dict]) -> None:
    path = registry_path()
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(instances, fh, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def find_instance(instances: List[dict], instance_id: str) -> Optional[dict]:
    for inst in instances:
        if inst.get("id") == instance_id:
            return inst
    return None


def next_instance_id(instances: List[dict]) -> str:
    used = {i.get("id") for i in instances}
    n = 1
    while f"instance_{n}" in used:
        n += 1
    return f"instance_{n}"


def next_free_port(instances: List[dict], start: int = 8010) -> int:
    taken = {int(i.get("port", 0)) for i in instances}
    port = start
    while port < 65535:
        if port not in taken and is_port_free(DEFAULT_HOST, port):
            return port
        port += 1
    raise RuntimeError("没有可用端口")


def is_port_free(host: str, port: int) -> bool:
    bind_host = "" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, port))
            return True
        except OSError:
            return False


# --------------------------------------------------------------------------
# Instance manager
# --------------------------------------------------------------------------

class InstanceManager:
    """Creates, starts, stops and reports on proxy instances."""

    def __init__(self, project_root: Optional[str] = None, python_executable: Optional[str] = None):
        self.project_root = project_root or globals()["project_root"]()
        self.python = python_executable or sys.executable
        self._processes: Dict[str, subprocess.Popen] = {}

    # -- creation ----------------------------------------------------------
    def create(self, name: str = "", port: Optional[int] = None,
               password: str = "", auth_mode: str = "api_key",
               api_key: str = "", token_names: Optional[List[str]] = None,
               auto_start: bool = False) -> dict:
        instances = load_registry()
        instance_id = next_instance_id(instances)

        if port is None:
            port = next_free_port(instances)
        port = int(port)
        if not (1 <= port <= 65535):
            raise ValueError("端口必须在 1 - 65535 之间。")
        for existing in instances:
            if int(existing.get("port", 0)) == port:
                raise ValueError(f"端口 {port} 已被实例 “{existing.get('name')}” 使用。")
        if not is_port_free(DEFAULT_HOST, port):
            raise ValueError(f"端口 {port} 已被其他程序占用。")

        if auth_mode not in VALID_AUTH_MODES:
            raise ValueError("认证模式必须是 api_key 或 token。")
        if not password:
            raise ValueError("访问密码不能为空。")
        if auth_mode == "api_key" and not api_key.strip():
            raise ValueError("API Key 模式下必须填写 API Key。")
        if auth_mode == "token" and not token_names:
            raise ValueError("Token 模式下至少要选择一个凭证。")

        record = {
            "id": instance_id,
            "name": name.strip() or instance_id,
            "port": port,
            "password": password,
            "auth_mode": auth_mode,
            "api_key": api_key.strip(),
            "token_names": list(token_names or []),
            "created_at": time.time(),
        }

        os.makedirs(os.path.join(instance_dir(instance_id), "config"), exist_ok=True)
        instances.append(record)
        save_registry(instances)
        self._write_config(record)

        logger.info(f"Created instance {instance_id} ({record['name']}) on port {port}")
        if auto_start:
            self.start(instance_id)
        return self.describe(record)

    def update(self, instance_id: str, **fields) -> dict:
        instances = load_registry()
        record = find_instance(instances, instance_id)
        if record is None:
            raise ValueError("实例不存在。")

        new_port = int(fields.get("port", record["port"]))
        if not (1 <= new_port <= 65535):
            raise ValueError("端口必须在 1 - 65535 之间。")
        for other in instances:
            if other["id"] != instance_id and int(other.get("port", 0)) == new_port:
                raise ValueError(f"端口 {new_port} 已被实例 “{other.get('name')}” 使用。")

        port_changed = new_port != int(record.get("port", 0))
        if port_changed and self.is_running(instance_id):
            self.stop(instance_id)
        if port_changed and not is_port_free(DEFAULT_HOST, new_port):
            raise ValueError(f"端口 {new_port} 已被其他程序占用。")

        auth_mode = fields.get("auth_mode", record.get("auth_mode"))
        if auth_mode not in VALID_AUTH_MODES:
            raise ValueError("认证模式必须是 api_key 或 token。")

        password = fields.get("password", record.get("password"))
        if not password:
            raise ValueError("访问密码不能为空。")

        api_key = str(fields.get("api_key", record.get("api_key", ""))).strip()
        if auth_mode == "api_key" and not api_key:
            raise ValueError("API Key 模式下必须填写 API Key。")

        token_names = fields.get("token_names", record.get("token_names", []))
        if auth_mode == "token" and not token_names:
            raise ValueError("Token 模式下至少要选择一个凭证。")

        record.update({
            "name": str(fields.get("name", record.get("name", instance_id))).strip() or instance_id,
            "port": new_port,
            "password": password,
            "auth_mode": auth_mode,
            "api_key": api_key,
            "token_names": list(token_names),
        })
        save_registry(instances)
        self._write_config(record)
        return self.describe(record)

    def delete(self, instance_id: str) -> None:
        self.stop(instance_id)
        instances = load_registry()
        instances = [i for i in instances if i.get("id") != instance_id]
        save_registry(instances)
        shutil.rmtree(instance_dir(instance_id), ignore_errors=True)
        logger.info(f"Deleted instance {instance_id}")

    # -- process control ---------------------------------------------------
    def start(self, instance_id: str) -> dict:
        if self.is_running(instance_id):
            return {"ok": True, "message": "实例已在运行。", "running": True}

        instances = load_registry()
        record = find_instance(instances, instance_id)
        if record is None:
            raise ValueError("实例不存在。")

        port = int(record["port"])
        if not is_port_free(DEFAULT_HOST, port):
            return {"ok": False, "running": False,
                    "message": f"端口 {port} 已被占用，无法启动。"}

        self._write_config(record)
        workdir = instance_dir(instance_id)
        entry = self._entry_point()

        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"

        # A bundled server binary is self-contained; a web.py script needs the
        # interpreter and the repo on the import path.
        if entry.endswith(".py"):
            command = [self.python, entry]
            env["PYTHONPATH"] = self.project_root
        else:
            command = [entry]

        log_path = os.path.join(workdir, "instance.log")
        log_file = open(log_path, "a", encoding="utf-8")
        log_file.write(f"\n=== start {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
        log_file.flush()

        try:
            proc = subprocess.Popen(
                command,
                cwd=workdir, env=env,
                stdout=log_file, stderr=subprocess.STDOUT,
            )
        except Exception as exc:
            log_file.close()
            return {"ok": False, "running": False, "message": f"启动失败：{exc}"}

        self._processes[instance_id] = proc

        if self._wait_healthy(port, proc):
            return {"ok": True, "running": True, "url": self.url(record),
                    "message": f"实例已启动：{self.url(record)}"}

        # Startup failed: surface the tail of the log to make it diagnosable.
        self.stop(instance_id)
        return {"ok": False, "running": False,
                "message": f"实例启动失败。日志：{self._log_tail(log_path)}"}

    def stop(self, instance_id: str) -> dict:
        proc = self._processes.pop(instance_id, None)
        if proc is None:
            # Untracked (e.g. the app was restarted). Fall back to stopping by
            # port so the user is never stuck with an instance they cannot halt.
            record = find_instance(load_registry(), instance_id)
            if record and self._healthy(int(record.get("port", 0))):
                if self._kill_by_port(int(record["port"])):
                    return {"ok": True, "running": False, "message": "实例已停止。"}
                return {"ok": False, "running": True,
                        "message": "无法停止该实例，请手动结束占用该端口的进程。"}
            return {"ok": True, "running": False, "message": "实例未在运行。"}
        try:
            proc.terminate()
            try:
                proc.wait(timeout=STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
        except Exception:
            logger.warning(f"Error stopping {instance_id}", exc_info=True)
        return {"ok": True, "running": False, "message": "实例已停止。"}

    @staticmethod
    def _kill_by_port(port: int) -> bool:
        """Terminate whatever process is listening on `port`."""
        try:
            found = subprocess.run(
                ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
                capture_output=True, text=True, timeout=10,
            )
            pids = [p for p in found.stdout.split() if p.isdigit()]
            for pid in pids:
                subprocess.run(["kill", pid], capture_output=True, timeout=5)
            return bool(pids)
        except Exception:
            logger.warning(f"Could not kill process on port {port}", exc_info=True)
            return False

    def stop_all(self) -> None:
        for instance_id in list(self._processes):
            self.stop(instance_id)

    def is_running(self, instance_id: str) -> bool:
        proc = self._processes.get(instance_id)
        if proc is None:
            return False
        if proc.poll() is not None:
            self._processes.pop(instance_id, None)
            return False
        return True

    # -- reporting ---------------------------------------------------------
    def describe(self, record: dict) -> dict:
        instance_id = record["id"]
        port = int(record.get("port", 0))
        running = self.is_running(instance_id)
        healthy = self._healthy(port) if running else False

        # After the desktop app restarts, the in-memory process map is empty
        # even though previously started instances are still serving. Report
        # those honestly rather than claiming they stopped.
        if not running and self._healthy(port):
            running, healthy = True, True

        info = {
            "id": instance_id,
            "name": record.get("name") or instance_id,
            "port": port,
            "url": self.url(record),
            "password": record.get("password", ""),
            "auth_mode": record.get("auth_mode", "api_key"),
            "api_key": record.get("api_key", ""),
            "token_names": record.get("token_names", []),
            "created_at": record.get("created_at", 0),
            "running": running,
            "healthy": healthy,
            "managed": self.is_running(instance_id),
        }
        return info

    def list_all(self) -> List[dict]:
        return [self.describe(r) for r in load_registry()]

    def get(self, instance_id: str) -> Optional[dict]:
        record = find_instance(load_registry(), instance_id)
        return self.describe(record) if record else None

    @staticmethod
    def url(record: dict) -> str:
        return f"http://{DEFAULT_HOST}:{int(record.get('port', 0))}"

    # -- internals ---------------------------------------------------------
    def _write_config(self, record: dict) -> None:
        """Write the instance's own config.json (+ credentials)."""
        instance_id = record["id"]
        base = instance_dir(instance_id)
        config_dir = os.path.join(base, "config")
        os.makedirs(config_dir, exist_ok=True)

        token_names = list(record.get("token_names") or [])
        if record.get("auth_mode") == "api_key":
            token_names = []
        elif token_names:
            write_instance_credentials(instance_id, token_names)

        config = {
            "CODEBUDDY_HOST": DEFAULT_HOST,
            "CODEBUDDY_PORT": int(record["port"]),
            "CODEBUDDY_PASSWORD": record["password"],
            "CODEBUDDY_AUTH_MODE": record.get("auth_mode", "api_key"),
            "CODEBUDDY_API_KEY": record.get("api_key", "") if record.get("auth_mode") == "api_key" else "",
            "CODEBUDDY_CREDS_DIR": ".codebuddy_creds",
            "CODEBUDDY_ROTATION_COUNT": 1,
        }
        # Inherit upstream routing (endpoint / internet environment) from the
        # parent process. Without this an instance falls back to the default
        # public endpoint, which rejects credentials issued for another region.
        config.update(self._inherited_settings())
        with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as fh:
            json.dump(config, fh, indent=2, ensure_ascii=False)

    @staticmethod
    def _inherited_settings() -> dict:
        """Upstream routing settings taken from the parent's configuration."""
        keys = (
            "CODEBUDDY_API_ENDPOINT",
            "CODEBUDDY_INTERNET_ENVIRONMENT",
            "CODEBUDDY_LOG_LEVEL",
            "CODEBUDDY_MODELS",
            "CODEBUDDY_SSL_VERIFY",
        )
        inherited = {}
        try:
            import config as app_config

            for key in keys:
                value = app_config._get_config_value(key)
                if value not in (None, ""):
                    inherited[key] = value
        except Exception:
            logger.warning("Could not read parent configuration", exc_info=True)
        return inherited

    def _entry_point(self) -> str:
        """Path to the instance server executable/script.

        A frozen .app cannot run `web.py` (the bundle holds no interpreter and
        no script file), so it ships a second headless binary alongside the GUI.
        """
        if getattr(sys, "frozen", False):
            bundled = self._bundled_server_binary()
            if bundled:
                return bundled
        return os.path.join(self.project_root, "web.py")

    def _bundled_server_binary(self) -> Optional[str]:
        """Locate the headless server executable inside the .app bundle."""
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        candidates = [
            os.path.join(exe_dir, "CodeBuddy2API-server"),
            os.path.join(exe_dir, "..", "Resources", "CodeBuddy2API-server"),
            os.path.join(os.environ.get("CODEBUDDY_BUNDLE_DIR", ""), "CodeBuddy2API-server"),
        ]
        for candidate in candidates:
            path = os.path.abspath(candidate)
            if os.path.isfile(path) and os.access(path, os.X_OK):
                return path
        return None

    def _wait_healthy(self, port: int, proc: subprocess.Popen) -> bool:
        deadline = time.time() + START_TIMEOUT
        while time.time() < deadline:
            if proc.poll() is not None:
                return False
            if self._healthy(port):
                return True
            time.sleep(POLL_INTERVAL)
        return False

    @staticmethod
    def _healthy(port: int) -> bool:
        try:
            with urllib.request.urlopen(
                f"http://{DEFAULT_HOST}:{port}/health", timeout=1.5
            ) as resp:
                return resp.status == 200
        except Exception:
            return False

    @staticmethod
    def _log_tail(path: str, lines: int = 6) -> str:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.readlines()[-lines:]
            return " / ".join(l.strip() for l in tail if l.strip())[-400:]
        except Exception:
            return "(无日志)"


_manager: Optional[InstanceManager] = None


def get_manager() -> InstanceManager:
    global _manager
    if _manager is None:
        _manager = InstanceManager(project_root=project_root())
    return _manager
