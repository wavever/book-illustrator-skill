"""Unpack a book and index its paragraphs, so entities can be found and figures placed.

Usage: python3 extract.py INPUT WORKDIR [--encoding NAME]

INPUT is an EPUB (2 or 3), an HTML/XHTML file (.html .htm .xhtml) or a Markdown file
(.md .markdown); other formats are converted to EPUB first (prepare.py). --encoding overrides
detection for HTML/Markdown sources that are neither UTF-8/UTF-16 nor declare their charset.

Writes
  WORKDIR/src/            the source, never edited (EPUB unpacked; HTML/Markdown copied together
                          with the local images/stylesheets they reference)
  WORKDIR/book.json       {"format", "opf", "spine", "toc": [...], "paras": [...], "marker", ...}
  WORKDIR/text/NNN.txt    one file per TOC entry: "pid<TAB>text" lines, for reading

Each paragraph gets a stable id (p00001...) with its file, character span and a safe insertion
offset ("ins"), so figures can later be inserted *between* elements without touching any
original byte. Nested containers (<div><p>..</p><p>..</p></div>, sections, lists, tables) are
indexed down to their innermost blocks.
"""
import json
import os
import posixpath
import shutil
import sys
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_html  # noqa: E402
import native_md  # noqa: E402
from native_util import (EncodingError, check_member_name, choose_marker, decode, detect_format,  # noqa: E402
                         inside, marker_view, newline_of, resolve_ref, safe_join, sha256)

XHTML_TYPES = {"application/xhtml+xml", "text/html"}
MAX_UNPACKED = 4 << 30


def fail(msg):
    sys.exit(f"extract.py: {msg}")


# --- EPUB --------------------------------------------------------------------------------------

def unpack(epub, src):
    try:
        z = zipfile.ZipFile(epub)
    except zipfile.BadZipFile:
        fail(f"{epub} is not a ZIP/EPUB archive")
    with z:
        infos = [i for i in z.infolist() if not i.filename.endswith("/")]
        names = [i.filename for i in infos]
        for n in names:
            try:
                check_member_name(n)
            except ValueError as ex:
                fail(str(ex))
        dup = {n for n in names if names.count(n) > 1}
        if dup:
            fail(f"duplicate archive members: {sorted(dup)[:5]}")
        if sum(i.file_size for i in infos) > MAX_UNPACKED:
            fail("archive expands to more than 4 GB — refusing")
        for i in infos:
            path = safe_join(src, i.filename)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with z.open(i) as r, open(path, "wb") as w:
                shutil.copyfileobj(r, w)
        return [[i.filename, i.compress_type] for i in infos]


def read_member(src, member):
    with open(safe_join(src, member), "rb") as f:
        return f.read()


def extract_epub(epub, src):
    members = unpack(epub, src)
    names = {m for m, _ in members}
    if "META-INF/encryption.xml" in names:
        print("warning: META-INF/encryption.xml present — the book may be DRM-protected")
    if "META-INF/container.xml" not in names:
        fail("not an EPUB: META-INF/container.xml is missing")
    try:
        container = ET.fromstring(read_member(src, "META-INF/container.xml"))
    except ET.ParseError as ex:
        fail(f"META-INF/container.xml is not well-formed: {ex}")
    rootfiles = container.findall(".//{*}rootfile")
    rootfile = next((r for r in rootfiles if r.get("media-type") == "application/oebps-package+xml"),
                    rootfiles[0] if rootfiles else None)
    if rootfile is None or not rootfile.get("full-path"):
        fail("container.xml names no package document")
    opf_member = rootfile.get("full-path")
    if opf_member not in names and unquote(opf_member) in names:
        opf_member = unquote(opf_member)
    if opf_member not in names:
        fail(f"package document {opf_member} is missing")
    try:
        opf = ET.fromstring(read_member(src, opf_member))
    except ET.ParseError as ex:
        fail(f"{opf_member} is not well-formed: {ex}")
    opf_dir = posixpath.dirname(opf_member)

    def opf_rel(member):
        return posixpath.relpath(member, opf_dir) if opf_dir else member

    manifest = {}
    man_el = opf.find("{*}manifest")
    for it in (man_el if man_el is not None else []):
        member = resolve_ref(opf_member, it.get("href") or "")
        if member is not None:
            manifest[it.get("id")] = {"member": member, "type": (it.get("media-type") or "").strip(),
                                      "props": (it.get("properties") or "").split(), "href": it.get("href")}
    spine_el = opf.find("{*}spine")
    spine_members = []
    for ref in (spine_el if spine_el is not None else []):
        it = manifest.get(ref.get("idref"))
        if it is None:
            print(f"warning: spine itemref {ref.get('idref')!r} has no manifest item, skipped")
        elif it["type"] in XHTML_TYPES and it["member"] not in spine_members:
            if it["member"] in names:
                spine_members.append(it["member"])
            else:
                print(f"warning: spine item {it['member']} is missing from the archive, skipped")
    if not spine_members:
        fail("the spine lists no XHTML content documents")

    texts, encodings, skipped = {}, {}, []
    for m in spine_members:
        try:
            texts[m], enc = decode(read_member(src, m), "xml")
        except EncodingError as ex:
            print(f"warning: {m}: {ex}; not indexed (no figures will be placed there)")
            skipped.append(opf_rel(m))
            continue
        if enc != "utf-8":
            encodings[opf_rel(m)] = enc
    views = [marker_view(read_member(src, m))[0] for m in names] + list(texts.values())
    marker = choose_marker(views)

    paras, titles = [], {}
    epub2 = (opf.get("version") or "").strip().startswith("2")  # content is validated as XHTML 1.1
    for m in spine_members:
        if m not in texts:
            continue
        ps, titles[m] = native_html.scan(texts[m], xhtml11=epub2)
        for p in ps:
            paras.append({"file": opf_rel(m), **p})

    # table of contents: EPUB3 nav, else EPUB2 NCX; hrefs are relative to the nav/NCX document
    toc, toc_source = [], None
    nav = next((i for i in manifest.values() if "nav" in i["props"]), None)
    ncx_id = spine_el.get("toc") if spine_el is not None else None
    ncx = manifest.get(ncx_id) or next((i for i in manifest.values() if i["type"] == "application/x-dtbncx+xml"),
                                       None)
    for kind, item in (("nav", nav), ("ncx", ncx)):
        if item is None or toc or item["member"] not in names:
            continue
        try:
            text, _ = decode(read_member(src, item["member"]), "xml")
        except EncodingError as ex:
            print(f"warning: {item['member']}: {ex}")
            continue
        entries = native_html.parse_nav(text) if kind == "nav" else native_html.parse_ncx(text)
        for title, href, depth in entries:
            member = resolve_ref(item["member"], href)
            if member is None or member not in spine_members:
                continue
            frag = unquote(href.partition("#")[2])
            toc.append({"title": title, "href": href, "depth": depth, "file": opf_rel(member), "frag": frag})
        toc_source = kind if toc else None
    if not toc:
        print("warning: no usable table of contents; using headings / spine files")
        toc_source = "fallback"
    spine = [opf_rel(m) for m in spine_members]
    for t in toc:
        s = texts.get(posixpath.normpath(posixpath.join(opf_dir, t["file"])), "")
        off = native_html.find_id(s, t.pop("frag")) if "#" in t["href"] else 0
        t["pos"] = [spine.index(t["file"]), off or 0]
    title_el = opf.find(".//{*}metadata/{*}title")
    book_title = (title_el.text or "").strip() if title_el is not None else ""
    file_titles = {opf_rel(m): t for m, t in titles.items()}
    book = {"format": "epub", "opf": opf_member, "version": opf.get("version"), "spine": spine,
            "members": members, "marker": marker, "toc_source": toc_source, "title": book_title,
            "source": {"name": os.path.basename(epub), "path": os.path.abspath(epub), "sha256": sha256(epub)},
            **({"encodings": encodings} if encodings else {}), **({"skipped_files": skipped} if skipped else {})}
    return book, toc, paras, file_titles


# --- HTML / Markdown -----------------------------------------------------------------------------

def extract_single(path, src, fmt, encoding):
    raw = open(path, "rb").read()
    try:
        text, enc = decode(raw, "html" if fmt == "html" else "text", encoding)
    except EncodingError as ex:
        fail(f"{path}: {ex}")
    name = os.path.basename(path)
    if fmt == "html":
        paras, title = native_html.scan(text)
    else:
        paras, title = native_md.parse(text)
    base = os.path.dirname(os.path.abspath(path))

    def read(rel):
        try:
            p = safe_join(base, rel)
        except ValueError:
            return None
        return open(p, "rb").read() if os.path.isfile(p) else None

    if fmt == "html":
        resources, skipped = native_html.collect_resources(name, text, read)
    else:
        refs = [r for p in paras for r in p.get("imgs", [])]
        resources, skipped = native_html.collect_resources(name, "", read)
        for r in refs:
            rel = resolve_ref(name, r)
            if rel is not None and read(rel) is not None:
                if rel not in resources:
                    resources.append(rel)
            elif native_html.is_local_ref(r):
                skipped.append(r)
    os.makedirs(src, exist_ok=True)
    with open(os.path.join(src, name), "wb") as f:
        f.write(raw)
    for rel in resources:
        dst = safe_join(src, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst, "wb") as f:
            f.write(read(rel))
    if skipped:
        print(f"warning: {len(skipped)} local reference(s) not found or outside the source directory "
              f"(not copied): {sorted(set(skipped))[:5]}")
    for p in paras:
        p["file"] = name
    marker = choose_marker([marker_view(raw)[0], text])
    book = {"format": fmt, "opf": "", "spine": [name], "marker": marker, "title": title or "",
            "source": {"name": name, "path": os.path.abspath(path), "sha256": sha256(path), "encoding": enc,
                       "newline": newline_of(text), "bom": text.startswith("\ufeff"),
                       "resources": resources, "skipped_resources": sorted(set(skipped))}}
    toc = []
    heads = [p for p in paras if p["tag"] in native_html.HEADINGS and (fmt == "html" or p.get("top"))]
    if heads:
        top = min(int(p["tag"][1]) for p in heads)
        toc = [{"title": p["text"], "href": name, "depth": int(p["tag"][1]) - top, "file": name,
                "pos": [0, p["start"]]} for p in heads]
    for p in paras:
        p.pop("top", None)
    return book, toc, paras, {name: title}


# --- shared ------------------------------------------------------------------------------------

def fallback_toc(spine, paras, titles, book_title):
    heads = [p for p in paras if p["tag"] in ("h1", "h2", "h3")]
    if heads:
        top = min(int(p["tag"][1]) for p in heads)
        return [{"title": p["text"], "href": p["file"], "depth": int(p["tag"][1]) - top, "file": p["file"],
                 "pos": [spine.index(p["file"]), p["start"]]} for p in heads]
    files = [f for f in spine if any(p["file"] == f for p in paras)] or spine[:1]
    return [{"title": titles.get(f) or (book_title if len(files) == 1 else "") or
             os.path.splitext(posixpath.basename(f))[0], "href": f, "depth": 0, "file": f,
             "pos": [spine.index(f), 0]} for f in files]


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    encoding = argv[argv.index("--encoding") + 1] if "--encoding" in argv else None
    if encoding:
        args.remove(encoding)
    if len(args) != 2:
        sys.exit(__doc__)
    inp, work = args
    fmt = detect_format(inp)
    if fmt is None:
        fail(f"unsupported input {inp!r}: native formats are .epub .html .htm .xhtml .md .markdown "
             "(convert others with prepare.py)")
    if not os.path.isfile(inp):
        fail(f"no such file: {inp}")
    src = os.path.join(work, "src")
    # everything extract.py (and later inject.py) deletes or overwrites inside WORKDIR
    for d in (src, os.path.join(work, "text"), os.path.join(work, "build"), os.path.join(work, "book.json")):
        if inside(inp, d):
            fail(f"the input lies inside {d}, which is replaced by extract.py/inject.py — copy it elsewhere first")
    shutil.rmtree(src, ignore_errors=True)
    os.makedirs(src, exist_ok=True)
    if fmt == "epub":
        book, toc, paras, titles = extract_epub(inp, src)
    else:
        book, toc, paras, titles = extract_single(inp, src, fmt, encoding)
    book["source_path"] = os.path.abspath(inp)  # stable contract: verify.py baseline for wrappers
    spine = book["spine"]
    if not toc:
        toc = fallback_toc(spine, paras, {**titles}, book.get("title", ""))

    for n, p in enumerate(paras, 1):
        p["pid"] = f"p{n:05d}"
    paras = [{k: p[k] for k in ("pid", "file", "start", "end", "ins", "tag", "text", "imgs") if k in p}
             for p in paras]
    # assign each paragraph to the TOC entry it falls under
    order = sorted(range(len(toc)), key=lambda i: tuple(toc[i]["pos"]))
    for p in paras:
        key = (spine.index(p["file"]), p["start"])
        p["toc"] = max((i for i in order if tuple(toc[i]["pos"]) <= key), key=lambda i: tuple(toc[i]["pos"]),
                       default=0)
    text_dir = os.path.join(work, "text")
    shutil.rmtree(text_dir, ignore_errors=True)
    os.makedirs(text_dir)
    for i, t in enumerate(toc):
        lines = [f"{p['pid']}\t{p['text']}" + (f"  [已有图片 {','.join(p['imgs'])}]" if p.get("imgs") else "")
                 for p in paras if p["toc"] == i]
        with open(os.path.join(text_dir, f"{i:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(f"# {t['title']}\n" + "\n".join(lines) + "\n")
        t["paras"] = len(lines)
    book.update(toc=toc, paras=paras)
    with open(os.path.join(work, "book.json"), "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=1)
    for i, t in enumerate(toc):
        print(f"{i:03d} {'  ' * t['depth']}{t['title']}  ({t['paras']} paras)")
    print(f"format: {book['format']}; {len(paras)} paragraphs in {len(spine)} file(s)")


if __name__ == "__main__":
    main(sys.argv[1:])
