"""Render a stretch of the illustrated book to PNG pages for a quick visual check.

Usage: python3 preview.py WORKDIR FIRST_TOC LAST_TOC [width_px]
Takes the built content (WORKDIR/build, written by inject.py) from the first paragraph of
FIRST_TOC to the end of LAST_TOC (plus the chapter gallery right after it), wraps it in a
reader-like page together with the book's own stylesheets and screenshots it with headless Chrome
into WORKDIR/preview/page_NN.png. Works for EPUB, HTML and Markdown jobs; Markdown needs the
optional markdown-it-py package (pip install markdown-it-py).

The book's markup is treated as untrusted and rendered as a static, offline copy (static_page.py):
no script, frames or forms, no network, and only images/stylesheets/fonts from inside WORKDIR/build,
embedded as data: URLs. The built book itself is not changed.
"""
import glob
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import static_page  # noqa: E402
import webfetch  # noqa: E402
from native_util import OUT_EXTS, block_re, book_format, decode, member_of, safe_join  # noqa: E402

LINK_CSS = re.compile(r"""<link\b[^>]*rel\s*=\s*["'][^"']*stylesheet[^>]*>""", re.I)
STYLE = re.compile(r"<style\b[^>]*>.*?</style\s*>", re.S | re.I)
HEAD = re.compile(r"<head\b.*?</head\s*>", re.S | re.I)


def fail(msg):
    sys.exit(f"preview.py: {msg}")


def main(argv):
    if len(argv) < 3:
        sys.exit(__doc__)
    work, first, last = argv[0], int(argv[1]), int(argv[2])
    width = int(argv[3]) if len(argv) > 3 else 760
    book = json.load(open(os.path.join(work, "book.json"), encoding="utf-8"))
    fmt = book_format(book)
    build = os.path.join(work, "build")
    if not os.path.isdir(build):
        fail("WORKDIR/build is missing — run inject.py first")
    paras = [p for p in book["paras"] if first <= p["toc"] <= last]
    if not paras:
        fail(f"no paragraphs in TOC range {first}..{last}")
    files = sorted({p["file"] for p in paras}, key=book["spine"].index)
    BLOCK = block_re(book.get("marker", "illus"), markdown=fmt == "markdown")
    if fmt == "markdown":
        try:
            from markdown_it import MarkdownIt
        except ImportError:
            fail("previewing a Markdown job needs markdown-it-py (pip install markdown-it-py); "
                 "or open the output .md in any Markdown viewer")

    def built_path(f):
        if fmt == "epub":
            return safe_join(build, member_of(book, f))
        docs = [p for p in glob.glob(os.path.join(build, "*")) if os.path.isfile(p)
                and os.path.splitext(p)[1].lower() in OUT_EXTS[fmt]]
        if not docs:
            fail("no built document in WORKDIR/build — run inject.py first")
        return max(docs, key=os.path.getmtime)

    body, head = [], []
    roots, embedded = [os.path.realpath(build)], {}  # only files inside build/ are ever embedded
    for f in files:
        path = built_path(f)
        raw = open(path, "rb").read()
        enc = (book.get("encodings", {}).get(f) if fmt == "epub" else book["source"].get("encoding"))
        s, _ = decode(raw, "text" if fmt == "markdown" else "xml", enc)
        ps = [p for p in paras if p["file"] == f]
        a = ps[0]["start"]
        b = max(p["end"] if p.get("ins", p["end"]) is None else p.get("ins", p["end"]) for p in ps)
        # Walk the built file, keeping original-text positions; inserted blocks occupy zero original width.
        i = orig = 0
        piece = []
        for m in list(BLOCK.finditer(s)) + [None]:
            seg = s[i:m.start() if m else len(s)]
            lo, hi = max(orig, a), min(orig + len(seg), b)
            if lo < hi:
                piece.append(seg[lo - orig:hi - orig])
            orig += len(seg)
            if m:
                if a < orig <= b or (orig == b and "illus-gallery" in m.group(0)):
                    piece.append(m.group(0))
                i = m.end()
        frag = "".join(piece)
        base_dir = os.path.dirname(path)
        if fmt == "markdown":
            frag = MarkdownIt("commonmark", {"html": True}).enable("table").render(frag)
        else:
            h = HEAD.search(s)
            if h:  # the book's own stylesheets (inlined, confined), after the reader defaults so they win
                head += static_page.inline_css(LINK_CSS.findall(h.group(0)) + STYLE.findall(h.group(0)),
                                               base_dir, roots, embedded)
        # the book's markup is untrusted: render a static, offline copy only (see static_page.py)
        body.append(static_page.absolutize(static_page.sanitize(frag), base_dir, roots, embedded))

    page = f"""<html><head>{static_page.csp_meta()}<meta charset="utf-8">
<style>body{{margin:0;background:#fbf8f1;}} .page{{max-width:{width - 60}px;margin:0 auto;padding:30px;
font:18px/1.75 'PingFang SC','Songti SC',serif;color:#222;}} p{{text-indent:2em;margin:0 0 .6em 0;}}
img{{height:auto;max-width:100%;}}</style>{''.join(dict.fromkeys(head))}</head>
<body><div class="page">{''.join(body)}</div></body></html>"""
    out_dir = os.path.join(work, "preview")
    os.makedirs(out_dir, exist_ok=True)
    for f in glob.glob(os.path.join(out_dir, "page_*.png")):
        os.remove(f)
    tmp = tempfile.mkdtemp(prefix="epubillus-preview-")
    try:
        page_html = os.path.join(tmp, "preview.html")
        open(page_html, "w", encoding="utf-8").write(page)
        try:
            chrome = webfetch.chrome_path()
        except RuntimeError as ex:
            fail(str(ex))
        shot = os.path.join(out_dir, "full.png")
        if os.path.exists(shot):
            os.remove(shot)
        r = subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars", *static_page.CHROME_FLAGS,
                            f"--user-data-dir={os.path.join(tmp, 'profile')}",
                            f"--window-size={width},16000", "--virtual-time-budget=8000",
                            f"--screenshot={shot}", pathlib.Path(page_html).as_uri()],
                           capture_output=True, text=True, timeout=180)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not os.path.exists(shot):
        fail(f"Chrome produced no screenshot (exit {r.returncode}): {(r.stderr or '').strip()[-300:]}")
    im = Image.open(shot).convert("RGB")
    bg = im.getpixel((5, im.height - 5))
    h = im.height
    while h > 100 and all(im.getpixel((x, h - 1)) == bg for x in range(0, im.width, 7)):
        h -= 1
    im = im.crop((0, 0, im.width, h))
    step = int(width * 1.6)
    n = 0
    for n, y in enumerate(range(0, h, step), 1):
        im.crop((0, y, im.width, min(h, y + step))).save(os.path.join(out_dir, f"page_{n:02d}.png"))
    print(f"{h}px -> {n} pages in {out_dir}" + ("  (hit 16000px cap — preview a shorter range)" if h >= 15990 else ""))


if __name__ == "__main__":
    main(sys.argv[1:])
