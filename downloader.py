"""Core download logic (wraps yt-dlp) plus a small CLI.

Usage:
    uv run downloader.py URL [URL ...] [--quality best|1080|720|480|audio|gif] [--out DIR]
                         [--start 1:23] [--end 2:10]

URLs can be videos or playlists. --start/--end save only that part of each video.
"""

import argparse
import math
import subprocess
from pathlib import Path
from typing import Callable

import engine

engine.activate()  # a newer downloaded yt-dlp, if there is one, before the import below

import imageio_ffmpeg  # noqa: E402
import yt_dlp  # noqa: E402
from yt_dlp.postprocessor.ffmpeg import FFmpegPostProcessor  # noqa: E402
from yt_dlp.utils import download_range_func  # noqa: E402

DEFAULT_OUT = Path.home() / "Downloads" / "video_downloader"
QUALITIES = ["best", "1080", "720", "480", "audio", "gif"]
GIF_MAX_SECONDS = 60  # longer GIFs get huge (hundreds of MB)
GIF_WIDTH = 480
GIF_FPS = "25/2"  # GIF frame delays are in 1/100 s: 12.5 fps (8/100) plays at true speed, 15 fps doesn't
# A clip either comes from the whole file, downloaded and cut here (the cut itself takes about a
# second), or ffmpeg fetches just the clip from YouTube. Measured: ffmpeg took 6-31 s for 5-40 s
# clips; whole files came at 2-11 MB/s depending on the moment. So the whole file is used when,
# even at a slowish 4 MB/s, it would arrive within the clip's length (at least 10 s).
WHOLE_FILE_BYTES_PER_SECOND = 4e6

# on_progress(stage, percent, speed): stage is "downloading", "cutting" (making a clip; no
# percentage) or "processing"
ProgressFn = Callable[[str, float, str], None]


def parse_time(text: str | float | None) -> float | None:
    """'83', '1:23', '1:02:03' or '1:23.5' → seconds. Blank → None."""
    if text is None or isinstance(text, (int, float)):
        return None if text is None else float(text)
    text = text.strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"Not a time: {text}")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"Not a time: {text}") from None
    if any(n < 0 for n in nums) or any(n >= 60 for n in nums[1:]):
        raise ValueError(f"Not a time: {text}")
    return sum(n * 60 ** i for i, n in enumerate(reversed(nums)))


def _stamp(seconds: float) -> str:
    """1h02m03s / 1m23s / 45s, safe in file names (no colons)."""
    s = int(seconds)
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h{m:02d}m{s:02d}s" if h else f"{m}m{s:02d}s" if m else f"{s}s"


def _format_for(quality: str) -> str:
    if quality == "audio":
        return "bestaudio/best"
    if quality == "gif":  # video only; H.264 when there is one, as it cuts fastest
        return "bestvideo[height<=720][vcodec^=avc1]/bestvideo[height<=720]/best[height<=720]/best"
    if quality == "best":
        return "bestvideo+bestaudio/best"
    return f"bestvideo[height<={quality}]+bestaudio/best[height<={quality}]"


def build_options(quality: str, out_dir: Path, start: float | None = None,
                  end: float | None = None, convert_audio: bool = True) -> dict:
    opts = {
        "format": _format_for(quality),
        "outtmpl": str(out_dir / "%(title)s [%(id)s].%(ext)s"),
        "ffmpeg_location": imageio_ffmpeg.get_ffmpeg_exe(),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if quality == "audio" and convert_audio:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    elif quality != "gif":
        opts["merge_output_format"] = "mp4"
    if start is not None or end is not None:
        # ffmpeg fetches only this part; the cut is re-encoded so it lands on the exact times
        opts["download_ranges"] = download_range_func(None, [(start or 0, math.inf if end is None else end)])
        opts["force_keyframes_at_cuts"] = True
    return opts


def clip_error(duration: float | None, start: float | None, end: float | None,
               quality: str) -> str | None:
    """Why this start/end can't be used, or None if it's fine."""
    if start is not None and duration and start >= duration:
        return "Start is past the end of the video"
    if end is not None and duration and end > duration + 1:
        return "End is past the end of the video"
    if end is not None and end <= (start or 0):
        return "End must come after start"
    if quality == "gif":
        length = (end if end is not None else duration or math.inf) - (start or 0)
        if length > GIF_MAX_SECONDS:
            return f"GIFs need a clip of {GIF_MAX_SECONDS} s or less: set a start and end"
    return None


def _ffmpeg(*args: str) -> None:
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-y", *args],
                   check=True, capture_output=True)


def _span(start: float | None, end: float | None) -> list[str]:
    """Input options that read only start..end (seeking first, so long files cut quickly)."""
    return (["-ss", str(start)] if start else []) + (["-to", str(end)] if end is not None else [])


def make_gif(src: Path, dest: Path, start: float | None = None, end: float | None = None) -> None:
    """Two-pass palette GIF: much cleaner colours than a straight conversion.
    Never wider than the source, so low-resolution videos aren't blown up."""
    graph = (f"fps={GIF_FPS},scale='min({GIF_WIDTH},iw)':-2:flags=lanczos,split[a][b];"
             "[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle")
    _ffmpeg(*_span(start, end), "-i", str(src), "-an", "-vf", graph, "-loop", "0", str(dest))


def cut(src: Path, dest: Path, start: float | None, end: float | None, quality: str) -> None:
    """An exact clip of a downloaded file: re-encoded, so it starts and ends on the given times
    rather than the nearest keyframes. Audio goes straight to mp3, encoded once."""
    if quality == "audio":
        _ffmpeg(*_span(start, end), "-i", str(src), "-vn", "-c:a", "libmp3lame", "-b:a", "192k", str(dest))
    else:
        _ffmpeg(*_span(start, end), "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(dest))


def _whole_file_is_faster(info: dict, start: float | None, end: float | None) -> bool:
    formats = info.get("requested_formats") or [info]
    sizes = [f.get("filesize") or f.get("filesize_approx") for f in formats]
    if not all(sizes):
        return False  # size unknown: let ffmpeg fetch just the clip
    length = (end if end is not None else info.get("duration") or 0) - (start or 0)
    return sum(sizes) / WHOLE_FILE_BYTES_PER_SECOND <= max(length, 10)


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
    # No ffmpeg needed just to look something up (the app may still be fetching it).
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "extract_flat": "in_playlist"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("_type") == "playlist":
        entries = [_summary(e) for e in info.get("entries") or []
                   if e and e.get("title") not in UNAVAILABLE]
        return {"type": "playlist", "title": info.get("title"), "entries": entries}
    return {"type": "video", **_summary(info)}


def download(url: str, quality: str = "best", out_dir: Path = DEFAULT_OUT,
             on_progress: ProgressFn | None = None, start: float | None = None,
             end: float | None = None) -> Path:
    """Download one video (or the part from start to end, in seconds) and return the final file."""
    out_dir.mkdir(parents=True, exist_ok=True)
    clip = start is not None or end is not None
    with yt_dlp.YoutubeDL(build_options(quality, out_dir)) as ydl:
        info = ydl.extract_info(url, download=False)
    problem = clip_error(info.get("duration"), start, end, quality)
    if problem:
        raise ValueError(problem)
    local = clip and _whole_file_is_faster(info, start, end)   # see WHOLE_FILE_BYTES_PER_SECOND
    remote = clip and not local
    if remote:
        # yt-dlp checks for ffmpeg before cutting without looking at ffmpeg_location.
        FFmpegPostProcessor._ffmpeg_location.set(imageio_ffmpeg.get_ffmpeg_exe())
        opts = build_options(quality, out_dir, start, end)
    else:
        opts = build_options(quality, out_dir, convert_audio=not local)
    with yt_dlp.YoutubeDL(opts) as ydl:
        # "best" fetches video and audio separately; report them as one 0–100%.
        streams = len(info.get("requested_formats") or [None])
        finished = 0

        def hook(d: dict) -> None:
            nonlocal finished
            if not on_progress:
                return
            if remote:  # ffmpeg cuts video and audio together and only reports the end
                if d["status"] == "finished":
                    on_progress("processing", 100, "")
            elif d["status"] == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                frac = d["downloaded_bytes"] / total if total else 0
                pct = min(99.9, (finished + frac) / streams * 100)
                on_progress("downloading", round(pct, 1), (d.get("_speed_str") or "").strip())
            elif d["status"] == "finished":
                finished += 1
                if finished >= streams:
                    on_progress("processing", 100, "")

        ydl.add_progress_hook(hook)
        if remote and on_progress:
            on_progress("cutting", 0, "")
        info = ydl.process_ie_result(info, download=True)
    path = Path(info["requested_downloads"][0]["filepath"])
    # "Title [id] 1m23s-2m10s.mp4", so a clip never looks like the whole video
    tail = f" {_stamp(start or 0)}-{_stamp(end if end is not None else info.get('duration') or 0)}" if clip else ""
    if local:
        if on_progress:
            on_progress("cutting", 0, "")
        ext = {"audio": ".mp3", "gif": ".gif"}.get(quality, ".mp4")
        dest = path.with_name(f"{path.stem}{tail}{ext}")
        if quality == "gif":
            make_gif(path, dest, start, end)
        else:
            cut(path, dest, start, end, quality)
        path.unlink()
        return dest
    if clip:
        path = path.rename(path.with_name(f"{path.stem}{tail}{path.suffix}"))
    if quality == "gif":
        if on_progress:
            on_progress("processing", 100, "")
        gif = path.with_suffix(".gif")
        make_gif(path, gif)
        path.unlink()
        path = gif
    return path


def _cli_progress(stage: str, pct: float, speed: str) -> None:
    if stage == "downloading":
        print(f"\r  {pct:5.1f}%  {speed:<12}", end="", flush=True)
    elif stage == "cutting":
        print("  cutting the clip…")
    else:
        print("\r  download done, processing…")


def main() -> None:
    p = argparse.ArgumentParser(description="Download YouTube videos and playlists.")
    p.add_argument("urls", nargs="+", metavar="URL")
    p.add_argument("--quality", "-q", choices=QUALITIES, default="best")
    p.add_argument("--out", "-o", type=Path, default=DEFAULT_OUT)
    p.add_argument("--start", type=parse_time, help="save from here, e.g. 1:23")
    p.add_argument("--end", type=parse_time, help="save up to here, e.g. 2:10")
    args = p.parse_args()

    for url in args.urls:
        info = get_info(url)
        if info["type"] == "playlist":
            print(f"Playlist: {info['title']} ({len(info['entries'])} videos)")
        for video in info["entries"] if info["type"] == "playlist" else [info]:
            print(f"→ {video['title']}  ({video['uploader']})")
            try:
                path = download(video["url"], args.quality, args.out, _cli_progress,
                                args.start, args.end)
                print(f"✓ Saved to {path}")
            except (yt_dlp.utils.DownloadError, ValueError, subprocess.CalledProcessError) as e:
                print(f"\r✗ {e}")


if __name__ == "__main__":
    main()
