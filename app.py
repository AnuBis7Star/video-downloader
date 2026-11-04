"""Local web UI. Run: uv run app.py  → http://127.0.0.1:5050  (desktop.py wraps it in a window)

Downloads run as batches (one video, many, or expanded playlists), one video at
a time. Each video runs in its own process group inside a private staging
folder, so cancelling can kill it (plus its ffmpeg/deno children) and delete
every partial file. Finished videos are moved into the folder the user picked.
Link lookups also run in fresh processes, so a yt-dlp update applies at once.
"""

import json
import multiprocessing as mp
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path
from typing import Callable

from flask import Flask, abort, jsonify, render_template, request

import engine
from downloader import QUALITIES, download, get_info

app = Flask(__name__, template_folder=str(engine.RESOURCES / "templates"))
batches: dict[str, dict] = {}
STAGING = Path(tempfile.gettempdir()) / "video_downloader"
SETTINGS = Path.home() / ".video_downloader.json"
ACTIVE = ("starting", "downloading", "processing")
ITEM_FIELDS = ("status", "progress", "speed", "error", "path")
MEDIA = {".mp4", ".mp3", ".webm", ".mkv", ".m4a", ".mov"}


def load_settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text())
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> dict:
    settings = {**load_settings(), **changes}
    SETTINGS.write_text(json.dumps(settings, indent=2))
    return settings


def _worker(url: str, quality: str, out_dir: str, q: mp.Queue) -> None:
    os.setsid()  # own process group, so cancel also takes down ffmpeg/deno
    try:
        path = download(url, quality, Path(out_dir),
                        lambda stage, pct, speed: q.put(("progress", stage, pct, speed)))
        q.put(("done", str(path)))
    except Exception as e:
        q.put(("error", str(e)))


def _kill(proc: mp.Process) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        proc.kill()  # cancelled before the child got its own group
    proc.join(5)


def _deliver(src: Path, dest: Path) -> Path:
    """Move a finished file into dest without overwriting anything."""
    dest.mkdir(parents=True, exist_ok=True)
    target, n = dest / src.name, 1
    while target.exists():
        target = dest / f"{src.stem} ({n}){src.suffix}"
        n += 1
    shutil.move(src, target)
    return target


def _run_item(batch: dict, item: dict) -> None:
    item_dir = STAGING / batch["id"] / item["id"]
    q = mp.Queue()
    proc = mp.Process(target=_worker, args=(item["url"], batch["quality"], str(item_dir), q),
                      daemon=True)
    with batch["lock"]:
        if item["status"] != "pending":  # skipped before it started
            return
        item.update(status="starting", proc=proc)
        proc.start()

    while item["status"] in ACTIVE:
        try:
            msg = q.get(timeout=0.5)
        except queue.Empty:
            if proc.is_alive():
                continue
            msg = ("error", "Download process exited unexpectedly")
        # Under the lock so a skip/cancel can't be overwritten by a late message.
        with batch["lock"]:
            if item["status"] not in ACTIVE:
                break
            if msg[0] == "progress":
                item.update(status=msg[1], progress=msg[2], speed=msg[3])
            elif msg[0] == "done":
                path = _deliver(Path(msg[1]), batch["dest"])
                item.update(status="done", progress=100, path=str(path))
            else:
                item.update(status="error", error=msg[1])
    shutil.rmtree(item_dir, ignore_errors=True)


def _run_batch(batch: dict) -> None:
    for item in batch["items"]:
        if batch["status"] == "cancelled":
            return
        _run_item(batch, item)
    with batch["lock"]:
        if batch["status"] != "cancelled":
            batch["status"] = "done"
    shutil.rmtree(STAGING / batch["id"], ignore_errors=True)


def _get_batch(batch_id: str) -> dict:
    return batches.get(batch_id) or abort(404)


_pool = None


def _lookups() -> ProcessPoolExecutor:
    """One fresh process per lookup, so it always imports the yt-dlp installed right now."""
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=4, mp_context=mp.get_context("spawn"),
                                    max_tasks_per_child=1)
    return _pool


def _pick_folder_osascript(start: str | None) -> str | None:
    script = 'choose folder with prompt "Save downloads to:"'
    if start and Path(start).is_dir():
        escaped = start.replace("\\", "\\\\").replace('"', '\\"')
        script += f' default location (POSIX file "{escaped}")'
    result = subprocess.run(["osascript", "-e", f"POSIX path of ({script})"],
                            capture_output=True, text=True)
    if result.returncode != 0:  # user pressed Cancel
        return None
    return result.stdout.strip().rstrip("/") or "/"


# The desktop app swaps in its own window's native folder dialog.
pick_folder: Callable[[str | None], str | None] = _pick_folder_osascript


def shutdown() -> None:
    """On quit: stop running downloads and remove their partial files. Finished files stay."""
    for batch in list(batches.values()):
        with batch["lock"]:
            batch["status"] = "cancelled"
            for item in batch["items"]:
                if item["status"] in ("pending", *ACTIVE):
                    item["status"] = "cancelled"
        for item in batch["items"]:
            if item.get("proc") and item["proc"].is_alive():
                _kill(item["proc"])
    if _pool:
        _pool.shutdown(wait=False, cancel_futures=True)
    shutil.rmtree(STAGING, ignore_errors=True)


@app.get("/")
def index():
    return render_template("index.html", qualities=QUALITIES, settings=load_settings(),
                           home=str(Path.home()))


@app.post("/api/info")
def info():
    global _pool
    url = request.json["url"]
    try:
        try:
            return jsonify(_lookups().submit(get_info, url).result(timeout=120))
        except BrokenProcessPool:  # a helper died; start a fresh pool rather than staying broken
            _pool = None
            return jsonify(_lookups().submit(get_info, url).result(timeout=120))
    except Exception as e:
        return jsonify(error=str(e)), 400


@app.get("/api/engine")
def engine_state():
    return jsonify(engine.state)


@app.post("/api/settings")
def update_settings():
    return jsonify(save_settings(use_last=bool(request.json.get("use_last"))))


@app.post("/api/choose-folder")
def choose_folder():
    """Open the native macOS folder picker; the choice becomes the last location."""
    path = pick_folder(request.json.get("start"))
    if not path:
        return jsonify(cancelled=True)
    save_settings(last_dir=path)
    return jsonify(path=path)


@app.get("/api/folder")
def folder():
    """The newest videos in the last-used folder, so the page can show what's already there."""
    last = load_settings().get("last_dir")
    if not last or not Path(last).is_dir():
        return jsonify(path=last, exists=False, count=0, files=[])
    files = [p for p in Path(last).iterdir()
             if p.is_file() and p.suffix.lower() in MEDIA and not p.name.startswith(".")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonify(path=last, exists=True, count=len(files),
                   files=[{"name": p.name, "size": p.stat().st_size, "mtime": p.stat().st_mtime}
                          for p in files[:6]])


@app.post("/api/open-folder")
def open_folder():
    last = load_settings().get("last_dir")
    if last and Path(last).is_dir():
        subprocess.run(["open", last])
    return jsonify(ok=True)


@app.post("/api/batches")
def start_batch():
    body = request.json
    dest = Path(body["dest"]).expanduser()
    if not dest.is_dir():
        return jsonify(error=f"Folder not found: {dest}"), 400
    batch = {
        "id": uuid.uuid4().hex, "dest": dest, "quality": body.get("quality", "best"),
        "status": "running", "lock": threading.Lock(),
        "items": [{"id": str(i), "url": it["url"], "status": "pending", "progress": 0,
                   "speed": "", "error": None, "path": None}
                  for i, it in enumerate(body["items"])],
    }
    batches[batch["id"]] = batch
    save_settings(last_dir=str(dest))
    threading.Thread(target=_run_batch, args=(batch,), daemon=True).start()
    return jsonify(id=batch["id"])


@app.get("/api/batches/<batch_id>")
def batch_status(batch_id: str):
    batch = _get_batch(batch_id)
    return jsonify(status=batch["status"],
                   items=[{k: it[k] for k in ITEM_FIELDS} for it in batch["items"]])


@app.post("/api/batches/<batch_id>/items/<int:index>/skip")
def skip_item(batch_id: str, index: int):
    """Stop one video (or drop it from the queue) and delete its partial files."""
    batch = _get_batch(batch_id)
    if index >= len(batch["items"]):
        abort(404)
    item = batch["items"][index]
    with batch["lock"]:
        if item["status"] not in ("pending", *ACTIVE):
            return jsonify(ok=False)
        item["status"] = "skipped"
        proc = item.get("proc")
    if proc:
        _kill(proc)
    shutil.rmtree(STAGING / batch_id / item["id"], ignore_errors=True)
    return jsonify(ok=True)


@app.post("/api/batches/<batch_id>/cancel")
def cancel_batch(batch_id: str):
    """Stop everything and remove every file this batch created, finished ones too."""
    batch = batches.pop(batch_id, None) or abort(404)
    with batch["lock"]:
        batch["status"] = "cancelled"
        for item in batch["items"]:
            if item["status"] in ("pending", *ACTIVE):
                item["status"] = "cancelled"
    for item in batch["items"]:
        if item.get("proc"):
            _kill(item["proc"])
        if item["path"]:
            Path(item["path"]).unlink(missing_ok=True)
    shutil.rmtree(STAGING / batch_id, ignore_errors=True)
    return jsonify(ok=True)


@app.post("/api/batches/<batch_id>/reveal")
def reveal(batch_id: str):
    subprocess.run(["open", str(_get_batch(batch_id)["dest"])])
    return jsonify(ok=True)


if __name__ == "__main__":
    shutil.rmtree(STAGING, ignore_errors=True)  # leftovers from a previous run
    engine.start_background_updates()
    try:
        app.run(host="127.0.0.1", port=5050)
    finally:
        shutdown()
