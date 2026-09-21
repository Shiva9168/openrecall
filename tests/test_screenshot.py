import os
import queue
import time
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from openrecall.screenshot import (
    CapturePipeline,
    compute_downsampled_grayscale,
    compute_frame_difference,
    is_similar,
)


class TestScreenshotPipelinePhase1D(unittest.TestCase):

    def test_compute_downsampled_grayscale_shape(self):
        img_1080p = np.zeros((1080, 1920, 3), dtype=np.uint8)
        gray_128 = compute_downsampled_grayscale(img_1080p, target_size=(128, 128))
        self.assertEqual(gray_128.ndim, 2)
        self.assertEqual(gray_128.shape[1], 128)
        self.assertEqual(gray_128.dtype, np.float32)


    def test_frame_difference_identical(self):
        f1 = np.ones((1080, 1920, 3), dtype=np.uint8) * 100
        f2 = np.ones((1080, 1920, 3), dtype=np.uint8) * 100
        diff = compute_frame_difference(f1, f2)
        self.assertEqual(diff, 0.0)
        self.assertTrue(is_similar(f1, f2))

    def test_frame_difference_dimension_change(self):
        f1 = np.zeros((1080, 1920, 3), dtype=np.uint8)
        f2 = np.zeros((1440, 2560, 3), dtype=np.uint8)
        diff = compute_frame_difference(f1, f2)
        self.assertEqual(diff, 1.0)
        self.assertFalse(is_similar(f1, f2))

    def test_frame_difference_threshold_behavior(self):
        f1 = np.zeros((1080, 1920, 3), dtype=np.uint8)
        f2 = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # Add a large modified region
        f2[200:800, 200:1200] = 255

        diff = compute_frame_difference(f1, f2)
        self.assertGreaterThan(diff, 0.001)
        self.assertFalse(is_similar(f1, f2, threshold=0.001))

    def test_bounded_queue_backpressure_policy(self):
        pipeline = CapturePipeline(max_queue_size=2)
        item1 = (100, 0, np.zeros((10, 10, 3)), "App1", "Title1")
        item2 = (101, 0, np.zeros((10, 10, 3)), "App2", "Title2")
        item3 = (102, 0, np.zeros((10, 10, 3)), "App3", "Title3")

        pipeline.queue.put_nowait(item1)
        pipeline.queue.put_nowait(item2)
        self.assertEqual(pipeline.queue.qsize(), 2)

        # Attempting to add item3 when full should drop item1
        with patch.object(pipeline, "last_screenshots", [np.zeros((10, 10, 3))]):
            with patch("openrecall.screenshot.take_screenshots", return_value=[np.ones((10, 10, 3)) * 255]):
                with patch("openrecall.platform.LinuxPlatformProvider.is_user_active", return_value=True):
                    res = pipeline.process_single_iteration()
                    self.assertEqual(res, 1)

        # Queue size should stay bounded at 2
        self.assertEqual(pipeline.queue.qsize(), 2)
        first_item = pipeline.queue.get_nowait()
        self.assertEqual(first_item[0], 101)  # item1 was dropped, item2 is now first

    def test_capture_error_recovery(self):
        pipeline = CapturePipeline()
        with patch("openrecall.screenshot.take_screenshots", side_effect=RuntimeError("Screen capture failed")):
            with patch("openrecall.platform.LinuxPlatformProvider.is_user_active", return_value=True):
                res = pipeline.process_single_iteration()
                self.assertEqual(res, 0)
                self.assertEqual(pipeline._consecutive_errors, 1)

    def test_pipeline_start_stop_lifecycle(self):
        pipeline = CapturePipeline()
        pipeline.start()
        self.assertIsNotNone(pipeline._capture_thread)
        self.assertIsNotNone(pipeline._worker_thread)
        pipeline.stop(timeout=1.0)
        self.assertIsNone(pipeline._capture_thread)
        self.assertIsNone(pipeline._worker_thread)

    def assertGreaterThan(self, a, b):
        self.assertTrue(a > b, f"{a} is not greater than {b}")


if __name__ == "__main__":
    unittest.main()
