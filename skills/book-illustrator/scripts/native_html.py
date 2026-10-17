"""Offset-preserving HTML/XHTML block scanner (no DOM, no re-serialization).

scan(text) walks the tags of a document, tracks nesting (including HTML's implicit </p>, </li>,
</dt>/</dd>), and reports every *leaf block* — a p/h1-6/li/div/blockquote/... element that has no
block-level element inside it — with:
  start, end   span of the element in the original text
  ins          a safe offset to insert a new <div> after it (never directly inside ul/ol/dl/table,
               never inside p/headings/figure/pre/a/svg..., see insertion())
  tag, text, imgs
So <div><p>one</p><p>two</p></div> yields two paragraphs, <ul><li>x</li></ul> inserts inside the
<li>, and a paragraph inside a table inserts after the whole table.
"""
import html
import re

from native_util import resolve_ref, is_local_ref

TOKEN = re.compile(
    r"<!--.*?(?:-->|\Z)"
    r"|<!\[CDATA\[.*?(?:\]\]>|\Z)"
    r"|<[!?][^>]*>"
    r"|</([A-Za-z][^\s/>]*)[^>]*>"
    r"|<([A-Za-z][^\s/>]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.S)

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track",
        "wbr", "keygen", "command", "basefont", "frame", "isindex"}
RAW = {"script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext"}
HEADINGS = {f"h{i}" for i in range(1, 7)}
# elements whose text is indexed when they contain no other block element
LEAF = {"p", "li", "blockquote", "div", "dd", "dt", "pre", "figcaption", "td", "th", "caption", "address",
        "section", "article", "aside", "header", "footer", "main", "summary", "center", "body"} | HEADINGS
# elements that make their ancestors containers rather than leaves
BLOCKY = LEAF - {"body"} | {"ul", "ol", "dl", "menu", "dir", "table", "tbody", "thead", "tfoot", "tr", "figure",
                            "hgroup", "nav", "hr", "form", "fieldset", "details", "dialog", "legend", "noscript"}
# never insert inside these: the insertion moves after the outermost one
BARRIER = {"table", "figure", "svg", "math", "pre", "a", "button", "label", "select", "ruby", "hgroup", "picture",
           "video", "audio", "object", "iframe", "p", "map", "canvas", "template"} | HEADINGS
# parents that accept a <div> child (flow content)
FLOW_PARENT = {"#root", "body", "div", "section", "article", "aside", "main", "header", "footer", "nav",
               "blockquote", "li", "dd", "td", "th", "details", "center", "form", "fieldset", "dialog", "noscript"}
# leaves that may receive the insertion just before their own end tag
INSIDE_OK = {"li", "dd", "td", "th", "body", "blockquote", "div", "section", "article", "aside", "main", "header",
             "footer", "details", "center", "form", "fieldset", "caption"}
PHRASING = {"span", "em", "strong", "b", "i", "u", "s", "small", "big", "sub", "sup", "font", "q", "cite", "code",
            "kbd", "samp", "var", "abbr", "acronym", "dfn", "mark", "time", "bdi", "bdo", "tt", "strike", "del",
            "ins", "nobr", "rb", "rt", "rp", "rtc", "data", "output", "meter", "progress", "dt", "summary",
            "legend", "address", "figcaption", "option", "optgroup", "textarea"}
KNOWN = LEAF | BLOCKY | BARRIER | FLOW_PARENT | PHRASING | VOID | RAW | {"html", "head", "colgroup", "tr", "tbody",
                                                                        "thead", "tfoot", "ul", "ol", "dl", "menu"}
SKIP_CONTEXT = {"head", "nav", "svg", "math", "template", "script", "style"}
CLOSES_P = {"address", "article", "aside", "blockquote", "center", "details", "dialog", "dir", "div", "dl",
            "fieldset", "figcaption", "figure", "footer", "form", "header", "hgroup", "hr", "main", "menu", "nav",
            "ol", "p", "pre", "section", "table", "ul", "li", "dd", "dt"} | HEADINGS
P_SCOPE = {"td", "th", "table", "html", "button", "caption", "object", "template", "#root"}

ATTR = r"""\s(?:[\w-]+:)?%s\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))"""
IMG_SRC = re.compile(r"<(?:\w+:)?img\b[^>]*?" + ATTR % "src", re.I | re.S)
SVG_IMAGE = re.compile(r"<(?:\w+:)?image\b[^>]*?" + ATTR % "href", re.I | re.S)
TAGS = re.compile(r"<[^>]*>", re.S)
STRIP = re.compile(r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<(script|style|rt|rp)\b[^>]*>.*?</\1\s*>", re.S | re.I)
BR = re.compile(r"<br\b[^>]*>", re.I)


def local(name):
    return name.split(":")[-1].lower()


def attr(attrs, name):
    m = re.search(ATTR % re.escape(name), " " + attrs, re.I | re.S)
    return html.unescape(next(g for g in m.groups() if g is not None)) if m else None


def html_text(fragment):
    s = STRIP.sub(" ", fragment)
    s = BR.sub(" ", s)
    s = html.unescape(TAGS.sub("", s))
    return re.sub(r"\s+", " ", s).strip()


def images(fragment):
    frag = re.sub(r"<!--.*?-->", "", fragment, flags=re.S)
    out = []
    for m in list(IMG_SRC.finditer(frag)) + list(SVG_IMAGE.finditer(frag)):
        out.append(html.unescape(next(g for g in m.groups() if g is not None)))
    return out


class Node:
    __slots__ = ("tag", "start", "open_end", "close_start", "end", "parent", "blocky")

    def __init__(self, tag, start, open_end, parent):
        self.tag, self.start, self.open_end, self.parent = tag, start, open_end, parent
        self.close_start = self.end = None
        self.blocky = False

    def ancestors(self):
        a = self.parent
        while a is not None:
            yield a
            a = a.parent


# EPUB 2 content documents are validated against XHTML 1.1 (OPS 2.0.1): a block inserted inside an
# element outside this vocabulary (e.g. calibre's HTML5 <section>) adds a validation error per child
XHTML11 = {"html", "head", "title", "base", "meta", "link", "style", "script", "noscript", "body", "div", "p",
           "ul", "ol", "li", "dl", "dt", "dd", "address", "hr", "pre", "blockquote", "ins", "del", "a", "span",
           "bdo", "br", "em", "strong", "dfn", "code", "samp", "kbd", "var", "cite", "abbr", "acronym", "q", "sub",
           "sup", "tt", "i", "b", "big", "small", "object", "param", "img", "map", "area", "form", "label", "input",
           "select", "optgroup", "option", "textarea", "fieldset", "legend", "button", "table", "caption", "thead",
           "tfoot", "tbody", "colgroup", "col", "tr", "th", "td", "ruby", "rbc", "rtc", "rb", "rt", "rp", "applet",
           "iframe", "svg"} | HEADINGS


def insertion(node, xhtml11=False):
    """Offset where a new block placed 'after' this leaf stays valid (X)HTML. With xhtml11 (EPUB 2),
    never inside an element XHTML 1.1 does not know."""
    def barrier(a):
        return a.tag in BARRIER or (xhtml11 and a.tag not in XHTML11)
    t = node
    for a in node.ancestors():
        if a.parent is not None and barrier(a):
            t = a
    while True:
        p = t.parent
        if p is None or p.tag in FLOW_PARENT or p.tag not in KNOWN:
            return t.end
        if t.tag in INSIDE_OK and not (xhtml11 and t.tag not in XHTML11):
            return t.close_start
        t = p


def scan(s, xhtml11=False):
    """(paras, title): leaf blocks in document order and the <title> text. xhtml11: EPUB 2 rules."""
    root = Node("#root", 0, 0, None)
    stack, leaves, title = [root], [], None

    def close(node, close_start, end):
        node.close_start, node.end = close_start, end
        if node.parent is not None and (node.blocky or node.tag in BLOCKY):
            node.parent.blocky = True
        if node.tag in LEAF and not node.blocky and not any(a.tag in SKIP_CONTEXT for a in node.ancestors()):
            leaves.append(node)

    def pop_to(i, at):  # implicitly close everything above stack[i] (exclusive)
        while len(stack) - 1 > i:
            close(stack.pop(), at, at)

    def find_open(names, scope):
        for i in range(len(stack) - 1, 0, -1):
            if stack[i].tag in names:
                return i
            if stack[i].tag in scope:
                return None
        return None

    pos = 0
    while True:
        m = TOKEN.search(s, pos)
        if not m:
            break
        pos = m.end()
        if m.group(1):  # end tag
            name = local(m.group(1))
            for i in range(len(stack) - 1, 0, -1):
                if stack[i].tag == name:
                    pop_to(i, m.start())
                    close(stack.pop(), m.start(), m.end())
                    break
            continue
        if not m.group(2):
            continue  # comment, doctype, PI, CDATA
        name, attrs = local(m.group(2)), m.group(3)
        selfclose = attrs.rstrip().endswith("/")
        if name in RAW and not selfclose:
            c = re.compile(r"</(?:\w+:)?%s\s*>" % name, re.I).search(s, pos)
            if name == "title" and title is None:
                title = html_text(s[pos:c.start() if c else len(s)])
            pos = c.end() if c else len(s)
            continue
        # HTML's implicit end tags
        if name in CLOSES_P:
            i = find_open({"p"}, P_SCOPE)
            if i is not None:
                pop_to(i - 1, m.start())
        if name == "li":
            i = find_open({"li"}, {"ul", "ol", "menu", "dir"} | P_SCOPE)
            if i is not None:
                pop_to(i - 1, m.start())
        if name in ("dt", "dd"):
            i = find_open({"dt", "dd"}, {"dl"} | P_SCOPE)
            if i is not None:
                pop_to(i - 1, m.start())
        if name in VOID or selfclose:
            if name in BLOCKY:
                stack[-1].blocky = True
            continue
        stack.append(Node(name, m.start(), m.end(), stack[-1]))
    pop_to(0, len(s))

    leaves.sort(key=lambda n: n.start)
    paras = []
    for n in leaves:
        inner = s[n.open_end:n.close_start]
        text, imgs = html_text(inner), images(inner)
        if not text and not imgs:
            continue
        paras.append({"start": n.start, "end": n.end, "ins": insertion(n, xhtml11), "tag": n.tag, "text": text,
                      **({"imgs": imgs} if imgs else {})})
    return paras, title


def find_id(s, frag):
    """Offset of the tag carrying id=frag (or legacy <a name=frag>), else None."""
    m = re.search(r"<[A-Za-z][^>]*?\s(?:id|name)\s*=\s*([\"'])%s\1" % re.escape(frag), s)
    return m.start() if m else None


# --- navigation documents ------------------------------------------------------------------

NAV = re.compile(r"<((?:\w+:)?nav)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>(.*?)</\1\s*>", re.S | re.I)


def parse_nav(s):
    """EPUB3 nav document -> [(title, href, depth)] from the toc nav (or the first nav)."""
    navs = list(NAV.finditer(s))
    if not navs:
        return []

    def kind(m):
        t = (attr(m.group(2), "epub:type") or attr(m.group(2), "type") or "").split()
        return 0 if "toc" in t else 1 if attr(m.group(2), "role") == "doc-toc" else 2
    body = min(navs, key=kind).group(3)
    out, depth, pos = [], 0, 0
    tok = re.compile(r"<(/?)((?:\w+:)?(?:ol|a))\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.I)
    while True:
        m = tok.search(body, pos)
        if not m:
            break
        pos = m.end()
        tag = local(m.group(2))
        if tag == "ol":
            depth += -1 if m.group(1) else 1
        elif not m.group(1):
            close = re.compile(r"</(?:\w+:)?a\s*>", re.I).search(body, pos)
            text = html_text(body[pos:close.start() if close else len(body)])
            href = attr(m.group(3), "href")
            if href:
                out.append((text, href, max(depth - 1, 0)))
            pos = close.end() if close else len(body)
    return out


def parse_ncx(s):
    """EPUB2 NCX -> [(title, src, depth)] from navMap, in document order."""
    m = re.search(r"<(?:\w+:)?navMap\b.*?</(?:\w+:)?navMap\s*>", s, re.S)
    if not m:
        return []
    body, out, open_entries = m.group(0), [], []
    tok = re.compile(r"<(/?)(?:\w+:)?(navPoint|text|content)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>")
    pos = 0
    while True:
        t = tok.search(body, pos)
        if not t:
            break
        pos = t.end()
        closing, tag, attrs = t.group(1), t.group(2), t.group(3)
        if tag == "navPoint":
            if closing:
                if open_entries:
                    open_entries.pop()
            elif not attrs.rstrip().endswith("/"):
                e = [None, None, len(open_entries)]
                out.append(e)
                open_entries.append(e)
        elif not open_entries or closing:
            continue
        elif tag == "text" and open_entries[-1][0] is None:
            c = re.compile(r"</(?:\w+:)?text\s*>").search(body, pos)
            open_entries[-1][0] = html_text(body[pos:c.start() if c else len(body)])
        elif tag == "content" and open_entries[-1][1] is None:
            open_entries[-1][1] = attr(attrs, "src")
    return [(t or "", h, d) for t, h, d in out if h]


# --- local resources -----------------------------------------------------------------------

REF_ATTRS = re.compile(r"""<(?:\w+:)?(\w+)\b((?:[^>"']|"[^"]*"|'[^']*')*)>""", re.S)
CSS_URL = re.compile(r"""url\(\s*(?:"([^"]*)"|'([^']*)'|([^)\s]*))\s*\)|@import\s+(?:"([^"]*)"|'([^']*)')""")


def css_refs(css):
    return [html.unescape(next(g for g in m.groups() if g is not None)) for m in CSS_URL.finditer(css)
            if any(g is not None for g in m.groups())]


def html_refs(s):
    """Local resource URLs used by a document (images, stylesheets, media, CSS url())."""
    s_nc = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    refs = []
    for m in REF_ATTRS.finditer(s_nc):
        tag, attrs = m.group(1).lower(), m.group(2)
        for a in ("src", "poster", "data"):
            v = attr(attrs, a)
            if v:
                refs.append(v)
        if tag in ("image", "use", "feimage"):
            v = attr(attrs, "href")
            if v:
                refs.append(v)
        if tag == "link" and attr(attrs, "href"):
            rel = (attr(attrs, "rel") or "").lower().split()
            if {"stylesheet", "icon", "preload", "apple-touch-icon"} & set(rel):
                refs.append(attr(attrs, "href"))
        for a in ("srcset", "imagesrcset"):
            v = attr(attrs, a)
            if v:
                refs += [c.strip().split()[0] for c in v.split(",") if c.strip()]
        v = attr(attrs, "style")
        if v:
            refs += css_refs(v)
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style\s*>", s_nc, re.S | re.I):
        refs += css_refs(m.group(1))
    return [r for r in refs if is_local_ref(r) and not r.strip().lower().startswith("data:")]


def collect_resources(doc_rel, text, read):
    """Container-relative paths of local resources reachable from a document (one level of CSS
    nesting deep). read(rel) -> bytes or None. Returns (found, skipped_refs)."""
    found, skipped, todo = [], [], [(doc_rel, r) for r in html_refs(text)]
    seen = set()
    while todo:
        base, ref = todo.pop(0)
        rel = resolve_ref(base, ref)
        if rel is None:
            skipped.append(ref)
            continue
        if rel in seen or rel == doc_rel:
            continue
        seen.add(rel)
        data = read(rel)
        if data is None:
            skipped.append(ref)
            continue
        found.append(rel)
        if rel.lower().endswith(".css"):
            todo += [(rel, r) for r in css_refs(data.decode("utf-8", "replace")) if is_local_ref(r)]
    return found, skipped
