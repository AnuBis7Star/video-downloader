# Video Downloader

Personal YouTube downloader built on [yt-dlp](https://github.com/yt-dlp/yt-dlp).
ffmpeg is bundled via `imageio-ffmpeg`; YouTube's JS challenges are solved with Deno + `yt-dlp-ejs`.

## Mac app
    ./make_app.sh        # builds ~/Applications/Video Downloader.app

Open it from Launchpad, Spotlight or `~/Applications`. It's the same page in its own window,
with the server running inside the app. The app runs this project folder in place, so if you
move the folder, run `./make_app.sh` again. Log: `~/Library/Logs/VideoDownloader.log`.
Quitting stops any running download and removes its partial file; finished files stay.

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
YouTube changes often and yt-dlp follows within days. The app (and `uv run app.py`) upgrades
yt-dlp and its challenge solver at launch and every 6 hours, through uv, so `uv.lock` records
the version in use (see `engine.py`). It only swaps packages while nothing is downloading or
looking up a link, and each download and lookup runs in a fresh process, so a new version
applies without a restart. The page shows the version in the top right.

To update by hand:

    uv lock --upgrade-package yt-dlp --upgrade-package yt-dlp-ejs && uv sync

## Icon
`assets/icon.png` is drawn by `uv run --no-project --with pillow python assets/make_icon.py`;
run `./make_app.sh` after changing it.
