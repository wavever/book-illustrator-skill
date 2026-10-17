#!/usr/bin/env python3
"""Export an illustrated book to the requested format.

If the requested output matches the native working format, builds via inject.py
and verifies the output with verify.py against the baseline file.
If a cross-format conversion is requested, builds a native intermediate, verifies
it with verify.py, and converts to .epub, .docx, .mobi, .azw3, or .pdf via Calibre.

Usage:
    python3 export.py WORKDIR --out OUTPUT [--calibre PATH]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

# Support running directly or as module
try:
    from . import conversion
except (ImportError, ValueError):
    import conversion


def parse_args():
    parser = argparse.ArgumentParser(description="Export an illustrated book to target format.")
    parser.add_argument("workdir", help="Path to working directory")
    parser.add_argument("--out", help="Path to destination output file", default=None)
    parser.add_argument("--calibre", help="Explicit path to Calibre ebook-convert binary", default=None)
    return parser.parse_args()


def validate_entities_and_candidates(workdir: Path, cfg: dict) -> None:
    """Verify that all entities with picks have their candidate image files present."""
    entities = cfg.get("entities", [])
    candidates_dir = workdir / "candidates"

    for e in entities:
        pick = e.get("pick")
        if not pick:
            continue
        picks = [pick] if isinstance(pick, str) else pick
        for p in picks:
            cand_path = candidates_dir / e["id"] / p
            if not cand_path.exists():
                cand_alt = candidates_dir / p
                if not cand_alt.exists():
                    sys.exit(
                        f"Error: Missing candidate image for entity '{e.get('id', e.get('name'))}':\n"
                        f"Expected image at '{cand_path}' was not found."
                    )


def verify_exported_file_sanity(output_path: Path) -> None:
    """Perform sanity checks on downstream converted file."""
    if not output_path.exists():
        sys.exit(f"Error: Exported file was not created: {output_path}")

    size = output_path.stat().st_size
    if size == 0:
        sys.exit(f"Error: Exported file is empty (0 bytes): {output_path}")

    ext = output_path.suffix.lower()
    raw_head = output_path.read_bytes()[:16]

    if ext in {".epub", ".docx"}:
        try:
            with zipfile.ZipFile(output_path, "r") as z:
                names = z.namelist()
                if ext == ".docx" and "word/document.xml" not in names:
                    sys.exit(f"Error: Converted DOCX missing document structure: {output_path}")
                elif ext == ".epub" and "mimetype" not in names:
                    sys.exit(f"Error: Converted EPUB missing mimetype: {output_path}")
        except zipfile.BadZipFile:
            sys.exit(f"Error: Converted file is not a valid zip container: {output_path}")

    elif ext == ".pdf":
        if not raw_head.startswith(b"%PDF-"):
            sys.exit(f"Error: Converted PDF missing valid PDF header magic: {output_path}")

    elif ext in {".mobi", ".azw3"}:
        if len(raw_head) < 4:
            sys.exit(f"Error: Converted MOBI/AZW3 file too small: {output_path}")


def run_native_verify(verify_script: Path, baseline: Path, target: Path) -> None:
    """Execute verify.py CLI and ensure text integrity was proven."""
    if not verify_script.exists():
        sys.exit(f"Error: verify.py not found in {verify_script.parent}")

    if not baseline.exists() or not baseline.is_file():
        sys.exit(f"Error: Baseline file '{baseline}' for verification does not exist.")

    if not target.exists() or not target.is_file():
        sys.exit(f"Error: Target file '{target}' for verification does not exist.")

    cmd = [sys.executable, str(verify_script), str(baseline), str(target)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        err_msg = (proc.stdout + "\n" + proc.stderr).strip()
        sys.exit(f"Error: Native verify failed ({proc.returncode}):\n{err_msg}")


def main():
    args = parse_args()
    workdir = Path(args.workdir).expanduser().resolve()

    if not workdir.exists() or not workdir.is_dir():
        sys.exit(f"Error: Working directory does not exist: {args.workdir}")

    book_json_path = workdir / "book.json"
    if not book_json_path.exists():
        sys.exit(f"Error: book.json not found in {workdir}. Run prepare.py first.")

    entities_json_path = workdir / "entities.json"
    if not entities_json_path.exists():
        sys.exit(f"Error: entities.json not found in {workdir}. Please configure entities before exporting.")

    try:
        book_data = json.loads(book_json_path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"Error reading book.json: {e}")

    try:
        entities_data = json.loads(entities_json_path.read_text(encoding="utf-8"))
    except Exception as e:
        sys.exit(f"Error reading entities.json: {e}")

    # Validate picked images
    validate_entities_and_candidates(workdir, entities_data)

    scripts_dir = Path(__file__).resolve().parent
    inject_script = scripts_dir / "inject.py"
    verify_script = scripts_dir / "verify.py"

    if not inject_script.exists():
        sys.exit(f"Error: inject.py not found in {scripts_dir}")

    # Determine baseline and format
    conversion_json_path = workdir / "conversion.json"
    is_conversion_job = False
    baseline_path = None
    conv_source_path = None

    if conversion_json_path.exists():
        try:
            conv_data = json.loads(conversion_json_path.read_text(encoding="utf-8"))
            candidate_baseline = Path(conv_data.get("validation_baseline", workdir / "normalized.epub"))
            book_src = book_data.get("source_path")
            if candidate_baseline.exists() and (book_src is None or Path(book_src).resolve() == candidate_baseline.resolve()):
                baseline_path = candidate_baseline.resolve()
                is_conversion_job = True
                if "source" in conv_data and "path" in conv_data["source"]:
                    conv_source_path = Path(conv_data["source"]["path"]).resolve()
        except Exception:
            pass

    book_source_path = None
    if not is_conversion_job:
        # Native job: baseline is the original source file passed to extract.py
        book_source = book_data.get("source_path")
        if book_source:
            baseline_path = Path(book_source).resolve()
            book_source_path = baseline_path
        elif (workdir / "original.epub").exists():
            baseline_path = (workdir / "original.epub").resolve()
            book_source_path = baseline_path

    if not baseline_path or not baseline_path.exists() or not baseline_path.is_file():
        sys.exit(
            f"Error: Cannot determine valid baseline file for verification in {workdir}.\n"
            "Ensure 'source_path' is recorded in book.json or normalized.epub exists."
        )

    # Determine native format
    # Determine native format and exact native extension
    source_name = (book_data.get("source") or {}).get("name", "")
    if not source_name and book_data.get("source_path"):
        source_name = Path(book_data["source_path"]).name
    src_ext = os.path.splitext(source_name)[1].lower()

    if is_conversion_job:
        native_format = "epub"
        native_ext = ".epub"
        allowed_native_exts = {".epub"}
    else:
        native_format = book_data.get("format", "epub").lower()
        if native_format == "epub":
            native_ext = ".epub"
            allowed_native_exts = {".epub"}
        elif native_format == "html":
            if src_ext == ".xhtml":
                native_ext = ".xhtml"
                allowed_native_exts = {".xhtml"}
            else:
                native_ext = src_ext if src_ext in {".html", ".htm"} else ".html"
                allowed_native_exts = {".html", ".htm"}
        elif native_format == "markdown":
            native_ext = src_ext if src_ext in {".md", ".markdown"} else ".md"
            allowed_native_exts = {".md", ".markdown"}
        else:
            native_ext = ".epub"
            allowed_native_exts = {".epub"}

    # Determine output path
    if args.out:
        output_path = Path(args.out).expanduser().resolve()
    else:
        title = entities_data.get("book") or Path(book_data.get("opf", "book")).stem
        safe_title = re.sub(r'[^\w\-\.]', '_', title).strip('_') or "book"
        output_path = workdir / f"{safe_title}_插图版{native_ext}"

    # Protect source, baseline, and intermediate files from being overwritten
    intermediate_path = (workdir / f"illustrated_intermediate{native_ext}").resolve()
    protected_paths = {baseline_path.resolve(), intermediate_path}
    if conv_source_path:
        protected_paths.add(conv_source_path)
    if book_source_path:
        protected_paths.add(book_source_path)

    if output_path.resolve() in protected_paths:
        sys.exit(f"Error: Output path cannot overwrite the source, baseline, or intermediate file: {output_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    target_ext = output_path.suffix.lower()

    # Determine if same-native
    same_native = target_ext in allowed_native_exts

    if same_native:
        # Same-native output: build via inject.py, verify, and write provenance
        print(f"Exporting same-native format ({target_ext}) via inject.py...")
        cmd = [sys.executable, str(inject_script), str(workdir), "--out", str(output_path)]
        proc = subprocess.run(cmd, check=False)
        if proc.returncode != 0:
            sys.exit(proc.returncode)

        print(f"Running native verify against baseline: {baseline_path.name}...")
        run_native_verify(verify_script, baseline_path, output_path)
        print("Native verify OK.")

        verify_exported_file_sanity(output_path)

        conversion.write_export_provenance(
            workdir=workdir,
            intermediate_path=output_path,
            output_path=output_path,
            cmd_args=cmd,
            tool_version="inject.py",
            is_cross_format=False,
            native_verified=True,
        )
        sys.exit(0)

    # Cross-format export requested
    if target_ext not in conversion.CROSS_EXPORT_FORMATS:
        supported_str = ", ".join(sorted(conversion.CROSS_EXPORT_FORMATS))
        sys.exit(
            f"Error: Unsupported export format '{target_ext}'.\n"
            f"Cross-format exports only support: {supported_str}.\n"
            f"(Native .html and .md only support same-format export)."
        )

    # Find Calibre for cross-format conversion
    try:
        calibre_bin = conversion.find_calibre(args.calibre)
        calibre_version = conversion.get_calibre_version(calibre_bin)
    except (conversion.CalibreNotFoundError, conversion.ConversionError) as e:
        sys.exit(f"Error: {e}")

    # Step 1: Build native intermediate
    print(f"Building verified native intermediate: {intermediate_path.name}...")
    inject_cmd = [sys.executable, str(inject_script), str(workdir), "--out", str(intermediate_path)]
    inject_proc = subprocess.run(inject_cmd, check=False)
    if inject_proc.returncode != 0:
        sys.exit(f"Error: inject.py failed to build intermediate ({inject_proc.returncode})")

    if not intermediate_path.exists() or intermediate_path.stat().st_size == 0:
        sys.exit(f"Error: Intermediate file was not created: {intermediate_path}")

    # Step 2: Native verify intermediate MUST run and pass before export conversion
    print(f"Running native verify against baseline: {baseline_path.name}...")
    run_native_verify(verify_script, baseline_path, intermediate_path)
    print("Native intermediate verify OK.")

    # Step 3: Convert intermediate to target output via Calibre
    print(f"Converting intermediate to {target_ext} using Calibre {calibre_version}...")
    cmd_args = [calibre_bin, str(intermediate_path), str(output_path)]
    try:
        conversion.run_calibre_convert(
            calibre_bin,
            intermediate_path,
            output_path,
            timeout=180,
        )
    except conversion.ConversionError as e:
        sys.exit(f"Error: {e}")

    # Step 4: Sanity checks
    verify_exported_file_sanity(output_path)

    # Step 5: Record export provenance
    conversion.write_export_provenance(
        workdir=workdir,
        intermediate_path=intermediate_path,
        output_path=output_path,
        cmd_args=cmd_args,
        tool_version=calibre_version,
        is_cross_format=True,
        native_verified=True,
    )

    print(f"Successfully exported to {output_path} ({output_path.stat().st_size} bytes)")
    sys.exit(0)


if __name__ == "__main__":
    main()
