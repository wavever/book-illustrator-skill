# Privacy, data flow and source terms

This file describes what the book-illustrator scripts do with your book and what they send over the
network. It describes the scripts only: the AI agent that runs them (Claude Code, Codex, …) is a
separate program with its own data handling.

## What stays on your computer

- **The book.** The scripts read the input book and write a working copy, paragraph index, text
  files, candidate images, contact sheets and previews into the work folder you choose. No script
  uploads the full book, its paragraphs or text files, or the working files. There is no telemetry
  or usage reporting. The only book-derived data the scripts send out are the entity search names
  described below.
- **The original file is never modified.** Native outputs (EPUB/HTML/Markdown) keep every original
  byte outside the inserted `<!--illus:…-->` blocks, including any scripts, forms or remote
  references that were already in the book. The skill does not clean or vet the book's own content;
  whether a reader app runs such content depends on that app.
- **Previews are static.** `preview.py` (and the repository's demo renderer) render a *copy* of the
  book's markup in headless Chrome with a Content-Security-Policy that blocks script, frames, forms
  and all network and `file:` loads; images, fonts and stylesheets from inside the build folder are
  embedded into the page, and Chrome is pointed at an unreachable proxy. This protects the preview
  step only. It is not a statement that the book is safe to open in other programs.
- **Calibre** (`ebook-convert`) runs locally for conversions.

## What the agent sees

To decide what to illustrate, the agent reads the chapter text files in the work folder and looks
at contact sheets and preview images. Depending on the host agent and model, that content may be
sent to a model provider under the agent's terms. With a cloud-hosted model, processing is
therefore **not local-only**; with a locally run model it may stay on your machine. Check your
agent's settings before using the skill on texts you may not share.

## What is sent over the network

| When | Destination | What is sent |
|---|---|---|
| `find_images.py`, default `auto` chain | `www.wikidata.org` API | Each entity's search name (or its `wikidata` override) and search languages, or an explicit QID. The `hint` is matched locally and not sent. |
| | `commons.wikimedia.org` API | File titles from Wikidata, and each entity's search name (or `commons` override). |
| | `api.openverse.org` | Each entity's search name (or `openverse` override) and any `settings.openverse` filters. |
| | Image hosts returned by those services (e.g. `upload.wikimedia.org`, Flickr) | A plain GET for the image URL. No cookies and no Referer. User-Agent: `book-illustrator/2.0 (+project URL) python-urllib`. |
| `image_manifest.json` written by the agent | Whatever hosts the agent put there | A GET for each listed `image_url`. |
| Opt-in legacy `baike` (headless) | `baike.baidu.com` plus whatever that page loads | The entity name in the page URL. The page is rendered in a throwaway Chrome profile with a desktop-browser User-Agent. Images are fetched with that User-Agent and the Baike page as Referer. |
| Opt-in legacy `web` (`so360`) | `image.so.com` | The query, with a browser User-Agent. Result images are fetched with the result page as Referer. |
| Opt-in legacy `"browser": "ego"` | Baidu Baike / Bing via **your own browser** (ego-browser) | Queries run in your real browser session, so its cookies and logins apply. |
| `epubcheck_diff.py`, first run only | `github.com` (w3c/epubcheck release) | A download of epubcheck 5.1.0 into `~/.cache/book-illustrator/`. |

Entity names come from the book, so these requests tell the queried services which people,
products and places your book mentions. The source APIs use HTTPS, so others on the network see the
hosts contacted and traffic metadata rather than the queries. An image URL that is plain `http://`
(possible in a manifest or a legacy result) is visible in transit. Each service handles requests under its
own privacy policy.

## Source terms

The scripts call public endpoints anonymously, honour `Retry-After` and stop a host for the run
when it asks for a longer wait. They contain no CAPTCHA solving, login or paywall bypass, and none
should be added. You remain bound by each source's terms:

- **Wikimedia (Wikidata, Commons):** [Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use),
  [User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy),
  [Privacy policy](https://foundation.wikimedia.org/wiki/Policy:Privacy_policy).
- **Openverse:** [Terms of Service](https://github.com/WordPress/openverse/blob/main/documentation/terms_of_service.md).
  This product uses the Openverse API but is not endorsed or certified by Openverse. Openverse
  aggregates licence metadata from other sites and does not verify it. Its terms also set rate
  limits that must not be circumvented.
- **Baidu Baike, 360 image search, Bing (legacy, opt-in):** these are unofficial page scrapes and
  undocumented endpoints, not supported APIs. They may break or be restricted at any time, and the
  sites' own terms apply. They are never used unless you list them in `settings.sources`.

## Image rights and credits

`SOURCE.json` records for each candidate what the source reported: page, image URL, creator,
licence and licence URL, and credit, attribution, copyright and usage-terms text where available.
Credit fields are kept whole. The illustrated book gets an **image-credits section** (`credits`
setting, on by default) listing, for every inserted image, the creator, licence, source page and
the changes the skill made (JPEG re-encode, downscaling, crop, transparency fill). `配图清单.md`
in the work folder has the same information plus the image URLs.

This metadata comes from the sources and is not verified. A licence label is not proof that you may
use an image, and "unknown" means nothing was recorded. Rights clearance is up to you. Local files
you supply (`reuse`) are credited as "local file supplied by the user", and their paths are not
written into the book.

## Local files the scripts create

- **Work folder:** a copy of the book, `book.json`, `text/`, `candidates/` (images, `SOURCE.json`,
  `STATE.json` with the URLs fetched), `sheets/`, `preview/`, `build/`, `配图清单.md`, and for
  conversions `normalized.epub`, `conversion.json` and `export.json`. Some of these contain
  absolute local paths. Delete the folder when you are done if the book is sensitive.
- **Cache:** `~/.cache/book-illustrator/` (epubcheck only).
- **Temporary Chrome profiles** for previews and legacy Baike rendering are deleted after each run.
