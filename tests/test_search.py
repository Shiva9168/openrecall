"""Unit tests for Phase 1H SQLite FTS5 search, metadata filtering, and timeline integration."""

import os
import tempfile
import time
import unittest

import numpy as np

from openrecall.database import (
    create_db,
    get_recent_entries,
    get_timeline_entries,
    insert_entry,
    sanitize_fts5_query,
    search_entries,
)


class TestSearchPhase1H(unittest.TestCase):
    """Test suite for Phase 1H local FTS5 search and timeline retrieval."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_recall.db")
        create_db(self.db_path)

        # Populate test fixtures
        self.now = int(time.time())

        # Entry 1: Terminal with code text
        insert_entry(
            text="def extract_text_from_image(image):\n    return provider.extract_text(image)",
            timestamp=self.now - 300,
            app="Terminal",
            title="bash - openrecall project",
            image_path="shot_1.webp",
            target_path=self.db_path,
        )

        # Entry 2: Browser with documentation
        insert_entry(
            text="SQLite FTS5 full text search documentation. Fast indexed queries on 2 GB RAM baseline.",
            timestamp=self.now - 200,
            app="Firefox",
            title="SQLite FTS5 Documentation",
            image_path="shot_2.webp",
            target_path=self.db_path,
        )

        # Entry 3: Code Editor with Unicode text
        insert_entry(
            text="Antigravity AI engine context summary €100 / $150 price metric.",
            timestamp=self.now - 100,
            app="VSCode",
            title="openrecall/database.py",
            image_path="shot_3.webp",
            target_path=self.db_path,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fts5_single_word_search(self):
        results = search_entries("extract_text", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "Terminal")

    def test_fts5_phrase_search(self):
        results = search_entries('"full text search"', target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "Firefox")

    def test_fts5_prefix_search(self):
        results = search_entries("docu*", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "Firefox")

    def test_end_to_end_ocr_to_fts5_search(self):
        # Add new OCR text entry dynamically
        insert_entry(
            text="Dynamic OCR recognized text sample: Tesseract 5.3",
            timestamp=self.now,
            app="ImageViewer",
            title="Screenshot Preview",
            image_path="shot_dynamic.webp",
            target_path=self.db_path,
        )

        results = search_entries("Tesseract", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Screenshot Preview")

    def test_search_metadata_filters(self):
        # Filter by app "Firefox"
        results_app = search_entries("search", app="Firefox", target_path=self.db_path)
        self.assertEqual(len(results_app), 1)

        # Filter by app "Terminal" (should return zero matching results)
        results_mismatch = search_entries("search", app="Terminal", target_path=self.db_path)
        self.assertEqual(len(results_mismatch), 0)

    def test_search_time_range_filter(self):
        start_t = self.now - 250
        end_t = self.now - 50

        results = search_entries("", start_time=start_t, end_time=end_t, target_path=self.db_path)
        self.assertEqual(len(results), 2)  # Entries 2 and 3

    def test_search_unicode_and_special_chars(self):
        results = search_entries("€100", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "VSCode")

    def test_search_malformed_query_resilience(self):
        # Malformed quotes or odd characters should not crash
        results = search_entries('"', target_path=self.db_path)
        self.assertIsInstance(results, list)

    def test_get_timeline_entries(self):
        timeline = get_timeline_entries(limit=10, target_path=self.db_path)
        self.assertEqual(len(timeline), 3)
        self.assertIsNone(timeline[0].embedding)  # Lightweight without BLOBs
        self.assertEqual(timeline[0].app, "VSCode")

    def test_pagination_bounds(self):
        p1 = get_recent_entries(limit=2, offset=0, target_path=self.db_path)
        self.assertEqual(len(p1), 2)

        p2 = get_recent_entries(limit=2, offset=2, target_path=self.db_path)
        self.assertEqual(len(p2), 1)
        self.assertNotEqual(p1[0].id, p2[0].id)


if __name__ == "__main__":
    unittest.main()
