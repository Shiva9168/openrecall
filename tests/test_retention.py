"""Unit and integration test suite for Phase 2E.1 Time-Based Retention & Deletion."""

import os
import tempfile
import unittest
from unittest.mock import patch

from openrecall.config import parse_max_storage_gb
from openrecall.database import (
    create_db,
    delete_entries_older_than,
    delete_entry_by_id,
    get_entry_by_id,
    get_recent_entries,
    get_referenced_storage_bytes,
    insert_entry,
    search_entries,
    trim_referenced_storage_to_capacity,
)


class TestRetentionPhase2E1(unittest.TestCase):
    """Test suite for single-entry deletion and time-based retention functions."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_recall.db")
        self.img_dir = os.path.join(self.tmp_dir.name, "screenshots")
        os.makedirs(self.img_dir, exist_ok=True)
        create_db(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_sample_file(self, filename: str) -> str:
        filepath = os.path.join(self.img_dir, filename)
        with open(filepath, "w") as f:
            f.write("sample webp image data")
        return filename

    def test_single_entry_deletion_success(self):
        img_name = self._create_sample_file("shot_100.webp")
        entry_id = insert_entry(
            text="Confidential financial report",
            timestamp=1000,
            app="FinanceApp",
            title="Q3 Report",
            image_path=img_name,
            target_path=self.db_path,
        )
        self.assertIsNotNone(entry_id)

        # Verify initial existence
        entry = get_entry_by_id(entry_id, target_path=self.db_path)
        self.assertIsNotNone(entry)
        self.assertTrue(os.path.exists(os.path.join(self.img_dir, img_name)))

        # Verify search works
        results = search_entries("financial", target_path=self.db_path)
        self.assertEqual(len(results), 1)

        # Delete entry
        res = delete_entry_by_id(entry_id, target_path=self.db_path, storage_dir=self.img_dir)
        self.assertTrue(res)

        # Verify database row tombstoned
        del_entry = get_entry_by_id(entry_id, target_path=self.db_path)
        self.assertIsNotNone(del_entry)
        self.assertEqual(del_entry.is_deleted, 1)

        # Verify image file removed
        self.assertFalse(os.path.exists(os.path.join(self.img_dir, img_name)))

        # Verify FTS5 search index updated
        results_after = search_entries("financial", target_path=self.db_path)
        self.assertEqual(len(results_after), 0)

    def test_single_entry_deletion_missing_file_resilience(self):
        # Entry in DB but image file missing from disk
        entry_id = insert_entry(
            text="Text with missing file",
            timestamp=1001,
            app="Notes",
            title="Note",
            image_path="non_existent.webp",
            target_path=self.db_path,
        )
        self.assertIsNotNone(entry_id)

        # Should succeed cleanly without unhandled exceptions
        res = delete_entry_by_id(entry_id, target_path=self.db_path, storage_dir=self.img_dir)
        self.assertTrue(res)
        e = get_entry_by_id(entry_id, target_path=self.db_path)
        self.assertIsNotNone(e)
        self.assertEqual(e.is_deleted, 1)

    def test_repeated_deletion_idempotency(self):
        img_name = self._create_sample_file("shot_102.webp")
        entry_id = insert_entry(
            text="Sample entry for repeated delete",
            timestamp=1002,
            image_path=img_name,
            target_path=self.db_path,
        )

        res1 = delete_entry_by_id(entry_id, target_path=self.db_path, storage_dir=self.img_dir)
        self.assertTrue(res1)

        # Repeated delete for non-existent ID should return False safely
        res2 = delete_entry_by_id(entry_id, target_path=self.db_path, storage_dir=self.img_dir)
        self.assertFalse(res2)

    def test_time_based_retention_cutoff_semantics(self):
        # Create entries with timestamps: 100, 200, 300, 400, 500
        for ts in [100, 200, 300, 400, 500]:
            img = self._create_sample_file(f"shot_{ts}.webp")
            insert_entry(
                text=f"Screenshot at timestamp {ts}",
                timestamp=ts,
                app="Browser",
                title=f"Page {ts}",
                image_path=img,
                target_path=self.db_path,
            )

        # Cutoff = 300. Entries with timestamp < 300 (i.e. 100, 200) deleted.
        # Entries at 300, 400, 500 MUST be preserved.
        summary = delete_entries_older_than(
            cutoff_timestamp=300,
            batch_size=50,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )

        self.assertEqual(summary["total_deleted_rows"], 2)
        self.assertEqual(summary["total_deleted_files"], 2)
        self.assertEqual(summary["failed_file_deletions"], 0)

        # Verify remaining entries in DB
        remaining = get_recent_entries(limit=100, target_path=self.db_path)
        remaining_ts = sorted([r.timestamp for r in remaining])
        self.assertEqual(remaining_ts, [300, 400, 500])

        # Verify files deleted vs preserved
        self.assertFalse(os.path.exists(os.path.join(self.img_dir, "shot_100.webp")))
        self.assertFalse(os.path.exists(os.path.join(self.img_dir, "shot_200.webp")))
        self.assertTrue(os.path.exists(os.path.join(self.img_dir, "shot_300.webp")))
        self.assertTrue(os.path.exists(os.path.join(self.img_dir, "shot_400.webp")))
        self.assertTrue(os.path.exists(os.path.join(self.img_dir, "shot_500.webp")))

    def test_time_based_retention_multiple_batches(self):
        # Insert 7 entries with batch_size=2 to test multiple transaction iterations
        for ts in range(10, 80, 10):  # 10, 20, 30, 40, 50, 60, 70
            img = self._create_sample_file(f"batch_{ts}.webp")
            insert_entry(
                text=f"Batch entry {ts}",
                timestamp=ts,
                image_path=img,
                target_path=self.db_path,
            )

        summary = delete_entries_older_than(
            cutoff_timestamp=60,
            batch_size=2,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )

        # 10, 20, 30, 40, 50 = 5 entries deleted across 3 batches (2 + 2 + 1)
        self.assertEqual(summary["total_deleted_rows"], 5)
        self.assertEqual(summary["batches_processed"], 3)

        remaining = get_recent_entries(limit=100, target_path=self.db_path)
        self.assertEqual(sorted([r.timestamp for r in remaining]), [60, 70])

    def test_retention_empty_database_handling(self):
        summary = delete_entries_older_than(
            cutoff_timestamp=5000,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )
        self.assertEqual(summary["total_deleted_rows"], 0)
        self.assertEqual(summary["total_deleted_files"], 0)
        self.assertEqual(summary["batches_processed"], 0)

    def test_file_deletion_failure_recovery(self):
        img = self._create_sample_file("protected.webp")
        entry_id = insert_entry(
            text="Protected screenshot",
            timestamp=50,
            image_path=img,
            target_path=self.db_path,
        )

        # Mock os.remove to raise OSError
        with patch("os.remove", side_effect=OSError("Permission denied")):
            res = delete_entry_by_id(entry_id, target_path=self.db_path, storage_dir=self.img_dir)
            # DB deletion succeeds even though file removal failed
            self.assertTrue(res)
            e = get_entry_by_id(entry_id, target_path=self.db_path)
            self.assertIsNotNone(e)
            self.assertEqual(e.is_deleted, 1)

    def test_fts5_indexing_after_retention_purge(self):
        insert_entry(text="Keep this alpha data", timestamp=1000, target_path=self.db_path)
        insert_entry(text="Purge this beta data", timestamp=10, target_path=self.db_path)

        # Search before purge
        self.assertEqual(len(search_entries("alpha", target_path=self.db_path)), 1)
        self.assertEqual(len(search_entries("beta", target_path=self.db_path)), 1)

        # Retention purge
        delete_entries_older_than(cutoff_timestamp=500, target_path=self.db_path, storage_dir=self.img_dir)

        # Search after purge
        self.assertEqual(len(search_entries("alpha", target_path=self.db_path)), 1)
        self.assertEqual(len(search_entries("beta", target_path=self.db_path)), 0)


class TestCapacityRetentionPhase2E2(unittest.TestCase):
    """Test suite for Phase 2E.2 referenced storage accounting and capacity retention."""

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp_dir.name, "test_recall.db")
        self.img_dir = os.path.join(self.tmp_dir.name, "screenshots")
        os.makedirs(self.img_dir, exist_ok=True)
        create_db(self.db_path)

    def tearDown(self):
        self.tmp_dir.cleanup()

    def _create_sample_file_of_size(self, filename: str, size_bytes: int) -> str:
        filepath = os.path.join(self.img_dir, filename)
        with open(filepath, "wb") as f:
            f.write(b"X" * size_bytes)
        return filename

    def test_parse_max_storage_gb(self):
        self.assertEqual(parse_max_storage_gb(None), 0)
        self.assertEqual(parse_max_storage_gb(0.0), 0)
        self.assertEqual(parse_max_storage_gb(5.0), 5_000_000_000)

        with self.assertRaises(ValueError):
            parse_max_storage_gb(-1.0)

    def test_get_referenced_storage_bytes_accounting(self):
        # Empty DB
        self.assertEqual(get_referenced_storage_bytes(target_path=self.db_path, storage_dir=self.img_dir), 0)

        # Create 2 referenced files of 1000 and 2000 bytes
        f1 = self._create_sample_file_of_size("ref_1.webp", 1000)
        f2 = self._create_sample_file_of_size("ref_2.webp", 2000)

        insert_entry(text="t1", timestamp=100, image_path=f1, target_path=self.db_path)
        insert_entry(text="t2", timestamp=200, image_path=f2, target_path=self.db_path)

        # Create 1 orphan file (not in DB) of 5000 bytes
        self._create_sample_file_of_size("orphan.webp", 5000)

        # Create 1 active .tmp write file (not in DB) of 3000 bytes
        self._create_sample_file_of_size("active.webp.tmp", 3000)

        # get_referenced_storage_bytes MUST return exactly 3000 bytes (1000 + 2000)
        total_ref = get_referenced_storage_bytes(target_path=self.db_path, storage_dir=self.img_dir)
        self.assertEqual(total_ref, 3000)

    def test_trim_referenced_storage_already_under_capacity(self):
        f1 = self._create_sample_file_of_size("shot1.webp", 1000)
        insert_entry(text="t1", timestamp=100, image_path=f1, target_path=self.db_path)

        summary = trim_referenced_storage_to_capacity(
            max_bytes=5000,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )
        self.assertEqual(summary["total_deleted_rows"], 0)
        self.assertEqual(summary["total_deleted_files"], 0)
        self.assertEqual(summary["final_referenced_bytes"], 1000)

    def test_trim_referenced_storage_to_capacity_success(self):
        # Create 5 entries, 1000 bytes each = 5000 bytes total referenced
        for ts in [100, 200, 300, 400, 500]:
            f = self._create_sample_file_of_size(f"cap_{ts}.webp", 1000)
            insert_entry(text=f"Cap entry {ts}", timestamp=ts, image_path=f, target_path=self.db_path)

        self.assertEqual(get_referenced_storage_bytes(target_path=self.db_path, storage_dir=self.img_dir), 5000)

        # Target quota = 2000 bytes. Must delete 3 oldest entries (100, 200, 300) leaving 400 and 500.
        summary = trim_referenced_storage_to_capacity(
            max_bytes=2000,
            batch_size=50,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )

        self.assertEqual(summary["total_deleted_rows"], 3)
        self.assertEqual(summary["total_deleted_files"], 3)
        self.assertEqual(summary["final_referenced_bytes"], 2000)

        # Verify oldest entries (100, 200, 300) deleted, newest (400, 500) preserved
        remaining = get_recent_entries(limit=100, target_path=self.db_path)
        remaining_ts = sorted([r.timestamp for r in remaining])
        self.assertEqual(remaining_ts, [400, 500])

    def test_trim_referenced_storage_orphan_isolation(self):
        """Crucial test: Verify capacity cleanup operates strictly on referenced storage.

        Orphan files on disk must NOT cause extra valid referenced history to be deleted.
        """
        # Create 3 referenced entries (1000 bytes each = 3000 bytes referenced)
        for ts in [100, 200, 300]:
            f = self._create_sample_file_of_size(f"ref_{ts}.webp", 1000)
            insert_entry(text=f"Ref {ts}", timestamp=ts, image_path=f, target_path=self.db_path)

        # Create a huge orphan file of 50,000 bytes in screenshots/
        self._create_sample_file_of_size("huge_orphan.webp", 50_000)

        # Referenced storage = 3000 bytes. Set target quota = 2000 bytes.
        # Trim should delete 1 oldest referenced entry (1000 bytes reclaimed -> 2000 bytes remaining) and STOP.
        summary = trim_referenced_storage_to_capacity(
            max_bytes=2000,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )

        self.assertEqual(summary["total_deleted_rows"], 1)
        self.assertEqual(summary["final_referenced_bytes"], 2000)

        # 2 referenced entries (200, 300) remain intact
        remaining = get_recent_entries(limit=100, target_path=self.db_path)
        self.assertEqual(sorted([r.timestamp for r in remaining]), [200, 300])

    def test_trim_referenced_storage_disabled(self):
        f = self._create_sample_file_of_size("shot.webp", 1000)
        insert_entry(text="t1", timestamp=100, image_path=f, target_path=self.db_path)

        # max_bytes = 0 (disabled)
        summary = trim_referenced_storage_to_capacity(
            max_bytes=0,
            target_path=self.db_path,
            storage_dir=self.img_dir,
        )
        self.assertEqual(summary["total_deleted_rows"], 0)


if __name__ == "__main__":
    unittest.main()
