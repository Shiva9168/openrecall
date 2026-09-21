"""Database abstraction layer for OpenRecall.

Provides SQLite FTS5 full-text indexing, metadata filtering, bounded pagination,
schema versioning, and safe data migration from legacy database schemas.
"""

import os
import re
import shutil
import sqlite3
import time
from collections import namedtuple
from typing import List, Optional

import numpy as np

from openrecall.config import db_path

SCHEMA_VERSION = 2

# Structure of a database entry, preserving legacy fields and adding path/platform metadata
Entry = namedtuple(
    "Entry",
    [
        "id",
        "app",
        "title",
        "text",
        "timestamp",
        "embedding",
        "image_path",
        "thumbnail_path",
        "platform",
        "monitor",
    ],
    defaults=(None, None, None, None),
)


def get_db_connection(target_path: Optional[str] = None) -> sqlite3.Connection:
    """Creates a database connection with Row factory enabled."""
    path = target_path or db_path
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def backup_database(target_path: Optional[str] = None) -> Optional[str]:
    """Creates an atomic backup copy of the target SQLite database file."""
    path = target_path or db_path
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return None

    timestamp_str = int(time.time())
    backup_path = f"{path}.v1_backup.{timestamp_str}"
    try:
        with sqlite3.connect(path) as src_conn:
            with sqlite3.connect(backup_path) as dst_conn:
                src_conn.backup(dst_conn)
        return backup_path
    except Exception as e:
        # Fallback to file copy if online backup fails
        try:
            shutil.copy2(path, backup_path)
            return backup_path
        except Exception as copy_err:
            print(f"Error creating database backup: {copy_err}")
            return None


def get_schema_version(target_path: Optional[str] = None) -> int:
    """Returns the current PRAGMA user_version of the database."""
    path = target_path or db_path
    if not os.path.exists(path):
        return 0
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA user_version")
            row = cursor.fetchone()
            return row[0] if row else 0
    except sqlite3.Error:
        return 0


def create_db(target_path: Optional[str] = None) -> None:
    """Creates or migrates the SQLite database to the target Schema Version (v2)."""
    path = target_path or db_path
    current_version = get_schema_version(path)

    if current_version == SCHEMA_VERSION:
        return  # Database is up to date

    # Backup if database exists and contains legacy data
    if os.path.exists(path) and os.path.getsize(path) > 0 and current_version < SCHEMA_VERSION:
        backup_database(path)

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute("BEGIN TRANSACTION")

            # 1. Create base entries table with new columns if not existing
            cursor.execute(
                """CREATE TABLE IF NOT EXISTS entries (
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       app TEXT,
                       title TEXT,
                       text TEXT,
                       timestamp INTEGER UNIQUE NOT NULL,
                       embedding BLOB,
                       image_path TEXT,
                       thumbnail_path TEXT,
                       platform TEXT,
                       monitor INTEGER DEFAULT 1
                   )"""
            )

            # Ensure columns exist on legacy tables being upgraded
            cursor.execute("PRAGMA table_info(entries)")
            existing_cols = {col["name"] for col in cursor.fetchall()}
            for col_name, col_type in [
                ("image_path", "TEXT"),
                ("thumbnail_path", "TEXT"),
                ("platform", "TEXT"),
                ("monitor", "INTEGER DEFAULT 1"),
            ]:
                if col_name not in existing_cols:
                    cursor.execute(f"ALTER TABLE entries ADD COLUMN {col_name} {col_type}")

            # 2. Create structured indexes
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_timestamp ON entries (timestamp)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_app ON entries (app)"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_title ON entries (title)"
            )

            # 3. Create SQLite FTS5 table with unicode61 tokenizer
            cursor.execute(
                """CREATE VIRTUAL TABLE IF NOT EXISTS entries_fts USING fts5(
                       text,
                       app,
                       title,
                       content='entries',
                       content_rowid='id',
                       tokenize='unicode61'
                   )"""
            )


            # 4. Create automatic synchronization triggers
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS entries_ai AFTER INSERT ON entries BEGIN
                       INSERT INTO entries_fts(rowid, text, app, title)
                       VALUES (new.id, new.text, new.app, new.title);
                   END;"""
            )
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS entries_ad AFTER DELETE ON entries BEGIN
                       INSERT INTO entries_fts(entries_fts, rowid, text, app, title)
                       VALUES('delete', old.id, old.text, old.app, old.title);
                   END;"""
            )
            cursor.execute(
                """CREATE TRIGGER IF NOT EXISTS entries_au AFTER UPDATE ON entries BEGIN
                       INSERT INTO entries_fts(entries_fts, rowid, text, app, title)
                       VALUES('delete', old.id, old.text, old.app, old.title);
                       INSERT INTO entries_fts(rowid, text, app, title)
                       VALUES (new.id, new.text, new.app, new.title);
                   END;"""
            )

            # 5. Populate/rebuild FTS5 index for all entries
            cursor.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")


            # 6. Set version stamp
            cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    except sqlite3.Error as e:
        print(f"Database creation/migration failed: {e}")
        raise


def sanitize_fts5_query(user_query: str) -> str:
    """Sanitizes user search queries into valid, safe SQLite FTS5 query strings."""
    if not user_query or not user_query.strip():
        return ""

    query_str = user_query.strip()
    if query_str.startswith('"') and query_str.endswith('"') and query_str.count('"') % 2 == 0:
        return query_str

    words = [w for w in re.split(r"\s+", query_str) if w]
    sanitized_tokens = []

    for word in words:
        w_clean = word.replace('"', "").strip()
        if not w_clean:
            continue
        # Double quote tokens containing non-alphanumeric punctuation (like :, /, \, ., -)
        if re.search(r"[^\w\*]", w_clean):
            sanitized_tokens.append(f'"{w_clean}"')
        else:
            if not w_clean.endswith("*"):
                sanitized_tokens.append(f"{w_clean}*")
            else:
                sanitized_tokens.append(w_clean)

    return " ".join(sanitized_tokens)



def _row_to_entry(row: sqlite3.Row) -> Entry:
    """Helper to convert sqlite3.Row into an Entry namedtuple."""
    embedding = None
    if "embedding" in row.keys() and row["embedding"] is not None:
        try:
            embedding = np.frombuffer(row["embedding"], dtype=np.float32)
        except Exception:
            embedding = None

    keys = row.keys()
    return Entry(
        id=row["id"],
        app=row["app"],
        title=row["title"],
        text=row["text"],
        timestamp=row["timestamp"],
        embedding=embedding,
        image_path=row["image_path"] if "image_path" in keys else None,
        thumbnail_path=row["thumbnail_path"] if "thumbnail_path" in keys else None,
        platform=row["platform"] if "platform" in keys else None,
        monitor=row["monitor"] if "monitor" in keys else 1,
    )


def insert_entry(
    text: str,
    timestamp: int,
    embedding: Optional[np.ndarray] = None,
    app: str = "Unknown App",
    title: str = "Unknown Title",
    image_path: Optional[str] = None,
    thumbnail_path: Optional[str] = None,
    platform: Optional[str] = None,
    monitor: int = 1,
    target_path: Optional[str] = None,
) -> Optional[int]:
    """Inserts a new screenshot record into the database."""
    path = target_path or db_path
    embedding_bytes = (
        embedding.astype(np.float32).tobytes() if embedding is not None else None
    )
    last_row_id: Optional[int] = None

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT INTO entries (text, timestamp, embedding, app, title, image_path, thumbnail_path, platform, monitor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(timestamp) DO NOTHING""",
                (
                    text,
                    timestamp,
                    embedding_bytes,
                    app,
                    title,
                    image_path,
                    thumbnail_path,
                    platform,
                    monitor,
                ),
            )
            conn.commit()
            if cursor.rowcount > 0:
                last_row_id = cursor.lastrowid
    except sqlite3.Error as e:
        print(f"Database error during insertion: {e}")
    return last_row_id


def get_recent_entries(
    limit: int = 50,
    offset: int = 0,
    target_path: Optional[str] = None,
) -> List[Entry]:
    """Retrieves a paginated list of entries ordered descending by timestamp."""
    path = target_path or db_path
    safe_limit = min(max(1, limit), 500)
    safe_offset = max(0, offset)
    entries: List[Entry] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor
                   FROM entries
                   ORDER BY timestamp DESC
                   LIMIT ? OFFSET ?""",
                (safe_limit, safe_offset),
            )
            rows = cursor.fetchall()
            entries = [_row_to_entry(r) for r in rows]
    except sqlite3.Error as e:
        print(f"Database error fetching recent entries: {e}")
    return entries


def get_all_entries(target_path: Optional[str] = None) -> List[Entry]:
    """Legacy compatibility wrapper around get_recent_entries."""
    return get_recent_entries(limit=500, offset=0, target_path=target_path)


def get_timestamps(
    limit: int = 1000,
    offset: int = 0,
    target_path: Optional[str] = None,
) -> List[int]:
    """Retrieves paginated timestamps ordered descending."""
    path = target_path or db_path
    safe_limit = min(max(1, limit), 10000)
    safe_offset = max(0, offset)
    timestamps: List[int] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp FROM entries ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (safe_limit, safe_offset),
            )
            rows = cursor.fetchall()
            timestamps = [row["timestamp"] for row in rows]
    except sqlite3.Error as e:
        print(f"Database error fetching timestamps: {e}")
    return timestamps


def get_available_apps(target_path: Optional[str] = None) -> List[str]:
    """Retrieves a sorted list of unique non-empty application names recorded in the database."""
    path = target_path or db_path
    apps: List[str] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT app FROM entries WHERE app IS NOT NULL AND app != '' ORDER BY app ASC"
            )
            rows = cursor.fetchall()
            apps = [row["app"] for row in rows if row["app"]]
    except sqlite3.Error as e:
        print(f"Database error fetching available apps: {e}")
    return apps


def get_entry_by_id(entry_id: int, target_path: Optional[str] = None) -> Optional[Entry]:
    """Retrieves a single entry by its unique integer database ID."""
    path = target_path or db_path
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor
                   FROM entries
                   WHERE id = ?""",
                (entry_id,),
            )
            row = cursor.fetchone()
            if row:
                return _row_to_entry(row)
    except sqlite3.Error as e:
        print(f"Database error fetching entry by ID {entry_id}: {e}")
    return None




def search_entries(
    query: str,
    app: Optional[str] = None,
    title: Optional[str] = None,
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    limit: int = 50,
    offset: int = 0,
    target_path: Optional[str] = None,
) -> List[Entry]:
    """Performs SQLite FTS5 full-text search with metadata filtering and pagination."""
    path = target_path or db_path
    safe_limit = min(max(1, limit), 500)
    safe_offset = max(0, offset)
    sanitized_query = sanitize_fts5_query(query)
    entries: List[Entry] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()

            if sanitized_query:
                sql = """
                    SELECT e.id, e.app, e.title, e.text, e.timestamp, e.embedding, e.image_path, e.thumbnail_path, e.platform, e.monitor
                    FROM entries e
                    JOIN entries_fts fts ON e.id = fts.rowid
                    WHERE entries_fts MATCH ?
                      AND (? IS NULL OR e.app LIKE ?)
                      AND (? IS NULL OR e.title LIKE ?)
                      AND (? IS NULL OR e.timestamp >= ?)
                      AND (? IS NULL OR e.timestamp <= ?)
                    ORDER BY fts.rank, e.timestamp DESC
                    LIMIT ? OFFSET ?
                """
                app_pattern = f"%{app}%" if app else None
                title_pattern = f"%{title}%" if title else None

                try:
                    cursor.execute(
                        sql,
                        (
                            sanitized_query,
                            app, app_pattern,
                            title, title_pattern,
                            start_time, start_time,
                            end_time, end_time,
                            safe_limit, safe_offset,
                        ),
                    )
                except sqlite3.OperationalError:
                    # Fallback to safe literal quoted query if FTS5 syntax fails
                    escaped_q = query.replace('"', '""')
                    safe_literal = f'"{escaped_q}"'
                    cursor.execute(
                        sql,
                        (
                            safe_literal,
                            app, app_pattern,
                            title, title_pattern,
                            start_time, start_time,
                            end_time, end_time,
                            safe_limit, safe_offset,
                        ),
                    )

            else:
                # Metadata-only filter search
                sql = """
                    SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor
                    FROM entries
                    WHERE (? IS NULL OR app LIKE ?)
                      AND (? IS NULL OR title LIKE ?)
                      AND (? IS NULL OR timestamp >= ?)
                      AND (? IS NULL OR timestamp <= ?)
                    ORDER BY timestamp DESC
                    LIMIT ? OFFSET ?
                """
                app_pattern = f"%{app}%" if app else None
                title_pattern = f"%{title}%" if title else None
                cursor.execute(
                    sql,
                    (
                        app, app_pattern,
                        title, title_pattern,
                        start_time, start_time,
                        end_time, end_time,
                        safe_limit, safe_offset,
                    ),
                )

            rows = cursor.fetchall()
            entries = [_row_to_entry(r) for r in rows]
    except sqlite3.Error as e:
        print(f"Database error during search: {e}")
    return entries


def get_timeline_entries(
    start_time: Optional[int] = None,
    end_time: Optional[int] = None,
    app: Optional[str] = None,
    title: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    target_path: Optional[str] = None,
) -> List[Entry]:
    """Retrieves timeline entries filtered by timestamp range and metadata without returning heavy BLOB columns."""
    path = target_path or db_path
    safe_limit = min(max(1, limit), 500)
    safe_offset = max(0, offset)
    entries: List[Entry] = []

    sql = """
        SELECT id, app, title, text, timestamp, NULL as embedding, image_path, thumbnail_path, platform, monitor
        FROM entries
        WHERE (? IS NULL OR app LIKE ?)
          AND (? IS NULL OR title LIKE ?)
          AND (? IS NULL OR timestamp >= ?)
          AND (? IS NULL OR timestamp <= ?)
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
    """
    app_pattern = f"%{app}%" if app else None
    title_pattern = f"%{title}%" if title else None

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                sql,
                (
                    app, app_pattern,
                    title, title_pattern,
                    start_time, start_time,
                    end_time, end_time,
                    safe_limit, safe_offset,
                ),
            )
            rows = cursor.fetchall()
            entries = [_row_to_entry(r) for r in rows]
    except sqlite3.Error as e:
        print(f"Database error during timeline fetch: {e}")
    return entries

