"""Image compression for the pictures inject.py adds to a book (Pillow only, no network).

Every inserted picture becomes a baseline JPEG (the one raster format every EPUB 2/3 reader
supports): EXIF orientation applied, colour converted to sRGB where a profile allows it,
transparency filled with white, the first pick's crop applied, metadata (EXIF/GPS, XMP, comments)
dropped, then encoded with optimised Huffman tables:

  1. shrink so the long side is at most max_side (never enlarged);
  2. if the file is over target_kb, lower the quality, at most down to min_quality;
  3. if it is still over, shrink further, at most down to a long side of min_side, choosing the
     highest quality that fits at each size.

The byte target is best effort: when the floors are reached first, the smallest encode is kept
and reported with target_met = false. A candidate that is already a suitable JPEG (baseline,
RGB/grey, upright, not cropped, within max_side) keeps its original image data, with only the
metadata segments removed, when it already fits target_kb or is no larger than the re-encode.

Candidate files are only read, never written.
"""
import io
import math

from PIL import Image, ImageOps

try:
    from PIL import ImageCms
except ImportError:  # Pillow built without LittleCMS
    ImageCms = None

PRESETS = {
    "compact": {"max_side": 800, "quality": 72, "target_kb": 80, "min_quality": 50, "min_side": 480},
    "balanced": {"max_side": 1024, "quality": 80, "target_kb": 120, "min_quality": 60, "min_side": 640},
    "high": {"max_side": 1600, "quality": 88, "target_kb": 300, "min_quality": 70, "min_side": 1024},
}
DEFAULT_PRESET = "balanced"
FIELDS = ("max_side", "quality", "target_kb", "min_quality", "min_side")
SIDE_RANGE, QUALITY_RANGE, TARGET_KB_MAX = (64, 8192), (1, 95), 65536


class ConfigError(ValueError):
    pass


def resolve(value):
    """Validated settings for settings.image_compression: a preset name, or an object with an
    optional "preset" (default balanced) plus overrides. target_kb may be null (no byte target)."""
    where = "settings.image_compression"
    names = ", ".join(PRESETS)
    if value is None:
        value = DEFAULT_PRESET
    if isinstance(value, str):
        preset, over = value, {}
    elif isinstance(value, dict):
        unknown = sorted(set(value) - {"preset", *FIELDS})
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {', '.join(map(repr, unknown))}; "
                              f"allowed: preset, {', '.join(FIELDS)}")
        preset = value.get("preset", DEFAULT_PRESET)
        over = {k: v for k, v in value.items() if k != "preset"}
    else:
        raise ConfigError(f"{where} must be a preset name ({names}) or an object such as "
                          '{"preset": "balanced", "target_kb": 150}; got ' + repr(value))
    if not isinstance(preset, str) or preset not in PRESETS:
        raise ConfigError(f"{where}: unknown preset {preset!r}; choose one of {names}")
    opts = {**PRESETS[preset], **over}
    for k, (lo, hi) in (("max_side", SIDE_RANGE), ("min_side", SIDE_RANGE),
                        ("quality", QUALITY_RANGE), ("min_quality", QUALITY_RANGE)):
        v = opts[k]
        if isinstance(v, bool) or not isinstance(v, int):
            raise ConfigError(f"{where}: {k} must be a whole number, got {v!r}")
        if not lo <= v <= hi:
            raise ConfigError(f"{where}: {k} must be between {lo} and {hi}, got {v}")
    t = opts["target_kb"]
    if t is not None:
        if isinstance(t, bool) or not isinstance(t, (int, float)) or not math.isfinite(t):
            raise ConfigError(f"{where}: target_kb must be a positive number of KiB or null, got {t!r}")
        if not 0 < t <= TARGET_KB_MAX:
            raise ConfigError(f"{where}: target_kb must be above 0 and at most {TARGET_KB_MAX}, got {t}")
    if opts["min_side"] > opts["max_side"]:
        raise ConfigError(f"{where}: min_side ({opts['min_side']}) must not exceed max_side ({opts['max_side']})")
    if opts["min_quality"] > opts["quality"]:
        raise ConfigError(f"{where}: min_quality ({opts['min_quality']}) must not exceed quality ({opts['quality']})")
    return {"preset": preset, "overrides": over, **opts}


# --- decoding to an upright, opaque RGB/L image ----------------------------------------------

def _to_srgb(im, icc):
    """(image, profile to embed or None, note). Converts RGB/CMYK data to sRGB when the embedded
    profile allows it; otherwise keeps a profile that still matches the pixels, else drops it."""
    if not icc:
        return im, None, None
    if ImageCms is None:
        return im, (icc if im.mode in ("RGB", "RGBA", "L", "LA") else None), "kept"
    try:
        prof = ImageCms.ImageCmsProfile(io.BytesIO(icc))
        space = prof.profile.xcolor_space.strip()
    except Exception:  # noqa: BLE001 - unreadable profile: nothing it could describe reliably
        return im, None, "dropped"
    if (space, im.mode) in (("RGB", "RGB"), ("RGB", "RGBA"), ("CMYK", "CMYK")):
        try:
            out = ImageCms.profileToProfile(im, prof, ImageCms.createProfile("sRGB"),
                                            outputMode="RGBA" if im.mode == "RGBA" else "RGB")
            return out, None, "converted to sRGB"
        except Exception:  # noqa: BLE001
            pass
    if (space == "RGB" and im.mode in ("RGB", "RGBA")) or (space == "GRAY" and im.mode in ("L", "LA")):
        return im, icc, "kept"
    return im, None, "dropped"


def _orientation(src):
    try:
        return src.getexif().get(0x0112)
    except Exception:  # noqa: BLE001
        return None


def load(path):
    """(image in RGB or L, ICC profile to embed or None, facts about the source)."""
    with Image.open(path) as src:
        orient = _orientation(src)
        facts = {"format": src.format, "width": src.width, "height": src.height, "mode": src.mode,
                 "progressive": bool(src.info.get("progressive") or src.info.get("progression")),
                 "oriented": orient in (2, 3, 4, 5, 6, 7, 8)}
        im = ImageOps.exif_transpose(src)  # a loaded copy (first frame of an animation)
    icc = im.info.get("icc_profile")
    alpha = im.mode in ("RGBA", "LA", "La", "RGBa", "PA") or "transparency" in im.info
    if alpha:
        im = im.convert("RGBA")
    elif im.mode == "P":
        im = im.convert("RGB")
    elif im.mode.startswith("I") or im.mode == "F":  # 16-bit / float grey
        im = im.convert("I") if im.mode.startswith("I;") else im
        if im.getextrema()[1] > 255:
            im = im.point(lambda v: v * (1 / 256))
        im = im.convert("L")
    elif im.mode == "1":
        im = im.convert("L")
    elif im.mode not in ("RGB", "L", "CMYK"):
        im = im.convert("RGB")
    im, icc, facts["icc"] = _to_srgb(im, icc)
    facts["flattened"] = False
    if im.mode == "RGBA":
        a = im.getchannel("A")
        facts["flattened"] = a.getextrema()[0] < 255
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=a)
        im = bg
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.info = {}  # Pillow would otherwise carry e.g. JPEG comments into the output
    return im, icc, facts


# --- keeping an original JPEG's image data --------------------------------------------------

KEEP_SEGMENTS = {0xE0, 0xEE}  # APP0 JFIF, APP14 Adobe (colour transform); APP2 kept only for ICC


def strip_jpeg_metadata(data):
    """The same JPEG with EXIF/XMP/comment/other APPn segments and trailing bytes removed, image
    data byte-identical; None if the stream is not a structure this parser fully understands."""
    if data[:2] != b"\xff\xd8":
        return None
    out, i = [data[:2]], 2
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return None
        m = data[i + 1]
        if m == 0xFF:  # fill byte
            i += 1
            continue
        if m == 0xDA:  # start of scan: copy through the end-of-image marker, drop anything after
            end = data.find(b"\xff\xd9", i + 2)
            if end < 0:
                return None
            out.append(data[i:end + 2])
            return b"".join(out)
        if m == 0x01 or 0xD0 <= m <= 0xD9:
            return None
        n = int.from_bytes(data[i + 2:i + 4], "big")
        if n < 2 or i + 2 + n > len(data):
            return None
        seg = data[i:i + 2 + n]
        if not (0xE0 <= m <= 0xEF or m == 0xFE) or m in KEEP_SEGMENTS or (
                m == 0xE2 and seg[4:16] == b"ICC_PROFILE\0"):
            out.append(seg)
        i += 2 + n
    return None


def _original_jpeg(path, facts, cropped, opts):
    """Metadata-stripped original bytes if the original can be shipped as-is, else None."""
    if (facts["format"] != "JPEG" or facts["mode"] not in ("RGB", "L") or facts["progressive"]
            or facts["oriented"] or cropped or max(facts["width"], facts["height"]) > opts["max_side"]):
        return None
    with open(path, "rb") as f:
        data = strip_jpeg_metadata(f.read())
    if data is None:
        return None
    try:
        with Image.open(io.BytesIO(data)) as check:
            check.load()
            if check.size != (facts["width"], facts["height"]) or check.mode != facts["mode"]:
                return None
    except Exception:  # noqa: BLE001
        return None
    return data


# --- adaptive encoding ----------------------------------------------------------------------

def _encode(im, quality, icc):
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality, optimize=True, **({"icc_profile": icc} if icc else {}))
    return buf.getvalue()


def _fit(im, side):
    w, h = im.size
    if max(w, h) <= side:
        return im
    s = side / max(w, h)
    return im.resize((max(1, round(w * s)), max(1, round(h * s))), Image.LANCZOS)


def _best_quality(im, opts, target, icc):
    """(data, quality, fits): the highest quality in [min_quality, quality] within target, else
    the min_quality encode with fits False."""
    data = _encode(im, opts["quality"], icc)
    if target is None or len(data) <= target:
        return data, opts["quality"], True
    lo = opts["min_quality"]
    floor = _encode(im, lo, icc) if lo < opts["quality"] else data
    if len(floor) > target:
        return floor, lo, False
    best, lo, hi = (floor, lo), lo + 1, opts["quality"] - 1
    while lo <= hi:  # size grows (almost) monotonically with quality
        mid = (lo + hi) // 2
        d = _encode(im, mid, icc)
        if len(d) <= target:
            best, lo = (d, mid), mid + 1
        else:
            hi = mid - 1
    return best[0], best[1], True


def compress(path, opts, crop=None):
    """(JPEG bytes, report dict) for one candidate file; opts from resolve()."""
    with open(path, "rb") as f:
        original_bytes = len(f.read())
    im, icc, facts = load(path)
    cropped = False
    if crop:
        l, t, r, b = crop
        w, h = im.size
        box = (int(l * w), int(t * h), int(r * w), int(b * h))
        cropped = box != (0, 0, w, h)
        im = im.crop(box)
    before = im.size
    target = None if opts["target_kb"] is None else int(opts["target_kb"] * 1024)
    rep = {"original": {"bytes": original_bytes, "width": facts["width"], "height": facts["height"],
                        "format": facts["format"]},
           "cropped": cropped, "flattened": facts["flattened"], "oriented": facts["oriented"],
           "color_profile": facts["icc"], "target_bytes": target}

    keep = _original_jpeg(path, facts, cropped, opts)
    if keep is not None and target is not None and len(keep) <= target:
        return keep, _done(rep, keep, before, None, True, target)

    base = _fit(im, opts["max_side"])
    floor_side = min(opts["min_side"], max(base.size))
    cur = base
    while True:
        data, q, fits = _best_quality(cur, opts, target, icc)
        side = max(cur.size)
        if fits or side <= floor_side:
            break
        est = int(side * math.sqrt(target / len(data)) * 0.95)
        cur = _fit(base, max(floor_side, min(est, int(side * 0.9))))  # always from base: no cumulative blur
    if keep is not None and len(keep) <= len(data):
        return keep, _done(rep, keep, before, None, target is None or len(keep) <= target, target)
    return data, _done(rep, data, cur.size, q, fits, target, before)


def _done(rep, data, size, quality, fits, target, before=None):
    if quality is None:  # original image data shipped: its own profile (if any) still applies
        rep["color_profile"] = "unchanged" if rep["color_profile"] else None
    rep.update({"output": {"bytes": len(data), "width": size[0], "height": size[1]},
                "quality": quality, "kept_original_jpeg": quality is None,
                "resized": list(size) if before and tuple(size) != tuple(before) else None,
                "target_met": bool(fits) if target is not None else None})
    return rep
