from pathlib import Path
import logging

import util

logger = logging.getLogger(__name__)

DRIVE_REMOTE_DEFAULT = "google:photo-frame"
DRIVE_LOCAL_DEFAULT = Path("~/google_photo_sync/").expanduser()
DRIVE_TIMEOUT_DEFAULT = 180
DRIVE_RETRY_DEFAULT = 2
DRIVE_SHARED_WITH_ME_DEFAULT = True


class GoogleDrive:
    def __init__(
        self,
        remote_path: str = DRIVE_REMOTE_DEFAULT,
        local_path: Path = DRIVE_LOCAL_DEFAULT,
        timeout: int = DRIVE_TIMEOUT_DEFAULT,
        retries: int = DRIVE_RETRY_DEFAULT,
        shared_with_me: bool = DRIVE_SHARED_WITH_ME_DEFAULT,
    ):
        self.remote_path = remote_path
        self.local_path = Path(local_path).expanduser()
        self.timeout = timeout
        self.retries = retries
        self.shared_with_me = shared_with_me

    def sync(self):
        logger.info("Running rclone sync from %s to %s", self.remote_path, self.local_path)
        argv = ["rclone", "sync", self.remote_path, str(self.local_path.absolute()), "--delete-excluded"]

        if self.shared_with_me:
            argv.append("--drive-shared-with-me")

        util.run(argv, timeout=self.timeout, retries=self.retries)
