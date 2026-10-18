"""Insert the picked images into a copy of the book and write it in the same native format.
Original text is untouched: every insertion is a new block placed *between* existing elements,
wrapped in <!--illus:start--> ... <!--illus:end--> so verify.py can prove the rest is
byte-identical (if the source already contains that marker, a unique variant is used).

Usage: python3 inject.py WORKDIR [--out OUTPUT]

OUTPUT (relative paths are relative to WORKDIR; absolute paths are fine) must match the source:
  EPUB      .epub                  images in illus/ next to the OPF, added to the manifest
  HTML      .html/.htm (.xhtml for XHTML sources)
  Markdown  .md/.markdown
For HTML and Markdown, the inserted images go to OUTPUT_STEM_assets/ beside OUTPUT, and the local
images/stylesheets the source references are copied beside OUTPUT under their original relative
paths (an existing different file there is a conflict and aborts before anything is written).
Default OUTPUT: illustrated.<source extension>.

entities.json "settings" (agreed with the user; top-level keys of the same name also work):
  inline:       true | false           figure after the first-mention paragraph (default true)
  gallery:      true | false           "本章人物与产品" page at the end of each chapter (default true)
  scope:        [first_toc, last_toc]  only place figures in this TOC range (optional)
  exclude_toc:  [toc indices]          never place figures here (front matter, existing gallery)
  chapter_re:   regex on TOC titles that start a chapter (default "^第.+章|^前言|^序|^后记|^尾声";
                if nothing matches, top-level TOC entries are used)
  gallery_title: heading of the chapter-end gallery (default "本章人物与产品"; an ASCII title also
                switches the image-credits section to English labels)
  credits:      true | false           image-credits section after the last paragraph (default true):
                for every inserted image the creator, licence (+URL), credit/attribution/copyright/
                usage-terms text, source page and the changes made here (JPEG re-encode, downscale,
                crop, transparency fill), copied from SOURCE.json; missing values read "unknown".
                Not a rights check. With false, keep 配图清单.md with the book when sharing it.
  credits_title: heading of that section (default "图片来源" / "Image credits")
  image_compression: "compact" | "balanced" | "high"  (default "balanced"), or an object
                {"preset": ..., "max_side", "quality", "target_kb", "min_quality", "min_side"} overriding
                single values (target_kb null = no byte target). Only the inserted pictures are
                compressed (see compress.py); per-image results go to WORKDIR/compression-report.json.
per entity:
  pick:    "file" | ["file", ...]     chosen candidate(s) in candidates/<id>/ ; no pick -> skipped
  match:   ["text", ...]              strings whose first occurrence places the figure
  at:      "p00123"                   force the paragraph (overrides match)
  caption: "..."                      first caption line (default "name · role")
  note:    "..."                      second caption line, e.g. "摄于 1998 年" / "1997 年网页存档"
  crop:    [l, t, r, b]               fractions 0–1 to crop the first picked image, measured on the
                                      upright image (EXIF orientation applied, as sheet.py shows it)
"""
import html
import json
import os
import posixpath
import re
import shutil
import sys
import tempfile
import time
import zipfile
from urllib.parse import quote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import compress  # noqa: E402
from native_util import (OUT_EXTS, book_format, choose_marker, decode, encode, inside,  # noqa: E402
                         marker_view, markers, member_of, safe_join, safe_name, sha256, url_to)


def fail(msg):
    sys.exit(f"inject.py: {msg}")


argv = sys.argv[1:]
if not argv or argv[0].startswith("--"):
    sys.exit(__doc__)
work = argv[0]
out_name = argv[argv.index("--out") + 1] if "--out" in argv else None
book = json.load(open(os.path.join(work, "book.json"), encoding="utf-8"))
cfg = json.load(open(os.path.join(work, "entities.json"), encoding="utf-8"))
fmt = book_format(book)
src, build = os.path.join(work, "src"), os.path.join(work, "build")

# --- output path ---------------------------------------------------------------------------------
source_name = (book.get("source") or {}).get("name", "")
src_ext = os.path.splitext(source_name)[1].lower()
default_ext = {"epub": ".epub", "html": src_ext or ".html", "markdown": src_ext or ".md"}[fmt]
out = os.path.abspath(os.path.join(work, out_name or "illustrated" + default_ext))
ext = os.path.splitext(out)[1].lower()
allowed = OUT_EXTS[fmt]
if fmt == "html":
    allowed = (".xhtml",) if src_ext == ".xhtml" else (".html", ".htm")
if ext not in allowed:
    fail(f"--out {out_name!r}: a native {fmt} job writes {'/'.join(allowed)} "
         "(use export.py to convert to another format)")
protected = {os.path.realpath(p) for p in (book.get("source_path"), (book.get("source") or {}).get("path")) if p}
if os.path.realpath(out) in protected:
    fail(f"--out would overwrite the original source {out}")
for d in (src, build, os.path.join(work, "text")):
    if inside(out, d):
        fail(f"--out must not be inside {d}")
for p in protected:
    if inside(p, build) or inside(p, src):
        fail(f"the original source {p} lies inside {build if inside(p, build) else src}, which is rebuilt — "
             "move it and re-run extract.py")
if fmt != "epub" and os.path.isdir(out):
    fail(f"--out {out} is a directory")

S = {**cfg, **cfg.get("settings", {})}
if not S.get("inline", True) and not S.get("gallery", True):
    fail("settings: inline and gallery are both off — nothing to insert")
try:
    COMP = compress.resolve(S.get("image_compression"))
except compress.ConfigError as ex:
    fail(str(ex))
report_path = os.path.join(work, "compression-report.json")
if (os.path.realpath(report_path) in protected | {os.path.realpath(out)}
        or any(inside(report_path, d) for d in (src, build, os.path.join(work, "candidates")))
        or os.path.exists(report_path) and not os.path.isfile(report_path)):
    fail(f"{report_path} points at the source, the output or a working/candidate file (or is not a regular "
         "file); remove it and re-run")
toc, paras = book["toc"], book["paras"]
lo, hi = S.get("scope", [0, len(toc) - 1])
exclude = set(S.get("exclude_toc", []))
chapter_re = re.compile(S.get("chapter_re", r"^第.+章|^前言|^序|^后记|^尾声"))
toc_titles = [t["title"] for t in toc]
spine = book["spine"]


def ins_of(p):
    return p.get("ins", p["end"])


def is_heading(p):
    return p["tag"].startswith("h") or any(p["text"] and p["text"] in t for t in toc_titles)


def placeable(p):
    return (lo <= p["toc"] <= hi and p["toc"] not in exclude and not is_heading(p) and "imgs" not in p
            and ins_of(p) is not None)


# 1. where does each entity first appear?
by_pid = {p["pid"]: p for p in paras}
placed = []  # (para_index, offset_in_para, entity)
for e in cfg["entities"]:
    if not e.get("pick"):
        continue
    hit = None
    if e.get("at"):
        if e["at"] not in by_pid:
            fail(f"{e['id']}: at={e['at']!r} is not a paragraph id")
        if ins_of(by_pid[e["at"]]) is None:
            fail(f"{e['id']}: nothing can be inserted after {e['at']} (end of a file without final newline)")
        hit = (paras.index(by_pid[e["at"]]), 0)
    else:
        for i, p in enumerate(paras):
            if not placeable(p):
                continue
            offs = [p["text"].find(m) for m in (e.get("match") or [e["name"]]) if m and m in p["text"]]
            if offs:
                hit = (i, min(offs))
                break
    if hit is None:
        print(f"warning: {e['id']} not found in scope, skipped", file=sys.stderr)
        continue
    placed.append((hit[0], hit[1], e))
placed.sort(key=lambda x: (x[0], x[1]))


# 2. images (compressed JPEG, see compress.py), named after the entity id; each is written once and
# shared by the inline figure, the gallery and the credits
used_names = set()
changes = {}  # output image name -> what was done to the picked file (credits + compression report)


def prepare(e):
    picks = e["pick"] if isinstance(e["pick"], list) else [e["pick"]]
    out_imgs = []
    for n, f in enumerate(picks):
        data, done = compress.compress(os.path.join(work, "candidates", e["id"], f), COMP,
                                       e.get("crop") if n == 0 else None)
        stem = safe_name(e["id"]) + ("" if n == 0 else f"_{n}")
        name, k = f"{stem}.jpg", 1
        while name.lower() in used_names:
            k += 1
            name = f"{stem}-{k}.jpg"
        used_names.add(name.lower())
        changes[name] = {"entity": e["id"], "pick": f, "image": name, **done}
        out_imgs.append((name, (done["output"]["width"], done["output"]["height"]), data))
    return out_imgs


images = {e["id"]: prepare(e) for _, _, e in placed}

# where images live and how a content file refers to them
members = book.get("members")
if fmt == "epub":
    if not members:  # job extracted by an older extract.py
        files = []
        for root, _, fs in os.walk(src):
            files += [os.path.relpath(os.path.join(root, f), src).replace(os.sep, "/") for f in fs]
        files.sort(key=lambda m: (m != "mimetype", m))
        members = [[m, zipfile.ZIP_STORED if m == "mimetype" else zipfile.ZIP_DEFLATED] for m in files]
    member_names = {m for m, _ in members}
    opf_dir = posixpath.dirname(book["opf"])
    illus, k = "illus", 1
    while any(m.startswith(posixpath.join(opf_dir, illus) + "/") for m in member_names):
        k += 1
        illus = f"illus_{k}"
    img_dir = posixpath.join(opf_dir, illus)

    def img_url(name, content_member):
        return url_to(posixpath.join(img_dir, name), content_member)
else:
    stem = os.path.splitext(os.path.basename(out))[0]
    assets = stem + "_assets"
    resources = book["source"].get("resources", [])
    taken = {r.lower() for r in resources}
    k = 1
    while any(r.startswith(assets.lower() + "/") for r in taken):
        k += 1
        assets = f"{stem}_assets{k}"
    out_doc = os.path.basename(out)

    def img_url(name, content_member):
        return quote(f"{assets}/{name}")

if fmt == "epub":
    def text_views():
        for m in member_names:
            yield marker_view(open(safe_join(src, m), "rb").read())[0]
else:
    def text_views():
        yield marker_view(open(safe_join(src, source_name), "rb").read())[0]
marker = book.get("marker") or choose_marker(text_views())
START, END = markers(marker)

# 3. blocks
esc = html.escape
CAP = "font-size:0.72em;line-height:1.4;color:#777;margin:0.3em 0 0 0;text-indent:0;text-align:center;"


def first_caption(e):
    return e.get("caption") or (f"{e['name']} · {e['role']}" if e.get("role") else e["name"])


def caption(e):
    note = f"<br/>{esc(e['note'])}" if e.get("note") else ""
    return f'<p style="{CAP}">{esc(first_caption(e))}{note}</p>'


def cell(e, width, max_h, member):
    imgs = "".join(
        f'<img src="{esc(img_url(n, member))}" alt="{esc(e["name"])}" style="max-width:100%;max-height:{max_h}em;" />'
        for n, _, _ in images[e["id"]][:1])
    return (f'<div style="display:inline-block;vertical-align:top;width:{width}%;margin:0 1%;'
            f'text-align:center;text-indent:0;">{imgs}{caption(e)}</div>')


def inline_html(group, member):
    k = len(group)
    width = {1: 70, 2: 46, 3: 31}.get(k, 31)
    max_h = 12 if k == 1 else 10
    cells = "".join(cell(e, width, max_h, member) for e in group)
    return (f'{START}<div class="illus-figure" style="margin:0.8em 0 1.2em 0;'
            f'text-align:center;text-indent:0;">{cells}</div>{END}')


def gallery_html(chapter_title, ents, member):
    cells = "".join(cell(e, 31, 9, member) for e in ents)
    return (f'{START}<div class="illus-gallery" style="page-break-before:always;'
            'text-align:center;text-indent:0;margin-top:1em;">'
            f'<p style="font-size:1.1em;font-weight:bold;text-align:center;text-indent:0;margin:0 0 0.2em 0;">'
            f'{esc(S.get("gallery_title", "本章人物与产品"))}</p>'
            f'<p style="{CAP}margin-bottom:1em;">{esc(chapter_title)}</p>{cells}</div>{END}')


MD_SPECIAL = re.compile(r"([\\`*_\[\]<>&!|~#])")


def md_esc(s):
    return MD_SPECIAL.sub(r"\\\1", s)


def md_caption(e):
    return f"*{md_esc(first_caption(e))}*" + (f"（{md_esc(e['note'])}）" if e.get("note") else "")


def md_image(e):
    n = images[e["id"]][0][0]
    return f"![{md_esc(e['name'])}]({img_url(n, None)})"


def inline_md(group, member):
    return ["illus-figure", " ".join(md_image(e) for e in group), " ｜ ".join(md_caption(e) for e in group)]


def gallery_md(chapter_title, ents, member):
    return (["illus-gallery", f"**{md_esc(S.get('gallery_title', '本章人物与产品'))}**", f"*{md_esc(chapter_title)}*"]
            + [f"{md_image(e)}\\\n{md_caption(e)}" for e in ents])


# Image credits: an in-book section (inside markers like every other insertion) so a book shared on
# its own still names each picture's creator, licence, source and the changes made here. Values are
# copied from SOURCE.json (source-page / provider metadata); nothing is inferred, missing -> unknown.
EN = bool(S.get("gallery_title")) and S["gallery_title"].isascii()
L = ({"title": "Image credits", "unknown": "unknown", "author": "Creator", "license": "Licence",
      "credit": "Credit", "attribution": "Attribution", "copyright": "Copyright", "terms": "Usage terms",
      "source": "Source", "changes": "Changes", "local": "local file supplied by the user", "sep": "; ",
      "colon": ": ", "jpeg": "converted to JPEG", "rejpeg": "re-encoded as JPEG", "crop": "cropped",
      "quality": " (quality {q})", "keep": "original JPEG data kept", "strip": "metadata removed",
      "orient": "rotated upright (EXIF orientation)",
      "flat": "transparency filled with white", "resize": "downscaled to {w}×{h} px",
      "note": "From the metadata recorded by each image's source page or supplier; not independently "
              "verified and not a statement that use is permitted. “unknown” means nothing was recorded."}
     if EN else
     {"title": "图片来源", "unknown": "未知", "author": "作者", "license": "许可", "credit": "图片署名",
      "attribution": "署名要求", "copyright": "版权", "terms": "使用条款", "source": "来源", "changes": "修改",
      "local": "用户提供的本地文件", "sep": "；", "colon": "：", "jpeg": "转换为 JPEG", "rejpeg": "重新编码为 JPEG",
      "quality": "（质量 {q}）", "keep": "保留原 JPEG 图像数据", "strip": "已移除元数据",
      "orient": "按 EXIF 方向转正", "crop": "已裁剪", "flat": "透明部分填充为白色", "resize": "缩小至 {w}×{h} 像素",
      "note": "以下信息取自各图片来源页面或提供者记录的元数据，未经独立核实，也不表示已获得使用许可；"
              "“未知”表示没有记录到该项。"})
LOCAL_SOURCES = {"reuse"}


def source_rows(e):
    p = os.path.join(work, "candidates", e["id"], "SOURCE.json")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        rows = json.load(f)
    return rows if isinstance(rows, list) else []


def provenance(e, pick):
    """The SOURCE.json provenance entry for a picked file: the first one carrying credit/licence
    metadata (identical bytes may have come from several sources), else the first; {} if none."""
    row = next((r for r in source_rows(e) if r.get("file") == os.path.basename(pick)), None)
    if row is None:
        return {}
    entries = row.get("provenance") or [row]
    return next((x for x in entries if any(x.get(k) for k in ("license", "author", "attribution", "credit"))),
                entries[0])


def is_local(prov):
    return prov.get("from") in LOCAL_SOURCES or bool(prov.get("file_path")) and prov.get("url") == prov.get("file_path")


def source_page(prov):
    # older SOURCE.json rows keep the image URL in "url" and the page in "source_page"/"page"
    return prov.get("source_page") or prov.get("page") or prov.get("url") or ""


def image_url(prov):
    """The image file's own URL when it is recorded and differs from the source page."""
    img = prov.get("img") or (prov.get("url") if prov.get("source_page") or prov.get("page") else "")
    return img if img and img != source_page(prov) and not is_local(prov) else ""


def credit_source(prov):
    if is_local(prov):
        return L["local"]  # never write a local filesystem path into the book
    return source_page(prov) or L["unknown"]


def credit_changes(name):
    c = changes[name]
    if c["kept_original_jpeg"]:
        out = [L["keep"]] + ([L["strip"]] if c["output"]["bytes"] != c["original"]["bytes"] else [])
    else:
        out = [(L["rejpeg"] if c["original"]["format"] == "JPEG" else L["jpeg"]) + L["quality"].format(q=c["quality"])]
    if c["oriented"]:
        out.append(L["orient"])
    if c["cropped"]:
        out.append(L["crop"])
    if c["flattened"]:
        out.append(L["flat"])
    if c["resized"]:
        out.append(L["resize"].format(w=c["resized"][0], h=c["resized"][1]))
    return ", ".join(out) if EN else "，".join(out)


def credit_entries():
    """[(label, [(field, value), ...])] for every image written into the book, in placement order."""
    out, seen = [], set()
    for _, _, e in placed:
        if e["id"] in seen:
            continue
        seen.add(e["id"])
        for k, (name, _, _) in enumerate(images[e["id"]]):
            prov = provenance(e, changes[name]["pick"])
            lic = prov.get("license") or L["unknown"]
            if prov.get("license_url"):
                lic += f" ({prov['license_url']})" if EN else f"（{prov['license_url']}）"
            fields = [("author", prov.get("author") or L["unknown"]), ("license", lic)]
            fields += [(key, prov[k2]) for key, k2 in (("credit", "credit"), ("attribution", "attribution"),
                                                       ("copyright", "copyright"), ("terms", "usage_terms"))
                       if prov.get(k2)]
            fields += [("source", credit_source(prov)), ("changes", credit_changes(name))]
            out.append((first_caption(e) + (f" ({k + 1})" if k else ""), fields))
    return out


def credit_line(label, fields, escape):
    return escape(label) + " — " + L["sep"].join(escape(L[f] + L["colon"] + v) for f, v in fields)


CREDIT_P = "font-size:0.72em;line-height:1.5;color:#555;margin:0 0 0.5em 0;text-indent:0;text-align:left;"


def credits_html(entries, member):
    rows = "".join(f'<p style="{CREDIT_P}word-wrap:break-word;">{n}. {credit_line(lab, fs, esc)}</p>'
                   for n, (lab, fs) in enumerate(entries, 1))
    return (f'{START}<div class="illus-credits" style="page-break-before:always;text-indent:0;margin-top:1em;">'
            f'<p style="font-size:1.1em;font-weight:bold;text-align:center;text-indent:0;margin:0 0 0.4em 0;">'
            f'{esc(S.get("credits_title", L["title"]))}</p>'
            f'<p style="{CAP}margin-bottom:1em;">{esc(L["note"])}</p>{rows}</div>{END}')


def credits_md(entries, member):
    return (["illus-credits", f"**{md_esc(S.get('credits_title', L['title']))}**", f"*{md_esc(L['note'])}*"]
            + [f"{n}\\. {credit_line(lab, fs, md_esc)}" for n, (lab, fs) in enumerate(entries, 1)])


inline_block, gallery_block = (inline_md, gallery_md) if fmt == "markdown" else (inline_html, gallery_html)
credits_block = credits_md if fmt == "markdown" else credits_html

# 4. insertion points: (file, offset, block, kind)
inserts = []
groups = {}
for i, _, e in placed:
    groups.setdefault(i, []).append(e)
for i, group in groups.items() if S.get("inline", True) else []:
    f = paras[i]["file"]
    for k in range(0, len(group), 3):
        inserts.append((f, ins_of(paras[i]), inline_block(group[k:k + 3], member_of(book, f)), "figure"))

chapters = []
if S.get("gallery", True):
    chapters = [i for i, t in enumerate(toc) if chapter_re.search(t["title"])]
    if not chapters:
        chapters = [i for i, t in enumerate(toc) if t.get("depth", 0) == 0]
        print(f"note: no TOC title matches chapter_re; galleries follow the {len(chapters)} top-level TOC entries",
              file=sys.stderr)
for c, start in enumerate(chapters):
    end = chapters[c + 1] if c + 1 < len(chapters) else len(toc)
    ents = [e for i, _, e in placed if start <= paras[i]["toc"] < end]
    cands = [p for p in paras if start <= p["toc"] < end and ins_of(p) is not None]
    if not ents or not cands:
        continue
    last = max(cands, key=lambda p: (spine.index(p["file"]), ins_of(p), p["end"]))
    inserts.append((last["file"], ins_of(last), gallery_block(toc[start]["title"], ents,
                                                              member_of(book, last["file"])), "gallery"))

# image credits after the last paragraph of the book (outside excluded TOC entries when possible)
if placed and S.get("credits", True):
    ends = [p for p in paras if ins_of(p) is not None]
    last = max([p for p in ends if p["toc"] not in exclude] or ends,
               key=lambda p: (spine.index(p["file"]), ins_of(p), p["end"]))
    inserts.append((last["file"], ins_of(last), credits_block(credit_entries(), member_of(book, last["file"])),
                    "credits"))
elif placed:
    print('warning: settings "credits": false — the book carries no image credits; keep 配图清单.md with it',
          file=sys.stderr)


def assemble(text, items, newline):
    """Original text with blocks spliced in; blocks at one offset keep their list order."""
    out_parts, pos = [], 0
    for off, block in sorted(items, key=lambda x: x[0]):
        if off < pos or off > len(text):
            fail(f"insertion offset {off} out of range — book.json does not match src/ (re-run extract.py)")
        out_parts.append(text[pos:off])
        if isinstance(block, list):  # Markdown: block-level HTML comments around Markdown paragraphs
            body = "\n\n".join(block[1:]).replace("\n", newline)
            glue = off == len(text) and text and not text.endswith(("\n", "\r"))
            block = START + newline * 2 + body + newline * 2 + END + ("" if glue else newline)
        out_parts.append(block)
        pos = off
    out_parts.append(text[pos:])
    return "".join(out_parts)


by_file = {}
for f, off, block, _ in inserts:
    by_file.setdefault(f, []).append((off, block))
modified = {}  # container-relative path -> new bytes
for f, items in by_file.items():
    member = member_of(book, f)
    raw = open(safe_join(src, member), "rb").read()
    if fmt == "epub":
        enc = book.get("encodings", {}).get(f)
        text, enc = decode(raw, "xml", enc)
        newline = "\n"
    else:
        if book["source"].get("sha256") and sha256(safe_join(src, member)) != book["source"]["sha256"]:
            fail(f"src/{member} changed since extract.py — re-run extract.py")
        text, enc = decode(raw, "text", book["source"]["encoding"])
        newline = book["source"].get("newline", "\n")
    modified[member] = encode(assemble(text, items, newline), enc)

all_images = [img for imgs in images.values() for img in imgs]
if fmt == "epub":
    # 5. manifest: our items, wrapped in markers, before </manifest>
    opf_raw = open(safe_join(src, book["opf"]), "rb").read()
    opf_text, opf_enc = decode(opf_raw, "xml")
    m = re.search(r"</((?:[\w.-]+:)?)manifest\s*>", opf_text)
    if m is None:
        fail(f"{book['opf']}: no </manifest>")
    ids = set(re.findall(r"""\bid\s*=\s*["']([^"']+)["']""", opf_text))
    items = []
    for n, _, _ in all_images:
        iid = "illus-" + re.sub(r"[^A-Za-z0-9._-]", "_", os.path.splitext(n)[0])
        base, k = iid, 1
        while iid in ids:
            k += 1
            iid = f"{base}-{k}"
        ids.add(iid)
        items.append(f'<{m.group(1)}item href="{esc(quote(f"{illus}/{n}"))}" id="{iid}" media-type="image/jpeg"/>')
    if items:
        nl = "\r\n" if "\r\n" in opf_text else "\n"
        block = START + "".join(nl + it for it in items) + nl + END
        modified[book["opf"]] = encode(opf_text[:m.start()] + block + opf_text[m.start():], opf_enc)
    new_files = {posixpath.join(img_dir, n): data for n, _, data in all_images}

    # build/ mirrors the output for preview.py
    shutil.rmtree(build, ignore_errors=True)
    shutil.copytree(src, build)
    for name, data in list(modified.items()) + list(new_files.items()):
        p = safe_join(build, name)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        open(p, "wb").write(data)

    # 6. package: mimetype first and stored, original member order and compression kept
    os.makedirs(os.path.dirname(out), exist_ok=True)
    stamp = time.localtime()[:6]
    order = sorted(members, key=lambda x: x[0] != "mimetype")
    fd, tmp = tempfile.mkstemp(suffix=".epub", dir=os.path.dirname(out))
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp, "w") as z:
            for name, ctype in order:
                data = modified.get(name)
                if data is None:
                    data = open(safe_join(src, name), "rb").read()
                zi = zipfile.ZipInfo(name, stamp)
                zi.compress_type = zipfile.ZIP_STORED if name == "mimetype" else ctype
                zi.external_attr = 0o644 << 16
                z.writestr(zi, data)
            for name, data in new_files.items():
                zi = zipfile.ZipInfo(name, stamp)
                zi.compress_type = zipfile.ZIP_DEFLATED
                zi.external_attr = 0o644 << 16
                z.writestr(zi, data)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    assets_dir, copied = None, 0
else:
    # 5. HTML / Markdown: OUTPUT + OUTPUT_STEM_assets/ + the source's own local resources
    out_dir = os.path.dirname(out)
    assets_dir = os.path.join(out_dir, assets)
    plan = {os.path.join(out_dir, *r.split("/")): open(safe_join(src, r), "rb").read() for r in resources}
    conflicts = []
    for p, data in plan.items():
        if os.path.isfile(p) and open(p, "rb").read() != data:
            conflicts.append(p)
        elif os.path.exists(p) and not os.path.isfile(p):
            conflicts.append(p)
    conflicts += [p for p in (os.path.join(out_dir, assets, n) for n, _, _ in all_images)
                  if os.path.realpath(p) in protected]
    written = {os.path.realpath(p) for p in plan} | {os.path.realpath(os.path.join(out_dir, assets, n))
                                                    for n, _, _ in all_images}
    if os.path.realpath(report_path) in written:
        conflicts.append(report_path + " (compression report)")
    if conflicts:
        fail("these files already exist beside the output with different content (choose another output "
             f"directory): {conflicts[:5]}")
    doc = modified.get(source_name) or open(safe_join(src, source_name), "rb").read()
    # build/ mirrors the output for preview.py
    shutil.rmtree(build, ignore_errors=True)
    os.makedirs(os.path.join(build, assets))
    open(os.path.join(build, out_doc), "wb").write(doc)
    for n, _, data in all_images:
        open(os.path.join(build, assets, n), "wb").write(data)
    for r in resources:
        p = safe_join(build, r)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        shutil.copyfile(safe_join(src, r), p)
    os.makedirs(out_dir, exist_ok=True)
    copied = 0
    for p, data in plan.items():
        if not os.path.exists(p):
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(data)
            copied += 1
    if all_images:
        os.makedirs(assets_dir, exist_ok=True)
    for n, _, data in all_images:
        open(os.path.join(assets_dir, n), "wb").write(data)
    fd, tmp = tempfile.mkstemp(suffix=ext, dir=out_dir)
    with os.fdopen(fd, "wb") as f:
        f.write(doc)
    os.replace(tmp, out)

# 7. compression report: only the inserted pictures were compressed; source-book files are untouched
def kib(n):
    return f"{n / 1048576:.1f} MiB" if n >= 1048576 else f"{n / 1024:.0f} KiB"


rows = [changes[n] for n, _, _ in all_images]
totals = {"images": len(rows), "candidate_bytes": sum(r["original"]["bytes"] for r in rows),
          "image_bytes": sum(r["output"]["bytes"] for r in rows),
          "kept_original_jpeg": sum(1 for r in rows if r["kept_original_jpeg"]),
          "targets_unmet": sum(1 for r in rows if r["target_met"] is False),
          "output_bytes": os.path.getsize(out)}
settings_used = {k: COMP[k] for k in compress.FIELDS}
report = {"version": 1, "preset": COMP["preset"], "overrides": COMP["overrides"], "settings": settings_used,
          "out": out, "totals": totals, "images": rows,
          "notes": ["candidate_bytes/image_bytes cover only the pictures inserted by inject.py; images, text, "
                    "markup and styles of the source book are copied unchanged.",
                    "output_bytes is the size of the written output file (for EPUB the whole book).",
                    "target_kb is best effort: target_met false means quality and size floors were reached first."]}
fd, tmp = tempfile.mkstemp(suffix=".json", dir=work)
with os.fdopen(fd, "w", encoding="utf-8") as f:
    json.dump(report, f, ensure_ascii=False, indent=1)
os.replace(tmp, report_path)  # replaces the path itself, never writes through a link
target_txt = f"{COMP['target_kb']:g} KiB" if COMP["target_kb"] is not None else None
comp_line = (f"图片压缩（{COMP['preset']}{'，自定义' if COMP['overrides'] else ''}）：{totals['images']} 张新插图，"
             f"所选原图共 {kib(totals['candidate_bytes'])} → 写入 {kib(totals['image_bytes'])}"
             + (f"；每张目标 ≤ {target_txt}（尽力而为）" if target_txt else "")
             + (f"，{totals['targets_unmet']} 张已到质量/尺寸下限仍未达到" if totals["targets_unmet"] else "")
             + "。原书自带的图片、文字和样式未改动；明细见 compression-report.json。")


# 8. review list: what went where, from which source, and what is still missing
def cell_md(s):
    return " ".join(str(s).split()).replace("|", "\\|")


# Credit/licence values are copied as recorded (unknown when absent); they are not a rights clearance.
lines = [f"# 配图清单 · {cfg.get('book', '')}", "",
         "许可与署名信息取自来源页面或提供者记录的元数据，未经独立核实，不代表已获得使用许可；“未知”表示未记录。"
         + ("" if S.get("credits", True) else " 本书未内嵌图片来源页（credits: false），分享时请附上本清单。"), "",
         comp_line, "",
         "| 位置 | 段落开头 | 对象 | 图注 | 来源 | 作者 | 许可 | 署名/版权/条款 | 修改 |",
         "|---|---|---|---|---|---|---|---|---|"]
for i, _, e in placed:
    name = images[e["id"]][0][0]
    prov = provenance(e, changes[name]["pick"])
    cap = (e.get("caption") or e["name"]) + (f"（{e['note']}）" if e.get("note") else "")
    lic = " ".join(x for x in (prov.get("license") or "未知", prov.get("license_url", "")) if x)
    extra = "；".join([f"{k}: {prov[k]}" for k in ("credit", "attribution", "copyright", "usage_terms", "copyrighted",
                                                   "attribution_required", "restrictions") if prov.get(k)]
                     + ([f"image: {image_url(prov)}"] if image_url(prov) else []))
    cells = [toc[paras[i]['toc']]['title'], paras[i]['text'][:16] + "…", e["name"], cap,
             f"{prov.get('from', '')} {source_page(prov)}".strip() or "未知", prov.get("author") or "未知", lic,
             extra, credit_changes(name)]
    lines.append("| " + " | ".join(cell_md(c) for c in cells) + " |")
missing = [e for e in cfg["entities"] if not e.get("pick")]
if missing:
    lines += ["", "## 暂无合适图片", ""] + [f"- {e['name']}（{e.get('role', '')}）" for e in missing]
open(os.path.join(work, "配图清单.md"), "w", encoding="utf-8").write("\n".join(lines) + "\n")

print(json.dumps({"format": fmt, "figures": sum(1 for x in inserts if x[3] == "figure"), "entities": len(placed),
                  "galleries": sum(1 for x in inserts if x[3] == "gallery"),
                  "credits": any(x[3] == "credits" for x in inserts), "out": out,
                  "assets": assets_dir if fmt != "epub" else None,
                  "resources_copied": copied, "marker": marker,
                  "compression": {"preset": COMP["preset"], "custom": bool(COMP["overrides"]), **totals,
                                  "report": report_path}}, ensure_ascii=False))
for i, _, e in placed:
    print(f"  {paras[i]['pid']} {e['id']}")
