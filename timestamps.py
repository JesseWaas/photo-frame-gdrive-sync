"""Date/time policy for files uploaded to the photo frame."""

from __future__ import annotations

from datetime import datetime
import logging
import os
from pathlib import Path

import piexif

from util import normalize_choice

logger = logging.getLogger(__name__)

DEFAULT_DATE_POLICY = "upload"
SUPPORTED_DATE_POLICIES = {"capture", "upload"}


def normalize_date_policy(value: str | None) -> str:
    """Normalize a date policy value to capture|upload."""
    return normalize_choice(value, SUPPORTED_DATE_POLICIES, DEFAULT_DATE_POLICY, label="date policy")


def _parse_exif_datetime(value: bytes | str) -> datetime | None:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="ignore")
    text = value.strip().strip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def read_capture_datetime(path: Path) -> datetime | None:
    """Read the best available capture timestamp from image EXIF."""
    try:
        exif_dict = piexif.load(str(path))
    except Exception as exc:
        logger.debug("No readable EXIF in %s: %s", path, exc)
        return None

    candidates = []
    exif_ifd = exif_dict.get("Exif") or {}
    zeroth = exif_dict.get("0th") or {}

    for tag in (piexif.ExifIFD.DateTimeOriginal, piexif.ExifIFD.DateTimeDigitized):
        raw = exif_ifd.get(tag)
        if raw:
            candidates.append(raw)

    raw = zeroth.get(piexif.ImageIFD.DateTime)
    if raw:
        candidates.append(raw)

    for raw in candidates:
        parsed = _parse_exif_datetime(raw)
        if parsed is not None:
            return parsed

    return None


def resolve_display_datetime(source_path: Path, policy: str, *, when: datetime | None = None) -> datetime:
    """Choose the datetime that should appear on the frame for this policy."""
    policy = normalize_date_policy(policy)
    when = when or datetime.now()

    if policy == "capture":
        captured = read_capture_datetime(source_path)
        if captured is not None:
            return captured
        logger.debug("No capture EXIF for %s; falling back to current time", source_path)
        return when

    return when


def write_exif_datetime(path: Path, when: datetime) -> None:
    """Write DateTime / DateTimeOriginal / DateTimeDigitized into an image file."""
    stamp = when.strftime("%Y:%m:%d %H:%M:%S").encode("utf-8")
    try:
        exif_dict = piexif.load(str(path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    exif_dict.setdefault("0th", {})
    exif_dict.setdefault("Exif", {})
    exif_dict["0th"][piexif.ImageIFD.DateTime] = stamp
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp

    # Drop thumbnail EXIF that can become invalid after cropping.
    exif_dict["thumbnail"] = None
    exif_dict["1st"] = {}

    try:
        piexif.insert(piexif.dump(exif_dict), str(path))
    except Exception as exc:
        logger.warning("Failed to write EXIF dates to %s: %s", path, exc)


def set_file_mtime(path: Path, when: datetime) -> None:
    """Set filesystem mtime/atime used by gphoto2 for PTP FILEMTIME."""
    timestamp = when.timestamp()
    os.utime(path, (timestamp, timestamp))
