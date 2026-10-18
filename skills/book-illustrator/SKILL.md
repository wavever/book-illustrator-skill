---
name: book-illustrator
description: Add real-photo illustrations to a book without changing a single character of its native text — a figure after the paragraph where each person, product, company, place or event first appears (caption "名字 · 身份 · 年份"), and/or a "本章人物与产品" gallery at the end of every chapter. Supports native EPUB, HTML, and Markdown (byte-preserved), plus DRM-free TXT, DOCX, MOBI, AZW3, and text-layer PDF via Calibre normalization. Use when the user wants to illustrate a book, e-book, 电子书 or long document, add pictures/photos/插图/配图 to it, or says they cannot picture the people, products or places while reading (biographies, business histories, travel and history non-fiction).
license: MIT
---

# Book Illustrator

Turns `book.epub` (or `.html`, `.md`, `.docx`, `.txt`, `.mobi`, `.azw3`, `.pdf`) into an illustrated edition (`book_插图版.epub` or other supported formats).

- **Native formats** (`.epub`, `.html`, `.md`): Original text is never edited; figures are new blocks inserted *between* existing elements wrapped in `<!--illus:start-->…<!--illus:end-->`; `verify.py` proves every original file is byte-identical once those blocks are removed.
- **Conversion formats** (`.txt`, `.docx`, `.mobi`, `.azw3`, `.pdf`): Original files on disk remain strictly untouched; Calibre reflows layout and normalizes markup into `WORKDIR/normalized.epub`, which serves as the byte-integrity validation baseline for all subsequent builds.

`SKILL_DIR` below means the directory containing this file; scripts live in `SKILL_DIR/scripts/` (call them by absolute path, so the current directory does not matter). Work in a per-book directory, e.g. `books/<slug>/`, with the book copied or referenced as input. Never write into `SKILL_DIR`.

**Privacy and terms** ([PRIVACY.md](PRIVACY.md); code under the [MIT licence](LICENSE), which does not cover books or images): the scripts never upload the full book, its paragraphs or text files, or the working files. The only book-derived text they send is the entity search names (derived from the book), which go to Wikidata, Wikimedia Commons and Openverse (legacy Baidu Baike/360/Bing only when opted in). Depending on the host agent and model, text and images you read may reach your model provider. Before the first search, tell the user this if they have not already been told. This skill uses the Openverse API but is not endorsed or certified by Openverse; Openverse licence metadata is aggregated and not verified. Respect each source's rate limits (the scripts honour Retry-After). Never add CAPTCHA, login or paywall workarounds.

## Step 0 — agree on illustration preferences (before any search)

Image acquisition is automatic and needs no choice from the user: the default `auto` chain uses only Python + network (no browser, Node, login or API key). Do **not** ask the user to pick search infrastructure. Ask only the questions below that the user has not already answered in this conversation (question tool if available, one call; otherwise plain text). Each offers the presets **and** free text; recommended preset first.

If the user volunteers sourcing wishes ("use my folder X", "also use Baidu Baike", "only open-licence images") or asks for smaller files / specific compression ("compact", "keep under 80KB"), map them onto `settings`/entity keys (steps 2 & 3) without further questions (automatic image compression defaults to `balanced`).

1. **插图放在哪里**
   - **正文首次出现处 + 章末图集（推荐）** → `"inline": true, "gallery": true`
   - **只在正文插图**（不加图集页） → `"inline": true, "gallery": false`
   - **只加章末图集**（正文完全不被打断） → `"inline": false, "gallery": true`
2. **配图范围**
   - **所有新出现的人物、产品、公司、地点（推荐）** — dense; a biography chapter gets 30–50.
   - **只配主要人物和核心产品** — roughly 10–15 per chapter.
   - **自定义** (e.g. "people only", "skip foreign figures").

Write the answers into `entities.json` → `"settings"` (see step 2) and keep them for the whole book. If the user later changes their mind, update `settings` and rerun from the affected step.

Fixed rules, not options: real images only (photos, scans, screenshots, web archives — never AI-generated); a person is labeled only when their identity is verified (see step 4).

## Requirements & Format Support

- **Native formats**: DRM-free EPUB (2 or 3), HTML (`.html`, `.htm`, `.xhtml`), and Markdown (`.md`, `.markdown`). Processed natively without quality loss; original source bytes preserved.
- **Conversion formats**: DRM-free TXT, DOCX, MOBI, AZW3, and PDF. Converted into `normalized.epub` by Calibre before extraction. Requires local Calibre (`ebook-convert` at `/Applications/calibre.app/Contents/MacOS/ebook-convert` or in PATH, or `--calibre`).
- **DRM**: Only DRM-free books are supported. Encrypted files are rejected; do not attempt decryption.
- **PDF**: Requires an extractable text layer for reflow. Scanned image-only PDFs require OCR, which is not supported and will be rejected.
- **TXT**: UTF-8 and Unicode BOMs are automatically detected. For ambiguous encodings (GBK/GB18030/Big5), pass `--encoding` explicitly to prevent silent mojibake.
- **Python 3.9+** (compatibility floor; prefer a currently maintained Python, 3.12+, because 3.9 receiving the code is not a security guarantee) with Pillow, lxml, certifi. If they are missing, bootstrap an isolated env in the work directory instead of touching the user's global Python: `python3 -m venv WORKDIR/.venv && WORKDIR/.venv/bin/python -m pip install -r SKILL_DIR/requirements.txt`, then run the scripts with `WORKDIR/.venv/bin/python` (Windows: `py -m venv WORKDIR\.venv`, `WORKDIR\.venv\Scripts\python.exe -m pip install -r SKILL_DIR\requirements.txt`, run with `WORKDIR\.venv\Scripts\python.exe`). In the commands below, `python3` stands for whichever interpreter has these packages.
- **Image acquisition** (`find_images.py`, `sheet.py`) needs only the above plus network access. Wikidata, Commons and Openverse may be slow, rate-limited or unreachable in some regions; failures are recorded and retried on the next run.
- **Google Chrome or Chromium — optional**: only for `preview.py` page renders and the opt-in legacy `baike` source (headless); set `CHROME=` if not found. Previews render a static copy of the book's markup: no script, frames or network, and only files from `WORKDIR/build`. The built book itself is unchanged, including any active content the original already had. ego-browser/Node only for the opt-in legacy `"browser": "ego"` mode.
- **Java — optional**, only for `epubcheck_diff.py`; it downloads epubcheck once into `~/.cache/book-illustrator/` (a jar already cached under the pre-rename `~/.cache/epub-illustrate/` is reused). Without Java, skip that check and say so in the hand-over.
- **Contact-sheet font**: `sheet.py` finds common system CJK fonts; set `BOOK_ILLUSTRATOR_FONT=/path/to/font.ttc` if labels render as boxes (the older `EPUB_ILLUSTRATE_FONT` still works).
- **Non-Chinese books**: captions are whatever you write; set `settings.gallery_title` (e.g. `"People and places in this chapter"`) and `settings.chapter_re` (e.g. `"^CHAPTER"`) so galleries get a fitting title and attach to the right TOC entries.
- **Optional**: `pip install markdown-it-py` only if using `preview.py` to render PNG preview pages for Markdown documents; native Markdown extraction, illustration, and verification do not require it.

## Workflow

1. **Prepare and index the book**
   ```bash
   python3 SKILL_DIR/scripts/prepare.py INPUT WORKDIR [--calibre PATH] [--encoding ENCODING]
   ```
   - For native formats (`.epub`, `.html`, `.md`), delegates directly to `extract.py`.
   - For convertible formats (`.txt`, `.docx`, `.mobi`, `.azw3`, `.pdf`), converts to `WORKDIR/normalized.epub`, writes `WORKDIR/conversion.json` provenance, and runs `extract.py`.
   - Writes `book.json` and `text/NNN.txt` (one file per TOC entry, lines `pid<TAB>text`, existing images marked `[已有图片 …]`).

2. **List entities** — read the chapter texts yourself and write `WORKDIR/entities.json`:
   ```json
   {"book": "…",
    "settings": {"inline": true, "gallery": true, "scope": [11, 18], "exclude_toc": [0, 1, 2]},
    "entities": [
     {"id": "ding_lei", "name": "丁磊", "type": "person", "match": ["丁磊"], "role": "网易创始人",
      "hint": "网易"}]}
   ```
   - Follow the agreed 配图范围. Skip passing mentions of universally known figures unless the book dwells on them, and skip abstract concepts.
   - `match`: exact strings whose first occurrence places the figure. Avoid short ambiguous strings (e.g. "小马"); use `"at": "p00123"` to force a paragraph.
   - Check the book's own images first (`[已有图片]`) so you don't duplicate them.
   - `hint` (any search language) disambiguates Wikidata; `"qid": "Q…"` pins the entity. Optional per-source query overrides: `"commons": "query"`, `"openverse": "query"`, `false` to skip a source.
   - **Image compression** (`settings.image_compression`): automatic default `"balanced"`. Presets:
     - `"compact"`: max 800px, quality 72, target 80 KiB, min quality 50, min side 480px.
     - `"balanced"`: max 1024px, quality 80, target 120 KiB, min quality 60, min side 640px.
     - `"high"`: max 1600px, quality 88, target 300 KiB, min quality 70, min side 1024px; quality-first and may be larger than balanced or older releases.
     Or an object overriding single keys: `{"preset": "balanced", "target_kb": 150, "max_side": 1200}` (`target_kb: null` disables byte budget). Byte targets are best effort: when min quality and size floors are reached first, the smallest encode is kept with `target_met = false`. `min_side` is a floor only (never upscaled). Downloaded originals in `candidates/` are retained untouched.
   - For a whole book, work chapter by chapter in one entities.json, widening `scope`.

3. **Collect candidates**
   ```bash
   python3 SKILL_DIR/scripts/find_images.py WORKDIR [ids…] [--only SRC[,SRC]] [--force]
   python3 SKILL_DIR/scripts/find_images.py WORKDIR --status      # per-source status, ambiguity options
   ```
   - Default `auto` chain, staged until an entity has `target` candidates (default 6, at most `per_source` = 4 per source): `reuse` (entity `"reuse": [paths]`, relative to WORKDIR — e.g. images from the extracted book or an earlier project) → `manifest` → `wikidata` (P18/P154 of the one resolved entity) → `commons` search → `openverse` (anonymous API).
   - **Host search handoff (optional)**: if you have your own web/image search tool, write found candidates to `WORKDIR/image_manifest.json`; they are downloaded and validated like any other source:
     ```json
     {"version": 1, "candidates": [
       {"entity": "ding_lei", "image_url": "https://…/a.jpg", "source_page": "https://…/article",
        "title": "…", "author": "…", "license": "…", "license_url": "…", "date": "1998", "query": "丁磊 1998"}]}
     ```
     Use `"file": "relative/path.jpg"` instead of `image_url` for files you already downloaded. Only record metadata you actually saw; leave fields out otherwise. `credit`, `attribution`, `copyright` and `usage_terms` are also accepted. Copy them verbatim, because they appear in the book's image-credits section.
   - Reruns are cheap and safe: sources that succeeded with unchanged configuration are skipped; failed, empty, partial and ambiguous ones are retried; changing an entity's query/hint/qid/limits re-queries that source. `--only SRC` / `--force` re-query regardless. Nothing is deleted before a successful refresh, picked files are never deleted, and identical bytes from several sources become one file with all provenance in `SOURCE.json`.
   - Every download is fully decoded with Pillow (corrupt/HTML payloads rejected), capped by bytes/pixels, named `<src>_<sha256[:12]>.<ext>` with the extension of the real format. `settings.limits` tunes `timeout`, `retries`, `max_wait` (longest Retry-After honoured; longer rate limits fail fast and retry next run), `max_bytes`, `max_pixels`, `min_side`.
   - **Ambiguous** entities (several Wikidata matches, or hint matches none) get no Wikidata images; `--status` lists options — set `"qid"` or a better `"hint"` and rerun.
   - **Legacy sources (opt-in only)** are unofficial page scrapes / undocumented endpoints, subject to those sites' own terms (see [PRIVACY.md](PRIVACY.md)): `"sources": ["auto", "baike", "web"]` with entity `"baike": true | "词条"` or `"baike_url": "https://baike.baidu.com/item/…"` (alone is enough; skips sense selection), `"web": "query"`. `"browser": "headless"` (default) renders Baike in a private headless Chrome and uses 360 image search via direct HTTPS requests (no browser); `"browser": "ego", "web_engine": "bing"` uses the user's browser via ego-browser. A Baike 多义词 page is followed only when `hint` selects exactly one sense.
   - **Migration from older entities.json**: old `sources` lists keep working (they now also get validation, state and dedup). `baike`/`web` no longer run unless listed in `settings.sources` — a `"browser"` setting alone prints a notice. Existing `candidates/<id>/SOURCE.json` files are adopted as-is; their files (and picks such as `bk_1.jpg`) are kept and those sources count as done; use `--force` to refresh.

4. **Pick** — `python3 SKILL_DIR/scripts/sheet.py WORKDIR [ids…]` makes contact sheets in `sheets/` showing **every** candidate (long rows continue on further rows/pages; stale pages are removed; `sheets/index.json` maps pages to files; the current pick is outlined). View them and set on each entity:
   - `"pick": "file"` in `candidates/<id>/` (or `["file1", "file2"]` for multi-figure rows);
   - `"caption"`: first caption line (default `"name · role"`);
   - `"note"`: optional second caption line ("1997 年网页存档", "今貌", "摄于 1998 年");
   - `"crop": [l, t, r, b]`: fractions 0–1 to crop candidate image, measured on the upright image (EXIF orientation applied, exactly as `sheet.py` shows it; full-page screenshots usually need one).
   - **Identity**: label a person only when ≥2 independent sources show the same face. Retrieval is not verification: `entity_match` in `SOURCE.json` (`identity.match` in `STATE.json`) only says how the *name* was resolved (`explicit` / `hint` / `unique` / `exact_label`). `family` is a provenance heuristic for shared hosting: Wikidata P18, Commons and Openverse-from-Wikimedia copies of one file share the `wikimedia` family and are never separate confirmations — but different families are not proof of independence either; judge the pages yourself.
   - Prefer period-appropriate images over modern ones; note modern images explicitly.

5. **Build, verify, preview, and export**

   > **Note on `--out` paths**: Always specify explicit absolute paths or `WORKDIR/<output_file>`, because relative paths passed to `--out` resolve against CWD.

   ### Case A: Native same-format export (`.epub`, `.html`, `.md`)
   ```bash
   python3 SKILL_DIR/scripts/export.py WORKDIR --out WORKDIR/<书名>_插图版.epub

   # verify.py checks byte identity against original input file (runs automatically inside export.py):
   python3 SKILL_DIR/scripts/verify.py original.epub WORKDIR/<书名>_插图版.epub

   # EPUB pairs only: check for new validation errors
   python3 SKILL_DIR/scripts/epubcheck_diff.py original.epub WORKDIR/<书名>_插图版.epub

   # Render preview pages (Chrome)
   python3 SKILL_DIR/scripts/preview.py WORKDIR FIRST_TOC LAST_TOC
   ```
   *Note for HTML/Markdown*: Native output generates the illustrated document plus an adjacent `OUTPUT_STEM_assets/` resource directory containing all referenced images.

   ### Case B: Conversion-input job exporting to EPUB
   ```bash
   python3 SKILL_DIR/scripts/export.py WORKDIR --out WORKDIR/<书名>_插图版.epub

   # verify.py checks byte identity against normalized.epub baseline (NOT original non-EPUB file):
   python3 SKILL_DIR/scripts/verify.py WORKDIR/normalized.epub WORKDIR/<书名>_插图版.epub

   # EPUB format check against normalized.epub baseline:
   python3 SKILL_DIR/scripts/epubcheck_diff.py WORKDIR/normalized.epub WORKDIR/<书名>_插图版.epub

   python3 SKILL_DIR/scripts/preview.py WORKDIR FIRST_TOC LAST_TOC
   ```

   ### Case C: Cross-format export (`.docx`, `.mobi`, `.azw3`, `.pdf`)
   ```bash
   python3 SKILL_DIR/scripts/export.py WORKDIR --out WORKDIR/<书名>_插图版.docx [--calibre PATH]
   ```
   - `export.py` automatically builds the native intermediate (`WORKDIR/illustrated_intermediate.epub`), executes `verify.py` against the baseline (`original.epub` or `normalized.epub`), and only invokes Calibre after intermediate verification passes.
   - Downstream converted files (`.docx`, `.pdf`, `.mobi`, `.azw3`) undergo layout reflow and cannot be checked by `verify.py` or `epubcheck_diff.py`; conduct visual inspection and text/image checks.

6. **Hand over** — give the user the output path, figure/gallery count, compression stats (preset used, inserted images count, candidate bytes → image bytes, targets unmet if any; full details in `WORKDIR/compression-report.json`), missing items and notes, and ask for feedback on a sample chapter before doing the whole book. Say that the book ends with an image-credits section (`settings.credits`, default on; `credits_title` renames it). For every inserted image it lists creator, licence and URL, credit text, source page and the changes made (JPEG re-encode with quality, original JPEG data retained/stripped, downscale, crop on upright coordinates, and transparency fill only when applied). Attribution comes from `SOURCE.json`; the exporter records the image transformations. Missing values read "unknown/未知". Point the user to `WORKDIR/配图清单.md`, which has the same information plus image URLs and compression stats. This metadata is not verified, and a licence label is not permission. Rights clearance stays with the user, so never describe images as cleared. If the user turns credits off, tell them to keep `配图清单.md` with the book when sharing it.

## Layout Notes

- **Inline figures**: Up to 3 entities first mentioned in the same paragraph share one row; single images max 12em tall, rows 10em.
- **Chapter galleries**: Page break, title "本章人物与产品" (`gallery_title`), 3 items per row. Galleries attach to TOC entries matching `chapter_re` (default `^第.+章|^前言|^序|^后记|^尾声`).
- **HTML & Markdown output**:
  - HTML/Markdown outputs always ship with an adjacent resource directory (`<OUTPUT_STEM>_assets/`).
  - Local images and stylesheets from the original package are copied beside the output.
  - Markdown preserves original block structures, code fences, and indentation without injecting arbitrary pagination.
- **Encoding and offsets**: Files are read and written byte-exactly (`newline=""`), preserving CRLF/LF line endings and original character encodings.
- **EPUB styling**: Styles are inline (no stylesheet edits) and XHTML 1.1 / EPUB 2 safe (`div`, `p`, `img`). Images are compressed baseline JPEG (see `image_compression` settings; default `balanced` max 1024px, 80 quality, 120 KiB target) into `illus/` next to the OPF and registered in the manifest.
