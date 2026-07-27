#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
# ]
# ///
"""
Link-rot check for catalog download URLs.

For every entry in content/wads/, fetches the first download URL and verifies
it responds 200 with real file bytes (zip/7z/gzip/rar/WAD magic), not an HTML
page. Requests are sequential with a per-request delay to stay polite — run
this occasionally (e.g. before a release), not in CI or a loop.

Usage:
    uv run scripts/check_download_links.py
    uv run scripts/check_download_links.py --slug sigil --slug epic-2
    uv run scripts/check_download_links.py --delay 2.0
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests

WADS_DIR = Path(__file__).parent.parent / "content" / "wads"
HEADERS = {"User-Agent": "DoomLauncher-WikiScraper/1.0"}

ARCHIVE_MAGIC = (b"PK", b"7z", b"\x1f\x8b")
WAD_MAGIC = (b"IWAD", b"PWAD")


def check_url(url: str) -> str | None:
    """Return a problem description, or None if the URL serves a real file."""
    # GET with stream (some hosts reject HEAD); only headers + 4 bytes are read
    response = requests.get(
        url, headers=HEADERS, stream=True, timeout=45, allow_redirects=True
    )
    try:
        if response.status_code != 200:
            return f"HTTP {response.status_code}"
        content_type = response.headers.get("content-type", "")
        first_bytes = next(response.iter_content(4), b"")
        if "text/html" in content_type:
            return f"HTML page, not a file (content-type: {content_type})"
        if first_bytes[:2] not in ARCHIVE_MAGIC and first_bytes[:4] not in (
            *WAD_MAGIC,
            b"Rar!",
        ):
            return f"unexpected leading bytes {first_bytes!r} (content-type: {content_type})"
        return None
    finally:
        response.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Check catalog download URLs for link rot")
    parser.add_argument("--slug", action="append", help="Check only these slugs (repeatable)")
    parser.add_argument(
        "--delay", type=float, default=1.0, help="Seconds between requests (default: 1.0)"
    )
    args = parser.parse_args()

    paths = sorted(WADS_DIR.glob("*.json"))
    if args.slug:
        paths = [p for p in paths if p.stem in args.slug]

    checked = 0
    no_download = []
    problems = []

    for i, path in enumerate(paths):
        entry = json.loads(path.read_text())
        downloads = entry.get("downloads", [])
        if not downloads:
            no_download.append(entry["slug"])
            continue

        url = downloads[0]["url"]
        print(f"[{checked + 1}] {entry['slug']}...", end=" ", flush=True)
        try:
            problem = check_url(url)
        except requests.RequestException as e:
            problem = f"{type(e).__name__}: {e}"
        checked += 1

        if problem:
            problems.append((entry["slug"], url, problem))
            print(f"PROBLEM: {problem}")
        else:
            print("ok")

        if i < len(paths) - 1:
            time.sleep(args.delay)

    print()
    print(f"checked: {checked}  no-download: {len(no_download)}  problems: {len(problems)}")
    if no_download:
        print(f"no-download entries (expected for commercial wads): {', '.join(no_download)}")
    if problems:
        print("\nproblems:")
        for slug, url, problem in problems:
            print(f"  {slug}: {problem}\n    {url}")
        sys.exit(1)


if __name__ == "__main__":
    main()
