# Video Downloader

Personal YouTube downloader built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).
ffmpeg is bundled via `imageio-ffmpeg`; YouTube's JS challenges are solved with Deno + `yt-dlp-ejs`.

## Mac app (standalone)
    ./build_app.sh             # dist/Video Downloader.app
    ./build_app.sh --install   # ...and install into ~/Applications (shortcut in /Applications)
    ./build_app.sh --zip       # ...and dist/Video-Downloader-mac.zip to send to someone

The app carries Python, yt-dlp, ffmpeg and Deno (the official standalone release, checked
against its published checksum), so it runs on an Apple Silicon Mac with nothing else
installed (about 220 MB, 113 MB zipped). It's the same page in its own window, with the
server running inside the app. Quitting stops any running download and removes its partial
file; finished files stay. Run `uv run desktop.py` to try changes without building.

**Sharing:** send `dist/Video-Downloader-mac.zip`. It holds the app and `How to open.txt`
(from `share/`). The app is signed ad-hoc, not with an Apple Developer ID, so the first time
the recipient opens it macOS says it can't verify it; they allow it once in
System Settings → Privacy & Security → Open Anyway. Removing that step needs a paid Apple
Developer account for signing and notarization. Apple Silicon only (M1 or newer).

## Web UI
    uv run app.py        # → http://127.0.0.1:5050

- Paste anything containing YouTube links (one video, many, playlists). They're detected
  automatically; playlists expand into their videos, duplicates are dropped, ✕ removes one.
- **Download** asks where to save (native macOS folder picker). Tick **Always save here**
  in the top bar to skip the picker; **Change** picks a new folder. Remembered in
  `~/.video_downloader.json`.
- Videos download one at a time. ✕ on a row skips just that video.
- **Cancel** stops everything and deletes every file that batch created (finished ones too),
  leaving the folder exactly as it was.
- Existing files are never overwritten: a repeat saves as `… (1).mp4`.
- A watch link with `&list=` is treated as that single video; use the `/playlist?list=` link
  for the whole playlist.

## CLI
    uv run downloader.py URL [URL ...] [-q best|1080|720|480|audio] [-o DIR]

URLs can be videos or playlists. Default output: `~/Downloads/video_downloader`.

## yt-dlp stays current on its own
YouTube changes often and yt-dlp follows within days. At launch and every 6 hours the app
(and `uv run app.py`) checks PyPI. A newer yt-dlp and the challenge solver it pins are
downloaded as wheels, checked against PyPI's SHA-256, unpacked into
`~/Library/Application Support/Video Downloader/yt-dlp/<version>`, test-loaded in a separate
process, and only then made current (see `engine.py`). Each download and lookup runs in a
fresh process that loads the current version first, so updates apply without a restart and
never touch files in use. If the check fails (offline), it keeps the version it has. The
page shows the version in the top right.

A rebuild ships whatever `uv.lock` pins; to move that forward:

    uv lock --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs && uv sync

## Icon
`assets/icon.png` is drawn by `uv run --no-project --with pillow python assets/make_icon.py`;
run `./build_app.sh --install` after changing it.
