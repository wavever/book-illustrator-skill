"""examples/render-comparison.py: multi-section excerpts are verified one by one, images resolve against
their own XHTML member, and non-adjacent passages are never shown as continuous text. Synthetic EPUBs, no Chrome."""
import base64
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location("render_comparison", os.path.join(ROOT, "examples", "render-comparison.py"))
rc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rc)

P = ["<p>Alpha one.</p>", "<p>Alpha two.</p>", "<p>Alpha three.</p>"]
Q = ["<p>Beta one.</p>", "<p>Beta two.</p>"]
FIG = '<!--illus:start--><div class="illus-figure"><img src="{src}" alt="x"/></div><!--illus:end-->'


def paras(file, parts, first_pid):
    out, pos = [], 0
    for n, part in enumerate(parts):
        out.append({"pid": f"p{first_pid + n:05d}", "file": file, "start": pos, "end": pos + len(part),
                    "ins": pos + len(part)})
        pos += len(part) + 1  # newline between paragraphs
    return out


class TestRenderSections(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        a, b = "\n".join(P), "\n".join(Q)
        # Same image basename in two directories: each member must get its own bytes
        a_ill = "\n".join([P[0] + FIG.format(src="illus/x.jpg"), P[1], P[2]])
        b_ill = "\n".join([Q[0], Q[1] + FIG.format(src="../illus/x.jpg")])
        self.orig, self.illus = self.path("o.epub"), self.path("i.epub")
        self.write_epub(self.orig, {"OEBPS/a.xhtml": a, "OEBPS/part/b.xhtml": b})
        self.write_epub(self.illus, {"OEBPS/a.xhtml": a_ill, "OEBPS/part/b.xhtml": b_ill,
                                     "OEBPS/illus/x.jpg": b"A-BYTES", "OEBPS/part/illus/x.jpg": b"WRONG",
                                     "OEBPS/x.jpg": b"WRONG2"})
        self.bj = self.path("book.json")
        with open(self.bj, "w", encoding="utf-8") as f:
            json.dump({"opf": "OEBPS/content.opf", "paras": paras("a.xhtml", P, 1) + paras("part/b.xhtml", Q, 4)}, f)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name):
        return os.path.join(self.tmp, name)

    @staticmethod
    def write_epub(path, members):
        with zipfile.ZipFile(path, "w") as z:
            for k, v in members.items():
                z.writestr(k, v)

    def sections(self, *secs):
        return rc.extract_sections(self.orig, self.illus, self.bj, [{"label": label, "pids": p} for label, p in secs])

    def test_sections_across_members_verified_and_images_resolved_per_member(self):
        s = self.sections(("Ch 1", ["p00001", "p00002"]), ("Ch 2", ["p00005"]))
        self.assertEqual([x["figures"] for x in s], [1, 1])
        self.assertEqual(s[0]["member"], "OEBPS/a.xhtml")
        self.assertEqual(s[1]["member"], "OEBPS/part/b.xhtml")
        a_data = base64.b64encode(b"A-BYTES").decode()
        self.assertIn(a_data, s[0]["illus_html"])
        self.assertIn(a_data, s[1]["illus_html"])  # ../illus/x.jpg from part/ -> OEBPS/illus/x.jpg
        for wrong in (b"WRONG", b"WRONG2"):
            self.assertNotIn(base64.b64encode(wrong).decode(), s[0]["illus_html"] + s[1]["illus_html"])
        self.assertNotIn("Alpha three", s[0]["orig_html"] + s[0]["illus_html"])
        self.assertFalse(s[1]["adjacent"])

    def test_gap_marked_between_nonadjacent_sections_only(self):
        s = self.sections(("A", ["p00001"]), ("B", ["p00002"]), ("C", ["p00003"]))
        self.assertEqual([x["adjacent"] for x in s], [False, True, True])
        s = self.sections(("A", ["p00001"]), ("C", ["p00003"]))
        page = rc.build_comparison_html("T", "S", "M", "Original", "Illustrated", lang="en", sections=s)
        self.assertEqual(page.count('class="gap"'), 1)
        self.assertEqual(page.count('class="sec-label"'), 2)
        self.assertTrue(page.split("<head>", 1)[1].lstrip().startswith('<meta http-equiv="Content-Security-Policy"'))

    def test_section_must_be_consecutive_and_in_one_file(self):
        with self.assertRaises(ValueError):
            self.sections(("skip", ["p00001", "p00003"]))
        with self.assertRaises(ValueError):
            self.sections(("two files", ["p00003", "p00004"]))
        with self.assertRaises(ValueError):
            self.sections(("missing", ["p09999"]))

    def test_changed_text_in_one_section_fails(self):
        tampered = "\n".join([P[0] + FIG.format(src="illus/x.jpg"), P[1].replace("two", "2"), P[2]])
        self.write_epub(self.illus, {"OEBPS/a.xhtml": tampered, "OEBPS/part/b.xhtml": "\n".join(Q)})
        self.sections(("ok", ["p00004"]))
        with self.assertRaises(AssertionError):
            self.sections(("ok", ["p00004"]), ("bad", ["p00002"]))

    def test_legacy_pids_and_layout_validation(self):
        self.assertEqual(rc.normalize_sections({"pids": ["p00001"]}), [{"label": None, "pids": ["p00001"]}])
        orig, illus, _ = rc.extract_fragments(self.orig, self.illus, self.bj, ["p00001"])
        self.assertEqual(rc.BLOCK_RE.sub("", illus), orig)
        self.assertIn("illus-figure", illus)
        self.assertEqual(rc.resolve_layout("en", {"font_size": 19})["font_size"], 19)
        for bad in ({"font_family": "x; } body { background: url(http://e)"}, {"nope": 1}):
            with self.assertRaises(ValueError):
                rc.resolve_layout("en", bad)


if __name__ == "__main__":
    unittest.main()
