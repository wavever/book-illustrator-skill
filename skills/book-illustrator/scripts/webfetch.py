"""Legacy, opt-in web lookups (find_images.py sources "baike" / "web"; never part of "auto").

- Baidu Baike and Bing Images reject direct (non-browser) requests, so they are rendered with a private headless
  Chrome (its own throwaway profile; it never touches the user's browser) and parsed with lxml.
  Chrome is only looked up when one of these functions is actually called.
- 360 image search (image.so.com) answers direct HTTPS requests with JSON (unofficial, undocumented
  endpoint; the site's own terms apply).

Each function returns a list of dicts: {"img": url, "url": page_url, "about": text}.
"""
import html as htmlmod
import json
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.parse
import urllib.request

from lxml import html as lhtml

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    SSL_CTX = ssl.create_default_context()

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
CHROME_CANDIDATES = [
    os.environ.get("CHROME", ""),
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    shutil.which("google-chrome") or "", shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
]


def chrome_path():
    for c in CHROME_CANDIDATES:
        if c and os.path.exists(c):
            return c
    raise RuntimeError("no Chrome/Chromium found; set CHROME=/path/to/chrome")


def render(url, wait_ms=8000):
    """Rendered DOM of a page via headless Chrome (fresh temporary profile)."""
    profile = tempfile.mkdtemp(prefix="epubillus-chrome-")
    try:
        out = subprocess.run(
            [chrome_path(), "--headless=new", "--disable-gpu", "--hide-scrollbars", "--mute-audio",
             f"--user-data-dir={profile}", f"--user-agent={UA}", "--lang=zh-CN",
             f"--virtual-time-budget={wait_ms}", "--window-size=1280,2400", "--dump-dom", url],
            capture_output=True, text=True, timeout=90)
        return out.stdout
    finally:
        shutil.rmtree(profile, ignore_errors=True)


def _has_class(part):
    return f"contains(@class,'{part}')"


def baike(item, hint="", url=None):
    """Legacy (needs Chrome). On a 多义词 page only a sense containing `hint` is followed; otherwise
    providers.Ambiguous lists the senses so the entity can get "baike_url" or a better "hint"."""
    explicit = bool(url)
    url = url or "https://baike.baidu.com/item/" + urllib.parse.quote(item)
    dom = render(url)
    doc = lhtml.fromstring(dom or "<html/>")
    if not explicit and doc.xpath("//body//*[not(self::script) and not(self::style)][contains(text(),'多义词')]"):
        senses = [(a.text_content().strip(), a.get("href")) for a in doc.xpath("//a[contains(@href,'/item/')]")]
        senses = [(t, h) for t, h in senses if t and len(t) < 60]
        matches = [s for s in senses if hint and hint in s[0]]
        if len({h for _, h in matches}) != 1:
            from providers import Ambiguous
            opts = [{"text": t, "href": urllib.parse.urljoin("https://baike.baidu.com", h)} for t, h in senses[:10]]
            raise Ambiguous(f'Baike "{item}" has several senses' + (f' and hint "{hint}" does not pick exactly one'
                            if hint else '') + '; set "baike_url" on the entity (or a "hint")', opts)
        url = urllib.parse.urljoin("https://baike.baidu.com", matches[0][1])
        dom = render(url)
        doc = lhtml.fromstring(dom or "<html/>")
    title = (doc.xpath("//title/text()") or [""])[0]
    summary = " ".join(" ".join(n.text_content().split()) for n in
                       doc.xpath(f"//*[{_has_class('lemmaSummary')} or {_has_class('lemma-summary')}]"))[:300]
    bad = " or ".join(_has_class(c) for c in ("video", "Video", "relation", "Relation"))
    urls = []
    for xp in (f"//*[{_has_class('abstractAlbum')}]//img",
               "//a[contains(@href,'lemma_content-image')]//img",
               f"//*[{_has_class('lemmaPicture')}]//img",
               f"//*[{_has_class('contentWrapper')}]//img"):
        for img in doc.xpath(xp):
            if img.xpath(f"ancestor::*[{bad}]"):
                continue
            src = img.get("src") or img.get("data-src") or ""
            if re.search(r"bkimg\.cdn\.bcebos\.com/pic/", src):
                clean = src.split("?")[0]
                if clean not in urls:
                    urls.append(clean)
    return [{"img": u, "url": url, "about": f"{title} {summary}".strip()} for u in urls[:8]]


def bing(query, limit=8):
    dom = render("https://cn.bing.com/images/search?q=" + urllib.parse.quote(query) + "&first=1")
    doc = lhtml.fromstring(dom or "<html/>")
    out = []
    for a in doc.xpath("//a[contains(@class,'iusc')]"):
        try:
            m = json.loads(htmlmod.unescape(a.get("m") or ""))
        except ValueError:
            continue
        if m.get("murl"):
            out.append({"img": m["murl"], "url": m.get("purl", ""), "about": m.get("t", "")})
        if len(out) >= limit:
            break
    return out


def so360(query, limit=8):
    url = ("https://image.so.com/j?" + urllib.parse.urlencode(
        {"q": query, "src": "srp", "sn": 0, "pn": limit}))
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://image.so.com/"})
    data = json.load(urllib.request.urlopen(req, timeout=30, context=SSL_CTX))
    return [{"img": x.get("img") or x.get("thumb"), "url": htmlmod.unescape(x.get("link", "")),
             "about": x.get("title", "")} for x in data.get("list", [])[:limit] if x.get("img") or x.get("thumb")]


if __name__ == "__main__":  # quick manual test: python3 webfetch.py baike 求伯君
    import sys
    fn = {"baike": baike, "bing": bing, "so360": so360}[sys.argv[1]]
    for r in fn(*sys.argv[2:]):
        print(r["img"], "|", r["about"][:60])
