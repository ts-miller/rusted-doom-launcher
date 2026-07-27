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
Test WAD summary extraction with difficulty scale, vibe, and praise.

Usage:
    uv run scripts/test_wad_summary.py ancient-aliens
    uv run scripts/test_wad_summary.py sunlust eviternity going-down
"""

import argparse
import json
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

import llm

SCRIPTS_DIR = Path(__file__).parent
HEADERS = {"User-Agent": "DoomLauncher-WikiScraper/1.0"}
WADS_DIR = SCRIPTS_DIR.parent / "content" / "wads"
DIFFICULTY_SCALE_FILE = SCRIPTS_DIR / "data" / "difficulty_scale.txt"


def load_difficulty_scale() -> str:
    """Load difficulty scale from file."""
    return DIFFICULTY_SCALE_FILE.read_text()


def build_prompt(difficulty_scale: str, authors: list[str]) -> str:
    """Build extraction prompt with difficulty scale and author info."""
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
    """Fetch URL and convert to markdown."""
    try:
        response = requests.get(url, headers=HEADERS, timeout=30)
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
        print(f"  Error fetching {url}: {e}", file=sys.stderr)
        return None


def get_doomwiki_url(entry: dict) -> str | None:
    """Get DoomWiki URL from entry."""
    for url in entry.get("urls", []):
        if "doomwiki.org" in url:
            return url
    return None


def extract_summary(content: str, prompt: str) -> dict:
    """Use the LLM to extract summary from content."""
    return llm.chat_json(
        f"Content about a Doom WAD:\n\n{content}\n\n{prompt}", temperature=0.3
    )


def process_wad(slug: str, difficulty_scale: str) -> dict:
    """Process a single WAD."""
    filepath = WADS_DIR / f"{slug}.json"
    if not filepath.exists():
        return {"error": f"WAD not found: {slug}"}

    entry = json.loads(filepath.read_text())
    print(f"\nProcessing: {entry['title']}", file=sys.stderr)

    # Get authors
    authors = [a.get("name", "") for a in entry.get("authors", [])]

    # Get DoomWiki content
    wiki_url = get_doomwiki_url(entry)
    if not wiki_url:
        return {"error": "No DoomWiki URL"}

    print(f"  Fetching: {wiki_url}", file=sys.stderr)
    content = fetch_and_convert(wiki_url)

    if not content:
        return {"error": "Failed to fetch content"}

    print(f"  Content size: {len(content)} chars", file=sys.stderr)
    print(f"  Authors: {', '.join(authors)}", file=sys.stderr)
    print(f"  Extracting summary...", file=sys.stderr)

    prompt = build_prompt(difficulty_scale, authors)
    result = extract_summary(content, prompt)
    result["_slug"] = slug
    result["_title"] = entry["title"]
    result["_authors"] = authors
    result["_source"] = wiki_url

    return result


def main():
    parser = argparse.ArgumentParser(description="Test WAD summary extraction")
    parser.add_argument("slugs", nargs="+", help="WAD slugs to process")
    parser.add_argument("--output", "-o", help="Output file (default: stdout)")
    args = parser.parse_args()

    difficulty_scale = load_difficulty_scale()

    results = []
    for slug in args.slugs:
        result = process_wad(slug, difficulty_scale)
        results.append(result)

        print(f"\n{'='*60}", file=sys.stderr)
        print(json.dumps(result, indent=2))

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2) + "\n")
        print(f"\nSaved to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
