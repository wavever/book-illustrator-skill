"""Unit and integration tests for format conversion, DRM detection, and export."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# Add project root and scripts dir to path
REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "skills" / "book-illustrator" / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(REPO_ROOT))

import conversion
from tests import conversion_helpers


def _calibre_or_none():
    """ebook-convert found the same way the scripts find it ($CALIBRE_CONVERT, PATH, default paths)."""
    try:
        return conversion.find_calibre()
    except conversion.CalibreNotFoundError:
        return None


CALIBRE = _calibre_or_none()


class TestCalibreDiscovery(unittest.TestCase):
    """Test locating and checking Calibre ebook-convert."""

    def test_find_calibre_default(self):
        try:
            calibre_bin = conversion.find_calibre()
            self.assertTrue(os.path.exists(calibre_bin))
            self.assertTrue(os.access(calibre_bin, os.X_OK))
        except conversion.CalibreNotFoundError:
            self.skipTest("Calibre not installed in default test environment")

    def test_find_calibre_custom_path_valid(self):
        if not CALIBRE:
            self.skipTest("Calibre not available")
        found = conversion.find_calibre(CALIBRE)
        self.assertEqual(Path(found).resolve(), Path(CALIBRE).resolve())

    def test_find_calibre_missing_raises_actionable_error(self):
        with self.assertRaises(conversion.CalibreNotFoundError) as ctx:
            conversion.find_calibre("/nonexistent/path/to/ebook-convert")
        msg = str(ctx.exception)
        self.assertIn("not exist or is not executable", msg.lower())
        self.assertIn("calibre-ebook.com", msg)

    def test_get_calibre_version(self):
        if not CALIBRE:
            self.skipTest("Calibre not available")
        self.assertRegex(conversion.get_calibre_version(CALIBRE), r"^\d+\.\d+")


class TestDRMDetection(unittest.TestCase):
    """Test DRM detection on EPUB, MOBI/AZW3, and PDF files."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="epub_test_drm_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_epub_drm_detection(self):
        drm_epub = self.temp_dir / "protected.epub"
        conversion_helpers.create_drm_epub(drm_epub)
        with self.assertRaises(conversion.DRMUnsupportedError) as ctx:
            conversion.check_drm(drm_epub)
        self.assertIn("DRM protection detected", str(ctx.exception))
        self.assertIn("only supports DRM-free", str(ctx.exception))

    def test_clean_epub_passes_drm_check(self):
        clean_epub = self.temp_dir / "clean.epub"
        conversion_helpers.create_synthetic_epub(clean_epub)
        conversion.check_drm(clean_epub)

    def test_mobi_drm_detection(self):
        drm_mobi = self.temp_dir / "protected.mobi"
        conversion_helpers.create_drm_mobi(drm_mobi)
        with self.assertRaises(conversion.DRMUnsupportedError) as ctx:
            conversion.check_drm(drm_mobi)
        self.assertIn("DRM", str(ctx.exception))

    def test_pdf_drm_detection(self):
        drm_pdf = self.temp_dir / "protected.pdf"
        drm_pdf.write_bytes(b"%PDF-1.4\n1 0 obj << /Encrypt 2 0 R >> endobj\ntrailer << /Encrypt 2 0 R >>\n%%EOF")
        with self.assertRaises(conversion.DRMUnsupportedError) as ctx:
            conversion.check_drm(drm_pdf)
        self.assertIn("encryption", str(ctx.exception).lower())


class TestEncodingDetection(unittest.TestCase):
    """Test character encoding detection for TXT files to avoid mojibake."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="epub_test_enc_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_utf8_detection(self):
        txt_path = self.temp_dir / "utf8.txt"
        txt_path.write_text("第一章 启程\n爱丽丝在1998年创办了实验室。", encoding="utf-8")
        enc = conversion.detect_txt_encoding(txt_path)
        self.assertEqual(enc, "utf-8")

    def test_utf8_bom_detection(self):
        txt_path = self.temp_dir / "utf8_bom.txt"
        txt_path.write_bytes(b"\xef\xbb\xbf" + "第一章 启程".encode("utf-8"))
        enc = conversion.detect_txt_encoding(txt_path)
        self.assertEqual(enc, "utf-8-sig")

    def test_utf16_bom_detection(self):
        txt_bom_le = self.temp_dir / "utf16_le_bom.txt"
        txt_bom_le.write_bytes(b"\xff\xfe" + "第一章".encode("utf-16-le"))
        self.assertEqual(conversion.detect_txt_encoding(txt_bom_le), "utf-16-le")

        txt_bom_be = self.temp_dir / "utf16_be_bom.txt"
        txt_bom_be.write_bytes(b"\xfe\xff" + "第一章".encode("utf-16-be"))
        self.assertEqual(conversion.detect_txt_encoding(txt_bom_be), "utf-16-be")

    def test_ambiguous_non_utf_requires_explicit_encoding(self):
        txt_path = self.temp_dir / "gb18030.txt"
        txt_path.write_bytes("第一章 启程\n阿特拉斯导航系统。".encode("gb18030"))
        with self.assertRaises(ValueError) as ctx:
            conversion.detect_txt_encoding(txt_path)
        self.assertIn("specify the encoding explicitly", str(ctx.exception))

    def test_override_encoding_valid(self):
        txt_path = self.temp_dir / "custom.txt"
        txt_path.write_bytes("第一章 启程".encode("gb18030"))
        enc = conversion.detect_txt_encoding(txt_path, override_encoding="gb18030")
        self.assertEqual(enc, "gb18030")

    def test_override_encoding_invalid_raises_error(self):
        txt_path = self.temp_dir / "custom.txt"
        txt_path.write_bytes("第一章 启程".encode("gb18030"))
        with self.assertRaises(ValueError) as ctx:
            conversion.detect_txt_encoding(txt_path, override_encoding="utf-8")
        self.assertIn("failed to decode", str(ctx.exception))


class TestWorkdirSafety(unittest.TestCase):
    """Test that input files are kept untouched and not clobbered if placed inside workdir."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="epub_test_safety_"))

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_input_inside_workdir_preserved(self):
        workdir = self.temp_dir / "work"
        workdir.mkdir()
        input_file = workdir / "original.txt"
        input_file.write_text("Chapter 1\nImportant source text.", encoding="utf-8")
        initial_hash = conversion.compute_sha256(input_file)

        stale_norm = workdir / "normalized.epub"
        stale_norm.write_bytes(b"stale epub content")
        stale_conv = workdir / "conversion.json"
        stale_conv.write_text("{}", encoding="utf-8")

        conversion.safe_cleanup_workdir(workdir, preserve_paths={input_file})

        self.assertFalse(stale_norm.exists())
        self.assertFalse(stale_conv.exists())
        self.assertTrue(input_file.exists())
        self.assertEqual(conversion.compute_sha256(input_file), initial_hash)

    def test_nested_input_directory_preserved(self):
        workdir = self.temp_dir / "work"
        workdir.mkdir()
        nested_dir = workdir / "inputs"
        nested_dir.mkdir()
        input_file = nested_dir / "book.txt"
        input_file.write_text("Chapter 1\nNested content.", encoding="utf-8")

        # Stale src directory should be removed
        (workdir / "src").mkdir()
        (workdir / "src" / "stale.html").write_text("old", encoding="utf-8")

        conversion.safe_cleanup_workdir(workdir, preserve_paths={input_file})
        self.assertFalse((workdir / "src").exists())
        self.assertTrue(input_file.exists())


class TestPrepareCLI(unittest.TestCase):
    """Test prepare.py execution, delegation, and safety."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="epub_test_prepare_"))
        self.workdir = self.temp_dir / "job"
        self.prepare_script = SCRIPTS_DIR / "prepare.py"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_nonexistent_input_fails(self):
        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), "/nonexistent/book.epub", str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("does not exist", proc.stderr + proc.stdout)

    def test_unsupported_input_format_fails(self):
        bad_file = self.temp_dir / "sample.xyz"
        bad_file.write_bytes(b"dummy")
        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), str(bad_file), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Unsupported input format", proc.stderr + proc.stdout)

    def test_input_inside_src_rejected(self):
        (self.workdir / "src").mkdir(parents=True)
        bad_input = self.workdir / "src" / "book.epub"
        conversion_helpers.create_synthetic_epub(bad_input)
        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), str(bad_input), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("extraction target directory", proc.stderr + proc.stdout)

    def test_native_epub_delegation(self):
        epub_path = self.temp_dir / "native.epub"
        conversion_helpers.create_synthetic_epub(epub_path)
        original_hash = conversion.compute_sha256(epub_path)

        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), str(epub_path), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"prepare.py failed: {proc.stderr}")

        self.assertTrue((self.workdir / "book.json").exists())
        self.assertTrue((self.workdir / "src").is_dir())
        self.assertTrue((self.workdir / "text").is_dir())
        self.assertFalse((self.workdir / "conversion.json").exists())
        self.assertEqual(conversion.compute_sha256(epub_path), original_hash)

    def test_convertible_txt_with_calibre(self):
        if not CALIBRE:
            self.skipTest("Calibre not available")

        txt_path = self.temp_dir / "sample.txt"
        txt_path.write_text(
            "Chapter 1\n\nAlice built a research laboratory. 爱丽丝建造了实验室。\n\n"
            "Chapter 2\n\nAtlas charted navigation paths.\n\nEND-OF-BOOK.\n",
            encoding="utf-8",
        )
        original_hash = conversion.compute_sha256(txt_path)

        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), str(txt_path), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"prepare.py failed: {proc.stderr}\n{proc.stdout}")

        normalized_epub = self.workdir / "normalized.epub"
        self.assertTrue(normalized_epub.exists())
        self.assertGreater(normalized_epub.stat().st_size, 0)

        conv_json = self.workdir / "conversion.json"
        self.assertTrue(conv_json.exists())
        data = json.loads(conv_json.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "conversion")
        self.assertEqual(data["source"]["format"], "txt")
        self.assertEqual(data["source"]["sha256"], original_hash)
        self.assertEqual(data["normalized"]["format"], "epub")
        self.assertEqual(conversion.compute_sha256(txt_path), original_hash)

    def test_pdf_prepare_passes_header_footer_skip(self):
        try:
            calibre_bin = conversion.find_calibre()
        except conversion.CalibreNotFoundError:
            self.skipTest("Calibre not available")

        # Generate synthetic EPUB and convert to text PDF inside self.temp_dir
        synth_epub = self.temp_dir / "synth_for_pdf.epub"
        conversion_helpers.create_synthetic_epub(synth_epub)
        synth_pdf = self.temp_dir / "synth.pdf"
        p_conv = subprocess.run(
            [calibre_bin, str(synth_epub), str(synth_pdf)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(p_conv.returncode, 0, p_conv.stderr + p_conv.stdout)
        self.assertTrue(synth_pdf.exists())

        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), str(synth_pdf), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"prepare.py failed on synthetic PDF: {proc.stderr}\n{proc.stdout}")

        conv_json = self.workdir / "conversion.json"
        self.assertTrue(conv_json.exists())
        data = json.loads(conv_json.read_text(encoding="utf-8"))
        cmd_args = data.get("tool", {}).get("command", [])
        self.assertIn("--pdf-header-skip", cmd_args)
        self.assertIn("--pdf-footer-skip", cmd_args)

    def test_scanned_pdf_rejected(self):
        if not CALIBRE:
            self.skipTest("Calibre not available")

        scanned_pdf = self.temp_dir / "scanned.pdf"
        conversion_helpers.create_scanned_pdf(scanned_pdf)

        proc = subprocess.run(
            [sys.executable, str(self.prepare_script), str(scanned_pdf), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Scanned or textless PDF detected", proc.stderr + proc.stdout)
        self.assertIn("OCR", proc.stderr + proc.stdout)


class TestExportCLI(unittest.TestCase):
    """Test export.py validation, same-native, and cross-format exports."""

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="epub_test_export_"))
        self.workdir = self.temp_dir / "job"
        self.workdir.mkdir()
        self.export_script = SCRIPTS_DIR / "export.py"
        self.prepare_script = SCRIPTS_DIR / "prepare.py"

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_missing_book_json_fails(self):
        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("book.json not found", proc.stderr + proc.stdout)

    def test_missing_entities_json_fails(self):
        (self.workdir / "book.json").write_text("{}", encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("entities.json not found", proc.stderr + proc.stdout)

    def test_missing_candidate_image_fails(self):
        (self.workdir / "book.json").write_text(json.dumps({"format": "epub", "opf": "content.opf"}), encoding="utf-8")
        entities_data = {
            "book": "Test Book",
            "entities": [
                {"id": "alice", "name": "Alice", "pick": "missing.png"}
            ]
        }
        (self.workdir / "entities.json").write_text(json.dumps(entities_data), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Missing candidate image", proc.stderr + proc.stdout)

    def test_unsupported_export_format_fails(self):
        epub_path = self.temp_dir / "input.epub"
        conversion_helpers.create_synthetic_epub(epub_path)
        subprocess.run([sys.executable, str(self.prepare_script), str(epub_path), str(self.workdir)], check=True)

        entities_data = {"book": "Test Book", "entities": []}
        (self.workdir / "entities.json").write_text(json.dumps(entities_data), encoding="utf-8")

        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir), "--out", str(self.temp_dir / "out.xyz")],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("Unsupported export format", proc.stderr + proc.stdout)

    def test_export_cannot_overwrite_baseline(self):
        epub_path = self.temp_dir / "input.epub"
        conversion_helpers.create_synthetic_epub(epub_path)
        subprocess.run([sys.executable, str(self.prepare_script), str(epub_path), str(self.workdir)], check=True)

        entities_data = {"book": "Test Book", "entities": []}
        (self.workdir / "entities.json").write_text(json.dumps(entities_data), encoding="utf-8")

        # Attempt to export with --out equal to input.epub
        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir), "--out", str(epub_path)],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("cannot overwrite the source, baseline, or intermediate file", proc.stderr + proc.stdout)

    def test_same_native_epub_export_and_provenance(self):
        epub_path = self.temp_dir / "input.epub"
        conversion_helpers.create_synthetic_epub(epub_path)
        subprocess.run([sys.executable, str(self.prepare_script), str(epub_path), str(self.workdir)], check=True)

        cand_dir = self.workdir / "candidates" / "alice"
        cand_dir.mkdir(parents=True, exist_ok=True)
        img_bytes = conversion_helpers.create_synthetic_image("ALICE PIC")
        (cand_dir / "alice.png").write_bytes(img_bytes)

        entities_data = {
            "book": "Synthetic Test Book",
            "settings": {"inline": True, "gallery": False},
            "entities": [
                {"id": "alice", "name": "Alice", "match": ["Alice"], "pick": "alice.png", "caption": "Alice · Researcher"}
            ]
        }
        (self.workdir / "entities.json").write_text(json.dumps(entities_data, ensure_ascii=False), encoding="utf-8")

        out_epub = self.temp_dir / "illustrated.epub"
        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir), "--out", str(out_epub)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"export.py failed: {proc.stderr}\n{proc.stdout}")
        self.assertTrue(out_epub.exists())
        self.assertGreater(out_epub.stat().st_size, 0)

        export_json = self.workdir / "export.json"
        self.assertTrue(export_json.exists())
        data = json.loads(export_json.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "native_export")
        self.assertEqual(data["output"]["format"], "epub")
        self.assertTrue(data["intermediate"]["native_verified"])

    def test_cross_format_export_to_docx_with_calibre(self):
        if not CALIBRE:
            self.skipTest("Calibre not available")

        epub_path = self.temp_dir / "input.epub"
        conversion_helpers.create_synthetic_epub(epub_path)
        subprocess.run([sys.executable, str(self.prepare_script), str(epub_path), str(self.workdir)], check=True)

        cand_dir = self.workdir / "candidates" / "alice"
        cand_dir.mkdir(parents=True, exist_ok=True)
        (cand_dir / "alice.png").write_bytes(conversion_helpers.create_synthetic_image("ALICE"))

        entities_data = {
            "book": "Synthetic Test Book",
            "settings": {"inline": True, "gallery": False},
            "entities": [
                {"id": "alice", "name": "Alice", "match": ["Alice"], "pick": "alice.png", "caption": "Alice · Lead"}
            ]
        }
        (self.workdir / "entities.json").write_text(json.dumps(entities_data, ensure_ascii=False), encoding="utf-8")

        out_docx = self.temp_dir / "illustrated.docx"
        proc = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir), "--out", str(out_docx)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc.returncode, 0, f"export.py failed: {proc.stderr}\n{proc.stdout}")
        self.assertTrue(out_docx.exists())
        self.assertGreater(out_docx.stat().st_size, 0)

        intermediate = self.workdir / "illustrated_intermediate.epub"
        self.assertTrue(intermediate.exists())

        export_json = self.workdir / "export.json"
        self.assertTrue(export_json.exists())
        data = json.loads(export_json.read_text(encoding="utf-8"))
        self.assertEqual(data["mode"], "cross_format_export")
        self.assertEqual(data["output"]["format"], "docx")
        self.assertTrue(data["intermediate"]["native_verified"])

    def test_native_xhtml_default_export_and_cross_export(self):
        xhtml_path = self.temp_dir / "lab.xhtml"
        xhtml_path.write_text(
            '<!DOCTYPE html>\n<html xmlns="http://www.w3.org/1999/xhtml">\n'
            '<head><title>Test Lab XHTML</title></head>\n<body>\n'
            '<h1>Chapter 1</h1>\n<p>Alice founded a research laboratory in 1998.</p>\n'
            '</body></html>\n',
            encoding="utf-8",
        )
        proc_prep = subprocess.run(
            [sys.executable, str(self.prepare_script), str(xhtml_path), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_prep.returncode, 0, f"prepare failed on xhtml: {proc_prep.stderr}")

        cand_dir = self.workdir / "candidates" / "alice"
        cand_dir.mkdir(parents=True, exist_ok=True)
        (cand_dir / "alice.png").write_bytes(conversion_helpers.create_synthetic_image("ALICE"))

        entities_data = {
            "book": "Test Lab XHTML",
            "settings": {"inline": True, "gallery": False},
            "entities": [
                {"id": "alice", "name": "Alice", "match": ["Alice"], "pick": "alice.png", "caption": "Alice · Lead"}
            ],
        }
        (self.workdir / "entities.json").write_text(json.dumps(entities_data, ensure_ascii=False), encoding="utf-8")

        # 1. Default export must produce .xhtml (not .html)
        proc_def = subprocess.run(
            [sys.executable, str(self.export_script), str(self.workdir)],
            capture_output=True,
            text=True,
        )
        self.assertEqual(proc_def.returncode, 0, f"export.py default failed: {proc_def.stderr}\n{proc_def.stdout}")
        expected_xhtml = self.workdir / "Test_Lab_XHTML_插图版.xhtml"
        self.assertTrue(expected_xhtml.exists())

        # 2. Cross-format export to EPUB must use .xhtml intermediate and succeed
        if CALIBRE:
            out_epub = self.temp_dir / "from_xhtml.epub"
            proc_epub = subprocess.run(
                [sys.executable, str(self.export_script), str(self.workdir), "--out", str(out_epub)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(proc_epub.returncode, 0, f"export to epub failed: {proc_epub.stderr}\n{proc_epub.stdout}")
            self.assertTrue(out_epub.exists())
            intermediate = self.workdir / "illustrated_intermediate.xhtml"
            self.assertTrue(intermediate.exists())


if __name__ == "__main__":
    unittest.main()
