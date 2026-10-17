import json
import os
import sys
import re
import shutil
import tempfile
import unittest
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_fixtures as F  # noqa: E402

DOC = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Doc Title</title>
<link rel="stylesheet" href="css/site.css">
<style>.x { background: url('img/bg.png') }</style>
<script>var s = "<p>Beacon in script</p>";</script>
</head>
<body><header><h1>Chapter One</h1></header>
<article><div><p>Beacon first appears.</p><p>More text.</p></div>
<ul><li>Gamma in list</li><li>other</li></ul>
<p><img src="img/existing.png" alt=""></p>
<h2>Chapter Two</h2>
<p>Lumen here<p>Unclosed paragraph
</article></body></html>
"""


class HtmlBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-html-")
        self.work = os.path.join(self.tmp, "work")
        self.srcdir = os.path.join(self.tmp, "site")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def site(self, doc=DOC, name="book.html", raw=None):
        F.write(os.path.join(self.srcdir, "css", "site.css"), "body{background:url(../img/paper.png)}\n")
        for n in ("bg.png", "existing.png", "paper.png"):
            F.image(os.path.join(self.srcdir, "img", n))
        return F.write(os.path.join(self.srcdir, name), raw if raw is not None else doc)

    def extract(self, path, *extra):
        r = F.run("extract.py", path, self.work, *extra)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return F.book(self.work)

    def inject(self, out):
        r = F.run("inject.py", self.work, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return json.loads(r.stdout.splitlines()[0])


class TestHtml(HtmlBase):
    def test_extract_toc_paras_resources(self):
        b = self.extract(self.site())
        self.assertEqual(b["format"], "html")
        self.assertEqual(b["source_path"], os.path.join(self.srcdir, "book.html"))
        texts = [p["text"] for p in b["paras"]]
        self.assertNotIn("Beacon in script", " ".join(texts))
        for t in ("Beacon first appears.", "More text.", "Gamma in list", "Lumen here", "Unclosed paragraph"):
            self.assertIn(t, texts)
        self.assertEqual([t["title"] for t in b["toc"]], ["Chapter One", "Chapter Two"])
        self.assertEqual(sorted(b["source"]["resources"]),
                         ["css/site.css", "img/bg.png", "img/existing.png", "img/paper.png"])
        for r in b["source"]["resources"]:
            self.assertTrue(os.path.isfile(os.path.join(self.work, "src", r)))

    def test_inject_package_and_verify(self):
        orig = self.site()
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}, {"id": "gamma", "name": "Gamma"},
                               {"id": "lumen", "name": "Lumen"}], chapter_re="^Chapter")
        out = os.path.join(self.tmp, "out", "illustrated.html")
        rep = self.inject(out)
        self.assertEqual((rep["out"], rep["assets"]), (out, os.path.join(self.tmp, "out", "illustrated_assets")))
        self.assertEqual((rep["figures"], rep["galleries"]), (3, 2))
        html = F.readb(out).decode("utf-8")
        self.assertIn("Gamma in list<!--illus:start-->", html)  # inside the <li>
        self.assertNotRegex(html, r"</li>\s*<!--illus:start-->")
        for src in re.findall(r'<img src="([^"]+)"', html):
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", unquote(src))), src)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", "img", "paper.png")))  # via CSS
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("OK: 6 inserted blocks", r.stdout)  # 3 figures + 2 galleries + image credits
        self.assertTrue(rep["credits"])
        self.assertEqual(html.count('class="illus-credits"'), 1)
        # text corruption must fail
        F.write(out, html.replace("More text.", "More text!"))
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHANGED", r.stdout)
        # a missing copied resource must fail
        F.write(out, html)
        os.remove(os.path.join(self.tmp, "out", "img", "existing.png"))
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("MISSING RESOURCE", r.stdout)

    def test_output_beside_source_reuses_resources(self):
        orig = self.site()
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}], gallery=False)
        out = os.path.join(self.srcdir, "book_illustrated.html")
        rep = self.inject(out)
        self.assertEqual(rep["resources_copied"], 0)
        self.assertEqual(F.run("verify.py", orig, out).returncode, 0)

    def test_conflicting_resource_aborts_before_writing(self):
        orig = self.site()
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}])
        outdir = os.path.join(self.tmp, "out")
        F.write(os.path.join(outdir, "css", "site.css"), "different\n")
        r = F.run("inject.py", self.work, "--out", os.path.join(outdir, "x.html"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("already exist", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(outdir, "x.html")))
        self.assertFalse(os.path.exists(os.path.join(outdir, "x_assets")))

    def test_wrong_extension_and_source_overwrite_rejected(self):
        orig = self.site()
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}])
        for bad in ("x.md", "x.epub", "x.xhtml", orig):
            r = F.run("inject.py", self.work, "--out", bad)
            self.assertNotEqual(r.returncode, 0, bad)
        self.assertEqual(F.readb(orig).decode("utf-8"), DOC)

    def test_no_heading_fallback_uses_title(self):
        b = self.extract(self.site("<html><head><title>Only Title</title></head><body><p>Beacon.</p></body></html>"))
        self.assertEqual([t["title"] for t in b["toc"]], ["Only Title"])

    def test_gbk_source_round_trips(self):
        doc = ('<html><head><meta charset="gbk"><title>t</title></head><body>\r\n<h1>第一章 开始</h1>\r\n'
               '<p>丁磊创办了网易。</p>\r\n</body></html>\r\n')
        orig = self.site(raw=doc.encode("gbk"))
        b = self.extract(orig)
        self.assertEqual(b["source"]["encoding"], "gbk")
        self.assertIn("丁磊创办了网易。", [p["text"] for p in b["paras"]])
        F.entities(self.work, [{"id": "ding", "name": "丁磊", "role": "网易创始人 ✓"}])
        out = os.path.join(self.tmp, "out", "g.html")
        self.inject(out)
        data = F.readb(out)
        self.assertIn("&#10003;".encode(), data)  # not encodable in GBK -> character reference
        self.assertIn("丁磊创办了网易。".encode("gbk"), data)
        self.assertEqual(F.run("verify.py", orig, out).returncode, 0)

    def test_undeclared_non_utf8_rejected_unless_encoding_given(self):
        orig = self.site(raw="<html><body><p>café Beacon</p></body></html>".encode("latin-1"))
        r = F.run("extract.py", orig, self.work)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--encoding", r.stderr)
        b = self.extract(orig, "--encoding", "latin-1")
        self.assertEqual(b["paras"][0]["text"], "café Beacon")

    def test_xhtml_marker_collision(self):
        doc = ('<?xml version="1.0" encoding="utf-8"?>\n<html xmlns="http://www.w3.org/1999/xhtml"><head><title>x'
               '</title></head><body><p>Beacon <!--illus:start-->x<!--illus:end--></p></body></html>\n')
        orig = self.site(doc, name="page.xhtml")
        b = self.extract(orig)
        self.assertNotEqual(b["marker"], "illus")
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}], gallery=False)
        out = os.path.join(self.tmp, "o", "page.xhtml")
        self.inject(out)
        self.assertEqual(F.run("verify.py", orig, out).returncode, 0)


if __name__ == "__main__":
    unittest.main()
