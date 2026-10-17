#!/usr/bin/env python3
"""render-comparison.py: Render side-by-side reading excerpt comparisons.

Produces clean, truthful BEFORE / AFTER comparison images from actual original
and script-generated illustrated EPUB outputs. Uses the byte-accurate offset-walking
logic from the book-illustrator engine and asserts that stripped illustrated text
is 100% identical to original text.

A demo selects either one passage (`"pids": [...]`) or several labelled passages
(`"sections": [{"label": "...", "pids": [...]}, ...]`). Each section is a run of
consecutive paragraphs in one XHTML member; sections may come from different
chapters or members. Every section is checked on its own, rendered as one aligned
row (original | illustrated), and a gap between non-adjacent sections is marked
as omitted text. Optional `"layout"` values style only the rendering copy.

Usage:
    python3 examples/render-comparison.py --manifest examples/demo-manifest.json --work-dir <scratch> --name <demo>
    python3 examples/render-comparison.py --manifest examples/demos/carnegie.json --work-dir <scratch>
    python3 examples/render-comparison.py --manifest examples/demo-manifest.json --work-dir <scratch> --all
"""

import argparse
import base64
import html
import json
import mimetypes
import os
import pathlib
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote

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
IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif")

# Optional per-demo "layout" keys -> (default for zh, default for en). Rendering copy only.
LAYOUT_DEFAULTS = {
    "width": (1240, 1240),            # rendered image width, px
    "font_size": (16, 16),            # body text, px
    "line_height": (1.8, 1.65),
    "paragraph_indent": ("2em", "0"),
    "paragraph_spacing": ("0.9em", "0.9em"),
    "image_max_height": (None, None),  # px; None keeps the figure block's own inline size
    "caption_size": (None, None),     # px; None keeps the figure block's own inline size
    "column_gap": (28, 28),           # px
    "font_family": (None, None),      # CSS font stack; None = language default
}
_CSS_SAFE = re.compile(r"^[\w\s,.'\"#%()-]*$", re.UNICODE)


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


def member_for(bj: dict, f_rel: str, names: List[str]) -> str:
    """Exact zip member of a book.json paragraph file (paths in book.json are relative to the OPF)."""
    if bj.get("opf"):
        member = posixpath.normpath(posixpath.join(posixpath.dirname(bj["opf"]), f_rel))
        if member in names:
            return member
        raise FileNotFoundError(f"Member {member} not found in EPUB")
    hits = [n for n in names if n == f_rel or n.endswith("/" + f_rel)]
    if len(hits) != 1:
        raise FileNotFoundError(f"Expected exactly one member for {f_rel}, found {hits}")
    return hits[0]


def resolve_image(member: str, src: str, names: List[str]) -> Optional[str]:
    """Zip member an <img src> in `member` points to; relative references only, no basename guessing."""
    s = html.unescape(src).split("#")[0].split("?")[0]
    if not s or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", s) or s.startswith("/") or "\\" in s:
        return None
    target = posixpath.normpath(posixpath.join(posixpath.dirname(member), unquote(s)))
    if target.startswith("../") or target not in names or not target.lower().endswith(IMAGE_EXTS):
        return None
    return target


def inline_images(fragment: str, member: str, z: zipfile.ZipFile) -> str:
    """Sanitized fragment with each resolvable <img src> as a data: URL; others blanked (CSP blocks them anyway)."""
    names = z.namelist()

    def repl(m):
        target = resolve_image(member, m.group(3), names)
        if target is None:
            return f'{m.group(1)}"{static_page.BLANK}"'
        mime = mimetypes.guess_type(target)[0] or "image/jpeg"
        return f'{m.group(1)}"data:{mime};base64,{base64.b64encode(z.read(target)).decode("ascii")}"'

    return IMG_SRC_RE.sub(repl, static_page.sanitize(fragment))


def walk_range(illus_raw: str, a: int, b: int) -> str:
    """Illustrated text covering original offsets [a, b) plus blocks inserted in (a, b]."""
    i = orig = 0
    piece = []
    for m in list(BLOCK_RE.finditer(illus_raw)) + [None]:
        seg = illus_raw[i: m.start() if m else len(illus_raw)]
        lo, hi = max(orig, a), min(orig + len(seg), b)
        if lo < hi:
            piece.append(seg[lo - orig: hi - orig])
        orig += len(seg)
        if m:
            if a < orig <= b:
                piece.append(m.group(0))
            i = m.end()
    return "".join(piece)


def normalize_sections(demo: dict) -> List[dict]:
    """Sections of a manifest demo; a legacy top-level "pids" list is one unlabelled section."""
    if demo.get("sections"):
        out = []
        for s in demo["sections"]:
            if not s.get("pids"):
                raise ValueError(f"Section without pids: {s}")
            out.append({"label": s.get("label"), "pids": list(s["pids"])})
        return out
    if demo.get("pids"):
        return [{"label": None, "pids": list(demo["pids"])}]
    raise ValueError("Demo needs 'pids' or 'sections'")


def extract_sections(
    orig_epub_path: str,
    illus_epub_path: str,
    book_json_path: str,
    sections: List[dict],
) -> List[dict]:
    """Per section: original and illustrated HTML (sanitized, images inlined), each independently verified.

    A section must be consecutive paragraphs (book order) of one member, so nothing omitted inside a
    section can pass as continuous text. `adjacent` tells whether it directly follows the previous one.
    """
    for p, what in ((orig_epub_path, "Original EPUB"), (illus_epub_path, "Illustrated EPUB"), (book_json_path, "book.json")):
        if not os.path.exists(p):
            raise FileNotFoundError(f"{what} not found: {p}")
    with open(book_json_path, encoding="utf-8") as f:
        bj = json.load(f)
    index = {p["pid"]: n for n, p in enumerate(bj["paras"])}

    out = []
    prev = None  # (member, end offset, original text) of the previous section
    with zipfile.ZipFile(orig_epub_path) as zo, zipfile.ZipFile(illus_epub_path) as zi:
        for sec in sections:
            missing = [pid for pid in sec["pids"] if pid not in index]
            if missing:
                raise ValueError(f"pids not in book.json: {missing}")
            idx = [index[pid] for pid in sec["pids"]]
            if idx != list(range(idx[0], idx[0] + len(idx))):
                raise ValueError(f"Section pids must be consecutive paragraphs in book order: {sec['pids']}; "
                                 "split non-adjacent passages into separate sections")
            paras = [bj["paras"][n] for n in idx]
            f_rel = paras[0]["file"]
            if any(p["file"] != f_rel for p in paras):
                raise ValueError(f"Section spans several files: {sec['pids']}; use one section per file")

            a = paras[0]["start"]
            last = paras[-1]
            b = last["end"] if last.get("ins") is None else last["ins"]
            declared_enc = bj.get("encodings", {}).get(f_rel) or bj.get("source", {}).get("encoding")

            o_member = member_for(bj, f_rel, zo.namelist())
            i_member = member_for(bj, f_rel, zi.namelist())
            orig_raw, _ = native_decode(zo.read(o_member), "xml", declared_enc)
            illus_raw, _ = native_decode(zi.read(i_member), "xml", declared_enc)
            orig_frag = orig_raw[a:b]
            illus_frag = walk_range(illus_raw, a, b)

            if BLOCK_RE.search(orig_frag) or BLOCK_RE.sub("", illus_frag) != orig_frag:
                raise AssertionError(
                    f"Truthfulness verification failed for section {sec.get('label') or sec['pids']}: "
                    "stripped illustrated excerpt does not strictly match original excerpt!"
                )

            out.append({
                "label": sec.get("label"),
                "pids": list(sec["pids"]),
                "member": i_member,
                # adjacent only when nothing but whitespace separates it from the previous section
                "adjacent": bool(prev) and prev[0] == o_member and prev[1] <= a and not prev[2][prev[1]:a].strip(),
                "figures": len(BLOCK_RE.findall(illus_frag)),
                # Render copies only: drop the book's active content; the page CSP allows data: images only
                "orig_html": inline_images(orig_frag, o_member, zo),
                "illus_html": inline_images(illus_frag, i_member, zi),
            })
            prev = (o_member, b, orig_raw)
    return out


def extract_fragments(
    orig_epub_path: str,
    illus_epub_path: str,
    book_json_path: str,
    pids: List[str],
) -> Tuple[str, str, Dict[str, bytes]]:
    """Single-passage form (legacy API): original and illustrated HTML of one run of paragraphs."""
    sec = extract_sections(orig_epub_path, illus_epub_path, book_json_path, [{"label": None, "pids": pids}])[0]
    return sec["orig_html"], sec["illus_html"], {}


def resolve_layout(lang: str, layout: Optional[dict] = None, width: Optional[int] = None) -> dict:
    """Layout values with language defaults; rejects values that could break out of the CSS."""
    zh = lang.startswith("zh")
    out = {k: v[0 if zh else 1] for k, v in LAYOUT_DEFAULTS.items()}
    if width:
        out["width"] = width
    for k, v in (layout or {}).items():
        if k not in LAYOUT_DEFAULTS:
            raise ValueError(f"Unknown layout key: {k}")
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out[k] = v
        elif isinstance(v, str) and _CSS_SAFE.match(v):
            out[k] = v
        elif v is not None:
            raise ValueError(f"Unsafe layout value for {k}: {v!r}")
    return out


def build_comparison_html(
    title: str,
    subtitle: str,
    meta_tag: str,
    label_left: str,
    label_right: str,
    orig_html: Optional[str] = None,
    illus_html: Optional[str] = None,
    lang: str = "zh",
    canvas_width: int = 1240,
    sections: Optional[List[dict]] = None,
    layout: Optional[dict] = None,
) -> str:
    """Side-by-side comparison page in a warm paper aesthetic; one aligned row per section."""
    is_zh = lang.startswith("zh")
    L = resolve_layout(lang, layout, canvas_width if layout is None or "width" not in layout else None)
    font_stack = L["font_family"] or (
        "'PingFang SC', 'Source Han Serif SC', 'Songti SC', 'Noto Serif CJK SC', serif"
        if is_zh
        else "'Charter', 'Georgia', 'Iowan Old Style', 'Times New Roman', serif"
    )
    if sections is None:
        sections = [{"label": None, "orig_html": orig_html or "", "illus_html": illus_html or "", "adjacent": False}]

    badge_orig_text = "原文内容" if is_zh else "Original Text"
    badge_illus_text = "插入真实图片" if is_zh else "Real Photos Inserted"
    omitted_text = "（中间段落省略）" if is_zh else "· · ·  intervening text omitted  · · ·"
    if len(sections) > 1:
        footer_text = (
            "每段摘录分别校验：移除插入的配图块后，与原书对应段落逐字节一致；摘录之间的原文已省略。"
            if is_zh
            else "Each excerpt is checked separately: with the inserted figure blocks removed, it is byte-identical "
                 "to the original passage. Text between excerpts is omitted."
        )
    else:
        footer_text = (
            "原文内容保持字节级未修改；仅在段落间插入配图块，移除配图后与原书完全一致。"
            if is_zh
            else "Original text content is byte-identical outside injected illustration blocks."
        )

    img_rule = ""
    if L["image_max_height"]:
        img_rule += (f".cell .illus-figure img {{ max-height: {L['image_max_height']}px !important; "
                     "width: auto !important; max-width: 100% !important; }}\n")
    if L["caption_size"]:
        img_rule += f".cell .illus-figure p {{ font-size: {L['caption_size']}px !important; }}\n"

    rows = []
    for n, sec in enumerate(sections):
        if n and not sec.get("adjacent"):
            rows.append(f'<div class="gap">{html.escape(omitted_text)}</div>')
        if sec.get("label"):
            rows.append(f'<div class="sec-label">{html.escape(sec["label"])}</div>')
        rows.append(f'<div class="cell">{sec["orig_html"].strip()}</div>')
        rows.append(f'<div class="cell cell-illus">{sec["illus_html"].strip()}</div>')
    body_rows = "\n    ".join(rows)

    return f"""<!DOCTYPE html>
<html lang="{html.escape(lang)}">
<head>
{static_page.csp_meta()}
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    padding: 32px 28px;
    background: #fbf8f1;
    color: #24211e;
    font-family: {font_stack};
    -webkit-font-smoothing: antialiased;
  }}
  .container {{ max-width: {L['width'] - 56}px; margin: 0 auto; }}
  .header {{
    text-align: center;
    margin-bottom: 22px;
    padding-bottom: 14px;
    border-bottom: 1px solid #e7ded0;
  }}
  .header .meta-tag {{
    display: inline-block;
    font-size: 12px;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: #8c8273;
    background: #f2ebe0;
    padding: 3px 10px;
    border-radius: 999px;
    margin-bottom: 8px;
    font-weight: 600;
  }}
  .header h1 {{ margin: 4px 0 2px 0; font-size: 26px; font-weight: 700; color: #1e1b18; }}
  .header .subtitle {{ margin: 0; font-size: 15px; color: #6d6457; }}
  .grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    column-gap: {L['column_gap']}px;
    background: #ffffff;
    border: 1px solid #e4dcd0;
    border-radius: 8px;
    overflow: hidden;
  }}
  .column-head {{
    background: #f5efe4;
    padding: 10px 22px;
    border-bottom: 1px solid #e7ded0;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  .column-label {{ font-size: 14px; font-weight: 700; letter-spacing: 0.04em; color: #4a4237; }}
  .column-badge {{ font-size: 12px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }}
  .badge-orig {{ background: #e9e2d5; color: #5c5346; }}
  .badge-illus {{ background: #2f6f4e; color: #ffffff; }}
  .sec-label {{
    grid-column: 1 / -1;
    padding: 14px 22px 0 22px;
    font-size: 13px;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: #8c8273;
  }}
  .gap {{
    grid-column: 1 / -1;
    margin: 0 22px;
    padding: 8px 0 0 0;
    border-top: 1px dashed #ddd3c3;
    text-align: center;
    font-size: 12px;
    color: #a39885;
  }}
  .cell {{
    padding: 10px 22px 18px 22px;
    font-size: {L['font_size']}px;
    line-height: {L['line_height']};
    color: #262320;
  }}
  .cell-illus {{ border-left: 1px solid #f0e9dd; }}
  .cell p {{ margin: 0 0 {L['paragraph_spacing']} 0; text-align: justify; text-indent: {L['paragraph_indent']}; }}
  .cell p:last-child {{ margin-bottom: 0; }}
  .cell h1, .cell h2, .cell h3, .cell h4 {{
    text-align: center; text-indent: 0; margin: 0 0 0.8em 0; color: #1e1b18; line-height: 1.35;
  }}
  .cell img {{ height: auto; }}
  .cell a {{ color: inherit; text-decoration: none; }}
  .illus-figure {{ margin: 1em 0 1.1em 0 !important; text-align: center !important; text-indent: 0 !important; }}
  .illus-figure p {{ text-align: center !important; text-indent: 0 !important; }}
  {img_rule}
  .footer-note {{ margin-top: 16px; text-align: center; font-size: 13px; color: #8c8273; line-height: 1.5; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <div class="meta-tag">{html.escape(meta_tag)}</div>
    <h1>{html.escape(title)}</h1>
    <div class="subtitle">{html.escape(subtitle)}</div>
  </div>
  <div class="grid">
    <div class="column-head">
      <span class="column-label">{html.escape(label_left)}</span>
      <span class="column-badge badge-orig">{html.escape(badge_orig_text)}</span>
    </div>
    <div class="column-head">
      <span class="column-label">{html.escape(label_right)}</span>
      <span class="column-badge badge-illus">{html.escape(badge_illus_text)}</span>
    </div>
    {body_rows}
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
) -> Tuple[int, int]:
    """Render HTML to WebP using headless Chrome and Pillow post-processing; returns (width, height)."""
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
        return cropped.width, cropped.height
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def resolve_manifest_path(path_val: str, work_dir: Optional[str]) -> str:
    """Resolve a path from manifest relative to work_dir if relative."""
    if os.path.isabs(path_val):
        return path_val
    if work_dir:
        return os.path.normpath(os.path.join(work_dir, path_val))
    return os.path.abspath(path_val)


def load_demos(manifest_path: str) -> Dict[str, dict]:
    """Demos of a manifest ({"demos": {...}}) or of a single per-book demo file."""
    with open(manifest_path, encoding="utf-8") as f:
        data = json.load(f)
    if "demos" in data:
        return data["demos"]
    key = data.get("book_id") or os.path.splitext(os.path.basename(manifest_path))[0]
    return {key: data}


def render_demo(d: dict, work_dir: Optional[str], chrome: str, out: Optional[str] = None) -> dict:
    rp = d.get("relative_paths", {})
    secs = extract_sections(
        orig_epub_path=resolve_manifest_path(rp.get("orig_epub", d.get("orig_epub", "")), work_dir),
        illus_epub_path=resolve_manifest_path(rp.get("illus_epub", d.get("illus_epub", "")), work_dir),
        book_json_path=resolve_manifest_path(rp.get("book_json", d.get("book_json", "")), work_dir),
        sections=normalize_sections(d),
    )
    lang = d.get("lang", "zh")
    layout = resolve_layout(lang, d.get("layout"), d.get("width"))
    page_html = build_comparison_html(
        title=d["title"],
        subtitle=d["subtitle"],
        meta_tag=d.get("meta_tag", "Reading Excerpt Comparison"),
        label_left=d.get("label_orig", "Original"),
        label_right=d.get("label_illus", "Illustrated"),
        lang=lang,
        sections=secs,
        layout=layout,
    )
    size = render_to_image(page_html, out or d["output"], chrome, canvas_width=layout["width"],
                           quality=d.get("quality", 90))
    for s in secs:
        print(f"  section {s['label'] or '-'}: {','.join(s['pids'])} in {s['member']}, "
              f"{s['figures']} block(s), verified")
    return {"size": size, "figures": sum(s["figures"] for s in secs), "sections": len(secs)}


def main():
    parser = argparse.ArgumentParser(description="Render comparison demo images.")
    parser.add_argument("--manifest", help="Demo manifest JSON ({\"demos\": {...}}) or a single per-book demo JSON")
    parser.add_argument("--work-dir", help="Working directory containing scratch books/demos")
    parser.add_argument("--name", help="Specific demo name from manifest to render")
    parser.add_argument("--all", action="store_true", help="Render all demos in manifest")
    parser.add_argument("--orig", help="Path to original EPUB")
    parser.add_argument("--illus", help="Path to illustrated EPUB")
    parser.add_argument("--book-json", help="Path to book.json")
    parser.add_argument("--pids", help="Comma-separated paragraph IDs (one passage)")
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
        demos = load_demos(args.manifest)
        if args.name:
            if args.name not in demos:
                sys.exit(f"Demo '{args.name}' not found in {args.manifest}. Available: {list(demos.keys())}")
            selected = {args.name: demos[args.name]}
        elif args.all or len(demos) == 1:
            selected = demos
        else:
            sys.exit("Please specify --name <demo_key> or --all when using --manifest.")

        work_dir = args.work_dir or os.environ.get("BOOK_DEMOS_DIR")
        for k, d in selected.items():
            print(f"--- Rendering demo: {k} ---")
            render_demo(d, work_dir, chrome, args.out)
        return

    # Direct CLI usage
    if not (args.orig and args.illus and args.book_json and args.pids and args.out):
        sys.exit("Error: Must provide either --manifest or all of --orig, --illus, --book-json, --pids, and --out.")

    pids_list = [p.strip() for p in args.pids.split(",") if p.strip()]
    render_demo({"orig_epub": args.orig, "illus_epub": args.illus, "book_json": args.book_json, "pids": pids_list,
                 "title": args.title or "Reading Excerpt", "subtitle": args.subtitle or "", "meta_tag": args.meta_tag,
                 "label_orig": args.label_orig, "label_illus": args.label_illus, "lang": args.lang,
                 "width": args.width, "quality": args.quality}, None, chrome, args.out)


if __name__ == "__main__":
    main()
