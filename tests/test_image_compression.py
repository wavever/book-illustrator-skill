"""Compression of the pictures inject.py inserts (compress.py): presets, best-effort byte targets,
orientation/crop/alpha handling, metadata removal, config validation and the report, on synthetic
images and books only."""
import io
import json
import os
import random
import re
import shutil
import stat
import sys
import tempfile
import unittest
import zipfile

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_fixtures as F  # noqa: E402
import compress  # noqa: E402


def photo(size=(2400, 1800), seed=1):
    """Smooth gradient plus mild noise: compresses like a photograph, not like flat colour."""
    w, h = size
    grad = Image.linear_gradient("L").resize(size)
    noise = Image.effect_noise(size, 40)
    rnd = random.Random(seed)
    tint = tuple(rnd.randrange(60, 200) for _ in range(3))
    return Image.merge("RGB", (grad, noise, Image.new("L", size, tint[2]))).convert("RGB")


def noise(size, seed=2):
    rnd = random.Random(seed)
    return Image.frombytes("RGB", size, bytes(rnd.getrandbits(8) for _ in range(size[0] * size[1] * 3)))


def save(im, path, fmt=None, **kw):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    im.save(path, fmt, **kw)
    return path


def exif(orientation=None):
    ex = Image.Exif()
    if orientation:
        ex[0x0112] = orientation
    ex[0x010F] = "SecretCam"  # Make
    return ex.tobytes()


class TestResolve(unittest.TestCase):
    def test_default_and_presets(self):
        self.assertEqual(compress.resolve(None)["preset"], "balanced")
        for name, values in compress.PRESETS.items():
            with self.subTest(name):
                opts = compress.resolve(name)
                self.assertEqual({k: opts[k] for k in compress.FIELDS}, values)
                self.assertEqual(compress.resolve({"preset": name})["overrides"], {})
        opts = compress.resolve({"preset": "compact", "target_kb": 50.5, "quality": 70})
        self.assertEqual((opts["target_kb"], opts["quality"], opts["max_side"]), (50.5, 70, 800))
        self.assertIsNone(compress.resolve({"target_kb": None})["target_kb"])

    def test_invalid(self):
        bad = ["tiny", 42, True, ["balanced"], {"preset": "x"}, {"preset": 1}, {"foo": 1},
               {"quality": True}, {"quality": 80.0}, {"quality": 0}, {"quality": 96}, {"max_side": "1024"},
               {"max_side": -1}, {"target_kb": -1}, {"target_kb": 0}, {"target_kb": float("nan")},
               {"target_kb": float("inf")}, {"target_kb": True}, {"min_side": 2000},
               {"min_quality": 90}, {"max_side": 600, "preset": "high"}]
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(compress.ConfigError) as cm:
                    compress.resolve(value)
                self.assertIn("settings.image_compression", str(cm.exception))


class TestCompress(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compress-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def p(self, name):
        return os.path.join(self.tmp, name)

    def test_every_preset_shrinks_a_large_photo_within_target(self):
        src = save(photo(), self.p("big.png"))
        before = F.readb(src)
        for name in compress.PRESETS:
            with self.subTest(name):
                opts = compress.resolve(name)
                data, rep = compress.compress(src, opts)
                im = Image.open(io.BytesIO(data))
                self.assertEqual(im.format, "JPEG")
                self.assertLessEqual(max(im.size), opts["max_side"])
                self.assertEqual(max(im.size), max(rep["output"]["width"], rep["output"]["height"]))
                self.assertLessEqual(len(data), opts["target_kb"] * 1024)
                self.assertTrue(rep["target_met"])
                self.assertTrue(opts["min_quality"] <= rep["quality"] <= opts["quality"])
                self.assertFalse(im.info.get("progressive"))
        self.assertEqual(F.readb(src), before)  # candidate untouched

    def test_unreachable_target_stops_at_floors_and_says_so(self):
        src = save(noise((1600, 1200)), self.p("noise.png"))
        opts = compress.resolve({"target_kb": 5})
        data, rep = compress.compress(src, opts)
        self.assertFalse(rep["target_met"])
        self.assertEqual(rep["quality"], opts["min_quality"])
        self.assertEqual(max(rep["output"]["width"], rep["output"]["height"]), opts["min_side"])
        self.assertGreater(len(data), 5 * 1024)
        self.assertEqual(rep["output"]["bytes"], len(data))

    def test_tiny_image_is_not_upscaled(self):
        src = save(Image.new("RGB", (64, 48), (10, 120, 200)), self.p("tiny.png"))
        data, rep = compress.compress(src, compress.resolve("high"))
        self.assertEqual(Image.open(io.BytesIO(data)).size, (64, 48))
        self.assertIsNone(rep["resized"])
        self.assertEqual(rep["quality"], 88)  # no quality spent where the budget is not tight

    def test_small_jpeg_keeps_image_data_and_drops_metadata(self):
        src = save(photo((500, 400)), self.p("small.jpg"), "JPEG", quality=70, exif=exif(1),
                   comment=b"private note", xmp=b"<x:xmpmeta>gps</x:xmpmeta>")
        data, rep = compress.compress(src, compress.resolve("balanced"))
        self.assertTrue(rep["kept_original_jpeg"])
        self.assertIsNone(rep["quality"])
        self.assertLess(len(data), os.path.getsize(src))  # never inflated
        out = Image.open(io.BytesIO(data))
        self.assertFalse(set(out.info) & {"exif", "comment", "xmp"})
        with Image.open(src) as orig_im:
            self.assertEqual(out.tobytes(), orig_im.tobytes())  # identical pixels
        self.assertNotIn(b"SecretCam", data)

    def test_reencode_strips_metadata(self):
        src = save(photo((2000, 1500)), self.p("cam.jpg"), "JPEG", quality=95, exif=exif(1), comment=b"note")
        data, rep = compress.compress(src, compress.resolve("balanced"))
        self.assertFalse(rep["kept_original_jpeg"])
        self.assertFalse(set(Image.open(io.BytesIO(data)).info) & {"exif", "comment", "xmp"})
        self.assertNotIn(b"SecretCam", data)

    def red_blue_rotated(self, name="rot.jpg"):
        """Stored 200x100, left half red / right half blue, EXIF 6: upright 100x200, red on top."""
        im = Image.new("RGB", (200, 100), (220, 0, 0))
        im.paste((0, 0, 220), (100, 0, 200, 100))
        return save(im, self.p(name), "JPEG", quality=95, exif=exif(6))

    def test_exif_orientation_and_crop_use_the_upright_frame(self):
        src = self.red_blue_rotated()
        data, rep = compress.compress(src, compress.resolve("balanced"))
        out = Image.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(out.size, (100, 200))
        self.assertTrue(rep["oriented"])
        self.assertFalse(rep["kept_original_jpeg"])  # rotation must be baked in, not left to EXIF
        self.assertGreater(out.getpixel((50, 20))[0], 150)
        self.assertGreater(out.getpixel((50, 180))[2], 150)
        data, rep = compress.compress(src, compress.resolve("balanced"), crop=[0, 0, 1, 0.5])
        out = Image.open(io.BytesIO(data)).convert("RGB")
        self.assertEqual(out.size, (100, 100))
        self.assertTrue(rep["cropped"])
        r, g, b = out.getpixel((50, 80))
        self.assertTrue(r > 150 and b < 80, (r, g, b))  # the upper (red) half, as the sheet shows it

    def test_sheet_shows_the_same_upright_frame(self):
        work = os.path.join(self.tmp, "w")
        shutil.copy(self.red_blue_rotated(), F.write(os.path.join(work, "candidates", "x", "rot.jpg"), b""))
        F.write(os.path.join(work, "entities.json"), json.dumps({"entities": [{"id": "x", "name": "X"}]}))
        r = F.run("sheet.py", work)
        self.assertEqual(r.returncode, 0, r.stderr)
        with Image.open(os.path.join(work, "sheets", "sheet_01.jpg")) as sim:
            sheet = sim.convert("RGB")
        cell, head = 280, 26  # sheet.py layout: first cell, thumbnail centred
        top, bottom = sheet.getpixel((cell // 2, head + cell // 4)), sheet.getpixel((cell // 2, head + 3 * cell // 4))
        self.assertTrue(top[0] > 150 and top[2] < 80, top)
        self.assertTrue(bottom[2] > 150 and bottom[0] < 80, bottom)

    def test_alpha_and_palette_are_filled_white(self):
        rgba = Image.new("RGBA", (120, 80), (0, 0, 0, 0))
        rgba.paste((0, 150, 0, 255), (60, 0, 120, 80))
        pal = rgba.convert("RGB").convert("P", palette=Image.ADAPTIVE)
        pal.info["transparency"] = pal.getpixel((0, 0))
        cases = [("rgba.png", rgba, True), ("pal.png", pal, True), ("gif.gif", pal, True),
                 ("opaque.png", Image.new("RGBA", (50, 50), (5, 5, 5, 255)), False),
                 ("la.png", Image.new("LA", (50, 50), (0, 0)), True)]
        for name, im, flattened in cases:
            with self.subTest(name):
                kw = {"transparency": im.info["transparency"]} if "transparency" in im.info else {}
                src = save(im, self.p(name), **kw)
                data, rep = compress.compress(src, compress.resolve("balanced"))
                out = Image.open(io.BytesIO(data)).convert("RGB")
                self.assertEqual(rep["flattened"], flattened)
                if flattened:
                    self.assertTrue(all(v > 235 for v in out.getpixel((5, 5))), out.getpixel((5, 5)))

    def test_strip_rejects_what_it_does_not_understand(self):
        self.assertIsNone(compress.strip_jpeg_metadata(b"not a jpeg"))
        good = F.readb(save(photo((40, 30)), self.p("t.jpg"), "JPEG"))
        self.assertIsNone(compress.strip_jpeg_metadata(good[:len(good) // 2]))
        self.assertTrue(compress.strip_jpeg_metadata(good + b"trailer").endswith(b"\xff\xd9"))


def epub_images(path):
    with zipfile.ZipFile(path) as z:
        return {n: z.read(n) for n in z.namelist() if re.search(r"/illus/[^/]+\.jpg$", n)}


def book_text(path):
    with zipfile.ZipFile(path) as z:
        return "".join(z.read(n).decode("utf-8") for n in z.namelist() if n.endswith(".xhtml"))


class TestInjectCompression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="compress-inject-")
        self.work = os.path.join(self.tmp, "w")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def epub_job(self, image_compression=None, **ent_extra):
        self.orig = F.epub3(os.path.join(self.tmp, "b.epub"))
        self.assertEqual(F.run("extract.py", self.orig, self.work).returncode, 0)
        ents = [{"id": "beacon", "name": "Beacon", **ent_extra}, {"id": "lumen", "name": "Lumen"}]
        settings = {} if image_compression is None else {"image_compression": image_compression}
        F.entities(self.work, ents, **settings)
        save(photo(), os.path.join(self.work, "candidates", "beacon", "a.png"))  # large candidate

    def inject(self, out):
        r = F.run("inject.py", self.work, "--out", out)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        return json.loads(r.stdout.splitlines()[0])

    def report(self):
        with open(os.path.join(self.work, "compression-report.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_epub_default_balanced_report_and_shared_assets(self):
        self.epub_job()
        cand = F.readb(os.path.join(self.work, "candidates", "beacon", "a.png"))
        out = os.path.join(self.tmp, "o.epub")
        rep = self.inject(out)
        v = F.run("verify.py", self.orig, out)
        self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
        for k in ("format", "figures", "galleries", "credits", "out", "marker"):
            self.assertIn(k, rep)  # existing output shape kept
        c = rep["compression"]
        self.assertEqual((c["preset"], c["images"], c["targets_unmet"]), ("balanced", 2, 0))
        imgs = epub_images(out)
        self.assertEqual(len(imgs), 2)  # inline figure and gallery share one file per entity
        self.assertEqual(c["image_bytes"], sum(map(len, imgs.values())))
        self.assertLess(c["image_bytes"], c["candidate_bytes"])
        self.assertEqual(c["output_bytes"], os.path.getsize(out))
        with zipfile.ZipFile(out) as z, zipfile.ZipFile(self.orig) as o:
            self.assertEqual(z.read("OPS/images/old.png"), o.read("OPS/images/old.png"))  # book image as-is
            refs = re.findall(r"illus/beacon\.jpg", "".join(z.read(n).decode() for n in z.namelist()
                                                             if n.endswith(".xhtml")))
        self.assertEqual(len(refs), 2)
        report = self.report()
        self.assertEqual(report["totals"], {k: c[k] for k in report["totals"]})
        row = next(r for r in report["images"] if r["entity"] == "beacon")
        self.assertEqual((row["original"]["width"], row["original"]["bytes"]), (2400, len(cand)))
        self.assertLessEqual(max(row["output"]["width"], row["output"]["height"]), 1024)
        self.assertTrue(row["target_met"])
        self.assertEqual(F.readb(os.path.join(self.work, "candidates", "beacon", "a.png")), cand)
        with open(os.path.join(self.work, "配图清单.md"), encoding="utf-8") as f:
            listing = f.read()
        self.assertIn("图片压缩（balanced）：2 张新插图", listing)
        self.assertIn(f"（质量 {row['quality']}）", listing)
        self.assertIn(f"（质量 {row['quality']}）", book_text(out))

    def test_exif_crop_through_inject_and_unmet_flag(self):
        self.epub_job({"preset": "compact", "target_kb": 1, "min_side": 480}, crop=[0, 0, 1, 0.5])
        src = os.path.join(self.work, "candidates", "beacon", "a.jpg")
        im = Image.new("RGB", (1200, 600), (220, 0, 0))
        im.paste((0, 0, 220), (600, 0, 1200, 600))
        save(im, src, "JPEG", exif=exif(6))
        with open(os.path.join(self.work, "entities.json"), encoding="utf-8") as f:
            cfg = json.load(f)
        cfg["entities"][0]["pick"] = "a.jpg"
        F.write(os.path.join(self.work, "entities.json"), json.dumps(cfg))
        out = os.path.join(self.tmp, "o.epub")
        rep = self.inject(out)
        self.assertEqual(F.run("verify.py", self.orig, out).returncode, 0)
        row = next(r for r in self.report()["images"] if r["entity"] == "beacon")
        self.assertFalse(row["target_met"])  # 1 KiB is impossible: reported, not hidden
        self.assertGreaterEqual(rep["compression"]["targets_unmet"], 1)
        b = Image.open(io.BytesIO(epub_images(out)["OPS/illus/beacon.jpg"])).convert("RGB")
        self.assertEqual(b.size, (480, 480))  # upright 600x1200, upper half, shrunk to min_side
        self.assertGreater(b.getpixel((240, 400))[0], 150)
        credits = book_text(out)
        self.assertIn("按 EXIF 方向转正", credits)
        self.assertIn("已裁剪", credits)
        with open(os.path.join(self.work, "配图清单.md"), encoding="utf-8") as f:
            self.assertIn("未达到", f.read())

    def test_invalid_config_fails_before_any_output(self):
        for value in ("tiny", {"quality": 80.5}, {"min_quality": 90}, {"target_kb": -3}):
            with self.subTest(value=value):
                shutil.rmtree(self.work, ignore_errors=True)
                self.epub_job(value)
                out = os.path.join(self.tmp, "bad.epub")
                r = F.run("inject.py", self.work, "--out", out)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("settings.image_compression", r.stderr)
                self.assertFalse(os.path.exists(out))
                self.assertFalse(os.path.exists(os.path.join(self.work, "compression-report.json")))
                self.assertFalse(os.path.exists(os.path.join(self.work, "build")))

    def test_report_path_cannot_redirect_into_candidates(self):
        self.epub_job()
        cand = os.path.join(self.work, "candidates", "lumen", "a.png")
        before = F.readb(cand)
        os.symlink(cand, os.path.join(self.work, "compression-report.json"))
        r = F.run("inject.py", self.work, "--out", os.path.join(self.tmp, "o.epub"))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("compression-report.json", r.stderr)
        self.assertEqual(F.readb(cand), before)
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "o.epub")))

    def test_html_and_markdown_preserved_and_compressed(self):
        site = os.path.join(self.tmp, "site")
        F.image(os.path.join(site, "img", "old.png"))
        docs = {"book.html": "<html><body><h1>Chapter One</h1>\n<p>Beacon here.</p>\n"
                             "<p><img src=\"img/old.png\"></p>\n</body></html>\n",
                "book.md": "# 第一章 Start\n\nBeacon here.\n\n![old](img/old.png)\n"}
        for name, text in docs.items():
            with self.subTest(name):
                shutil.rmtree(self.work, ignore_errors=True)
                src = F.write(os.path.join(site, name), text)
                self.assertEqual(F.run("extract.py", src, self.work).returncode, 0)
                F.entities(self.work, [{"id": "beacon", "name": "Beacon"}], chapter_re="^Chapter|^第",
                           image_compression="compact")
                save(photo(), os.path.join(self.work, "candidates", "beacon", "a.png"))
                ext = os.path.splitext(name)[1]
                out = os.path.join(self.tmp, "out" + name.replace(".", "_"), "o" + ext)
                rep = self.inject(out)
                v = F.run("verify.py", src, out)
                self.assertEqual(v.returncode, 0, v.stdout + v.stderr)
                asset = os.path.join(rep["assets"], "beacon.jpg")
                self.assertLessEqual(os.path.getsize(asset), 80 * 1024)
                with Image.open(asset) as aimg:
                    self.assertLessEqual(max(aimg.size), 800)
                self.assertEqual(rep["compression"]["image_bytes"], os.path.getsize(asset))
                self.assertEqual(F.readb(os.path.join(os.path.dirname(out), "img", "old.png")),
                                 F.readb(os.path.join(site, "img", "old.png")))

    def test_html_resource_named_like_the_report_is_a_conflict(self):
        site = os.path.join(self.tmp, "site")
        F.write(os.path.join(site, "compression-report.json"), "body{}\n")
        src = F.write(os.path.join(site, "book.html"),
                      '<html><head><link rel="stylesheet" href="compression-report.json"></head><body>'
                      "<h1>Chapter One</h1>\n<p>Beacon here.</p>\n</body></html>\n")
        self.assertEqual(F.run("extract.py", src, self.work).returncode, 0)
        F.entities(self.work, [{"id": "beacon", "name": "Beacon"}], chapter_re="^Chapter")
        out = os.path.join(self.work, "o.html")  # resources would be copied into WORKDIR itself
        r = F.run("inject.py", self.work, "--out", out)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("compression report", r.stderr)
        self.assertFalse(os.path.exists(out))

    def test_calibre_export_converts_the_compressed_intermediate(self):
        self.epub_job("compact")
        stub = F.write(os.path.join(self.tmp, "stub", "ebook-convert"), (
            f"#!{sys.executable}\nimport shutil, sys, zipfile\n"
            "if sys.argv[1] == '--version':\n    print('ebook-convert (calibre 7.0.0)'); sys.exit(0)\n"
            "shutil.copy(sys.argv[1], sys.argv[2] + '.input')\n"
            "with zipfile.ZipFile(sys.argv[2], 'w') as z:\n    z.writestr('word/document.xml', '<w/>')\n"))
        os.chmod(stub, os.stat(stub).st_mode | stat.S_IEXEC)
        out = os.path.join(self.tmp, "o.docx")
        r = F.run("export.py", self.work, "--out", out, "--calibre", stub)
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        inter = os.path.join(self.work, "illustrated_intermediate.epub")
        report = self.report()
        self.assertEqual(report["out"], os.path.realpath(inter))
        imgs = epub_images(inter)
        self.assertEqual(report["totals"]["image_bytes"], sum(map(len, imgs.values())))
        self.assertTrue(all(len(d) <= 80 * 1024 for d in imgs.values()))
        given = [f for f in os.listdir(self.tmp) if f.endswith(".input")]
        self.assertEqual(F.readb(os.path.join(self.tmp, given[0])), F.readb(inter))  # Calibre got that file


if __name__ == "__main__":
    unittest.main()
