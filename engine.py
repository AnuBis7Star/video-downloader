"""Keeps yt-dlp current, also inside the packaged app.

YouTube changes often and yt-dlp follows within days. The app ships with a copy of yt-dlp
and checks PyPI at launch and every 6 hours for a newer one. A new version is downloaded as
wheels (checked against PyPI's SHA-256), unpacked into its own folder under
~/Library/Application Support/Video Downloader/yt-dlp/<version>, test-loaded in a separate
process, and only then marked current. `activate()` puts the current one first on sys.path;
every download and lookup runs in a fresh process that calls it before importing yt-dlp, so a
new version applies at once and a version folder never changes while something uses it.
"""

import hashlib
import io
import json
import multiprocessing as mp
import os
import re
import shutil
import ssl
import sys
import threading
import time
import urllib.request
import zipfile
from importlib import metadata
from pathlib import Path

HOME = Path.home() / "Library" / "Application Support" / "Video Downloader" / "yt-dlp"
CURRENT = HOME / "current"   # the folder name of the version in use
CHECK_EVERY = 6 * 3600
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

state = {"version": None, "checked": None, "status": "starting", "error": None}


def _v(version: str | None) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", version or "0"))


def bundled_version() -> str:
    """The yt-dlp that came with the app (or with the project's virtualenv)."""
    shipped = RESOURCES / "bundled_versions.json"
    if shipped.exists():
        return json.loads(shipped.read_text())["yt-dlp"]
    return metadata.version("yt-dlp")


def _current_overlay() -> tuple[Path, str] | None:
    try:
        name = CURRENT.read_text().strip()
    except OSError:
        return None
    folder = HOME / name
    if (folder / "yt_dlp").is_dir() and _v(name) > _v(bundled_version()):
        return folder, name
    return None


def activate() -> None:
    """Call before importing yt_dlp: prefer a newer downloaded version over the bundled one."""
    overlay = _current_overlay()
    if overlay and str(overlay[0]) not in sys.path:
        sys.path.insert(0, str(overlay[0]))


def active_version() -> str:
    overlay = _current_overlay()
    return overlay[1] if overlay else bundled_version()


def _get(url: str, timeout: int = 60) -> bytes:
    import certifi  # the packaged app has no system certificate store of its own
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(url, headers={"User-Agent": "video-downloader-updater"})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def _wheel(project: str, version: str) -> tuple[bytes, dict]:
    info = json.loads(_get(f"https://pypi.org/pypi/{project}/{version}/json"))
    whl = next(u for u in info["urls"]
               if u["packagetype"] == "bdist_wheel" and u["filename"].endswith("py3-none-any.whl"))
    data = _get(whl["url"], timeout=300)
    if hashlib.sha256(data).hexdigest() != whl["digests"]["sha256"]:
        raise RuntimeError(f"{whl['filename']} failed its checksum")
    return data, info


def _probe(folder: str, expected: str) -> None:
    """Runs in a fresh process: load the new version exactly as a download would."""
    for name in [m for m in sys.modules if m.split(".")[0] in ("yt_dlp", "yt_dlp_ejs")]:
        del sys.modules[name]
    sys.path.insert(0, folder)
    import yt_dlp
    import yt_dlp_ejs  # noqa: F401
    from yt_dlp.extractor import gen_extractor_classes
    gen_extractor_classes()
    sys.exit(0 if yt_dlp.version.__version__ == expected else 1)


def _loads(folder: Path, version: str) -> bool:
    p = mp.get_context("spawn").Process(target=_probe, args=(str(folder), version))
    p.start()
    p.join(120)
    return p.exitcode == 0


def update() -> None:
    state["status"] = "checking"
    try:
        latest = json.loads(_get("https://pypi.org/pypi/yt-dlp/json"))["info"]["version"]
        if _v(latest) > _v(active_version()):
            state["status"] = "updating"
            ytdlp, info = _wheel("yt-dlp", latest)
            # yt-dlp pins the challenge solver it was built for; take exactly that one.
            pin = next((m.group(1) for r in info["info"]["requires_dist"] or []
                        if r.startswith("yt-dlp-ejs") and (m := re.search(r"==\s*([\w.]+)", r))), None)
            ejs, _ = _wheel("yt-dlp-ejs",
                            pin or json.loads(_get("https://pypi.org/pypi/yt-dlp-ejs/json"))["info"]["version"])
            partial = HOME / f".{latest}.partial"
            shutil.rmtree(partial, ignore_errors=True)
            partial.mkdir(parents=True)
            for wheel in (ytdlp, ejs):
                zipfile.ZipFile(io.BytesIO(wheel)).extractall(partial)
            if not _loads(partial, latest):
                shutil.rmtree(partial, ignore_errors=True)
                raise RuntimeError(f"yt-dlp {latest} did not load; keeping {active_version()}")
            final = HOME / latest
            shutil.rmtree(final, ignore_errors=True)
            partial.rename(final)
            (HOME / ".current.tmp").write_text(latest)
            os.replace(HOME / ".current.tmp", CURRENT)
        state.update(status="ok", error=None)
    except Exception as e:  # offline, PyPI down: keep the version we have
        state.update(status="error", error=str(e))
    finally:
        state.update(version=active_version(), checked=time.time())


def _tidy() -> None:
    """At launch, before anything runs: keep the current version and the one before it."""
    if not HOME.is_dir():
        return
    keep = {active_version()}
    versions = sorted((p.name for p in HOME.iterdir() if p.is_dir() and not p.name.startswith(".")), key=_v)
    keep.update(v for v in versions[-2:] if _v(v) > _v(bundled_version()))
    for p in HOME.iterdir():
        if p.is_dir() and p.name not in keep:
            shutil.rmtree(p, ignore_errors=True)


def start_background_updates() -> None:
    _tidy()
    state["version"] = active_version()

    def loop():
        while True:
            update()
            time.sleep(CHECK_EVERY)

    threading.Thread(target=loop, daemon=True).start()
