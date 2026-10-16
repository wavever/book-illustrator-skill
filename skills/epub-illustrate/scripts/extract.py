"""Unpack an EPUB and index its paragraphs, so entities can be found and figures placed.

Usage: python3 extract.py book.epub WORKDIR

Writes
  WORKDIR/src/            the unpacked EPUB (never edited; inject.py builds from a copy)
  WORKDIR/book.json       {"opf", "spine", "toc": [...], "paras": [...]}
  WORKDIR/text/NN.txt     one file per TOC entry: "pid<TAB>text" lines, for reading

Each paragraph gets a stable id (p00001...) with its spine file and character span, so figures
can later be inserted *between* paragraphs without touching any original byte.
"""
import html
import json
import os
import re
import shutil
import sys
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

epub, work = sys.argv[1], sys.argv[2]
src = os.path.join(work, "src")
shutil.rmtree(src, ignore_errors=True)
with zipfile.ZipFile(epub) as z:
    for n in z.namelist():
        if n.startswith("/") or ".." in n.split("/"):
            sys.exit(f"unsafe path in epub: {n}")
    z.extractall(src)

if os.path.exists(os.path.join(src, "META-INF", "encryption.xml")):
    print("warning: META-INF/encryption.xml present — the book may be DRM-protected")

container = ET.parse(os.path.join(src, "META-INF", "container.xml")).getroot()
opf_rel = container.find(".//{*}rootfile").get("full-path")
opf_path = os.path.join(src, opf_rel)
opf_dir = os.path.dirname(opf_path)
opf = ET.parse(opf_path).getroot()
manifest = {i.get("id"): i for i in opf.find("{*}manifest")}
spine = [manifest[r.get("idref")].get("href") for r in opf.find("{*}spine")
         if manifest[r.get("idref")].get("media-type") == "application/xhtml+xml"]


def read(href):
    return open(os.path.join(opf_dir, unquote(href)), encoding="utf-8", newline="").read()


# --- table of contents (EPUB3 nav, else EPUB2 NCX) ---
toc = []
nav = next((i for i in manifest.values() if "nav" in (i.get("properties") or "").split()), None)
if nav is not None:
    root = ET.fromstring(read(nav.get("href")).encode())
    navel = next(n for n in root.iter("{*}nav") if "toc" in (n.get("{http://www.idpf.org/2007/ops}type") or "toc"))

    def walk_nav(ol, depth):
        for li in ol.findall("{*}li"):
            a = li.find("{*}a")
            if a is not None:
                toc.append({"title": "".join(a.itertext()).strip(), "href": a.get("href"), "depth": depth})
            sub = li.find("{*}ol")
            if sub is not None:
                walk_nav(sub, depth + 1)
    walk_nav(navel.find("{*}ol"), 0)
else:
    ncx_item = manifest[opf.find("{*}spine").get("toc")]
    root = ET.fromstring(read(ncx_item.get("href")).encode())

    def walk_ncx(parent, depth):
        for np in parent.findall("{*}navPoint"):
            toc.append({"title": np.find("{*}navLabel/{*}text").text.strip(),
                        "href": np.find("{*}content").get("src"), "depth": depth})
            walk_ncx(np, depth + 1)
    walk_ncx(root.find("{*}navMap"), 0)

# --- paragraphs ---
BLOCK = re.compile(r"<(p|h[1-6]|li|blockquote|div)\b[^>]*>(.*?)</\1\s*>", re.S | re.I)
TAG = re.compile(r"<[^>]+>")
paras, n = [], 0
for href in spine:
    s = read(href)
    for m in BLOCK.finditer(s):
        inner = m.group(2)
        if re.search(r"<(p|div|blockquote|h[1-6])\b", inner, re.I):
            continue  # a container; its children are indexed on their own
        text = html.unescape(TAG.sub("", inner)).strip()
        imgs = re.findall(r"<img\b[^>]*\bsrc=\"([^\"]+)\"", inner, re.I)
        if not text and not imgs:
            continue
        n += 1
        paras.append({"pid": f"p{n:05d}", "file": href, "start": m.start(), "end": m.end(),
                      "tag": m.group(1).lower(), "text": text, **({"imgs": imgs} if imgs else {})})

# locate each TOC entry, then assign paragraphs to the entry they fall under
spine_idx = {h: i for i, h in enumerate(spine)}
for t in toc:
    f, _, frag = t["href"].partition("#")
    s = read(f)
    pos = 0
    if frag:
        m = re.search(r"\bid=\"%s\"" % re.escape(frag), s)
        pos = m.start() if m else 0
    t.update(file=f, pos=(spine_idx.get(f, 0), pos))
order = sorted(range(len(toc)), key=lambda i: toc[i]["pos"])
for p in paras:
    key = (spine_idx[p["file"]], p["start"])
    p["toc"] = max((i for i in order if toc[i]["pos"] <= key), key=lambda i: toc[i]["pos"], default=0)

os.makedirs(os.path.join(work, "text"), exist_ok=True)
for i, t in enumerate(toc):
    lines = [f"{p['pid']}\t{p['text']}" + (f"  [已有图片 {','.join(p['imgs'])}]" if p.get("imgs") else "")
             for p in paras if p["toc"] == i]
    with open(os.path.join(work, "text", f"{i:03d}.txt"), "w", encoding="utf-8") as f:
        f.write(f"# {t['title']}\n" + "\n".join(lines) + "\n")
    t["paras"] = len(lines)

json.dump({"opf": os.path.relpath(opf_path, src), "spine": spine, "toc": toc, "paras": paras},
          open(os.path.join(work, "book.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for i, t in enumerate(toc):
    print(f"{i:03d} {'  ' * t['depth']}{t['title']}  ({t['paras']} paras)")
