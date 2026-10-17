"""Image credits carried inside the illustrated book, full credit metadata from providers, and the
static/offline preview rendering of untrusted book markup. Synthetic inputs only; the real-Chrome
tests use a temporary canary file and a 127.0.0.1 collector, never the network."""
import base64
import glob
import http.server
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_fixtures as F  # noqa: E402
import providers  # noqa: E402
import static_page  # noqa: E402

LONG_AUTHOR = "Photographer " + "with a very long required attribution line, " * 8 + "end-of-credit"
CC = "https://creativecommons.org/licenses/by-sa/4.0/"


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload

    def get_json(self, url, params=None, headers=None):
        return self.payload


class TestProviderCredits(unittest.TestCase):
    def test_commons_keeps_credit_fields_whole_and_bounds_description(self):
        ext = {"Artist": {"value": f"<a href='u'>{LONG_AUTHOR}</a>"}, "LicenseShortName": {"value": "CC BY-SA 4.0"},
               "LicenseUrl": {"value": CC}, "Credit": {"value": "<span>Own work</span>"},
               "Attribution": {"value": "Required attribution text"}, "AttributionRequired": {"value": "true"},
               "UsageTerms": {"value": "Creative Commons Attribution-Share Alike 4.0"},
               "Copyrighted": {"value": "True"}, "ImageDescription": {"value": "d" * 500}}
        hits = providers._commons_hits([{"title": "File:A.jpg", "imageinfo": [{
            "url": "https://upload.example/a.jpg", "mime": "image/jpeg", "descriptionurl": "https://c.example/File:A.jpg",
            "extmetadata": ext}]}])
        h = hits[0]
        self.assertEqual(h["author"], LONG_AUTHOR)  # no longer cut at 200 characters
        self.assertEqual((h["credit"], h["attribution"], h["attribution_required"], h["copyrighted"]),
                         ("Own work", "Required attribution text", "true", "True"))
        self.assertEqual(h["usage_terms"], "Creative Commons Attribution-Share Alike 4.0")
        self.assertEqual(len(h["about"]), 200)  # descriptive text stays bounded

    def test_openverse_keeps_attribution(self):
        hits = providers.openverse(FakeHttp({"results": [{
            "id": "x", "url": "https://img.example/x.jpg", "creator": "Ann", "creator_url": "https://example/ann",
            "license": "by", "license_version": "2.0", "license_url": "https://creativecommons.org/licenses/by/2.0/",
            "attribution": '"x" by Ann is licensed under CC BY 2.0.', "foreign_landing_url": "https://example/p",
            "source": "flickr"}]}), "x")
        self.assertEqual(hits[0]["attribution"], '"x" by Ann is licensed under CC BY 2.0.')
        self.assertEqual(hits[0]["license"], "BY 2.0")


def epub_text(path):
    with zipfile.ZipFile(path) as z:
        return "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml"))


class TestInBookCredits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="credits-")
        self.orig = F.epub3(os.path.join(self.tmp, "b.epub"))
        self.work = os.path.join(self.tmp, "w")
        self.assertEqual(F.run("extract.py", self.orig, self.work).returncode, 0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def source(self, eid, rows):
        F.write(os.path.join(self.work, "candidates", eid, "SOURCE.json"), json.dumps(rows))

    def build(self, ents=None, **settings):
        ents = ents or [{"id": "beacon", "name": "Beacon", "role": "lamp"}, {"id": "lumen", "name": "Lumen"}]
        F.entities(self.work, ents, **settings)
        self.source("beacon", [{"file": "a.png", "from": "commons", "url": "https://c.example/File:B.jpg",
                                "img": "https://upload.example/b.jpg", "author": LONG_AUTHOR, "license": "CC BY-SA 4.0",
                                "license_url": CC, "credit": "Own work", "usage_terms": "Share alike"}])
        out = os.path.join(self.tmp, "o.epub")
        r = F.run("inject.py", self.work, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        v = F.run("verify.py", self.orig, out)
        self.assertEqual(v.returncode, 0, v.stdout)  # credits live inside markers: original bytes intact
        return epub_text(out), json.loads(r.stdout.splitlines()[0]), r

    def credits(self, text):
        blocks = re.findall(r'<div class="illus-credits".*?</div>', text, re.S)
        self.assertEqual(len(blocks), 1, "exactly one credits section")
        return blocks[0]

    def test_inline_gallery_and_both_carry_credits(self):
        for settings in ({"inline": True, "gallery": True}, {"inline": True, "gallery": False},
                         {"inline": False, "gallery": True}):
            with self.subTest(**settings):
                text, rep, _ = self.build(**settings)
                self.assertTrue(rep["credits"])
                c = self.credits(text)
                self.assertIn("Beacon · lamp", c)
                self.assertIn(LONG_AUTHOR, c)  # whole credit, not truncated
                self.assertIn(f"CC BY-SA 4.0（{CC}）", c)
                self.assertIn("Own work", c)
                self.assertIn("Share alike", c)
                self.assertIn("https://c.example/File:B.jpg", c)

    def test_unknown_is_explicit_and_changes_are_what_was_done(self):
        text, _, _ = self.build([{"id": "beacon", "name": "Beacon", "crop": [0, 0, 0.5, 1]},
                                 {"id": "lumen", "name": "Lumen"}])
        c = self.credits(text)
        beacon, lumen = re.findall(r"<p [^>]*>\d+\. (.*?)</p>", c)
        self.assertIn("已裁剪", beacon)
        self.assertIn("转换为 JPEG", beacon)  # PNG candidate
        self.assertNotIn("已裁剪", lumen)  # no crop asked -> none claimed
        self.assertIn("作者：未知", lumen)
        self.assertIn("许可：未知", lumen)
        self.assertIn("来源：未知", lumen)
        self.assertIn("未经独立核实", c)
        lst = F.readb(os.path.join(self.work, "配图清单.md")).decode("utf-8")
        self.assertIn(LONG_AUTHOR, lst)
        self.assertIn("image: https://upload.example/b.jpg", lst)

    def test_legacy_rows_use_source_page_and_local_paths_stay_out_of_book(self):
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}, {"id": "lumen", "name": "Lumen"}])
        self.source("beacon", [{"file": "a.png", "from": "web", "url": "https://img.example/raw.jpg",
                                "source_page": "https://commons.example/wiki/File:Raw.jpg", "license": "CC BY 4.0"}])
        secret = os.path.join(self.tmp, "private-folder", "me.png")
        self.source("lumen", [{"file": "a.png", "from": "reuse", "url": secret}])
        out = os.path.join(self.tmp, "o.epub")
        r = F.run("inject.py", self.work, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        c = self.credits(epub_text(out))
        self.assertIn("来源：https://commons.example/wiki/File:Raw.jpg", c)
        self.assertIn("用户提供的本地文件", c)
        self.assertNotIn("private-folder", c)

    def test_english_labels_and_opt_out(self):
        text, _, _ = self.build(gallery_title="People in this chapter")
        c = self.credits(text)
        self.assertIn("Image credits", c)
        self.assertIn("Creator: unknown", c)
        text, rep, r = self.build(credits=False)
        self.assertNotIn("illus-credits", text)
        self.assertFalse(rep["credits"])
        self.assertIn('"credits": false', r.stderr)

    def test_html_and_markdown_credits_verify(self):
        for name, body in (("a.html", "<html><body><h1>Chapter 1</h1><p>Beacon here.</p></body></html>"),
                           ("a.md", "# Chapter 1\n\nBeacon here.\n")):
            with self.subTest(name):
                d = os.path.join(self.tmp, name.replace(".", "_"))
                orig = F.write(os.path.join(d, name), body)
                work = os.path.join(d, "w")
                self.assertEqual(F.run("extract.py", orig, work).returncode, 0)
                F.entities(work, [{"id": "beacon", "name": "Beacon"}], chapter_re="^Chapter")
                F.write(os.path.join(work, "candidates", "beacon", "SOURCE.json"), json.dumps(
                    [{"file": "a.png", "from": "openverse", "url": "https://p.example/1", "author": "A_|_B",
                      "license": "BY 2.0", "attribution": "by A_B"}]))
                out = os.path.join(d, "out", "o" + os.path.splitext(name)[1])
                self.assertEqual(F.run("inject.py", work, "--out", out).returncode, 0)
                v = F.run("verify.py", orig, out)
                self.assertEqual(v.returncode, 0, v.stdout)
                text = F.readb(out).decode("utf-8")
                self.assertIn('class="illus-credits"' if name.endswith(".html") else "**图片来源**", text)
                self.assertIn("https://p.example/1", text)
                self.assertIn("by A", text)
                lst = F.readb(os.path.join(work, "配图清单.md")).decode("utf-8")
                self.assertIn("A_\\|_B", lst)  # table cells cannot be split by metadata


class TestStaticPage(unittest.TestCase):
    """The rewriting layer embeds only regular files whose real path is inside the roots."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="static-page-"))
        self.root = os.path.join(self.tmp, "build")
        self.doc_dir = os.path.join(self.root, "OPS", "text")
        os.makedirs(self.doc_dir)
        self.canary = os.path.join(self.tmp, "canary.png")
        Image.new("RGB", (60, 60), (0, 255, 0)).save(self.canary)
        self.canary_b64 = base64.b64encode(F.readb(self.canary)).decode()
        self.ok = F.image(os.path.join(self.root, "OPS", "images", "ok.png"), color=(10, 20, 30))
        os.symlink(self.canary, os.path.join(self.root, "OPS", "images", "link.png"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def render(self, frag):
        return static_page.absolutize(static_page.sanitize(frag), self.doc_dir, [self.root])

    def test_only_files_inside_root_are_embedded(self):
        ok = self.render('<img src="../images/ok.png"/>')
        self.assertIn(base64.b64encode(F.readb(self.ok)).decode(), ok)
        for frag in ("<img src=../../../canary.png>",                        # unquoted
                     '<img src="&#46;&#46;/&#46;&#46;/&#46;&#46;/canary.png">',  # HTML entities
                     '<img src="../%2e%2e/%2e%2e/canary.png">',               # percent-encoded
                     f'<img src="file://{self.canary}">',                     # absolute file URL
                     f'<img src="{self.canary}">',                            # root-relative path
                     '<img src="../images/link.png">',                        # symlink escaping the root
                     '<p style="background:url(\\2e\\2e/\\2e\\2e/\\2e\\2e/canary.png)">x</p>',  # CSS escapes
                     '<p style="background:url(../../../canary.png)">x</p>',
                     '<svg><image xlink:href="../../../canary.png"/></svg>'):
            with self.subTest(frag):
                out = self.render(frag)
                self.assertNotIn(self.canary_b64, out)
                self.assertNotIn("canary.png", out.replace("\\2e", ""))  # rewritten to about:blank
                self.assertNotIn("file:", out)

    def test_active_content_removed(self):
        out = self.render('<p onclick="x()">a<script>alert(1)</script><iframe src="x"></iframe>'
                          '<meta http-equiv="refresh" content="0;url=http://127.0.0.1/"><base href="http://x/">'
                          '<img src="../images/ok.png" srcset="http://127.0.0.1/a 1x" onerror="y()">'
                          '<object data="x"></object><embed src="x"><link rel="prefetch" href="http://x/"></p>')
        markup = re.sub(r"data:[^\"']+", "", out).lower()  # base64 payloads may contain any letters
        for bad in ("script", "iframe", "meta", "base", "srcset", "onerror", "onclick", "object", "embed", "link"):
            self.assertNotIn(bad, markup)
        self.assertIn("data:image/png;base64,", out)

    def test_stylesheets_inlined_with_confined_urls_and_imports(self):
        F.write(os.path.join(self.root, "OPS", "s.css"), '@import "../../canary.css";\n@import "t.css";\n'
                'p{background:url(images/ok.png)} h1{background:url(../../canary.png)}')
        F.write(os.path.join(self.root, "OPS", "t.css"), "em{color:red}")
        F.write(os.path.join(self.tmp, "canary.css"), "body{secret:1}")
        out = "".join(static_page.inline_css(['<link rel="stylesheet" href="../s.css">'], self.doc_dir, [self.root]))
        self.assertIn("em{color:red}", out)
        self.assertNotIn("secret", out)
        self.assertNotIn(self.canary_b64, out)
        self.assertIn("data:image/png;base64,", out)


def chrome():
    try:
        import webfetch
        return webfetch.chrome_path()
    except RuntimeError:
        return None


@unittest.skipUnless(chrome(), "Chrome/Chromium not available")
class TestPreviewInRealChrome(unittest.TestCase):
    """End to end with the real browser: hostile book markup, a canary image outside the book and a
    loopback collector. Nothing may reach the collector and no canary pixel may be rendered."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="preview-chrome-"))
        self.hits = []
        hits = self.hits

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                self.send_response(204)
                self.end_headers()

            def log_message(self, *a):
                pass
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.c = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.canary = os.path.join(self.tmp, "canary.png")
        Image.new("RGB", (300, 300), (0, 255, 0)).save(self.canary)
        os.makedirs(os.path.join(self.tmp, "book"))

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def green_pixels(self, png):
        im = Image.open(png).convert("RGB")
        return sum(1 for p in im.getdata() if p[1] > 200 and p[0] < 60 and p[2] < 60)

    def test_hostile_book_preview_is_static_and_confined(self):
        c, canary = self.c, self.canary
        body = (f"<h1>Chapter 1</h1><p>Beacon here.</p>"
                f"<script>new Image().src='{c}/script';</script>"
                f"<p><img src=x onerror=\"new Image().src='{c}/onerror'\"/></p>"
                f'<p><img src="{c}/remote.png"/><img srcset="{c}/srcset.png 1x"/></p>'
                f"<p><img src=../canary.png width=300 height=300 /><img src=link.png width=300 /></p>"
                f'<p><img src="&#46;&#46;/canary.png" width="300"/></p>'
                f'<p><img src="file://{canary}" width="300"/></p>'
                f'<p style="height:300px;background:url(\\2e\\2e/canary.png)">x</p>'
                f'<iframe src="file://{canary}" width="300" height="300"></iframe><iframe src="{c}/iframe"></iframe>'
                f'<meta http-equiv="refresh" content="0;url={c}/refresh"/><link rel="prefetch" href="{c}/prefetch"/>'
                f"<p>End.</p>")
        orig = F.write(os.path.join(self.tmp, "book", "a.html"),
                       f'<html><head><style>@import url("{c}/import.css");</style></head><body>{body}</body></html>')
        os.symlink(canary, os.path.join(self.tmp, "book", "link.png"))
        work = os.path.join(self.tmp, "w")
        self.assertEqual(F.run("extract.py", orig, work).returncode, 0)
        F.entities(work, [{"id": "beacon", "name": "Beacon"}], chapter_re="^Chapter")
        F.image(os.path.join(work, "candidates", "beacon", "a.png"), color=(220, 20, 20), size=(200, 150))
        self.assertEqual(F.run("inject.py", work, "--out", "o.html").returncode, 0)
        built = {p: F.readb(p) for p in glob.glob(os.path.join(work, "build", "**"), recursive=True) if os.path.isfile(p)}
        r = F.run("preview.py", work, 0, 5)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.srv.shutdown()
        self.assertEqual(self.hits, [])
        png = os.path.join(work, "preview", "page_01.png")
        self.assertEqual(self.green_pixels(png), 0)
        im = Image.open(png).convert("RGB")
        self.assertTrue(any(p[0] > 180 and p[1] < 70 and p[2] < 70 for p in im.getdata()),
                        "the inserted (red) illustration still renders")
        self.assertEqual({p: F.readb(p) for p in built}, built)  # preview never changes the built book

    def test_csp_alone_blocks_file_and_network_loads(self):
        """The CSP is the load boundary even for markup the rewriting layer never saw."""
        import pathlib
        import subprocess
        page = os.path.join(self.tmp, "p.html")
        F.write(page, f"<html><head>{static_page.csp_meta()}</head><body>"
                      f'<img src="canary.png" width="300"><img src="file://{self.canary}" width="300">'
                      f'<img src="{self.c}/x.png"><script>new Image().src="{self.c}/s"</script></body></html>')
        shot = os.path.join(self.tmp, "s.png")
        subprocess.run([chrome(), "--headless", "--disable-gpu", *static_page.CHROME_FLAGS,
                        f"--user-data-dir={os.path.join(self.tmp, 'prof')}", "--window-size=800,800",
                        "--virtual-time-budget=3000", f"--screenshot={shot}", pathlib.Path(page).as_uri()],
                       capture_output=True, timeout=120)
        self.assertTrue(os.path.exists(shot))
        self.srv.shutdown()
        self.assertEqual(self.hits, [])
        self.assertEqual(self.green_pixels(shot), 0)


if __name__ == "__main__":
    unittest.main()
