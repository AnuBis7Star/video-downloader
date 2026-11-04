"""Keeps yt-dlp current, also inside the packaged app.

YouTube changes often and yt-dlp follows within days. The app ships with a copy of yt-dlp
and checks PyPI at launch and every 6 hours for a newer one. A new version is downloaded as
wheels (checked against PyPI's SHA-256), unpacked into its own folder under
~/Library/Application Support/Video Downloader/yt-dlp/<version>, test-loaded in a separate
process, and only then marked current. `activate()` puts the current one first on sys.path;
every download and lookup runs in a fresh process that calls it before importing yt-dlp, so a
new version applies at once and a version folder never changes while something uses it.

The packaged app also fetches ffmpeg on first launch instead of shipping it; see _fetch_ffmpeg.
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
FFMPEG_HOME = HOME.parent / "ffmpeg"
CHECK_EVERY = 6 * 3600
RESOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))

state = {"version": None, "checked": None, "status": "starting", "error": None, "ffmpeg": "starting"}
ffmpeg_ready = threading.Event()
_ffmpeg_lock = threading.Lock()


def _shipped(name: str) -> str | None:
    """A version recorded at build time (the packaged app only)."""
    shipped = RESOURCES / "bundled_versions.json"
    return json.loads(shipped.read_text()).get(name) if shipped.exists() else None


def _v(version: str | None) -> tuple:
    return tuple(int(p) for p in re.findall(r"\d+", version or "0"))


def bundled_version() -> str:
    """The yt-dlp that came with the app (or with the project's virtualenv)."""
    return _shipped("yt-dlp") or metadata.version("yt-dlp")


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


def _fetch_ffmpeg() -> None:
    """The packaged app doesn't ship ffmpeg: the build it uses is GPL-licensed. On first launch it
    downloads that build from PyPI (imageio-ffmpeg, pinned to the version the app was built with,
    checked against PyPI's SHA-256) into Application Support, once."""
    version = _shipped("imageio-ffmpeg")
    if os.environ.get("IMAGEIO_FFMPEG_EXE") or not version:
        ffmpeg_ready.set()  # running from source: the virtualenv's copy is used
        return
    target = FFMPEG_HOME / version / "ffmpeg"
    try:
        if not target.exists():
            state["ffmpeg"] = "downloading"
            info = json.loads(_get(f"https://pypi.org/pypi/imageio-ffmpeg/{version}/json"))
            whl = next(u for u in info["urls"] if re.search(r"macosx_[\d_]+_arm64\.whl$", u["filename"]))
            data = _get(whl["url"], timeout=600)
            if hashlib.sha256(data).hexdigest() != whl["digests"]["sha256"]:
                raise RuntimeError(f"{whl['filename']} failed its checksum")
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                member = next(n for n in z.namelist() if "binaries/ffmpeg-macos-aarch64" in n)
                partial = target.with_name(".ffmpeg.partial")
                partial.parent.mkdir(parents=True, exist_ok=True)
                partial.write_bytes(z.read(member))
            partial.chmod(0o755)
            partial.rename(target)
            for old in FFMPEG_HOME.iterdir():
                if old.is_dir() and old.name != version:
                    shutil.rmtree(old, ignore_errors=True)
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(target)  # downloads started from now on use it
        state.update(ffmpeg="ready")
        ffmpeg_ready.set()
    except Exception as e:  # offline on first launch: try again when a download starts
        state.update(ffmpeg="error", ffmpeg_error=str(e))


def ffmpeg_available() -> bool:
    """Called before each download: waits for the first-launch fetch, or retries a failed one."""
    if not ffmpeg_ready.is_set():
        with _ffmpeg_lock:
            if not ffmpeg_ready.is_set():
                _fetch_ffmpeg()
    return ffmpeg_ready.is_set()


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
        ffmpeg_available()
        while True:
            update()
            time.sleep(CHECK_EVERY)

    threading.Thread(target=loop, daemon=True).start()
