import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from crop import PreparedUpload
import rotation


def _prepared(path: Path, name: str, size: int) -> PreparedUpload:
    path.write_bytes(b"x" * size)
    return PreparedUpload(
        frame_name=name,
        source_path=path,
        crop="none",
        date_policy="upload",
    )


class RotationTests(unittest.TestCase):
    def test_parse_byte_size(self):
        self.assertEqual(rotation.parse_byte_size("20GB"), 20 * 1024**3)
        self.assertEqual(rotation.parse_byte_size("512MB"), 512 * 1024**2)
        self.assertEqual(rotation.parse_byte_size(100), 100)
        self.assertIsNone(rotation.parse_byte_size(None))

    def test_select_rotation_window_respects_max_files_and_rotates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            items = [
                _prepared(root / "a.jpg", "a.jpg", 10),
                _prepared(root / "b.jpg", "b.jpg", 10),
                _prepared(root / "c.jpg", "c.jpg", 10),
                _prepared(root / "d.jpg", "d.jpg", 10),
            ]

            hour_0 = rotation.select_rotation_window(
                items,
                max_files=2,
                rotation="hourly",
                when=datetime.fromtimestamp(0),
            )
            hour_1 = rotation.select_rotation_window(
                items,
                max_files=2,
                rotation="hourly",
                when=datetime.fromtimestamp(3600),
            )

        self.assertEqual([item.frame_name for item in hour_0], ["a.jpg", "b.jpg"])
        self.assertEqual([item.frame_name for item in hour_1], ["b.jpg", "c.jpg"])

    def test_select_rotation_window_respects_max_bytes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            items = [
                _prepared(root / "a.jpg", "a.jpg", 40),
                _prepared(root / "b.jpg", "b.jpg", 40),
                _prepared(root / "c.jpg", "c.jpg", 40),
            ]

            selected = rotation.select_rotation_window(
                items,
                max_bytes=80,
                rotation="off",
                when=datetime(2026, 1, 1),
            )

        self.assertEqual([item.frame_name for item in selected], ["a.jpg", "b.jpg"])

    def test_no_limits_returns_all(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            items = [
                _prepared(root / "a.jpg", "a.jpg", 10),
                _prepared(root / "b.jpg", "b.jpg", 10),
            ]
            selected = rotation.select_rotation_window(items)

        self.assertEqual(selected, items)


if __name__ == "__main__":
    unittest.main()
