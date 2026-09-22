"""Screen capture engine and change-detection pipeline for OpenRecall.

Provides low-CPU downsampled frame difference checking, multi-monitor support,
bounded queue backpressure handling, and graceful error recovery.
"""

import io
import logging
import os
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

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
from openrecall.privacy import (
    PrivacyPolicy,
    get_privacy_policy,
    log_privacy_pause,
)

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


def encode_screenshot_bytes(
    image_array: np.ndarray,
    quality: int = 80,
) -> Optional[bytes]:
    """Encodes a screenshot numpy array into WebP bytes in-memory (lock-free).

    Args:
        image_array: Input RGB image array (numpy ndarray).
        quality: WebP quality setting (1-100, default: 80).

    Returns:
        Encoded WebP bytes if successful, None otherwise.
    """
    if image_array is None or image_array.size == 0 or image_array.ndim < 3:
        logger.error("Cannot encode screenshot: invalid or empty image array.")
        return None

    try:
        buffer = io.BytesIO()
        image = Image.fromarray(image_array)
        image.save(buffer, format="webp", quality=quality)
        return buffer.getvalue()
    except Exception as e:
        logger.error(f"Failed to encode screenshot to WebP bytes: {e}")
        return None


def write_screenshot_bytes(
    encoded_bytes: bytes,
    filepath: str,
) -> bool:
    """Safely writes pre-encoded WebP bytes to disk using atomic file replacement.

    Ensures parent directories exist, performs write to a temporary file first,
    and replaces the target file atomically to guarantee database/filesystem consistency.

    Args:
        encoded_bytes: Input WebP image bytes.
        filepath: Target absolute path for output image file.

    Returns:
        True if the screenshot file was successfully written to disk, False otherwise.
    """
    if not encoded_bytes:
        logger.error("Cannot write screenshot: invalid or empty bytes buffer.")
        return False

    try:
        norm_path = os.path.normpath(filepath)
        dir_name = os.path.dirname(norm_path)
        if dir_name and not os.path.exists(dir_name):
            os.makedirs(dir_name, exist_ok=True)

        temp_path = norm_path + ".tmp"
        with open(temp_path, "wb") as f:
            f.write(encoded_bytes)

        # Atomic replacement
        os.replace(temp_path, norm_path)
        return True
    except Exception as e:
        logger.error(f"Failed to write screenshot bytes to {filepath}: {e}")
        try:
            temp_path = os.path.normpath(filepath) + ".tmp"
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass
        return False


def save_screenshot_image(
    image_array: np.ndarray,
    filepath: str,
    quality: int = 80,
) -> bool:
    """Safely encodes and saves a screenshot numpy array to disk."""
    encoded_bytes = encode_screenshot_bytes(image_array, quality=quality)
    if not encoded_bytes:
        return False
    return write_screenshot_bytes(encoded_bytes, filepath)


class CapturePipeline:

    """Managed capture pipeline with bounded worker queue, health monitoring, and self-healing recovery."""

    def __init__(
        self,
        max_queue_size: int = CAPTURE_QUEUE_MAX_SIZE,
        privacy_policy: Optional[PrivacyPolicy] = None,
    ):
        self.max_queue_size = max_queue_size
        self.privacy_policy = privacy_policy or get_privacy_policy()
        self.queue: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self.last_screenshots: List[np.ndarray] = []
        self.storage_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._worker_thread: Optional[threading.Thread] = None
        self._consecutive_errors = 0
        self._last_error_log_time = 0.0

        # Operational health & metrics
        self.status = "stopped"
        self.start_time: Optional[float] = None
        self.captures_processed = 0
        self.frames_dropped = 0
        self.worker_restarts = 0
        self.last_capture_timestamp: Optional[int] = None
        self._restart_history: List[float] = []

    def pause(self) -> None:
        """Pauses capture pipeline and immediately clears any queued unprocessed frames."""
        self.privacy_policy.pause()
        self.clear_queue()

    def resume(self) -> None:
        """Resumes capture pipeline."""
        self.privacy_policy.resume()

    def is_paused(self) -> bool:
        """Returns True if capture pipeline is currently paused."""
        return self.privacy_policy.is_paused()

    def clear_queue(self) -> None:
        """Drains and discards all pending items from the processing queue, keeping task_done balanced."""
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
                self.queue.task_done()
            except queue.Empty:
                break

    def start(self) -> None:
        """Starts the capture and downstream worker background threads."""
        if self._capture_thread is not None and self._capture_thread.is_alive():
            return

        self._stop_event.clear()
        self.status = "ok"
        self.start_time = time.time()
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
        self.status = "stopped"

        # Stop capture thread first so no new frames are pushed
        if self._capture_thread and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=timeout)

        # Allow worker thread bounded operational window to process remaining queue items
        deadline = time.time() + timeout
        while not self.queue.empty() and time.time() < deadline:
            time.sleep(0.05)

        # Cleanly drain any remaining items if worker timeout expired
        self.clear_queue()

        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=timeout)

        self._capture_thread = None
        self._worker_thread = None
        logger.info("CapturePipeline stopped.")

    def get_health_status(self) -> Dict[str, Any]:
        """Returns an operational health status summary."""
        capture_alive = self._capture_thread is not None and self._capture_thread.is_alive()
        worker_alive = self._worker_thread is not None and self._worker_thread.is_alive()

        if self._stop_event.is_set() or (self._capture_thread is None and self._worker_thread is None):
            current_status = "stopped"
        elif not capture_alive or not worker_alive:
            current_status = "degraded"
        else:
            current_status = self.status

        uptime = round(time.time() - self.start_time, 1) if self.start_time else 0.0

        return {
            "status": current_status,
            "uptime_seconds": uptime,
            "capture_thread_alive": capture_alive,
            "worker_thread_alive": worker_alive,
            "queue_depth": self.queue.qsize(),
            "captures_processed": self.captures_processed,
            "frames_dropped": self.frames_dropped,
            "worker_restarts": self.worker_restarts,
            "last_capture_timestamp": self.last_capture_timestamp,
        }

    def _check_and_heal_workers(self) -> None:
        """Monitors worker threads and heals/restarts them if unexpectedly dead."""
        if self._stop_event.is_set():
            return

        now = time.time()
        self._restart_history = [t for t in self._restart_history if now - t < 60.0]

        # Check worker thread
        if self._worker_thread is not None and not self._worker_thread.is_alive():
            logger.error("Processing worker thread died unexpectedly.")
            if len(self._restart_history) < 3:
                self._restart_history.append(now)
                self.worker_restarts += 1
                logger.info(f"Self-healing: Restarting processing worker thread (attempt {len(self._restart_history)}/3)...")
                time.sleep(min(2.0 ** len(self._restart_history), 4.0))
                self._worker_thread = threading.Thread(
                    target=self._processing_worker_loop, daemon=True, name="OpenRecall-Worker"
                )
                self._worker_thread.start()
            else:
                self.status = "degraded"
                logger.error("Self-healing limit reached (3 restarts/60s). System status set to degraded.")

        # Check capture thread
        if self._capture_thread is not None and not self._capture_thread.is_alive():
            logger.error("Capture loop thread died unexpectedly.")
            if len(self._restart_history) < 3:
                self._restart_history.append(now)
                self.worker_restarts += 1
                logger.info(f"Self-healing: Restarting capture loop thread (attempt {len(self._restart_history)}/3)...")
                time.sleep(min(2.0 ** len(self._restart_history), 4.0))
                self._capture_thread = threading.Thread(
                    target=self._capture_loop, daemon=True, name="OpenRecall-Capture"
                )
                self._capture_thread.start()
            else:
                self.status = "degraded"
                logger.error("Self-healing limit reached (3 restarts/60s). System status set to degraded.")

    def process_single_iteration(self) -> int:
        """Executes a single capture iteration with pause state checking."""

        platform_provider = get_platform_provider()

        if not platform_provider.is_user_active():
            return 0

        # Early pause check before screenshot capture
        if self.privacy_policy.is_paused():
            log_privacy_pause()
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

        app_name = None
        window_title = None

        for idx, current_shot in enumerate(current_screenshots):
            last_shot = self.last_screenshots[idx]
            diff = compute_frame_difference(current_shot, last_shot)

            if diff >= FRAME_CHANGE_THRESHOLD:
                self.last_screenshots[idx] = current_shot
                item = (timestamp, idx, current_shot, app_name, window_title)

                # Bounded queue backpressure policy: if queue is full, drop oldest item
                try:
                    self.queue.put_nowait(item)
                    changed_count += 1
                    self.last_capture_timestamp = timestamp
                except queue.Full:
                    try:
                        dropped_item = self.queue.get_nowait()  # Drop oldest frame
                        self.queue.task_done()                  # Immediately balance unfinished_tasks
                        self.frames_dropped += 1
                        self.queue.put_nowait(item)
                        changed_count += 1
                        self.last_capture_timestamp = timestamp
                        logger.warning("Capture queue full. Dropped oldest frame to prevent memory inflation.")
                    except queue.Empty:
                        pass

        return changed_count

    def _capture_loop(self) -> None:
        """Main background loop polling monitor frames."""
        os.environ["TOKENIZERS_PARALLELISM"] = "false"

        while not self._stop_event.is_set():
            self.process_single_iteration()
            self._check_and_heal_workers()
            self._stop_event.wait(timeout=CAPTURE_INTERVAL_SECONDS)

    def _processing_worker_loop(self) -> None:
        """Background worker thread processing queued frames (saving disk image and DB record)."""

        while not self._stop_event.is_set():
            try:
                item = self.queue.get(timeout=0.5)
            except queue.Empty:
                continue

            try:
                # Discard queued items if capture is paused
                if self.privacy_policy.is_paused():
                    log_privacy_pause()
                    continue

                timestamp, monitor_idx, shot_array, app_name, window_title = item
                filename = f"{timestamp}_{monitor_idx}.webp"
                filepath = os.path.normpath(os.path.join(screenshots_path, filename))

                # Process text extraction lock-free (CPU heavy)
                try:
                    text = extract_text_from_image(shot_array)
                except Exception as ocr_err:
                    self._handle_ocr_error(ocr_err)
                    text = ""

                embedding = get_embedding(text) if text and text.strip() else None

                # Pre-encode WebP image bytes lock-free in-memory
                webp_bytes = encode_screenshot_bytes(shot_array, quality=80)
                if webp_bytes is None:
                    logger.warning(f"Skipping database insertion for {filename}: In-memory WebP encoding failed.")
                    continue

                # Critical section: file write + DB insertion protected by storage_lock
                with self.storage_lock:
                    if not write_screenshot_bytes(webp_bytes, filepath):
                        logger.warning(f"Skipping database insertion for {filename}: File write failed.")
                        continue

                    try:
                        insert_entry(
                            text=text or "",
                            timestamp=timestamp,
                            embedding=embedding,
                            app=app_name,
                            title=window_title,
                            image_path=filename,
                            monitor=monitor_idx + 1,
                        )
                        self.captures_processed += 1
                    except Exception as db_err:
                        logger.error(f"Database insertion failed for screenshot {filename}: {db_err}")
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
