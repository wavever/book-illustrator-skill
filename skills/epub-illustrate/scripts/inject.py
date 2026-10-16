"""Insert the picked images into a copy of the book and repackage it. Original text is untouched:
every insertion is a new block placed *between* existing elements, wrapped in
<!--illus:start--> ... <!--illus:end--> so verify.py can prove the rest is byte-identical.

Usage: python3 inject.py WORKDIR [--out NAME.epub]

entities.json "settings" (agreed with the user; top-level keys of the same name also work):
  inline:       true | false           figure after the first-mention paragraph (default true)
  gallery:      true | false           "本章人物与产品" page at the end of each chapter (default true)
  scope:        [first_toc, last_toc]  only place figures in this TOC range (optional)
  exclude_toc:  [toc indices]          never place figures here (front matter, existing gallery)
  chapter_re:   regex on TOC titles that start a chapter (default "^第.+章|^前言|^序|^后记|^尾声")
  gallery_title: heading of the chapter-end gallery (default "本章人物与产品")
per entity:
  pick:    "file" | ["file", ...]     chosen candidate(s) in candidates/<id>/ ; no pick -> skipped
  match:   ["text", ...]              strings whose first occurrence places the figure
  at:      "p00123"                   force the paragraph (overrides match)
  caption: "..."                      first caption line (default "name · role")
  note:    "..."                      second caption line, e.g. "摄于 1998 年" / "1997 年网页存档"
  crop:    [l, t, r, b]               fractions 0–1 to crop the picked image
"""
import html
import json
import os
import re
import shutil
import sys
import zipfile

from PIL import Image

work = sys.argv[1]
out_name = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else None
book = json.load(open(os.path.join(work, "book.json"), encoding="utf-8"))
cfg = json.load(open(os.path.join(work, "entities.json"), encoding="utf-8"))
src, build = os.path.join(work, "src"), os.path.join(work, "build")
shutil.rmtree(build, ignore_errors=True)
shutil.copytree(src, build)
opf_path = os.path.join(build, book["opf"])
opf_dir = os.path.dirname(opf_path)
ILLUS = "illus"
os.makedirs(os.path.join(opf_dir, ILLUS), exist_ok=True)

S = {**cfg, **cfg.get("settings", {})}
if not S.get("inline", True) and not S.get("gallery", True):
    sys.exit("settings: inline and gallery are both off — nothing to insert")
toc, paras = book["toc"], book["paras"]
lo, hi = S.get("scope", [0, len(toc) - 1])
exclude = set(S.get("exclude_toc", []))
chapter_re = re.compile(S.get("chapter_re", r"^第.+章|^前言|^序|^后记|^尾声"))
toc_titles = [t["title"] for t in toc]


def is_heading(p):
    return p["tag"].startswith("h") or any(p["text"] and p["text"] in t for t in toc_titles)


def placeable(p):
    return lo <= p["toc"] <= hi and p["toc"] not in exclude and not is_heading(p) and "imgs" not in p


# 1. where does each entity first appear?
by_pid = {p["pid"]: p for p in paras}
placed = []  # (para_index, offset_in_para, entity)
for e in cfg["entities"]:
    if not e.get("pick"):
        continue
    hit = None
    if e.get("at"):
        hit = (paras.index(by_pid[e["at"]]), 0)
    else:
        for i, p in enumerate(paras):
            if not placeable(p):
                continue
            offs = [p["text"].find(m) for m in e["match"] if m in p["text"]]
            if offs:
                hit = (i, min(offs))
                break
    if hit is None:
        print(f"warning: {e['id']} not found in scope, skipped")
        continue
    placed.append((hit[0], hit[1], e))
placed.sort(key=lambda x: (x[0], x[1]))


# 2. images -> OEBPS/illus/<id>[_n].jpg
def prepare(e):
    picks = e["pick"] if isinstance(e["pick"], list) else [e["pick"]]
    out = []
    for n, f in enumerate(picks):
        im = Image.open(os.path.join(work, "candidates", e["id"], f))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, "white")
            bg.paste(im, mask=im.split()[-1])
            im = bg
        im = im.convert("RGB")
        if e.get("crop") and n == 0:
            l, t, r, b = e["crop"]
            w, h = im.size
            im = im.crop((int(l * w), int(t * h), int(r * w), int(b * h)))
        im.thumbnail((1400, 1400))
        name = f"{e['id']}{'' if n == 0 else f'_{n}'}.jpg"
        im.save(os.path.join(opf_dir, ILLUS, name), quality=85)
        out.append((name, im.size))
    return out


images = {e["id"]: prepare(e) for _, _, e in placed}

esc = html.escape
CAP = "font-size:0.72em;line-height:1.4;color:#777;margin:0.3em 0 0 0;text-indent:0;text-align:center;"


def caption(e):
    first = e.get("caption") or (f"{e['name']} · {e['role']}" if e.get("role") else e["name"])
    note = f"<br/>{esc(e['note'])}" if e.get("note") else ""
    return f'<p style="{CAP}">{esc(first)}{note}</p>'


def cell(e, width, max_h):
    imgs = "".join(
        f'<img src="{ILLUS}/{n}" alt="{esc(e["name"])}" style="max-width:100%;max-height:{max_h}em;" />'
        for n, _ in images[e["id"]][:1])
    return (f'<div style="display:inline-block;vertical-align:top;width:{width}%;margin:0 1%;'
            f'text-align:center;text-indent:0;">{imgs}{caption(e)}</div>')


def inline_block(group):
    k = len(group)
    width = {1: 70, 2: 46, 3: 31}.get(k, 31)
    max_h = 12 if k == 1 else 10
    cells = "".join(cell(e, width, max_h) for e in group)
    return ('<!--illus:start--><div class="illus-figure" style="margin:0.8em 0 1.2em 0;'
            f'text-align:center;text-indent:0;">{cells}</div><!--illus:end-->')


def gallery_block(chapter_title, ents):
    cells = "".join(cell(e, 31, 9) for e in ents)
    return ('<!--illus:start--><div class="illus-gallery" style="page-break-before:always;'
            'text-align:center;text-indent:0;margin-top:1em;">'
            f'<p style="font-size:1.1em;font-weight:bold;text-align:center;text-indent:0;margin:0 0 0.2em 0;">'
            f'{esc(S.get("gallery_title", "本章人物与产品"))}</p>'
            f'<p style="{CAP}margin-bottom:1em;">{esc(chapter_title)}</p>{cells}</div><!--illus:end-->')


# 3. insertion points: (file, offset, html)
inserts = []
groups = {}
for i, _, e in placed:
    groups.setdefault(i, []).append(e)
for i, group in groups.items() if S.get("inline", True) else []:
    for k in range(0, len(group), 3):
        inserts.append((paras[i]["file"], paras[i]["end"], inline_block(group[k:k + 3])))

chapters = [i for i, t in enumerate(toc) if chapter_re.search(t["title"])] if S.get("gallery", True) else []
for c, start in enumerate(chapters):
    end = chapters[c + 1] if c + 1 < len(chapters) else len(toc)
    ents = [e for i, _, e in placed if start <= paras[i]["toc"] < end]
    if not ents:
        continue
    last = max((p for p in paras if start <= p["toc"] < end), key=lambda p: (book["spine"].index(p["file"]), p["end"]))
    inserts.append((last["file"], last["end"], gallery_block(toc[start]["title"], ents)))

by_file = {}
for f, off, h in inserts:
    by_file.setdefault(f, []).append((off, h))
for f, items in by_file.items():
    path = os.path.join(opf_dir, f)
    s = open(path, encoding="utf-8", newline="").read()
    for off, h in sorted(items, key=lambda x: x[0], reverse=True):
        s = s[:off] + h + s[off:]
    open(path, "w", encoding="utf-8", newline="").write(s)

# 4. manifest
opf = open(opf_path, encoding="utf-8", newline="").read()
items = "".join(f'<item href="{ILLUS}/{n}" id="illus-{os.path.splitext(n)[0]}" media-type="image/jpeg"/>\n'
                for imgs in images.values() for n, _ in imgs)
opf = opf.replace("</manifest>", items + "</manifest>", 1)
open(opf_path, "w", encoding="utf-8", newline="").write(opf)

# 5. package (mimetype first, stored)
out = os.path.join(work, out_name or "illustrated.epub")
with zipfile.ZipFile(out, "w") as z:
    z.write(os.path.join(build, "mimetype"), "mimetype", compress_type=zipfile.ZIP_STORED)
    for root, _, files in os.walk(build):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, build)
            if rel != "mimetype":
                z.write(full, rel, compress_type=zipfile.ZIP_DEFLATED)
# 6. review list: what went where, from which source, and what is still missing
lines = [f"# 配图清单 · {cfg.get('book', '')}", "",
         "| 位置 | 段落开头 | 对象 | 图注 | 来源 |", "|---|---|---|---|---|"]
for i, _, e in placed:
    pick = e["pick"] if isinstance(e["pick"], str) else e["pick"][0]
    srcs = json.load(open(os.path.join(work, "candidates", e["id"], "SOURCE.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(work, "candidates", e["id"], "SOURCE.json")) else []
    hit = next((s for s in srcs if s["file"] == os.path.basename(pick)), {})
    cap = (e.get("caption") or e["name"]) + (f"（{e['note']}）" if e.get("note") else "")
    lines.append(f"| {toc[paras[i]['toc']]['title']} | {paras[i]['text'][:16]}… | {e['name']} | {cap} | "
                 f"{hit.get('from', '')} {hit.get('url', '')} |")
missing = [e for e in cfg["entities"] if not e.get("pick")]
if missing:
    lines += ["", "## 暂无合适图片", ""] + [f"- {e['name']}（{e.get('role', '')}）" for e in missing]
open(os.path.join(work, "配图清单.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

print(json.dumps({"figures": sum(1 for f, o, h in inserts if "illus-figure" in h), "entities": len(placed),
                  "galleries": sum(1 for f, o, h in inserts if "illus-gallery" in h), "out": out},
                 ensure_ascii=False))
for i, _, e in placed:
    print(f"  {paras[i]['pid']} {e['id']}")
