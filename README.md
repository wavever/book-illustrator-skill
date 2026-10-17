<h1 align="center">Book Illustrator</h1>

<p align="center"><b>An Agent Skill that adds sourced images to a book — after the paragraph where each person, product, place or event first appears — without changing a word of the text.</b></p>

<p align="center">
English · <a href="README.zh-CN.md">简体中文</a>
</p>

<p align="center">
<a href="#see-it">See it</a> ·
<a href="#use-it-with-your-agent">Use it with your agent</a> ·
<a href="#install">Install</a> ·
<a href="#requirements">Requirements</a> ·
<a href="#formats">Formats</a> ·
<a href="#limits">Limits</a> ·
<a href="#development">Development</a>
</p>

## See it

Each image shows the **same passages** twice: the original book on the left, the skill's actual output on the right. Each row is one passage; the text is identical, and the only change is captioned photos inserted after paragraphs. Text between the selected passages is omitted.

<p align="center">
<img src="docs/assets/carnegie-before-after.webp" alt="Autobiography of Andrew Carnegie: three original passages beside the same passages with photos of Andrew Carnegie, Henry Phipps and Henry Clay Frick inserted" width="100%">
<br><sub><i>Autobiography of Andrew Carnegie</i> — Project Gutenberg #17976. Photos of Andrew Carnegie, Henry Phipps and Henry Clay Frick: Library of Congress.</sub>
</p>

<p align="center">
<img src="docs/assets/li-hongzhang-before-after.webp" alt="Chinese excerpts beside the same excerpts with photos of Li Hongzhang, Itō Hirobumi and Mutsu Munemitsu inserted" width="100%">
<br><sub>Liang Qichao, <i>Li Hongzhang</i> (梁启超《李鸿章传》), Chapter 8. Photos of Li Hongzhang, Itō Hirobumi and Mutsu Munemitsu.</sub>
</p>

[Sources, credits and reproduction](examples/README.md).

## What it does

Biographies, business histories and travel or natural-history books introduce dozens of people, products, companies and places you have never seen. Book Illustrator lets your coding agent read the whole book, list those entities, gather real photographs, archived web pages and product screenshots for them, and build an illustrated edition with:

- **Inline figures** — a small captioned figure after the paragraph where an entity first appears (`Name · role`, optional second line such as a year or "modern photo").
- **Chapter galleries** — an end-of-chapter page collecting everything that chapter introduced.

Use either or both. The agent asks two preference questions (where to place figures, how many entities to cover), shows you contact sheets to pick from, and does a sample chapter before the whole book.

## Use it with your agent

1. **Install** the skill with the one-line command under [Install](#install).
2. **Restart your agent** (or open a new session) so it picks up the new skill.
3. **Give it the book**: attach the file, or paste its absolute path, such as `/Users/me/Books/Autobiography-of-Andrew-Carnegie.epub`.
4. **Paste a prompt** that names the skill and says what you want: which chapters, which people, figures in the text or a gallery per chapter, a sample first, and the original left untouched.
5. **Review the sample.** The agent shows contact sheets of candidate images; you choose, then tell it to continue.

A complete first prompt:

> Use the book-illustrator skill on /Users/me/Books/Autobiography-of-Andrew-Carnegie.epub. Start with Chapters I–XVI. Illustrate the main people — Andrew Carnegie, Henry Phipps, Henry Clay Frick and Carnegie's other partners — with real archival photos. Put a captioned photo after the paragraph where each person first appears; no chapter galleries. Do one sample chapter first and show me the result. Don't modify my original file.

Follow-ups after the sample:

> Fewer pictures: only people who come up again later, at most five per chapter.

> Looks good. Do the whole book the same way and also give me a DOCX copy.

What you get, in a separate work folder (the agent tells you the path):

- an illustrated copy of the book: the same format for EPUB, HTML and Markdown; an illustrated EPUB for TXT, DOCX, MOBI, AZW3 and PDF. Other [supported formats](#formats) on request;
- your original file, unchanged;
- an image-credits section at the end of the book, and `配图清单.md` listing each picture with its source, license and placement;
- a short report of figures added and people for whom no reliable photo was found.

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
