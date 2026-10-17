"""Fixture helpers for conversion and export testing without external downloads."""

import io
import struct
import tempfile
import zipfile
from pathlib import Path
from PIL import Image, ImageDraw


def create_synthetic_image(text="TEST IMAGE", color="#b5d6ee", size=(320, 180)) -> bytes:
    """Create a synthetic PNG image in memory."""
    im = Image.new("RGB", size, color)
    draw = ImageDraw.Draw(im)
    draw.rectangle((10, 10, size[0] - 10, size[1] - 10), outline="#293548", width=2)
    draw.text((20, size[1] // 2 - 10), text, fill="#182133")
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def create_synthetic_epub(
    dest_path: Path,
    title="Synthetic Test Book",
    chinese_english=True,
    include_image=True,
) -> Path:
    """Create a synthetic EPUB2 book with two chapters, nested containers, and notes."""
    img_bytes = create_synthetic_image("TEST CHAPTER IMAGE")

    ch1_text = (
        "Alice founded an innovative laboratory. 爱丽丝创办了一家创新实验室。<br/>"
        "Special terms: café, résumé &amp; ampersand."
    ) if chinese_english else "Alice founded a lab."

    ch2_text = (
        "Atlas developed a modern navigation system. 阿特拉斯开发了现代导航系统。<br/>"
        "Beacon emitted lighthouse signals across the strait."
    ) if chinese_english else "Atlas developed a system."

    container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""

    opf_xml = """<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="BookID">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>""" + title + """</dc:title>
    <dc:language>zh-CN</dc:language>
    <dc:identifier id="BookID">urn:uuid:test-synthetic-book-12345</dc:identifier>
    <dc:creator>Test Suite</dc:creator>
  </metadata>
  <manifest>
    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
    <item id="img1" href="images/sample.png" media-type="image/png"/>
  </manifest>
  <spine toc="ncx">
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>"""

    ncx_xml = """<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head><meta name="dtb:uid" content="urn:uuid:test-synthetic-book-12345"/></head>
  <docTitle><text>""" + title + """</text></docTitle>
  <navMap>
    <navPoint id="nav1" playOrder="1">
      <navLabel><text>第一章 启程</text></navLabel>
      <content src="chapter1.xhtml"/>
    </navPoint>
    <navPoint id="nav2" playOrder="2">
      <navLabel><text>第二章 远航</text></navLabel>
      <content src="chapter2.xhtml"/>
    </navPoint>
  </navMap>
</ncx>"""

    ch1_html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>第一章 启程</title></head>
<body>
  <h1>第一章 启程</h1>
  <div class="chapter-content">
    <section>
      <p>{ch1_text}</p>
      <p><img src="images/sample.png" alt="Existing note figure"/>Existing note figure caption.</p>
    </section>
  </div>
</body>
</html>"""

    ch2_html = f"""<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.1//EN" "http://www.w3.org/TR/xhtml11/DTD/xhtml11.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>第二章 远航</title></head>
<body>
  <h1>第二章 远航</h1>
  <div class="chapter-content">
    <p>{ch2_text}</p>
    <blockquote><p>Historical note on maritime exploration.</p></blockquote>
  </div>
</body>
</html>"""

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container_xml)
        z.writestr("OEBPS/content.opf", opf_xml)
        z.writestr("OEBPS/toc.ncx", ncx_xml)
        z.writestr("OEBPS/chapter1.xhtml", ch1_html)
        z.writestr("OEBPS/chapter2.xhtml", ch2_html)
        if include_image:
            z.writestr("OEBPS/images/sample.png", img_bytes)

    return dest_path


def create_drm_epub(dest_path: Path) -> Path:
    """Create a synthetic EPUB with META-INF/encryption.xml simulating DRM."""
    create_synthetic_epub(dest_path)
    enc_xml = """<?xml version="1.0" encoding="UTF-8"?>
<encryption xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <EncryptedData xmlns="http://www.w3.org/2001/04/xmlenc#">
    <EncryptionMethod Algorithm="http://www.w3.org/2001/04/xmlenc#aes128-cbc"/>
    <CipherData>
      <CipherReference URI="OEBPS/chapter1.xhtml"/>
    </CipherData>
  </EncryptedData>
</encryption>"""
    with zipfile.ZipFile(dest_path, "a") as z:
        z.writestr("META-INF/encryption.xml", enc_xml)
    return dest_path


def create_drm_mobi(dest_path: Path) -> Path:
    """Create a minimal synthetic MOBI file with encryption type set to 2."""
    header = bytearray(256)
    header[0:14] = b"TestMobiBook\x00\x00"
    struct.pack_into(">H", header, 76, 1)
    rec0_offset = 96
    struct.pack_into(">I", header, 78, rec0_offset)
    struct.pack_into(">H", header, rec0_offset, 1)
    struct.pack_into(">I", header, rec0_offset + 4, 100)
    struct.pack_into(">H", header, rec0_offset + 8, 1)
    struct.pack_into(">H", header, rec0_offset + 10, 4096)
    struct.pack_into(">H", header, rec0_offset + 12, 2)

    dest_path.parent.mkdir(parents=True, exist_ok=True)
    dest_path.write_bytes(header)
    return dest_path


def create_scanned_pdf(dest_path: Path) -> Path:
    """Create a synthetic image-only scanned PDF using Pillow with zero text layer."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (600, 800), "#ffffff")
    draw = ImageDraw.Draw(im)
    draw.rectangle((50, 50, 550, 750), outline="#000000", width=4)
    # Save as PDF via Pillow - creates purely an image-based PDF
    im.save(dest_path, "PDF")
    return dest_path
