"""Collect candidate images for each entity, for review and picking.

Usage: python3 find_images.py WORKDIR [entity_id ...] [--only SOURCE[,SOURCE]]

Reads WORKDIR/entities.json. Top-level "settings" (agreed with the user at the start):
  "sources":  which sources may be used, any of
              reuse, wikidata, commons, baike, web          (default: all)
  "browser":  how Baike / web search are fetched
              "headless" – private headless Chrome + plain HTTP, never touches the user's browser (default)
              "ego"      – the user's browser via ego-browser (needed for Bing; pauses if the user takes over)
  "web_engine": "so360" (default, plain HTTP) | "bing" (requires browser "ego")
Per entity, these keys drive the search:
  reuse:    [local paths]       copy existing images (e.g. from an earlier project)
  wikidata: "name" | true       Wikidata entity search (P18 image); true = use entity name
  commons:  "query"             Wikimedia Commons file search
  baike:    "词条" | true       Baidu Baike entry pictures (summary picture first)
  web:      "query" | [...]     web image search
  hint:     "word"              disambiguation: matched against Wikidata descriptions / Baike senses
Writes WORKDIR/candidates/<id>/<src>_<n>.<ext> + SOURCE.json. Entities already searched are skipped
unless named on the command line; --only reruns just the given sources (replacing their old files).
"""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import webfetch  # noqa: E402

args = [a for a in sys.argv[1:]]
only_sources = None
if "--only" in args:
    k = args.index("--only")
    only_sources = set(args[k + 1].split(","))
    del args[k:k + 2]
work, only_ids = args[0], set(args[1:])
cfg = json.load(open(os.path.join(work, "entities.json"), encoding="utf-8"))
settings = cfg.get("settings", {})
allowed = set(settings.get("sources", ["reuse", "wikidata", "commons", "baike", "web"]))
if only_sources:
    allowed &= only_sources
browser = settings.get("browser", "headless")
engine = settings.get("web_engine", "so360")
if engine == "bing" and browser != "ego":
    sys.exit('web_engine "bing" needs browser "ego" (Bing serves no results to headless Chrome)')
cand_root = os.path.join(work, "candidates")
UA = {"User-Agent": "epub-illustrate/1.0 (personal reading aid)"}
PREFIX = {"reuse": "reuse", "wikidata": "wd", "commons": "cm", "baike": "bk", "web": "web"}


def get_json(url, params):
    for attempt in range(5):
        try:
            req = urllib.request.Request(url + "?" + urllib.parse.urlencode(params), headers=UA)
            return json.load(urllib.request.urlopen(req, timeout=30, context=webfetch.SSL_CTX))
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("rate limited: " + url)


def download(url, dest, referer=""):
    headers = {"User-Agent": webfetch.UA}
    if referer:
        headers["Referer"] = referer
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers=headers)
            resp = urllib.request.urlopen(req, timeout=40, context=webfetch.SSL_CTX)
            if not resp.headers.get("Content-Type", "image/").startswith("image/"):
                return False
            data = resp.read()
            if len(data) < 8000:
                return False
            open(dest, "wb").write(data)
            return True
        except urllib.error.HTTPError as e:
            if e.code != 429:
                return False
            time.sleep(5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            return False
    return False


def commons_files(titles, width=1400):
    out = []
    for i in range(0, len(titles), 20):
        r = get_json("https://commons.wikimedia.org/w/api.php", {
            "action": "query", "titles": "|".join(titles[i:i + 20]), "prop": "imageinfo",
            "iiprop": "url|extmetadata|mime", "iiurlwidth": width, "format": "json"})
        for p in r["query"]["pages"].values():
            ii = (p.get("imageinfo") or [{}])[0]
            if not ii or ii.get("mime", "").startswith(("video", "audio", "application")):
                continue
            desc = ii.get("extmetadata", {}).get("ImageDescription", {}).get("value", "")[:200]
            out.append((ii.get("thumburl") or ii["url"], ii["descriptionurl"], desc))
    return out


def ext_of(url):
    e = os.path.splitext(urllib.parse.urlparse(url).path)[1].lower()
    return e if e in (".jpg", ".jpeg", ".png", ".gif", ".webp") else ".jpg"


def save_hits(d, prefix, origin, hits, sources):
    n = 0
    for h in hits:
        dest = os.path.join(d, f"{prefix}_{n + 1}{ext_of(h['img'])}")
        if download(h["img"], dest, referer=h.get("url", "")):
            n += 1
            sources.append({"file": os.path.basename(dest), "from": origin, "url": h.get("url", ""),
                            "img": h["img"], "about": h.get("about", ""), **({"query": h["query"]} if h.get("query") else {})})
    return n


ego_jobs = {"baike": [], "web": []}
for e in cfg["entities"]:
    eid = e["id"]
    if only_ids and eid not in only_ids:
        continue
    d = os.path.join(cand_root, eid)
    src_json = os.path.join(d, "SOURCE.json")
    if os.path.exists(src_json) and not only_ids and not only_sources:
        continue
    os.makedirs(d, exist_ok=True)
    todo = [s for s in ("reuse", "wikidata", "commons", "baike", "web") if s in allowed and e.get(s)]
    sources = json.load(open(src_json, encoding="utf-8")) if os.path.exists(src_json) else []
    # rerun = replace this source's earlier files
    for s in todo:
        for f in [x for x in os.listdir(d) if x.startswith(PREFIX[s] + "_")]:
            os.remove(os.path.join(d, f))
        sources = [x for x in sources if x["from"] != s]
    report = []
    try:
        if "reuse" in todo:
            for n, p in enumerate(e["reuse"], 1):
                dest = os.path.join(d, f"reuse_{n}{os.path.splitext(p)[1].lower()}")
                shutil.copy(p, dest)
                sources.append({"file": os.path.basename(dest), "from": "reuse", "url": p})
            report.append(f"reuse {len(e['reuse'])}")
        if "wikidata" in todo:
            q = e["name"] if e["wikidata"] is True else e["wikidata"]
            hits = get_json("https://www.wikidata.org/w/api.php", {
                "action": "wbsearchentities", "search": q, "language": "zh", "uselang": "zh",
                "format": "json", "limit": 7}).get("search", [])
            if e.get("hint"):
                hits = [h for h in hits if e["hint"].lower() in (h.get("description", "") + h.get("label", "")).lower()] or hits[:1]
            n = 0
            for h in hits[:2]:
                claims = get_json("https://www.wikidata.org/w/api.php", {
                    "action": "wbgetclaims", "entity": h["id"], "property": "P18", "format": "json"})
                for k, c in enumerate(claims.get("claims", {}).get("P18", [])[:2], 1):
                    for url, page, _ in commons_files(["File:" + c["mainsnak"]["datavalue"]["value"]]):
                        dest = os.path.join(d, f"wd_{h['id']}_{k}{ext_of(url)}")
                        if download(url, dest):
                            n += 1
                            sources.append({"file": os.path.basename(dest), "from": "wikidata", "url": page,
                                            "about": f"{h['id']} {h.get('description', '')}"})
            report.append(f"wikidata {n}")
        if "commons" in todo:
            r = get_json("https://commons.wikimedia.org/w/api.php", {
                "action": "query", "list": "search", "srsearch": e["commons"], "srnamespace": 6,
                "srlimit": e.get("limit", 6), "format": "json"})
            hits = [{"img": u, "url": p, "about": a} for u, p, a in
                    commons_files([s["title"] for s in r["query"]["search"]])]
            report.append(f"commons {save_hits(d, 'cm', 'commons', hits, sources)}")
        if "baike" in todo:
            item = e["name"] if e["baike"] is True else e["baike"]
            if browser == "ego":
                ego_jobs["baike"].append({"id": eid, "item": item, "hint": e.get("hint", ""), "dir": os.path.abspath(d)})
            else:
                hits = webfetch.baike(item, e.get("hint", ""))
                report.append(f"baike {save_hits(d, 'bk', 'baike', hits, sources)}")
        if "web" in todo:
            queries = [e["web"]] if isinstance(e["web"], str) else e["web"]
            if browser == "ego" and engine == "bing":
                for q in queries:
                    ego_jobs["web"].append({"id": eid, "query": q, "dir": os.path.abspath(d), "limit": e.get("limit", 6)})
            else:
                hits = []
                for q in queries:
                    hits += [dict(h, query=q) for h in webfetch.so360(q, e.get("limit", 6))]
                report.append(f"web {save_hits(d, 'web', 'web', hits, sources)}")
    finally:
        json.dump(sources, open(src_json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{eid}: " + ", ".join(report) + (" (+ego browser jobs)" if any(j["id"] == eid for v in ego_jobs.values() for j in v) else ""))

here = os.path.dirname(os.path.abspath(__file__))
for kind, module in (("baike", "baike.mjs"), ("web", "bing.mjs")):
    jobs = ego_jobs[kind]
    if not jobs:
        continue
    script = os.path.join(work, f"jobs_{module}")
    with open(script, "w", encoding="utf-8") as f:
        f.write(f"const JOBS = {json.dumps(jobs, ensure_ascii=False)};\n")
        f.write(open(os.path.join(here, module), encoding="utf-8").read())
    with open(script, encoding="utf-8") as f:
        subprocess.run(["ego-browser", "nodejs"], stdin=f, check=False)
