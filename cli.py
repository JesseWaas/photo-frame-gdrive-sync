import argparse
from pathlib import Path

from googledrive import (
    DRIVE_LOCAL_DEFAULT,
    DRIVE_REMOTE_DEFAULT,
    DRIVE_RETRY_DEFAULT,
    DRIVE_SHARED_WITH_ME_DEFAULT,
    DRIVE_TIMEOUT_DEFAULT,
)
from photoframe import FRAME_DIR_DEFAULT, FRAME_TIMEOUT_DEFAULT
from crop import DEFAULT_ASPECT_RATIO, parse_aspect_ratio
from timestamps import DEFAULT_DATE_POLICY, normalize_date_policy


def cli(argv=None):
    """CLI entrypoint."""
    ap = argparse.ArgumentParser(description="Sync photos from Google Drive to a local cache and photo frame")

    vgroup = ap.add_mutually_exclusive_group()
    vgroup.add_argument("--quiet", "-q", action="store_true", help="less output (warnings and errors only)")
    vgroup.add_argument("--verbose", "-v", action="store_true", help="more output (debug)")

    ap.add_argument(
        "--remote",
        default=DRIVE_REMOTE_DEFAULT,
        help=f"rclone remote folder (default: {DRIVE_REMOTE_DEFAULT})",
    )
    ap.add_argument(
        "--local-path",
        type=Path,
        default=DRIVE_LOCAL_DEFAULT,
        help=f"local cache directory (default: {DRIVE_LOCAL_DEFAULT})",
    )
    ap.add_argument(
        "--frame-dir",
        default=FRAME_DIR_DEFAULT,
        help=f"gphoto2 folder on the frame (default: {FRAME_DIR_DEFAULT})",
    )
    ap.add_argument(
        "--aspect-ratio",
        default=None,
        help=(
            "override target frame aspect ratio as W:H "
            f"(default: value from config/<hostname>.json, else {DEFAULT_ASPECT_RATIO})"
        ),
    )
    ap.add_argument(
        "--date",
        default=None,
        choices=["capture", "upload"],
        help=(
            "override photo date policy: capture keeps EXIF/taken time, "
            f"upload uses the sync/upload time (default: host config or {DEFAULT_DATE_POLICY})"
        ),
    )

    ap.add_argument(
        "--shared-with-me",
        dest="shared_with_me",
        action="store_true",
        default=DRIVE_SHARED_WITH_ME_DEFAULT,
        help="pass --drive-shared-with-me to rclone (default)",
    )
    ap.add_argument(
        "--no-shared-with-me",
        dest="shared_with_me",
        action="store_false",
        help="do not pass --drive-shared-with-me to rclone",
    )

    ap.add_argument(
        "--retries",
        type=int,
        default=DRIVE_RETRY_DEFAULT,
        help=f"retries for external commands (default: {DRIVE_RETRY_DEFAULT})",
    )
    ap.add_argument(
        "--rclone-timeout",
        type=int,
        default=DRIVE_TIMEOUT_DEFAULT,
        help=f"timeout in seconds for rclone commands (default: {DRIVE_TIMEOUT_DEFAULT})",
    )
    ap.add_argument(
        "--gphoto-timeout",
        type=int,
        default=FRAME_TIMEOUT_DEFAULT,
        help=f"timeout in seconds for gphoto2 commands (default: {FRAME_TIMEOUT_DEFAULT})",
    )

    ap.add_argument("--hub-location", "--hub_location", dest="hub_location", help="Hub location (example '1-1')")
    ap.add_argument("--hub-vendor", "--hub_vendor", dest="hub_vendor", help="Hub vendor (example '17ef:103a')")
    ap.add_argument(
        "--mock-frame-dir",
        type=Path,
        default=None,
        help="use a local directory as a fake frame instead of gphoto2 (for testing)",
    )
    ap.add_argument(
        "--sync-files",
        "--sync_files",
        dest="sync_files",
        action="store_true",
        help="Sync local cache from Google Drive",
    )
    ap.add_argument(
        "--update-frame",
        "--update_frame",
        dest="update_frame",
        action="store_true",
        help="Update the photo frame from the local cache",
    )
    ap.add_argument("--diff", action="store_true", help="Print planned uploads and deletes")
    ap.add_argument(
        "--dry-run",
        "--dry_run",
        dest="dry_run",
        action="store_true",
        help="Show planned frame changes without modifying the frame",
    )

    args = ap.parse_args(argv)
    args.local_path = Path(args.local_path).expanduser()
    if args.mock_frame_dir is not None:
        args.mock_frame_dir = Path(args.mock_frame_dir).expanduser()
    if args.aspect_ratio is not None:
        parse_aspect_ratio(args.aspect_ratio)
    if args.date is not None:
        args.date = normalize_date_policy(args.date)
    return args
