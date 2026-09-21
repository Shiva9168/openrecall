"""Storage maintenance controller and periodic background worker for OpenRecall."""

import logging
import threading
import time
from typing import Any, Dict, Optional

from openrecall.config import (
    MAINTENANCE_ENABLED,
    MAINTENANCE_INTERVAL_SECONDS,
    db_path,
    screenshots_path,
)
from openrecall.database import reconcile_storage_and_database

logger = logging.getLogger(__name__)


def run_maintenance_now(
    target_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
    storage_lock: Optional[Any] = None,
    cutoff_timestamp: Optional[int] = None,
    max_capacity_bytes: int = 0,
) -> Dict[str, Any]:
    """Triggers synchronous storage maintenance and orphan reconciliation.

    Args:
        target_path: Optional path to SQLite database file.
        storage_dir: Optional path to screenshot directory.
        storage_lock: Optional threading.Lock protecting pipeline file writes.
        cutoff_timestamp: Optional Unix timestamp for time-based retention cutoff.
        max_capacity_bytes: Target referenced storage capacity in bytes (0 = disabled).

    Returns:
        Structured summary dictionary containing maintenance metrics.
    """
    try:
        summary = reconcile_storage_and_database(
            target_path=target_path or db_path,
            storage_dir=storage_dir or screenshots_path,
            storage_lock=storage_lock,
            cutoff_timestamp=cutoff_timestamp,
            max_capacity_bytes=max_capacity_bytes,
        )
        logger.info(f"Storage maintenance completed successfully: {summary}")
        return summary
    except Exception as e:
        logger.error(f"Storage maintenance failed: {e}")
        return {
            "error": str(e),
            "files_scanned": 0,
            "orphan_files_removed": 0,
            "orphan_files_failed": 0,
            "tmp_files_removed": 0,
            "missing_image_rows_fixed": 0,
            "time_retention_deleted": 0,
            "capacity_retention_deleted": 0,
            "duration_seconds": 0.0,
        }


class MaintenanceWorker:

    """Background thread runner for periodic storage maintenance."""

    def __init__(
        self,
        interval_seconds: int = MAINTENANCE_INTERVAL_SECONDS,
        enabled: bool = MAINTENANCE_ENABLED,
        target_path: Optional[str] = None,
        storage_dir: Optional[str] = None,
        storage_lock: Optional[Any] = None,
    ):
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self.target_path = target_path
        self.storage_dir = storage_dir
        self.storage_lock = storage_lock

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._reconciliation_lock = threading.Lock()

    def start(self) -> None:
        """Starts background periodic maintenance thread if enabled."""
        if not self.enabled:
            logger.info("Storage maintenance is disabled by configuration.")
            return

        if self._thread is not None and self._thread.is_alive():
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="OpenRecall-Maintenance"
        )
        self._thread.start()
        logger.info(f"MaintenanceWorker started (interval: {self.interval_seconds}s).")

    def stop(self, timeout: float = 2.0) -> None:
        """Stops background maintenance thread gracefully."""
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        logger.info("MaintenanceWorker stopped.")

    def run_once(self, cutoff_timestamp: Optional[int] = None, max_capacity_bytes: int = 0) -> Dict[str, Any]:
        """Runs a single maintenance pass with non-blocking re-entrancy protection."""
        if not self._reconciliation_lock.acquire(blocking=False):
            logger.warning("Maintenance pass skipped: Previous maintenance run still in progress.")
            return {"skipped": True}

        try:
            return run_maintenance_now(
                target_path=self.target_path,
                storage_dir=self.storage_dir,
                storage_lock=self.storage_lock,
                cutoff_timestamp=cutoff_timestamp,
                max_capacity_bytes=max_capacity_bytes,
            )
        finally:
            self._reconciliation_lock.release()

    def _run_loop(self) -> None:
        """Periodic loop running maintenance passes."""
        while not self._stop_event.is_set():
            # Wait for interval or stop signal
            if self._stop_event.wait(timeout=float(self.interval_seconds)):
                break
            self.run_once()
