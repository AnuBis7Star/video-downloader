"""The Mac app: the same page in its own window, with the server running inside it.

Run: uv run desktop.py   (build_app.sh packages it as a standalone "Video Downloader.app")
"""

import multiprocessing

if __name__ == "__main__":
    # In the packaged app every helper process starts by running this file again;
    # this hands it straight to its job. Heavy imports wait until main().
    multiprocessing.freeze_support()

import os
import sys
from pathlib import Path


def _use_bundled_tools(resources: Path) -> None:
    """The packaged app carries its own Deno (for YouTube's challenges) and ffmpeg."""
    tools = resources / "bin"
    if not tools.is_dir():
        return  # running from the project: the virtualenv and Homebrew provide them
    os.environ["PATH"] = f"{tools}{os.pathsep}{os.environ.get('PATH', '/usr/bin:/bin')}"
    ffmpeg = next(tools.glob("ffmpeg*"), None)
    if ffmpeg:
        os.environ["IMAGEIO_FFMPEG_EXE"] = str(ffmpeg)


def _name_the_app(icon: Path) -> None:
    """Show "Video Downloader" (not "Python") in the menu bar and Dock when run unpackaged."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = info["CFBundleDisplayName"] = "Video Downloader"
        if not getattr(sys, "frozen", False) and icon.exists():
            NSApplication.sharedApplication().setApplicationIconImage_(
                NSImage.alloc().initWithContentsOfFile_(str(icon)))
    except Exception:  # cosmetic only
        pass


def main() -> None:
    import shutil
    from threading import Thread

    import webview
    from werkzeug.serving import make_server

    import engine

    _use_bundled_tools(engine.RESOURCES)

    import app as web

    shutil.rmtree(web.STAGING, ignore_errors=True)  # leftovers from a previous run
    engine.start_background_updates()

    server = make_server("127.0.0.1", 0, web.app, threaded=True)  # any free port
    Thread(target=server.serve_forever, daemon=True).start()

    window = webview.create_window("Video Downloader", f"http://127.0.0.1:{server.server_port}/",
                                   width=1180, height=860, min_size=(420, 560))

    def pick_folder(start: str | None) -> str | None:
        chosen = window.create_file_dialog(webview.FileDialog.FOLDER, directory=start or "")
        return chosen[0].rstrip("/") if chosen else None

    def on_closed() -> None:
        web.shutdown()
        server.shutdown()

    web.pick_folder = pick_folder
    window.events.closed += on_closed
    _name_the_app(engine.RESOURCES / "assets" / "icon.png")
    webview.start()


if __name__ == "__main__":
    main()
