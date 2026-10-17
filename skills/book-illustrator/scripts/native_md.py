"""Offset-preserving Markdown block scanner (CommonMark/GFM block structure, no rendering).

parse(text) -> (paras, front_matter_title). Each para carries start/end (span of its lines in the
original text), ins (where a figure may be inserted: the end of the enclosing *top-level* block,
so lists and blockquotes are never split), tag (p, h1-h6, li, blockquote, table, html), text and
existing imgs. Fenced and indented code, thematic breaks, front matter and link reference
definitions are never indexed; inline code spans are left out of the text so entity names inside
code never place a figure. ins is None where nothing can be inserted safely (a file without a
final newline whose last line would be broken by appending to it).
"""
import html
import re

from native_html import html_text, images as html_images

FENCE = re.compile(r"^( {0,3})(`{3,}|~{3,})(.*)$")
ATX = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
HR = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
SETEXT = re.compile(r"^ {0,3}(=+|-+)[ \t]*$")
QUOTE = re.compile(r"^ {0,3}> ?")
ITEM = re.compile(r"^( {0,3})([-+*]|\d{1,9}[.)])([ \t]+|$)")
DEF = re.compile(r"^ {0,3}\[((?:[^\]\\]|\\.)+)\]:[ \t]*(<[^>\n]*>|\S+)")
TABLE_DELIM = re.compile(r"^ {0,3}\|?[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)*\|?[ \t]*$")
BLOCK_TAGS = ("address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd|details|dialog|"
              "dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset|h[1-6]|head|header|hr|html|"
              "iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol|optgroup|option|p|param|section|source|"
              "summary|table|tbody|td|tfoot|th|thead|title|tr|track|ul")
HTML_BLOCKS = [  # (start, end-condition or None = ends before a blank line, may interrupt a paragraph)
    (re.compile(r"^ {0,3}<(script|pre|style|textarea)(?:\s|>|$)", re.I),
     re.compile(r"</(?:script|pre|style|textarea)>", re.I), True),
    (re.compile(r"^ {0,3}<!--"), re.compile(r"-->"), True),
    (re.compile(r"^ {0,3}<\?"), re.compile(r"\?>"), True),
    (re.compile(r"^ {0,3}<![A-Za-z]"), re.compile(r">"), True),
    (re.compile(r"^ {0,3}<!\[CDATA\["), re.compile(r"\]\]>"), True),
    (re.compile(r"^ {0,3}</?(?:%s)(?:\s|/?>|$)" % BLOCK_TAGS, re.I), None, True),
    (re.compile(r"^ {0,3}(?:<[A-Za-z][\w-]*(?:\s+[A-Za-z_:][\w:.-]*(?:\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s\"'=<>`]+))?)*"
                r"\s*/?>|</[A-Za-z][\w-]*\s*>)[ \t]*$"), None, False),
]


class Line:
    __slots__ = ("text", "start", "end")

    def __init__(self, text, start, end):
        self.text, self.start, self.end = text, start, end  # end: after the newline


def split_lines(s):
    out, i, nl = [], 0, re.compile(r"\r\n|\n|\r")
    while i < len(s):
        m = nl.search(s, i)
        j, k = (m.start(), m.end()) if m else (len(s), len(s))
        out.append(Line(s[i:j], i, k))
        i = k
    return out


def blank(t):
    return not t.strip(" \t")


def indent_of(t):
    cols = i = 0
    while i < len(t) and t[i] in " \t":
        cols = cols + 1 if t[i] == " " else (cols // 4 + 1) * 4
        i += 1
    return cols, i


def strip_cols(line, n):
    cols = i = 0
    t = line.text
    while i < len(t) and t[i] in " \t" and cols < n:
        cols = cols + 1 if t[i] == " " else (cols // 4 + 1) * 4
        i += 1
    return Line(t[i:], line.start + i, line.end)


def html_start(t, interrupting):
    for k, (start, end, can_interrupt) in enumerate(HTML_BLOCKS):
        if start.match(t) and (can_interrupt or not interrupting):
            return k
    return None


def fence_open(t):
    m = FENCE.match(t)
    if m and not (m.group(2)[0] == "`" and "`" in m.group(3)):
        return m
    return None


def starts_block(t):
    """Would this line interrupt a paragraph?"""
    if ATX.match(t) or fence_open(t) or HR.match(t) or QUOTE.match(t) or html_start(t, True) is not None:
        return True
    m = ITEM.match(t)
    if m and not blank(t[m.end():]):
        return not m.group(2)[0].isdigit() or m.group(2)[:-1] == "1"
    return False


def norm_label(s):
    return re.sub(r"\s+", " ", s.strip()).lower()


def parse_lines(L, defs):
    """Blocks of a list of Lines: dicts with kind para/heading/table/html/code/hr/container."""
    out, i, n = [], 0, len(L)
    while i < n:
        t = L[i].text
        if blank(t):
            i += 1
            continue
        if indent_of(t)[0] >= 4:  # indented code
            last = i
            j = i + 1
            while j < n and (blank(L[j].text) or indent_of(L[j].text)[0] >= 4):
                if not blank(L[j].text):
                    last = j
                j += 1
            out.append({"kind": "code", "start": L[i].start, "end": L[last].end, "last": "other"})
            i = last + 1
            continue
        m = fence_open(t)
        if m:
            close = re.compile(r"^ {0,3}%s{%d,}[ \t]*$" % (re.escape(m.group(2)[0]), len(m.group(2))))
            j = i + 1
            while j < n and not close.match(L[j].text):
                j += 1
            last = min(j, n - 1)
            out.append({"kind": "code", "start": L[i].start, "end": L[last].end, "last": "other"})
            i = last + 1
            continue
        m = ATX.match(t)
        if m:
            title = re.sub(r"(?:^|[ \t]+)#+[ \t]*$", "", m.group(2) or "")
            text, imgs = inline_text(title, defs)
            out.append({"kind": "heading", "level": len(m.group(1)), "start": L[i].start, "end": L[i].end,
                        "text": text, "imgs": imgs, "last": "text"})
            i += 1
            continue
        if HR.match(t):
            out.append({"kind": "hr", "start": L[i].start, "end": L[i].end, "last": "other"})
            i += 1
            continue
        if QUOTE.match(t):
            inner, j, prev_text = [], i, False
            while j < n:
                u = L[j].text
                if blank(u):
                    break
                q = QUOTE.match(u)
                if q:
                    inner.append(Line(u[q.end():], L[j].start + q.end(), L[j].end))
                    prev_text = not blank(u[q.end():]) and not starts_block(u[q.end():])
                elif prev_text and not starts_block(u):
                    inner.append(L[j])  # lazy continuation
                else:
                    break
                j += 1
            kids = parse_lines(inner, defs)
            out.append({"kind": "container", "tag": "blockquote", "start": L[i].start, "end": L[j - 1].end,
                        "children": kids, "last": kids[-1]["last"] if kids else "text"})
            i = j
            continue
        m = ITEM.match(t)
        if m:
            i, block = parse_list(L, i, defs)
            out.append(block)
            continue
        k = html_start(t, False)
        if k is not None:
            end = HTML_BLOCKS[k][1]
            j = i
            if end is None:
                while j + 1 < n and not blank(L[j + 1].text):
                    j += 1
            else:
                while j < n - 1 and not end.search(L[j].text):
                    j += 1
            frag = "\n".join(x.text for x in L[i:j + 1])
            block = {"kind": "html", "start": L[i].start, "end": L[j].end, "last": "text", "text": "", "imgs": []}
            if end is None:
                block.update(text=html_text(frag), imgs=html_images(frag))
            out.append(block)
            i = j + 1
            continue
        # paragraph (with link reference definitions, setext headings and GFM tables)
        j = i
        level = None
        while j + 1 < n:
            u = L[j + 1].text
            if blank(u):
                break
            s = SETEXT.match(u)
            if s and indent_of(u)[0] < 4:
                level = 1 if s.group(1)[0] == "=" else 2
                break
            if starts_block(u):
                break
            j += 1
        lines = L[i:j + 1]
        while lines and level is None:
            d = DEF.match(lines[0].text)
            if not d:
                break
            defs.setdefault(norm_label(d.group(1)), d.group(2).strip("<>"))
            lines = lines[1:]
        if lines:
            src = "\n".join(x.text for x in lines)
            if level:
                text, imgs = inline_text(src, defs)
                out.append({"kind": "heading", "level": level, "start": lines[0].start, "end": L[j + 1].end,
                            "text": text, "imgs": imgs, "last": "other"})
            elif len(lines) >= 2 and "|" in lines[0].text and TABLE_DELIM.match(lines[1].text):
                rows = "\n".join(x.text for k, x in enumerate(lines) if k != 1)
                text, imgs = inline_text(re.sub(r"(?<!\\)\|", " ", rows), defs)
                out.append({"kind": "table", "start": lines[0].start, "end": lines[-1].end, "text": text,
                            "imgs": imgs, "last": "other"})
            else:
                text, imgs = inline_text(src, defs)
                out.append({"kind": "para", "start": lines[0].start, "end": lines[-1].end, "text": text,
                            "imgs": imgs, "last": "text"})
        i = j + (2 if level else 1)
    return out


def parse_list(L, i, defs):
    n = len(L)
    first = ITEM.match(L[i].text)
    key = first.group(2)[-1] if first.group(2)[-1] in ".)" else first.group(2)
    items, j, last_line = [], i, i
    while True:
        t = L[j].text
        m = ITEM.match(t)
        rest = t[m.end():]
        pad = len(m.group(3).expandtabs(4))
        content_col = len(m.group(1)) + len(m.group(2)) + (1 if blank(rest) or pad > 4 else pad)
        cut = len(m.group(1)) + len(m.group(2)) + (min(1, len(m.group(3))) if pad > 4 else len(m.group(3)))
        inner = [Line(t[cut:], L[j].start + cut, L[j].end)]
        k, prev_blank, last, in_fence = j + 1, False, j, bool(fence_open(t[cut:]))
        prev_text = not in_fence and not blank(t[cut:]) and not starts_block(t[cut:])
        while k < n:
            u = L[k].text
            if blank(u):
                inner.append(Line("", L[k].start, L[k].end))
                prev_blank, prev_text = True, False
                k += 1
                continue
            if indent_of(u)[0] >= content_col:
                line = strip_cols(L[k], content_col)
                if (fence_open(line.text) if not in_fence else FENCE.match(line.text)):
                    in_fence = not in_fence
                inner.append(line)
                prev_text = not in_fence and not starts_block(line.text)
            elif prev_text and not prev_blank and not starts_block(u):
                inner.append(L[k])  # lazy continuation of the item's paragraph
            else:
                break
            last, prev_blank = k, False
            k += 1
        while inner and blank(inner[-1].text):
            inner.pop()
        items.append(parse_lines(inner, defs))
        last_line = last
        k = last + 1
        while k < n and blank(L[k].text):
            k += 1
        m2 = ITEM.match(L[k].text) if k < n else None
        if m2 and not HR.match(L[k].text) and indent_of(L[k].text)[0] < content_col and \
                (m2.group(2)[-1] if m2.group(2)[-1] in ".)" else m2.group(2)) == key:
            j = k
            continue
        break
    kids = [b for item in items for b in item]
    return last_line + 1, {"kind": "container", "tag": "li", "start": L[i].start, "end": L[last_line].end,
                           "children": kids, "last": kids[-1]["last"] if kids else "text"}


# --- inline text ---------------------------------------------------------------------------

ESC = re.compile(r"\\([!-/:-@\[-`{-~])")
CODE_SPAN = re.compile(r"(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)", re.S)
LABEL = r"((?:[^\[\]\\]|\\.|\[(?:[^\[\]\\]|\\.)*\])*)"
DEST = r"(<[^>\n]*>|(?:[^\s()\\]|\\.|\((?:[^\s()\\]|\\.)*\))*)"
TITLE = r"""(?:\s+(?:"[^"]*"|'[^']*'|\([^)]*\)))?"""
IMG_INLINE = re.compile(r"!\[" + LABEL + r"\]\(\s*" + DEST + TITLE + r"\s*\)")
IMG_REF = re.compile(r"!\[" + LABEL + r"\](?:\[((?:[^\[\]\\]|\\.)*)\])?")
LINK_INLINE = re.compile(r"\[" + LABEL + r"\]\(\s*" + DEST + TITLE + r"\s*\)")
LINK_REF = re.compile(r"\[" + LABEL + r"\]\[((?:[^\[\]\\]|\\.)*)\]")
AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^<>\s]*|[^<>\s@]+@[^<>\s]+)>")
PUA = 0xE000


def inline_text(src, defs):
    """(plain text, [image urls]) of a run of Markdown inline content."""
    s = ESC.sub(lambda m: chr(PUA + ord(m.group(1))), src)
    s = CODE_SPAN.sub(" ", s)
    s = re.sub(r"<!--.*?-->", " ", s, flags=re.S)
    imgs = html_images(s)

    def restore(x):
        return re.sub("[\uE000-\uE07F]", lambda c: chr(ord(c.group(0)) - PUA), x)

    def img_inline(m):
        imgs.append(restore(m.group(2)[1:-1] if m.group(2).startswith("<") else m.group(2)))
        return " "

    def img_ref(m):
        ref = norm_label(m.group(2) or m.group(1))
        if ref in defs:
            imgs.append(restore(defs[ref]))
            return " "
        return m.group(0)
    s = IMG_INLINE.sub(img_inline, s)
    s = IMG_REF.sub(img_ref, s)
    s = LINK_INLINE.sub(lambda m: m.group(1), s)
    s = LINK_REF.sub(lambda m: m.group(1), s)
    s = AUTOLINK.sub(lambda m: m.group(1), s)
    s = re.sub(r"</?[A-Za-z][^>]*>", "", s)
    s = re.sub(r"\\(?=\n)", "", s)  # hard line break
    s = re.sub(r"\*+|~~|(?<![^\W_])_+|_+(?![^\W_])", "", s)
    s = restore(html.unescape(s))
    return re.sub(r"\s+", " ", s).strip(), imgs


# --- document ------------------------------------------------------------------------------

def parse(s):
    lines = split_lines(s)
    if lines and lines[0].text.startswith("\ufeff"):
        lines[0] = Line(lines[0].text[1:], lines[0].start + 1, lines[0].end)
    title = None
    if lines and lines[0].text.rstrip() == "---":  # YAML front matter
        for k in range(1, len(lines)):
            if lines[k].text.rstrip() in ("---", "..."):
                for x in lines[1:k]:
                    m = re.match(r"title\s*:\s*(.+)$", x.text.strip())
                    if m and title is None:
                        title = m.group(1).strip().strip("\"'")
                lines = lines[k + 1:]
                break
    defs = {}
    parse_lines(lines, defs)  # pass 1: link reference definitions anywhere (never inside code)
    blocks = parse_lines(lines, defs)  # pass 2: text and images with every definition known
    paras = []
    eof_glue = bool(s) and not s.endswith(("\n", "\r"))

    def emit(b, top, tag=None):
        if b["kind"] == "container":
            for c in b["children"]:
                emit(c, top, b["tag"] if c["kind"] != "heading" else None)
            return
        if b["kind"] not in ("para", "heading", "table", "html") or (not b["text"] and not b["imgs"]):
            return
        ins = top["end"]
        if eof_glue and ins == len(s) and top["last"] != "text":
            ins = None
        ptag = tag or {"heading": f"h{b.get('level', 1)}", "para": "p"}.get(b["kind"], b["kind"])
        paras.append({"start": b["start"], "end": b["end"], "ins": ins, "tag": ptag, "text": b["text"],
                      **({"imgs": b["imgs"]} if b["imgs"] else {}), **({"top": True} if b is top else {})})
    for b in blocks:
        emit(b, b)
    return paras, title
