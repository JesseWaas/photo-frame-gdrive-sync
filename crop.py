"""Portrait cropping helpers for frame uploads."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import shutil

import cv2
import numpy as np
from PIL import Image, ImageOps

from timestamps import (
    normalize_date_policy,
    resolve_display_datetime,
    set_file_mtime,
    write_exif_datetime,
)
from util import normalize_choice

logger = logging.getLogger(__name__)

DEFAULT_ASPECT_RATIO = "3:4"
UPLOAD_STAGING_DIRNAME = ".upload_stage"
SUPPORTED_STRATEGIES = {"none", "center", "faces"}
FRAME_NAME_HASH_LEN = 16


class FaceCropAborted(RuntimeError):
    """Raised when faces crop is requested but no faces are detected."""


@dataclass(frozen=True)
class AspectRatio:
    width: float
    height: float

    @property
    def value(self) -> float:
        return self.width / self.height

    @property
    def slug(self) -> str:
        w = int(self.width) if self.width.is_integer() else self.width
        h = int(self.height) if self.height.is_integer() else self.height
        return f"{w}x{h}"


@dataclass(frozen=True)
class PreparedUpload:
    """A planned frame upload; cropping happens later during staging."""

    frame_name: str
    source_path: Path
    crop: str
    date_policy: str


def parse_aspect_ratio(value: str) -> AspectRatio:
    """Parse an aspect ratio like '3:4', '9/16', or '0.75'."""
    text = (value or "").strip().lower()
    if not text:
        raise ValueError("aspect ratio must not be empty")

    if ":" in text or "/" in text:
        sep = ":" if ":" in text else "/"
        left, right = text.split(sep, 1)
        width = float(left)
        height = float(right)
    else:
        width = float(text)
        height = 1.0

    if width <= 0 or height <= 0:
        raise ValueError(f"aspect ratio must be positive: {value!r}")

    return AspectRatio(width=width, height=height)


def normalize_crop_strategy(value: str | None) -> str:
    """Normalize a config crop value to a supported strategy."""
    return normalize_choice(value, SUPPORTED_STRATEGIES, "none", label="crop strategy")


def crop_window(
    image_width: int,
    image_height: int,
    aspect: AspectRatio,
    *,
    center_x: float | None = None,
    center_y: float | None = None,
) -> tuple[int, int, int, int]:
    """Return a clamped crop box (left, top, right, bottom).

    Prefers a full-height window when the source is wider than the target
    aspect (typical landscape -> portrait case). Otherwise uses a full-width
    window and crops vertically.
    """
    target = aspect.value
    image_aspect = image_width / image_height

    if image_aspect >= target:
        crop_height = image_height
        crop_width = min(image_width, max(1, int(round(crop_height * target))))
    else:
        crop_width = image_width
        crop_height = min(image_height, max(1, int(round(crop_width / target))))

    if center_x is None:
        center_x = image_width / 2
    if center_y is None:
        center_y = image_height / 2

    left = int(round(center_x - crop_width / 2))
    top = int(round(center_y - crop_height / 2))
    left = max(0, min(left, image_width - crop_width))
    top = max(0, min(top, image_height - crop_height))

    return left, top, left + crop_width, top + crop_height


def _load_image(path: Path) -> Image.Image:
    with Image.open(path) as image:
        return ImageOps.exif_transpose(image).convert("RGB")


def _haar_cascade_path(name: str = "haarcascade_frontalface_default.xml") -> Path | None:
    """Locate an OpenCV Haar cascade XML (pip wheels and Debian packages differ)."""
    candidates: list[Path] = []
    data = getattr(cv2, "data", None)
    haarcascades = getattr(data, "haarcascades", None) if data is not None else None
    if haarcascades:
        candidates.append(Path(haarcascades) / name)
    candidates.extend(
        [
            Path("/usr/share/opencv4/haarcascades") / name,
            Path("/usr/share/opencv/haarcascades") / name,
            Path("/usr/local/share/opencv4/haarcascades") / name,
        ]
    )
    for path in candidates:
        if path.is_file():
            return path
    return None


def _face_box_is_plausible(width: int, height: int, image_width: int, image_height: int) -> bool:
    """Reject elongated / huge blobs that Haar often mistakes for faces (foliage, etc.)."""
    if width <= 0 or height <= 0 or image_width <= 0 or image_height <= 0:
        return False
    aspect = width / height
    if not (0.65 <= aspect <= 1.45):
        return False
    area_fraction = (width * height) / float(image_width * image_height)
    # Real faces are rarely most of the frame; foliage false-positives often are large.
    return 0.002 <= area_fraction <= 0.35


def _face_score(box: tuple[int, int, int, int], image_width: int, image_height: int) -> float:
    """Prefer larger faces that are still plausible and nearer the frame center."""
    x, y, w, h = box
    area = float(w * h)
    cx = x + w / 2
    cy = y + h / 2
    dx = (cx - image_width / 2) / max(image_width / 2, 1)
    dy = (cy - image_height / 2) / max(image_height / 2, 1)
    center_penalty = 1.0 + (dx * dx + dy * dy)
    return area / center_penalty


def _select_face_group(
    faces: list[tuple[int, int, int, int]],
    image_width: int,
    image_height: int,
) -> list[tuple[int, int, int, int]]:
    """Keep the best plausible face and nearby faces; drop distant false positives."""
    plausible = [
        (x, y, w, h)
        for x, y, w, h in faces
        if _face_box_is_plausible(w, h, image_width, image_height)
    ]
    if not plausible:
        return []

    anchor = max(plausible, key=lambda box: _face_score(box, image_width, image_height))
    ax, ay, aw, ah = anchor
    anchor_cx = ax + aw / 2
    anchor_cy = ay + ah / 2
    # Allow other faces in a group portrait around the main subject.
    max_distance = 2.5 * max(aw, ah)

    group = []
    for x, y, w, h in plausible:
        cx = x + w / 2
        cy = y + h / 2
        distance = ((cx - anchor_cx) ** 2 + (cy - anchor_cy) ** 2) ** 0.5
        if distance <= max_distance:
            group.append((x, y, w, h))
    return group or [anchor]


def _detect_face_center(image: Image.Image) -> tuple[float, float] | None:
    """Return the center of detected faces, or None if none found."""
    gray = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2GRAY)
    cascade_path = _haar_cascade_path()
    if cascade_path is None:
        logger.error(
            "OpenCV face cascade not found (tried cv2.data and /usr/share/opencv*/haarcascades). "
            "Install opencv-data or use crop strategy center/none."
        )
        return None

    detector = cv2.CascadeClassifier(str(cascade_path))
    if detector.empty():
        logger.error("Failed to load OpenCV face cascade from %s", cascade_path)
        return None

    # Higher minNeighbors cuts foliage false positives; slightly coarser scale helps on Pi Zero.
    raw_faces = detector.detectMultiScale(
        gray,
        scaleFactor=1.15,
        minNeighbors=8,
        minSize=(64, 64),
        flags=cv2.CASCADE_SCALE_IMAGE,
    )
    faces = [(int(x), int(y), int(w), int(h)) for x, y, w, h in raw_faces]
    group = _select_face_group(faces, image.width, image.height)
    if not group:
        return None

    left = top = float("inf")
    right = bottom = float("-inf")
    for x, y, w, h in group:
        left = min(left, x)
        top = min(top, y)
        right = max(right, x + w)
        bottom = max(bottom, y + h)
    return (left + right) / 2, (top + bottom) / 2


def compute_crop_box(image: Image.Image, strategy: str, aspect: AspectRatio) -> tuple[int, int, int, int]:
    """Choose a crop box for the given strategy.

    Raises FaceCropAborted if strategy is faces and no faces are detected.
    """
    width, height = image.size
    strategy = normalize_crop_strategy(strategy)

    if strategy == "none":
        return 0, 0, width, height

    center_x = width / 2
    center_y = height / 2

    if strategy == "faces":
        detected = _detect_face_center(image)
        if detected is None:
            raise FaceCropAborted("No faces detected; aborting faces crop")
        center_x, center_y = detected
        logger.debug("Face-centered crop at (%.1f, %.1f)", center_x, center_y)

    return crop_window(width, height, aspect, center_x=center_x, center_y=center_y)


def render_crop(source_path: Path, strategy: str, aspect: AspectRatio, output_path: Path) -> Path:
    """Write a cropped JPEG to output_path and return that path."""
    strategy = normalize_crop_strategy(strategy)
    if strategy == "none":
        raise ValueError("render_crop requires a cropping strategy")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    image = _load_image(source_path)
    box = compute_crop_box(image, strategy, aspect)
    image.crop(box).save(output_path, format="JPEG", quality=92, optimize=True)
    logger.info(
        "Cropped %s with strategy=%s aspect=%s -> %s",
        source_path,
        strategy,
        aspect.slug,
        output_path,
    )
    return output_path


def frame_name_for(
    content_hash: str,
    strategy: str,
    aspect: AspectRatio,
    date_policy: str,
    suffix: str,
) -> str:
    """Stable frame filename that ignores rewritten upload timestamps."""
    identity = f"{content_hash}|{strategy}|{aspect.slug}|{date_policy}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{digest[:FRAME_NAME_HASH_LEN]}{suffix.lower()}"


def stage_upload_file(
    prepared: PreparedUpload,
    *,
    aspect: AspectRatio,
    staging_path: Path,
    when=None,
) -> Path:
    """Crop (if needed) into a staging path and apply the date policy.

    Staging is intended to be short-lived: one file at a time around upload.
    """
    staging_path = Path(staging_path)
    staging_path.parent.mkdir(parents=True, exist_ok=True)

    if prepared.crop == "none":
        shutil.copy2(prepared.source_path, staging_path)
    else:
        render_crop(prepared.source_path, prepared.crop, aspect, staging_path)

    display_time = resolve_display_datetime(prepared.source_path, prepared.date_policy, when=when)
    write_exif_datetime(staging_path, display_time)
    set_file_mtime(staging_path, display_time)
    logger.debug("Staged %s (crop=%s) -> %s", prepared.source_path, prepared.crop, staging_path)
    return staging_path
