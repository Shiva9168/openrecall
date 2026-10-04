import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np

from openrecall.database import (
    Entry,
    SCHEMA_VERSION,
    backup_database,
    create_db,
    get_all_entries,
    get_recent_entries,
    get_schema_version,
    get_timestamps,
    insert_entry,
    sanitize_fts5_query,
    search_entries,
)


class TestDatabasePhase1C(unittest.TestCase):

    def setUp(self):
        """Create a temporary database file for each test."""
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_path = self.temp_db.name
        self.temp_db.close()
        create_db(self.db_path)

    def tearDown(self):
        """Clean up database files and backups after each test."""
        if os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except Exception:
                pass
        # Clean up any backup files created during tests
        dir_name = os.path.dirname(self.db_path)
        base_name = os.path.basename(self.db_path)
        for f in os.listdir(dir_name):
            if f.startswith(base_name) and ".v1_backup." in f:
                try:
                    os.remove(os.path.join(dir_name, f))
                except Exception:
                    pass

    def test_schema_version(self):
        """Verify PRAGMA user_version is set to target SCHEMA_VERSION (2)."""
        version = get_schema_version(self.db_path)
        self.assertEqual(version, SCHEMA_VERSION)

    def test_fts5_tables_and_triggers_exist(self):
        """Verify entries_fts table and triggers are created."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='entries_fts'")
            self.assertIsNotNone(cursor.fetchone())

            cursor.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name='entries_ai'")
            self.assertIsNotNone(cursor.fetchone())

    def test_insert_and_fts5_auto_sync(self):
        """Verify inserting an entry automatically syncs to entries_fts."""
        ts = int(time.time())
        row_id = insert_entry(
            text="Developing Python application on Linux MATE desktop",
            timestamp=ts,
            app="VSCode",
            title="main.py - OpenRecall",
            target_path=self.db_path,
        )
        self.assertIsNotNone(row_id)

        # Check entries_fts directly
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT rowid, text, app, title FROM entries_fts WHERE rowid = ?", (row_id,))
            row = cursor.fetchone()
            self.assertIsNotNone(row)
            self.assertIn("Python", row[1])

    def test_fts5_lexical_search_normal_and_technical_terms(self):
        """Test FTS5 search with normal terms, phrases, and technical strings."""
        ts = int(time.time())
        insert_entry("Running server on http://localhost:8080/api", ts, app="Terminal", title="bash", target_path=self.db_path)
        insert_entry("Editing file in /home/shiva/Vibe/openrecall/database.py", ts + 1, app="VSCode", title="database.py", target_path=self.db_path)
        insert_entry("Windows path C:\\Users\\Shiva\\Documents\\report.docx", ts + 2, app="Word", title="report.docx", target_path=self.db_path)

        # 1. Search technical URL
        results = search_entries("localhost:8080", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "Terminal")

        # 2. Search Linux file path
        results = search_entries("/home/shiva", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "VSCode")

        # 3. Search Windows file path
        results = search_entries("Users\\Shiva", target_path=self.db_path)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].app, "Word")

    def test_metadata_filtering(self):
        """Test searching with application, window title, and time range filters."""
        ts = 1000
        insert_entry("Document content A", ts, app="Firefox", title="Page 1", target_path=self.db_path)
        insert_entry("Document content B", ts + 100, app="Chrome", title="Page 2", target_path=self.db_path)
        insert_entry("Document content C", ts + 200, app="Firefox", title="Page 3", target_path=self.db_path)

        # Filter by app
        results = search_entries("Document", app="Firefox", target_path=self.db_path)
        self.assertEqual(len(results), 2)
        for r in results:
            self.assertEqual(r.app, "Firefox")

        # Filter by time range
        results = search_entries("Document", start_time=ts + 50, end_time=ts + 250, target_path=self.db_path)
        self.assertEqual(len(results), 2)

    def test_pagination_bounding(self):
        """Test limit and offset bounds handling."""
        ts = int(time.time())
        for i in range(15):
            insert_entry(f"Screenshot text {i}", ts + i, app="App", title="Title", target_path=self.db_path)

        # Page 1 (limit 5)
        p1 = get_recent_entries(limit=5, offset=0, target_path=self.db_path)
        self.assertEqual(len(p1), 5)
        self.assertEqual(p1[0].timestamp, ts + 14)

        # Page 2 (limit 5, offset 5)
        p2 = get_recent_entries(limit=5, offset=5, target_path=self.db_path)
        self.assertEqual(len(p2), 5)
        self.assertEqual(p2[0].timestamp, ts + 9)

        # Limit upper bound enforcement (500)
        p_large = get_recent_entries(limit=999999, target_path=self.db_path)
        self.assertEqual(len(p_large), 15)

    def test_legacy_schema_v1_migration(self):
        """Test that create_db refuses to automatically migrate a legacy database and raises LegacyDatabaseMigrationRequiredError."""
        from openrecall.database import LegacyDatabaseMigrationRequiredError, detect_database_state, DatabaseState
        legacy_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            # Create a legacy v1 database
            with sqlite3.connect(legacy_db) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """CREATE TABLE entries (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           app TEXT,
                           title TEXT,
                           text TEXT,
                           timestamp INTEGER UNIQUE,
                           embedding BLOB
                       )"""
                )
                cursor.execute("PRAGMA user_version = 1")
                emb_bytes = np.array([0.1, 0.2, 0.3], dtype=np.float32).tobytes()
                cursor.execute(
                    "INSERT INTO entries (app, title, text, timestamp, embedding) VALUES (?, ?, ?, ?, ?)",
                    ("LegacyApp", "LegacyTitle", "Legacy OCR Text", 55555, emb_bytes),
                )
                conn.commit()

            # Verify detection classifies it as LEGACY
            self.assertEqual(detect_database_state(legacy_db), DatabaseState.LEGACY)

            # Calling create_db on legacy database must raise LegacyDatabaseMigrationRequiredError
            with self.assertRaises(LegacyDatabaseMigrationRequiredError):
                create_db(legacy_db)

            # Schema version remains unchanged (v1) and not silently upgraded
            self.assertEqual(get_schema_version(legacy_db), 1)

        finally:
            if os.path.exists(legacy_db):
                try:
                    os.remove(legacy_db)
                except Exception:
                    pass

    def test_legacy_database_normal_startup_friendly_message(self):
        """Test A: Normal startup on legacy DB shows friendly migration message, no traceback, exits with status 1."""
        from unittest.mock import patch
        from io import StringIO
        from openrecall.app import main
        from openrecall.config import parser

        temp_dir = tempfile.mkdtemp(prefix="test_legacy_startup_")
        legacy_db = os.path.join(temp_dir, "recall.db")
        try:
            with sqlite3.connect(legacy_db) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """CREATE TABLE entries (
                           id INTEGER PRIMARY KEY AUTOINCREMENT,
                           app TEXT,
                           title TEXT,
                           text TEXT,
                           timestamp INTEGER UNIQUE,
                           embedding BLOB
                       )"""
                )
                cursor.execute("PRAGMA user_version = 1")
                conn.commit()

            parsed_args = parser.parse_args(["--storage-path", temp_dir])

            stderr_buf = StringIO()
            with patch("openrecall.config.args", parsed_args), \
                 patch("sys.stderr", stderr_buf):
                with self.assertRaises(SystemExit) as cm:
                    main()

                # Verify exit code 1
                self.assertEqual(cm.exception.code, 1)

            err_msg = stderr_buf.getvalue()
            # Assert friendly message requirements
            self.assertIn("An older OpenRecall database was detected", err_msg)
            self.assertIn("Normal startup does not automatically migrate", err_msg)
            self.assertIn("openrecall", err_msg)
            self.assertIn("--migrate", err_msg)
            self.assertIn("--audit-legacy-storage", err_msg)

            # DB remains untouched in v1
            self.assertEqual(get_schema_version(legacy_db), 1)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_already_current_database_migrate_messaging(self):
        """Test B: Running --migrate on v3 database reports already current, no migration performed, exits 0, no 'Successfully migrated' text."""
        from unittest.mock import patch
        from io import StringIO
        from openrecall.app import main
        from openrecall.config import parser

        temp_dir = tempfile.mkdtemp(prefix="test_current_migrate_")
        current_db = os.path.join(temp_dir, "recall.db")
        try:
            create_db(current_db)
            self.assertEqual(get_schema_version(current_db), 3)

            parsed_args = parser.parse_args(["--storage-path", temp_dir, "--migrate"])

            stdout_buf = StringIO()
            with patch("openrecall.config.args", parsed_args), \
                 patch("sys.stdout", stdout_buf):
                with self.assertRaises(SystemExit) as cm:
                    main()

                # Verify exit code 0
                self.assertEqual(cm.exception.code, 0)

            out_msg = stdout_buf.getvalue()
            self.assertIn("already current (v3); no migration required", out_msg)
            self.assertIn("No migration was performed", out_msg)
            self.assertNotIn("Successfully migrated", out_msg)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_unsupported_schema_rejected(self):
        """Test D: Unsupported / altered schema remains strictly rejected."""
        from openrecall.database import UnsupportedDatabaseSchemaError, detect_database_state, DatabaseState, migrate_legacy_database
        temp_dir = tempfile.mkdtemp(prefix="test_unsupported_schema_")
        bad_db = os.path.join(temp_dir, "recall.db")
        try:
            with sqlite3.connect(bad_db) as conn:
                cursor = conn.cursor()
                cursor.execute("CREATE TABLE unknown_table (col1 TEXT)")
                cursor.execute("PRAGMA user_version = 99")
                conn.commit()

            self.assertEqual(detect_database_state(bad_db), DatabaseState.UNKNOWN)

            with self.assertRaises(UnsupportedDatabaseSchemaError):
                migrate_legacy_database(bad_db)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_sanitize_fts5_query_helper(self):
        """Test query sanitization for safe FTS5 execution."""
        self.assertEqual(sanitize_fts5_query(""), "")
        self.assertEqual(sanitize_fts5_query(' "exact phrase" '), '"exact phrase"')
        self.assertEqual(sanitize_fts5_query("python code"), "python* code*")
        self.assertEqual(sanitize_fts5_query("test*"), "test*")

    def test_get_entry_by_id(self):
        """Test retrieving a single database entry by integer ID."""
        from openrecall.database import get_entry_by_id

        ts = int(time.time())
        row_id = insert_entry("Detail test text", ts, app="Firefox", title="Doc Title", target_path=self.db_path)
        self.assertIsNotNone(row_id)

        entry = get_entry_by_id(row_id, target_path=self.db_path)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.id, row_id)
        self.assertEqual(entry.app, "Firefox")
        self.assertEqual(entry.text, "Detail test text")

        # Test nonexistent ID returns None
        self.assertIsNone(get_entry_by_id(999999, target_path=self.db_path))


if __name__ == "__main__":
    unittest.main()