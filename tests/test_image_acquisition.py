"""Offline tests for image acquisition (find_images.py, imgfetch.py, providers.py, sheet.py).

All remote APIs are mocked through imgfetch.Http(opener=...); nothing touches the network,
a browser, Node or ego-browser.
"""
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import native_fixtures as F  # noqa: E402,F401  (puts scripts/ on sys.path)

import find_images as fi  # noqa: E402
import imgfetch  # noqa: E402
import providers  # noqa: E402
import sheet  # noqa: E402


def img_bytes(fmt="JPEG", size=(120, 90), color=(200, 40, 40), noise=False):
    im = Image.new("RGB", size, color)
    if noise:
        rnd = random.Random(7)
        im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, fmt, **({"quality": 95} if fmt == "JPEG" else {}))
    return buf.getvalue()


class Resp:
    def __init__(self, body, headers=None):
        self.body, self.pos, self.headers, self.reads = body, 0, headers or {}, 0

    def read(self, n=-1):
        self.reads += 1
        chunk = self.body[self.pos:] if n < 0 else self.body[self.pos:self.pos + n]
        self.pos += len(chunk)
        return chunk

    def close(self):
        pass


def http_error(url, code, headers=None):
    return urllib.error.HTTPError(url, code, "err", headers or {}, None)


class FakeNet:
    """Fake Wikidata + Commons + Openverse + image hosts. Mutate the dicts between runs."""

    def __init__(self):
        self.calls = []
        self.search = {}      # wikidata query -> [{"id","label","description"}]
        self.entities = {}    # qid -> [P18 file names]
        self.files = {}       # "File:x.jpg" -> image URL
        self.csearch = {}     # commons query -> ["File:..."]
        self.ov = {}          # openverse query -> [result dicts]
        self.images = {}      # image URL -> bytes | Exception | callable(url)
        self.fail = {}        # api name -> callable(url) -> response/exception

    def count(self, part):
        return sum(1 for u in self.calls if part in u)

    def __call__(self, req, timeout):
        url = req.full_url
        self.calls.append(url)
        parts = urllib.parse.urlsplit(url)
        q = dict(urllib.parse.parse_qsl(parts.query))
        api = {"www.wikidata.org": "wikidata", "commons.wikimedia.org": "commons",
               "api.openverse.org": "openverse"}.get(parts.netloc)
        if api and api in self.fail:
            r = self.fail[api](url)
            if isinstance(r, Exception):
                raise r
            if r is not None:
                return r
        if url.endswith("?orig"):  # Commons original-size fallback URL
            url = url[:-5]
        if api == "wikidata" and q.get("action") == "wbsearchentities":
            return self.json({"search": self.search.get(q["search"], [])})
        if api == "wikidata" and q.get("action") == "wbgetentities":
            qid = q["ids"]
            if qid not in self.entities:
                return self.json({"entities": {qid: {"id": qid, "missing": ""}}})
            claims = {"P18": [{"mainsnak": {"datavalue": {"value": f}}, "rank": "normal"} for f in self.entities[qid]]}
            return self.json({"entities": {qid: {"claims": claims, "labels": {"en": {"value": qid}}}}})
        if api == "commons":
            titles = q["titles"].split("|") if "titles" in q else self.csearch.get(q.get("gsrsearch"), [])
            pages = []
            for i, t in enumerate(titles):
                if t in self.files:
                    pages.append({"title": t, "index": i + 1, "imageinfo": [{
                        "url": self.files[t] + "?orig", "thumburl": self.files[t], "mime": "image/jpeg",
                        "descriptionurl": "https://commons.wikimedia.org/wiki/" + t,
                        "extmetadata": {"Artist": {"value": "<a href='x'>Jane Doe</a>"},
                                        "LicenseShortName": {"value": "CC BY-SA 4.0"},
                                        "LicenseUrl": {"value": "https://creativecommons.org/licenses/by-sa/4.0"}}}]})
            return self.json({"query": {"pages": pages}})
        if api == "openverse":
            return self.json({"result_count": 1, "results": self.ov.get(q["q"], [])})
        if url in self.images:
            v = self.images[url]
            v = v(url) if callable(v) else v
            if isinstance(v, Exception):
                raise v
            return Resp(v, {"Content-Type": "image/jpeg"})
        raise urllib.error.URLError("no route to " + url)

    @staticmethod
    def json(obj):
        return Resp(json.dumps(obj).encode())


def ov_result(i, url, source="flickr"):
    return {"id": f"ov{i}", "title": f"t{i}", "url": url, "thumbnail": None, "creator": "Ann",
            "license": "by", "license_version": "2.0", "license_url": "https://creativecommons.org/licenses/by/2.0/",
            "foreign_landing_url": f"https://flickr.com/p/{i}", "source": source}


class Base(unittest.TestCase):
    def setUp(self):
        self.work = tempfile.mkdtemp(prefix="imgacq-")
        self.net = FakeNet()
        self.sleeps = []
        self.logs = []

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def cfg(self, entities, **settings):
        cfg = {"book": "t", "settings": settings, "entities": entities}
        F.write(os.path.join(self.work, "entities.json"), json.dumps(cfg, ensure_ascii=False))
        return cfg

    def http(self, **limits):
        return imgfetch.Http(limits, opener=self.net, sleep=self.sleeps.append)

    def run_find(self, *args, **limits):
        return fi.main([self.work, *args], http=self.http(**limits),
                       ego_runner=lambda *a: self.fail("ego-browser must not run"))

    def d(self, eid):
        return os.path.join(self.work, "candidates", eid)

    def state(self, eid):
        with open(os.path.join(self.d(eid), "STATE.json"), encoding="utf-8") as f:
            return json.load(f)

    def source(self, eid):
        with open(os.path.join(self.d(eid), "SOURCE.json"), encoding="utf-8") as f:
            return json.load(f)

    def images(self, eid):
        return sorted(f for f in os.listdir(self.d(eid)) if not f.endswith(".json") and not f.startswith("."))


class TestInspectImage(unittest.TestCase):
    def test_truncated_jpeg_rejected_even_when_large(self):
        data = img_bytes(size=(300, 300), noise=True)
        bad = data[: len(data) // 2]
        self.assertGreater(len(bad), 8000)  # the old downloader accepted anything >= 8000 bytes
        with self.assertRaises(imgfetch.FetchError) as cm:
            imgfetch.inspect_image(bad)
        self.assertEqual(cm.exception.kind, "not_image")

    def test_html_rejected(self):
        with self.assertRaises(imgfetch.FetchError):
            imgfetch.inspect_image(b"<html>" + b"x" * 20000 + b"</html>")

    def test_small_valid_logo_accepted(self):
        data = img_bytes("PNG", size=(64, 64))
        self.assertLess(len(data), 8000)
        info = imgfetch.inspect_image(data)
        self.assertEqual((info["ext"], info["width"], info["height"]), (".png", 64, 64))

    def test_tracking_pixel_and_pixel_bomb_rejected(self):
        with self.assertRaises(imgfetch.FetchError) as cm:
            imgfetch.inspect_image(img_bytes("GIF", size=(1, 1)))
        self.assertEqual(cm.exception.kind, "too_small")
        with self.assertRaises(imgfetch.FetchError) as cm:
            imgfetch.inspect_image(img_bytes("PNG", size=(2000, 2000)), {"max_pixels": 1_000_000})
        self.assertEqual(cm.exception.kind, "too_many_pixels")


class TestHttp(unittest.TestCase):
    def test_content_length_over_cap(self):
        big = Resp(b"x" * 10, {"Content-Length": "999999999"})
        http = imgfetch.Http({"max_bytes": 1000}, opener=lambda r, t: big, sleep=lambda s: None)
        with self.assertRaises(imgfetch.FetchError) as cm:
            http.get("https://h/a")
        self.assertEqual(cm.exception.kind, "too_large")

    def test_streamed_body_stops_at_cap(self):
        body = Resp(b"x" * 10_000_000)
        http = imgfetch.Http({"max_bytes": 200_000}, opener=lambda r, t: body, sleep=lambda s: None)
        with self.assertRaises(imgfetch.FetchError):
            http.get("https://h/a")
        self.assertLess(body.pos, 400_000)

    def test_retry_transient_then_success(self):
        sleeps, n = [], [0]

        def opener(req, timeout):
            n[0] += 1
            if n[0] < 3:
                raise http_error(req.full_url, 503)
            return Resp(b"ok")
        http = imgfetch.Http({"retries": 2}, opener=opener, sleep=sleeps.append)
        self.assertEqual(http.get("https://h/a")[0], b"ok")
        self.assertEqual(len(sleeps), 2)

    def test_retry_after_honoured_or_refused(self):
        sleeps = []
        seq = [http_error("u", 429, {"Retry-After": "3"}), Resp(b"ok")]

        def flaky(req, timeout):
            r = seq.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        http = imgfetch.Http({"max_wait": 10}, opener=flaky, sleep=sleeps.append)
        self.assertEqual(http.get("https://h/a")[0], b"ok")
        self.assertEqual(sleeps, [3.0])

        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise http_error(req.full_url, 429, {"Retry-After": "3600"})
        http = imgfetch.Http({"max_wait": 10}, opener=opener, sleep=sleeps.append)
        with self.assertRaises(imgfetch.RateLimited):
            http.get("https://slow/a")
        self.assertEqual(sleeps, [3.0])  # no hour-long sleep
        with self.assertRaises(imgfetch.RateLimited):
            http.get("https://slow/b")  # host skipped for the rest of the run
        self.assertEqual(len(calls), 1)

    def test_non_transient_not_retried(self):
        calls = []

        def opener(req, timeout):
            calls.append(1)
            raise http_error(req.full_url, 404)
        with self.assertRaises(imgfetch.FetchError):
            imgfetch.Http(opener=opener, sleep=lambda s: None).get("https://h/a")
        self.assertEqual(len(calls), 1)


class TestAutoChain(Base):
    def setup_wiki(self):
        n = self.net
        n.search["Ada"] = [{"id": "Q7259", "label": "Ada", "description": "mathematician"}]
        n.entities["Q7259"] = ["Ada.jpg"]
        n.files["File:Ada.jpg"] = "https://upload.wikimedia.org/ada.jpg"
        n.images["https://upload.wikimedia.org/ada.jpg"] = img_bytes(color=(10, 10, 10))
        n.csearch["Ada"] = ["File:Ada.jpg", "File:Ada2.jpg"]
        n.files["File:Ada2.jpg"] = "https://upload.wikimedia.org/ada2.jpg"
        n.images["https://upload.wikimedia.org/ada2.jpg"] = img_bytes(color=(20, 20, 20))
        n.ov["Ada"] = [ov_result(1, "https://img.example/ov1.png")]
        n.images["https://img.example/ov1.png"] = img_bytes("PNG", color=(30, 30, 30))

    def test_default_needs_no_browser_and_uses_payload_extension(self):
        self.setup_wiki()
        self.net.images["https://img.example/ov1.png"] = img_bytes("JPEG", color=(30, 30, 30))  # .png URL, JPEG bytes
        self.cfg([{"id": "ada", "name": "Ada"}])
        boom = mock.Mock(side_effect=AssertionError("no subprocess/browser in the default chain"))
        with mock.patch.object(subprocess, "run", boom), mock.patch.object(subprocess, "Popen", boom), \
                mock.patch("shutil.which", boom):
            self.run_find()
        st = self.state("ada")
        self.assertEqual({s: r["status"] for s, r in st["sources"].items()},
                         {"wikidata": "ok", "commons": "ok", "openverse": "ok"})
        files = self.images("ada")
        self.assertEqual(len(files), 3)
        self.assertTrue(any(f.startswith("ov_") and f.endswith(".jpg") for f in files))
        src = {r["from"]: r for r in self.source("ada")}
        self.assertEqual(src["wikidata"]["entity"], "Q7259")
        self.assertEqual(src["openverse"]["license"], "BY 2.0")
        self.assertEqual(src["openverse"]["author"], "Ann")
        self.assertIn("sha256", src["openverse"])
        self.assertNotIn("baike", st["sources"])

    def test_dedup_keeps_provenance_of_each_source(self):
        self.setup_wiki()
        same = self.net.images["https://upload.wikimedia.org/ada.jpg"]
        self.net.ov["Ada"] = [ov_result(1, "https://mirror.example/x.jpeg", source="wikimedia")]
        self.net.images["https://mirror.example/x.jpeg"] = same
        self.cfg([{"id": "ada", "name": "Ada"}])
        self.run_find()
        rows = self.source("ada")
        first = rows[0]
        self.assertEqual(first["from"], "wikidata")
        froms = [p["from"] for p in first["provenance"]]
        self.assertEqual(froms, ["wikidata", "commons", "openverse"])
        self.assertEqual({p["family"] for p in first["provenance"]}, {"wikimedia"})  # not independent
        self.assertEqual(len(self.images("ada")), 2)
        self.assertEqual(self.net.count("ada.jpg"), 1)  # identical URL downloaded once

    def test_staged_budget_skips_later_sources(self):
        self.setup_wiki()
        self.net.entities["Q7259"] = ["Ada.jpg", "Ada2.jpg"]
        self.cfg([{"id": "ada", "name": "Ada"}], target=2)
        self.run_find()
        self.assertEqual(self.net.count("gsrsearch"), 0)
        self.assertEqual(self.net.count("openverse"), 0)
        self.assertEqual(set(self.state("ada")["sources"]), {"wikidata"})

    def test_failure_isolated_per_source_and_entity_then_retried(self):
        self.setup_wiki()
        self.net.search["Bob"] = [{"id": "Q2", "label": "Bob", "description": "x"}]
        self.net.entities["Q2"] = []
        self.net.fail["openverse"] = lambda u: http_error(u, 500)
        self.net.fail["commons"] = lambda u: Resp(b"not json") if "gsrsearch" in u else None
        self.cfg([{"id": "ada", "name": "Ada"}, {"id": "bob", "name": "Bob"}])
        self.run_find()
        st = self.state("ada")
        self.assertEqual(st["sources"]["commons"]["status"], "failed")
        self.assertEqual(st["sources"]["openverse"]["status"], "failed")
        self.assertEqual(self.state("bob")["sources"]["wikidata"]["status"], "empty")
        self.assertIn("openverse", self.state("bob")["sources"])  # second entity still processed
        del self.net.fail["openverse"], self.net.fail["commons"]
        self.net.calls.clear()
        self.run_find()
        st = self.state("ada")
        self.assertEqual(st["sources"]["wikidata"]["status"], "ok")
        self.assertEqual(st["sources"]["commons"]["status"], "ok")
        self.assertEqual(st["sources"]["openverse"]["status"], "ok")
        # successful unchanged wikidata work is neither re-queried nor re-downloaded
        self.assertEqual(self.net.count("wbsearchentities&search=Ada"), 0)
        self.assertEqual(self.net.count("upload.wikimedia.org/ada.jpg"), 0)
        self.assertEqual(self.net.count("wbsearchentities&search=Bob"), 1)  # empty result retried on a normal rerun

    def test_ingest_exception_does_not_abort_later_sources(self):
        self.setup_wiki()
        self.cfg([{"id": "ada", "name": "Ada"}, {"id": "bob", "name": "Bob", "wikidata": False}])
        real = fi.Run.ingest

        def broken(run, e, d, st, src, hits, limit):
            if src == "commons":
                raise OSError("disk full")
            return real(run, e, d, st, src, hits, limit)
        with mock.patch.object(fi.Run, "ingest", broken):
            self.run_find()
        st = self.state("ada")
        self.assertEqual(st["sources"]["commons"]["status"], "failed")
        self.assertIn("disk full", st["sources"]["commons"]["error"])
        self.assertEqual(st["sources"]["openverse"]["status"], "ok")
        self.assertTrue(os.path.exists(os.path.join(self.d("bob"), "STATE.json")))

    def test_rate_limit_marks_failed_without_long_wait(self):
        self.setup_wiki()
        self.net.fail["openverse"] = lambda u: http_error(u, 429, {"Retry-After": "86400"})
        self.cfg([{"id": "ada", "name": "Ada"}, {"id": "bob", "name": "Bob", "wikidata": False, "commons": False}])
        self.run_find()
        self.assertEqual(self.sleeps, [])
        rec = self.state("ada")["sources"]["openverse"]
        self.assertEqual((rec["status"], rec["error_kind"]), ("failed", "rate_limited"))
        self.assertEqual(self.state("bob")["sources"]["openverse"]["status"], "failed")
        self.assertEqual(self.net.count("api.openverse.org"), 1)  # blocked for the rest of the run

    def test_corrupt_payload_gives_empty_and_is_retried(self):
        self.setup_wiki()
        big = img_bytes(size=(300, 300), noise=True)
        self.net.images["https://upload.wikimedia.org/ada.jpg"] = big[: len(big) // 2]
        self.cfg([{"id": "ada", "name": "Ada", "commons": False, "openverse": False}])
        self.run_find()
        rec = self.state("ada")["sources"]["wikidata"]
        self.assertEqual(rec["status"], "empty")
        self.assertIn("not_image", rec["rejected"][0]["reason"])
        self.assertEqual(self.images("ada"), [])
        self.run_find()
        self.assertEqual(self.net.count("wbsearchentities&search=Ada"), 2)

    def test_repaired_payload_at_same_url_accepted_on_normal_rerun(self):
        self.setup_wiki()
        url = "https://upload.wikimedia.org/ada.jpg"
        good = self.net.images[url]
        self.net.images[url] = good[: len(good) // 2]
        self.cfg([{"id": "ada", "name": "Ada", "commons": False, "openverse": False}])
        self.run_find()
        self.assertEqual(self.state("ada")["sources"]["wikidata"]["status"], "empty")
        self.assertNotIn("rejected", self.state("ada"))  # no persistent negative cache
        self.net.images[url] = good
        self.run_find()
        self.assertEqual(self.state("ada")["sources"]["wikidata"]["status"], "ok")
        self.assertEqual(len(self.images("ada")), 1)

    def test_temporary_403_recovers_on_normal_rerun(self):
        self.setup_wiki()
        url = "https://img.example/ov1.png"
        good = self.net.images[url]
        self.net.images[url] = lambda u: http_error(u, 403)
        self.cfg([{"id": "ada", "name": "Ada", "wikidata": False, "commons": False}])
        self.run_find()
        rec = self.state("ada")["sources"]["openverse"]
        self.assertEqual(rec["status"], "empty")
        self.assertIn("403", rec["rejected"][0]["reason"])
        self.net.images[url] = good
        self.run_find()
        self.assertEqual(self.state("ada")["sources"]["openverse"]["status"], "ok")
        self.assertEqual(len(self.images("ada")), 1)

    def test_bad_url_downloaded_once_per_run_across_sources(self):
        self.setup_wiki()
        url = "https://upload.wikimedia.org/ada.jpg"
        self.net.images[url] = b"<html>blocked</html>"
        self.net.csearch["Ada"] = ["File:Ada.jpg"]
        self.cfg([{"id": "ada", "name": "Ada", "openverse": False}])
        self.run_find()
        self.assertEqual(self.net.count("ada.jpg"), 2)  # thumb + ?orig once; commons reuses the verdict
        self.run_find("--force")
        self.assertEqual(self.net.count("ada.jpg"), 4)

    def test_config_change_requeries_and_unchanged_is_cached(self):
        self.setup_wiki()
        self.net.csearch["Ada Lovelace"] = ["File:Ada2.jpg"]
        ents = [{"id": "ada", "name": "Ada", "wikidata": False, "openverse": False}]
        self.cfg(ents)
        self.run_find()
        self.run_find()
        self.assertEqual(self.net.count("gsrsearch=Ada&"), 1)
        ents[0]["commons"] = "Ada Lovelace"
        self.cfg(ents)
        self.run_find()
        self.assertEqual(self.net.count("gsrsearch=Ada+Lovelace"), 1)
        self.assertEqual(len(self.images("ada")), 1)  # superseded unpicked file pruned after success
        self.cfg(ents, per_source=2)  # fingerprint includes limits
        self.run_find()
        self.assertEqual(self.net.count("gsrsearch=Ada+Lovelace"), 2)

    def test_old_pick_retained_on_failed_and_successful_refresh(self):
        self.setup_wiki()
        ents = [{"id": "ada", "name": "Ada", "wikidata": False, "openverse": False}]
        self.cfg(ents)
        self.run_find()
        before = self.images("ada")
        self.assertEqual(len(before), 2)
        picked = next(r["file"] for r in self.source("ada") if r["img"].endswith("ada.jpg"))
        ents[0]["pick"] = picked
        ents[0]["commons"] = "Ada new"
        self.cfg(ents)
        self.net.fail["commons"] = lambda u: urllib.error.URLError("offline")
        self.run_find()
        self.assertEqual(self.images("ada"), before)  # nothing removed on failure
        self.assertEqual(len(self.source("ada")), 2)
        self.assertEqual(self.state("ada")["sources"]["commons"]["status"], "failed")
        del self.net.fail["commons"]
        self.net.csearch["Ada new"] = ["File:Ada3.jpg"]
        self.net.files["File:Ada3.jpg"] = "https://upload.wikimedia.org/ada3.jpg"
        self.net.images["https://upload.wikimedia.org/ada3.jpg"] = img_bytes(color=(90, 0, 0))
        self.run_find()
        now = self.images("ada")
        self.assertIn(picked, now)  # picked survives a successful refresh
        self.assertEqual(len(now), 2)  # the unpicked old file is replaced
        self.assertIn(picked, [r["file"] for r in self.source("ada")])

    def test_deleted_candidate_invalidates_cache(self):
        self.setup_wiki()
        self.cfg([{"id": "ada", "name": "Ada", "commons": False, "openverse": False}])
        self.run_find()
        (f,) = self.images("ada")
        os.remove(os.path.join(self.d("ada"), f))
        self.run_find()
        self.assertEqual(self.images("ada"), [f])

    def test_legacy_source_json_and_picks_survive(self):
        self.setup_wiki()
        d = self.d("ada")
        F.write(os.path.join(d, "cm_1.jpg"), img_bytes(color=(1, 2, 3)))
        F.write(os.path.join(d, "wd_Q7259_1.jpg"), self.net.images["https://upload.wikimedia.org/ada.jpg"])
        F.write(os.path.join(d, "SOURCE.json"), json.dumps([
            {"file": "wd_Q7259_1.jpg", "from": "wikidata", "url": "https://commons.wikimedia.org/wiki/File:Ada.jpg"},
            {"file": "cm_1.jpg", "from": "commons", "url": "https://commons.wikimedia.org/wiki/File:Old.jpg",
             "img": "https://upload.wikimedia.org/old.jpg"}]))
        self.cfg([{"id": "ada", "name": "Ada", "pick": "cm_1.jpg"}])
        self.run_find()
        files = self.images("ada")
        self.assertIn("cm_1.jpg", files)
        self.assertIn("wd_Q7259_1.jpg", files)
        st = self.state("ada")
        self.assertTrue(st["sources"]["commons"].get("migrated"))
        self.assertEqual(self.net.count("wbsearchentities&search=Ada"), 0)  # migrated sources count as done
        self.assertEqual(self.source("ada")[0]["file"], "wd_Q7259_1.jpg")
        # forcing a refresh dedups identical bytes onto the legacy name instead of a second copy
        self.run_find("--only", "wikidata")
        self.assertEqual(self.images("ada").count("wd_Q7259_1.jpg"), 1)
        self.assertFalse(any(f.startswith("wd_") and f != "wd_Q7259_1.jpg" for f in self.images("ada")))


class TestIdentity(Base):
    def test_hint_mismatch_is_ambiguous_not_first_hit(self):
        self.net.search["Li"] = [{"id": "Q1", "label": "Li", "description": "river"},
                                 {"id": "Q2", "label": "Li", "description": "surname"}]
        self.net.entities["Q1"] = ["River.jpg"]
        ents = [{"id": "li", "name": "Li", "hint": "entrepreneur", "commons": False, "openverse": False}]
        self.cfg(ents)
        self.run_find()
        rec = self.state("li")["sources"]["wikidata"]
        self.assertEqual(rec["status"], "ambiguous")
        self.assertEqual([o["qid"] for o in rec["options"]], ["Q1", "Q2"])
        self.assertIn('"qid"', rec["error"])
        self.assertEqual(self.net.count("wbgetentities"), 0)

    def test_hint_checked_in_each_search_language(self):
        zh = [{"id": "Q7259", "label": "爱达", "description": "英国数学家"},
              {"id": "Q9", "label": "爱达 日", "description": "纪念日"}]
        en = [{"id": "Q7259", "label": "Ada Lovelace", "description": "English mathematician"},
              {"id": "Q9", "label": "Ada Lovelace Day", "description": "day"}]

        def wb(u):
            if "wbsearchentities" in u:
                return FakeNet.json({"search": zh if "language=zh" in u else en})
            return None
        self.net.fail["wikidata"] = wb
        self.net.entities["Q7259"] = []
        self.cfg([{"id": "ada", "name": "Ada Lovelace", "hint": "mathematician", "commons": False,
                   "openverse": False}])
        self.run_find()
        rec = self.state("ada")["sources"]["wikidata"]
        self.assertEqual(rec["status"], "empty")  # resolved (no P18 in the fake), not ambiguous
        self.assertEqual((rec["identity"]["qid"], rec["identity"]["match"]), ("Q7259", "hint"))

    def test_multiple_hits_without_hint_are_ambiguous(self):
        self.net.search["Li"] = [{"id": "Q1", "label": "Li X", "description": "a"},
                                 {"id": "Q2", "label": "Li Y", "description": "b"}]
        self.cfg([{"id": "li", "name": "Li", "commons": False, "openverse": False}])
        self.run_find()
        self.assertEqual(self.state("li")["sources"]["wikidata"]["status"], "ambiguous")

    def test_explicit_qid_override(self):
        self.net.entities["Q2"] = ["Li.jpg"]
        self.net.files["File:Li.jpg"] = "https://upload.wikimedia.org/li.jpg"
        self.net.images["https://upload.wikimedia.org/li.jpg"] = img_bytes()
        self.cfg([{"id": "li", "name": "Li", "qid": "Q2", "commons": False, "openverse": False}])
        self.run_find()
        rec = self.state("li")["sources"]["wikidata"]
        self.assertEqual((rec["status"], rec["identity"]["match"]), ("ok", "explicit"))
        self.assertEqual(self.source("li")[0]["entity_match"], "explicit")
        self.assertEqual(self.net.count("wbsearchentities"), 0)

    def test_baike_disambiguation_without_hint_raises(self):
        import webfetch
        page = ("<html><body><div>多义词</div><a href='/item/Li/1'>Li (river)</a>"
                "<a href='/item/Li/2'>Li (person)</a></body></html>")
        with mock.patch.object(webfetch, "render", return_value=page):
            with self.assertRaises(providers.Ambiguous) as cm:
                webfetch.baike("Li", "")
            self.assertEqual(len(cm.exception.options), 2)
            with self.assertRaises(providers.Ambiguous):
                webfetch.baike("Li", "Li")  # hint matches both senses
            rendered = []
            with mock.patch.object(webfetch, "render", side_effect=lambda u: rendered.append(u) or "<html/>"):
                webfetch.baike("Li", "", url="https://baike.baidu.com/item/Li/2")
            self.assertEqual(rendered, ["https://baike.baidu.com/item/Li/2"])


class TestLocalSources(Base):
    def test_reuse_revalidated_after_in_place_edit(self):
        p = os.path.join(self.work, "mine", "local.bmp")
        F.write(p, img_bytes("BMP", size=(80, 60), color=(255, 0, 0)))
        self.cfg([{"id": "a", "name": "A", "reuse": ["mine/local.bmp"], "wikidata": False, "commons": False,
                   "openverse": False}])
        self.run_find()
        (first,) = self.images("a")
        F.write(p, img_bytes("BMP", size=(100, 70), color=(0, 0, 255)))
        self.run_find()
        (second,) = self.images("a")
        self.assertNotEqual(first, second)
        self.assertEqual(self.state("a")["files"][second]["width"], 100)

    def test_same_size_quick_edit_detected(self):
        p = os.path.join(self.work, "local.bmp")
        F.write(p, img_bytes("BMP", size=(80, 60), color=(255, 0, 0)))
        st0 = os.stat(p)
        self.cfg([{"id": "a", "name": "A", "reuse": [p], "wikidata": False, "commons": False, "openverse": False}])
        self.run_find()
        (first,) = self.images("a")
        F.write(p, img_bytes("BMP", size=(80, 60), color=(0, 255, 0)))
        os.utime(p, ns=(st0.st_atime_ns, st0.st_mtime_ns))  # same size, same mtime
        self.assertEqual(os.path.getsize(p), st0.st_size)
        self.run_find()
        (second,) = self.images("a")
        self.assertNotEqual(first, second)
        with Image.open(os.path.join(self.d("a"), second)) as im:
            self.assertEqual(im.convert("RGB").getpixel((0, 0)), (0, 255, 0))

    def test_manifest_handoff_urls_and_local_files(self):
        local = os.path.join(self.work, "dl", "x.png")
        F.write(local, img_bytes("PNG", size=(64, 64), color=(5, 5, 5)))
        self.net.images["https://news.example/p.jpg"] = img_bytes(color=(6, 6, 6))
        F.write(os.path.join(self.work, "image_manifest.json"), json.dumps({"version": 1, "candidates": [
            {"entity": "a", "image_url": "https://news.example/p.jpg", "source_page": "https://news.example/story",
             "title": "Founder in 1998", "date": "1998", "found_by": "host web search"},
            {"entity": "a", "file": "dl/x.png", "source_page": "https://site.example/about"}]}))
        self.cfg([{"id": "a", "name": "A", "wikidata": False, "commons": False, "openverse": False}])
        self.run_find()
        rows = self.source("a")
        self.assertEqual([r["from"] for r in rows], ["manifest", "manifest"])
        self.assertEqual(rows[0]["url"], "https://news.example/story")
        self.assertEqual(rows[0]["date"], "1998")
        self.assertNotIn("author", rows[0])  # nothing invented
        self.assertEqual(rows[1]["file_path"], local)
        # changed local manifest file is picked up (content fingerprint)
        F.write(local, img_bytes("PNG", size=(64, 64), color=(9, 9, 9)))
        self.run_find()
        self.assertEqual(len(self.images("a")), 2)
        self.assertEqual(self.net.count("news.example/p.jpg"), 1)


class TestLegacyOptIn(Base):
    def test_legacy_sources_only_when_listed(self):
        self.cfg([{"id": "a", "name": "A", "baike": True, "web": "A photo", "wikidata": False,
                   "commons": False, "openverse": False}], browser="headless", web_engine="so360")
        out = io.StringIO()
        with mock.patch("webfetch.render", side_effect=AssertionError("Chrome must not start")), \
                mock.patch("sys.stdout", new=out):
            self.run_find()
        self.assertNotIn("baike", self.state("a")["sources"])
        self.assertNotIn("web", self.state("a")["sources"])
        self.assertIn('["auto", "baike", "web"]', out.getvalue())  # migration notice

    def test_listed_legacy_source_uses_webfetch(self):
        import webfetch
        self.net.images["https://bk.example/1.jpg"] = img_bytes()
        self.cfg([{"id": "a", "name": "A", "baike": True, "wikidata": False, "commons": False, "openverse": False}],
                 sources=["auto", "baike"])
        hits = [{"img": "https://bk.example/1.jpg", "url": "https://baike.baidu.com/item/A", "about": "A"}]
        with mock.patch.object(webfetch, "baike", return_value=hits) as m:
            with open(os.path.join(self.work, "entities.json"), encoding="utf-8") as f:
                cfg = json.load(f)
            run = fi.Run(self.work, cfg, http=self.http(), log=self.logs.append)
            run.web_http = self.http()
            run.run()
        m.assert_called_once()
        self.assertEqual(self.state("a")["sources"]["baike"]["status"], "ok")

    def test_baike_url_alone_enables_baike(self):
        import webfetch
        url = "https://baike.baidu.com/item/A/2"
        self.cfg([{"id": "a", "name": "A", "baike_url": url, "wikidata": False, "commons": False,
                   "openverse": False}], sources=["auto", "baike"])
        with mock.patch.object(webfetch, "baike", return_value=[]) as m:
            self.run_find()
        self.assertEqual(m.call_args.kwargs["url"], url)
        self.assertEqual(self.state("a")["sources"]["baike"]["status"], "empty")


class TestSheet(Base):
    def test_all_candidates_paginated_and_stale_pages_removed(self):
        d = self.d("many")
        rows = []
        for i in range(15):
            name = f"cm_{i:02d}abcdef0123456789.jpg"
            F.write(os.path.join(d, name), img_bytes(size=(60 + i, 50), color=(i * 10, 0, 0)))
            rows.append({"file": name, "from": "commons"})
        F.write(os.path.join(d, "SOURCE.json"), json.dumps(rows))
        F.write(os.path.join(d, "STATE.json"), "{}")
        F.write(os.path.join(self.d("none"), ".keep"), "")
        self.cfg([{"id": "many", "name": "Many", "pick": rows[3]["file"]}, {"id": "none", "name": "None"}])
        stale = F.write(os.path.join(self.work, "sheets", "sheet_09.jpg"), b"old")
        with mock.patch("sys.stdout", new=io.StringIO()):
            index = sheet.main([self.work, "--cols", "4", "--rows", "2"])
        self.assertFalse(os.path.exists(stale))
        shown = [f for page in index for f in page["entities"].get("many", [])]
        self.assertEqual(sorted(shown), sorted(r["file"] for r in rows))  # all 15, none dropped
        self.assertEqual(len(index), 3)  # 4 rows for "many" + 1 for "none", 2 rows per page
        self.assertEqual(sorted(os.listdir(os.path.join(self.work, "sheets"))),
                         ["index.json", "sheet_01.jpg", "sheet_02.jpg", "sheet_03.jpg"])

    def test_font_fallback_without_system_fonts(self):
        with mock.patch.object(sheet, "CJK_FONTS", []), mock.patch.object(sheet, "LATIN_FONTS", []), \
                mock.patch.dict(os.environ, {"BOOK_ILLUSTRATOR_FONT": "", "EPUB_ILLUSTRATE_FONT": ""}):
            font, cjk = sheet.load_font(14)
        self.assertFalse(cjk)
        self.assertIsNotNone(font)

    def test_font_env_new_name_wins_and_old_name_still_works(self):
        new = F.write(os.path.join(self.work, "new.ttf"), b"font")
        old = F.write(os.path.join(self.work, "old.ttf"), b"font")
        with mock.patch.object(sheet, "CJK_FONTS", []), mock.patch.object(sheet, "LATIN_FONTS", []), \
                mock.patch.object(sheet.ImageFont, "truetype", side_effect=lambda path, size: path):
            with mock.patch.dict(os.environ, {"BOOK_ILLUSTRATOR_FONT": new, "EPUB_ILLUSTRATE_FONT": old}):
                self.assertEqual(sheet.load_font(14), (new, True))
            with mock.patch.dict(os.environ, {"BOOK_ILLUSTRATOR_FONT": "", "EPUB_ILLUSTRATE_FONT": old}):
                self.assertEqual(sheet.load_font(14), (old, True))


if __name__ == "__main__":
    unittest.main()
