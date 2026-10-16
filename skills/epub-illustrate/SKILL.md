---
name: epub-illustrate
description: Add real-photo illustrations to an EPUB e-book without changing a single character of its text — a figure after the paragraph where each person, product, company, place or event first appears (caption "名字 · 身份 · 年份"), and/or a "本章人物与产品" gallery at the end of every chapter. Use when the user wants to enrich, illustrate or add pictures/photos/插图/配图 to an e-book, EPUB, 电子书, or says they can't picture the people/products while reading a book (biographies, business histories, non-fiction).
---

# epub-illustrate

Turns `book.epub` into `book_插图版.epub`. Original text is never edited: figures are new blocks
inserted *between* existing elements and wrapped in `<!--illus:start-->…<!--illus:end-->`;
`verify.py` proves every original file is byte-identical once those blocks are removed.

Scripts live in `scripts/` next to this file (call them by absolute path). Work in a per-book
directory, e.g. `books/<slug>/`, with the EPUB copied to `books/<slug>/original.epub`.

## Step 0 — agree on the approach with the user (always, before any search)

Ask these before doing any work; skip only questions the user has already answered in this
conversation. Use the question tool if available (one call, up to 4 questions), otherwise ask in
plain text. Every question offers the presets below **and** lets the user describe their own
approach ("Other" / free text). Put the recommended preset first.

1. **图片检索方案** (how candidates are found)
   - **后台检索（推荐）**: 本地素材 + Wikidata/Commons + 百度百科 + 360 图片搜索. Baike is rendered
     by a private headless Chrome, 360 answers plain HTTP — runs in the background and never
     touches the user's browser. → `"browser": "headless", "web_engine": "so360"`
   - **使用你的浏览器**: same sources plus Bing Images, through the user's Chrome via ego-browser;
     can get past captchas/logins, but opens tabs in their browser and pauses whenever they take
     control. → `"browser": "ego", "web_engine": "bing"`
   - **仅开放图库**: Wikidata/Commons (+ local files) only — clear provenance, but weak coverage of
     Chinese people and products. → `"sources": ["reuse", "wikidata", "commons"]`
   - **自定义**: e.g. "only use my folder X", "also search site Y", "no web search". Map it onto
     `settings` (`reuse` paths, `sources`, `web` queries); if the scripts can't do it, say so and
     propose the closest option or extend the scripts.
2. **插图放在哪里**
   - **正文首次出现处 + 章末图集（推荐）** → `"inline": true, "gallery": true`
   - **只在正文插图**（不加图集页） → `"inline": true, "gallery": false`
   - **只加章末图集**（正文完全不被打断） → `"inline": false, "gallery": true`
3. **配图范围**
   - **所有新出现的人物、产品、公司、地点（推荐）** — dense; a biography chapter gets 30–50.
   - **只配主要人物和核心产品** — roughly 10–15 per chapter.
   - **自定义** (e.g. "people only", "skip foreign figures").

Write the answers into `entities.json` → `"settings"` (see step 2) and keep them for the whole
book. If the user later changes their mind, update `settings` and rerun from the affected step.

Fixed rules, not options: real images only (photos, scans, screenshots, web archives — never
AI-generated); a person is labeled only when their identity is verified (see step 4).

## Requirements

- A DRM-free EPUB (EPUB 2 or 3). `extract.py` warns if `META-INF/encryption.xml` exists; if the
  book is DRM-protected, stop and tell the user — do not attempt to remove DRM.
- Python 3.9+ with `pip install -r requirements.txt` (Pillow, lxml, certifi). Check with
  `python3 -c "import PIL, lxml, certifi"` and install if missing (ask the user first).
- Google Chrome or Chromium (headless Baike rendering, `preview.py`); set `CHROME=` if not found.
- Java for epubcheck; `epubcheck_diff.py` downloads epubcheck once into `~/.cache/epub-illustrate/`.
- Only for the "使用你的浏览器" option: `ego-browser` (run `ego-browser skill` first). If the user
  takes control of the browser task space, stop and wait for them to say continue.

## Workflow

1. **Index the book**
   `python3 scripts/extract.py original.epub WORKDIR` → `book.json`, `text/NNN.txt` (one file per
   TOC entry, lines `pid<TAB>text`, existing images marked `[已有图片 …]`). The printed TOC
   indices are used for `scope` / `exclude_toc`.

2. **List entities** — read the chapter texts yourself and write `WORKDIR/entities.json`:
   ```json
   {"book": "…",
    "settings": {"browser": "headless", "web_engine": "so360",
                 "sources": ["reuse", "wikidata", "commons", "baike", "web"],
                 "inline": true, "gallery": true, "scope": [11, 18], "exclude_toc": [0, 1, 2]},
    "entities": [
     {"id": "ding_lei", "name": "丁磊", "type": "person", "match": ["丁磊"], "role": "网易创始人",
      "wikidata": true, "hint": "网易", "baike": true, "web": ["丁磊 1997 年轻 照片"]}]}
   ```
   - Follow the agreed 配图范围. Skip passing mentions of universally known figures unless the
     book dwells on them, and skip abstract concepts.
   - `match`: exact strings whose first occurrence (outside headings, excluded TOC entries and
     paragraphs that already contain an image) places the figure. Avoid short ambiguous strings
     (e.g. "小马"); use `"at": "p00123"` to force a paragraph.
   - Check the book's own images first (`[已有图片]`) so you don't duplicate them.
   - Per-entity sources (used only if allowed by `settings.sources`): `reuse` (local files),
     `wikidata` (true or a query; foreign people, famous places), `commons` (query; screenshots,
     logos, places), `baike` (true or 词条; Chinese people/companies/products), `web` (query list;
     old photos, period UI, archived pages). `hint` disambiguates Wikidata hits and Baike 多义词.
   - For a whole book, work chapter by chapter in one entities.json, widening `scope`.

3. **Collect candidates**
   `python3 scripts/find_images.py WORKDIR [ids…] [--only baike,web]`
   New entities only, unless ids are named; `--only` reruns just those sources (replacing their
   old files). Results: `candidates/<id>/` + `SOURCE.json` (origin page + description/summary).

4. **Pick** — `python3 scripts/sheet.py WORKDIR [ids…]` makes contact sheets in `sheets/`; view
   them and set on each entity `"pick": "bk_1.jpg"` (or `"../other_id/file"`), `"caption"`,
   optional `"note"` (second line: "1997 年网页存档", "今貌", "摄于 1998 年") and
   `"crop": [l,t,r,b]` (fractions; full-page web screenshots usually need one).
   - **Identity**: label a person only when ≥2 independent sources show the same face (Baike
     summary picture, news photos, a previously verified local image, a captioned photo). Search
     results and Baike pages mix in other people and namesakes. If unsure, leave `pick` empty or
     caption only what is certain (e.g. the logos in a photo, not the person).
   - Prefer images from the period the text describes (young founders, original product UI,
     archived homepages) over recent ones; say so in `note` when an image is modern.
   - Avoid images dominated by headline text or watermarks (video covers, promo banners), and
     modern-version screenshots presented as historical ones.

5. **Build, verify, look**
   ```
   python3 scripts/inject.py WORKDIR --out <书名>_插图版.epub
   python3 scripts/verify.py original.epub WORKDIR/<书名>_插图版.epub
   python3 scripts/epubcheck_diff.py original.epub WORKDIR/<书名>_插图版.epub
   python3 scripts/preview.py WORKDIR FIRST_TOC LAST_TOC     # PNG pages in WORKDIR/preview/
   ```
   `verify.py` must print OK and `epubcheck_diff.py` must report 0 new error kinds (retail and
   converted books often fail epubcheck on their own markup; only new problems matter). Look at
   the preview pages before handing over. `inject.py` also writes `WORKDIR/配图清单.md`
   (placement, caption and source of every figure, plus entities still missing a picture).

6. **Hand over** — give the user the EPUB path, how many figures/galleries, what is still
   missing and why, and ask for feedback on a sample chapter before doing the whole book.

## Layout notes

- Inline figure: up to 3 entities first mentioned in the same paragraph share one row; single
  images max 12em tall, rows 10em. Gallery: page break, title "本章人物与产品"
  (`gallery_title`), 3 per row.
- Styles are inline (no stylesheet edits) and XHTML 1.1 / EPUB 2 safe (`div`, `p`, `img`).
- Galleries attach to TOC entries matching `chapter_re` (default
  `^第.+章|^前言|^序|^后记|^尾声`); override in settings for other books.
- Images are re-encoded as JPEG ≤1400px into `illus/` next to the OPF and added to the manifest.
- Files are read and written byte-exactly (`newline=""`), so CRLF books stay CRLF.
