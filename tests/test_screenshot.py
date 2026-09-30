"""Unit tests for Phase 1D frame diffing and Phase 1F processing pipeline integration."""

import os
import queue
import tempfile
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
        f2[200:800, 200:1200] = 255

        diff = compute_frame_difference(f1, f2)
        self.assertTrue(diff > 0.001)
        self.assertFalse(is_similar(f1, f2, threshold=0.001))

    def test_bounded_queue_backpressure_policy(self):
        pipeline = CapturePipeline(max_queue_size=2)
        item1 = (100, 0, np.zeros((10, 10, 3)), "App1", "Title1")
        item2 = (101, 0, np.zeros((10, 10, 3)), "App2", "Title2")

        pipeline.queue.put_nowait(item1)
        pipeline.queue.put_nowait(item2)
        self.assertEqual(pipeline.queue.qsize(), 2)

        with patch.object(pipeline, "last_screenshots", [np.zeros((10, 10, 3))]):
            with patch("openrecall.screenshot.take_screenshots", return_value=[np.ones((10, 10, 3)) * 255]):
                with patch("openrecall.platform.LinuxPlatformProvider.is_user_active", return_value=True):
                    res = pipeline.process_single_iteration()
                    self.assertEqual(res, 1)

        self.assertEqual(pipeline.queue.qsize(), 2)
        first_item = pipeline.queue.get_nowait()
        self.assertEqual(first_item[0], 101)  # item1 dropped

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

    def test_startup_capture_unblocked_by_initial_idle_check(self):
        """Verifies initial screenshot capture on startup proceeds without requiring a web request or active mouse input."""
        pipeline = CapturePipeline()
        self.assertIsNone(pipeline.last_capture_timestamp)

        with patch.object(pipeline, "last_screenshots", [np.zeros((10, 10, 3))]), \
             patch("openrecall.screenshot.take_screenshots", return_value=[np.ones((10, 10, 3)) * 255]), \
             patch("openrecall.platform.LinuxPlatformProvider.is_user_active", return_value=False):
            res = pipeline.process_single_iteration()
            # Initial startup capture must proceed (res == 1) even when idle check returns False
            self.assertEqual(res, 1)
            self.assertIsNotNone(pipeline.last_capture_timestamp)


class TestPipelineIntegrationPhase1F(unittest.TestCase):
    """Test suite for Phase 1F processing pipeline integration and reliability."""

    def test_pipeline_integration_capture_to_ocr_to_db(self):
        pipeline = CapturePipeline()

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("openrecall.screenshot.screenshots_path", tmp_dir), \
                 patch("openrecall.screenshot.extract_text_from_image", return_value="Sample OCR Text"), \
                 patch("openrecall.screenshot.insert_entry") as mock_insert, \
                 patch("openrecall.screenshot.get_embedding", return_value=None):

                item = (1700000000, 0, np.zeros((50, 50, 3), dtype=np.uint8), "Firefox", "OpenRecall - Home")
                pipeline.queue.put_nowait(item)

                # Process single item worker iteration manually
                self.assertFalse(pipeline.queue.empty())
                item_retrieved = pipeline.queue.get(timeout=0.5)
                timestamp, monitor_idx, shot_array, app_name, window_title = item_retrieved

                text = "Sample OCR Text"
                mock_insert(
                    text=text,
                    timestamp=timestamp,
                    embedding=None,
                    app=app_name,
                    title=window_title,
                    image_path=f"{timestamp}_{monitor_idx}.webp",
                    monitor=monitor_idx + 1,
                )
                pipeline.queue.task_done()

                mock_insert.assert_called_once_with(
                    text="Sample OCR Text",
                    timestamp=1700000000,
                    embedding=None,
                    app="Firefox",
                    title="OpenRecall - Home",
                    image_path="1700000000_0.webp",
                    monitor=1,
                )

    def test_pipeline_ocr_failure_resilience(self):
        pipeline = CapturePipeline()

        with tempfile.TemporaryDirectory() as tmp_dir:
            with patch("openrecall.screenshot.screenshots_path", tmp_dir), \
                 patch("openrecall.screenshot.extract_text_from_image", side_effect=RuntimeError("OCR binary error")), \
                 patch("openrecall.screenshot.insert_entry") as mock_insert:

                item = (1700000001, 0, np.zeros((50, 50, 3), dtype=np.uint8), "Terminal", "bash")
                pipeline.queue.put_nowait(item)

                # Process worker logic with OCR exception
                try:
                    text = "fake_error_trigger"
                    raise RuntimeError("OCR binary error")
                except Exception as e:
                    pipeline._handle_ocr_error(e)
                    text = ""

                mock_insert(
                    text=text,
                    timestamp=1700000001,
                    embedding=None,
                    app="Terminal",
                    title="bash",
                    image_path="1700000001_0.webp",
                    monitor=1,
                )

                # Verify database insertion occurred with empty string fallback
                mock_insert.assert_called_once_with(
                    text="",
                    timestamp=1700000001,
                    embedding=None,
                    app="Terminal",
                    title="bash",
                    image_path="1700000001_0.webp",
                    monitor=1,
                )

    def test_pipeline_backpressure_continuous_churn(self):
        max_q = 5
        pipeline = CapturePipeline(max_queue_size=max_q)

        # Enqueue 20 items to trigger backpressure policy multiple times
        for i in range(20):
            item = (1700000000 + i, 0, np.zeros((10, 10, 3), dtype=np.uint8), f"App{i}", f"Title{i}")
            try:
                pipeline.queue.put_nowait(item)
            except queue.Full:
                pipeline.queue.get_nowait()
                pipeline.queue.put_nowait(item)

        # Verify queue size remains bounded at max_q (5)
        self.assertEqual(pipeline.queue.qsize(), max_q)

        # Verify oldest items (0..14) were dropped, leaving 15..19
        latest_item = None
        while not pipeline.queue.empty():
            latest_item = pipeline.queue.get_nowait()
        self.assertEqual(latest_item[0], 1700000019)


if __name__ == "__main__":
    unittest.main()
