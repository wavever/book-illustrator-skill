"""Bounded HTTP + image validation shared by the image acquisition scripts.

Plain urllib (certifi CA bundle when available); no browser, no API key. Everything is bounded:
per-request socket timeout, overall read deadline, byte cap, a few retries with backoff, and
Retry-After honoured only up to `max_wait` seconds (longer waits raise RateLimited instead of
blocking the run). Downloaded bytes are accepted only if Pillow fully decodes them; the file
extension comes from the decoded format, never from the URL.
"""
import hashlib
import io
import json
import os
import socket
import ssl
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings
from email.utils import parsedate_to_datetime

from PIL import Image

try:
    import certifi
    SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:  # pragma: no cover
    SSL_CTX = ssl.create_default_context()

# Wikimedia asks API clients for a descriptive User-Agent with a project URL.
API_UA = "book-illustrator/2.0 (+https://github.com/wavever/book-illustrator-skill) python-urllib"
FORMAT_EXT = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp", "BMP": ".bmp", "TIFF": ".tif"}
RETRY_STATUS = {429, 500, 502, 503, 504}

DEFAULT_LIMITS = {
    "timeout": 20,          # seconds per socket operation
    "deadline": 60,         # seconds for one whole response body
    "retries": 2,           # extra attempts after the first, transient errors only
    "max_wait": 30,         # longest Retry-After / backoff we are willing to sleep
    "max_bytes": 25_000_000,
    "max_json_bytes": 5_000_000,
    "max_pixels": 60_000_000,
    "min_side": 48,         # rejects tracking pixels/spacers, not small photos or logos
}


class FetchError(Exception):
    """kind: network | timeout | http | too_large | rate_limited | not_image | too_small | too_many_pixels"""

    def __init__(self, kind, message, status=None, retry_after=None):
        super().__init__(message)
        self.kind, self.status, self.retry_after = kind, status, retry_after

    @property
    def transient(self):
        return self.kind in ("network", "timeout", "rate_limited") or (
            self.kind == "http" and self.status in RETRY_STATUS)


class RateLimited(FetchError):
    def __init__(self, message, retry_after=None, status=429):
        super().__init__("rate_limited", message, status=status, retry_after=retry_after)


def _default_open(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX)


def parse_retry_after(value, now=None):
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    return max(0.0, dt.timestamp() - (now if now is not None else time.time()))


class Http:
    """Small bounded HTTP client. `opener(request, timeout)` and `sleep` are injectable for tests."""

    def __init__(self, limits=None, opener=None, sleep=None, user_agent=API_UA):
        self.limits = dict(DEFAULT_LIMITS, **(limits or {}))
        self.opener = opener or _default_open
        self.sleep = sleep or time.sleep
        self.user_agent = user_agent
        self.blocked = {}  # host -> reason, set after a long rate limit; skipped for the rest of the run

    def _once(self, url, headers, max_bytes):
        req = urllib.request.Request(url, headers={"User-Agent": self.user_agent, **(headers or {})})
        try:
            resp = self.opener(req, self.limits["timeout"])
        except urllib.error.HTTPError as e:
            ra = parse_retry_after(e.headers.get("Retry-After") if e.headers else None)
            if e.code == 429 or (e.code == 503 and ra is not None):
                raise RateLimited(f"HTTP {e.code} from {urllib.parse.urlsplit(url).netloc}", ra, e.code)
            raise FetchError("http", f"HTTP {e.code}", status=e.code)
        except (socket.timeout, TimeoutError) as e:
            raise FetchError("timeout", f"timeout: {e}")
        except urllib.error.URLError as e:
            kind = "timeout" if isinstance(e.reason, (socket.timeout, TimeoutError)) else "network"
            raise FetchError(kind, f"{kind}: {e.reason}")
        except (ConnectionError, ssl.SSLError, OSError) as e:
            raise FetchError("network", f"network: {e}")
        try:
            length = resp.headers.get("Content-Length") if resp.headers else None
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("too_large", f"Content-Length {length} > {max_bytes} bytes")
            start, chunks, total = time.monotonic(), [], 0
            while True:
                try:
                    chunk = resp.read(65536)
                except (socket.timeout, TimeoutError) as e:
                    raise FetchError("timeout", f"timeout while reading: {e}")
                except (ConnectionError, OSError) as e:
                    raise FetchError("network", f"network while reading: {e}")
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise FetchError("too_large", f"body exceeds {max_bytes} bytes")
                if time.monotonic() - start > self.limits["deadline"]:
                    raise FetchError("timeout", f"body not complete within {self.limits['deadline']}s")
                chunks.append(chunk)
            return b"".join(chunks), (resp.headers.get("Content-Type", "") if resp.headers else "")
        finally:
            close = getattr(resp, "close", None)
            if close:
                close()

    def get(self, url, headers=None, max_bytes=None):
        """Bytes of `url`, retrying transient failures a bounded number of times."""
        host = urllib.parse.urlsplit(url).netloc
        if host in self.blocked:
            raise RateLimited(f"{host} skipped for this run: {self.blocked[host]}")
        max_bytes = max_bytes or self.limits["max_bytes"]
        attempts = 1 + max(0, int(self.limits["retries"]))
        for attempt in range(attempts):
            try:
                return self._once(url, headers, max_bytes)
            except FetchError as e:
                if not e.transient or attempt == attempts - 1:
                    if isinstance(e, RateLimited):
                        self.blocked[host] = str(e)
                    raise
                wait = e.retry_after if e.retry_after is not None else min(2 ** attempt, 8)
                if wait > self.limits["max_wait"]:
                    self.blocked[host] = f"Retry-After {int(wait)}s exceeds max_wait {self.limits['max_wait']}s"
                    raise RateLimited(f"{host} asks to wait {int(wait)}s; retry later", wait)
                self.sleep(wait)
        raise AssertionError("unreachable")  # pragma: no cover

    def get_json(self, url, params=None, headers=None):
        full = url + ("?" + urllib.parse.urlencode(params) if params else "")
        data, _ = self.get(full, headers=headers, max_bytes=self.limits["max_json_bytes"])
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as e:
            raise FetchError("http", f"invalid JSON from {urllib.parse.urlsplit(url).netloc}: {e}")


def inspect_image(data, limits=None):
    """Fully decode `data`; return {format, ext, width, height, sha256, bytes} or raise FetchError."""
    lim = dict(DEFAULT_LIMITS, **(limits or {}))
    if len(data) > lim["max_bytes"]:
        raise FetchError("too_large", f"{len(data)} bytes > {lim['max_bytes']}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            fmt, (w, h) = im.format, im.size
            if fmt not in FORMAT_EXT:
                raise FetchError("not_image", f"unsupported image format {fmt}")
            if w * h > lim["max_pixels"]:
                raise FetchError("too_many_pixels", f"{w}x{h} exceeds {lim['max_pixels']} pixels")
            im.verify()
            im = Image.open(io.BytesIO(data))
            im.load()  # verify() alone misses truncated/corrupt pixel data
    except FetchError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as e:
        raise FetchError("too_many_pixels", str(e))
    except Exception as e:  # noqa: BLE001 - Pillow raises many types for bad data
        raise FetchError("not_image", f"not a decodable image: {type(e).__name__}: {e}")
    if min(w, h) < lim["min_side"]:
        raise FetchError("too_small", f"{w}x{h} is below min_side {lim['min_side']}px")
    return {"format": fmt, "ext": FORMAT_EXT[fmt], "width": w, "height": h,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def atomic_write(path, data):
    """Write bytes/str to `path` via a temp file in the same directory + os.replace."""
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=d)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data.encode("utf-8") if isinstance(data, str) else data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def atomic_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=1) + "\n")
