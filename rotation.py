"""Capacity-limited rotating window selection for frame uploads."""

from __future__ import annotations

from datetime import datetime
import logging
import re

from crop import PreparedUpload
from util import normalize_choice

logger = logging.getLogger(__name__)

DEFAULT_ROTATION = None  # no rotating offset; fill from the start of the sorted set
SUPPORTED_ROTATIONS = {"hourly", "daily", "every-run", "off"}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?b?)?\s*$", re.IGNORECASE)
_UNIT_MULTIPLIER = {
    "": 1,
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
    "t": 1024**4,
    "tb": 1024**4,
}


def parse_byte_size(value) -> int | None:
    """Parse a byte size like 20GB, 512MB, or an integer byte count."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError(f"invalid byte size: {value!r}")
    if isinstance(value, (int, float)):
        if value < 0:
            raise ValueError(f"byte size must be non-negative: {value}")
        return int(value)

    text = str(value).strip()
    match = _SIZE_RE.match(text)
    if not match:
        raise ValueError(f"invalid byte size: {value!r}")

    amount = float(match.group(1))
    unit = (match.group(2) or "").lower()
    if unit not in _UNIT_MULTIPLIER:
        raise ValueError(f"invalid byte size unit in {value!r}")
    return int(amount * _UNIT_MULTIPLIER[unit])


def parse_max_files(value) -> int | None:
    """Parse a max_files limit."""
    if value is None or value == "":
        return None
    files = int(value)
    if files < 0:
        raise ValueError(f"max_files must be non-negative: {value}")
    return files


def normalize_rotation(value: str | None) -> str | None:
    """Normalize rotation policy; None/off means fill from the start."""
    choice = normalize_choice(value, SUPPORTED_ROTATIONS, "off", label="rotation")
    return DEFAULT_ROTATION if choice == "off" else choice


def rotation_bucket(when: datetime, rotation: str | None) -> int:
    """Return a deterministic bucket index for the current rotation policy."""
    rotation = normalize_rotation(rotation)
    if rotation is None:
        return 0
    if rotation == "hourly":
        return int(when.timestamp() // 3600)
    if rotation == "daily":
        return int(when.timestamp() // 86400)
    if rotation == "every-run":
        # Changes about every minute; pairs well with a frequent systemd timer.
        return int(when.timestamp() // 60)
    return 0


def select_rotation_window(
    prepared: list[PreparedUpload],
    *,
    max_bytes: int | None = None,
    max_files: int | None = None,
    rotation: str | None = None,
    when: datetime | None = None,
) -> list[PreparedUpload]:
    """Choose a capacity-limited rotating subset of prepared uploads.

    Files are sorted by frame_name for stability, then a rotating start offset is
    applied. Selection walks forward (wrapping) until max_bytes and/or max_files
    would be exceeded. If neither limit is set, the full prepared set is returned.
    """
    if not prepared:
        return []

    if max_bytes is None and max_files is None:
        return list(prepared)

    when = when or datetime.now()
    ordered = sorted(prepared, key=lambda item: item.frame_name)
    count = len(ordered)
    start = rotation_bucket(when, rotation) % count

    selected: list[PreparedUpload] = []
    total_bytes = 0

    for offset in range(count):
        item = ordered[(start + offset) % count]
        try:
            size = item.source_path.stat().st_size
        except OSError as exc:
            logger.warning("Skipping %s; cannot stat source path: %s", item.source_path, exc)
            continue

        if max_files is not None and len(selected) >= max_files:
            break

        if max_bytes is not None:
            if size > max_bytes:
                logger.warning(
                    "Skipping %s (%d bytes); larger than max_bytes=%d",
                    item.source_path,
                    size,
                    max_bytes,
                )
                continue
            if total_bytes + size > max_bytes:
                if selected:
                    break
                continue

        selected.append(item)
        total_bytes += size

    logger.info(
        "Rotation window selected %d/%d file(s) (%d bytes, rotation=%s, max_bytes=%s, max_files=%s)",
        len(selected),
        count,
        total_bytes,
        normalize_rotation(rotation) or "off",
        max_bytes,
        max_files,
    )
    return selected
