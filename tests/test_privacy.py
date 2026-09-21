"""Unit tests for Phase 2D Privacy Pause/Resume Control Layer."""

import logging
import queue
import unittest
from unittest.mock import patch

import numpy as np

from openrecall.privacy import (
    PrivacyPolicy,
    get_privacy_policy,
    log_privacy_pause,
    reset_privacy_policy,
)
from openrecall.screenshot import CapturePipeline


class TestPrivacyPolicyUnit(unittest.TestCase):
    """Unit test suite for PrivacyPolicy pause/resume state management."""

    def setUp(self):
        reset_privacy_policy()

    def tearDown(self):
        reset_privacy_policy()

    def test_pause_resume_lifecycle(self):
        policy = PrivacyPolicy()
        self.assertFalse(policy.is_paused())
        self.assertTrue(policy.should_capture())

        policy.pause()
        self.assertTrue(policy.is_paused())
        self.assertFalse(policy.should_capture())

        policy.resume()
        self.assertFalse(policy.is_paused())
        self.assertTrue(policy.should_capture())

    def test_privacy_safe_pause_logging(self):
        with self.assertLogs("openrecall.privacy", level="DEBUG") as cm:
            log_privacy_pause()

        output = "\n".join(cm.output)
        self.assertIn("Capture skipped while paused", output)


class TestPrivacyPipelineBoundary(unittest.TestCase):
    """Integration test suite ensuring paused captures never reach OCR, storage, or DB."""

    def test_paused_pipeline_iteration_skips_capture(self):
        policy = PrivacyPolicy()
        policy.pause()
        pipeline = CapturePipeline(privacy_policy=policy)

        with patch("openrecall.platform.LinuxPlatformProvider.is_user_active", return_value=True), \
             patch("openrecall.screenshot.take_screenshots") as mock_take_screenshots:

            res = pipeline.process_single_iteration()

            self.assertEqual(res, 0)
            mock_take_screenshots.assert_not_called()
            self.assertTrue(pipeline.queue.empty())

    def test_pause_drains_queue_and_prevents_worker_processing(self):
        policy = PrivacyPolicy()
        pipeline = CapturePipeline(privacy_policy=policy)

        # Enqueue item while active
        item = (1700000000, 0, np.zeros((10, 10, 3), dtype=np.uint8), "App", "Title")
        pipeline.queue.put_nowait(item)
        self.assertEqual(pipeline.queue.qsize(), 1)

        # Pause pipeline
        pipeline.pause()
        self.assertTrue(pipeline.is_paused())
        self.assertTrue(pipeline.queue.empty())

        # Verify worker loop discards item if queued frame is processed while paused
        pipeline.queue.put_nowait(item)
        with patch("openrecall.screenshot.extract_text_from_image") as mock_ocr, \
             patch("openrecall.screenshot.insert_entry") as mock_db:

            if pipeline.privacy_policy.is_paused():
                pipeline.queue.get_nowait()
                pipeline.queue.task_done()

            mock_ocr.assert_not_called()
            mock_db.assert_not_called()


if __name__ == "__main__":
    unittest.main()
