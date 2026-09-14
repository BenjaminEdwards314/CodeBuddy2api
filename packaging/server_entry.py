"""
Headless server entry point for proxy instances.

PyInstaller turns this into a standalone executable that the desktop app
launches once per instance. Each child gets its own working directory, so
config.py reads that instance's config/config.json and .codebuddy_creds.
"""
import os
import sys

# The instance directory is the working directory; make the bundled modules
# importable the same way web.py expects.
if getattr(sys, "frozen", False):
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        sys.path.insert(0, bundle)
        os.environ.setdefault("CODEBUDDY_BUNDLE_DIR", bundle)

import web  # noqa: F401  (importing runs the server via its __main__ guard)


def _run() -> None:
    """Run the hypercorn server using the configuration already loaded."""
    from config import get_server_host, get_server_port, get_log_level
    import asyncio
    from hypercorn.config import Config
    from hypercorn.asyncio import serve

    cfg = Config()
    cfg.bind = [f"{get_server_host()}:{get_server_port()}"]
    cfg.loglevel = get_log_level().lower()
    asyncio.run(serve(web.app, cfg))


if __name__ == "__main__":
    _run()
