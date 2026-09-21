"""Unit and integration test suite for Phase 2E.1 Time-Based Retention & Deletion."""

import os
import tempfile
import unittest
from unittest.mock import patch

from openrecall.database import (
    create_db,
    delete_entries_older_than,
    delete_entry_by_id,
    get_entry_by_id,
    get_recent_entries,
    insert_entry,
    search_entries,
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

        # Verify database row removed
        self.assertIsNone(get_entry_by_id(entry_id, target_path=self.db_path))

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
        self.assertIsNone(get_entry_by_id(entry_id, target_path=self.db_path))

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
            self.assertIsNone(get_entry_by_id(entry_id, target_path=self.db_path))

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


if __name__ == "__main__":
    unittest.main()
