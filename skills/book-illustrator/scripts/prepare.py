#!/usr/bin/env python3
"""Prepare an e-book for illustration.

For native formats (.epub, .html, .md, etc.), delegates directly to extract.py.
For convertible formats (.txt, .docx, .mobi, .azw3, .pdf), converts to
WORKDIR/normalized.epub via Calibre ebook-convert, records provenance in
WORKDIR/conversion.json, and runs extract.py on the normalized EPUB.

Usage:
    python3 prepare.py INPUT WORKDIR [--calibre PATH] [--encoding ENCODING]
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Support running directly or as module
try:
    from . import conversion
except (ImportError, ValueError):
    import conversion


def parse_args():
    parser = argparse.ArgumentParser(description="Prepare an e-book for illustration.")
    parser.add_argument("input", help="Path to input book file")
    parser.add_argument("workdir", help="Path to working directory")
    parser.add_argument("--calibre", help="Explicit path to Calibre ebook-convert binary", default=None)
    parser.add_argument("--encoding", help="Character encoding for text input files (e.g. utf-8, gb18030)", default=None)
    return parser.parse_args()


def main():
    args = parse_args()
    input_path = Path(args.input).expanduser().resolve()
    workdir = Path(args.workdir).expanduser().resolve()

    if not input_path.exists() or not input_path.is_file():
        sys.exit(f"Error: Input file does not exist: {args.input}")

    # Reject if input file will be overwritten by extraction or normalization
    if input_path == (workdir / "normalized.epub").resolve():
        sys.exit(f"Error: Input file matches the normalization destination: {input_path}")
    if (workdir / "src").resolve() in input_path.parents or input_path == (workdir / "src").resolve():
        sys.exit(
            f"Error: Input file '{input_path}' is inside the extraction target directory '{workdir / 'src'}'.\n"
            "Please move the input file outside the extraction target directory."
        )
    if (workdir / "text").resolve() in input_path.parents or input_path == (workdir / "text").resolve():
        sys.exit(
            f"Error: Input file '{input_path}' is inside the text output directory '{workdir / 'text'}'.\n"
            "Please move the input file outside the text output directory."
        )

    scripts_dir = Path(__file__).resolve().parent
    extract_script = scripts_dir / "extract.py"
    if not extract_script.exists():
        sys.exit(f"Error: extract.py not found in {scripts_dir}")

    # Check DRM protection upfront
    try:
        conversion.check_drm(input_path)
    except (conversion.DRMUnsupportedError, FileNotFoundError) as e:
        sys.exit(f"Error: {e}")

    is_native = conversion.is_native_format(input_path)
    is_conv = conversion.is_convertible_input(input_path)

    if not is_native and not is_conv:
        supported_native = ", ".join(sorted(conversion.NATIVE_INPUT_FORMATS))
        supported_conv = ", ".join(sorted(conversion.CONVERTIBLE_INPUT_FORMATS))
        sys.exit(
            f"Error: Unsupported input format '{input_path.suffix}'.\n"
            f"Supported native formats: {supported_native}\n"
            f"Supported convertible formats: {supported_conv}"
        )

    # Detect or validate text encoding upfront before any cleanup
    detected_encoding = None
    if input_path.suffix.lower() == ".txt":
        try:
            detected_encoding = conversion.detect_txt_encoding(input_path, args.encoding)
        except ValueError as e:
            sys.exit(f"Error: {e}")

    # Validate Calibre binary upfront if conversion is required
    calibre_bin = None
    calibre_version = "unknown"
    if not is_native:
        try:
            calibre_bin = conversion.find_calibre(args.calibre)
            calibre_version = conversion.get_calibre_version(calibre_bin)
        except (conversion.CalibreNotFoundError, conversion.ConversionError) as e:
            sys.exit(f"Error: {e}")

        # Check PDF text layer upfront
        if input_path.suffix.lower() == ".pdf":
            try:
                conversion.validate_pdf_text(input_path, calibre_bin)
            except conversion.ScannedPDFError as e:
                sys.exit(f"Error: {e}")

    # Now that all validations have succeeded, ensure workdir exists and safely clean stale files
    workdir.mkdir(parents=True, exist_ok=True)
    conversion.safe_cleanup_workdir(workdir, preserve_paths={input_path})

    if is_native:
        # Native formats delegate directly to extract.py
        cmd = [sys.executable, str(extract_script), str(input_path), str(workdir)]
        if args.encoding:
            cmd.extend(["--encoding", args.encoding])
        proc = subprocess.run(cmd, check=False)
        sys.exit(proc.returncode)

    normalized_epub = workdir / "normalized.epub"

    # Prepare Calibre command options
    extra_args = []
    if detected_encoding:
        extra_args.extend(["--input-encoding", detected_encoding])

    # Deterministically preserve PDF header/footer and avoid Calibre reflow IndexError
    if input_path.suffix.lower() == ".pdf":
        extra_args.extend(["--pdf-header-skip", "0", "--pdf-footer-skip", "0"])

    cmd_args = [calibre_bin, str(input_path), str(normalized_epub)] + extra_args

    print(f"Converting '{input_path.name}' to normalized EPUB using Calibre {calibre_version}...")
    try:
        conversion.run_calibre_convert(
            calibre_bin,
            input_path,
            normalized_epub,
            extra_args=extra_args,
            timeout=180,
        )
    except (conversion.ConversionError, conversion.DRMUnsupportedError) as e:
        sys.exit(f"Error: {e}")

    # For PDF documents, validate converted EPUB body text as secondary check
    if input_path.suffix.lower() == ".pdf":
        try:
            conversion.validate_normalized_epub_text(normalized_epub, input_path.name)
        except (conversion.ScannedPDFError, conversion.ConversionError) as e:
            if normalized_epub.exists():
                try:
                    normalized_epub.unlink()
                except OSError:
                    pass
            sys.exit(f"Error: {e}")

    # Record conversion provenance
    conversion.write_conversion_provenance(
        workdir=workdir,
        source_path=input_path,
        normalized_path=normalized_epub,
        cmd_args=cmd_args,
        tool_version=calibre_version,
        encoding=detected_encoding,
    )

    # Delegate extraction to extract.py using the normalized EPUB
    print("Indexing normalized EPUB...")
    extract_cmd = [sys.executable, str(extract_script), str(normalized_epub), str(workdir)]
    extract_proc = subprocess.run(extract_cmd, check=False)
    sys.exit(extract_proc.returncode)


if __name__ == "__main__":
    main()
