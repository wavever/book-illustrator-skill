"""Candidate image providers for find_images.py.

Each provider returns a list of hits (dicts) and never downloads image bytes itself:
  imgs:   [image URL, fallback URL, ...]   (or "path": local file for reuse/manifest files)
  page:   page the image is described on (source page)
  plus whatever metadata the provider really has: title, about, author, license, license_url,
  date, entity (Wikidata QID), match (how the entity was resolved), family (who hosts the file —
  Wikidata P18 and Commons search share the "wikimedia" family: one file is not two
  independent identity sources).
Providers raise imgfetch.FetchError for transport problems and Ambiguous when an entity name
cannot be resolved without guessing.
"""
import re
import urllib.parse

from imgfetch import FetchError

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
OPENVERSE_API = "https://api.openverse.org/v1/images/"
THUMB_WIDTH = 1400
QID_RE = re.compile(r"^Q[1-9]\d*$")
SKIP_MIME = ("video/", "audio/", "application/")


class Ambiguous(Exception):
    def __init__(self, message, options=()):
        super().__init__(message)
        self.options = list(options)


def _strip_html(value):
    value = (value or "").strip()
    if not value:
        return ""
    if "<" in value:
        from lxml import html as lhtml
        try:
            value = lhtml.fromstring(f"<div>{value}</div>").text_content()
        except Exception:  # noqa: BLE001
            value = re.sub(r"<[^>]+>", "", value)
    return " ".join(value.split())


def _meta(ext, key, limit=None):
    """Plain text of an extmetadata field; `limit` only for descriptive fields, never for credit,
    licence or copyright fields (those are kept whole)."""
    value = _strip_html((ext.get(key) or {}).get("value", ""))
    return value[:limit] if limit else value


# Commons extmetadata fields kept verbatim (as plain text) for attribution: SOURCE.json key -> field
CREDIT_FIELDS = {"author": "Artist", "credit": "Credit", "attribution": "Attribution",
                 "attribution_required": "AttributionRequired", "license": "LicenseShortName",
                 "license_url": "LicenseUrl", "usage_terms": "UsageTerms", "copyrighted": "Copyrighted",
                 "restrictions": "Restrictions"}


def _commons_hits(pages):
    hits = []
    for p in sorted(pages, key=lambda p: p.get("index", 0)):
        ii = (p.get("imageinfo") or [None])[0]
        if not ii or ii.get("mime", "").startswith(SKIP_MIME) or not ii.get("url"):
            continue
        ext = ii.get("extmetadata") or {}
        h = {"imgs": [u for u in (ii.get("thumburl"), ii["url"]) if u],
             "page": ii.get("descriptionurl", ""), "title": p.get("title", ""),
             "about": _meta(ext, "ImageDescription", 200),
             **{k: _meta(ext, field) for k, field in CREDIT_FIELDS.items()},
             "date": _meta(ext, "DateTimeOriginal", 80), "family": "wikimedia"}
        hits.append({k: v for k, v in h.items() if v})
    return hits


def commons_files(http, titles):
    """Commons imageinfo (with author/license metadata) for File: titles, in the given order."""
    pages = []
    for i in range(0, len(titles), 20):
        r = http.get_json(COMMONS_API, {
            "action": "query", "titles": "|".join(titles[i:i + 20]), "prop": "imageinfo",
            "iiprop": "url|size|mime|extmetadata", "iiurlwidth": THUMB_WIDTH, "format": "json",
            "formatversion": 2})
        got = {p.get("title"): p for p in (r.get("query") or {}).get("pages", [])}
        norm = {n["from"]: n["to"] for n in (r.get("query") or {}).get("normalized", [])}
        for k, t in enumerate(titles[i:i + 20]):
            p = got.get(norm.get(t, t))
            if p:
                pages.append(dict(p, index=i + k))
    return _commons_hits(pages)


def commons_search(http, query, limit):
    r = http.get_json(COMMONS_API, {
        "action": "query", "generator": "search", "gsrsearch": query, "gsrnamespace": 6,
        "gsrlimit": min(max(limit * 2, limit), 50), "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": THUMB_WIDTH, "format": "json",
        "formatversion": 2})
    hits = _commons_hits((r.get("query") or {}).get("pages", []))
    for h in hits:
        h["query"] = query
    return hits


def _text(h):
    return " ".join([h.get("label", ""), h.get("description", ""),
                     (h.get("match") or {}).get("text", "")] + list(h.get("aliases") or [])).lower()


def _option(h):
    return {"qid": h["id"], "label": h.get("label", ""), "description": h.get("description", "")}


def wikidata_resolve(http, query, hint="", qid="", languages=("zh", "en")):
    """{"qid", "label", "description", "match"}; never silently takes the first search result.
    "match" says how the *name* was resolved (explicit | hint | unique | exact_label) — it is not
    evidence that a picture shows the person."""
    if qid:
        return {"qid": qid, "label": "", "description": "", "match": "explicit"}
    results, first, pool = [], [], []
    for lang in languages:  # the hint may be written in any of the search languages
        results = http.get_json(WIKIDATA_API, {
            "action": "wbsearchentities", "search": query, "language": lang, "uselang": lang,
            "type": "item", "format": "json", "limit": 10}).get("search", [])
        first = first or results
        if not hint and results:
            break
        if hint:
            pool = [h for h in results if hint.lower() in _text(h)]
            if pool:
                break
    if not first:
        return None
    how = "unique"
    if hint:
        how = "hint"
        if not pool:
            raise Ambiguous(f'no Wikidata result for "{query}" matches hint "{hint}"; '
                            'set "qid" on the entity (or change "hint")', [_option(h) for h in first[:7]])
    else:
        pool = results
    if len(pool) > 1:
        exact = [h for h in pool if h.get("label", "").lower() == query.lower()
                 or (h.get("match") or {}).get("text", "").lower() == query.lower()]
        if len(exact) == 1:
            pool, how = exact, how if how == "hint" else "exact_label"
        else:
            raise Ambiguous(f'{len(pool)} Wikidata entities match "{query}"'
                            + (f' with hint "{hint}"' if hint else "")
                            + '; set "qid" on the entity (or a more specific "hint")',
                            [_option(h) for h in pool[:7]])
    h = pool[0]
    return {"qid": h["id"], "label": h.get("label", ""), "description": h.get("description", ""), "match": how}


def wikidata(http, query, hint="", qid="", limit=4, languages=("zh", "en")):
    """Images (P18 image, P154 logo) of the one Wikidata entity the name resolves to."""
    ident = wikidata_resolve(http, query, hint, qid, languages)
    if ident is None:
        return [], None
    r = http.get_json(WIKIDATA_API, {"action": "wbgetentities", "ids": ident["qid"],
                                      "props": "claims|labels|descriptions",
                                      "languages": "|".join(languages), "format": "json"})
    ent = (r.get("entities") or {}).get(ident["qid"]) or {}
    if "missing" in ent or not ent:
        raise Ambiguous(f'Wikidata entity {ident["qid"]} does not exist; fix "qid"', [])
    for lang in languages:
        ident["label"] = ident["label"] or ((ent.get("labels") or {}).get(lang) or {}).get("value", "")
        ident["description"] = ident["description"] or ((ent.get("descriptions") or {}).get(lang) or {}).get("value", "")
    files = []
    for prop in ("P18", "P154"):
        for c in (ent.get("claims") or {}).get(prop, []):
            v = ((c.get("mainsnak") or {}).get("datavalue") or {}).get("value")
            if isinstance(v, str) and c.get("rank") != "deprecated" and v not in files:
                files.append(v)
    hits = commons_files(http, ["File:" + f for f in files[:limit]]) if files else []
    about = f'{ident["qid"]} {ident["label"]}: {ident["description"]}'.strip(" :")
    for h in hits:
        h.update(entity=ident["qid"], entity_match=ident["match"], about=about,
                 wikidata_url=f'https://www.wikidata.org/wiki/{ident["qid"]}')
    return hits, ident


def openverse(http, query, limit=4, extra=None):
    """Openverse anonymous image search (openly licensed works; https://api.openverse.org/v1/)."""
    params = {"q": query, "page_size": max(1, min(int(limit) * 2, 20)), "mature": "false"}
    for k, v in (extra or {}).items():
        if k in ("license", "license_type", "source", "excluded_source", "category", "extension",
                 "aspect_ratio", "size"):
            params[k] = v
    r = http.get_json(OPENVERSE_API, params)
    if not isinstance(r, dict) or "results" not in r:
        raise FetchError("http", "unexpected Openverse response")
    hits = []
    for x in r["results"]:
        imgs = [u for u in (x.get("url"), x.get("thumbnail")) if u]
        if not imgs:
            continue
        lic = " ".join(s for s in (str(x.get("license") or "").upper(), str(x.get("license_version") or "")) if s)
        src = x.get("source") or x.get("provider") or ""
        h = {"imgs": imgs, "page": x.get("foreign_landing_url") or "", "title": x.get("title") or "",
             "author": x.get("creator") or "", "license": lic, "license_url": x.get("license_url") or "",
             "attribution": x.get("attribution") or "", "creator_url": x.get("creator_url") or "",
             "openverse_id": x.get("id") or "", "provider": src,
             "family": "wikimedia" if src == "wikimedia" else f"openverse:{src or 'unknown'}",
             "query": query}
        hits.append({k: v for k, v in h.items() if v})
    return hits


def manifest_hits(entries, base_dir):
    """Hits from the neutral host-agent manifest entries of one entity."""
    import os
    hits = []
    for x in entries:
        h = {"page": x.get("source_page") or x.get("page") or "", "title": x.get("title") or "",
             "about": x.get("about") or x.get("description") or "", "author": x.get("author") or "",
             "license": x.get("license") or "", "license_url": x.get("license_url") or "",
             "date": x.get("date") or "", "query": x.get("query") or "", "found_by": x.get("found_by") or "",
             **{k: x.get(k) or "" for k in ("credit", "attribution", "copyright", "usage_terms")}}
        if x.get("image_url"):
            h["imgs"] = [x["image_url"]]
        elif x.get("file"):
            h["path"] = os.path.join(base_dir, os.path.expanduser(x["file"]))
        else:
            continue
        page_host = urllib.parse.urlsplit(h["page"] or x.get("image_url", "")).netloc
        h["family"] = page_host or "manifest"
        hits.append({k: v for k, v in h.items() if v})
    return hits
