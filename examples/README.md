# Book Illustrator Examples & Before/After Demos

This directory contains reproduction tools, metadata manifests, and attribution records for the demonstration comparison assets used in the project documentation.

## Demo Overview

| Demo | Work | Excerpt Scope | Illustration Subject | Source & License |
| :--- | :--- | :--- | :--- | :--- |
| **Sanguo** | *三國志演義 / 三國演義* (羅貫中) | Ch. 1, §宴桃園豪傑三結義 (p00026, 254 chars) | 桃園三結義 · 劉備、關羽、張飛 | **Sakurai Sekkan (櫻井雪館)**, The Field Museum, Public domain (PD-Art / PD-old-100) |
| **Beagle** | *The Voyage of the Beagle* (Charles Darwin) | Ch. XVII, Galapagos Archipelago (p00728, 229 words) | Galápagos Giant Tortoise (*Chelonoidis vicina*) | **Bernard Gagnon**, Charles Darwin Research Station, [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) |

The rendered side-by-side comparison images live in `docs/assets/`:
- `docs/assets/sanguo-before-after.webp` (1240 × 842 px, ~92 KB)
- `docs/assets/beagle-before-after.webp` (1240 × 1104 px, ~148 KB)

---

## Attribution & Rights

Both demonstration works use texts and visual materials that are public domain or openly licensed. See [asset licenses and release status](../docs/assets/LICENSE.md). The Beagle comparison contributions and photograph are explicitly covered by CC BY-SA 4.0; public-domain texts remain public domain. The Sanguo comparison layout is contributed under CC BY 4.0; underlying public-domain text and historical artwork remain public domain.

The illustrations are resized and re-encoded for the illustrated EPUBs and reading comparisons. Their original credits and licenses are recorded below.

### 1. Sanguo Case (*三國志演義*)

- **Source Text**:
  - **Work**: 《三國志演義》 / 《三國演義》
  - **Author**: 羅貫中 (Luo Guanzhong, c. 1330–1400)
  - **Source Edition**: Project Gutenberg EBook #23950 ([https://www.gutenberg.org/ebooks/23950](https://www.gutenberg.org/ebooks/23950)), produced by Jian-Lun Huang.
  - **Status & Rights**: Public domain in the USA per Project Gutenberg catalog page. Classical Chinese original text with no modern commentary or annotations. Full source e-books and complete extracted texts remain scratch and are not bundled in this repository.
  - **Excerpt Scope**: Chapter 1 section "宴桃園豪傑三結義，斬黃巾英雄首立功" (paragraph `p00026`; 254 characters describing the peach garden oath of Liu Bei, Guan Yu, and Zhang Fei).
- **Illustration**:
  - **Subject**: Three Brothers (Liu Bei, Guan Yu, Zhang Fei) / 桃園三結義
  - **Artist / Repository**: Sakurai Sekkan (櫻井雪館 / 桜井雪館, 1715–1790), Japanese Edo-period painter; collection of The Field Museum of Natural History.
  - **Source Page**: [Wikimedia Commons File:Three_Brothers_(Bei,_Yu,_Fei).jpg](https://commons.wikimedia.org/wiki/File:Three_Brothers_(Bei,_Yu,_Fei).jpg)
  - **Image URL**: `https://upload.wikimedia.org/wikipedia/commons/9/91/Three_Brothers_%28Bei%2C_Yu%2C_Fei%29.jpg`
  - **Date**: Undated on the source page; attributed artist lived 1715–1790 (Edo period)
  - **License**: Public domain (PD-Art / PD-old-100; faithful photographic reproduction PD in USA; terms may differ in other jurisdictions).
  - **Verbatim Credit**: The Field Museum
  - **Caption in Book**: `桃園三結義 · 劉備、關羽、張飛` / `櫻井雪館（1715—1790）繪絹本畫作 · 菲爾德自然史博物館藏（歷史畫作）` (explicitly labeled as a historical artistic depiction, not a contemporaneous photograph).

### 2. Beagle Case (*The Voyage of the Beagle*)

- **Source Text**:
  - **Work**: *The Voyage of the Beagle* (Journal of Researches into the Natural History and Geology of the Countries Visited During the Voyage of H.M.S. Beagle Round the World)
  - **Author**: Charles Darwin
  - **Source Edition**: Project Gutenberg EBook #944 ([https://www.gutenberg.org/ebooks/944](https://www.gutenberg.org/ebooks/944))
  - **Status & Rights**: Public domain in the USA per Project Gutenberg catalog page.
  - **Excerpt Scope**: Chapter XVII — Galapagos Archipelago, paragraph `p00728` (229 words, 1284 characters describing the habits and anatomy of *Testudo nigra*).
- **Illustration**:
  - **Subject**: Galápagos Giant Tortoise (*Chelonoidis vicina*)
  - **Photographer**: Bernard Gagnon
  - **Source Page**: [Wikimedia Commons File:Chelonoidis vicina 04.jpg](https://commons.wikimedia.org/wiki/File:Chelonoidis_vicina_04.jpg)
  - **Image URL**: `https://upload.wikimedia.org/wikipedia/commons/c/cf/Chelonoidis_vicina_04.jpg`
  - **Date**: 2014-12-07
  - **Location**: Charles Darwin Research Station, Santa Cruz Island, Galápagos National Park, Ecuador
  - **License**: [Creative Commons Attribution-ShareAlike 4.0 International (CC BY-SA 4.0)](https://creativecommons.org/licenses/by-sa/4.0/)
  - **Caption in Book**: `Galápagos Giant Tortoise · Chelonoidis vicina` / `Modern reference photo (Bernard Gagnon, 2014)` (explicitly labeled as a modern reference photo so there is no implied voyage-era photography).

---

## Machine-Readable Metadata

The configuration and provenance for each demo are declared in [`demo-manifest.json`](demo-manifest.json):
- Book titles and subtitles
- Language codes (`zh` / `en`)
- Paragraph IDs (`pids`) identifying excerpt boundaries
- Relative paths for input EPUBs, illustrated EPUBs, and `book.json` indices
- Complete rights statements, image URLs, author credits, and license links

---

## Complete End-to-End Reproduction Workflow

All scratch books, downloaded images, and build artifacts are kept outside the repository in a user-chosen working directory (e.g. `WORKDIR=~/work/demos`).

### Step 1: Initialize Scratch Workspaces and Obtain Input EPUBs

```bash
# Set your scratch working directory
WORKDIR="/path/to/your/scratch/demos"
mkdir -p "$WORKDIR/beagle" "$WORKDIR/sanguo"

# 1. Charles Darwin - Voyage of the Beagle (Project Gutenberg #944)
curl -L -o "$WORKDIR/beagle/original.epub" "https://www.gutenberg.org/ebooks/944.epub.noimages"
python3 skills/book-illustrator/scripts/prepare.py "$WORKDIR/beagle/original.epub" "$WORKDIR/beagle"

# 2. Luo Guanzhong - Romance of the Three Kingdoms (Project Gutenberg #23950)
curl -L -o "$WORKDIR/sanguo/original.epub" "https://www.gutenberg.org/ebooks/23950.epub.noimages"
python3 skills/book-illustrator/scripts/prepare.py "$WORKDIR/sanguo/original.epub" "$WORKDIR/sanguo"
```

### Step 2: Configure Demo Entities & Source Manifests

Write the following ready-to-copy configurations to the respective book folders:

#### Beagle Configuration

`$WORKDIR/beagle/image_manifest.json`:
```json
{
  "version": 1,
  "candidates": [
    {
      "entity": "galapagos_tortoise",
      "image_url": "https://upload.wikimedia.org/wikipedia/commons/c/cf/Chelonoidis_vicina_04.jpg",
      "source_page": "https://commons.wikimedia.org/wiki/File:Chelonoidis_vicina_04.jpg",
      "title": "File:Chelonoidis vicina 04.jpg",
      "author": "Bernard Gagnon",
      "date": "2014-12-07",
      "license": "CC BY-SA 4.0",
      "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
      "query": "Galapagos giant tortoise"
    }
  ]
}
```

`$WORKDIR/beagle/entities.json`:
```json
{
  "book": "The Voyage of the Beagle",
  "settings": {
    "inline": true,
    "gallery": false,
    "gallery_title": "Chapter gallery",
    "sources": ["manifest"],
    "scope": [19, 19]
  },
  "entities": [
    {
      "id": "galapagos_tortoise",
      "name": "Galápagos Giant Tortoise",
      "type": "animal",
      "at": "p00728",
      "match": ["Testudo nigra"],
      "role": "Chelonoidis vicina",
      "caption": "Galápagos Giant Tortoise · Chelonoidis vicina",
      "note": "Modern reference photo (Bernard Gagnon, 2014)"
    }
  ]
}
```

#### Sanguo Configuration

`$WORKDIR/sanguo/image_manifest.json`:
```json
{
  "version": 1,
  "candidates": [
    {
      "entity": "three_brothers",
      "image_url": "https://upload.wikimedia.org/wikipedia/commons/9/91/Three_Brothers_%28Bei%2C_Yu%2C_Fei%29.jpg",
      "source_page": "https://commons.wikimedia.org/wiki/File:Three_Brothers_(Bei,_Yu,_Fei).jpg",
      "title": "File:Three Brothers (Bei, Yu, Fei).jpg",
      "author": "Sakurai Sekkan (櫻井雪館, 1715–1790)",
      "credit": "The Field Museum",
      "date": "Undated on source page; artist lived 1715–1790",
      "license": "Public domain (PD-Art / PD-old-100)",
      "license_url": "https://commons.wikimedia.org/wiki/File:Three_Brothers_(Bei,_Yu,_Fei).jpg",
      "query": "Three Brothers Bei Yu Fei Sakurai Sekkan"
    }
  ]
}
```

`$WORKDIR/sanguo/entities.json`:
```json
{
  "book": "三國演義",
  "settings": {
    "inline": true,
    "gallery": false,
    "sources": ["manifest"],
    "scope": [0, 0]
  },
  "entities": [
    {
      "id": "three_brothers",
      "name": "桃園結義",
      "type": "event",
      "at": "p00026",
      "match": ["桃園", "結為兄弟", "劉備", "關羽", "張飛"],
      "role": "劉備、關羽、張飛桃園三結義",
      "caption": "桃園三結義 · 劉備、關羽、張飛",
      "note": "櫻井雪館（1715—1790）繪絹本畫作 · 菲爾德自然史博物館藏（歷史畫作）"
    }
  ]
}
```

### Step 3: Fetch Candidates and Set Picks

```bash
# 1. Download and decode candidate images via manifest
python3 skills/book-illustrator/scripts/find_images.py "$WORKDIR/beagle"
python3 skills/book-illustrator/scripts/find_images.py "$WORKDIR/sanguo"

# 2. Select the downloaded candidate filename recorded in SOURCE.json into entities.json "pick"
python3 -c '
import json, os, sys
for slug in ["beagle", "sanguo"]:
    work = os.path.join(sys.argv[1], slug)
    ent_file = os.path.join(work, "entities.json")
    with open(ent_file, "r+", encoding="utf-8") as f:
        data = json.load(f)
        for e in data.get("entities", []):
            src_file = os.path.join(work, "candidates", e["id"], "SOURCE.json")
            if os.path.isfile(src_file):
                src_data = json.load(open(src_file, encoding="utf-8"))
                if src_data:
                    e["pick"] = src_data[0]["file"]
        f.seek(0); f.truncate(); json.dump(data, f, indent=2, ensure_ascii=False)
' "$WORKDIR"
```

### Step 4: Export and Byte-Verify

```bash
# Export illustrated editions (credits: true by default appends an in-book image source section)
python3 skills/book-illustrator/scripts/export.py "$WORKDIR/beagle" --out "$WORKDIR/beagle/beagle_illustrated.epub"
python3 skills/book-illustrator/scripts/export.py "$WORKDIR/sanguo" --out "$WORKDIR/sanguo/sanguo_illustrated.epub"

# Verify byte identity: proves native original text is 100% byte-identical outside <!--illus:...--> blocks
python3 skills/book-illustrator/scripts/verify.py "$WORKDIR/beagle/original.epub" "$WORKDIR/beagle/beagle_illustrated.epub"
python3 skills/book-illustrator/scripts/verify.py "$WORKDIR/sanguo/original.epub" "$WORKDIR/sanguo/sanguo_illustrated.epub"
```

Both verify checks will output:
```
OK: 2 inserted blocks; original files byte-identical outside them
```
(Corresponding to 1 inline figure block and 1 in-book image sources credit block; original text is byte-identical outside these inserted blocks).

### Step 5: Render Visual Comparison Assets

```bash
# Render all demos declared in examples/demo-manifest.json
python3 examples/render-comparison.py \
  --manifest examples/demo-manifest.json \
  --work-dir "$WORKDIR" \
  --all
```

Outputs:
- `docs/assets/sanguo-before-after.webp`
- `docs/assets/beagle-before-after.webp`

> **Notes on Environment & Editions**:
> 1. **Chrome Requirement**: Headless Google Chrome or Chromium is required only to re-render the screenshot comparisons in Step 5 (`render-comparison.py`). The core pipeline (`prepare.py`, `find_images.py`, `export.py`, `verify.py`) runs on pure Python without Chrome.
> 2. **Paragraph IDs**: The paragraph IDs (`p00026` and `p00728`) are determined by `prepare.py` during extraction. If a different edition or variation of an EPUB is used, inspect `book.json` and adjust the IDs accordingly.
> 3. **Truthfulness Guarantee**: `render-comparison.py` asserts that stripped illustrated text matches original text byte-for-byte before inlining images:
>    ```python
>    stripped = BLOCK_RE.sub("", illus_frag)
>    assert stripped == orig_frag, "Stripped illustrated excerpt must strictly match original excerpt"
>    ```
