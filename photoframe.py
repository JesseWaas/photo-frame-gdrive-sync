import re
import logging
import shutil
from pathlib import Path

import util

logger = logging.getLogger(__name__)

FRAME_DIR_DEFAULT = "/store_00010001/DCIM"
FRAME_TIMEOUT_DEFAULT = 180
FRAME_RETRY_DEFAULT = 2
FILE_NAME_PATTERN = r"FILENAME='([^']+)'"


class PhotoFrame:
    def __init__(
        self,
        frame_dir: str = FRAME_DIR_DEFAULT,
        timeout: int = FRAME_TIMEOUT_DEFAULT,
        retries: int = FRAME_RETRY_DEFAULT,
    ):
        self.frame_dir = frame_dir
        self.timeout = timeout
        self.retries = retries

    def list(self) -> list[Path]:
        """List files in the frame directory via gphoto2."""
        logger.info("Listing frame files from %s", self.frame_dir)
        out = util.run(
            ["gphoto2", "--folder", self.frame_dir, "--list-files", "--parsable"],
            timeout=self.timeout,
            retries=self.retries,
        )
        files: list[Path] = []

        for line in out.splitlines():
            # Example: FILENAME='/store_00010001/DCIM/Image_20260110_092902_535.jpeg' ...
            match = re.search(FILE_NAME_PATTERN, line)
            if match:
                files.append(Path(match.group(1)))

        return files

    def upload(self, local_file: Path, filename=None) -> None:
        """Upload a local file to the frame directory via gphoto2."""
        args = ["gphoto2", "--upload-file", str(local_file), "--folder", self.frame_dir]

        if filename:
            args += ["--filename", filename]

        logger.info("Uploading %s to frame directory %s", local_file, self.frame_dir)
        util.run(args, timeout=self.timeout, retries=self.retries)

    def delete(self, full_path: Path) -> None:
        """Delete a file from the frame via gphoto2 using its full device path."""
        logger.info("Deleting %s from frame", full_path)
        util.run(["gphoto2", "--delete-file", str(full_path)], timeout=self.timeout, retries=self.retries)

    def connected(self) -> bool:
        """Determine if a frame is connected."""
        try:
            out = util.run(["gphoto2", "--auto-detect"], timeout=self.timeout, retries=self.retries)
        except util.CmdError as exc:
            logger.warning("gphoto2 auto-detect failed: %s", exc)
            return False

        return "USB PTP Class Camera" in out


class MockPhotoFrame:
    """Local-directory stand-in for a gphoto2 frame, for hardware-free testing."""

    def __init__(self, mock_dir: Path):
        self.mock_dir = Path(mock_dir).expanduser()
        self.frame_dir = str(self.mock_dir)

    def connected(self) -> bool:
        self.mock_dir.mkdir(parents=True, exist_ok=True)
        logger.info("Mock frame ready at %s", self.mock_dir)
        return True

    def list(self) -> list[Path]:
        self.mock_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(path for path in self.mock_dir.iterdir() if path.is_file())
        logger.info("Listing %d mock frame file(s) from %s", len(files), self.mock_dir)
        return files

    def upload(self, local_file: Path, filename=None) -> None:
        self.mock_dir.mkdir(parents=True, exist_ok=True)
        dest_name = filename or Path(local_file).name
        dest = self.mock_dir / dest_name
        logger.info("Mock upload %s -> %s", local_file, dest)
        shutil.copy2(local_file, dest)

    def delete(self, full_path: Path) -> None:
        path = Path(full_path)
        if not path.exists():
            candidate = self.mock_dir / path.name
            if candidate.exists():
                path = candidate
        logger.info("Mock delete %s", path)
        if path.exists():
            path.unlink()


def create_photo_frame(
    *,
    frame_dir: str = FRAME_DIR_DEFAULT,
    timeout: int = FRAME_TIMEOUT_DEFAULT,
    retries: int = FRAME_RETRY_DEFAULT,
    mock_frame_dir: Path | str | None = None,
) -> PhotoFrame | MockPhotoFrame:
    """Create a real or mock photo-frame backend."""
    if mock_frame_dir is not None:
        return MockPhotoFrame(Path(mock_frame_dir))
    return PhotoFrame(frame_dir=frame_dir, timeout=timeout, retries=retries)
