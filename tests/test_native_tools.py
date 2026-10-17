import base64
import glob
import os
import re
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_fixtures as F  # noqa: E402

# a real jar is optional: $EPUBCHECK_JAR, the skill's cache, or the pre-rename cache
JAR = next((p for p in (os.environ.get("EPUBCHECK_JAR", ""),
                        os.path.expanduser("~/.cache/book-illustrator/epubcheck-5.1.0/epubcheck.jar"),
                        os.path.expanduser("~/.cache/epub-illustrate/epubcheck-5.1.0/epubcheck.jar"))
            if p and os.path.isfile(p)), "")

FAKE_JAVA = r'''#!{py}
import json, os, sys
args = sys.argv[1:]
epub, report = args[2], args[args.index("--json") + 1]
mode = os.environ["FAKE_MODE"]
if mode == "crash":
    sys.stderr.write("Exception in thread main\n"); sys.exit(3)
n = 1 if "orig" in os.path.basename(epub) else 2
msgs = [{{"ID": "RSC-005", "severity": "ERROR", "message": "same problem",
          "locations": [{{"path": "OPS/a.xhtml", "line": i}} for i in range(n)]}}]
if mode == "nolist":
    msgs = []
json.dump({{"checker": {{"nFatal": 0, "nError": n}}, "messages": msgs}}, open(report, "w"))
sys.exit(1)
'''

FAKE_CHROME = r'''#!{py}
import os, shutil, sys
from urllib.parse import urlparse, unquote
from PIL import Image
shot = next(a.split("=", 1)[1] for a in sys.argv if a.startswith("--screenshot="))
shutil.copy(unquote(urlparse(sys.argv[-1]).path), os.environ["FAKE_PAGE_COPY"])
im = Image.new("RGB", (760, 900), (251, 248, 241))
im.paste((0, 0, 0), (10, 10, 300, 600))
im.save(shot)
'''


def script(path, body):
    F.write(path, body.format(py=sys.executable))
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


class TestEpubcheckDiff(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-tools-")
        self.java = script(os.path.join(self.tmp, "java"), FAKE_JAVA)
        self.jar = F.write(os.path.join(self.tmp, "epubcheck.jar"), b"jar")
        self.orig = F.write(os.path.join(self.tmp, "orig.epub"), b"x")
        self.new = F.write(os.path.join(self.tmp, "new.epub"), b"x")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def diff(self, mode, java=None):
        env = {**os.environ, "JAVA": java or self.java, "FAKE_MODE": mode}
        return F.run("epubcheck_diff.py", self.orig, self.new, self.jar, env=env)

    def test_repeated_error_growth_is_new(self):
        r = self.diff("count")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("original: 1 errors; illustrated: 2 errors; new: 1 in 1 kinds", r.stdout)

    def test_tool_failures_are_not_zero_errors(self):
        for mode in ("crash", "nolist"):
            r = self.diff(mode)
            self.assertEqual(r.returncode, 2, mode + r.stdout)
            self.assertNotIn("0 errors", r.stdout)
        r = self.diff("count", java=os.path.join(self.tmp, "no-java"))
        self.assertEqual(r.returncode, 2)
        self.assertIn("not found", r.stdout)
        r = F.run("epubcheck_diff.py", self.orig, self.new, os.path.join(self.tmp, "none.jar"))
        self.assertEqual(r.returncode, 2)

    def test_jar_cached_under_old_name_is_reused(self):
        import epubcheck_diff
        new, old = os.path.join(self.tmp, "new-cache"), os.path.join(self.tmp, "old-cache")
        legacy = F.write(os.path.join(old, f"epubcheck-{epubcheck_diff.VERSION}", "epubcheck.jar"), b"jar")
        with mock.patch.object(epubcheck_diff, "CACHE", new), \
                mock.patch.object(epubcheck_diff, "LEGACY_CACHE", old), \
                mock.patch("urllib.request.urlopen", side_effect=AssertionError("must not download")):
            self.assertEqual(epubcheck_diff.ensure_jar(), legacy)
            current = F.write(os.path.join(new, f"epubcheck-{epubcheck_diff.VERSION}", "epubcheck.jar"), b"jar")
            self.assertEqual(epubcheck_diff.ensure_jar(), current)

    @unittest.skipUnless(os.path.isfile(JAR) and shutil.which("java"), "local epubcheck jar/java not available")
    def test_real_epubcheck_on_illustrated_fixtures(self):
        for maker, ents in ((F.epub3, ["Beacon", "Gamma", "Delta", "Lumen"]), (F.epub2, ["丁磊", "Lumen"])):
            work = os.path.join(self.tmp, maker.__name__)
            orig = maker(os.path.join(self.tmp, maker.__name__ + ".epub"))
            self.assertEqual(F.run("extract.py", orig, work).returncode, 0)
            F.entities(work, [{"id": f"e{k}", "name": n} for k, n in enumerate(ents)])
            r = F.run("inject.py", work, "--out", "o.epub")
            self.assertEqual(r.returncode, 0, r.stderr)
            r = F.run("epubcheck_diff.py", orig, os.path.join(work, "o.epub"), JAR)
            self.assertEqual(r.returncode, 0, r.stdout)
            self.assertIn("illustrated: 0 errors", r.stdout)

    @unittest.skipUnless(os.path.isfile(JAR) and shutil.which("java"), "local epubcheck jar/java not available")
    def test_real_epubcheck_epub2_with_invalid_section_gets_no_new_errors(self):
        orig = F.epub2_section(os.path.join(self.tmp, "sec.epub"))
        work = os.path.join(self.tmp, "sec")
        self.assertEqual(F.run("extract.py", orig, work).returncode, 0)
        F.entities(work, [{"id": "alice", "name": "Alice"}, {"id": "beacon", "name": "Beacon"}])
        self.assertEqual(F.run("inject.py", work, "--out", "o.epub").returncode, 0)
        r = F.run("epubcheck_diff.py", orig, os.path.join(work, "o.epub"), JAR)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertRegex(r.stdout, r"original: [1-9]\d* errors")  # the source really is invalid
        self.assertIn("new: 0 in 0 kinds", r.stdout)


class TestPreview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-preview-")
        self.page = os.path.join(self.tmp, "page.html")
        self.env = {**os.environ, "CHROME": script(os.path.join(self.tmp, "chrome"), FAKE_CHROME),
                    "FAKE_PAGE_COPY": self.page}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def preview(self, work):
        """Preview page as rendered: it loads nothing by URL; local images are embedded data: URIs."""
        r = F.run("preview.py", work, 0, 1, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        self.assertTrue(os.path.isfile(os.path.join(work, "preview", "page_01.png")))
        page = F.readb(self.page).decode("utf-8")
        self.assertIn("img-src data:;", page)
        self.assertNotIn("file:", page)
        self.assertNotIn("<link", page)
        embedded = [base64.b64decode(b) for b in re.findall(r"data:[\w/+.-]+;base64,([A-Za-z0-9+/=]+)", page)]
        return page, embedded

    def assert_embeds(self, embedded, *paths):
        for p in paths:
            self.assertIn(F.readb(p), embedded, p)

    def build(self, orig, ents):
        work = os.path.join(self.tmp, "w")
        self.assertEqual(F.run("extract.py", orig, work).returncode, 0)
        F.entities(work, [{"id": n.lower(), "name": n} for n in ents], chapter_re="^第|^Chapter")
        out = "o" + os.path.splitext(orig)[1]
        r = F.run("inject.py", work, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.built = {p: F.readb(p) for p in glob.glob(os.path.join(work, "build", "**"), recursive=True)
                      if os.path.isfile(p)}
        return work

    def assert_build_unchanged(self):
        self.assertEqual({p: F.readb(p) for p in self.built}, self.built)

    def test_epub_preview_resolves_images_per_content_dir_and_keeps_css(self):
        work = self.build(F.epub3(os.path.join(self.tmp, "b.epub")), ["Beacon", "Lumen"])
        page, embedded = self.preview(work)
        b = os.path.join(work, "build", "OPS")
        self.assert_embeds(embedded, os.path.join(b, "illus", "beacon.jpg"), os.path.join(b, "illus", "lumen.jpg"),
                           os.path.join(b, "images", "old.png"))
        self.assertIn("p { text-indent: 2em; }", page)  # the book's stylesheet, inlined
        self.assert_build_unchanged()

    def test_html_preview(self):
        d = os.path.join(self.tmp, "site")
        F.write(os.path.join(d, "s.css"), "p{color:#333}")
        orig = F.write(os.path.join(d, "a.html"), '<html><head><link rel="stylesheet" href="s.css"></head><body>'
                       "<h1>Chapter 1</h1><p>Beacon here.</p></body></html>")
        work = self.build(orig, ["Beacon"])
        page, embedded = self.preview(work)
        self.assertIn("<style>p{color:#333}</style>", page)
        self.assert_embeds(embedded, os.path.join(work, "build", "o_assets", "beacon.jpg"))
        self.assert_build_unchanged()

    def test_markdown_preview_or_actionable_message(self):
        orig = F.write(os.path.join(self.tmp, "m", "a.md"), "# Chapter 1\n\nBeacon here.\n")
        work = self.build(orig, ["Beacon"])
        try:
            import markdown_it  # noqa: F401
        except ImportError:
            r = F.run("preview.py", work, 0, 0, env=self.env)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("markdown-it-py", r.stderr)
            return
        page, embedded = self.preview(work)
        self.assertIn("<img", page)
        self.assert_embeds(embedded, os.path.join(work, "build", "o_assets", "beacon.jpg"))


if __name__ == "__main__":
    unittest.main()
