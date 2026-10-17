<h1 align="center">Book Illustrator</h1>

<p align="center"><b>An Agent Skill that adds sourced images to a book — after the paragraph where each person, product, place or event first appears — without changing a word of the text.</b></p>

<p align="center">
English · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
<a href="#see-it">See it</a> ·
<a href="#quick-start">Quick start</a> ·
<a href="#install">Install</a> ·
<a href="#requirements">Requirements</a> ·
<a href="#formats">Formats</a> ·
<a href="#limits">Limits</a> ·
<a href="#development">Development</a>
</p>

## See it

Each image shows the **same excerpt** twice: the original book on the left, the skill's actual output on the right. The text is identical; the only change is figures with captions inserted after paragraphs.

<p align="center">
<img src="docs/assets/beagle-before-after.webp" alt="The Voyage of the Beagle: original excerpt beside the same excerpt with photos inserted after paragraphs" width="100%">
<br><sub>Charles Darwin, <i>The Voyage of the Beagle</i> — Project Gutenberg #944. Photo: Bernard Gagnon, CC BY-SA 4.0; resized and re-encoded.</sub>
</p>

<p align="center">
<img src="docs/assets/sanguo-before-after.webp" alt="Romance of the Three Kingdoms: original classical Chinese excerpt beside the illustrated excerpt" width="100%">
<br><sub>Luo Guanzhong, <i>Romance of the Three Kingdoms</i> — Project Gutenberg #23950. Historical painting by Sakurai Sekkan (1715–1790), via Wikimedia Commons / The Field Museum; public-domain reproduction, resized and re-encoded.</sub>
</p>

[Sources, credits and reproduction](examples/README.md).

## What it does

Biographies, business histories and travel or natural-history books introduce dozens of people, products, companies and places you have never seen. Book Illustrator lets your coding agent read the whole book, list those entities, gather real photographs, archived web pages and product screenshots for them, and build an illustrated edition with:

- **Inline figures** — a small captioned figure after the paragraph where an entity first appears (`Name · role`, optional second line such as a year or "modern photo").
- **Chapter galleries** — an end-of-chapter page collecting everything that chapter introduced.

Use either or both. The agent asks two preference questions (where to place figures, how many entities to cover), shows you contact sheets to pick from, and does a sample chapter before the whole book.

## Quick start

After [installing](#install), open your agent and say:

> Illustrate this book with real photos: ~/Books/The-Voyage-of-the-Beagle.epub

If your agent does not pick skills automatically, start with "Use the book-illustrator skill to …". The illustrated copy is written to a separate work folder; your original file is only read.

## Install

Install with the [Skills CLI](https://github.com/vercel-labs/skills):

```bash
npx skills add wavever/book-illustrator-skill
```

Choose your agents when prompted. The default installation is project-local; add `-g` to make the skill available across projects:

```bash
npx skills add wavever/book-illustrator-skill -g
```

For an unattended install to Claude Code:

```bash
npx skills add wavever/book-illustrator-skill --skill book-illustrator -a claude-code -g -y
```

The installer discovers `book-illustrator` under [`skills/book-illustrator/`](skills/book-illustrator/) and installs its instructions, scripts and requirements together. Node.js/npm is needed for this installer; Python is needed when running the skill. No separate npm package is required.

From a local checkout, run `npx skills add . --skill book-illustrator`. Use `npx skills add . --list` to preview the available skill.

<details>
<summary>Manual installation (optional)</summary>

Copy the **whole** `skills/book-illustrator/` folder (`SKILL.md`, `requirements.txt`, `scripts/`). From the repository root:

```bash
# Claude Code, available in every project
mkdir -p ~/.claude/skills && cp -R skills/book-illustrator ~/.claude/skills/

# Claude Code, one project only
mkdir -p /path/to/project/.claude/skills && cp -R skills/book-illustrator /path/to/project/.claude/skills/
```

Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force $HOME\.claude\skills | Out-Null
Copy-Item -Recurse skills\book-illustrator $HOME\.claude\skills\
```

- **Other agents with Agent Skills support**: copy the same folder into the skills directory that agent documents.
- **Any other agent**: keep the folder anywhere and tell the agent: *"Read /path/to/book-illustrator/SKILL.md and follow it to illustrate ~/Books/my-book.epub."*

</details>

### Python packages

The `npx` command installs the skill files. On first use, the agent checks Python dependencies and creates a virtual environment in the book's work folder when Pillow, lxml or certifi are missing. No manual pip step is normally needed. For optional advance setup, use the skill directory reported by the installer:

```bash
SKILL_DIR="/absolute/path/to/installed/book-illustrator"
python3 -m venv ~/.venvs/book-illustrator
~/.venvs/book-illustrator/bin/python -m pip install -r "$SKILL_DIR/requirements.txt"
```

From a repository checkout at its root, the requirements file is `skills/book-illustrator/requirements.txt`; from inside the installed skill folder it is `requirements.txt`. Tell your agent which Python to use (for example, "use ~/.venvs/book-illustrator/bin/python").

## Requirements

| Component | Needed for | Required? |
|---|---|---|
| Maintained Python (3.12+ recommended), Pillow, lxml, certifi; compatibility floor 3.9 | Everything | Yes |
| Network access | Image search (Wikidata, Wikimedia Commons, Openverse) | Yes, unless you supply your own images |
| [Calibre](https://calibre-ebook.com/download) `ebook-convert` | Importing DOCX/TXT/MOBI/AZW3/PDF; exporting to those formats | Only for those formats |
| Java 11+ | `epubcheck_diff.py` (reports new EPUB validation errors) | Optional |
| Google Chrome or Chromium | `preview.py` page renders; opt-in legacy Baidu Baike source | Optional |
| `markdown-it-py` | `preview.py` renders of Markdown books | Optional |
| Node.js | `npx skills` installer; opt-in legacy ego-browser mode | Optional |

No browser, login or API key is needed for the default image search. If your agent has its own web search, it can hand the image URLs it finds to the skill, which downloads and checks them like any other source.

## Formats

| Input | How it is handled | What is guaranteed |
|---|---|---|
| **EPUB 2/3, HTML/XHTML, Markdown** (native) | The original file is only read. A working copy keeps the original markup; figures are added *between* existing elements, wrapped in `<!--illus:start-->…<!--illus:end-->` | `verify.py` removes those blocks and checks every original file is **byte-identical**. Output has the same format. |
| **DOCX, TXT, MOBI, AZW3, text-layer PDF** (normalized) | Calibre converts a copy to `normalized.epub`, which becomes the baseline | The original file is never modified, and the illustrated EPUB is byte-verified against `normalized.epub` — **not** against the original. Conversion reflows the layout. |
| **Export to DOCX, MOBI, AZW3, PDF** | A verified EPUB is converted by Calibre | Layout depends on Calibre; check visually. No byte guarantee. |

Native HTML and Markdown output comes with a `<name>_assets/` folder holding the images.

## How it works

1. `prepare.py` unpacks (or converts) the book and indexes every paragraph.
2. The agent reads each chapter and writes `entities.json` with the entities to illustrate.
3. `find_images.py` collects candidates: your local files → an optional `image_manifest.json` from the agent's own search → Wikidata → Wikimedia Commons → Openverse. Every download is size-limited, fully decoded with Pillow and de-duplicated; provenance goes to `SOURCE.json`. Reruns skip sources that already succeeded and retry the ones that failed.
4. `sheet.py` renders contact sheets with every candidate; you or the agent pick, crop and caption.
5. `export.py` injects the figures and galleries, runs `verify.py`, and converts if needed.
6. The book ends with an image-credits section by default: recorded creators, licenses and source pages, plus changes such as resizing or cropping. The work folder also contains `配图清单.md` with placement details, full provenance and missing images.

Full agent instructions are in [`SKILL.md`](skills/book-illustrator/SKILL.md).

## Limits

- **Only real images.** Photos, scans, screenshots and web archives; nothing AI-generated.
- **People are named only when verified.** At least two independent sources must show the same face. Search hits are not verification, and ambiguous names are flagged for you to settle instead of guessing.
- **Sharing:** check the book and image licenses before distributing an illustrated edition.
- **No DRM.** Protected books are refused; nothing is decrypted.
- **PDF needs a text layer.** Scanned image-only PDFs need OCR, which is not supported.
- **TXT encoding**: UTF-8 and Unicode BOMs are detected; pass `--encoding` for GBK, GB18030, Big5 and similar.
- **Text and markup are preserved, not the visual layout.** In native formats every original byte outside the illustration blocks stays the same, but the added figures still change pagination and flow. Converted and exported formats are also reflowed by Calibre.
- **Source availability varies by region.** Wikidata, Commons and Openverse can be slow, rate-limited or blocked; failures are recorded and retried on the next run.
- **Chinese defaults.** The gallery title (`本章人物与产品`) and chapter pattern default to Chinese books; the agent sets `gallery_title` and `chapter_re` for other languages.

## Development

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r skills/book-illustrator/requirements.txt
.venv/bin/python -m unittest discover -s tests -t . -v
```

Tests use synthetic books, a mocked network and a stub Chrome, so they run offline. Calibre and Java/epubcheck checks are skipped when those tools are absent. See [CONTRIBUTING.md](CONTRIBUTING.md).

```
skills/book-illustrator/
├── SKILL.md            # instructions the agent follows
├── requirements.txt
└── scripts/            # prepare, find_images, sheet, inject, export, verify, …
tests/                  # offline unit and integration tests
docs/assets/            # README demo images (separate rights, see examples/README.md)
examples/               # demo sources, credits and reproduction notes
```

## License

Code: [MIT](LICENSE) · [Demo credits and licenses](docs/assets/LICENSE.md) · [Privacy and data flow](PRIVACY.md)

<sub>Uses the Openverse API; not endorsed or certified by Openverse. [Source terms](https://github.com/WordPress/openverse/blob/main/documentation/terms_of_service.md).</sub>
