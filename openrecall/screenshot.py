"""Screen capture engine and change-detection pipeline for OpenRecall.

Provides low-CPU downsampled frame difference checking, multi-monitor support,
bounded queue backpressure handling, and graceful error recovery.
"""

import logging
import os
import queue
import threading
import time
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image

from openrecall.config import (
    CAPTURE_INTERVAL_SECONDS,
    CAPTURE_QUEUE_MAX_SIZE,
    DOWNSAMPLE_SIZE,
    FRAME_CHANGE_THRESHOLD,
    args,
    screenshots_path,
)
from openrecall.database import insert_entry
from openrecall.nlp import get_embedding
from openrecall.ocr import extract_text_from_image
from openrecall.platform import MSSScreenCaptureProvider, get_platform_provider

logger = logging.getLogger(__name__)


def compute_downsampled_grayscale(
    img: np.ndarray, target_size: Tuple[int, int] = DOWNSAMPLE_SIZE
) -> np.ndarray:
    """Converts an RGB image array to downsampled float32 grayscale using fast strided slicing."""
    if img is None or img.size == 0 or img.ndim < 3:
        return np.zeros(target_size, dtype=np.float32)

    h, w = img.shape[:2]
    step_y = max(1, h // target_size[1])
    step_x = max(1, w // target_size[0])
    sub = img[::step_y, ::step_x]

    # Convert RGB to grayscale float32
    g = 0.2989 * sub[..., 0] + 0.5870 * sub[..., 1] + 0.1140 * sub[..., 2]
    return g.astype(np.float32)


def compute_frame_difference(
    img1: np.ndarray,
    img2: np.ndarray,
    target_size: Tuple[int, int] = DOWNSAMPLE_SIZE,
) -> float:
    """Calculates normalized Mean Absolute Difference (MAD) between two frames on downsampled grayscale buffers."""
    if img1 is None or img2 is None:
        return 1.0
    if img1.shape != img2.shape:
        return 1.0  # Resolution / monitor layout change -> 100% change

    g1 = compute_downsampled_grayscale(img1, target_size)
    g2 = compute_downsampled_grayscale(img2, target_size)
    return float(np.mean(np.abs(g1 - g2)) / 255.0)


def is_similar(
    img1: np.ndarray,
    img2: np.ndarray,
    threshold: float = FRAME_CHANGE_THRESHOLD,
) -> bool:
    """Checks if two images are similar (difference below threshold)."""
    diff = compute_frame_difference(img1, img2)
    return diff < threshold


def take_screenshots() -> List[np.ndarray]:
    """Captures screenshots of connected monitors via MSSScreenCaptureProvider."""
    primary_only = getattr(args, "primary_monitor_only", False) if args else False
    capture_provider = MSSScreenCaptureProvider()
    return capture_provider.take_screenshots(primary_only=primary_only)


class CapturePipeline:
    """Managed capture pipeline with bounded worker queue and backpressure handling."""

    def __init__(self, max_queue_size: int = CAPTURE_QUEUE_MAX_SIZE):
        self.max_queue_size = max_queue_size
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self.last_screenshots: List[np.ndarray] = []
        self._stop_event = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._consecutive_errors = 0
        self._last_error_log_time = 0.0

    def start(self) -> None:
        """Starts the capture and downstream worker background threads."""
        if self._stop_event.is_set() or self._capture_thread is not None:
            return

        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._processing_worker_loop, daemon=True, name="OpenRecall-Worker"
        )
        self._capture_thread = threading.Thread(
            target=self._capture_loop, daemon=True, name="OpenRecall-Capture"
        )
        self._worker_thread.start()
        self._capture_thread.start()
        logger.info("CapturePipeline started successfully.")

    def stop(self, timeout: float = 2.0) -> None:
        """Stops capture and worker threads gracefully."""
        self._stop_event.set()
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=timeout)
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)
        self._capture_thread = None
        self._worker_thread = None
        logger.info("CapturePipeline stopped.")

    def process_single_iteration(self) -> int:
        """Executes a single capture iteration (useful for testing and manual polling)."""

        platform_provider = get_platform_provider()

        if not platform_provider.is_user_active():
            return 0

        try:
            current_screenshots = take_screenshots()
            self._consecutive_errors = 0
        except Exception as e:
            self._handle_capture_error(e)
            return 0

        if not current_screenshots:
            return 0

        if len(self.last_screenshots) != len(current_screenshots):
            self.last_screenshots = current_screenshots
            return 0

        changed_count = 0
        timestamp = int(time.time())

        for idx, current_shot in enumerate(current_screenshots):
            last_shot = self.last_screenshots[idx]
            diff = compute_frame_difference(current_shot, last_shot)

            if diff >= FRAME_CHANGE_THRESHOLD:
                self.last_screenshots[idx] = current_shot
                app_name = platform_provider.get_active_app_name() or "Unknown App"
                window_title = platform_provider.get_active_window_title() or "Unknown Title"

                item = (timestamp, idx, current_shot, app_name, window_title)

                # Bounded queue backpressure policy: if queue is full, drop oldest item
                try:
                    self.queue.put_nowait(item)
                    changed_count += 1
                except queue.Full:
                    try:
                        self.queue.get_nowait()  # Drop oldest frame
                        self.queue.put_nowait(item)
                        changed_count += 1
                        logger.warning("Capture queue full. Dropped oldest frame to prevent memory inflation.")
                    except queue.Empty:
                        pass

        return changed_count

    def _capture_loop(self) -> None:
        """Main background loop polling monitor frames."""
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        while not self._stop_event.is_set():
            self.process_single_iteration()
            time.sleep(CAPTURE_INTERVAL_SECONDS)

    def _processing_worker_loop(self) -> None:
        """Background worker thread processing queued frames (saving disk image and DB record)."""
        while not self._stop_event.is_set():
            try:
                item = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                timestamp, monitor_idx, shot_array, app_name, window_title = item
                filename = f"{timestamp}_{monitor_idx}.webp"
                filepath = os.path.join(screenshots_path, filename)

                # Save screenshot as WebP image
                image = Image.fromarray(shot_array)
                image.save(filepath, format="webp", quality=80)

                # Process text extraction safely without throwing or killing worker thread
                try:
                    text = extract_text_from_image(shot_array)
                except Exception as ocr_err:
                    self._handle_ocr_error(ocr_err)
                    text = ""

                embedding = get_embedding(text) if text and text.strip() else None
                insert_entry(
                    text=text or "",
                    timestamp=timestamp,
                    embedding=embedding,
                    app=app_name,
                    title=window_title,
                    image_path=filename,
                    monitor=monitor_idx + 1,
                )
            except Exception as e:
                logger.error(f"Error processing frame item in worker loop: {e}")
            finally:
                self.queue.task_done()

    def _handle_capture_error(self, err: Exception) -> None:
        self._consecutive_errors += 1
        now = time.time()
        if now - self._last_error_log_time > 10.0:
            logger.error(f"Screen capture failed (consecutive errors: {self._consecutive_errors}): {err}")
            self._last_error_log_time = now

    def _handle_ocr_error(self, err: Exception) -> None:
        now = time.time()
        if now - self._last_error_log_time > 10.0:
            logger.error(f"OCR text extraction failed: {err}")
            self._last_error_log_time = now



# Global pipeline instance
_pipeline_instance: Optional[CapturePipeline] = None


def get_capture_pipeline() -> CapturePipeline:
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = CapturePipeline()
    return _pipeline_instance


def record_screenshots_thread() -> None:
    """Legacy entrypoint function starting the capture pipeline."""
    pipeline = get_capture_pipeline()
    pipeline.start()
