#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "requests",
#     "beautifulsoup4",
#     "markdownify",
# ]
# ///
"""
Batch extract WAD summaries for all WADs with DoomWiki URLs.

Usage:
    uv run scripts/batch_extract_summaries.py
    uv run scripts/batch_extract_summaries.py --limit 10
"""

import argparse
import json
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

import llm

SCRIPTS_DIR = Path(__file__).parent
WADS_DIR = SCRIPTS_DIR.parent / "content" / "wads"
DIFFICULTY_SCALE_FILE = SCRIPTS_DIR / "data" / "difficulty_scale.txt"
OUTPUT_FILE = SCRIPTS_DIR / "data" / "all_wad_summaries.json"


def load_difficulty_scale() -> str:
    return DIFFICULTY_SCALE_FILE.read_text()


def build_prompt(difficulty_scale: str, authors: list[str]) -> str:
    authors_str = ", ".join(authors) if authors else "unknown"

    return f"""{difficulty_scale}

You are analyzing DoomWiki content about a Doom WAD/mod.
Authors/creators: {authors_str}

Extract and return as JSON:

{{
  "difficulty_rating": <decimal 1.0-10.0, NEVER an integer>,
  "vibe": "<one sentence painting the WORLD - what does this place look/feel like?>",
  "praise": "<one sentence weaving in creator names naturally - what's special about this?>"
}}

VIBE rules:
- Paint the WORLD, not the gameplay
- NO idle words (exclusively, breathtaking, masterfully)
- NO generic Doom stuff (dodging projectiles, slaughter, combat)
- NO creator names (separate field for authors)
- BE SPECIFIC to what makes this WAD's world unique

Good vibe examples:
- "A neon-soaked hallucination melding Mayan ruins, flying saucers, and Illuminati iconography into a technicolor conspiracy theory."
- "Jagged, spiky architecture suspends impossible geometry over a boundless void, saturated in a feverish collision of neon teal, burnt orange, and deep purple."
- "A claustrophobic elevator ride crashing from rooftop to sewer, stopping at every cramped boardroom and blood-soaked bathroom in the UAC headquarters."

PRAISE rules:
- Weave creator names INTO the sentence naturally (not tagged on at end)
- Don't START with creator name
- Be specific about what's good
- ~20 words, no filler

Good praise example:
- "Neon visuals and game-changing monsters by skillsaw pair perfectly with stewboy's tunes for a surreal, endless flow of action."

Return ONLY valid JSON, no markdown or explanation."""


def fetch_and_convert(url: str) -> str | None:
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        content = soup.find(id="mw-content-text")
        if not content:
            content = soup.find("main") or soup.find("article") or soup.find("body")

        if content:
            for selector in ['.mw-editsection', '#toc', '.printfooter', 'nav', 'script', 'style']:
                for tag in content.select(selector):
                    tag.decompose()
            return md(str(content), heading_style="ATX")
        return None
    except requests.RequestException as e:
        print(f"    Error: {e}", file=sys.stderr)
        return None


def get_doomwiki_url(entry: dict) -> str | None:
    for url in entry.get("urls", []):
        if "doomwiki.org" in url:
            return url
    return None


def extract_summary(content: str, prompt: str) -> dict:
    return llm.chat_json(
        f"Content about a Doom WAD:\n\n{content}\n\n{prompt}",
        temperature=0.3,
    )


def process_wad(filepath: Path, difficulty_scale: str) -> dict | None:
    entry = json.loads(filepath.read_text())
    slug = filepath.stem

    wiki_url = get_doomwiki_url(entry)
    if not wiki_url:
        return None

    authors = [a.get("name", "") for a in entry.get("authors", [])]

    content = fetch_and_convert(wiki_url)
    if not content:
        return None

    prompt = build_prompt(difficulty_scale, authors)
    result = extract_summary(content, prompt)

    return {
        "slug": slug,
        "title": entry["title"],
        "authors": authors,
        "difficulty_rating": result.get("difficulty_rating"),
        "vibe": result.get("vibe"),
        "praise": result.get("praise"),
    }


def main():
    parser = argparse.ArgumentParser(description="Batch extract WAD summaries")
    parser.add_argument("--limit", type=int, help="Limit to first N WADs")
    parser.add_argument("--resume", action="store_true", help="Resume from existing output")
    args = parser.parse_args()

    difficulty_scale = load_difficulty_scale()

    # Get all WAD files
    wad_files = sorted(WADS_DIR.glob("*.json"))

    # Filter to those with DoomWiki URLs
    wads_to_process = []
    for filepath in wad_files:
        entry = json.loads(filepath.read_text())
        if get_doomwiki_url(entry):
            wads_to_process.append(filepath)

    if args.limit:
        wads_to_process = wads_to_process[:args.limit]

    # Load existing results if resuming
    existing = {}
    if args.resume and OUTPUT_FILE.exists():
        data = json.loads(OUTPUT_FILE.read_text())
        existing = {r["slug"]: r for r in data}
        print(f"Resuming with {len(existing)} existing results", file=sys.stderr)

    print(f"Processing {len(wads_to_process)} WADs...", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    results = list(existing.values())
    processed = 0
    errors = 0

    for i, filepath in enumerate(wads_to_process, 1):
        slug = filepath.stem

        if slug in existing:
            print(f"[{i}/{len(wads_to_process)}] {slug} - skipped (exists)", file=sys.stderr)
            continue

        print(f"[{i}/{len(wads_to_process)}] {slug}...", end=" ", file=sys.stderr, flush=True)

        try:
            result = process_wad(filepath, difficulty_scale)
            if result:
                results.append(result)
                processed += 1
                print(f"OK (difficulty: {result['difficulty_rating']})", file=sys.stderr)
            else:
                errors += 1
                print("SKIP (no wiki URL or fetch failed)", file=sys.stderr)
        except Exception as e:
            errors += 1
            print(f"ERROR: {e}", file=sys.stderr)

        # Save progress every 10 WADs
        if processed % 10 == 0:
            OUTPUT_FILE.write_text(json.dumps(results, indent=2) + "\n")

        # Rate limit
        time.sleep(0.3)

    # Final save
    OUTPUT_FILE.write_text(json.dumps(results, indent=2) + "\n")

    print("=" * 60, file=sys.stderr)
    print(f"Done! Processed: {processed}, Errors: {errors}", file=sys.stderr)
    print(f"Output: {OUTPUT_FILE}", file=sys.stderr)


if __name__ == "__main__":
    main()
