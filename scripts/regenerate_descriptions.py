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
Regenerate player-facing `description` fields for the WAD catalog.

Two-phase so the git diff is the review surface:
  Phase 1 (default): generate candidates into scripts/data/description_candidates.json
  Phase 2 (--apply): write reviewed candidates into content/wads/*.json,
                     stamping `_descriptionSource: "generated"`.

Sources per entry (the model may ONLY use these — no invented facts):
  - idgames API description for the entry's exact download path
  - DoomWiki article (from the entry's urls)
  - the entry's own metadata (title, authors, year, type)
Entries with no usable source are skipped, not hallucinated.

Entries with `_descriptionSource: "manual"` are never touched.

Usage:
  uv run scripts/regenerate_descriptions.py --slug epic-2       # one entry
  uv run scripts/regenerate_descriptions.py --limit 10          # first N eligible
  uv run scripts/regenerate_descriptions.py                     # all eligible
  uv run scripts/regenerate_descriptions.py --resume            # keep existing candidates
  uv run scripts/regenerate_descriptions.py --apply             # write candidates to catalog
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

import llm

SCRIPTS_DIR = Path(__file__).parent
WADS_DIR = SCRIPTS_DIR.parent / "content" / "wads"
CANDIDATES_FILE = SCRIPTS_DIR / "data" / "description_candidates.json"

IDGAMES_API = "https://www.doomworld.com/idgames/api/api.php"
HEADERS = {"User-Agent": "DoomLauncher-WikiScraper/1.0"}
MAX_WIKI_CHARS = 15000

def fetch_idgames_description(entry: dict) -> str | None:
    """Fetch the idgames text-file description for the entry's download path."""
    downloads = entry.get("downloads", [])
    if not downloads:
        return None
    match = re.search(r"/idgames/(.+)$", downloads[0]["url"])
    if not match:
        return None

    response = requests.get(
        IDGAMES_API,
        params={"action": "get", "file": match.group(1), "out": "json"},
        headers=HEADERS,
        timeout=30,
    )
    response.raise_for_status()
    content = response.json().get("content")
    if not content:
        return None
    description = content.get("description") or ""
    description = re.sub(r"<br\s*/?>", "\n", description)
    return description.strip() or None


def fetch_doomwiki_article(entry: dict) -> str | None:
    """Fetch and markdown-convert the entry's DoomWiki article, if any."""
    wiki_url = next((u for u in entry.get("urls", []) if "doomwiki.org" in u), None)
    if not wiki_url:
        return None

    response = requests.get(wiki_url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    content = soup.find(id="mw-content-text")
    if not content:
        return None
    for selector in [".mw-editsection", "#toc", ".printfooter", "nav", "script", "style"]:
        for tag in content.select(selector):
            tag.decompose()
    return md(str(content), heading_style="ATX")[:MAX_WIKI_CHARS]


def build_prompt(entry: dict, idgames_text: str | None, wiki_text: str | None) -> str:
    authors = ", ".join(a["name"] for a in entry.get("authors", [])) or "unknown"
    sections = []
    if idgames_text:
        sections.append(f"=== idgames archive text-file description ===\n{idgames_text}")
    if wiki_text:
        sections.append(f"=== DoomWiki article ===\n{wiki_text}")
    sources = "\n\n".join(sections)

    return f"""You are writing the catalog description shown to players in a Doom WAD launcher.

WAD metadata:
- Title: {entry["title"]}
- Authors: {authors}
- Year: {entry.get("year")}
- Type: {entry.get("type")}
- IWAD: {entry.get("iwad")}

Source material (use ONLY facts found here — do not invent anything):

{sources}

Write a description following this contract:
- 1-3 sentences, 30-70 words, plain text, complete sentences (never truncated).
- Say what the WAD IS: scale (map count if stated), setting/theme, and what is distinctive about it.
- NO install or launch instructions, NO source-port compatibility notes.
- NO awards (shown separately in the UI), NO release-history trivia.
- Neutral catalog tone: no marketing filler, no first-person author voice.
- Standard ASCII punctuation (straight quotes, hyphens).

Return ONLY JSON: {{"description": "<text>"}}"""


def generate_candidate(path: Path) -> dict | None:
    entry = json.loads(path.read_text())

    idgames_text = fetch_idgames_description(entry)
    wiki_text = fetch_doomwiki_article(entry)
    sources = [name for name, text in [("idgames", idgames_text), ("doomwiki", wiki_text)] if text]
    if not sources:
        print("SKIP (no sources)", file=sys.stderr)
        return None

    result = llm.chat_json(build_prompt(entry, idgames_text, wiki_text))
    description = result["description"].strip()
    if not description:
        raise ValueError("Model returned empty description")

    return {
        "slug": entry["slug"],
        "old": entry["description"],
        "new": description,
        "sources": sources,
    }


def run_generate(args: argparse.Namespace) -> None:
    paths = sorted(WADS_DIR.glob("*.json"))
    if args.slug:
        paths = [p for p in paths if p.stem in args.slug]
        missing = set(args.slug) - {p.stem for p in paths}
        if missing:
            sys.exit(f"Unknown slug(s): {', '.join(sorted(missing))}")

    existing: dict[str, dict] = {}
    if args.resume and CANDIDATES_FILE.exists():
        existing = {c["slug"]: c for c in json.loads(CANDIDATES_FILE.read_text())}
        print(f"Resuming with {len(existing)} existing candidates", file=sys.stderr)

    eligible = []
    for path in paths:
        entry = json.loads(path.read_text())
        if entry.get("_descriptionSource") == "manual":
            continue
        if entry["slug"] in existing:
            continue
        eligible.append(path)

    if args.limit:
        eligible = eligible[: args.limit]

    print(f"Generating {len(eligible)} candidates...", file=sys.stderr)
    candidates = list(existing.values())
    errors = 0

    for i, path in enumerate(eligible, 1):
        print(f"[{i}/{len(eligible)}] {path.stem}... ", end="", file=sys.stderr, flush=True)
        try:
            candidate = generate_candidate(path)
            if candidate:
                candidates.append(candidate)
                print(f"OK ({len(candidate['new'])} chars, {'+'.join(candidate['sources'])})",
                      file=sys.stderr)
        except Exception as e:
            errors += 1
            print(f"ERROR: {e}", file=sys.stderr)

        if i % 10 == 0:
            CANDIDATES_FILE.write_text(json.dumps(candidates, indent=2) + "\n")
        time.sleep(0.3)

    CANDIDATES_FILE.write_text(json.dumps(candidates, indent=2) + "\n")
    print(f"\nDone: {len(candidates)} candidates, {errors} errors -> {CANDIDATES_FILE}",
          file=sys.stderr)


def run_apply() -> None:
    if not CANDIDATES_FILE.exists():
        sys.exit(f"No candidates file at {CANDIDATES_FILE} — run the generate phase first")

    candidates = json.loads(CANDIDATES_FILE.read_text())
    applied = 0
    for candidate in candidates:
        path = WADS_DIR / f"{candidate['slug']}.json"
        entry = json.loads(path.read_text())
        if entry.get("_descriptionSource") == "manual":
            print(f"SKIP {candidate['slug']} (manual)", file=sys.stderr)
            continue
        entry["description"] = candidate["new"]
        entry["_descriptionSource"] = "generated"
        path.write_text(json.dumps(entry, indent=2) + "\n")
        applied += 1

    print(f"Applied {applied}/{len(candidates)} descriptions. Review with: git diff content/wads/",
          file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate WAD catalog descriptions")
    parser.add_argument("--slug", action="append", help="Only this slug (repeatable)")
    parser.add_argument("--limit", type=int, help="Limit to first N eligible entries")
    parser.add_argument("--resume", action="store_true", help="Keep existing candidates")
    parser.add_argument("--apply", action="store_true",
                        help="Write candidates into content/wads/ (phase 2)")
    args = parser.parse_args()

    if args.apply:
        run_apply()
    else:
        run_generate(args)


if __name__ == "__main__":
    main()
