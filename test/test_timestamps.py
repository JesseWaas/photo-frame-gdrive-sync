import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from PIL import Image
import piexif

import crop
import timestamps


class TimestampTests(unittest.TestCase):
    def _write_jpeg_with_exif(self, path: Path, taken: datetime) -> None:
        image = Image.new("RGB", (32, 32), (12, 34, 56))
        image.save(path, format="JPEG", quality=90)
        stamp = taken.strftime("%Y:%m:%d %H:%M:%S").encode("utf-8")
        exif_dict = {
            "0th": {piexif.ImageIFD.DateTime: stamp},
            "Exif": {
                piexif.ExifIFD.DateTimeOriginal: stamp,
                piexif.ExifIFD.DateTimeDigitized: stamp,
            },
            "1st": {},
            "thumbnail": None,
        }
        piexif.insert(piexif.dump(exif_dict), str(path))

    def test_normalize_date_policy(self):
        self.assertEqual(timestamps.normalize_date_policy(None), "upload")
        self.assertEqual(timestamps.normalize_date_policy("capture"), "capture")
        self.assertEqual(timestamps.normalize_date_policy("upload"), "upload")

    def test_resolve_capture_uses_exif(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "photo.jpg"
            taken = datetime(2019, 5, 4, 10, 11, 12)
            self._write_jpeg_with_exif(path, taken)

            resolved = timestamps.resolve_display_datetime(path, "capture", when=datetime(2026, 1, 1))

        self.assertEqual(resolved, taken)

    def test_stage_upload_sets_exif_and_mtime(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            staging = Path(temp_dir) / "staged.jpg"
            taken = datetime(2018, 1, 2, 3, 4, 5)
            self._write_jpeg_with_exif(source, taken)
            upload_time = datetime(2026, 7, 31, 11, 0, 0)
            prepared = crop.PreparedUpload(
                frame_name="abc.jpg",
                source_path=source,
                crop="none",
                date_policy="upload",
            )

            crop.stage_upload_file(
                prepared,
                aspect=crop.parse_aspect_ratio("3:4"),
                staging_path=staging,
                when=upload_time,
            )

            self.assertEqual(timestamps.read_capture_datetime(staging), upload_time)
            self.assertEqual(int(staging.stat().st_mtime), int(upload_time.timestamp()))


if __name__ == "__main__":
    unittest.main()
