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
import native_md  # noqa: E402

MD = ("\ufeff# 第一章 Start\r\n\r\n"
      "Intro mentions Beacon here and `NOTME` code.\r\n\r\n"
      "```\r\nBeacon NOTME in fence\r\n[photo]: evil.png\r\n```\r\n\r\n"
      "- Gamma in list\r\n- second\r\n  continued\r\n\r\n"
      "![Old picture][photo]\r\n\r\n"
      "# 第二章 Next\r\n\r\n"
      "> Lumen in quote\r\n\r\n"
      "NOTME plain.\r\n\r\n"
      "[photo]: images/old.png\r\n")


class MdBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="native-md-")
        self.work = os.path.join(self.tmp, "work")
        self.srcdir = os.path.join(self.tmp, "notes")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def source(self, text=MD, name="book.md"):
        F.image(os.path.join(self.srcdir, "images", "old.png"))
        return F.write(os.path.join(self.srcdir, name), text.encode("utf-8"))

    def extract(self, path):
        r = F.run("extract.py", path, self.work)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return F.book(self.work)

    def inject(self, out):
        r = F.run("inject.py", self.work, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return json.loads(r.stdout.splitlines()[0])


class TestMarkdownParse(unittest.TestCase):
    def test_forward_reference_image(self):
        paras, _ = native_md.parse("![Alice][photo]\n\n[photo]: resources/existing.png\n")
        self.assertEqual(paras[0]["imgs"], ["resources/existing.png"])
        self.assertEqual(paras[0]["text"], "")

    def test_definition_inside_fence_ignored(self):
        paras, _ = native_md.parse("```\n[p]: evil.png\n```\n\n![x][p] text\n")
        self.assertNotIn("imgs", paras[0])

    def test_code_never_indexed(self):
        paras, _ = native_md.parse("    indented Beacon\n\n~~~py\nBeacon\n~~~\n\nUse `Beacon` and ``x ` Beacon``.\n")
        self.assertEqual([p["text"] for p in paras], ["Use and ."])

    def test_list_and_quote_insert_after_container(self):
        s = "- a\n- b\n\n  para in b\n\n> q1\n>\n> q2\nlazy\n\nend\n"
        paras, _ = native_md.parse(s)
        by = {p["text"]: p for p in paras}
        self.assertEqual(by["a"]["ins"], s.index("\n> q1"))
        self.assertEqual(by["para in b"]["ins"], s.index("\n> q1"))
        self.assertEqual(by["q2 lazy"]["ins"], s.index("\nend"))
        self.assertEqual(by["a"]["tag"], "li")

    def test_setext_table_and_eof(self):
        s = "Title\n=====\n\n| a | b |\n|---|---|\n| Beacon | 2 |"
        paras, _ = native_md.parse(s)
        self.assertEqual(paras[0]["tag"], "h1")
        self.assertEqual(paras[1]["tag"], "table")
        self.assertIsNone(paras[1]["ins"])  # appending to a table row without a newline is unsafe
        paras, _ = native_md.parse("Para Beacon")
        self.assertEqual(paras[0]["ins"], len("Para Beacon"))


class TestMarkdown(MdBase):
    def test_crlf_bom_code_lists_and_verify(self):
        orig = self.source()
        b = self.extract(orig)
        self.assertEqual(b["format"], "markdown")
        self.assertEqual((b["source"]["newline"], b["source"]["bom"]), ("\r\n", True))
        self.assertEqual([t["title"] for t in b["toc"]], ["第一章 Start", "第二章 Next"])
        texts = [p["text"] for p in b["paras"]]
        self.assertFalse(any("in fence" in t for t in texts))
        self.assertIn("Intro mentions Beacon here and code.", texts)
        self.assertEqual([p["imgs"] for p in b["paras"] if p.get("imgs")], [["images/old.png"]])
        self.assertEqual(b["source"]["resources"], ["images/old.png"])
        self.assertTrue(os.path.isfile(os.path.join(self.work, "src", "images", "old.png")))

        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}, {"id": "gamma", "name": "Gamma"},
                               {"id": "notme", "name": "NOTME", "caption": "Not *me* [x]"},
                               {"id": "lumen", "name": "Lumen"}])
        out = os.path.join(self.tmp, "out", "book_illus.md")
        rep = self.inject(out)
        self.assertEqual((rep["figures"], rep["galleries"]), (4, 2))
        data = F.readb(out)
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        self.assertEqual(data.count(b"\r\n"), data.count(b"\n"))  # still CRLF only
        text = data.decode("utf-8")
        # list: the figure follows the whole list, never splits it
        self.assertIn("  continued\r\n<!--illus:start-->", text)
        # NOTME placed at the plain mention, not inside code
        self.assertIn("NOTME plain.\r\n<!--illus:start-->", text)
        self.assertIn(r"*Not \*me\* \[x\]*", text)
        refs = re.findall(r"!\[[^\]]*\]\(([^)]+)\)", text)
        self.assertEqual(len(refs), 4 + 4)  # 4 inline + 2 galleries x 2
        for ref in refs:
            self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", unquote(ref))), ref)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "out", "images", "old.png")))
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 0, r.stdout)
        F.write(out, data.replace(b"Lumen in quote", b"Lumen in Quote"))
        r = F.run("verify.py", orig, out)
        self.assertEqual(r.returncode, 1)
        self.assertIn("CHANGED", r.stdout)

    def test_lf_without_final_newline(self):
        orig = self.source("# 第一章 A\n\nfirst\n\nLast mention of Beacon", name="n.markdown")
        self.extract(orig)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}])
        out = os.path.join(self.tmp, "o", "n.markdown")
        rep = self.inject(out)
        self.assertEqual((rep["figures"], rep["galleries"]), (1, 1))
        text = F.readb(out).decode("utf-8")
        self.assertNotIn("\r", text)
        self.assertTrue(text.endswith("<!--illus:end-->"))
        self.assertEqual(F.run("verify.py", orig, out).returncode, 0)

    def test_reject_conversion_extension(self):
        self.extract(self.source())
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}])
        r = F.run("inject.py", self.work, "--out", "x.html")
        self.assertNotEqual(r.returncode, 0)
        r = F.run("verify.py", os.path.join(self.srcdir, "book.md"), os.path.join(self.tmp, "x.html"))
        self.assertEqual(r.returncode, 2)

    def test_input_in_text_dir_survives(self):
        p = self.source()
        nested = F.write(os.path.join(self.work, "text", "book.md"), F.readb(p))
        r = F.run("extract.py", nested, self.work)
        self.assertNotEqual(r.returncode, 0)
        self.assertTrue(os.path.isfile(nested))


if __name__ == "__main__":
    unittest.main()
