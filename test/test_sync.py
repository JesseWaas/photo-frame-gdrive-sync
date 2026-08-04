import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

import config
import crop
import sync
import util
from googledrive import GoogleDrive


class SyncTests(unittest.TestCase):
    def test_prepare_frame_files_dedupes_identical_content(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "album-a" / "one.jpg"
            second = root / "album-b" / "two.jpg"
            different = root / "album-c" / "three.jpg"
            for path in (first, second, different):
                path.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(b"same-bytes")
            second.write_bytes(b"same-bytes")
            different.write_bytes(b"other-bytes")

            prepared = sync.prepare_frame_files(
                [
                    config.SelectedFile(path=first, crop="none"),
                    config.SelectedFile(path=second, crop="center"),
                    config.SelectedFile(path=different, crop="none"),
                ],
                aspect=crop.parse_aspect_ratio("3:4"),
                date_policy="upload",
            )

            self.assertEqual(len(prepared), 2)
            self.assertEqual(prepared[0].source_path, first)
            self.assertEqual(prepared[0].crop, "none")
            self.assertEqual(prepared[1].source_path, different)
            self.assertEqual(len({item.frame_name for item in prepared}), 2)
            self.assertFalse((root / ".crop_cache").exists())

    def test_diff_compares_by_prepared_frame_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            keep = root / "keep.jpg"
            add = root / "add.jpg"
            keep.write_bytes(b"keep-bytes")
            add.write_bytes(b"add-bytes")

            prepared = sync.prepare_frame_files(
                [
                    config.SelectedFile(path=keep, crop="none"),
                    config.SelectedFile(path=add, crop="none"),
                ],
                aspect=crop.parse_aspect_ratio("3:4"),
                date_policy="upload",
            )
            keep_name = next(item.frame_name for item in prepared if item.source_path == keep)
            add_name = next(item.frame_name for item in prepared if item.source_path == add)
            stale_name = "ffffffffffffffff.jpg"

            new_files, removed_files = sync.diff(
                [(item.frame_name, item.source_path) for item in prepared],
                [
                    Path(f"/store_00010001/DCIM/{keep_name}"),
                    Path(f"/store_00010001/DCIM/{stale_name}"),
                ],
            )

        self.assertEqual(new_files, [(add_name, add)])
        self.assertEqual(removed_files, [(stale_name, Path(f"/store_00010001/DCIM/{stale_name}"))])

    def test_diff_preserve_unmanaged_skips_foreign_names(self):
        desired = [("aaaaaaaaaaaaaaaa.jpg", Path("/cache/a.jpg"))]
        frame_files = [
            Path("/store_00010001/DCIM/aaaaaaaaaaaaaaaa.jpg"),
            Path("/store_00010001/DCIM/ffffffffffffffff.jpg"),
            Path("/store_00010001/DCIM/THUMB0001.thm"),
            Path("/store_00010001/DCIM/Image_20260110_092902.jpeg"),
        ]

        new_files, removed_files = sync.diff(desired, frame_files, preserve_unmanaged=True)

        self.assertEqual(new_files, [])
        self.assertEqual(
            removed_files,
            [("ffffffffffffffff.jpg", Path("/store_00010001/DCIM/ffffffffffffffff.jpg"))],
        )

    def test_is_managed_frame_name(self):
        self.assertTrue(sync.is_managed_frame_name("0123456789abcdef.jpg"))
        self.assertTrue(sync.is_managed_frame_name("0123456789ABCDEF.JPEG"))
        self.assertFalse(sync.is_managed_frame_name("THUMB0001.thm"))
        self.assertFalse(sync.is_managed_frame_name("Image_20260110_092902.jpeg"))
        self.assertFalse(sync.is_managed_frame_name("0123456789abcde.jpg"))  # 15 hex chars

    def test_update_frame_stages_crop_per_upload_and_discards(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = Path(temp_dir)
            photo = local_path / "album-a" / "photo.jpg"
            photo.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (1600, 900), (10, 20, 30)).save(photo)

            googledrive = GoogleDrive(local_path=local_path)
            selected = [config.SelectedFile(path=photo, crop="center")]
            photoframe = MagicMock()
            photoframe.connected.return_value = True
            photoframe.list.return_value = []

            with patch("sync.create_photo_frame", return_value=photoframe):
                sync.update_frame(googledrive, selected, aspect_ratio="3:4")

            photoframe.upload.assert_called_once()
            staged_path, frame_name = photoframe.upload.call_args.args
            self.assertEqual(staged_path.name, frame_name)
            self.assertFalse(staged_path.exists())
            self.assertFalse((local_path / ".crop_cache").exists())
            staging_dir = local_path / ".upload_stage"
            if staging_dir.exists():
                self.assertEqual(list(staging_dir.iterdir()), [])

    def test_update_frame_diff_only_logs_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = Path(temp_dir)
            photo = local_path / "album-a" / "photo.jpg"
            photo.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 30), (10, 20, 30)).save(photo)

            googledrive = GoogleDrive(local_path=local_path)
            selected = [config.SelectedFile(path=photo, crop="center")]
            photoframe = MagicMock()
            photoframe.connected.return_value = True
            photoframe.list.return_value = []

            with patch("sync.create_photo_frame", return_value=photoframe):
                sync.update_frame(googledrive, selected, diff_only=True, aspect_ratio="3:4")

        photoframe.delete.assert_not_called()
        photoframe.upload.assert_not_called()

    def test_update_frame_dry_run_logs_without_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            local_path = Path(temp_dir)
            photo = local_path / "album-a" / "photo.jpg"
            photo.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (40, 30), (10, 20, 30)).save(photo)

            googledrive = GoogleDrive(local_path=local_path)
            selected = [config.SelectedFile(path=photo, crop="center")]
            photoframe = MagicMock()
            photoframe.connected.return_value = True
            photoframe.list.return_value = [Path("/store_00010001/DCIM/unused_photo.jpg")]

            with patch("sync.create_photo_frame", return_value=photoframe):
                sync.update_frame(googledrive, selected, dry_run=True, aspect_ratio="3:4")

        photoframe.delete.assert_not_called()
        photoframe.upload.assert_not_called()

    def test_ensure_frame_connected_retries_after_usb_reset(self):
        photoframe = MagicMock()
        photoframe.connected.side_effect = [False, True]
        hub = MagicMock()
        hub.reset.return_value = True

        with patch("sync.UsbHub", return_value=hub), patch("sync.time.sleep"):
            connected = sync.ensure_frame_connected(photoframe, hub_location="1-1")

        self.assertTrue(connected)
        hub.reset.assert_called_once_with()


class CropTests(unittest.TestCase):
    def test_parse_aspect_ratio(self):
        self.assertEqual(crop.parse_aspect_ratio("3:4").value, 0.75)
        self.assertEqual(crop.parse_aspect_ratio("9/16").value, 9 / 16)

    def test_landscape_center_crop_is_full_height_portrait(self):
        box = crop.crop_window(1600, 900, crop.parse_aspect_ratio("3:4"))
        left, top, right, bottom = box
        self.assertEqual(top, 0)
        self.assertEqual(bottom, 900)
        self.assertEqual(right - left, 675)
        self.assertEqual(left, (1600 - 675) // 2)

    def test_faces_strategy_shifts_horizontal_window(self):
        image = Image.new("RGB", (1600, 900), (30, 30, 30))
        with patch("crop._detect_face_center", return_value=(300.0, 450.0)):
            box = crop.compute_crop_box(image, "faces", crop.parse_aspect_ratio("3:4"))

        left, top, right, bottom = box
        self.assertEqual((top, bottom), (0, 900))
        self.assertEqual(right - left, 675)
        self.assertEqual(left, 0)  # clamped to left edge around face at x=300

    def test_faces_falls_back_to_center_when_no_faces(self):
        image = Image.new("RGB", (1600, 900), (30, 30, 30))
        with patch("crop._detect_face_center", return_value=None):
            box = crop.compute_crop_box(image, "faces", crop.parse_aspect_ratio("3:4"))

        left, top, right, bottom = box
        self.assertEqual((top, bottom), (0, 900))
        self.assertEqual(left, (1600 - 675) // 2)

    def test_select_face_group_prefers_largest_and_ignores_distant_blob(self):
        person = (200, 300, 120, 130)
        tree_blob = (1100, 80, 90, 95)
        group = crop._select_face_group([person, tree_blob], 1600, 900)
        self.assertEqual(group, [person])

    def test_select_face_group_keeps_nearby_faces(self):
        left_face = (200, 300, 100, 110)
        right_face = (340, 310, 95, 105)
        group = crop._select_face_group([left_face, right_face], 1600, 900)
        self.assertEqual(set(group), {left_face, right_face})

    def test_select_face_group_rejects_non_square_blobs(self):
        face = (200, 300, 100, 110)
        wide_blob = (500, 100, 200, 60)
        group = crop._select_face_group([face, wide_blob], 1600, 900)
        self.assertEqual(group, [face])

    def test_select_face_group_rejects_huge_foliage_blob(self):
        person = (700, 350, 120, 130)
        tree = (50, 40, 900, 820)  # most of the frame
        group = crop._select_face_group([person, tree], 1600, 900)
        self.assertEqual(group, [person])

    def test_render_crop_writes_jpeg(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            output = Path(temp_dir) / "out.jpg"
            Image.new("RGB", (1600, 900), (80, 90, 100)).save(source)

            path = crop.render_crop(source, "center", crop.parse_aspect_ratio("3:4"), output)
            with Image.open(path) as image:
                self.assertEqual(image.size, (675, 900))

    def test_stage_upload_file_crops_into_staging(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source.jpg"
            staging = Path(temp_dir) / "staged.jpg"
            Image.new("RGB", (1600, 900), (80, 90, 100)).save(source)
            prepared = crop.PreparedUpload(
                frame_name="abc.jpg",
                source_path=source,
                crop="center",
                date_policy="upload",
            )

            path = crop.stage_upload_file(
                prepared,
                aspect=crop.parse_aspect_ratio("3:4"),
                staging_path=staging,
            )
            with Image.open(path) as image:
                self.assertEqual(image.size, (675, 900))


class ConfigTests(unittest.TestCase):
    def test_load_host_config_merges_defaults_and_host_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            (config_dir / "defaults.json").write_text(
                """
{
  "aspect_ratio": "3:4",
  "date": "upload",
  "max_bytes": "20GB",
  "rotation": "hourly",
  "preserve_unmanaged": true
}
""",
                encoding="utf-8",
            )
            (config_dir / "frame-host.json").write_text(
                """
{
  "aspect_ratio": "9:16",
  "date": "capture",
  "max_files": 100,
  "visibility_rules": [
    {"filter": "^albums/family/.*", "schedule": "* * * * *"},
    {"filter": "^albums/holidays/.*", "schedule": "* * * 12 *"}
  ],
  "crop_rules": [
    {"filter": "^albums/family/.*", "crop": "faces"},
    {"filter": "^albums/holidays/.*", "crop": "center"}
  ]
}
""",
                encoding="utf-8",
            )
            (config_dir / "other-host.json").write_text(
                """
{
  "visibility_rules": [
    {"filter": "^albums/work/.*", "schedule": "* 9-17 * * 1-5"}
  ],
  "crop_rules": [
    {"filter": "^albums/work/.*", "crop": "center"}
  ]
}
""",
                encoding="utf-8",
            )

            host_config = config.load_host_config(config_dir, "frame-host")
            other = config.load_host_config(config_dir, "other-host")

        self.assertEqual(host_config.aspect_ratio, "9:16")
        self.assertEqual(host_config.date, "capture")
        self.assertEqual(host_config.max_bytes, 20 * 1024**3)
        self.assertEqual(host_config.max_files, 100)
        self.assertEqual(host_config.rotation, "hourly")
        self.assertTrue(host_config.preserve_unmanaged)
        self.assertEqual(
            host_config.visibility_rules,
            [
                config.VisibilityRule(filter="^albums/family/.*", schedule="* * * * *"),
                config.VisibilityRule(filter="^albums/holidays/.*", schedule="* * * 12 *"),
            ],
        )
        self.assertEqual(
            host_config.crop_rules,
            [
                config.CropRule(filter="^albums/family/.*", crop="faces"),
                config.CropRule(filter="^albums/holidays/.*", crop="center"),
            ],
        )
        self.assertEqual(other.aspect_ratio, "3:4")
        self.assertTrue(other.preserve_unmanaged)
        self.assertEqual(other.max_bytes, 20 * 1024**3)

    def test_invalid_host_json_selects_no_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            (config_dir / "defaults.json").write_text('{"aspect_ratio": "3:4"}', encoding="utf-8")
            (config_dir / "broken-host.json").write_text('{"visibility_rules": [', encoding="utf-8")

            host_config = config.load_host_config(config_dir, "broken-host")

        self.assertEqual(host_config.visibility_rules, [])

    def test_missing_host_file_selects_no_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            config_dir = Path(temp_dir) / "config"
            config_dir.mkdir()
            (config_dir / "defaults.json").write_text('{"aspect_ratio": "3:4"}', encoding="utf-8")

            host_config = config.load_host_config(config_dir, "unknown-host")

        self.assertEqual(host_config.visibility_rules, [])

    def test_select_files_ors_active_rules_and_applies_crop_rules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            family = root / "albums" / "family" / "a.jpg"
            holidays = root / "albums" / "holidays" / "b.jpg"
            work = root / "albums" / "work" / "c.jpg"
            for path in (family, holidays, work):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x", encoding="utf-8")

            visibility_rules = [
                config.VisibilityRule(filter="^albums/family/.*", schedule="* * * * *"),
                config.VisibilityRule(filter="^albums/holidays/.*", schedule="* * * * *"),
            ]
            crop_rules = [
                config.CropRule(filter="^albums/family/.*", crop="faces"),
                config.CropRule(filter="^albums/holidays/.*", crop="center"),
            ]

            selected = config.select_files(
                root, visibility_rules, crop_rules, when=datetime(2026, 7, 31, 12, 0)
            )

        selected_by_path = {item.path: item.crop for item in selected}
        self.assertEqual(selected_by_path[family], "faces")
        self.assertEqual(selected_by_path[holidays], "center")
        self.assertNotIn(work, selected_by_path)

    def test_select_files_ignores_inactive_schedules(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            december = root / "albums" / "holidays" / "dec.jpg"
            december.parent.mkdir(parents=True, exist_ok=True)
            december.write_text("x", encoding="utf-8")

            visibility_rules = [
                config.VisibilityRule(filter="^albums/holidays/.*", schedule="* * * 12 *"),
            ]
            crop_rules = [
                config.CropRule(filter="^albums/holidays/.*", crop="faces"),
            ]

            july = config.select_files(
                root, visibility_rules, crop_rules, when=datetime(2026, 7, 31, 12, 0)
            )
            dec = config.select_files(
                root, visibility_rules, crop_rules, when=datetime(2026, 12, 25, 12, 0)
            )

        self.assertEqual(july, [])
        self.assertEqual(dec, [config.SelectedFile(path=december, crop="faces")])

    def test_crop_rules_apply_even_when_selection_schedule_differs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            family = root / "albums" / "family" / "a.jpg"
            family.parent.mkdir(parents=True, exist_ok=True)
            family.write_text("x", encoding="utf-8")

            visibility_rules = [
                config.VisibilityRule(filter="^albums/family/.*", schedule="* * * * *"),
            ]
            crop_rules = [
                config.CropRule(filter="^albums/family/.*", crop="faces"),
            ]

            selected = config.select_files(
                root, visibility_rules, crop_rules, when=datetime(2026, 7, 31, 12, 0)
            )

        self.assertEqual(selected, [config.SelectedFile(path=family, crop="faces")])

    def test_missing_config_directory_selects_all_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo = root / "albums" / "a.jpg"
            photo.parent.mkdir(parents=True, exist_ok=True)
            photo.write_text("x", encoding="utf-8")

            host_config = config.load_host_config(root / "config", "any-host")
            selected = config.select_files(
                root, host_config.visibility_rules, host_config.crop_rules
            )

        self.assertIsNone(host_config.visibility_rules)
        self.assertIsNone(host_config.crop_rules)
        self.assertEqual(selected, [config.SelectedFile(path=photo, crop="none")])

    def test_select_files_skips_config_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            photo = root / "albums" / "a.jpg"
            cfg = root / "config" / "kitchen-frame.json"
            photo.parent.mkdir(parents=True, exist_ok=True)
            cfg.parent.mkdir(parents=True, exist_ok=True)
            photo.write_text("x", encoding="utf-8")
            cfg.write_text('{"visibility_rules": []}', encoding="utf-8")

            selected = config.select_files(root, None, None)

        self.assertEqual(selected, [config.SelectedFile(path=photo, crop="none")])


class UtilHashTests(unittest.TestCase):
    def test_file_content_hash_is_stable_and_cached(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "photo.jpg"
            path.write_bytes(b"hash-me")

            first = util.file_content_hash(path)
            second = util.file_content_hash(path)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 64)


if __name__ == "__main__":
    unittest.main()
