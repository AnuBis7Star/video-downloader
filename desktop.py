"""The Mac app: the same page in its own window, with the server running inside it.

Run: uv run desktop.py   (the "Video Downloader" app in ~/Applications runs this; see make_app.sh)
"""

import shutil
from pathlib import Path
from threading import Thread

import webview
from werkzeug.serving import make_server

import app as web
import engine

ICON = Path(__file__).parent / "assets" / "icon.png"


def _name_the_app() -> None:
    """Show "Video Downloader" (not "Python") in the menu bar and Dock."""
    try:
        from AppKit import NSImage
        from Foundation import NSBundle
        info = NSBundle.mainBundle().infoDictionary()
        info["CFBundleName"] = info["CFBundleDisplayName"] = "Video Downloader"
        if ICON.exists():
            from AppKit import NSApplication
            NSApplication.sharedApplication().setApplicationIconImage_(
                NSImage.alloc().initWithContentsOfFile_(str(ICON)))
    except Exception:  # cosmetic only
        pass


def main() -> None:
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
    _name_the_app()
    webview.start()


if __name__ == "__main__":
    main()
