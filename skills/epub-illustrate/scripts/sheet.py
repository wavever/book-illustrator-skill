"""Contact sheets of the candidate images, one row per entity, for picking.

Usage: python3 sheet.py WORKDIR [entity_id ...]
Writes WORKDIR/sheets/sheet_NN.jpg (8 entities per sheet). Each cell is labeled "<id>/<file>".
"""
import glob
import json
import os
import sys

from PIL import Image, ImageDraw, ImageFont

work = sys.argv[1]
only = sys.argv[2:]
ents = json.load(open(os.path.join(work, "entities.json"), encoding="utf-8"))["entities"]
ids = only or [e["id"] for e in ents]
CELL, COLS, ROWS_PER_SHEET = 300, 8, 8
font = None
for f in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Light.ttc",
          "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"):
    if os.path.exists(f):
        font = ImageFont.truetype(f, 18)
        break
font = font or ImageFont.load_default()

rows = []
for eid in ids:
    order = {"reuse": 0, "wd": 1, "cm": 2, "bk": 3}
    files = sorted((f for f in glob.glob(os.path.join(work, "candidates", eid, "*"))
                    if not f.endswith((".json", ".txt"))),
                   key=lambda f: (order.get(os.path.basename(f).split("_")[0], 9), f))
    rows.append((eid, files[:COLS]))

out_dir = os.path.join(work, "sheets")
os.makedirs(out_dir, exist_ok=True)
for s in range(0, len(rows), ROWS_PER_SHEET):
    chunk = rows[s:s + ROWS_PER_SHEET]
    sheet = Image.new("RGB", (CELL * COLS, (CELL + 26) * len(chunk)), "white")
    d = ImageDraw.Draw(sheet)
    for r, (eid, files) in enumerate(chunk):
        y = r * (CELL + 26)
        if not files:
            d.text((8, y + CELL // 2), f"{eid}: no candidates", fill="red", font=font)
        for c, f in enumerate(files):
            try:
                im = Image.open(f).convert("RGB")
            except Exception:  # noqa: BLE001
                continue
            w, h = im.size
            im.thumbnail((CELL - 6, CELL - 6))
            sheet.paste(im, (c * CELL + 3, y + 3))
            d.text((c * CELL + 4, y + CELL - 2), f"{eid}/{os.path.basename(f)} {w}x{h}", fill="black", font=font)
    path = os.path.join(out_dir, f"sheet_{s // ROWS_PER_SHEET + 1:02d}.jpg")
    sheet.save(path, quality=82)
    print(path)
