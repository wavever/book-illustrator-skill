# Reading comparisons

The README shows three selected passages and three real portrait photographs from each book. These are outputs of the illustration pipeline, not mock-ups. Separate passages are labelled; omitted text is marked between rows. Each displayed paragraph is shown whole, and original/illustrated text is checked before rendering.

| Demo | Text | Photographs |
|---|---|---|
| [李鸿章传](demos/li-hongzhang.json) | 梁启超，1901；第八章三个选段 | 李鸿章、伊藤博文、陆奥宗光 |
| [Autobiography of Andrew Carnegie](demos/carnegie.json) | Andrew Carnegie, edited by John C. Van Dyke, 1920; Chapters I, XIII, XVI | Andrew Carnegie, Henry Phipps, Henry Clay Frick |

The Chinese example is a working EPUB made from the original chapter body on [Wikisource, pinned revision 2489933](https://zh.wikisource.org/w/index.php?title=李鴻章傳/08&oldid=2489933). It excludes website navigation and adds a chapter heading. It is not a modern annotated edition. The English example uses the [Project Gutenberg #17976 EPUB without images](https://www.gutenberg.org/ebooks/17976). Only comparison images, scripts and metadata are shipped; source books, downloaded portraits and extracted chapter files stay outside the repository.

## Sources and image changes

Complete source URLs, dates, captions, crops, paragraph selections and reproduction configuration are in the linked per-book JSON files. The combined [demo-manifest.json](demo-manifest.json) is the renderer input. [Asset rights and attribution](../docs/assets/LICENSE.md) cover the comparison images.

- **Li Hongzhang:** Russell & Sons, 1896; reproduced in Mrs Archibald Little, *Li Hung Chang: His Life and Times* (1903). [Commons source](https://commons.wikimedia.org/wiki/File:Li_Hung_Chang_in_1896.jpg), marked PD-US.
- **Ito Hirobumi:** [National Diet Library portrait 12](https://www.ndl.go.jp/portrait/e/datas/12/).
- **Mutsu Munemitsu:** [National Diet Library portrait 199](https://www.ndl.go.jp/portrait/e/datas/199/). Both NDL portraits are photographs, with no capture date or photographer recorded on their catalog pages. Source: **Portraits of Modern Japanese Historical Figures, National Diet Library**. The [exhibition reuse policy](https://www.ndl.go.jp/portrait/e/about) states that its portrait images are public domain and requires a source credit.
- **Andrew Carnegie:** Frances Benjamin Johnston, c. 1905. [Library of Congress, LC-USZ62-88699](https://www.loc.gov/pictures/item/2001704108/), Frances Benjamin Johnston Collection. Catalog: “No known restrictions on reproduction.”
- **Henry Phipps:** Bain News Service (publisher), undated. [Library of Congress, LC-DIG-ggbain-06856](https://www.loc.gov/pictures/item/2014686848/), George Grantham Bain Collection.
- **Henry Clay Frick:** Bain News Service (publisher), between ca. 1915 and ca. 1920. [Library of Congress, LC-DIG-ggbain-29798](https://www.loc.gov/pictures/item/2014709956/), George Grantham Bain Collection. Both Bain records: “No known restrictions on publication”; [collection rights statement](https://www.loc.gov/rr/print/res/274_bain.html).

Photographs are resized and re-encoded for the illustrated EPUBs. Phipps and Frick are cropped to remove negative borders and labels; any additional crops are recorded in the manifests. These portraits illustrate the people mentioned; their capture dates do not necessarily match the events in each passage.

## Reproduce

Run from the repository root using Python with the [skill dependencies](../skills/book-illustrator/requirements.txt) installed. Calibre is required to build the Chinese source EPUB; Chrome/Chromium is needed only for the final comparison screenshots. Use an absolute scratch directory **outside this repository**.

```bash
WORKDIR="/absolute/path/to/scratch/book-demos"
mkdir -p "$WORKDIR/carnegie/dl"
curl --fail --location \
  https://www.gutenberg.org/ebooks/17976.epub.noimages \
  --output "$WORKDIR/carnegie/dl/original.epub"
python3 skills/book-illustrator/scripts/prepare.py \
  "$WORKDIR/carnegie/dl/original.epub" "$WORKDIR/carnegie/job"
python3 examples/prepare-li-hongzhang.py \
  --work-dir "$WORKDIR/li-hongzhang"
```

The Chinese adapter fetches and validates the pinned Wikisource API revision, builds the working EPUB with Calibre, and indexes it. To supply a custom converter path, add `--calibre /path/to/ebook-convert`.

Write the ready-made entities and source manifests, download and select the photographs, export, and verify:

```bash
python3 - "$WORKDIR" <<'PYTHON'
import json
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1]).resolve()
scripts = Path("skills/book-illustrator/scripts")
for name in ("li-hongzhang", "carnegie"):
    demo = json.loads(Path(f"examples/demos/{name}.json").read_text())
    paths = {k: root / v for k, v in demo["relative_paths"].items()}
    job = paths["book_json"].parent
    entities = demo["entities"]
    for filename, value in (("entities.json", entities),
                            ("image_manifest.json", demo["image_manifest"])):
        (job / filename).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    def run(script, *args):
        subprocess.run([sys.executable, str(scripts / script),
                        *map(str, args)], check=True)
    run("find_images.py", job)
    for entity in entities["entities"]:
        records = json.loads((job / "candidates" / entity["id"] / "SOURCE.json").read_text())
        if not records:
            raise RuntimeError(f"No downloaded photograph for {entity['id']}")
        entity["pick"] = records[0]["file"]
    (job / "entities.json").write_text(json.dumps(entities, ensure_ascii=False, indent=2))
    run("export.py", job, "--out", paths["illus_epub"])
    run("verify.py", paths["orig_epub"], paths["illus_epub"])
PYTHON

python3 examples/render-comparison.py \
  --manifest examples/demo-manifest.json --work-dir "$WORKDIR" --all
```

Each book should report **4 inserted blocks**: three inline photographs and one image-credit section, with original files byte-identical outside them. This comparison is against the prepared EPUB; for the Chinese sample it does not compare EPUB bytes with web HTML. The renderer independently checks each selected passage, resolves pictures from the exported EPUB, and produces `docs/assets/li-hongzhang-before-after.webp` and `docs/assets/carnegie-before-after.webp`.

The manifests record the tested source edition and candidate hashes. Upstream EPUB revisions can change paragraph IDs: inspect `book.json` and update the selection if necessary. Changed image bytes produce new candidate filenames; the reproduction snippet selects the downloaded candidate from `SOURCE.json`. Review downloaded portraits before using a changed upstream source.
