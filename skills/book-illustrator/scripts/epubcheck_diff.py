"""Run epubcheck on the original and the illustrated EPUB and report only *new* problems,
since many retail/converted books already fail validation on their own markup.

Usage: python3 epubcheck_diff.py ORIGINAL.epub ILLUSTRATED.epub [epubcheck.jar]
Needs Java. Without a jar argument, epubcheck is downloaded once into ~/.cache/book-illustrator/
(a jar already cached under the pre-rename ~/.cache/epub-illustrate/ is reused).

A problem is (severity, code, file, message); it is "new" when the illustrated book has more
occurrences of it than the original (so a repeated existing error that grows is reported too).
Exit status: 0 no new errors, 1 new errors, 2 epubcheck could not be run or its report is unusable
(never reported as "0 errors").
"""
import collections
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

VERSION = "5.1.0"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "book-illustrator")
# the skill was called epub-illustrate before; reuse a jar cached there instead of downloading again
LEGACY_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "epub-illustrate")


class ToolError(RuntimeError):
    pass


def ensure_jar():
    jar = os.path.join(CACHE, f"epubcheck-{VERSION}", "epubcheck.jar")
    legacy = os.path.join(LEGACY_CACHE, f"epubcheck-{VERSION}", "epubcheck.jar")
    if not os.path.exists(jar) and os.path.exists(legacy):
        return legacy
    if not os.path.exists(jar):
        from webfetch import SSL_CTX
        url = f"https://github.com/w3c/epubcheck/releases/download/v{VERSION}/epubcheck-{VERSION}.zip"
        print(f"downloading epubcheck {VERSION} to {CACHE} …")
        data = urllib.request.urlopen(url, timeout=300, context=SSL_CTX).read()
        os.makedirs(CACHE, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(CACHE)
    return jar


def problems(epub, jar, java="java"):
    """Counter of (severity, code, file, message) for ERROR/FATAL messages, from epubcheck's JSON."""
    if not os.path.isfile(epub):
        raise ToolError(f"no such file: {epub}")
    tmp = tempfile.mkdtemp(prefix="epubcheck-")
    report = os.path.join(tmp, "report.json")
    try:
        try:
            r = subprocess.run([java, "-jar", jar, epub, "--json", report], capture_output=True, text=True,
                               timeout=900)
        except FileNotFoundError:
            raise ToolError(f"{java!r} not found — install Java 11+ to run epubcheck")
        except subprocess.TimeoutExpired:
            raise ToolError(f"epubcheck timed out on {epub}")
        # epubcheck exits 1 when the book has errors; that is a result, not a tool failure
        if r.returncode not in (0, 1) or not os.path.exists(report):
            tail = (r.stderr or r.stdout).strip().splitlines()[-5:]
            raise ToolError(f"epubcheck failed on {epub} (exit {r.returncode}): " + " | ".join(tail))
        try:
            data = json.load(open(report, encoding="utf-8"))
            checker, messages = data["checker"], data["messages"]
        except (ValueError, KeyError, TypeError) as ex:
            raise ToolError(f"unreadable epubcheck report for {epub}: {ex}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    c = collections.Counter()
    for m in messages:
        sev = m.get("severity")
        if sev not in ("ERROR", "FATAL"):
            continue
        for loc in m.get("locations") or [{}]:
            c[(sev, m.get("ID"), loc.get("path") or "", (m.get("message") or "")[:160])] += 1
    reported = (checker.get("nFatal") or 0) + (checker.get("nError") or 0)
    if reported and not c:
        raise ToolError(f"epubcheck reports {reported} errors for {epub} but listed none")
    return c


def main(argv):
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    try:
        jar = argv[2] if len(argv) > 2 else ensure_jar()
        if not os.path.isfile(jar):
            raise ToolError(f"no epubcheck jar at {jar}")
        java = os.environ.get("JAVA", "java")
        a, b = problems(argv[0], jar, java), problems(argv[1], jar, java)
    except (ToolError, OSError) as ex:
        print(f"epubcheck_diff: {ex}")
        return 2
    new = {k: v - a.get(k, 0) for k, v in b.items() if v > a.get(k, 0)}
    print(f"original: {sum(a.values())} errors; illustrated: {sum(b.values())} errors; "
          f"new: {sum(new.values())} in {len(new)} kinds")
    for (sev, code, f, msg), n in sorted(new.items(), key=lambda x: -x[1])[:20]:
        print(f"  +{n:<4d} {sev} {code} {f}: {msg}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
