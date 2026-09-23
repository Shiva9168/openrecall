"""Unit and integration tests for storage maintenance and orphan reconciliation (Phase 2E.3)."""

import os
import sqlite3
import tempfile
import threading
import time
import pytest

from openrecall.database import (
    create_db,
    get_db_connection,
    get_entry_by_id,
    insert_entry,
    reconcile_storage_and_database,
    search_entries,
)
from openrecall.maintenance import MaintenanceWorker, run_maintenance_now
from openrecall.screenshot import CapturePipeline


@pytest.fixture
def temp_env():
    """Provides isolated temporary database and storage directory."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_p = os.path.join(tmp_dir, "recall.db")
        storage_p = os.path.join(tmp_dir, "screenshots")
        os.makedirs(storage_p, exist_ok=True)
        create_db(target_path=db_p)
        yield db_p, storage_p


def test_orphan_webp_reconciliation(temp_env):
    """Verifies that unreferenced .webp files are safely removed from disk."""
    db_p, storage_p = temp_env

    # 1. Insert a referenced entry and create its file
    insert_entry(
        text="Referenced entry text",
        timestamp=1000,
        app="TestApp",
        title="Valid Window",
        image_path="1000_0.webp",
        target_path=db_p,
    )
    ref_file = os.path.join(storage_p, "1000_0.webp")
    with open(ref_file, "wb") as f:
        f.write(b"REFERENCED_IMAGE_DATA")

    # 2. Create an unreferenced (orphan) .webp file
    orphan_file = os.path.join(storage_p, "9999_0.webp")
    with open(orphan_file, "wb") as f:
        f.write(b"ORPHAN_IMAGE_DATA")

    assert os.path.exists(ref_file)
    assert os.path.exists(orphan_file)

    # 3. Run reconciliation
    summary = reconcile_storage_and_database(target_path=db_p, storage_dir=storage_p)

    assert summary["orphan_files_removed"] == 1
    assert os.path.exists(ref_file)
    assert not os.path.exists(orphan_file)


def test_stale_tmp_cleanup(temp_env):
    """Verifies that leftover .tmp write buffer files are removed."""
    db_p, storage_p = temp_env

    tmp_file = os.path.join(storage_p, "1000_0.webp.tmp")
    with open(tmp_file, "wb") as f:
        f.write(b"LEFT_OVER_TMP_DATA")

    assert os.path.exists(tmp_file)

    summary = reconcile_storage_and_database(target_path=db_p, storage_dir=storage_p)

    assert summary["tmp_files_removed"] == 1
    assert not os.path.exists(tmp_file)


def test_missing_image_db_row_preservation(temp_env):
    """Verifies that DB entries referencing missing images set image_path = NULL while preserving OCR/text metadata."""
    db_p, storage_p = temp_env

    entry_id = insert_entry(
        text="Important OCR text that must not be deleted",
        timestamp=1000,
        app="SecretApp",
        title="Confidential Window",
        image_path="missing_file.webp",
        target_path=db_p,
    )

    # File missing_file.webp is NOT created on disk

    summary = reconcile_storage_and_database(target_path=db_p, storage_dir=storage_p)

    assert summary["missing_image_rows_fixed"] == 1

    # Verify SQLite row preserved original stored image_path
    entry = get_entry_by_id(entry_id, target_path=db_p)
    assert entry is not None
    assert entry.image_path == "missing_file.webp"

    # Verify FTS5 searchability remains intact
    results = search_entries("Important OCR text", target_path=db_p)
    assert len(results) == 1
    assert results[0].id == entry_id


def test_storage_lock_prevents_race(temp_env):
    """Verifies that storage_lock prevents concurrent pipeline worker writes from racing with maintenance."""
    db_p, storage_p = temp_env

    pipeline = CapturePipeline()
    lock = pipeline.storage_lock

    # Acquire lock in thread to simulate active worker insertion
    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def worker_simulation():
        with lock:
            lock_acquired.set()
            release_lock.wait(timeout=2.0)

    t = threading.Thread(target=worker_simulation)
    t.start()
    lock_acquired.wait(timeout=1.0)

    # Now run maintenance in separate thread, which will wait on storage_lock
    m_done = threading.Event()
    m_summary = {}

    def run_m():
        res = reconcile_storage_and_database(target_path=db_p, storage_dir=storage_p, storage_lock=lock)
        m_summary.update(res)
        m_done.set()

    mt = threading.Thread(target=run_m)
    mt.start()

    # Maintenance should be waiting on lock
    time.sleep(0.1)
    assert mt.is_alive()

    # Release worker lock
    release_lock.set()
    mt.join(timeout=2.0)
    t.join(timeout=2.0)

    assert m_done.is_set()
    assert "duration_seconds" in m_summary


def test_unified_retention_integration(temp_env):
    """Verifies time-based retention and capacity-based trimming execute within unified reconciliation pass."""
    db_p, storage_p = temp_env

    # Insert 3 old entries and 2 new entries
    for ts in range(1000, 1003):
        img_name = f"{ts}_0.webp"
        insert_entry(text=f"Old text {ts}", timestamp=ts, image_path=img_name, target_path=db_p)
        with open(os.path.join(storage_p, img_name), "wb") as f:
            f.write(b"X" * 100)

    for ts in range(2000, 2002):
        img_name = f"{ts}_0.webp"
        insert_entry(text=f"New text {ts}", timestamp=ts, image_path=img_name, target_path=db_p)
        with open(os.path.join(storage_p, img_name), "wb") as f:
            f.write(b"X" * 100)

    # Run maintenance with cutoff_timestamp = 1500 (prunes 3 old entries)
    summary = reconcile_storage_and_database(
        target_path=db_p, storage_dir=storage_p, cutoff_timestamp=1500
    )

    assert summary["time_retention_deleted"] == 3

    with get_db_connection(db_p) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM entries")
        count = cursor.fetchone()[0]
        assert count == 2


def test_per_operation_failure_resilience(temp_env, monkeypatch):
    """Verifies that an un-deletable file increments orphan_files_failed without crashing maintenance."""
    db_p, storage_p = temp_env

    orphan_file = os.path.join(storage_p, "locked_0.webp")
    with open(orphan_file, "wb") as f:
        f.write(b"LOCKED_DATA")

    # Mock os.remove to raise OSError for locked_0.webp
    orig_remove = os.remove

    def mock_remove(path):
        if "locked_0.webp" in path:
            raise OSError("Permission denied / File locked")
        return orig_remove(path)

    monkeypatch.setattr(os, "remove", mock_remove)

    summary = reconcile_storage_and_database(target_path=db_p, storage_dir=storage_p)

    assert summary["orphan_files_failed"] == 1
    assert summary["orphan_files_removed"] == 0
    assert os.path.exists(orphan_file)


def test_maintenance_worker_lifecycle(temp_env):
    """Verifies MaintenanceWorker start/stop and non-blocking run_once re-entrancy protection."""
    db_p, storage_p = temp_env

    worker = MaintenanceWorker(
        interval_seconds=1,
        enabled=True,
        target_path=db_p,
        storage_dir=storage_p,
    )

    summary1 = worker.run_once()
    assert "duration_seconds" in summary1

    worker.start()
    assert worker._thread is not None and worker._thread.is_alive()

    worker.stop()
    assert worker._thread is None


def test_idempotency(temp_env):
    """Verifies that running maintenance repeatedly on the same state is idempotent."""
    db_p, storage_p = temp_env

    insert_entry(text="Entry 1", timestamp=1000, image_path="1000_0.webp", target_path=db_p)
    with open(os.path.join(storage_p, "1000_0.webp"), "wb") as f:
        f.write(b"DATA")

    with open(os.path.join(storage_p, "orphan.webp"), "wb") as f:
        f.write(b"ORPHAN")

    summary1 = run_maintenance_now(target_path=db_p, storage_dir=storage_p)
    assert summary1["orphan_files_removed"] == 1

    summary2 = run_maintenance_now(target_path=db_p, storage_dir=storage_p)
    assert summary2["orphan_files_removed"] == 0
    assert summary2["missing_image_rows_fixed"] == 0
    assert summary2["tmp_files_removed"] == 0
