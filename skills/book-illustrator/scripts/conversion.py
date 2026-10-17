"""Format conversion, DRM detection, and encoding normalization for book-illustrator.

Handles conversion of non-native book formats (txt, docx, mobi, azw3, pdf) to
normalized EPUB using local Calibre ebook-convert, and exporting verified EPUB
to supported downstream formats (epub, docx, mobi, azw3, pdf).
"""

import codecs
import hashlib
import json
import os
import posixpath
import re
import shutil
import struct
import subprocess
import sys
import zipfile
import zlib
from pathlib import Path
from xml.etree import ElementTree as ET

# Supported input extensions
NATIVE_INPUT_FORMATS = {".epub", ".html", ".htm", ".xhtml", ".md", ".markdown"}
CONVERTIBLE_INPUT_FORMATS = {".txt", ".docx", ".mobi", ".azw3", ".pdf"}

# Supported cross-format export extensions (via ebook-convert)
CROSS_EXPORT_FORMATS = {".epub", ".docx", ".mobi", ".azw3", ".pdf"}

# Standard Calibre executable locations
DEFAULT_CALIBRE_PATHS = [
    "/Applications/calibre.app/Contents/MacOS/ebook-convert",
    "/usr/bin/ebook-convert",
    "/usr/local/bin/ebook-convert",
    "/opt/homebrew/bin/ebook-convert",
]


class ConversionError(Exception):
    """Raised when a format conversion fails."""
    pass


class CalibreNotFoundError(ConversionError):
    """Raised when Calibre's ebook-convert tool cannot be located."""
    pass


class DRMUnsupportedError(ConversionError):
    """Raised when DRM encryption is detected in the input file."""
    pass


class ScannedPDFError(ConversionError):
    """Raised when a PDF has no extractable text layer (scanned/image-only)."""
    pass


class UnsupportedFormatError(ConversionError):
    """Raised when an input or output format is not supported."""
    pass


def find_calibre(custom_path=None) -> str:
    """Locate Calibre ebook-convert binary with actionable error if missing.

    If custom_path is specified, only that path is checked without fallback.
    """
    if custom_path:
        p = Path(custom_path).expanduser().resolve()
        if p.is_file() and os.access(p, os.X_OK):
            return str(p)
        raise CalibreNotFoundError(
            f"Specified Calibre binary '{custom_path}' does not exist or is not executable.\n"
            "Please verify the path or install Calibre from https://calibre-ebook.com/download"
        )

    candidates = []
    env_path = os.environ.get("CALIBRE_CONVERT")
    if env_path:
        candidates.append(Path(env_path).expanduser().resolve())

    which_path = shutil.which("ebook-convert")
    if which_path:
        candidates.append(Path(which_path).resolve())

    for p in DEFAULT_CALIBRE_PATHS:
        candidates.append(Path(p).resolve())

    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)

    error_msg = (
        "Calibre 'ebook-convert' was not found.\n"
        "Conversion requires Calibre to be installed.\n"
        "- Download and install Calibre from: https://calibre-ebook.com/download\n"
        "- Or specify the executable path using: --calibre /path/to/ebook-convert\n"
        "- Or set the CALIBRE_CONVERT environment variable."
    )
    raise CalibreNotFoundError(error_msg)


def find_pdftotext(calibre_bin: str = None) -> str:
    """Locate bundled Calibre or system pdftotext executable."""
    candidates = []
    if calibre_bin:
        calibre_path = Path(calibre_bin).resolve()
        # macOS Calibre bundle layout: .../Contents/utils.app/Contents/MacOS/pdftotext
        candidates.append(calibre_path.parent.parent.parent / "utils.app" / "Contents" / "MacOS" / "pdftotext")
        candidates.append(calibre_path.parent / "pdftotext")
    candidates.append(Path("/Applications/calibre.app/Contents/utils.app/Contents/MacOS/pdftotext"))
    which_pdf = shutil.which("pdftotext")
    if which_pdf:
        candidates.append(Path(which_pdf))

    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def get_calibre_version(calibre_bin: str) -> str:
    """Get version string from ebook-convert, raising error on nonzero return."""
    try:
        proc = subprocess.run(
            [calibre_bin, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            raise ConversionError(
                f"Calibre binary at '{calibre_bin}' returned error code {proc.returncode}: {proc.stderr}"
            )
        out = (proc.stdout or proc.stderr).strip()
        m = re.search(r"calibre\s+([\d.]+)", out, re.I)
        if m:
            return m.group(1)
        return out.splitlines()[0] if out else "unknown"
    except (subprocess.TimeoutExpired, OSError) as e:
        raise ConversionError(f"Failed to execute Calibre version check: {e}") from e


def detect_format(path: str) -> str:
    """Return lowercase suffix including dot, e.g. '.epub'."""
    return Path(path).suffix.lower()


def is_native_format(path: str) -> bool:
    """Check if file format is handled natively without Calibre."""
    return detect_format(path) in NATIVE_INPUT_FORMATS


def is_convertible_input(path: str) -> bool:
    """Check if file format can be converted to EPUB via Calibre."""
    return detect_format(path) in CONVERTIBLE_INPUT_FORMATS


def compute_sha256(path: Path) -> str:
    """Calculate SHA256 checksum of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def check_drm(input_path: Path) -> None:
    """Check for DRM encryption in supported formats and raise DRMUnsupportedError if found."""
    ext = detect_format(input_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    # Check EPUB DRM
    if ext == ".epub":
        try:
            with zipfile.ZipFile(input_path, "r") as z:
                if "META-INF/encryption.xml" in z.namelist():
                    enc_xml = z.read("META-INF/encryption.xml").decode("utf-8", errors="ignore")
                    if re.search(r"<(?:\w+:)?EncryptedData\b[^>]*>(?:(?!FontMangling|font).)*?</(?:\w+:)?EncryptedData>", enc_xml, re.DOTALL | re.I):
                        raise DRMUnsupportedError(
                            f"DRM protection detected in EPUB '{input_path.name}'. "
                            "book-illustrator only supports DRM-free books and does not decrypt protected content."
                        )
                if "META-INF/rights.xml" in z.namelist():
                    raise DRMUnsupportedError(
                        f"DRM rights record detected in EPUB '{input_path.name}'. "
                        "book-illustrator only supports DRM-free books."
                    )
        except zipfile.BadZipFile:
            pass

    # Check MOBI / AZW3 DRM
    elif ext in {".mobi", ".azw3", ".azw", ".prc"}:
        try:
            with open(input_path, "rb") as f:
                data = f.read(1024)
            if len(data) >= 82:
                rec0_offset = struct.unpack(">I", data[78:82])[0]
                if len(data) >= rec0_offset + 14:
                    enc_type = struct.unpack(">H", data[rec0_offset + 12 : rec0_offset + 14])[0]
                    if enc_type != 0:
                        raise DRMUnsupportedError(
                            f"DRM encryption detected in MOBI/AZW3 '{input_path.name}' (encryption type {enc_type}). "
                            "book-illustrator only supports DRM-free books and does not remove DRM."
                        )
            if b"DRMError" in data or b"Kindle DRM" in data:
                raise DRMUnsupportedError(
                    f"DRM protection detected in MOBI/AZW3 '{input_path.name}'. "
                    "book-illustrator only supports DRM-free books."
                )
        except (OSError, struct.error):
            pass

    # Check PDF DRM / encryption
    elif ext == ".pdf":
        try:
            with open(input_path, "rb") as f:
                head = f.read(4096)
                f.seek(max(0, input_path.stat().st_size - 4096))
                tail = f.read(4096)
            if b"/Encrypt" in head or b"/Encrypt" in tail:
                raise DRMUnsupportedError(
                    f"PDF encryption/DRM detected in '{input_path.name}'. "
                    "Password-protected or DRM-encrypted PDFs cannot be converted."
                )
        except OSError:
            pass


def detect_txt_encoding(txt_path: Path, override_encoding: str = None) -> str:
    """Detect TXT encoding strictly without guessing ambiguous codecs to prevent mojibake.

    Auto-detects UTF BOMs (UTF-32, UTF-16, UTF-8) and standard UTF-8.
    Requires --encoding for non-UTF files.
    """
    if override_encoding:
        try:
            codecs.lookup(override_encoding)
        except LookupError:
            raise ValueError(f"Unknown encoding codec '{override_encoding}'.")

        try:
            with open(txt_path, "r", encoding=override_encoding, errors="strict") as f:
                f.read()
            return override_encoding
        except UnicodeDecodeError as e:
            raise ValueError(
                f"Specified encoding '{override_encoding}' failed to decode '{txt_path.name}': {e}"
            )

    raw = txt_path.read_bytes()
    if not raw:
        return "utf-8"

    # Check UTF-32 BOMs first (4 bytes)
    if raw.startswith(b"\x00\x00\xfe\xff"):
        return "utf-32-be"
    if raw.startswith(b"\xff\xfe\x00\x00"):
        return "utf-32-le"

    # Check UTF-16 BOMs (2 bytes)
    if raw.startswith(b"\xfe\xff"):
        return "utf-16-be"
    if raw.startswith(b"\xff\xfe"):
        return "utf-16-le"

    # Check UTF-8 BOM (3 bytes)
    if raw.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"

    # Check strict UTF-8
    try:
        raw.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        pass

    # Non-UTF text without BOM is ambiguous; require explicit encoding to prevent mojibake
    raise ValueError(
        f"Cannot automatically determine encoding for '{txt_path.name}'. "
        "The file is not valid UTF-8 and contains no Unicode BOM. "
        "Please specify the encoding explicitly using --encoding (e.g. --encoding gb18030, --encoding gbk, --encoding big5) to avoid silent mojibake."
    )


def validate_pdf_text(pdf_path: Path, calibre_bin: str = None) -> None:
    """Verify that a PDF contains an extractable text layer; reject scanned image-only PDFs."""
    pdftotext = find_pdftotext(calibre_bin)
    if pdftotext:
        try:
            proc = subprocess.run(
                [pdftotext, str(pdf_path.resolve()), "-"],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            clean_text = "".join((proc.stdout or "").split())
            if len(clean_text) < 20:
                raise ScannedPDFError(
                    f"Scanned or textless PDF detected in '{pdf_path.name}'. "
                    "The document contains no extractable text layer. "
                    "Image-only scanned documents require OCR, which is not supported."
                )
            return
        except subprocess.TimeoutExpired:
            pass
        except OSError:
            pass

    # Fallback inspection: scan raw stream text operators
    raw = pdf_path.read_bytes()
    streams = re.findall(rb"stream\r?\n(.*?)\r?\nendstream", raw, re.DOTALL)
    decompressed = []
    for s in streams:
        try:
            decompressed.append(zlib.decompress(s))
        except Exception:
            decompressed.append(s)

    content = b"\n".join(decompressed)
    bt_count = content.count(b"BT")
    tj_matches = re.findall(rb"\(([^)]+)\)\s*T[jJ]", content)
    readable_chars = sum(len(m.strip()) for m in tj_matches)

    if bt_count == 0 or readable_chars < 20:
        raise ScannedPDFError(
            f"Scanned or textless PDF detected in '{pdf_path.name}'. "
            "The document contains no extractable text layer. "
            "Image-only scanned documents require OCR, which is not supported."
        )


def validate_normalized_epub_text(epub_path: Path, source_name: str) -> None:
    """Inspect converted EPUB body text to verify it contains extractable content (excluding title/cover)."""
    if not epub_path.exists():
        raise ConversionError(f"Normalized EPUB was not created: {epub_path}")

    body_text_parts = []
    try:
        with zipfile.ZipFile(epub_path, "r") as z:
            container = ET.fromstring(z.read("META-INF/container.xml"))
            opf_rel = container.find(".//{*}rootfile").get("full-path")
            opf_dir = posixpath.dirname(opf_rel)
            opf = ET.fromstring(z.read(opf_rel))
            manifest = {i.get("id"): i.get("href") for i in opf.findall(".//{*}item")}
            spine_refs = [i.get("idref") for i in opf.findall(".//{*}itemref")]

            for ref in spine_refs:
                href = manifest.get(ref)
                if not href:
                    continue
                full_path = posixpath.normpath(posixpath.join(opf_dir, href))
                if "titlepage" in href.lower() or "cover" in href.lower():
                    continue
                if full_path in z.namelist():
                    content = z.read(full_path)
                    root = ET.fromstring(content)
                    body = root.find(".//{*}body")
                    if body is not None:
                        for elem in body.iter():
                            tag = elem.tag.split("}")[-1].lower() if "}" in elem.tag else elem.tag.lower()
                            if tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li", "blockquote"}:
                                t = "".join(elem.itertext()).strip()
                                if t:
                                    body_text_parts.append(t)
    except Exception:
        pass

    combined = "".join(" ".join(body_text_parts).split())
    if len(combined) < 20:
        raise ScannedPDFError(
            f"Scanned or textless document detected in '{source_name}'. "
            "Extracted body text contains no readable content. "
            "Image-only scanned documents require OCR, which is not supported."
        )


def safe_cleanup_workdir(workdir: Path, preserve_paths: set = None) -> None:
    """Remove stale conversion/extraction artifacts while preserving input files.

    Ensures that any directory containing preserved input is NOT deleted.
    """
    if preserve_paths is None:
        preserve_paths = set()

    normalized_preserve = set()
    for p in preserve_paths:
        try:
            normalized_preserve.add(Path(p).resolve())
        except Exception:
            normalized_preserve.add(Path(p).absolute())

    # Stale files to remove on repeated runs
    stale_files = ["normalized.epub", "conversion.json", "book.json", "export.json"]
    for fn in stale_files:
        target = (workdir / fn).resolve()
        if target.exists() and target not in normalized_preserve:
            try:
                target.unlink()
            except OSError:
                pass

    # Stale directories
    stale_dirs = ["src", "text"]
    for dn in stale_dirs:
        target = (workdir / dn).resolve()
        if not target.exists():
            continue
        has_preserved_item = any(
            p == target or target in p.parents for p in normalized_preserve
        )
        if not has_preserved_item:
            try:
                shutil.rmtree(target, ignore_errors=True)
            except OSError:
                pass
        else:
            for root, dirs, files in os.walk(target, topdown=False):
                root_path = Path(root).resolve()
                for fn in files:
                    fpath = (root_path / fn).resolve()
                    if fpath not in normalized_preserve:
                        try:
                            fpath.unlink()
                        except OSError:
                            pass
                for d in dirs:
                    dpath = (root_path / d).resolve()
                    if not any(p == dpath or dpath in p.parents for p in normalized_preserve):
                        try:
                            shutil.rmtree(dpath, ignore_errors=True)
                        except OSError:
                            pass


def run_calibre_convert(
    calibre_bin: str,
    input_path: Path,
    output_path: Path,
    extra_args: list = None,
    timeout: int = 180,
) -> subprocess.CompletedProcess:
    """Execute Calibre ebook-convert with atomic output and bounded timeout."""
    input_resolved = input_path.resolve()
    output_resolved = output_path.resolve()

    if input_resolved == output_resolved:
        raise ConversionError(f"Input and output paths cannot be identical: {input_path}")

    output_resolved.parent.mkdir(parents=True, exist_ok=True)
    # Write to temporary sibling file first to ensure atomic replacement
    tmp_output = output_resolved.parent / f".tmp_{os.getpid()}_{output_resolved.name}"
    if tmp_output.exists():
        try:
            tmp_output.unlink()
        except OSError:
            pass

    cmd = [calibre_bin, str(input_resolved), str(tmp_output)]
    if extra_args:
        cmd.extend(extra_args)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as e:
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        raise ConversionError(
            f"Calibre conversion timed out after {timeout} seconds on '{input_path.name}'."
        ) from e
    except OSError as e:
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        raise ConversionError(f"Failed to execute Calibre ebook-convert: {e}") from e

    stdout_stderr = (proc.stdout or "") + "\n" + (proc.stderr or "")

    # Check for DRM errors in Calibre output
    if "is locked by DRM" in stdout_stderr or "DRMError" in stdout_stderr:
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        raise DRMUnsupportedError(
            f"Calibre reported DRM protection in '{input_path.name}': "
            "Conversion refused. Only DRM-free books are supported."
        )

    # Narrow retry for known Calibre PDF reflow IndexError in find_header_footer
    if proc.returncode != 0 and ("find_header_footer" in stdout_stderr or "reflow.py" in stdout_stderr):
        retry_args = list(extra_args or [])
        if "--pdf-header-skip" not in retry_args:
            retry_args.extend(["--pdf-header-skip", "0", "--pdf-footer-skip", "0"])
        elif "--pdf-engine" not in retry_args:
            retry_args.extend(["--pdf-engine", "pdftohtml"])
        retry_cmd = [calibre_bin, str(input_resolved), str(tmp_output)] + retry_args
        try:
            proc = subprocess.run(
                retry_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            stdout_stderr = (proc.stdout or "") + "\n" + (proc.stderr or "")
        except (subprocess.TimeoutExpired, OSError):
            pass

    if proc.returncode != 0:
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        tail = stdout_stderr.strip()[-2000:]
        raise ConversionError(
            f"Calibre ebook-convert failed with exit code {proc.returncode}:\n{tail}"
        )

    if not tmp_output.exists() or tmp_output.stat().st_size == 0:
        if tmp_output.exists():
            try:
                tmp_output.unlink()
            except OSError:
                pass
        raise ConversionError(
            f"Calibre completed but output file '{output_path}' was not created or is empty."
        )

    # Atomic move to final destination
    os.replace(tmp_output, output_resolved)
    return proc


def write_conversion_provenance(
    workdir: Path,
    source_path: Path,
    normalized_path: Path,
    cmd_args: list,
    tool_version: str,
    encoding: str = None,
    limitations: list = None,
) -> Path:
    """Write provenance information to WORKDIR/conversion.json."""
    if limitations is None:
        limitations = [
            "Conversion to normalized EPUB reflows markup and may alter typography or layout.",
            "PDF text reflow can be lossy and does not perform OCR on scanned pages.",
            "Integrity verification must compare against normalized.epub, not the original source bytes."
        ]

    provenance = {
        "version": "1.0",
        "mode": "conversion",
        "source": {
            "path": str(source_path.resolve()),
            "format": detect_format(source_path).lstrip("."),
            "sha256": compute_sha256(source_path),
            "size_bytes": source_path.stat().st_size,
        },
        "normalized": {
            "path": str(normalized_path.resolve()),
            "format": "epub",
            "sha256": compute_sha256(normalized_path),
            "size_bytes": normalized_path.stat().st_size,
        },
        "tool": {
            "name": "calibre",
            "command": cmd_args,
            "version": tool_version,
        },
        "limitations": limitations,
        "validation_baseline": str(normalized_path.resolve()),
    }
    if encoding:
        provenance["source"]["encoding"] = encoding

    conv_path = workdir / "conversion.json"
    conv_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return conv_path


def write_export_provenance(
    workdir: Path,
    intermediate_path: Path,
    output_path: Path,
    cmd_args: list,
    tool_version: str,
    is_cross_format: bool,
    native_verified: bool = False,
    limitations: list = None,
) -> Path:
    """Write export provenance information to WORKDIR/export.json."""
    if limitations is None:
        if is_cross_format:
            limitations = [
                "Cross-format conversion does not guarantee byte identity with original source.",
                "Target format layout reflow is performed by Calibre.",
                "Downstream converted validation is limited; manual visual inspection is recommended."
            ]
        else:
            limitations = [
                "Native export preserves all original source bytes outside inserted illustration markers."
            ]

    provenance = {
        "version": "1.0",
        "mode": "cross_format_export" if is_cross_format else "native_export",
        "intermediate": {
            "path": str(intermediate_path.resolve()) if intermediate_path else None,
            "sha256": compute_sha256(intermediate_path) if intermediate_path and intermediate_path.exists() else None,
            "native_verified": native_verified,
        },
        "output": {
            "path": str(output_path.resolve()),
            "format": detect_format(output_path).lstrip("."),
            "sha256": compute_sha256(output_path),
            "size_bytes": output_path.stat().st_size,
        },
        "tool": {
            "name": "calibre" if is_cross_format else "inject.py",
            "command": cmd_args,
            "version": tool_version,
        },
        "limitations": limitations,
    }

    export_json_path = workdir / "export.json"
    export_json_path.write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
    return export_json_path
