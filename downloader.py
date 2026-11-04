"""Core download logic (wraps yt-dlp) plus a small CLI.

Usage:
    uv run downloader.py URL [URL ...] [--quality best|1080|720|480|audio] [--out DIR]

URLs can be videos or playlists.
"""

import argparse
from pathlib import Path
from typing import Callable

import engine

engine.activate()  # a newer downloaded yt-dlp, if there is one, before the import below

import imageio_ffmpeg  # noqa: E402
import yt_dlp  # noqa: E402

DEFAULT_OUT = Path.home() / "Downloads" / "video_downloader"
QUALITIES = ["best", "1080", "720", "480", "audio"]

# on_progress(stage, percent, speed): stage is "downloading" or "processing"
ProgressFn = Callable[[str, float, str], None]


def _format_for(quality: str) -> str:
    if quality == "audio":
        return "bestaudio/best"
    if quality == "best":
        return "bestvideo+bestaudio/best"
    return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"


def build_options(quality: str, out_dir: Path) -> dict:
    opts = {
        "format": _format_for(quality),
        "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if quality == "audio":
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    else:
        opts["merge_output_format"] = "mp4"
    return opts


UNAVAILABLE = {"[Private video]", "[Deleted video]"}


def _summary(info: dict) -> dict:
    vid = info["id"]
    return {
        "id": vid,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "title": info.get("title"),
        "uploader": info.get("uploader") or info.get("channel"),
        "duration": info.get("duration"),
        "thumbnail": info.get("thumbnail") or f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
    }


def get_info(url: str) -> dict:
    """Fetch metadata without downloading. Playlists come back with their entries.

    A watch link that also carries &list= is treated as the single video.
    """
    opts = {"quiet": True, "noplaylist": True, "extract_flat": "in_playlist",
            "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe()}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [_summary(e) for e in info.get("entries") or []
                   if e and e.get("title") not in UNAVAILABLE]
        return {"type": "playlist", "title": info.get("title"), "entries": entries}
    return {"type": "video", **_summary(info)}


def download(url: str, quality: str = "best", out_dir: Path = DEFAULT_OUT,
             on_progress: ProgressFn | None = None) -> Path:
    """Download one video and return the final file path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    with yt_dlp.YoutubeDL(build_options(quality, out_dir)) as ydl:
        info = ydl.extract_info(url, download=False)
        # "best" fetches video and audio separately; report them as one 0–100%.
        streams = len(info.get("requested_formats") or [None])
        finished = 0

        def hook(d: dict) -> None:
            nonlocal finished
            if not on_progress:
                return
            if d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                frac = d["downloaded_bytes"] / total if total else 0
                pct = min(99.9, (finished + frac) / streams * 100)
                on_progress("downloading", round(pct, 1), (d.get("_speed_str") or "").strip())
            elif d["status"] == "finished":
                finished += 1
                if finished >= streams:
                    on_progress("processing", 100, "")

        ydl.add_progress_hook(hook)
        info = ydl.process_ie_result(info, download=True)
    return Path(info["requested_downloads"][0]["filepath"])


def _cli_progress(stage: str, pct: float, speed: str) -> None:
    if stage == "downloading":
        print(f"\r  {pct:5.1f}%  {speed:<12}", end="", flush=True)
    else:
        print("\r  download done, processing…")


def main() -> None:
    p = argparse.ArgumentParser(description="Download YouTube videos and playlists.")
    p.add_argument("urls", nargs="+", metavar="URL")
    p.add_argument("--quality", "-q", choices=QUALITIES, default="best")
    p.add_argument("--out", "-o", type=Path, default=DEFAULT_OUT)
    args = p.parse_args()

    for url in args.urls:
        info = get_info(url)
        if info["type"] == "playlist":
            print(f"Playlist: {info['title']} ({len(info['entries'])} videos)")
        for video in info["entries"] if info["type"] == "playlist" else [info]:
            print(f"→ {video['title']}  ({video['uploader']})")
            try:
                path = download(video["url"], args.quality, args.out, _cli_progress)
                print(f"✓ Saved to {path}")
            except yt_dlp.utils.DownloadError as e:
                print(f"\r✗ {e}")


if __name__ == "__main__":
    main()
