"""Render a stretch of the illustrated book to PNG pages for a quick visual check.

Usage: python3 preview.py WORKDIR FIRST_TOC LAST_TOC [width_px]
Takes the built content (WORKDIR/build) from the first paragraph of FIRST_TOC to the end of
LAST_TOC (plus the chapter gallery right after it), wraps it in a reader-like page and screenshots
it with headless Chrome into WORKDIR/preview/page_NN.png.
"""
import glob
import json
import os
import re
import subprocess
import sys
import tempfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webfetch  # noqa: E402

work, first, last = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
width = int(sys.argv[4]) if len(sys.argv) > 4 else 760
book = json.load(open(os.path.join(work, "book.json"), encoding="utf-8"))
opf_dir = os.path.dirname(os.path.join(work, "build", book["opf"]))
paras = [p for p in book["paras"] if first <= p["toc"] <= last]
files = sorted({p["file"] for p in paras}, key=book["spine"].index)
BLOCK = re.compile(r"<!--illus:start-->.*?<!--illus:end-->", re.S)

body = []
for f in files:
    s = open(os.path.join(opf_dir, f), encoding="utf-8", newline="").read()
    ps = [p for p in paras if p["file"] == f]
    a, b = ps[0]["start"], ps[-1]["end"]  # offsets in the *original* file
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
    body.append("".join(piece))

html = f"""<html><head><meta charset="utf-8">
<style>body{{margin:0;background:#fbf8f1;}} .page{{max-width:{width - 60}px;margin:0 auto;padding:30px;
font:18px/1.75 'PingFang SC','Songti SC',serif;color:#222;}} p{{text-indent:2em;margin:0 0 .6em 0;}}
img{{height:auto;}}</style></head><body><div class="page">{''.join(body)}</div></body></html>"""
out_dir = os.path.join(work, "preview")
os.makedirs(out_dir, exist_ok=True)
for f in glob.glob(os.path.join(out_dir, "page_*.png")):
    os.remove(f)
page_html = os.path.join(opf_dir, "_preview.html")  # beside the content so relative image paths resolve
open(page_html, "w", encoding="utf-8").write(html)
chrome = webfetch.chrome_path()
shot = os.path.join(out_dir, "full.png")
subprocess.run([chrome, "--headless", "--disable-gpu", "--hide-scrollbars", f"--user-data-dir={tempfile.mkdtemp()}",
                "--allow-file-access-from-files", f"--window-size={width},16000", "--virtual-time-budget=8000",
                f"--screenshot={shot}", "file://" + os.path.abspath(page_html)], capture_output=True, timeout=180)
os.remove(page_html)
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
