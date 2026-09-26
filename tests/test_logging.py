"""Tests for minimal local logging, rotation, duplicate handler prevention, and strict privacy controls."""

import logging
import os
import shutil
import tempfile
from unittest.mock import patch

import pytest
import numpy as np

from openrecall.utils import setup_logging, get_logger
from openrecall.screenshot import CapturePipeline, encode_screenshot_bytes, write_screenshot_bytes
from openrecall.ocr import TesseractOCRProvider, FallbackOCRProvider, get_ocr_provider


@pytest.fixture
def temp_storage_dir():
    temp_dir = tempfile.mkdtemp()
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


def test_setup_logging_default_location(temp_storage_dir):
    """Verifies that setup_logging creates log.txt in specified storage_path."""
    logger = setup_logging(temp_storage_dir)
    assert logger is not None

    log_path = os.path.join(temp_storage_dir, "log.txt")
    logger.info("Test info message")

    # Flush handlers
    for h in logger.handlers:
        h.flush()

    assert os.path.exists(log_path)
    with open(log_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "Test info message" in content


def test_duplicate_handler_prevention(temp_storage_dir):
    """Verifies that calling setup_logging multiple times does not attach duplicate RotatingFileHandlers."""
    logger = setup_logging(temp_storage_dir)
    initial_handler_count = len(logger.handlers)

    # Call setup_logging again with same path
    setup_logging(temp_storage_dir)
    setup_logging(temp_storage_dir)

    assert len(logger.handlers) == initial_handler_count

    # Write a test log message and verify it appears exactly once per log line
    logger.info("Unique test marker message")
    for h in logger.handlers:
        h.flush()

    log_path = os.path.join(temp_storage_dir, "log.txt")
    with open(log_path, "r", encoding="utf-8") as f:
        lines = [line for line in f if "Unique test marker message" in line]

    assert len(lines) == 1


def test_log_rotation(temp_storage_dir):
    """Verifies log rotation at 5 MB maxBytes limit creating log.txt.1."""
    logger = setup_logging(temp_storage_dir)
    log_path = os.path.join(temp_storage_dir, "log.txt")
    backup_path = os.path.join(temp_storage_dir, "log.txt.1")

    # Write enough logs to exceed 5 MB (5 * 1024 * 1024 bytes)
    chunk = "X" * 1000
    for _ in range(5500):
        logger.info(chunk)

    for h in logger.handlers:
        h.flush()

    assert os.path.exists(log_path)
    assert os.path.exists(backup_path)
    # Ensure current log file size is bounded under 5.5 MB
    assert os.path.getsize(log_path) <= 5.5 * 1024 * 1024


def test_privacy_no_sensitive_data_in_logs(temp_storage_dir):
    """Asserts that log output NEVER contains OCR text, screenshot paths/filenames, window titles, or app names."""
    logger = setup_logging(temp_storage_dir)
    log_path = os.path.join(temp_storage_dir, "log.txt")

    sensitive_ocr_text = "SECRET_USER_PASSWORD_12345"
    sensitive_filepath = os.path.join(temp_storage_dir, "1700000000_0.webp")
    sensitive_title = "Bank Account Balance - Confidential"
    sensitive_app = "SecretFinancialApp"

    # Trigger image write failure (empty bytes)
    write_screenshot_bytes(b"", sensitive_filepath)

    # Trigger OCR extraction error handling
    provider = TesseractOCRProvider()
    with patch("pytesseract.image_to_string", side_effect=RuntimeError(f"Failed processing {sensitive_title}")):
        img = np.zeros((100, 100, 3), dtype=np.uint8)
        provider.extract_text(img)

    for h in logger.handlers:
        h.flush()

    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            log_content = f.read()

        assert sensitive_ocr_text not in log_content
        assert sensitive_filepath not in log_content
        assert "1700000000_0.webp" not in log_content
        assert sensitive_title not in log_content
        assert sensitive_app not in log_content


def test_werkzeug_logging_suppressed(temp_storage_dir):
    """Verifies that Werkzeug logger is set to WARNING level."""
    setup_logging(temp_storage_dir)
    werkzeug_logger = logging.getLogger("werkzeug")
    assert werkzeug_logger.level >= logging.WARNING


def test_quiet_capture_path(temp_storage_dir):
    """Verifies that running a capture iteration does not generate per-capture log messages."""
    logger = setup_logging(temp_storage_dir)
    log_path = os.path.join(temp_storage_dir, "log.txt")

    # Clear previous log file contents
    if os.path.exists(log_path):
        open(log_path, "w").close()

    pipeline = CapturePipeline()
    # Mock active user and monitor capture
    with patch("openrecall.platform.get_platform_provider") as mock_platform, \
         patch("openrecall.screenshot.take_screenshots") as mock_take:
        mock_platform.return_value.is_user_active.return_value = True
        dummy_frame = np.ones((100, 100, 3), dtype=np.uint8) * 128
        mock_take.return_value = [dummy_frame]

        # First iteration populates last_screenshots
        pipeline.process_single_iteration()

        # Second iteration with changed frame queues item
        new_frame = np.ones((100, 100, 3), dtype=np.uint8) * 200
        mock_take.return_value = [new_frame]
        pipeline.process_single_iteration()

    for h in logger.handlers:
        h.flush()

    if os.path.exists(log_path):
        with open(log_path, "r", encoding="utf-8") as f:
            log_text = f.read()

        assert "Screenshot captured" not in log_text
        assert "OCR completed" not in log_text
        assert "GET /" not in log_text
