import logging
import re
import socket
import time
from pathlib import Path

from cli import cli
from config import CONFIG_DIRNAME, HostConfig, SelectedFile, load_host_config, select_files
from crop import (
    DEFAULT_ASPECT_RATIO,
    FRAME_NAME_HASH_LEN,
    UPLOAD_STAGING_DIRNAME,
    AspectRatio,
    PreparedUpload,
    frame_name_for,
    normalize_crop_strategy,
    parse_aspect_ratio,
    stage_upload_file,
)
from googledrive import (
    DRIVE_LOCAL_DEFAULT,
    DRIVE_REMOTE_DEFAULT,
    DRIVE_RETRY_DEFAULT,
    DRIVE_SHARED_WITH_ME_DEFAULT,
    DRIVE_TIMEOUT_DEFAULT,
    GoogleDrive,
)
from photoframe import FRAME_DIR_DEFAULT, FRAME_RETRY_DEFAULT, FRAME_TIMEOUT_DEFAULT, create_photo_frame
from rotation import select_rotation_window
from timestamps import DEFAULT_DATE_POLICY, normalize_date_policy
from usbhub import UsbHub
import util


logger = logging.getLogger(__name__)

MANAGED_FRAME_NAME_RE = re.compile(rf"^[0-9a-f]{{{FRAME_NAME_HASH_LEN}}}\.[^.]+$", re.IGNORECASE)


def is_managed_frame_name(name: str) -> bool:
    """Return True if name matches this tool's uploaded frame filename pattern."""
    return MANAGED_FRAME_NAME_RE.fullmatch(name) is not None


def prepare_frame_files(
    selected_files: list[SelectedFile],
    *,
    aspect: AspectRatio,
    date_policy: str = DEFAULT_DATE_POLICY,
) -> list[PreparedUpload]:
    """Dedupe selected files by source content and assign frame names.

    Cropping is deferred until a file is actually staged for upload.
    """
    prepared: list[PreparedUpload] = []
    seen_hashes: dict[str, Path] = {}
    used_names: set[str] = set()
    date_policy = normalize_date_policy(date_policy)

    for item in selected_files:
        content_hash = util.file_content_hash(item.path)
        if content_hash in seen_hashes:
            logger.info(
                "Skipping duplicate content: %s (same as %s)",
                item.path,
                seen_hashes[content_hash],
            )
            continue

        strategy = normalize_crop_strategy(item.crop)
        suffix = ".jpg" if strategy != "none" else (item.path.suffix.lower() or ".jpg")
        frame_name = frame_name_for(content_hash, strategy, aspect, date_policy, suffix)
        if frame_name in used_names:
            logger.error("Frame name collision for %s (%s); skipping", item.path, frame_name)
            continue

        seen_hashes[content_hash] = item.path
        used_names.add(frame_name)
        prepared.append(
            PreparedUpload(
                frame_name=frame_name,
                source_path=item.path,
                crop=strategy,
                date_policy=date_policy,
            )
        )

    logger.info(
        "Prepared %d unique frame file(s) from %d selected file(s)",
        len(prepared),
        len(selected_files),
    )
    return prepared


def diff(
    source_named: list[tuple[str, Path]],
    frame_files: list[Path],
    *,
    preserve_unmanaged: bool = False,
) -> tuple[list[tuple[str, Path]], list[tuple[str, Path]]]:
    """Compare prepared local files to frame files by frame filename.

    When preserve_unmanaged is True, only delete frame files that look like
    names this tool uploads (hash prefix + extension). Frame-generated
    thumbnails and other foreign files are left alone.
    """
    desired = {name: path for name, path in source_named}
    on_frame = {path.name: path for path in frame_files}
    new_files = [(name, desired[name]) for name in desired.keys() - on_frame.keys()]

    removed_files = []
    for name in on_frame.keys() - desired.keys():
        if preserve_unmanaged and not is_managed_frame_name(name):
            logger.info("Preserving unmanaged frame file: %s", on_frame[name])
            continue
        removed_files.append((name, on_frame[name]))

    return new_files, removed_files


def sync_files(
    *,
    remote: str = DRIVE_REMOTE_DEFAULT,
    local_path: Path = DRIVE_LOCAL_DEFAULT,
    shared_with_me: bool = DRIVE_SHARED_WITH_ME_DEFAULT,
    retries: int = DRIVE_RETRY_DEFAULT,
    rclone_timeout: int = DRIVE_TIMEOUT_DEFAULT,
) -> tuple[GoogleDrive, list[SelectedFile], HostConfig]:
    """Sync Google Drive to the local cache and return filtered files plus host config."""
    googledrive = GoogleDrive(
        remote_path=remote,
        local_path=local_path,
        shared_with_me=shared_with_me,
        retries=retries,
        timeout=rclone_timeout,
    )
    logger.info("Syncing %s to local cache %s", googledrive.remote_path, googledrive.local_path)
    googledrive.sync()

    hostname = socket.gethostname()
    host_config = load_host_config(googledrive.local_path / CONFIG_DIRNAME, hostname)
    selected = select_files(
        googledrive.local_path, host_config.visibility_rules, host_config.crop_rules
    )

    logger.info(
        "Selected %d local files after filtering (aspect_ratio=%s, date=%s)",
        len(selected),
        host_config.aspect_ratio,
        host_config.date,
    )
    for item in selected:
        logger.debug("Selected %s (crop=%s)", item.path, item.crop)

    return googledrive, selected, host_config


def ensure_frame_connected(photoframe, hub_location=None, hub_vendor=None) -> bool:
    """Check for a frame, optionally reset the hub, then retry detection."""
    if photoframe.connected():
        logger.debug("Photo frame detected")
        return True

    logger.warning("Photo frame not detected")
    if not (hub_location or hub_vendor):
        return False

    logger.warning("Attempting USB hub reset")
    if not UsbHub(hub_location, hub_vendor).reset():
        return False

    time.sleep(5)
    if photoframe.connected():
        logger.info("Photo frame detected after USB hub reset")
        return True

    logger.error("Photo frame not detected after USB hub reset")
    return False


def log_diff(new_files: list[tuple[str, Path]], removed_files: list[tuple[str, Path]]) -> None:
    """Log planned frame changes."""
    logger.info("Plan: upload %d, delete %d", len(new_files), len(removed_files))
    for alt_name, path in new_files:
        logger.info("UPLOAD %s <= %s", alt_name, path)
    for _, path in removed_files:
        logger.info("DELETE %s", path)


def update_frame(
    googledrive: GoogleDrive,
    selected_files: list[SelectedFile],
    hub_location=None,
    hub_vendor=None,
    diff_only: bool = False,
    dry_run: bool = False,
    *,
    frame_dir: str = FRAME_DIR_DEFAULT,
    gphoto_timeout: int = FRAME_TIMEOUT_DEFAULT,
    retries: int = FRAME_RETRY_DEFAULT,
    aspect_ratio: str = DEFAULT_ASPECT_RATIO,
    date_policy: str = DEFAULT_DATE_POLICY,
    mock_frame_dir: Path | str | None = None,
    max_bytes: int | None = None,
    max_files: int | None = None,
    rotation: str | None = None,
    preserve_unmanaged: bool = False,
):
    """Apply local cache changes to the frame."""
    photoframe = create_photo_frame(
        frame_dir=frame_dir,
        timeout=gphoto_timeout,
        retries=retries,
        mock_frame_dir=mock_frame_dir,
    )

    if not ensure_frame_connected(photoframe, hub_location, hub_vendor):
        logger.error("Frame not detected")
        return

    aspect = parse_aspect_ratio(aspect_ratio)
    staging_dir = googledrive.local_path / UPLOAD_STAGING_DIRNAME
    prepared = select_rotation_window(
        prepare_frame_files(selected_files, aspect=aspect, date_policy=date_policy),
        max_bytes=max_bytes,
        max_files=max_files,
        rotation=rotation,
    )
    prepared_by_name = {item.frame_name: item for item in prepared}
    source_named = [(item.frame_name, item.source_path) for item in prepared]

    logger.info("Listing files on photo frame")
    new_files, removed_files = diff(
        source_named,
        photoframe.list(),
        preserve_unmanaged=preserve_unmanaged,
    )

    if diff_only or dry_run:
        log_diff(new_files, removed_files)
    if diff_only:
        return

    if not dry_run:
        for _, path in removed_files:
            logger.info("Deleting from frame: %s", path)
            photoframe.delete(path)

        for alt_name, _ in new_files:
            staged = staging_dir / alt_name
            try:
                stage_upload_file(prepared_by_name[alt_name], aspect=aspect, staging_path=staged)
                logger.info("Uploading to frame: %s as %s", staged, alt_name)
                photoframe.upload(staged, alt_name)
            finally:
                try:
                    staged.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning("Failed to remove staging file %s: %s", staged, exc)


def main():
    args = cli()

    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.WARNING
    else:
        level = logging.INFO

    logging.basicConfig(level=level, format="%(levelname)s: %(message)s")

    if not args.sync_files and not args.update_frame:
        logger.error("Choose at least one action: --sync-files and/or --update-frame")
        return 2

    googledrive, selected_files, host_config = sync_files(
        remote=args.remote,
        local_path=args.local_path,
        shared_with_me=args.shared_with_me,
        retries=args.retries,
        rclone_timeout=args.rclone_timeout,
    )

    if args.update_frame:
        aspect_ratio = args.aspect_ratio or host_config.aspect_ratio or DEFAULT_ASPECT_RATIO
        date_policy = args.date or host_config.date or DEFAULT_DATE_POLICY
        logger.info(
            "Using aspect_ratio=%s date=%s max_bytes=%s max_files=%s rotation=%s preserve_unmanaged=%s",
            aspect_ratio,
            date_policy,
            host_config.max_bytes,
            host_config.max_files,
            host_config.rotation or "off",
            host_config.preserve_unmanaged,
        )
        update_frame(
            googledrive,
            selected_files,
            args.hub_location,
            args.hub_vendor,
            diff_only=args.diff,
            dry_run=args.dry_run,
            frame_dir=args.frame_dir,
            gphoto_timeout=args.gphoto_timeout,
            retries=args.retries,
            aspect_ratio=aspect_ratio,
            date_policy=date_policy,
            mock_frame_dir=args.mock_frame_dir,
            max_bytes=host_config.max_bytes,
            max_files=host_config.max_files,
            rotation=host_config.rotation,
            preserve_unmanaged=host_config.preserve_unmanaged,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
