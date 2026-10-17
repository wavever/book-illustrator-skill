#!/usr/bin/env python3
"""render-comparison.py: Render side-by-side reading excerpt comparisons.

Produces clean, truthful BEFORE / AFTER comparison images from actual original
and script-generated illustrated EPUB outputs. Uses the byte-accurate offset-walking
logic from the book-illustrator engine and asserts that stripped illustrated text
is 100% identical to original text.

Usage:
    python3 examples/render-comparison.py --manifest examples/demo-manifest.json --work-dir <path_to_work_books> --name sanguo
    python3 examples/render-comparison.py --manifest examples/demo-manifest.json --work-dir <path_to_work_books> --name beagle
    python3 examples/render-comparison.py --manifest examples/demo-manifest.json --work-dir <path_to_work_books> --all
"""

import argparse
import base64
import html
import json
import mimetypes
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

from PIL import Image

# Add skills/book-illustrator/scripts to sys.path to access native_util
_SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "book-illustrator", "scripts")
if os.path.isdir(_SCRIPTS_DIR) and _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import static_page  # noqa: E402  (book markup is untrusted: CSP, sanitizing, offline Chrome flags)

try:
    from native_util import decode as native_decode
except ImportError:
    def native_decode(raw: bytes, kind: str = "xml", declared_enc: Optional[str] = None):
        enc = declared_enc or "utf-8"
        return raw.decode(enc), enc

BLOCK_RE = re.compile(r"<!--illus(?:-[0-9a-f]{8})?:start-->.*?<!--illus(?:-[0-9a-f]{8})?:end-->", re.DOTALL)
IMG_SRC_RE = re.compile(r"""(<img\b[^>]*\bsrc\s*=\s*)(["'])(.*?)\2""", re.IGNORECASE)


def find_chrome() -> str:
    """Locate Google Chrome or Chromium executable."""
    env_path = os.environ.get("CHROME")
    if env_path and os.path.isfile(env_path) and os.access(env_path, os.X_OK):
        return env_path

    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        shutil.which("google-chrome"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("chrome"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("Chrome executable not found. Set CHROME=/path/to/chrome")


def extract_fragments(
    orig_epub_path: str,
    illus_epub_path: str,
    book_json_path: str,
    pids: List[str],
) -> Tuple[str, str, Dict[str, bytes]]:
    """Extract identical original and illustrated HTML fragments plus embedded images."""
    if not os.path.exists(orig_epub_path):
        raise FileNotFoundError(f"Original EPUB not found: {orig_epub_path}")
    if not os.path.exists(illus_epub_path):
        raise FileNotFoundError(f"Illustrated EPUB not found: {illus_epub_path}")
    if not os.path.exists(book_json_path):
        raise FileNotFoundError(f"book.json not found: {book_json_path}")

    with open(book_json_path, encoding="utf-8") as f:
        bj = json.load(f)

    paras = [p for p in bj["paras"] if p["pid"] in pids]
    if not paras:
        raise ValueError(f"None of the requested pids found in book.json: {pids}")

    f_rel = paras[0]["file"]
    for p in paras:
        if p["file"] != f_rel:
            raise ValueError("All requested paragraphs in an excerpt must belong to the same HTML file.")

    a = paras[0]["start"]
    b = max(p["end"] if p.get("ins", p["end"]) is None else p.get("ins", p["end"]) for p in paras)

    # Encoding determination from book.json metadata
    declared_enc = bj.get("encodings", {}).get(f_rel) or bj.get("source", {}).get("encoding")

    # 1. Read original HTML file from original EPUB
    orig_raw_bytes = None
    with zipfile.ZipFile(orig_epub_path) as z:
        for name in z.namelist():
            if name.endswith(f_rel):
                orig_raw_bytes = z.read(name)
                break
    if orig_raw_bytes is None:
        raise FileNotFoundError(f"Could not locate member ending with {f_rel} in {orig_epub_path}")

    orig_raw, _ = native_decode(orig_raw_bytes, "xml", declared_enc)
    orig_frag = orig_raw[a:b]

    # 2. Read illustrated HTML file from illustrated EPUB and walk offsets
    illus_raw_bytes = None
    images: Dict[str, bytes] = {}
    with zipfile.ZipFile(illus_epub_path) as z:
        for name in z.namelist():
            if name.endswith(f_rel):
                illus_member_dir = os.path.dirname(name)
                illus_raw_bytes = z.read(name)
                break
        # Pre-cache all images in the illustrated EPUB
        for name in z.namelist():
            ext = os.path.splitext(name)[1].lower()
            if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
                images[name] = z.read(name)
                if illus_member_dir and name.startswith(illus_member_dir + "/"):
                    rel = name[len(illus_member_dir) + 1 :]
                    images[rel] = images[name]
                images[os.path.basename(name)] = images[name]

    if illus_raw_bytes is None:
        raise FileNotFoundError(f"Could not locate member ending with {f_rel} in {illus_epub_path}")

    illus_raw, _ = native_decode(illus_raw_bytes, "xml", declared_enc)

    # Walk the built file using position-walking logic
    i = orig = 0
    piece = []
    for m in list(BLOCK_RE.finditer(illus_raw)) + [None]:
        seg = illus_raw[i : m.start() if m else len(illus_raw)]
        lo, hi = max(orig, a), min(orig + len(seg), b)
        if lo < hi:
            piece.append(seg[lo - orig : hi - orig])
        orig += len(seg)
        if m:
            if a < orig <= b:
                piece.append(m.group(0))
            i = m.end()

    illus_frag = "".join(piece)

    # Rigorous byte/text assertion: stripped illustrated excerpt MUST equal original excerpt
    stripped = BLOCK_RE.sub("", illus_frag)
    if stripped != orig_frag:
        raise AssertionError(
            "Truthfulness verification failed: stripped illustrated excerpt does not strictly match original excerpt!"
        )

    # Convert relative img src to base64 data URIs
    def inline_img(match):
        prefix, quote, src = match.group(1), match.group(2), match.group(3)
        clean_src = src.split("?")[0].split("#")[0]
        data = images.get(clean_src) or images.get(os.path.basename(clean_src))
        if data:
            mime = mimetypes.guess_type(clean_src)[0] or "image/jpeg"
            b64 = base64.b64encode(data).decode("ascii")
            return f'{prefix}"data:{mime};base64,{b64}"'
        return match.group(0)

    # Render copies only: drop the book's active content; the page CSP allows data: images only
    illus_frag = IMG_SRC_RE.sub(inline_img, static_page.sanitize(illus_frag))
    orig_frag = IMG_SRC_RE.sub(inline_img, static_page.sanitize(orig_frag))

    return orig_frag, illus_frag, images


def build_comparison_html(
    title: str,
    subtitle: str,
    meta_tag: str,
    label_left: str,
    label_right: str,
    orig_html: str,
    illus_html: str,
    lang: str = "zh",
    canvas_width: int = 1240,
) -> str:
    """Build a side-by-side comparison page in a warm paper aesthetic."""
    is_zh = lang.startswith("zh")
    font_stack = (
        "'PingFang SC', 'Source Han Serif SC', 'Songti SC', 'Noto Serif CJK SC', serif"
        if is_zh
        else "'Charter', 'Georgia', 'Iowan Old Style', 'Times New Roman', serif"
    )

    clean_orig = orig_html.strip()
    clean_illus = illus_html.strip()

    badge_orig_text = "原文内容" if is_zh else "Original Text"
    badge_illus_text = "插入真实图片" if is_zh else "Real Photo Injected"
    footer_text = (
        "原文内容保持字节级未修改；仅在段落间插入配图块，移除配图后与原书完全一致。"
        if is_zh
        else "Original text content is byte-identical outside injected illustration blocks."
    )

    return f"""<!DOCTYPE html>
<html lang="{html.escape(lang)}">
<head>
{static_page.csp_meta()}
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 32px 24px;
    background: #fbf8f1;
    color: #24211e;
    font-family: {font_stack};
    -webkit-font-smoothing: antialiased;
  }}
  .container {{
    max-width: {canvas_width}px;
    margin: 0 auto;
    background: #fbf8f1;
  }}
  .header {{
    text-align: center;
    margin-bottom: 24px;
    padding-bottom: 16px;
    border-bottom: 1px solid #e7ded0;
  }}
  .header .meta-tag {{
    display: inline-block;
    font-size: 11px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #8c8273;
    background: #f2ebe0;
    padding: 3px 10px;
    border-radius: 999px;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .header h1 {{
    margin: 4px 0 2px 0;
    font-size: 22px;
    font-weight: 700;
    color: #1e1b18;
    letter-spacing: -0.01em;
  }}
  .header .subtitle {{
    margin: 0;
    font-size: 14px;
    color: #6d6457;
  }}
  .columns {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 28px;
  }}
  .column {{
    background: #ffffff;
    border: 1px solid #e4dcd0;
    border-radius: 8px;
    box-shadow: 0 1px 3px rgba(60, 50, 40, 0.04);
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }}
  .column-head {{
    background: #f5efe4;
    padding: 10px 18px;
    border-bottom: 1px solid #e7ded0;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .column-label {{
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.04em;
    color: #4a4237;
  }}
  .column-badge {{
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 4px;
    font-weight: 600;
  }}
  .badge-orig {{
    background: #e9e2d5;
    color: #5c5346;
  }}
  .badge-illus {{
    background: #2f6f4e;
    color: #ffffff;
  }}
  .column-body {{
    padding: 28px 24px;
    font-size: 16px;
    line-height: 1.8;
    color: #262320;
    flex: 1;
  }}
  .column-body p {{
    margin: 0 0 1em 0;
    text-align: justify;
    text-indent: {"2em" if is_zh else "0"};
  }}
  .column-body p:last-child {{
    margin-bottom: 0;
  }}
  .column-body h1, .column-body h2, .column-body h3, .column-body h4 {{
    text-align: center;
    text-indent: 0;
    margin: 0 0 0.8em 0;
    color: #1e1b18;
    line-height: 1.35;
  }}
  .column-body img {{
    height: auto;
  }}
  .illus-figure {{
    margin: 1.2em 0 1.4em 0 !important;
    text-align: center !important;
    text-indent: 0 !important;
  }}
  .footer-note {{
    margin-top: 18px;
    text-align: center;
    font-size: 12px;
    color: #8c8273;
    line-height: 1.5;
  }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="meta-tag">{html.escape(meta_tag)}</div>
    <h1>{html.escape(title)}</h1>
    <div class="subtitle">{html.escape(subtitle)}</div>
  </div>
  <div class="columns">
    <div class="column">
      <div class="column-head">
        <span class="column-label">{html.escape(label_left)}</span>
        <span class="column-badge badge-orig">{html.escape(badge_orig_text)}</span>
      </div>
      <div class="column-body">
        {clean_orig}
      </div>
    </div>
    <div class="column">
      <div class="column-head">
        <span class="column-label">{html.escape(label_right)}</span>
        <span class="column-badge badge-illus">{html.escape(badge_illus_text)}</span>
      </div>
      <div class="column-body">
        {clean_illus}
      </div>
    </div>
  </div>
  <div class="footer-note">
    {html.escape(footer_text)}
  </div>
</div>
</body>
</html>
"""


def render_to_image(
    page_html: str,
    output_path: str,
    chrome_path: str,
    canvas_width: int = 1240,
    quality: int = 90,
) -> None:
    """Render HTML to WebP using headless Chrome and Pillow post-processing."""
    tmp_dir = tempfile.mkdtemp(prefix="book-illustrator-render-")
    try:
        html_file = os.path.join(tmp_dir, "page.html")
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(page_html)

        shot_png = os.path.join(tmp_dir, "shot.png")
        cmd = [
            chrome_path,
            "--headless",
            "--disable-gpu",
            "--hide-scrollbars",
            *static_page.CHROME_FLAGS,
            f"--user-data-dir={os.path.join(tmp_dir, 'profile')}",
            f"--window-size={canvas_width},12000",
            "--virtual-time-budget=6000",
            f"--screenshot={shot_png}",
            pathlib.Path(html_file).as_uri(),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if not os.path.exists(shot_png):
            raise RuntimeError(f"Chrome screenshot failed (exit {res.returncode}): {res.stderr}")

        # Crop bottom background padding like preview.py
        im = Image.open(shot_png).convert("RGB")
        bg = im.getpixel((5, im.height - 5))
        h = im.height
        while h > 100 and all(im.getpixel((x, h - 1)) == bg for x in range(0, im.width, 10)):
            h -= 1

        # Add 24px bottom breathing room
        final_h = min(im.height, h + 24)
        cropped = im.crop((0, 0, im.width, final_h))

        os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
        ext = os.path.splitext(output_path)[1].lower()
        if ext == ".webp":
            cropped.save(output_path, "WEBP", quality=quality, method=6)
        elif ext == ".png":
            cropped.save(output_path, "PNG", optimize=True)
        else:
            cropped.save(output_path)

        out_size = os.path.getsize(output_path)
        print(f"Rendered: {output_path} ({cropped.width}x{cropped.height}px, {out_size / 1024:.1f} KB)")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def resolve_manifest_path(path_val: str, work_dir: Optional[str]) -> str:
    """Resolve a path from manifest relative to work_dir if relative."""
    if os.path.isabs(path_val):
        return path_val
    if work_dir:
        return os.path.normpath(os.path.join(work_dir, path_val))
    return os.path.abspath(path_val)


def main():
    parser = argparse.ArgumentParser(description="Render comparison demo images.")
    parser.add_argument("--manifest", help="Path to demo manifest JSON file")
    parser.add_argument("--work-dir", help="Working directory containing scratch books/demos")
    parser.add_argument("--name", help="Specific demo name from manifest to render")
    parser.add_argument("--all", action="store_true", help="Render all demos in manifest")
    parser.add_argument("--orig", help="Path to original EPUB")
    parser.add_argument("--illus", help="Path to illustrated EPUB")
    parser.add_argument("--book-json", help="Path to book.json")
    parser.add_argument("--pids", help="Comma-separated paragraph IDs")
    parser.add_argument("--title", help="Demo title")
    parser.add_argument("--subtitle", help="Demo subtitle")
    parser.add_argument("--meta-tag", default="Reading Excerpt Comparison", help="Top badge text")
    parser.add_argument("--label-orig", default="Original", help="Left column label")
    parser.add_argument("--label-illus", default="Illustrated", help="Right column label")
    parser.add_argument("--lang", default="en", help="Language code (zh or en)")
    parser.add_argument("--out", help="Output image file path (.webp or .png)")
    parser.add_argument("--width", type=int, default=1240, help="Canvas width in px")
    parser.add_argument("--quality", type=int, default=90, help="WebP quality (default 90)")

    args = parser.parse_args()
    chrome = find_chrome()

    if args.manifest:
        with open(args.manifest, encoding="utf-8") as f:
            manifest_data = json.load(f)

        demos = manifest_data.get("demos", {})
        if args.name:
            if args.name not in demos:
                sys.exit(f"Demo '{args.name}' not found in {args.manifest}. Available: {list(demos.keys())}")
            selected = {args.name: demos[args.name]}
        elif args.all:
            selected = demos
        else:
            sys.exit("Please specify --name <demo_key> or --all when using --manifest.")

        work_dir = args.work_dir or os.environ.get("BOOK_DEMOS_DIR")
        for k, d in selected.items():
            print(f"--- Rendering demo: {k} ---")
            rp = d.get("relative_paths", {})
            orig_epub = resolve_manifest_path(rp.get("orig_epub", d.get("orig_epub", "")), work_dir)
            illus_epub = resolve_manifest_path(rp.get("illus_epub", d.get("illus_epub", "")), work_dir)
            book_json = resolve_manifest_path(rp.get("book_json", d.get("book_json", "")), work_dir)

            orig_frag, illus_frag, _ = extract_fragments(
                orig_epub_path=orig_epub,
                illus_epub_path=illus_epub,
                book_json_path=book_json,
                pids=d["pids"],
            )
            page_html = build_comparison_html(
                title=d["title"],
                subtitle=d["subtitle"],
                meta_tag=d.get("meta_tag", "Reading Excerpt Comparison"),
                label_left=d.get("label_orig", "Original"),
                label_right=d.get("label_illus", "Illustrated"),
                orig_html=orig_frag,
                illus_html=illus_frag,
                lang=d.get("lang", "zh"),
                canvas_width=d.get("width", 1240),
            )
            out_file = args.out or d["output"]
            render_to_image(
                page_html=page_html,
                output_path=out_file,
                chrome_path=chrome,
                canvas_width=d.get("width", 1240),
                quality=d.get("quality", 90),
            )
        return

    # Direct CLI usage
    if not (args.orig and args.illus and args.book_json and args.pids and args.out):
        sys.exit("Error: Must provide either --manifest or all of --orig, --illus, --book-json, --pids, and --out.")

    pids_list = [p.strip() for p in args.pids.split(",") if p.strip()]
    orig_frag, illus_frag, _ = extract_fragments(
        orig_epub_path=args.orig,
        illus_epub_path=args.illus,
        book_json_path=args.book_json,
        pids=pids_list,
    )
    page_html = build_comparison_html(
        title=args.title or "Reading Excerpt",
        subtitle=args.subtitle or "",
        meta_tag=args.meta_tag,
        label_left=args.label_orig,
        label_right=args.label_illus,
        orig_html=orig_frag,
        illus_html=illus_frag,
        lang=args.lang,
        canvas_width=args.width,
    )
    render_to_image(
        page_html=page_html,
        output_path=args.out,
        chrome_path=chrome,
        canvas_width=args.width,
        quality=args.quality,
    )


if __name__ == "__main__":
    main()
