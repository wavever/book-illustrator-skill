"""Prove the illustrated book keeps the original text: strip every <!--illus:start-->…<!--illus:end-->
block that inject.py added and require the rest to be byte-identical to the original. Also checks
that every inserted image exists. (Standards validation for EPUB: epubcheck_diff.py.)

Usage: python3 verify.py ORIGINAL OUTPUT

Native formats only — both files must be .epub, or HTML (.html/.htm/.xhtml), or Markdown
(.md/.markdown). A converted output (export.py to another format) cannot be byte-verified.
  EPUB      every original member must be present and identical outside our blocks; the only new
            members allowed are the images our manifest block declares; mimetype stays first/stored.
  HTML/MD   the file must be identical outside our blocks (encoding, BOM and newlines included);
            inserted images must exist beside OUTPUT, as must every local resource that exists
            beside ORIGINAL.
Only marker variants that never occur in the original are treated as ours, so original text that
happens to look like a marker can never be stripped away.
Exit status: 0 OK, 1 verification failed, 2 usage/unsupported input.
"""
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_html  # noqa: E402
import native_md  # noqa: E402
from native_util import (EncodingError, block_re, collides, decode, detect_format, marker_view,  # noqa: E402
                         resolve_ref)

ANY_MARK = re.compile(r"<!--(illus(?:-[0-9a-f]{8})?):(?:start|end)-->")
SRC_ATTR = re.compile(r"""\s(?:src|href)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
MD_DEST = re.compile(r"!\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?")
LEGACY_ITEM = re.compile(rb'<item href="(illus/[^"]+)" id="illus-[^"]+" media-type="image/jpeg"/>\n')
TEXTUAL = (".xhtml", ".html", ".htm", ".opf", ".ncx", ".xml", ".svg", ".css", ".md", ".markdown")
problems = []


def bad(*msg):
    problems.append(" ".join(msg))
    print(*msg)


def strip_ours(orig_views, new_raw, markdown=False):
    """(new bytes with our blocks removed, [block texts]) — viewed in the new file's own encoding."""
    view, enc = marker_view(new_raw)
    tags = set(ANY_MARK.findall(view))
    ours = {t for t in tags if not any(collides(t, v) for v in orig_views)}
    for t in sorted(tags - ours):  # not ours: left in place, so they must match the original byte for byte
        print(f"note: <!--{t}:…--> also occurs in the original; treated as original text")
    blocks = []
    for t in ours:
        rx = block_re(t, markdown)
        blocks += [m.group(0) for m in rx.finditer(view)]
        view = rx.sub("", view)
        if collides(t, view):
            bad("UNBALANCED MARKER", t)
    return view.encode(enc), blocks


def refs_in(block):
    return [next(g for g in m.groups() if g is not None) for m in SRC_ATTR.finditer(block)] + \
        MD_DEST.findall(block)


def verify_epub(orig_path, new_path):
    orig, new = zipfile.ZipFile(orig_path), zipfile.ZipFile(new_path)
    o_names = [n for n in orig.namelist() if not n.endswith("/")]
    n_infos = [i for i in new.infolist() if not i.filename.endswith("/")]
    n_names = [i.filename for i in n_infos]
    if len(set(n_names)) != len(n_names):
        bad("DUPLICATE MEMBERS in output")
    n_set, o_set = set(n_names), set(o_names)
    if "mimetype" in o_names:
        first = new.infolist()[0]
        if first.filename != "mimetype" or first.compress_type != zipfile.ZIP_STORED:
            bad("MIMETYPE", "must be the first member and stored uncompressed")
    views = [marker_view(orig.read(n))[0] for n in o_names]
    inserted, additions, content_refs = 0, set(), []
    for name in o_names:
        a = orig.read(name)
        if name not in n_set:
            bad("MISSING", name)
            continue
        b = new.read(name)
        if a == b:
            continue
        if not name.lower().endswith(TEXTUAL):
            bad("CHANGED", name)
            continue
        stripped, blocks = strip_ours(views, b)
        if name.lower().endswith(".opf"):
            for blk in blocks:
                for r in re.findall(r"""\bhref\s*=\s*["']([^"']+)["']""", blk):
                    additions.add(resolve_ref(name, r.replace("&amp;", "&")))
            if not blocks and stripped != a:  # output of the pre-marker inject.py
                def legacy(m):  # only lines declaring images the original does not have
                    target = resolve_ref(name, m.group(1).decode())
                    if target is None or target in o_set:
                        return m.group(0)
                    additions.add(target)
                    return b""
                stripped = LEGACY_ITEM.sub(legacy, stripped)
        else:
            inserted += len(blocks)
            content_refs += [(name, r) for blk in blocks for r in refs_in(blk)]
        if stripped != a:
            bad("CHANGED", name)
    for base, r in content_refs:
        target = resolve_ref(base, r.replace("&amp;", "&"))
        if target is None or target not in n_set:
            bad("MISSING IMAGE", f"{r} (from {base})")
        elif target not in additions and target not in o_names:
            bad("UNDECLARED IMAGE", f"{target} is not in the manifest")
    for extra in sorted(n_set - set(o_names)):
        if extra not in additions:
            bad("UNEXPECTED MEMBER", extra)
    for a in sorted(x for x in additions if x is None or x not in n_set):
        bad("MANIFEST ITEM WITHOUT FILE", str(a))
    return inserted


def verify_single(orig_path, new_path, fmt):
    a, b = open(orig_path, "rb").read(), open(new_path, "rb").read()
    stripped, blocks = strip_ours([marker_view(a)[0]], b, markdown=fmt == "markdown")
    if stripped != a:
        bad("CHANGED", os.path.basename(new_path), "differs from the original outside the inserted blocks")
    out_dir, out_name = os.path.dirname(os.path.abspath(new_path)), os.path.basename(new_path)
    for blk in blocks:
        for r in refs_in(blk):
            target = resolve_ref(out_name, r.replace("&amp;", "&"))
            if target is None or not os.path.isfile(os.path.join(out_dir, *target.split("/"))):
                bad("MISSING IMAGE", r)
    # the source's own local resources must still resolve beside the output
    try:
        text, _ = decode(a, "html" if fmt == "html" else "text")
    except EncodingError:
        text = a.decode("latin-1")
    if fmt == "html":
        refs = native_html.html_refs(text)
    else:
        refs = [r for p in native_md.parse(text)[0] for r in p.get("imgs", [])]
    orig_dir, orig_name = os.path.dirname(os.path.abspath(orig_path)), os.path.basename(orig_path)
    for r in refs:
        t_out, t_orig = resolve_ref(out_name, r), resolve_ref(orig_name, r)
        if t_out is None or t_orig is None:
            continue
        if os.path.isfile(os.path.join(orig_dir, *t_orig.split("/"))) and \
                not os.path.isfile(os.path.join(out_dir, *t_out.split("/"))):
            bad("MISSING RESOURCE", r)
    return len(blocks)


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    orig, new = argv
    fo, fn = detect_format(orig), detect_format(new)
    if fo is None or fn is None or fo != fn:
        print(f"verify.py checks native outputs only (same format as the original): got {orig!r} -> {new!r}. "
              "A converted export cannot be byte-verified; verify its native intermediate instead.")
        return 2
    for p in (orig, new):
        if not os.path.isfile(p):
            print(f"no such file: {p}")
            return 2
    try:
        n = verify_epub(orig, new) if fo == "epub" else verify_single(orig, new, fo)
    except zipfile.BadZipFile as ex:
        print(f"FAILED: not a valid EPUB/ZIP: {ex}")
        return 1
    if problems:
        print("FAILED")
        return 1
    print(f"OK: {n} inserted blocks; original {'files' if fo == 'epub' else 'source'} byte-identical outside them")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
