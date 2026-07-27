#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
# ]
# ///
"""
Use the configured LLM (with web search) to find real download URLs for WADs
with placeholder URLs.

Usage:
  uv run scripts/fix_wad_urls.py                    # Analyze only (dry run)
  uv run scripts/fix_wad_urls.py --fix              # Actually fix the JSON files
  uv run scripts/fix_wad_urls.py --fix --limit 5    # Fix first 5 only
"""

import argparse
import json
import re
import time
from pathlib import Path

import llm

PENDING_DIR = Path(__file__).parent.parent / "content" / "wads-pending"
WADS_DIR = Path(__file__).parent.parent / "content" / "wads"
PLACEHOLDER_PATTERNS = ["example.com", "placeholder"]

# Rate limiting
REQUESTS_PER_MINUTE = 10
REQUEST_DELAY = 60 / REQUESTS_PER_MINUTE


def is_placeholder_url(url: str) -> bool:
    """Check if URL is a placeholder."""
    return any(p in url.lower() for p in PLACEHOLDER_PATTERNS)


def get_wads_needing_urls() -> list[dict]:
    """Get list of WADs from pending directory (all need URLs)."""
    results = []

    if not PENDING_DIR.exists():
        return results

    for wad_file in sorted(PENDING_DIR.glob("*.json")):
        try:
            data = json.loads(wad_file.read_text())
        except json.JSONDecodeError:
            continue

        results.append({
            "file": wad_file,
            "data": data,
            "slug": data.get("slug", wad_file.stem),
            "title": data.get("title", ""),
            "wiki_url": data.get("urls", [""])[0] if data.get("urls") else "",
        })

    return results


def find_download_url(wad: dict) -> dict | None:
    """Use the configured LLM with web search to find a download URL for a WAD."""
    prompt = f"""Find the DIRECT download URL for this Doom WAD:

Title: {wad['title']}
DoomWiki URL: {wad['wiki_url']}

I need a URL that directly downloads a file (.zip, .pk3, .wad, .7z).
NOT: forum posts, download pages, or "click here to download" pages.

Preferred sources (in order):
1. idGames archive - URL pattern: youfailit.net/pub/idgames/.../*.zip
2. ModDB - URL pattern: moddb.com/downloads/mirror/... (the actual download, not the page)
3. GitHub releases - URL pattern: github.com/.../releases/download/...
4. Archive.org - URL pattern: archive.org/download/...
5. Mediafire - URL pattern: mediafire.com/file/...
6. Dropbox, Google Drive direct links

Return ONLY a JSON object:
- url: the DIRECT download link (must end in .zip, .pk3, .wad, .7z, or be from idgames/moddb/github CDN)
- filename: the filename with extension
- type: one of "idgames", "moddb", "github", "direct"
- confidence: "high" (verified link), "medium" (likely works), "low" (uncertain)

Return {{"error": "not found"}} if you can't find a DIRECT download link.

NO forum threads. NO download pages. NO HTML pages. Only direct file downloads."""

    try:
        result = llm.chat_json(prompt, web_search=True)

        if "error" in result:
            return None

        # Validate the URL looks like a direct download
        url = result.get("url", "").lower()
        is_direct = (
            url.endswith((".zip", ".pk3", ".wad", ".7z", ".pk7")) or
            "youfailit.net/pub/idgames/" in url or
            "doomworld.com/idgames/" in url or
            "/downloads/mirror/" in url or  # ModDB CDN
            "/releases/download/" in url or  # GitHub releases
            "archive.org/download/" in url or
            "mediafire.com/file/" in url or  # Mediafire
            "dropbox.com/" in url or  # Dropbox
            "drive.google.com/" in url  # Google Drive
        )

        if not is_direct:
            print(f"    ⚠ URL rejected (not direct): {result.get('url', '')[:60]}...")
            return None

        return result

    except json.JSONDecodeError as e:
        print(f"    JSON parse error: {e}")
        return None
    except Exception as e:
        print(f"    API error: {e}")
        return None


def update_wad_file(wad: dict, url_info: dict) -> bool:
    """Update WAD JSON file with new URL and move to main wads directory."""
    data = wad["data"]
    src_file = wad["file"]
    dst_file = WADS_DIR / src_file.name

    data["downloads"] = [{
        "type": url_info["type"],
        "url": url_info["url"],
        "filename": url_info["filename"],
    }]

    try:
        # Write updated data to main wads directory
        dst_file.write_text(json.dumps(data, indent=2))
        # Remove from pending
        src_file.unlink()
        return True
    except Exception as e:
        print(f"    Write error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Fix WAD placeholder URLs using the configured LLM")
    parser.add_argument("--fix", action="store_true", help="Actually update the JSON files")
    parser.add_argument("--limit", type=int, help="Limit to first N WADs")
    args = parser.parse_args()

    print(f"WAD URL Fixer (using {llm.load_llm_config()['model']})")
    print("=" * 60)

    wads = get_wads_needing_urls()

    if args.limit:
        wads = wads[:args.limit]

    print(f"WADs needing URLs: {len(wads)}")
    print(f"Mode: {'FIX' if args.fix else 'DRY RUN'}")
    print("=" * 60)

    if not wads:
        print("No WADs need fixing!")
        return

    stats = {"found": 0, "not_found": 0, "updated": 0, "errors": 0}

    for i, wad in enumerate(wads, 1):
        print(f"\n[{i}/{len(wads)}] {wad['title']}")
        print(f"    Wiki: {wad['wiki_url']}")

        url_info = find_download_url(wad)

        if url_info:
            stats["found"] += 1
            print(f"    ✓ Found: {url_info['url']}")
            print(f"      Type: {url_info['type']}, Confidence: {url_info.get('confidence', 'unknown')}")

            if args.fix:
                if update_wad_file(wad, url_info):
                    stats["updated"] += 1
                    print(f"    ✓ Moved to wads/{wad['file'].name}")
                else:
                    stats["errors"] += 1
        else:
            stats["not_found"] += 1
            print(f"    ✗ No URL found")

        # Rate limiting
        if i < len(wads):
            time.sleep(REQUEST_DELAY)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total processed: {len(wads)}")
    print(f"URLs found:      {stats['found']}")
    print(f"Not found:       {stats['not_found']}")
    if args.fix:
        print(f"Files updated:   {stats['updated']}")
        print(f"Errors:          {stats['errors']}")


if __name__ == "__main__":
    main()
