import json
import os
import sys
import posixpath
import re
import shutil
import tempfile
import unittest
import zipfile
from urllib.parse import unquote
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_fixtures as F  # noqa: E402


def img_refs(xhtml):
    return re.findall(r"""<img\b[^>]*?\ssrc\s*=\s*["']([^"']+)["']""", xhtml)


class EpubBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-epub-")
        self.work = os.path.join(self.tmp, "work")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def extract(self, epub):
        r = F.run("extract.py", epub, self.work)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return F.book(self.work)

    def inject(self, out="out.epub", ok=True):
        r = F.run("inject.py", self.work, "--out", out)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            return json.loads(r.stdout.splitlines()[0])
        return r

    def assert_package_sound(self, out):
        """Every modified XHTML/OPF is well-formed and every image reference resolves to a member."""
        z = zipfile.ZipFile(out)
        names = set(z.namelist())
        self.assertEqual(z.infolist()[0].filename, "mimetype")
        self.assertEqual(z.infolist()[0].compress_type, zipfile.ZIP_STORED)
        container = ET.fromstring(z.read("META-INF/container.xml"))
        opf_path = container.find(".//{*}rootfile").get("full-path")
        opf = ET.fromstring(z.read(opf_path))
        hrefs = {posixpath.normpath(posixpath.join(posixpath.dirname(opf_path), unquote(i.get("href"))))
                 for i in opf.find("{*}manifest")}
        ids = [i.get("id") for i in opf.find("{*}manifest")]
        self.assertEqual(len(ids), len(set(ids)))
        for h in hrefs:
            self.assertIn(h, names)
        for n in names:
            if n.endswith(".xhtml"):
                ET.fromstring(z.read(n))
                for src in img_refs(z.read(n).decode("utf-8")):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(n), unquote(src)))
                    self.assertIn(target, names, f"{src} from {n}")
                    self.assertIn(target, hrefs)
        return z


class TestEpub3(EpubBase):
    def test_extract_nested_lists_tables_and_paths(self):
        b = self.extract(F.epub3(os.path.join(self.tmp, "b3.epub")))
        self.assertEqual(b["format"], "epub")
        self.assertEqual(b["source_path"], os.path.abspath(os.path.join(self.tmp, "b3.epub")))
        self.assertEqual(b["spine"], ["text/ch 1.xhtml", "text/sub/ch2.xhtml"])
        texts = {p["text"]: p for p in b["paras"]}
        for t in ("Intro mentions Beacon here.", "Second para & more.", "Gamma is only in a list.",
                  "Delta inside a table cell.", "Quote with Lumen here.", "Lumen again, plain."):
            self.assertIn(t, texts)
        self.assertEqual([t["title"] for t in b["toc"]], ["第一章 Start", "第二章 Next"])
        self.assertEqual([t["file"] for t in b["toc"]], ["text/ch 1.xhtml", "text/sub/ch2.xhtml"])
        self.assertEqual(b["toc_source"], "nav")
        self.assertEqual(texts["Quote with Lumen here."]["toc"], 1)
        self.assertEqual(texts["Intro mentions Beacon here."]["toc"], 0)
        old = [p for p in b["paras"] if p.get("imgs")]
        self.assertEqual(old[0]["imgs"], ["../images/old.png"])
        with open(os.path.join(self.work, "text", "000.txt"), encoding="utf-8") as f:
            self.assertIn("Intro mentions Beacon here.", f.read())

    def test_inject_verify_and_safe_offsets(self):
        orig = F.epub3(os.path.join(self.tmp, "b3.epub"))
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}, {"id": "gamma", "name": "Gamma"},
                               {"id": "delta", "name": "Delta"}, {"id": "lumen", "name": "Lumen", "note": "1998"}])
        rep = self.inject()
        self.assertEqual((rep["figures"], rep["galleries"], rep["entities"]), (4, 2, 4))
        out = rep["out"]
        self.assertEqual(out, os.path.join(os.path.abspath(self.work), "out.epub"))
        z = self.assert_package_sound(out)
        ch1 = z.read("OPS/text/ch 1.xhtml").decode()
        # list item: inserted inside the <li>, never as a child of <ul>
        self.assertIn("Gamma is only in a list.<!--illus:start-->", ch1)
        self.assertNotRegex(ch1, r"</li>\s*<!--illus:start-->")
        # table cell paragraph: inserted after the whole table
        self.assertIn("</table><!--illus:start-->", ch1)
        # nested div: inserted right after the paragraph, inside the div
        self.assertIn("Intro mentions Beacon here.</p><!--illus:start-->", ch1)
        # images resolve relative to each content file's own directory
        self.assertIn('src="../illus/beacon.jpg"', ch1)
        self.assertIn('src="../../illus/lumen.jpg"', z.read("OPS/text/sub/ch2.xhtml").decode())
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("OK: 7 inserted blocks", r.stdout)  # figures, galleries, manifest + image credits

    def test_inline_and_gallery_toggles(self):
        orig = F.epub3(os.path.join(self.tmp, "b3.epub"))
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}, {"id": "lumen", "name": "Lumen"}], gallery=False)
        rep = self.inject("inline.epub")
        self.assertEqual((rep["figures"], rep["galleries"]), (2, 0))
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}, {"id": "lumen", "name": "Lumen"}], inline=False)
        rep = self.inject("gallery.epub")
        self.assertEqual((rep["figures"], rep["galleries"]), (0, 2))
        z = self.assert_package_sound(rep["out"])
        self.assertNotIn("illus-figure", z.read("OPS/text/ch 1.xhtml").decode())
        for out in ("inline.epub", "gallery.epub"):
            self.assertEqual(F.run("verify.py", orig, os.path.join(self.work, out)).returncode, 0)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}], inline=False, gallery=False)
        self.assertNotEqual(self.inject("none.epub", ok=False).returncode, 0)

    def test_existing_image_paragraph_and_headings_not_used(self):
        self.extract(F.epub3(os.path.join(self.tmp, "b3.epub")))
        F.entities(self.work, [{"id": "start", "name": "Start"}], gallery=False)
        rep = self.inject()
        self.assertEqual(rep["entities"], 0)  # "Start" only occurs in a heading

    def test_no_toc_fallback(self):
        orig = F.epub3(os.path.join(self.tmp, "nonav.epub"), nav=False)
        b = self.extract(orig)
        self.assertEqual(b["toc_source"], "fallback")
        self.assertEqual([t["title"] for t in b["toc"]], ["第一章 Start", "第二章 Next"])
        F.entities(self.work, [{"id": "lumen", "name": "Lumen"}])
        rep = self.inject()
        self.assertEqual(rep["galleries"], 1)
        self.assertEqual(F.run("verify.py", orig, rep["out"]).returncode, 0)

    def test_marker_collision_uses_unique_marker(self):
        orig = F.epub3(os.path.join(self.tmp, "c.epub"),
                       extra_body="<p>literal <!--illus:start-->kept<!--illus:end--> text</p>\n")
        b = self.extract(orig)
        self.assertNotEqual(b["marker"], "illus")
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}])
        rep = self.inject()
        self.assertEqual(F.run("verify.py", orig, rep["out"]).returncode, 0)
        # dropping the original look-alike block must be detected, not silently accepted
        bad = os.path.join(self.tmp, "bad.epub")
        with zipfile.ZipFile(rep["out"]) as zin, zipfile.ZipFile(bad, "w") as zout:
            for i in zin.infolist():
                data = zin.read(i)
                if i.filename.endswith("ch 1.xhtml"):
                    data = data.replace(b"<!--illus:start-->kept<!--illus:end-->", b"")
                zout.writestr(i, data)
        r = F.run("verify.py", orig, bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHANGED", r.stdout)

    def test_rejects_wrong_output_extension_and_source_overwrite(self):
        orig = F.epub3(os.path.join(self.tmp, "b3.epub"))
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}])
        r = self.inject("out.html", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("writes .epub", r.stderr)
        before = F.readb(orig)
        r = self.inject(os.path.abspath(orig), ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(F.readb(orig), before)
        r = self.inject(os.path.join("src", "x.epub"), ok=False)
        self.assertNotEqual(r.returncode, 0)


class TestEpub2(EpubBase):
    def test_ncx_relative_to_ncx_dir_and_crlf_preserved(self):
        orig = F.epub2(os.path.join(self.tmp, "b2.epub"))
        b = self.extract(orig)
        self.assertEqual(b["toc_source"], "ncx")
        self.assertEqual([(t["title"], t["file"], t["depth"]) for t in b["toc"]],
                         [("第一章 起点", "Text/ch1.xhtml", 0), ("Sub", "Text/ch1.xhtml", 1),
                          ("第二章 发展", "Text/ch2.xhtml", 0)])
        F.entities(self.work, [{"id": "ding_lei", "name": "丁磊", "role": "网易创始人"},
                               {"id": "lumen", "name": "Lumen"}])
        rep = self.inject("b2_out.epub")
        self.assertEqual((rep["figures"], rep["galleries"]), (2, 2))
        z = self.assert_package_sound(rep["out"])
        ch1 = z.read("OEBPS/Text/ch1.xhtml")
        self.assertEqual(ch1.count(b"\r\n"), ch1.count(b"\n"))  # still pure CRLF
        self.assertIn(b'src="../illus/ding_lei.jpg"', ch1)
        r = F.run("verify.py", orig, rep["out"])
        self.assertEqual(r.returncode, 0, r.stdout)

    def test_epub2_never_inserts_inside_html5_section(self):
        orig = F.epub2_section(os.path.join(self.tmp, "sec.epub"))
        self.extract(orig)
        F.entities(self.work, [{"id": "alice", "name": "Alice"}, {"id": "beacon", "name": "Beacon"}])
        rep = self.inject()
        z = self.assert_package_sound(rep["out"])
        html = z.read("text/part0000.html").decode()
        self.assertIn("More original words.</p></section><!--illus:start-->", html)
        self.assertIn("Section-only Beacon.</p></section><!--illus:start-->", html)
        self.assertNotRegex(html, r"(?s)<section>(?:(?!</section>).)*<!--illus:start-->")
        self.assertEqual(F.run("verify.py", orig, rep["out"]).returncode, 0)

    def test_verify_cli_detects_corruption_and_extra_members(self):
        orig = F.epub2(os.path.join(self.tmp, "b2.epub"))
        self.extract(orig)
        F.entities(self.work, [{"id": "lumen", "name": "Lumen"}])
        out = self.inject()["out"]

        def rewrite(dst, fn, extra=None):
            with zipfile.ZipFile(out) as zin, zipfile.ZipFile(dst, "w") as zout:
                for i in zin.infolist():
                    zout.writestr(i, fn(i.filename, zin.read(i)))
                if extra:
                    zout.writestr(extra, b"x")
            return dst
        bad = rewrite(os.path.join(self.tmp, "t.epub"),
                      lambda n, d: d.replace("Lumen appears".encode(), "Lumen Appears".encode())
                      if n.endswith("ch2.xhtml") else d)
        r = F.run("verify.py", orig, bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHANGED OEBPS/Text/ch2.xhtml", r.stdout)
        bad = rewrite(os.path.join(self.tmp, "x.epub"), lambda n, d: d, extra="OEBPS/evil.js")
        r = F.run("verify.py", orig, bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("UNEXPECTED MEMBER OEBPS/evil.js", r.stdout)
        bad = rewrite(os.path.join(self.tmp, "i.epub"),
                      lambda n, d: d.replace(b"lumen.jpg", b"nothere.jpg") if n.endswith("ch2.xhtml") else d)
        r = F.run("verify.py", orig, bad)
        self.assertEqual(r.returncode, 1)
        self.assertIn("MISSING IMAGE", r.stdout)
        r = F.run("verify.py", orig, os.path.join(self.tmp, "nope.html"))
        self.assertEqual(r.returncode, 2)


class TestEpubSafety(EpubBase):
    def test_unsafe_member_rejected(self):
        bad = F.make_epub(os.path.join(self.tmp, "evil.epub"),
                          {"META-INF/container.xml": F.CONTAINER.format(opf="x.opf"), "../escape.txt": "x"})
        r = F.run("extract.py", bad, self.work)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("unsafe path", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "escape.txt")))

    def test_input_inside_replaced_dirs_rejected(self):
        for sub in ("src", "text", "build"):
            os.makedirs(os.path.join(self.work, sub), exist_ok=True)
            p = F.epub3(os.path.join(self.work, sub, "b.epub"))
            r = F.run("extract.py", p, self.work)
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue(os.path.exists(p), f"input in {sub}/ was deleted")

    def test_existing_legacy_style_illus_item_is_preserved(self):
        """A book that already has illus/old.jpg declared with the exact legacy inject.py line."""
        base = F.epub2(os.path.join(self.tmp, "base.epub"))
        line = '<item href="illus/old.jpg" id="illus-old" media-type="image/jpeg"/>\n'
        with zipfile.ZipFile(base) as z:
            files = {n: z.read(n) for n in z.namelist() if n != "mimetype"}
        files["OEBPS/content.opf"] = files["OEBPS/content.opf"].replace(b"</manifest>", line.encode() + b"</manifest>")
        files["OEBPS/illus/old.jpg"] = F.readb(F.image(os.path.join(self.tmp, "old.jpg")))
        orig = F.make_epub(os.path.join(self.tmp, "legacy.epub"), files)
        self.extract(orig)
        F.entities(self.work, [{"id": "lumen", "name": "Lumen"}])
        out = self.inject()["out"]
        z = self.assert_package_sound(out)
        self.assertIn("OEBPS/illus_2/lumen.jpg", z.namelist())  # never overwrites the book's own illus/
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 0, r.stdout)
        # an output written by the old (marker-less) inject.py: its own legacy line is stripped,
        # the original's identical-looking line is kept
        legacy_out = os.path.join(self.tmp, "legacy_out.epub")
        new_line = b'<item href="illus/lumen.jpg" id="illus-lumen" media-type="image/jpeg"/>\n'
        with zipfile.ZipFile(out) as zin:
            files = {n: zin.read(n) for n in zin.namelist() if n != "mimetype"}
        files = {n.replace("illus_2/", "illus/"): d.replace(b"illus_2/", b"illus/") for n, d in files.items()}
        with zipfile.ZipFile(orig) as zo:
            opf = zo.read("OEBPS/content.opf")
        files["OEBPS/content.opf"] = opf.replace(b"</manifest>", new_line + b"</manifest>")
        F.make_epub(legacy_out, files)
        r = F.run("verify.py", orig, legacy_out)
        self.assertEqual(r.returncode, 0, r.stdout)
        files["OEBPS/content.opf"] = opf.replace(line.encode(), b"").replace(b"</manifest>", new_line + b"</manifest>")
        F.make_epub(legacy_out, files)
        r = F.run("verify.py", orig, legacy_out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHANGED OEBPS/content.opf", r.stdout)

    def test_old_job_without_format_still_injects(self):
        orig = F.epub2(os.path.join(self.tmp, "b2.epub"))
        b = self.extract(orig)
        for k in ("format", "members", "marker", "source_path"):
            b.pop(k)
        for p in b["paras"]:
            p.pop("ins")
        F.write(os.path.join(self.work, "book.json"), json.dumps(b, ensure_ascii=False))
        F.entities(self.work, [{"id": "lumen", "name": "Lumen"}])
        rep = self.inject()
        self.assertEqual(F.run("verify.py", orig, rep["out"]).returncode, 0)


if __name__ == "__main__":
    unittest.main()
