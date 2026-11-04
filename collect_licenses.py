"""Gathers the license texts of everything the packaged app contains into build/licenses.

build_app.sh runs this and puts the folder inside the app (Contents/Resources/licenses).
Usage: uv run python collect_licenses.py <deno version>
"""

import importlib.metadata as md
import re
import shutil
import subprocess
import sys
import sysconfig
import urllib.request
from pathlib import Path

OUT = Path("build/licenses")
# Files named like LICENSE, LICENSE.txt, COPYING.md, NOTICE: not code that merely mentions them.
LICENSE_FILE = re.compile(r"^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)([._-][\w.-]*)?$", re.I)
TEXT_SUFFIXES = {"", ".txt", ".md", ".rst", ".mit", ".apache", ".bsd", ".psf"}


def runtime_packages() -> list[str]:
    """What the app ships: the project's dependencies, without build-only tools."""
    out = subprocess.run(["uv", "export", "--frozen", "--no-default-groups", "--no-hashes",
                          "--no-emit-project", "--no-header", "--no-annotate"],
                         capture_output=True, text=True, check=True).stdout
    return sorted({re.split(r"[=<>;\[ ]", line.strip())[0]
                   for line in out.splitlines() if line.strip() and not line.startswith(("#", " "))})


def license_name(meta) -> str:
    expression = meta.get("License-Expression")
    if expression:
        return expression
    classifiers = [c.split(" :: ")[-1] for c in meta.get_all("Classifier") or [] if c.startswith("License ::")]
    short = (meta.get("License") or "").strip().splitlines()
    return ", ".join(classifiers) or (short[0][:80] if short else "see the files in its folder")


def from_source_release(name: str, version: str, folder: Path) -> None:
    """Some wheels leave their license out; take it from the source release on PyPI instead."""
    import io
    import json
    import tarfile
    import zipfile
    info = json.loads(urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{version}/json", timeout=60).read())
    sdist = next((u for u in info["urls"] if u["packagetype"] == "sdist"), None)
    if not sdist:
        return
    data = io.BytesIO(urllib.request.urlopen(sdist["url"], timeout=120).read())
    if sdist["filename"].endswith(".zip"):
        archive = zipfile.ZipFile(data)
        members = [(n, lambda n=n: archive.read(n)) for n in archive.namelist()]
    else:
        archive = tarfile.open(fileobj=data)
        members = [(m.name, lambda m=m: archive.extractfile(m).read()) for m in archive.getmembers() if m.isfile()]
    for path, read in members:
        parts = path.split("/")
        if len(parts) == 2 and LICENSE_FILE.match(parts[1]):  # top level of the release only
            (folder / parts[1]).write_bytes(read())


def project_url(meta) -> str:
    urls = [u.split(",", 1)[-1].strip() for u in meta.get_all("Project-URL") or []]
    return meta.get("Home-page") or next((u for u in urls if "github" in u or "source" in u.lower()), urls[0] if urls else "")


def main(deno_version: str) -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    rows = []

    for name in runtime_packages():
        try:
            dist = md.distribution(name)
        except md.PackageNotFoundError:
            continue  # a dependency for other platforms only
        folder = OUT / f"{dist.metadata['Name']}-{dist.version}"
        folder.mkdir()
        for f in dist.files or []:
            in_dist_info = ".dist-info/" in str(f)
            if (LICENSE_FILE.match(f.name) or (in_dist_info and "/licenses/" in str(f))) \
                    and Path(f.name).suffix.lower() in TEXT_SUFFIXES | ({Path(f.name).suffix.lower()} if in_dist_info else set()):
                path = Path(f.locate())
                if path.is_file():
                    shutil.copy(path, folder / str(f).split(".dist-info/")[-1].replace("/", "_"))
        if not any(folder.iterdir()):
            from_source_release(dist.metadata["Name"], dist.version, folder)
        url = project_url(dist.metadata)
        if not any(folder.iterdir()):  # declared in its metadata only
            author = dist.metadata.get("Author") or dist.metadata.get("Author-email") or "its authors"
            note = (f"{dist.metadata['Name']} {dist.version} declares the license "
                    f"\"{license_name(dist.metadata)}\" in its package metadata but ships no license file.\n"
                    f"Author (from its metadata): {author}\nSource: {url}\n")
            if "MIT" in license_name(dist.metadata):
                note += "\n" + Path("LICENSE").read_text().replace("Copyright (c) 2026 AnuBis7Star",
                                                                   f"Copyright (c) {author}")
            (folder / "LICENSE-NOTE.txt").write_text(note)
        rows.append(f"{dist.metadata['Name']} {dist.version}: {license_name(dist.metadata)}"
                    + (f" ({url})" if url else ""))

    python = OUT / f"Python-{sys.version.split()[0]}"
    python.mkdir()
    shutil.copy(Path(sysconfig.get_paths()["stdlib"]) / "LICENSE.txt", python / "LICENSE.txt")
    rows.append(f"Python {sys.version.split()[0]}: PSF-2.0 (includes the notices for its bundled libraries)")

    deno = OUT / f"Deno-{deno_version}"
    deno.mkdir()
    url = f"https://raw.githubusercontent.com/denoland/deno/v{deno_version}/LICENSE.md"
    (deno / "LICENSE.md").write_bytes(urllib.request.urlopen(url, timeout=60).read())
    rows.append(f"Deno {deno_version}: MIT (the official release, https://github.com/denoland/deno)")

    own = OUT / "Video Downloader"
    own.mkdir()
    shutil.copy("LICENSE", own / "LICENSE")

    (OUT / "README.txt").write_text(
        "Video Downloader is MIT-licensed (see \"Video Downloader/LICENSE\").\n"
        "It contains the components below, each under its own license; their license texts\n"
        "are in the folders next to this file.\n\n"
        + "\n".join(f"- {r}" for r in sorted(rows, key=str.lower))
        + "\n\nNot included: ffmpeg. Its build is GPL-licensed, so the app downloads it on first\n"
          "launch from PyPI (the imageio-ffmpeg package, https://github.com/imageio/imageio-ffmpeg),\n"
          "where its license and source information are published.\n"
          "The app was packaged with PyInstaller, whose license lets apps it builds use any license.\n")
    print(f"{len(rows)} components, licenses in {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])
