"""Keeps yt-dlp current.

YouTube changes often and yt-dlp follows within days, so the app upgrades yt-dlp (and its
challenge solver) at launch and every few hours after. The upgrade goes through uv, so
uv.lock records the version in use. Packages are only replaced while nothing is using them:
downloads and lookups hold `engine.using()`, and an update waits for them to finish.
Each download and lookup runs in a fresh process, so a new version takes effect at once.
"""

import shutil
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).parent
PACKAGES = ["yt-dlp", "yt-dlp-ejs"]
CHECK_EVERY = 6 * 3600
RETRY_BUSY = 60

state = {"version": None, "checked": None, "status": "starting", "error": None}

_cond = threading.Condition()
_users = 0
_updating = False


@contextmanager
def using():
    """Hold while yt-dlp is in use; waits if an update is replacing it right now."""
    global _users
    with _cond:
        while _updating:
            _cond.wait()
        _users += 1
    try:
        yield
    finally:
        with _cond:
            _users -= 1
            _cond.notify_all()


def _uv() -> str | None:
    # Apps opened from Finder get a bare PATH, so look in the usual install places too.
    candidates = [shutil.which("uv"), "/opt/homebrew/bin/uv", "/usr/local/bin/uv",
                  str(Path.home() / ".local/bin/uv"), str(Path.home() / ".cargo/bin/uv")]
    return next((c for c in candidates if c and Path(c).exists()), None)


def installed_version() -> str | None:
    """Read the version from disk, not from an already-imported module."""
    r = subprocess.run([sys.executable, "-c",
                        "import importlib.metadata as m; print(m.version('yt-dlp'))"],
                       capture_output=True, text=True)
    return r.stdout.strip() or None


def update() -> bool:
    """Upgrade yt-dlp if a newer one exists. Returns False if it had to wait for idle."""
    global _updating
    uv = _uv()
    if not uv:
        state.update(status="error", error="uv not found", checked=time.time())
        return True
    with _cond:
        if _users:
            return False
        _updating = True
    state["status"] = "updating"
    try:
        upgrade = [a for p in PACKAGES for a in ("--upgrade-package", p)]
        for cmd in ([uv, "lock", *upgrade], [uv, "sync", "--inexact"]):
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1])
        state.update(status="ok", error=None)
    except Exception as e:  # offline, PyPI down: keep the version we have
        state.update(status="error", error=str(e))
    finally:
        state.update(version=installed_version(), checked=time.time())
        with _cond:
            _updating = False
            _cond.notify_all()
    return True


def start_background_updates() -> None:
    def loop():
        while True:
            time.sleep(CHECK_EVERY if update() else RETRY_BUSY)

    state["version"] = installed_version()
    threading.Thread(target=loop, daemon=True).start()
