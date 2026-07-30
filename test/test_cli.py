import unittest
from pathlib import Path

from cli import cli
from googledrive import DRIVE_LOCAL_DEFAULT, DRIVE_REMOTE_DEFAULT, DRIVE_TIMEOUT_DEFAULT
from photoframe import FRAME_DIR_DEFAULT, FRAME_TIMEOUT_DEFAULT


class CliTests(unittest.TestCase):
    def test_defaults_match_module_constants(self):
        args = cli([])

        self.assertEqual(args.remote, DRIVE_REMOTE_DEFAULT)
        self.assertEqual(args.local_path, DRIVE_LOCAL_DEFAULT)
        self.assertEqual(args.frame_dir, FRAME_DIR_DEFAULT)
        self.assertIsNone(args.aspect_ratio)
        self.assertTrue(args.shared_with_me)
        self.assertEqual(args.rclone_timeout, DRIVE_TIMEOUT_DEFAULT)
        self.assertEqual(args.gphoto_timeout, FRAME_TIMEOUT_DEFAULT)
        self.assertEqual(args.retries, 2)

    def test_runtime_overrides(self):
        args = cli(
            [
                "--remote",
                "google:other",
                "--local-path",
                "~/custom_cache",
                "--frame-dir",
                "/store/custom",
                "--aspect-ratio",
                "9:16",
                "--no-shared-with-me",
                "--retries",
                "5",
                "--rclone-timeout",
                "300",
                "--gphoto-timeout",
                "400",
                "--sync-files",
                "--update-frame",
                "--dry-run",
                "--hub-location",
                "1-1",
            ]
        )

        self.assertEqual(args.remote, "google:other")
        self.assertEqual(args.local_path, Path("~/custom_cache").expanduser())
        self.assertEqual(args.frame_dir, "/store/custom")
        self.assertEqual(args.aspect_ratio, "9:16")
        self.assertFalse(args.shared_with_me)
        self.assertEqual(args.retries, 5)
        self.assertEqual(args.rclone_timeout, 300)
        self.assertEqual(args.gphoto_timeout, 400)
        self.assertTrue(args.sync_files)
        self.assertTrue(args.update_frame)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.hub_location, "1-1")

    def test_underscore_flag_aliases(self):
        args = cli(["--sync_files", "--update_frame", "--dry_run", "--hub_location", "2-1"])
        self.assertTrue(args.sync_files)
        self.assertTrue(args.update_frame)
        self.assertTrue(args.dry_run)
        self.assertEqual(args.hub_location, "2-1")


if __name__ == "__main__":
    unittest.main()
