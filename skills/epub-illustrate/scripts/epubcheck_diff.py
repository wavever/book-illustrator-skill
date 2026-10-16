"""Run epubcheck on the original and the illustrated EPUB and report only *new* problem types,
since many retail/converted books already fail validation on their own markup.

Usage: python3 epubcheck_diff.py ORIGINAL.epub ILLUSTRATED.epub [epubcheck.jar]
Needs Java. Without a jar argument, epubcheck is downloaded once into ~/.cache/epub-illustrate/.
"""
import collections
import io
import os
import re
import subprocess
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from webfetch import SSL_CTX  # noqa: E402

VERSION = "5.1.0"
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "epub-illustrate")


def ensure_jar():
    jar = os.path.join(CACHE, f"epubcheck-{VERSION}", "epubcheck.jar")
    if not os.path.exists(jar):
        url = f"https://github.com/w3c/epubcheck/releases/download/v{VERSION}/epubcheck-{VERSION}.zip"
        print(f"downloading epubcheck {VERSION} to {CACHE} …")
        data = urllib.request.urlopen(url, timeout=300, context=SSL_CTX).read()
        os.makedirs(CACHE, exist_ok=True)
        zipfile.ZipFile(io.BytesIO(data)).extractall(CACHE)
    return jar


jar = sys.argv[3] if len(sys.argv) > 3 else ensure_jar()


def problems(epub):
    out = subprocess.run(["java", "-jar", jar, epub], capture_output=True, text=True)
    c = collections.Counter()
    for line in (out.stdout + out.stderr).splitlines():
        m = re.match(r"(ERROR|FATAL)\((\w+-\d+)\): .*?\.epub/([^()]+?)(?:\(\d+,\d+\))?: (.*)", line)
        if m:
            c[(m.group(2), m.group(3), m.group(4)[:120])] += 1
    return c


a, b = problems(sys.argv[1]), problems(sys.argv[2])
new = {k: v for k, v in b.items() if k not in a}
print(f"original: {sum(a.values())} errors; illustrated: {sum(b.values())} errors; new kinds: {len(new)}")
for (code, f, msg), n in sorted(new.items(), key=lambda x: -x[1])[:20]:
    print(f"  {n:4d} {code} {f}: {msg}")
sys.exit(1 if new else 0)
