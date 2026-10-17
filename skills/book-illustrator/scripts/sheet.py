"""Contact sheets of ALL candidate images, for picking.

Usage: python3 sheet.py WORKDIR [entity_id ...] [--cols N] [--rows N]
Writes WORKDIR/sheets/sheet_NN.jpg and sheets/index.json (page -> entity -> files). Every
candidate is shown: an entity with more than --cols images (default 6) continues on further rows
and pages. Each cell is labeled with the exact file name to use as "pick", its size and source;
the current pick is outlined in green. Old sheet_*.jpg pages are removed first, so no stale page
survives. Fonts: $BOOK_ILLUSTRATOR_FONT (or the older $EPUB_ILLUSTRATE_FONT), then common system
CJK fonts, then Pillow's built-in font.
"""
import glob
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

IMG_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff")
CJK_FONTS = [
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc", "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/google-noto-cjk/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
    "C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
]
LATIN_FONTS = ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/Library/Fonts/Arial.ttf",
               "/System/Library/Fonts/Supplemental/Arial.ttf", "C:/Windows/Fonts/arial.ttf"]


def load_font(size):
    """(font, supports_cjk)."""
    env = os.environ.get("BOOK_ILLUSTRATOR_FONT") or os.environ.get("EPUB_ILLUSTRATE_FONT")
    for path, cjk in [(env, True)] * bool(env) + [(p, True) for p in CJK_FONTS] + [(p, False) for p in LATIN_FONTS]:
        if path and os.path.exists(path):
            try:
                return ImageFont.truetype(path, size), cjk
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=size), False  # Pillow >= 10.1
    except TypeError:
        return ImageFont.load_default(), False


def fit(draw, text, font, width):
    """Shorten text in the middle with an ellipsis until it fits `width` pixels."""
    if draw.textlength(text, font=font) <= width:
        return text
    keep = len(text)
    while keep > 4:
        keep -= 1
        t = text[:keep // 2] + "…" + text[len(text) - (keep - keep // 2):]
        if draw.textlength(t, font=font) <= width:
            return t
    return text[:4]


def candidates(work, eid):
    """[(file, source label)] in SOURCE.json order, then unlisted image files on disk."""
    d = os.path.join(work, "candidates", eid)
    out, seen = [], set()
    src = os.path.join(d, "SOURCE.json")
    if os.path.exists(src):
        try:
            with open(src, encoding="utf-8") as f:
                rows = json.load(f)
        except ValueError:
            rows = []
        for r in rows if isinstance(rows, list) else []:
            f = r.get("file")
            if f and f not in seen and os.path.isfile(os.path.join(d, f)):
                seen.add(f)
                froms = [r.get("from", "")] + [p.get("from", "") for p in r.get("provenance", [])[1:]]
                out.append((f, "+".join(dict.fromkeys(x for x in froms if x))))
    for p in sorted(glob.glob(os.path.join(d, "*"))):
        f = os.path.basename(p)
        if f not in seen and not f.startswith(".") and f.lower().endswith(IMG_EXT):
            out.append((f, "?"))
    return out


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cols, rows_per = 6, 6
    for flag in ("--cols", "--rows"):
        if flag in args:
            k = args.index(flag)
            val = max(1, int(args[k + 1]))
            del args[k:k + 2]
            cols, rows_per = (val, rows_per) if flag == "--cols" else (cols, val)
    if not args:
        sys.exit(__doc__)
    work, only = args[0], args[1:]
    with open(os.path.join(work, "entities.json"), encoding="utf-8") as f:
        ents = json.load(f)["entities"]
    by_id = {e["id"]: e for e in ents}
    ids = only or [e["id"] for e in ents]
    font, cjk = load_font(16)
    small, _ = load_font(14)
    CELL, HEAD, LABEL = 280, 26, 42

    rows = []  # (eid, header text, [(file, src)], picks)
    for eid in ids:
        e = by_id.get(eid, {"id": eid, "name": eid})
        files = candidates(work, eid)
        p = e.get("pick") or []
        picks = {os.path.basename(x) for x in ([p] if isinstance(p, str) else p)}
        name = f" {e.get('name', '')}" if cjk else ""
        if not files:
            rows.append((eid, f"{eid}{name}: no candidates", [], picks))
        for i in range(0, len(files), cols):
            part = f" ({i + 1}-{min(i + cols, len(files))} of {len(files)})"
            rows.append((eid, f"{eid}{name}{part}", files[i:i + cols], picks))

    out_dir = os.path.join(work, "sheets")
    os.makedirs(out_dir, exist_ok=True)
    for old in glob.glob(os.path.join(out_dir, "sheet_*.jpg")):
        os.remove(old)
    index = []
    for s in range(0, len(rows), rows_per):
        chunk = rows[s:s + rows_per]
        row_h = HEAD + CELL + LABEL
        sheet = Image.new("RGB", (CELL * cols, row_h * len(chunk)), "white")
        d = ImageDraw.Draw(sheet)
        page = {}
        for r, (eid, head, files, picks) in enumerate(chunk):
            y = r * row_h
            d.rectangle((0, y, CELL * cols, y + HEAD - 2), fill=(235, 235, 235))
            d.text((6, y + 4), fit(d, head, font, CELL * cols - 12), fill="red" if not files else "black", font=font)
            page.setdefault(eid, []).extend(f for f, _ in files)
            for c, (f, src) in enumerate(files):
                x, top = c * CELL, y + HEAD
                size = "unreadable"
                try:
                    with Image.open(os.path.join(work, "candidates", eid, f)) as im:
                        size = f"{im.width}x{im.height}"
                        im.thumbnail((CELL - 8, CELL - 8))
                        th = im.convert("RGB")
                    sheet.paste(th, (x + (CELL - th.width) // 2, top + (CELL - th.height) // 2))
                except Exception:  # noqa: BLE001
                    d.rectangle((x + 4, top + 4, x + CELL - 4, top + CELL - 4), outline="gray")
                if f in picks:
                    d.rectangle((x + 1, top + 1, x + CELL - 2, top + CELL + LABEL - 2), outline=(0, 160, 0), width=4)
                d.text((x + 5, top + CELL), fit(d, f, small, CELL - 10), fill="black", font=small)
                d.text((x + 5, top + CELL + 19), fit(d, f"{size} {src}" + (" PICKED" if f in picks else ""),
                                                    small, CELL - 10), fill=(90, 90, 90), font=small)
        path = os.path.join(out_dir, f"sheet_{s // rows_per + 1:02d}.jpg")
        sheet.save(path, quality=82)
        index.append({"sheet": os.path.basename(path), "entities": page})
        print(path)
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return index


if __name__ == "__main__":
    main()
