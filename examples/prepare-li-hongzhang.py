#!/usr/bin/env python3
"""prepare-li-hongzhang.py: Source-preparation adapter for 梁啟超《李鴻章傳》第八章.

Fetches the pinned public Wikisource chapter 08 revision via the public MediaWiki
API, extracts faithful original body paragraph nodes (excluding navigation,
headers, styles, and footers), builds a working edition EPUB via Calibre, and
indexes paragraphs via extract.py.

This produces a clean working edition for illustration and reading comparison.
All book files remain in --work-dir outside the repository.
"""

import argparse
import hashlib
import json
import os
import shutil
import ssl
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
import certifi
from lxml import html

WIKISOURCE_PARSE_API = "https://zh.wikisource.org/w/api.php"
PINNED_REVISION_ID = 2489933
EXPECTED_TITLE = "李鴻章傳/08"
PERMANENT_URL = f"https://zh.wikisource.org/w/index.php?title={urllib.parse.quote(EXPECTED_TITLE)}&oldid={PINNED_REVISION_ID}"
USER_AGENT = "EpubIllustratePreparation/1.0 (https://github.com/wavever/book-illustrator-skill)"


def find_calibre(user_path=None):
    if user_path:
        if os.path.isfile(user_path) and os.access(user_path, os.X_OK):
            return user_path
        raise FileNotFoundError(f"Specified Calibre executable not found or not executable: {user_path}")

    candidates = [
        shutil.which("ebook-convert"),
        "/Applications/calibre.app/Contents/MacOS/ebook-convert",
        "/usr/bin/ebook-convert",
        "/usr/local/bin/ebook-convert",
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("Calibre ebook-convert binary not found in standard paths. Specify --calibre /path/to/ebook-convert")


def fetch_chapter(work_dir: Path) -> dict:
    cached_json = work_dir / "chapter08_parse.json"
    if cached_json.exists():
        print(f"Loading cached Wikisource API response: {cached_json}")
        with open(cached_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        params = {
            "action": "parse",
            "oldid": str(PINNED_REVISION_ID),
            "prop": "text|revid|displaytitle",
            "format": "json",
        }
        url = f"{WIKISOURCE_PARSE_API}?{urllib.parse.urlencode(params)}"
        print(f"Fetching Wikisource chapter 08 from {url}...")

        ctx = ssl.create_default_context(cafile=certifi.where())
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            raise RuntimeError(f"Failed to fetch chapter from Wikisource API: {e}")

        with open(cached_json, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # Strictly verify revision and title
    revid = data.get("parse", {}).get("revid")
    title = data.get("parse", {}).get("title")
    if revid != PINNED_REVISION_ID:
        raise ValueError(f"Revision mismatch: expected {PINNED_REVISION_ID}, got {revid}")
    if title != EXPECTED_TITLE:
        raise ValueError(f"Title mismatch: expected '{EXPECTED_TITLE}', got '{title}'")

    return data


def extract_body_nodes(parse_data: dict) -> str:
    raw_html = parse_data.get("parse", {}).get("text", {}).get("*", "")
    if not raw_html:
        raise ValueError("Wikisource API returned empty text content")

    doc = html.fromstring(raw_html)
    parser_out = doc.xpath('//div[contains(@class, "mw-parser-output")]')
    if not parser_out:
        raise ValueError("mw-parser-output container not found in parsed HTML")

    body_elements = []
    for child in parser_out[0].iterchildren():
        if child.tag == "table" and "ws-header" in child.get("class", ""):
            continue
        if child.tag == "style":
            continue
        if child.tag in ("p", "ul", "ol", "h2", "h3"):
            body_elements.append(html.tostring(child, encoding="utf-8").decode("utf-8"))

    return "\n".join(body_elements)


def main():
    parser = argparse.ArgumentParser(description="Prepare working edition EPUB for 李鴻章傳 Chapter 08.")
    parser.add_argument("--work-dir", required=True, help="Working directory for all intermediate and book files")
    parser.add_argument("--calibre", help="Explicit path to ebook-convert")
    args = parser.parse_args()

    work_dir = Path(args.work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    calibre_bin = find_calibre(args.calibre)
    print(f"Using Calibre: {calibre_bin}")

    parse_data = fetch_chapter(work_dir)
    revid = parse_data["parse"]["revid"]
    title = parse_data["parse"]["title"]
    print(f"Verified Wikisource revision: {revid} and title: {title}")

    body_html_str = extract_body_nodes(parse_data)
    html_content = f"""<!DOCTYPE html>
<html lang="zh">
<head>
  <meta charset="utf-8"/>
  <title>李鴻章傳 - 第八章 外交家之李鴻章（上）</title>
</head>
<body>
  <h1>第八章 外交家之李鴻章（上）</h1>
{body_html_str}
</body>
</html>
"""

    html_file = work_dir / "chapter08.html"
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Wrote faithful HTML working edition: {html_file} ({html_file.stat().st_size} bytes)")

    orig_epub = work_dir / "original.epub"
    convert_cmd = [
        calibre_bin,
        str(html_file),
        str(orig_epub),
        "--title", "李鴻章傳",
        "--authors", "梁啟超",
        "--language", "zh",
        "--no-default-epub-cover",
    ]
    print(f"Running Calibre conversion -> {orig_epub}...")
    res = subprocess.run(convert_cmd, capture_output=True, text=True, timeout=120)
    if res.returncode != 0:
        raise RuntimeError(f"Calibre conversion failed (exit {res.returncode}): {res.stderr}")
    print(f"Created original.epub: {orig_epub} ({orig_epub.stat().st_size} bytes)")

    # Run extract.py
    repo_root = Path(__file__).resolve().parent.parent
    extract_script = repo_root / "skills" / "book-illustrator" / "scripts" / "extract.py"
    if extract_script.exists():
        print("Indexing EPUB via extract.py...")
        proc = subprocess.run(
            [sys.executable, str(extract_script), str(orig_epub), str(work_dir)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"extract.py failed: {proc.stderr}")
        print("Indexed successfully: book.json and text/ directory created.")

    print("\nSource Preparation Provenance:")
    print(f"  Source Title: 梁啟超《李鴻章傳》第八章 外交家之李鴻章（上）")
    print(f"  Author: 梁啟超 (Liang Qichao, 1873–1929)")
    print(f"  Permanent URL: {PERMANENT_URL}")
    print(f"  Revision ID: {revid}")
    print(f"  Rights: Public domain (PD-old-80 / author died 1929; published 1901)")
    print(f"  Working Edition: Faithful Wikisource chapter body extract; no text altered.")


if __name__ == "__main__":
    main()
