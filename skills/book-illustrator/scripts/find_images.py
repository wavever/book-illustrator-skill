"""Collect candidate images for each entity, for review and picking.

Usage: python3 find_images.py WORKDIR [entity_id ...] [--only SRC[,SRC]] [--force] [--status]

Default ("auto") needs only Python + network: no browser, Node, login, API key or calibre.
Sources, tried in this order until an entity has `target` candidates (staged, not all at once):
  reuse      local files: entity "reuse": [paths] (relative paths are relative to WORKDIR)
  manifest   WORKDIR/image_manifest.json written by the host agent's own search (optional)
  wikidata   P18 image / P154 logo of the one resolved Wikidata entity (via Commons)
  commons    Wikimedia Commons file search
  openverse  Openverse anonymous image search (openly licensed images)
  baike, web legacy, opt-in only: listed explicitly in settings "sources" (see below)

entities.json "settings" (all optional):
  sources:     "auto" (default) | list, e.g. ["auto", "baike"] or ["reuse", "wikidata"]
  target:      candidates per entity before later sources are skipped (default 6)
  per_source:  max images taken from one source (default 4; entity "limit" overrides)
  manifest:    manifest path (default image_manifest.json in WORKDIR)
  limits:      {timeout, deadline, retries, max_wait, max_bytes, max_pixels, min_side}
  wikidata_languages: search languages (default ["zh", "en"])
  openverse:   extra Openverse filters, e.g. {"license_type": "commercial"}
  browser:     legacy "headless" (private headless Chrome) | "ego" (user's browser via ego-browser)
  web_engine:  legacy "so360" (direct HTTPS requests, no browser; default) | "bing" (needs browser "ego")
Per entity:
  qid: "Q123"                explicit Wikidata entity (also accepted: "wikidata": "Q123")
  hint: "word"               disambiguation against Wikidata descriptions / Baike senses
  wikidata | commons | openverse: "query" or false (default: entity name)
  baike: true | "词条" | "https://baike.baidu.com/item/..."   web: "query" | [...]   (legacy)
  baike_url: explicit Baike page (skips sense selection)

Writes WORKDIR/candidates/<id>/: images named <src>_<sha256[:12]>.<ext> (extension from the
decoded payload), SOURCE.json (list of {"file", "from", "url", "img", ...} for inject.py) and
STATE.json (per-source status, configuration fingerprint, URL cache). A normal rerun skips
sources that succeeded with an unchanged configuration and retries failed / empty / ambiguous
ones; --only SRC or --force re-query regardless. Existing files are never deleted before a
successful refresh, and picked or legacy (pre-STATE.json) files are never deleted.
"""
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import imgfetch  # noqa: E402
import providers  # noqa: E402
from imgfetch import FetchError  # noqa: E402

CHAIN = ["reuse", "manifest", "wikidata", "commons", "openverse", "baike", "web"]
AUTO = ["reuse", "manifest", "wikidata", "commons", "openverse"]
LEGACY = {"baike", "web"}
ALWAYS = {"reuse", "manifest"}  # user-supplied: not subject to the staged budget
PREFIX = {"reuse": "reuse", "manifest": "mf", "wikidata": "wd", "commons": "cm", "openverse": "ov",
          "baike": "bk", "web": "web"}
PROVIDER_VERSION = 3  # 3: full credit/licence fields; cached sources re-query once (files are reused)
RETRY = {"failed", "partial", "empty", "ambiguous"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def sha_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_stamp(path):
    """Fingerprint part for a local file: content hash (mtime alone misses quick same-size edits)."""
    try:
        st = os.stat(path)
        return [path, st.st_size, st.st_mtime_ns, sha_file(path) if os.path.isfile(path) else None]
    except OSError:
        return [path, None, None, None]


def resolve_sources(settings):
    raw = settings.get("sources", "auto")
    raw = [raw] if isinstance(raw, str) else list(raw)
    picked, unknown = set(), []
    for s in raw:
        if s == "auto":
            picked.update(AUTO)
        elif s in CHAIN:
            picked.add(s)
        else:
            unknown.append(s)
    return [s for s in CHAIN if s in picked], unknown


class Run:
    def __init__(self, work, cfg, http=None, ego_runner=None, log=print):
        self.work, self.cfg, self.log = work, cfg, log
        self.settings = cfg.get("settings", {})
        self.sources, unknown = resolve_sources(self.settings)
        for u in unknown:
            log(f"warning: unknown source {u!r} in settings.sources ignored")
        self.limits = dict(imgfetch.DEFAULT_LIMITS, **self.settings.get("limits", {}))
        self.http = http or imgfetch.Http(self.limits)
        self.web_http = None
        self.ego_runner = ego_runner or run_ego_jobs
        self.target = int(self.settings.get("target", 6))
        self.per_source = int(self.settings.get("per_source", 4))
        self.browser = self.settings.get("browser", "headless")
        self.engine = self.settings.get("web_engine", "so360")
        self.languages = tuple(self.settings.get("wikidata_languages", ["zh", "en"]))
        self.cand_root = os.path.join(work, "candidates")
        self.manifest = self._load_manifest()
        self.limits_fp = fingerprint({k: self.limits[k] for k in ("max_bytes", "max_pixels", "min_side")})
        self.ego_jobs = []
        self.rejected = {}  # image URL -> reason; in memory only, never persisted
        if ("browser" in self.settings or "web_engine" in self.settings) and not LEGACY & set(self.sources):
            log('note: settings "browser"/"web_engine" only affect the legacy baike/web sources, which now '
                'run only when listed in settings.sources (e.g. ["auto", "baike", "web"])')

    # ---------- configuration ----------
    def _load_manifest(self):
        path = self.settings.get("manifest", "image_manifest.json")
        path = path if os.path.isabs(path) else os.path.join(self.work, path)
        self.manifest_dir = os.path.dirname(path)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except ValueError as e:
            self.log(f"warning: {path} is not valid JSON ({e}); manifest ignored")
            return {}
        items = data.get("candidates", []) if isinstance(data, dict) else data
        out = {}
        for x in items if isinstance(items, list) else []:
            if isinstance(x, dict) and x.get("entity"):
                out.setdefault(x["entity"], []).append(x)
        return out

    def limit_of(self, e):
        return int(e.get("limit", self.per_source))

    def plan(self, e):
        """{source: (fingerprint, params)} for sources applicable to entity e."""
        name, lim = e["name"], self.limit_of(e)
        common = {"v": PROVIDER_VERSION, "limits": self.limits_fp}
        out = {}

        def q(key):
            v = e.get(key, True)
            return None if v is False or v is None else (v if isinstance(v, str) and v.strip() else name)

        for s in self.sources:
            p = None
            if s == "reuse" and e.get("reuse"):
                paths = [x if os.path.isabs(os.path.expanduser(x)) else os.path.join(self.work, x)
                         for x in map(os.path.expanduser, e["reuse"])]
                p = {"paths": [local_stamp(x) for x in paths]}
            elif s == "manifest" and self.manifest.get(e["id"]):
                stats = []
                for x in self.manifest[e["id"]]:
                    if x.get("file") and not x.get("image_url"):
                        stats.append(local_stamp(os.path.join(self.manifest_dir, os.path.expanduser(x["file"]))))
                p = {"entries": self.manifest[e["id"]], "files": stats}
            elif s == "wikidata" and q("wikidata") is not None:
                wd = e.get("wikidata")
                qid = e.get("qid") or (wd if isinstance(wd, str) and providers.QID_RE.match(wd) else "")
                p = {"query": name if qid else q("wikidata"), "hint": e.get("hint", ""), "qid": qid,
                     "lang": list(self.languages), "limit": lim}
            elif s == "commons" and q("commons") is not None:
                p = {"query": q("commons"), "limit": lim}
            elif s == "openverse" and q("openverse") is not None:
                p = {"query": q("openverse"), "limit": lim, "extra": self.settings.get("openverse", {})}
            elif s == "baike" and (e.get("baike") or e.get("baike_url")):
                b = e.get("baike") or True
                url = e.get("baike_url") or (b if isinstance(b, str) and b.startswith("http") else "")
                p = {"item": name if b is True else b, "url": url, "hint": e.get("hint", ""),
                     "browser": self.browser, "limit": lim}
            elif s == "web" and e.get("web"):
                p = {"queries": [e["web"]] if isinstance(e["web"], str) else list(e["web"]),
                     "engine": self.engine, "browser": self.browser, "limit": lim}
            if p is not None:
                out[s] = (fingerprint(dict(common, source=s, **p)), p)
        return out

    # ---------- state ----------
    def load(self, e, d):
        state_path = os.path.join(d, "STATE.json")
        if os.path.exists(state_path):
            try:
                with open(state_path, encoding="utf-8") as f:
                    st = json.load(f)
                if st.get("version") == 1:
                    return st
            except ValueError:
                self.log(f"{e['id']}: STATE.json unreadable, rebuilding from SOURCE.json")
        st = {"version": 1, "sources": {}, "files": {}, "urls": {}, "seq": 0}
        src_path = os.path.join(d, "SOURCE.json")
        legacy = []
        if os.path.exists(src_path):
            try:
                with open(src_path, encoding="utf-8") as f:
                    legacy = json.load(f)
            except ValueError:
                legacy = []
        plan = self.plan(e)
        for entry in legacy if isinstance(legacy, list) else []:
            f = entry.get("file")
            if not f or not os.path.exists(os.path.join(d, f)):
                continue
            prov = {k: v for k, v in entry.items() if k not in ("file", "provenance")}
            if f not in st["files"]:
                st["seq"] += 1
                st["files"][f] = {"sha256": sha_file(os.path.join(d, f)), "legacy": True, "seq": st["seq"],
                                  "provenance": []}
            st["files"][f]["provenance"].append(prov)
            src = prov.get("from", "legacy")
            rec = st["sources"].setdefault(src, {"status": "ok", "migrated": True, "updated": now(),
                                                  "fingerprint": plan.get(src, ("legacy",))[0], "files": []})
            if f not in rec["files"]:
                rec["files"].append(f)
            if prov.get("img"):
                st["urls"][prov["img"]] = f
        return st

    def save(self, e, d, st):
        imgfetch.atomic_json(os.path.join(d, "STATE.json"), st)
        order = {s: i for i, s in enumerate(CHAIN)}
        rows = []
        for f, rec in sorted(st["files"].items(),
                             key=lambda kv: (order.get((kv[1]["provenance"] or [{}])[0].get("from"), 99), kv[1]["seq"])):
            if not rec["provenance"]:
                continue
            row = {"file": f, **rec["provenance"][0]}
            for k in ("width", "height", "sha256"):
                if rec.get(k):
                    row[k] = rec[k]
            if len(rec["provenance"]) > 1:
                row["provenance"] = rec["provenance"]
            rows.append(row)
        imgfetch.atomic_json(os.path.join(d, "SOURCE.json"), rows)

    @staticmethod
    def picks(e):
        p = e.get("pick") or []
        return {os.path.basename(x) for x in ([p] if isinstance(p, str) else p)}

    # ---------- providers ----------
    def query(self, src, e, p):
        """Hits for one source; may raise FetchError / providers.Ambiguous / anything else."""
        info = {}
        if src == "reuse":
            hits = [{"path": x[0], "page": x[0], "family": "local"} for x in p["paths"]]
        elif src == "manifest":
            hits = providers.manifest_hits(p["entries"], self.manifest_dir)
        elif src == "wikidata":
            hits, ident = providers.wikidata(self.http, p["query"], p["hint"], p["qid"], p["limit"],
                                             tuple(p["lang"]))
            if ident:
                info["identity"] = ident
        elif src == "commons":
            hits = providers.commons_search(self.http, p["query"], p["limit"])
        elif src == "openverse":
            hits = providers.openverse(self.http, p["query"], p["limit"], p["extra"])
        elif src == "baike":
            import webfetch
            raw = webfetch.baike(p["item"], p["hint"], url=p["url"] or None)
            hits = [{"imgs": [h["img"]], "page": h["url"], "about": h.get("about", ""), "family": "baike"}
                    for h in raw]
        elif src == "web":
            import webfetch
            hits = []
            for qq in p["queries"]:
                hits += [{"imgs": [h["img"]], "page": h["url"], "about": h.get("about", ""), "query": qq,
                          "family": "web"} for h in webfetch.so360(qq, p["limit"])]
        else:  # pragma: no cover
            raise ValueError(src)
        return hits, info

    def fetch_bytes(self, src, h):
        """(data, info, used_url) or raise FetchError (last error)."""
        if h.get("path"):
            path = h["path"]
            if not os.path.isfile(path):
                raise FetchError("missing", f"no such file: {path}")
            if os.path.getsize(path) > self.limits["max_bytes"]:
                raise FetchError("too_large", f"{path} exceeds {self.limits['max_bytes']} bytes")
            with open(path, "rb") as f:
                data = f.read()
            return data, imgfetch.inspect_image(data, self.limits), path
        err = FetchError("http", "no image URL")
        for u in h.get("imgs", []):
            headers = {"Referer": h["page"]} if src in LEGACY and h.get("page") else {}
            http = self.http
            if src in LEGACY:  # legacy hosts often refuse non-browser user agents
                if self.web_http is None:
                    import webfetch
                    self.web_http = imgfetch.Http(self.limits, user_agent=webfetch.UA)
                http = self.web_http
            try:
                data, _ = http.get(u, headers=headers)
                return data, imgfetch.inspect_image(data, self.limits), u
            except FetchError as ex:
                err = ex
        raise err

    def ingest(self, e, d, st, src, hits, limit):
        """Download/validate hits; returns (accepted files, rejected list, transient failure count)."""
        by_sha = {r["sha256"]: f for f, r in st["files"].items() if r.get("sha256")}
        accepted, rejected, transient = [], [], 0
        for h in hits:
            if len(accepted) >= limit:
                break
            keys = [h["path"]] if h.get("path") else h.get("imgs", [])
            # remote URLs are cached; local files are always re-read (they can change in place)
            name = None if h.get("path") else next(
                (st["urls"][k] for k in keys if k in st["urls"] and st["urls"][k] in st["files"]
                 and os.path.exists(os.path.join(d, st["urls"][k]))), None)
            if name is None and not h.get("path") and keys and all(k in self.rejected for k in keys):
                rejected.append({"img": keys[0], "reason": self.rejected[keys[0]], "cached": True})
                continue
            if name is None:
                try:
                    data, info, used = self.fetch_bytes(src, h)
                except FetchError as ex:
                    rejected.append({"img": keys[0] if keys else "", "reason": f"{ex.kind}: {ex}"})
                    if ex.transient:
                        transient += 1
                    elif not h.get("path"):  # negative cache for this run only: later runs retry
                        for k in keys:
                            self.rejected[k] = f"{ex.kind}: {ex}"
                    continue
                name = by_sha.get(info["sha256"])
                if name is None or not os.path.exists(os.path.join(d, name)):
                    name = name or f"{PREFIX[src]}_{info['sha256'][:12]}{info['ext']}"
                    imgfetch.atomic_write(os.path.join(d, name), data)
                    st["seq"] += 1
                    st["files"].setdefault(name, {"provenance": [], "seq": st["seq"]})
                st["files"][name].update({k: info[k] for k in ("sha256", "width", "height", "format", "bytes")})
                by_sha[info["sha256"]] = name
                if not h.get("path"):
                    st["urls"][used] = name
            if h.get("path"):
                prov = {"from": src, "url": h.get("page") or h["path"]}
                if prov["url"] != h["path"]:
                    prov["file_path"] = h["path"]
            else:
                prov = {"from": src, "url": h.get("page", ""),
                        "img": next((k for k in keys if st["urls"].get(k) == name), keys[0] if keys else "")}
            prov.update({k: v for k, v in h.items() if k not in ("imgs", "path", "page") and v})
            plist = st["files"][name]["provenance"]
            same = [i for i, x in enumerate(plist) if x.get("from") == src and x.get("url") == prov["url"]
                    and x.get("img", "") == prov.get("img", "")]
            if same:
                plist[same[0]] = prov
            else:
                plist.append(prov)
            if name not in accepted:
                accepted.append(name)
        return accepted, rejected, transient

    def prune(self, e, d, st, src, keep):
        """After a successful refresh: drop src provenance from files it no longer returns."""
        picks = self.picks(e)
        for f in list(st["files"]):
            rec = st["files"][f]
            if (f in keep or f in picks or rec.get("legacy")
                    or not any(p.get("from") == src for p in rec["provenance"])):
                continue
            rec["provenance"] = [p for p in rec["provenance"] if p.get("from") != src]
            if not rec["provenance"]:
                path = os.path.join(d, f)
                if os.path.exists(path):
                    os.remove(path)
                del st["files"][f]
        for u in [u for u, f in st["urls"].items() if f not in st["files"]]:
            del st["urls"][u]

    def record(self, st, src, fp, status, files=None, **extra):
        prev = st["sources"].get(src, {})
        rec = {"status": status, "fingerprint": fp, "updated": now(),
               "files": files if files is not None else prev.get("files", [])}
        rec.update({k: v for k, v in extra.items() if v not in (None, [], "")})
        st["sources"][src] = rec
        return rec

    def run_source(self, e, d, st, src, fp, p):
        try:
            hits, info = self.query(src, e, p)
        except providers.Ambiguous as ex:
            return self.record(st, src, fp, "ambiguous", error=str(ex), options=ex.options)
        except FetchError as ex:
            return self.record(st, src, fp, "failed", error=str(ex), error_kind=ex.kind,
                               retry_after=ex.retry_after)
        except Exception as ex:  # noqa: BLE001 - isolate any provider bug to this source + entity
            return self.record(st, src, fp, "failed", error=f"{type(ex).__name__}: {ex}", error_kind="error")
        return self.safe_finish(e, d, st, src, fp, hits, info)

    def safe_finish(self, e, d, st, src, fp, hits, info=None):
        try:
            return self.finish(e, d, st, src, fp, hits, info)
        except Exception as ex:  # noqa: BLE001 - e.g. disk errors while saving one image
            return self.record(st, src, fp, "failed", error=f"{type(ex).__name__}: {ex}", error_kind="error")

    def finish(self, e, d, st, src, fp, hits, info=None):
        accepted, rejected, transient = self.ingest(e, d, st, src, hits, self.limit_of(e))
        status = ("partial" if transient else "ok") if accepted else ("failed" if transient else "empty")
        if status in ("ok", "empty"):
            self.prune(e, d, st, src, set(accepted))
            files = accepted
        else:
            prev = [f for f in st["sources"].get(src, {}).get("files", []) if f in st["files"]]
            files = accepted + [f for f in prev if f not in accepted]
        return self.record(st, src, fp, status, files=files, hits=len(hits), rejected=rejected[:10],
                           error=(f"{transient} transient download failure(s)" if transient else None),
                           **(info or {}))

    # ---------- main loop ----------
    def entity(self, e, force_sources=(), force_all=False):
        eid = e["id"]
        d = os.path.join(self.cand_root, eid)
        os.makedirs(d, exist_ok=True)
        st = self.load(e, d)
        plan = self.plan(e)
        report, seen = [], set()
        for f in st["files"]:  # legacy files not attributable to any active source count first
            if all(p.get("from") not in plan for p in st["files"][f]["provenance"]):
                seen.add(f)
        for src in CHAIN:
            if src not in plan:
                continue
            fp, p = plan[src]
            prev = st["sources"].get(src)
            forced = force_all or src in force_sources
            listed = (prev or {}).get("files", [])
            have = [f for f in listed if f in st["files"] and os.path.exists(os.path.join(d, f))]
            intact = len(have) == len(listed)  # a deleted candidate invalidates the cache
            if prev and prev["status"] == "ok" and prev["fingerprint"] == fp and intact and not forced:
                seen.update(have)
                report.append(f"{src} cached {len(have)}")
                continue
            changed = bool(prev) and prev["status"] in ("ok", "empty") and (
                prev.get("fingerprint") != fp or not intact)
            if len(seen) >= self.target and not forced and not changed and src not in ALWAYS:
                seen.update(have)
                report.append(f"{src} skipped (target {self.target} reached)")
                continue
            if src in LEGACY and self.browser == "ego" and (src == "baike" or self.engine == "bing"):
                self.ego_jobs.append({"id": eid, "src": src, "fp": fp, "p": p, "dir": os.path.abspath(d)})
                seen.update(have)
                report.append(f"{src} queued for ego-browser")
                continue
            rec = self.run_source(e, d, st, src, fp, p)
            seen.update(f for f in rec.get("files", []) if f in st["files"])
            report.append(f"{src} {rec['status']} {len(rec.get('files', []))}"
                          + (f" ({rec['error']})" if rec.get("error") and rec["status"] != "ok" else ""))
            self.save(e, d, st)
        self.save(e, d, st)
        self.log(f"{eid}: " + ", ".join(report or ["no applicable sources"]))
        return st

    def run(self, only_ids=(), force_sources=(), force_all=False):
        if self.settings.get("web_engine") == "bing" and self.browser != "ego" and "web" in self.sources:
            sys.exit('web_engine "bing" needs browser "ego" (Bing serves no results to headless Chrome)')
        results = {}
        for e in self.cfg["entities"]:
            if only_ids and e["id"] not in only_ids:
                continue
            try:
                results[e["id"]] = self.entity(e, force_sources, force_all)
            except Exception as ex:  # noqa: BLE001 - one broken entity must not stop the others
                self.log(f"{e['id']}: error {type(ex).__name__}: {ex}")
                results[e["id"]] = None
        if self.ego_jobs:
            self.finish_ego(results)
        return results

    def finish_ego(self, results):
        outputs = self.ego_runner(self.work, self.ego_jobs, self.log)
        ents = {e["id"]: e for e in self.cfg["entities"]}
        for job, out in zip(self.ego_jobs, outputs):
            e, d = ents[job["id"]], job["dir"]
            st = self.load(e, d)
            if out.get("status") == "ambiguous":
                rec = self.record(st, job["src"], job["fp"], "ambiguous", error=out.get("error"),
                                  options=out.get("senses", [])[:10])
            elif out.get("status") != "ok":
                rec = self.record(st, job["src"], job["fp"], "failed", error=out.get("error") or "ego-browser job failed",
                                  error_kind="browser")
            else:
                hits = [{"imgs": [h["img"]], "page": h.get("page", ""), "about": h.get("about", ""),
                         "family": job["src"], **({"query": h["query"]} if h.get("query") else {})}
                        for h in out.get("hits", []) if h.get("img")]
                rec = self.safe_finish(e, d, st, job["src"], job["fp"], hits)
            self.save(e, d, st)
            results[job["id"]] = st
            self.log(f"{job['id']}: {job['src']} {rec['status']} {len(rec.get('files', []))} (ego-browser)")


def run_ego_jobs(work, jobs, log=print):
    """Run legacy ego-browser jobs (explicit "browser": "ego" only); returns one result per job."""
    exe = shutil.which("ego-browser")
    if not exe:
        return [{"status": "failed", "error": "ego-browser not found on PATH"} for _ in jobs]
    here = os.path.dirname(os.path.abspath(__file__))
    out_dir = os.path.join(work, "ego_jobs")
    os.makedirs(out_dir, exist_ok=True)
    results = [None] * len(jobs)
    for kind, module in (("baike", "baike.mjs"), ("web", "bing.mjs")):
        mine = [(i, j) for i, j in enumerate(jobs) if j["src"] == kind]
        if not mine:
            continue
        payload = []
        for i, j in mine:
            out = os.path.join(os.path.abspath(out_dir), f"{j['id']}_{kind}.json")
            if os.path.exists(out):
                os.remove(out)
            p = j["p"]
            payload.append({"id": j["id"], "out": out, "item": p.get("item", ""), "url": p.get("url", ""),
                            "hint": p.get("hint", ""), "queries": p.get("queries", []), "limit": p["limit"] * 2})
        script = os.path.join(out_dir, f"jobs_{module}")
        with open(os.path.join(here, module), encoding="utf-8") as f:
            body = f.read()
        imgfetch.atomic_write(script, f"const JOBS = {json.dumps(payload, ensure_ascii=False)};\n" + body)
        try:
            with open(script, encoding="utf-8") as f:
                subprocess.run([exe, "nodejs"], stdin=f, check=False, timeout=600)
        except subprocess.TimeoutExpired:
            log(f"warning: ego-browser {kind} jobs timed out")
        for (i, j), pl in zip(mine, payload):
            try:
                with open(pl["out"], encoding="utf-8") as f:
                    results[i] = json.load(f)
            except (OSError, ValueError):
                results[i] = {"status": "failed", "error": "ego-browser produced no result"}
    return results


def status(work, cfg, only_ids=()):
    for e in cfg["entities"]:
        if only_ids and e["id"] not in only_ids:
            continue
        p = os.path.join(work, "candidates", e["id"], "STATE.json")
        if not os.path.exists(p):
            print(f"{e['id']}: not searched")
            continue
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
        parts = []
        for s, r in st["sources"].items():
            parts.append(f"{s}={r['status']}({len(r.get('files', []))})")
            if r["status"] in ("ambiguous", "failed") and r.get("error"):
                parts[-1] += f" [{r['error']}]"
        print(f"{e['id']}: {len(st['files'])} files; " + ", ".join(parts))
        for s, r in st["sources"].items():
            for o in r.get("options", []) if r["status"] == "ambiguous" else []:
                print(f"    {s} option: " + " ".join(str(o.get(k, "")) for k in ("qid", "label", "description", "text", "href") if o.get(k)))


def main(argv=None, http=None, ego_runner=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0].startswith("-"):
        sys.exit(__doc__)
    force_sources, force_all, show = set(), False, False
    if "--only" in args:
        k = args.index("--only")
        force_sources = set(args[k + 1].split(","))
        del args[k:k + 2]
    if "--force" in args:
        args.remove("--force")
        force_all = True
    if "--status" in args:
        args.remove("--status")
        show = True
    work, only_ids = args[0], set(args[1:])
    with open(os.path.join(work, "entities.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    if show:
        return status(work, cfg, only_ids)
    run = Run(work, cfg, http=http, ego_runner=ego_runner)
    if force_sources:
        bad = force_sources - set(CHAIN)
        if bad:
            sys.exit(f"--only: unknown source(s) {', '.join(sorted(bad))}")
        extra = force_sources - set(run.sources)
        if extra:  # --only may name an opt-in source explicitly
            run.sources = [s for s in CHAIN if s in set(run.sources) | extra]
        run.sources = [s for s in run.sources if s in force_sources]
    return run.run(only_ids, force_sources, force_all)


if __name__ == "__main__":
    main()
