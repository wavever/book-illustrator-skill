"""Static, offline rendering of untrusted book markup for preview screenshots.

Book HTML is untrusted input. Preview pages are built from copies of it (built books are never
changed) and rendered so that the book's own active content cannot run or load anything:

  * The Content-Security-Policy <meta> placed first in <head> is the boundary for loads: no
    script, frames, forms, plugins or connections; images and fonts from data: URLs only; styles
    inline only. The page never loads a file: or network URL, however a reference is written.
  * CHROME_FLAGS: all traffic sent to a dead proxy, no DNS, no background services; no
    --allow-file-access-from-files.
  * Local resources are copied into the page as data: URLs only after their real path (symlinks
    resolved) is checked to lie inside the allowed roots (embed()). sanitize() also strips active
    elements and attributes. These rewrites serve fidelity and tidiness; references they miss are
    simply not rendered because the CSP blocks them.
"""
import base64
import html as htmlmod
import mimetypes
import os
import re
from urllib.parse import unquote, urlsplit

CSP = ("default-src 'none'; script-src 'none'; object-src 'none'; frame-src 'none'; child-src 'none'; "
       "worker-src 'none'; connect-src 'none'; media-src 'none'; manifest-src 'none'; form-action 'none'; "
       "base-uri 'none'; img-src data:; font-src data:; style-src 'unsafe-inline'")


def csp_meta():
    return f'<meta http-equiv="Content-Security-Policy" content="{CSP}">'


# (--blink-settings=scriptEnabled=false would be a further layer, but headless --screenshot then
# writes no image; script is blocked by the CSP and sanitize() instead.)
CHROME_FLAGS = [
    "--proxy-server=http://127.0.0.1:9", "--proxy-bypass-list=<-loopback>",
    "--host-resolver-rules=MAP * ~NOTFOUND",
    "--disable-background-networking", "--disable-component-update", "--disable-default-apps",
    "--disable-extensions", "--disable-sync", "--no-first-run", "--no-default-browser-check",
    "--no-pings", "--disable-domain-reliability",
]

_PAIRED = "script|noscript|iframe|frame|frameset|object|applet|template|portal|fencedframe"
_DROP_PAIRED = re.compile(rf"<({_PAIRED})\b.*?</\1\s*>", re.S | re.I)
_DROP_TAG = re.compile(rf"</?(?:{_PAIRED}|embed|meta|base|param|link)\b[^>]*>", re.I)
_BAD_ATTR = re.compile(r"""\s(?:on[a-z]+|srcdoc|srcset|imagesrcset|ping|formaction|action)\s*=\s*"""
                       r"""(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.I)
_URL_ATTR = re.compile(r"""(\s(?:src|href|poster|data|background|xlink:href)\s*=\s*)"""
                       r"""(?:(["'])(.*?)\2|([^\s"'>]+))""", re.S | re.I)
_CSS_URL = re.compile(r"""url\(\s*(["']?)([^"')]+)\1\s*\)""", re.I)
_CSS_IMPORT = re.compile(r"""@import\s+(?:url\(\s*)?(["']?)([^"')\s;]+)\1\s*\)?[^;]*;?""", re.I)
BLANK = "about:blank"
MAX_EMBED = 20_000_000
EMBED_TYPES = ("image/", "font/", "application/font", "application/x-font", "application/vnd.ms-fontobject")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/otf", ".otf")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("image/webp", ".webp")
mimetypes.add_type("image/svg+xml", ".svg")


def sanitize(fragment):
    """Markup with script/frame/plugin/meta/base/link elements and active attributes removed."""
    prev = None
    while prev != fragment:  # nested/overlapping constructs
        prev = fragment
        fragment = _DROP_PAIRED.sub("", fragment)
    return _BAD_ATTR.sub("", _DROP_TAG.sub("", fragment))


def inside(path, roots):
    real = os.path.realpath(path)
    return any(real == r or real.startswith(r.rstrip(os.sep) + os.sep) for r in roots)


def local_path(url, base_dir, roots):
    """Real path of a local reference written in a file in base_dir if it is a regular file inside
    roots (relative URL or file: URL), else None."""
    u = htmlmod.unescape(url.strip())
    if not u or "\x00" in u:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        if not u.lower().startswith("file:"):
            return None
        path = unquote(urlsplit(u).path)
    elif u.startswith(("/", "\\", "#")) or "\\" in u:
        return None
    else:
        path = os.path.join(base_dir, unquote(u.split("#")[0].split("?")[0]))
    real = os.path.realpath(path)
    return real if path and os.path.isfile(real) and inside(real, roots) else None


def embed(url, base_dir, roots, cache=None):
    """data: URL with the bytes of an allowed local image/font, the URL itself for data:/#
    fragments, else about:blank."""
    u = htmlmod.unescape(url.strip())
    if u.startswith("#") or u.lower().startswith("data:"):
        return url
    path = local_path(url, base_dir, roots)
    if path is None:
        return BLANK
    if cache is not None and path in cache:
        return cache[path]
    mime = mimetypes.guess_type(path)[0] or ""
    if not mime.startswith(EMBED_TYPES) or os.path.getsize(path) > MAX_EMBED:
        out = BLANK
    else:
        with open(path, "rb") as f:
            out = f"data:{mime};base64,{base64.b64encode(f.read()).decode('ascii')}"
    if cache is not None:
        cache[path] = out
    return out


def absolutize(fragment, base_dir, roots, cache=None):
    """Every URL attribute / CSS url() -> data: URL of an allowed local resource, or blank."""
    cache = {} if cache is None else cache

    def attr(m):
        q = m.group(2) or '"'
        return m.group(1) + q + embed(m.group(3) if m.group(2) else m.group(4), base_dir, roots, cache) + q
    fragment = _URL_ATTR.sub(attr, fragment)
    return _CSS_URL.sub(lambda m: f'url("{embed(m.group(2), base_dir, roots, cache)}")', fragment)


def _css_text(css, base_dir, roots, cache, depth=0):
    """CSS with @imports of allowed local files inlined (others dropped) and url()s embedded."""
    def imp(m):
        path = local_path(m.group(2), base_dir, roots)
        return _read_css(path, roots, cache, depth + 1) if path and depth < 3 else ""
    css = _CSS_IMPORT.sub(imp, css)
    css = _CSS_URL.sub(lambda m: f'url("{embed(m.group(2), base_dir, roots, cache)}")', css)
    return re.sub(r"</(style)", r"<\\/\1", css, flags=re.I)


def _read_css(path, roots, cache, depth=0):
    if not path or not path.lower().endswith(".css") or os.path.getsize(path) > MAX_EMBED:
        return ""
    with open(path, "rb") as f:
        text = f.read().decode("utf-8", errors="replace")
    return _css_text(text, os.path.dirname(path), roots, cache, depth)


_STYLE = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>)", re.S | re.I)
_HREF = re.compile(r"""\shref\s*=\s*(?:(["'])(.*?)\1|([^\s"'>]+))""", re.S | re.I)


def inline_css(head_items, base_dir, roots, cache=None):
    """<link rel=stylesheet>/<style> items from a book's <head> as self-contained <style> blocks."""
    cache = {} if cache is None else cache
    out = []
    for item in head_items:
        m = _STYLE.match(item)
        if m:
            out.append(m.group(1) + _css_text(m.group(2), base_dir, roots, cache) + m.group(3))
            continue
        h = _HREF.search(item)
        css = _read_css(local_path(h.group(2) or h.group(3), base_dir, roots), roots, cache) if h else ""
        if css:
            out.append(f"<style>{css}</style>")
    return out
