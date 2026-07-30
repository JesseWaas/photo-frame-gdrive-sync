import tempfile
import unittest
from pathlib import Path

from PIL import Image

from photoframe import MockPhotoFrame, create_photo_frame


class MockPhotoFrameTests(unittest.TestCase):
    def test_mock_frame_list_upload_delete(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_dir = Path(temp_dir) / "frame"
            source = Path(temp_dir) / "source.jpg"
            Image.new("RGB", (20, 20), (1, 2, 3)).save(source)

            frame = MockPhotoFrame(mock_dir)
            self.assertTrue(frame.connected())
            self.assertEqual(frame.list(), [])

            frame.upload(source, "abc123.jpg")
            listed = frame.list()
            self.assertEqual([path.name for path in listed], ["abc123.jpg"])

            frame.delete(listed[0])
            self.assertEqual(frame.list(), [])

    def test_create_photo_frame_returns_mock_when_dir_set(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            mock_dir = Path(temp_dir) / "frame"
            frame = create_photo_frame(mock_frame_dir=mock_dir)
            self.assertIsInstance(frame, MockPhotoFrame)
            self.assertTrue(frame.connected())
            self.assertTrue(mock_dir.is_dir())


if __name__ == "__main__":
    unittest.main()
