"""Prove the illustrated EPUB keeps the original text: strip every <!--illus:start-->…<!--illus:end-->
block from each content file and require it to be byte-identical to the original. Also checks that
every inserted image exists in the package. (Standards validation: epubcheck_diff.py.)

Usage: python3 verify.py ORIGINAL.epub ILLUSTRATED.epub
"""
import re
import sys
import zipfile

orig, new = zipfile.ZipFile(sys.argv[1]), zipfile.ZipFile(sys.argv[2])
BLOCK = re.compile(rb"<!--illus:start-->.*?<!--illus:end-->", re.S)
ok = True
names_new = set(new.namelist())
inserted = 0
for name in orig.namelist():
    if name.endswith("/"):
        continue  # directory entry
    a = orig.read(name)
    if name not in names_new:
        print("MISSING", name)
        ok = False
        continue
    b = new.read(name)
    if name.endswith(".opf"):
        b = re.sub(rb'<item href="illus/[^"]+" id="illus-[^"]+" media-type="image/jpeg"/>\n', b"", b)
    inserted += len(BLOCK.findall(b))
    if BLOCK.sub(b"", b) != a:
        print("CHANGED", name)
        ok = False
content = b"".join(new.read(n) for n in new.namelist() if n.endswith((".html", ".xhtml", ".htm")))
for src in set(re.findall(rb'src="(illus/[^"]+)"', content)):
    if not any(n.endswith(src.decode()) for n in names_new):
        print("MISSING IMAGE", src.decode())
        ok = False
print(f"OK: {inserted} inserted blocks; all original files byte-identical outside them" if ok else "FAILED")
sys.exit(0 if ok else 1)
