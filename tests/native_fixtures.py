"""Synthetic books for the native (EPUB/HTML/Markdown) tests. No network, no real books."""
import json
import os
import subprocess
import sys
import zipfile

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "skills", "book-illustrator", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)


def run(script, *args, env=None):
    """Run a script CLI; returns CompletedProcess (text)."""
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, script), *map(str, args)],
                          capture_output=True, text=True, env=env, timeout=120)


def write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": ""})) as f:
        f.write(data)
    return path


def readb(path):
    with open(path, "rb") as f:
        return f.read()


def image(path, color=(200, 30, 30), size=(64, 48)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def make_epub(path, files, mimetype=True):
    """files: {member: str|bytes} in order; mimetype written first, stored."""
    with zipfile.ZipFile(path, "w") as z:
        if mimetype:
            z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        for name, data in files.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
    return path


CONTAINER = ('<?xml version="1.0" encoding="UTF-8"?>\n<container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
             '<rootfile full-path="{opf}" media-type="application/oebps-package+xml"/></rootfiles></container>')

XHTML = ('<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml" '
         'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>{title}</title>'
         '<link rel="stylesheet" type="text/css" href="{css}"/></head>\n<body>\n{body}\n</body>\n</html>\n')

PNG = None


def png_bytes():
    global PNG
    if PNG is None:
        import io
        b = io.BytesIO()
        Image.new("RGB", (8, 8), (0, 90, 200)).save(b, "PNG")
        PNG = b.getvalue()
    return PNG


def epub3(path, nav=True, extra_body=""):
    """EPUB3: OPF in OPS/, nav in OPS/nav/, content in OPS/text/ and OPS/text/sub/, a percent-encoded
    file name, nested containers, a list, single-quoted attributes and an existing image."""
    ch1 = XHTML.format(title="Ch1", css="../styles/book.css", body=(
        "<section epub:type='chapter'>\n<h1 id='c1'>第一章 Start</h1>\n"
        "<div class='wrap'><p>Intro mentions Beacon here.</p><p class='x'>Second para &amp; more.</p></div>\n"
        "<ul><li>Gamma is only in a list.</li><li>other item</li></ul>\n"
        "<p><img src='../images/old.png' alt='old'/></p>\n"
        "<table><tr><td><p>Delta inside a table cell.</p></td></tr></table>\n"
        f"{extra_body}</section>"))
    ch2 = XHTML.format(title="Ch2", css="../../styles/book.css", body=(
        "<div><section>\n<h2 id=\"sec2\">第二章 Next</h2>\n"
        "<blockquote><p>Quote with Lumen here.</p></blockquote>\n<p>Lumen again, plain.</p>\n</section></div>"))
    navdoc = XHTML.format(title="Nav", css="../styles/book.css", body=(
        "<nav epub:type='toc' id='toc'><h1>Contents</h1><ol>"
        "<li><a href='../text/ch%201.xhtml#c1'>第一章 Start</a></li>"
        "<li><a href='../text/sub/ch2.xhtml#sec2'>第二章 Next</a></li></ol></nav>"))
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
           'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="uid">urn:uuid:12345678-1234-1234-1234-123456789abc</dc:identifier>'
           '<dc:title>Test Book 3</dc:title><dc:language>en</dc:language>'
           '<meta property="dcterms:modified">2026-01-01T00:00:00Z</meta></metadata>\n<manifest>\n'
           + ('<item id="nav" href="nav/toc.xhtml" media-type="application/xhtml+xml" properties="nav"/>\n'
              if nav else '')
           + '<item id="c1" href="text/ch%201.xhtml" media-type="application/xhtml+xml"/>\n'
             '<item id="c2" href="text/sub/ch2.xhtml" media-type="application/xhtml+xml"/>\n'
             '<item id="css" href="styles/book.css" media-type="text/css"/>\n'
             '<item id="old" href="images/old.png" media-type="image/png"/>\n'
             '</manifest>\n<spine><itemref idref="c1"/><itemref idref="c2"/></spine>\n</package>\n')
    files = {"META-INF/container.xml": CONTAINER.format(opf="OPS/package.opf"), "OPS/package.opf": opf,
             "OPS/text/ch 1.xhtml": ch1, "OPS/text/sub/ch2.xhtml": ch2,
             "OPS/styles/book.css": "p { text-indent: 2em; }\n", "OPS/images/old.png": png_bytes()}
    if nav:
        files["OPS/nav/toc.xhtml"] = navdoc
    return make_epub(path, files)


def epub2(path, crlf=True):
    """EPUB2 with CRLF content, OPF in OEBPS/, NCX in OEBPS/toc/ (hrefs relative to the NCX)."""
    nl = "\r\n" if crlf else "\n"
    x = ('<?xml version="1.0" encoding="utf-8"?>{nl}<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" '
         '"http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">{nl}<html xmlns="http://www.w3.org/1999/xhtml">'
         '<head><title>{t}</title></head>{nl}<body>{nl}{b}{nl}</body>{nl}</html>{nl}')
    ch1 = x.format(nl=nl, t="One", b=nl.join([
        '<div class="chapter">', '<h1 id="c1">第一章 起点</h1>', '<div>',
        '<p>丁磊创办了网易，这里第一次提到 Beacon。</p>', '<p>Second paragraph.</p>', '</div>', '</div>']))
    ch2 = x.format(nl=nl, t="Two", b=nl.join([
        '<h1 id="c2">第二章 发展</h1>', "<div><div><p class='a'>Lumen appears in chapter two.</p></div></div>"]))
    ncx = ('<?xml version="1.0" encoding="UTF-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
           'version="2005-1"><head><meta name="dtb:uid" content="uid2"/></head><docTitle><text>T</text></docTitle>'
           '<navMap><navPoint id="n1" playOrder="1"><navLabel><text>第一章 起点</text></navLabel>'
           '<content src="../Text/ch1.xhtml#c1"/>'
           '<navPoint id="n1a" playOrder="2"><navLabel><text>Sub</text></navLabel>'
           '<content src="../Text/ch1.xhtml"/></navPoint></navPoint>'
           '<navPoint id="n2" playOrder="3"><navLabel><text>第二章 发展</text></navLabel>'
           '<content src="../Text/ch2.xhtml#c2"/></navPoint></navMap></ncx>\n')
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
           'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
           'xmlns:opf="http://www.idpf.org/2007/opf"><dc:identifier id="uid">uid2</dc:identifier>'
           '<dc:title>Test Book 2</dc:title><dc:language>zh</dc:language></metadata>\n<manifest>\n'
           '<item id="ncx" href="toc/toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
           '<item id="c1" href="Text/ch1.xhtml" media-type="application/xhtml+xml"/>\n'
           '<item id="c2" href="Text/ch2.xhtml" media-type="application/xhtml+xml"/>\n'
           '</manifest>\n<spine toc="ncx"><itemref idref="c1"/><itemref idref="c2"/></spine>\n</package>\n')
    return make_epub(path, {"META-INF/container.xml": CONTAINER.format(opf="OEBPS/content.opf"),
                            "OEBPS/content.opf": opf, "OEBPS/toc/toc.ncx": ncx,
                            "OEBPS/Text/ch1.xhtml": ch1, "OEBPS/Text/ch2.xhtml": ch2})


def epub2_section(path):
    """EPUB2 as calibre writes it from AZW3: HTML5 <section> (invalid in XHTML 1.1) around paragraphs."""
    ch = ("<?xml version='1.0' encoding='utf-8'?>\n<html xmlns=\"http://www.w3.org/1999/xhtml\">\n<head>"
          "<title>Chapter 1</title></head>\n<body class=\"calibre\"><h1 id=\"ch1\">第一章 Alice</h1>\n"
          "<div class=\"outer\"><section><p class=\"c\">Alice built a small observatory.</p>\n"
          "<p class=\"c\">More original words.</p></section></div>\n<p class=\"c\">Atlas maps things.</p>\n"
          "<section><p>Section-only Beacon.</p></section>\n</body>\n</html>\n")
    ncx = ('<?xml version="1.0" encoding="UTF-8"?>\n<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" '
           'version="2005-1"><head><meta name="dtb:uid" content="u"/></head><docTitle><text>T</text></docTitle>'
           '<navMap><navPoint id="n1" playOrder="1"><navLabel><text>第一章 Alice</text></navLabel>'
           '<content src="text/part0000.html#ch1"/></navPoint></navMap></ncx>\n')
    opf = ('<?xml version="1.0" encoding="UTF-8"?>\n<package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
           'unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:identifier id="uid">u</dc:identifier><dc:title>S</dc:title><dc:language>en</dc:language></metadata>\n'
           '<manifest>\n<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>\n'
           '<item id="p0" href="text/part0000.html" media-type="application/xhtml+xml"/>\n</manifest>\n'
           '<spine toc="ncx"><itemref idref="p0"/></spine>\n</package>\n')
    return make_epub(path, {"META-INF/container.xml": CONTAINER.format(opf="content.opf"), "content.opf": opf,
                            "toc.ncx": ncx, "text/part0000.html": ch})


def entities(work, ents, **settings):
    """Write entities.json and one synthetic candidate image per entity."""
    for k, e in enumerate(ents):
        image(os.path.join(work, "candidates", e["id"], "a.png"), color=(40 * k % 255, 120, 200))
        e.setdefault("pick", "a.png")
        e.setdefault("match", [e["name"]])
    cfg = {"book": "t", "settings": {"inline": True, "gallery": True, "chapter_re": "^第.+章", **settings},
           "entities": ents}
    write(os.path.join(work, "entities.json"), json.dumps(cfg, ensure_ascii=False))
    return cfg


def book(work):
    with open(os.path.join(work, "book.json"), encoding="utf-8") as f:
        return json.load(f)
