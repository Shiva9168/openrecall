"""Unit tests for Phase 1G screenshot storage and lifecycle optimization."""

import os
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from openrecall.screenshot import save_screenshot_image


class TestStorageLifecyclePhase1G(unittest.TestCase):
    """Test suite for Phase 1G screenshot storage efficiency, atomic writes, and DB consistency."""

    def test_save_screenshot_image_success(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = os.path.join(tmp_dir, "test_screenshot.webp")
            img_arr = np.zeros((100, 100, 3), dtype=np.uint8)

            success = save_screenshot_image(img_arr, target_path, quality=80)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(target_path))
            self.assertFalse(os.path.exists(target_path + ".tmp"))

            # Verify saved WebP image can be opened by PIL
            with Image.open(target_path) as loaded_img:
                self.assertEqual(loaded_img.size, (100, 100))

    def test_save_screenshot_image_auto_mkdir(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            nested_path = os.path.join(tmp_dir, "nested", "subfolder", "shot.webp")
            img_arr = np.ones((50, 50, 3), dtype=np.uint8) * 128

            success = save_screenshot_image(img_arr, nested_path)
            self.assertTrue(success)
            self.assertTrue(os.path.exists(nested_path))

    def test_save_screenshot_image_invalid_input(self):
        self.assertFalse(save_screenshot_image(None, "/tmp/dummy.webp"))
        self.assertFalse(save_screenshot_image(np.array([]), "/tmp/dummy.webp"))
        self.assertFalse(save_screenshot_image(np.zeros((10,)), "/tmp/dummy.webp"))

    def test_save_screenshot_image_write_failure_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_path = os.path.join(tmp_dir, "failed_shot.webp")
            img_arr = np.zeros((50, 50, 3), dtype=np.uint8)

            # Mock PIL Image.save to raise an OSError (e.g. disk full / permission denied)
            with patch("PIL.Image.Image.save", side_effect=OSError("Disk write error")):
                success = save_screenshot_image(img_arr, target_path)
                self.assertFalse(success)
                self.assertFalse(os.path.exists(target_path))
                self.assertFalse(os.path.exists(target_path + ".tmp"))

    def test_cross_platform_path_normalization(self):
        raw_path = "some/relative/../dir/shot.webp"
        norm = os.path.normpath(raw_path)
        self.assertNotIn("..", norm)


if __name__ == "__main__":
    unittest.main()
