"""Shared helpers for native (byte-preserving) EPUB / HTML / Markdown jobs.

Everything here works on exact source text: files are decoded only with an encoding that
round-trips to the identical bytes, offsets are Python string offsets into that text, and nothing
is ever re-serialized through a DOM.
"""
import codecs
import hashlib
import os
import posixpath
import re
import secrets
from urllib.parse import quote, unquote

FORMATS = {".epub": "epub", ".html": "html", ".htm": "html", ".xhtml": "html",
           ".md": "markdown", ".markdown": "markdown"}
OUT_EXTS = {"epub": (".epub",), "html": (".html", ".htm", ".xhtml"), "markdown": (".md", ".markdown")}


def detect_format(path):
    return FORMATS.get(os.path.splitext(path)[1].lower())


# --- encodings -------------------------------------------------------------------------------

BOMS = [(codecs.BOM_UTF32_LE, "utf-32-le"), (codecs.BOM_UTF32_BE, "utf-32-be"), (codecs.BOM_UTF8, "utf-8"),
        (codecs.BOM_UTF16_LE, "utf-16-le"), (codecs.BOM_UTF16_BE, "utf-16-be")]
XML_DECL = re.compile(rb"""^<\?xml[^>]*?\bencoding\s*=\s*["']([A-Za-z0-9._:-]+)["']""")
META_CHARSET = re.compile(rb"""<meta\b[^>]*?\bcharset\s*=\s*["']?\s*([A-Za-z0-9._:-]+)""", re.I)


class EncodingError(ValueError):
    pass


def bom_encoding(raw):
    for bom, name in BOMS:
        if raw.startswith(bom):
            return name
    return None


def declared_encoding(raw):
    """Encoding named by an XML declaration or an HTML <meta> in the first 4 KB, if any."""
    m = XML_DECL.match(raw[:200]) or META_CHARSET.search(raw[:4096])
    return m.group(1).decode("ascii") if m else None


def decode(raw, kind="html", override=None):
    """(text, encoding) with text.encode(encoding) == raw guaranteed, else EncodingError.

    kind "html"/"xml" honours BOM, then XML declaration / <meta charset>, then UTF-8;
    kind "text" (Markdown) honours BOM, then UTF-8. A BOM stays in the text as U+FEFF.
    """
    enc = override or bom_encoding(raw) or (declared_encoding(raw) if kind != "text" else None) or "utf-8"
    try:
        name = codecs.lookup(enc).name
    except LookupError:
        raise EncodingError(f"unknown encoding {enc!r}")
    if name in ("utf-16", "utf-32"):  # declared without a byte order mark
        name = bom_encoding(raw) or name
    try:
        text = raw.decode(name)
    except UnicodeDecodeError as ex:
        hint = "" if override else " (pass --encoding NAME if you know the real encoding)"
        raise EncodingError(f"not valid {name}: {ex.reason} at byte {ex.start}{hint}")
    if text.encode(name) != raw:
        raise EncodingError(f"{name} decoding does not round-trip to the original bytes")
    return text, name


def encode(text, enc):
    """Encode inserted text; characters the source encoding lacks become numeric references."""
    return text.encode(enc, errors="xmlcharrefreplace")


def marker_view(raw):
    """A str view of raw bytes in which ASCII markers can be found and that maps back exactly:
    UTF-16/32 (by BOM) are decoded, everything else is viewed byte-for-byte as latin-1."""
    enc = bom_encoding(raw)
    if enc and enc != "utf-8":
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("latin-1"), "latin-1"


def newline_of(text):
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf > lf and crlf >= cr:
        return "\r\n"
    if cr > lf:
        return "\r"
    return "\n"


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- insertion markers -----------------------------------------------------------------------

MARK_RE = re.compile(r"<!--(illus(?:-[0-9a-f]{8})?):start-->")


def markers(tag="illus"):
    return f"<!--{tag}:start-->", f"<!--{tag}:end-->"


def collides(tag, view):
    a, b = markers(tag)
    return a in view or b in view


def choose_marker(views):
    """'illus' unless some original text already contains that marker, then a random variant.
    verify.py only strips markers that never occur in the original, so a collision can never make
    it remove original content."""
    views = list(views)
    tag = "illus"
    while any(collides(tag, v) for v in views):
        tag = "illus-" + secrets.token_hex(4)
    return tag


def block_re(tag, markdown=False):
    a, b = markers(tag)
    # Markdown blocks own exactly one trailing newline (see inject.py); HTML blocks own none.
    return re.compile(re.escape(a) + r".*?" + re.escape(b) + (r"(?:\r\n|\n|\r)?" if markdown else ""), re.S)


# --- book.json ---------------------------------------------------------------------------------

def book_format(book):
    return book.get("format", "epub")  # jobs extracted before multi-format support are EPUB


def member_of(book, f):
    """Container-relative path (inside WORKDIR/src) of a book.json 'file' entry."""
    if "format" not in book:
        f = unquote(f)  # old jobs stored raw (possibly percent-encoded) OPF-relative hrefs
    if book_format(book) != "epub":
        return f
    d = posixpath.dirname(book["opf"])
    return posixpath.normpath(posixpath.join(d, f) if d else f)


# --- paths and URLs --------------------------------------------------------------------------

SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def is_local_ref(ref):
    ref = ref.strip()
    return bool(ref) and not SCHEME.match(ref) and not ref.startswith(("/", "#", "\\")) and "\\" not in ref


def resolve_ref(base_file, ref):
    """Container-relative POSIX path that `ref` (a URL, as written in base_file) points to, or None
    for external/absolute/escaping references. base_file is itself a container-relative path."""
    if not is_local_ref(ref):
        return None
    path = unquote(ref.strip().split("#", 1)[0].split("?", 1)[0])
    if not path or "\x00" in path:
        return None
    joined = posixpath.normpath(posixpath.join(posixpath.dirname(base_file), path))
    if joined == ".." or joined.startswith("../") or joined.startswith("/"):
        return None
    return joined


def url_to(target, from_file):
    """Relative URL from from_file to target (both container-relative POSIX paths)."""
    rel = posixpath.relpath(target, posixpath.dirname(from_file) or ".")
    return quote(rel, safe="/")


def check_member_name(name):
    """Raise ValueError for archive member names that could escape the extraction directory."""
    if (not name or "\x00" in name or "\\" in name or name.startswith("/") or re.match(r"^[A-Za-z]:", name)
            or ".." in name.split("/")):
        raise ValueError(f"unsafe path in archive: {name!r}")


def safe_join(root, rel):
    """root/rel for a POSIX relative path, refusing anything that would land outside root."""
    check_member_name(rel)
    path = os.path.normpath(os.path.join(root, *[p for p in rel.split("/") if p]))
    real_root = os.path.realpath(root)
    if os.path.commonpath([real_root, os.path.realpath(path)]) != real_root:
        raise ValueError(f"path escapes {root}: {rel!r}")
    return path


def inside(path, root):
    a, b = os.path.realpath(path), os.path.realpath(root)
    return a == b or a.startswith(b.rstrip(os.sep) + os.sep)


def safe_name(s):
    """File-name stem for an entity id: no separators, no traversal, never empty."""
    s = re.sub(r"[^\w.-]", "_", str(s)).strip(".")
    return s or "img"
