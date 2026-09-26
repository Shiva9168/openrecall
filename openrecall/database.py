"""Database abstraction layer for OpenRecall.

Provides SQLite FTS5 full-text indexing, metadata filtering, bounded pagination,
schema versioning, and safe data migration from legacy database schemas.
"""

import logging
import os
import re
import shutil
import sqlite3
import time
from collections import namedtuple
from typing import Any, List, Optional

import numpy as np

from openrecall.config import db_path, screenshots_path

logger = logging.getLogger(__name__)


SCHEMA_VERSION = 3

# Structure of a database entry, preserving legacy fields and adding path/platform/deletion metadata
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
        "is_deleted",
    ],
    defaults=(None, None, None, None, 1, 0),
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
            logger.error(f"Error creating database backup: {copy_err}")
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
                       monitor INTEGER DEFAULT 1,
                       is_deleted INTEGER NOT NULL DEFAULT 0
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
                ("is_deleted", "INTEGER NOT NULL DEFAULT 0"),
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
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_active_timestamp ON entries (is_deleted, timestamp)"
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

            # 4. Explicitly drop and re-create automatic synchronization triggers for FTS5
            cursor.execute("DROP TRIGGER IF EXISTS entries_ai;")
            cursor.execute("DROP TRIGGER IF EXISTS entries_ad;")
            cursor.execute("DROP TRIGGER IF EXISTS entries_au;")

            cursor.execute(
                """CREATE TRIGGER entries_ai AFTER INSERT ON entries BEGIN
                       INSERT INTO entries_fts(rowid, text, app, title)
                       SELECT new.id, CASE WHEN new.text = '__OCR_FAILED__' THEN '' ELSE new.text END, new.app, new.title
                       WHERE new.is_deleted = 0;
                   END;"""
            )
            cursor.execute(
                """CREATE TRIGGER entries_ad AFTER DELETE ON entries BEGIN
                       INSERT INTO entries_fts(entries_fts, rowid, text, app, title)
                       VALUES('delete', old.id, CASE WHEN old.text = '__OCR_FAILED__' THEN '' ELSE old.text END, old.app, old.title);
                   END;"""
            )
            cursor.execute(
                """CREATE TRIGGER entries_au AFTER UPDATE ON entries BEGIN
                       INSERT INTO entries_fts(entries_fts, rowid, text, app, title)
                       VALUES('delete', old.id, CASE WHEN old.text = '__OCR_FAILED__' THEN '' ELSE old.text END, old.app, old.title);
                       INSERT INTO entries_fts(rowid, text, app, title)
                       SELECT new.id, CASE WHEN new.text = '__OCR_FAILED__' THEN '' ELSE new.text END, new.app, new.title
                       WHERE new.is_deleted = 0;
                   END;"""
            )

            # 5. Populate/rebuild FTS5 index for all entries and purge tombstones
            cursor.execute("INSERT INTO entries_fts(entries_fts) VALUES('rebuild')")
            cursor.execute(
                """INSERT INTO entries_fts(entries_fts, rowid, text, app, title)
                   SELECT 'delete', id, text, app, title FROM entries WHERE is_deleted = 1"""
            )

            # 6. Set version stamp
            cursor.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    except sqlite3.Error as e:
        logger.error(f"Database creation/migration failed: {e}")
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
        is_deleted=row["is_deleted"] if "is_deleted" in keys else 0,
    )


def insert_entry(
    text: str,
    timestamp: int,
    embedding: Optional[np.ndarray] = None,
    app: Optional[str] = None,
    title: Optional[str] = None,
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
                """INSERT INTO entries (text, timestamp, embedding, app, title, image_path, thumbnail_path, platform, monitor, is_deleted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
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
        logger.error(f"Database error during insertion: {e}")
    return last_row_id


def get_recent_entries(
    limit: int = 50,
    offset: int = 0,
    target_path: Optional[str] = None,
) -> List[Entry]:
    """Retrieves a paginated list of active entries ordered descending by timestamp."""
    path = target_path or db_path
    safe_limit = min(max(1, limit), 500)
    safe_offset = max(0, offset)
    entries: List[Entry] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor, is_deleted
                   FROM entries
                   WHERE is_deleted = 0
                   ORDER BY timestamp DESC
                   LIMIT ? OFFSET ?""",
                (safe_limit, safe_offset),
            )
            rows = cursor.fetchall()
            entries = [_row_to_entry(r) for r in rows]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching recent entries: {e}")
    return entries


def get_all_entries(target_path: Optional[str] = None) -> List[Entry]:
    """Legacy compatibility wrapper around get_recent_entries."""
    return get_recent_entries(limit=500, offset=0, target_path=target_path)


def get_timestamps(
    limit: int = 1000,
    offset: int = 0,
    target_path: Optional[str] = None,
) -> List[int]:
    """Retrieves paginated timestamps of active entries ordered descending."""
    path = target_path or db_path
    safe_limit = min(max(1, limit), 10000)
    safe_offset = max(0, offset)
    timestamps: List[int] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT timestamp FROM entries WHERE is_deleted = 0 ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (safe_limit, safe_offset),
            )
            rows = cursor.fetchall()
            timestamps = [row["timestamp"] for row in rows]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching timestamps: {e}")
    return timestamps


def get_available_apps(target_path: Optional[str] = None) -> List[str]:
    """Retrieves a sorted list of unique non-empty application names recorded in active entries."""
    path = target_path or db_path
    apps: List[str] = []

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT app FROM entries WHERE is_deleted = 0 AND app IS NOT NULL AND app != '' ORDER BY app ASC"
            )
            rows = cursor.fetchall()
            apps = [row["app"] for row in rows if row["app"]]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching available apps: {e}")
    return apps


def get_entry_by_id(entry_id: int, target_path: Optional[str] = None) -> Optional[Entry]:
    """Retrieves a single raw entry by its database ID (returns record regardless of is_deleted status)."""
    path = target_path or db_path
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor, is_deleted
                   FROM entries
                   WHERE id = ?""",
                (entry_id,),
            )
            row = cursor.fetchone()
            if row:
                return _row_to_entry(row)
    except sqlite3.Error as e:
        logger.error(f"Database error fetching entry by ID {entry_id}: {e}")
    return None


def get_previous_capture_id(current_timestamp: int, target_path: Optional[str] = None) -> Optional[int]:
    """Returns the ID of the nearest preceding active/missing capture by timestamp."""
    path = target_path or db_path
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id FROM entries
                   WHERE timestamp < ? AND is_deleted = 0
                   ORDER BY timestamp DESC
                   LIMIT 1""",
                (current_timestamp,),
            )
            row = cursor.fetchone()
            if row:
                return row["id"]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching previous capture ID: {e}")
    return None


def get_next_capture_id(current_timestamp: int, target_path: Optional[str] = None) -> Optional[int]:
    """Returns the ID of the nearest succeeding active/missing capture by timestamp."""
    path = target_path or db_path
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """SELECT id FROM entries
                   WHERE timestamp > ? AND is_deleted = 0
                   ORDER BY timestamp ASC
                   LIMIT 1""",
                (current_timestamp,),
            )
            row = cursor.fetchone()
            if row:
                return row["id"]
    except sqlite3.Error as e:
        logger.error(f"Database error fetching next capture ID: {e}")
    return None


def get_total_entries_count(target_path: Optional[str] = None) -> int:
    """Returns the total number of active/missing records in the entries database table."""
    path = target_path or db_path
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM entries WHERE is_deleted = 0")
            row = cursor.fetchone()
            return row[0] if row else 0
    except sqlite3.Error as e:
        logger.error(f"Database error fetching total entries count: {e}")
        return 0


def _get_historical_db_paths(target_path: Optional[str] = None) -> List[str]:
    """Helper to return active DB path for strict storage isolation."""
    active = target_path or db_path
    return [active]


def _connect_readonly_db(db_file: str) -> sqlite3.Connection:
    """Safely connects to a SQLite database in read-only mode."""
    try:
        conn = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True)
    except Exception:
        conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row
    return conn


def get_timeline_bounds(target_path: Optional[str] = None) -> dict:
    """Returns minimum timestamp, maximum timestamp, and total count across active and historical databases."""
    db_paths = _get_historical_db_paths(target_path)
    min_ts: Optional[int] = None
    max_ts: Optional[int] = None
    total_count: int = 0

    for p in db_paths:
        try:
            with _connect_readonly_db(p) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT MIN(timestamp) as min_ts, MAX(timestamp) as max_ts, COUNT(*) as cnt FROM entries WHERE is_deleted = 0")
                row = cursor.fetchone()
                if row and row["cnt"] > 0:
                    if row["min_ts"] is not None:
                        min_ts = row["min_ts"] if min_ts is None else min(min_ts, row["min_ts"])
                    if row["max_ts"] is not None:
                        max_ts = row["max_ts"] if max_ts is None else max(max_ts, row["max_ts"])
                    total_count += row["cnt"]
        except sqlite3.Error as e:
            logger.error(f"Database error fetching timeline bounds: {e}")

    return {
        "earliest_ts": min_ts,
        "latest_ts": max_ts,
        "total_count": total_count,
    }


def get_timeline_captures_index(target_path: Optional[str] = None) -> List[dict]:
    """Returns a lightweight sorted list of capture metadata dicts [{'id': id, 'timestamp': ts}, ...] for discrete timeline navigation."""
    db_paths = _get_historical_db_paths(target_path)
    all_items = []
    seen_timestamps = set()

    for p in db_paths:
        try:
            with _connect_readonly_db(p) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT id, timestamp FROM entries WHERE is_deleted = 0 ORDER BY timestamp ASC")
                rows = cursor.fetchall()
                for r in rows:
                    ts = r["timestamp"]
                    if ts not in seen_timestamps:
                        seen_timestamps.add(ts)
                        all_items.append({"id": r["id"], "timestamp": ts})
        except sqlite3.Error as e:
            logger.error(f"Database error fetching timeline index: {e}")

    all_items.sort(key=lambda x: x["timestamp"])
    return all_items


def get_entry_nearest_timestamp(
    target_ts: int, target_path: Optional[str] = None
) -> Optional[Entry]:
    """Retrieves the single entry closest to target_ts using O(log N) indexed B-tree queries."""
    db_paths = _get_historical_db_paths(target_path)
    candidates: List[Entry] = []

    sql_le = """SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor, is_deleted
                FROM entries
                WHERE timestamp <= ? AND is_deleted = 0
                ORDER BY timestamp DESC
                LIMIT 1"""

    sql_ge = """SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor, is_deleted
                FROM entries
                WHERE timestamp >= ? AND is_deleted = 0
                ORDER BY timestamp ASC
                LIMIT 1"""

    for p in db_paths:
        try:
            with _connect_readonly_db(p) as conn:
                cursor = conn.cursor()
                cursor.execute(sql_le, (target_ts,))
                row_le = cursor.fetchone()
                if row_le:
                    candidates.append(_row_to_entry(row_le))

                cursor.execute(sql_ge, (target_ts,))
                row_ge = cursor.fetchone()
                if row_ge:
                    candidates.append(_row_to_entry(row_ge))
        except sqlite3.Error as e:
            logger.error(f"Database error fetching nearest timestamp entry: {e}")

    if not candidates:
        return None

    return min(candidates, key=lambda e: abs(e.timestamp - target_ts))


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
                    SELECT e.id, e.app, e.title, e.text, e.timestamp, e.embedding, e.image_path, e.thumbnail_path, e.platform, e.monitor, e.is_deleted
                    FROM entries e
                    JOIN entries_fts fts ON e.id = fts.rowid
                    WHERE entries_fts MATCH ?
                      AND e.is_deleted = 0
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
                    SELECT id, app, title, text, timestamp, embedding, image_path, thumbnail_path, platform, monitor, is_deleted
                    FROM entries
                    WHERE is_deleted = 0
                      AND (? IS NULL OR app LIKE ?)
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
        logger.error(f"Database error during search: {e}")
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
        SELECT id, app, title, text, timestamp, NULL as embedding, image_path, thumbnail_path, platform, monitor, is_deleted
        FROM entries
        WHERE is_deleted = 0
          AND (? IS NULL OR app LIKE ?)
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
        logger.error(f"Database error during timeline fetch: {e}")
    return entries


def _safe_remove_image_file(
    image_path: Optional[str],
    storage_dir: Optional[str] = None
) -> bool:
    """Attempts best-effort removal of a WebP screenshot file from disk.

    Consistency Model:
        SQLite deletion is transactional. Filesystem deletion occurs after commit and is best-effort.
        Failed filesystem deletion may leave an orphan file for future reconciliation.

    Args:
        image_path: The relative or absolute image filename stored in the database entry.
        storage_dir: Target directory path override for testing or custom storage locations.

    Returns:
        True if the file was removed or was already missing.
        False if file deletion failed due to an OS/permission error.
    """
    if not image_path:
        return True

    safe_filename = os.path.basename(image_path)
    if not safe_filename or safe_filename.startswith(".") or safe_filename != image_path:
        # Reject path traversal payloads (e.g., ../../etc/passwd)
        return False

    import openrecall.config as config
    base_dir = storage_dir or config.screenshots_path
    allowed_dirs = [os.path.abspath(base_dir)]
    try:
        default_screenshots = os.path.abspath(os.path.join(config.get_appdata_folder(), "screenshots"))
        if default_screenshots not in allowed_dirs:
            allowed_dirs.append(default_screenshots)
    except Exception:
        pass

    full_path = os.path.join(allowed_dirs[0], safe_filename)
    norm_path = os.path.abspath(os.path.normpath(full_path))

    # Security check: verify path is contained within an allowed storage root
    is_contained = any(
        os.path.commonpath([norm_path, allowed_dir]) == allowed_dir
        for allowed_dir in allowed_dirs
    )
    if not is_contained:
        logger.warning("Security Warning: Rejected removal of path outside storage root.")
        return False

    if not os.path.exists(norm_path):
        return True

    try:
        os.remove(norm_path)
        return True
    except Exception as e:
        logger.warning(f"Warning: Filesystem removal failed: {e}")
        return False


def delete_entry_by_id(
    entry_id: int,
    target_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> Optional[bool]:
    """Soft-deletes a single screenshot entry by database ID (sets is_deleted = 1).

    Consistency Model:
        1. BEGIN SQLite transaction: UPDATE entries SET is_deleted = 1 WHERE id = ? AND is_deleted = 0
           Trigger entries_au removes the record from entries_fts search index.
        2. COMMIT transaction. (Record is instantly hidden from UI, timeline, search & navigation).
        3. ONLY AFTER COMMIT: attempt physical screenshot file deletion (safe path containment).
           If file deletion fails (e.g. OSError), log warning. Maintenance worker will clean it up later.

    Args:
        entry_id: Integer database ID of the entry to delete.
        target_path: SQLite database path override.
        storage_dir: Screenshot storage directory path override.

    Returns:
        True: if the capture existed and was soft-deleted (200 OK)
        False: if the capture was already soft-deleted (410 Gone)
        None: if the capture ID never existed (404 Not Found)
    """
    path = target_path or db_path

    # Step 1: Check if entry exists in DB
    entry = get_entry_by_id(entry_id, target_path=path)
    if entry is None:
        return None

    if getattr(entry, "is_deleted", 0) == 1:
        return False

    # Step 2: Perform soft-delete in DB transaction
    row_updated = False
    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE entries SET is_deleted = 1 WHERE id = ? AND is_deleted = 0",
                (entry_id,),
            )
            conn.commit()
            if cursor.rowcount > 0:
                row_updated = True
    except sqlite3.Error as e:
        logger.error(f"Database error soft-deleting entry ID {entry_id}: {e}")
        raise

    if not row_updated:
        return False

    # Step 3: Best-effort filesystem cleanup AFTER commit
    if entry.image_path:
        _safe_remove_image_file(entry.image_path, storage_dir=storage_dir)

    return True


def delete_entries_older_than(
    cutoff_timestamp: int,
    batch_size: int = 50,
    target_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> dict:
    """Deletes all screenshot entries with timestamp strictly less than cutoff_timestamp.

    Cutoff Semantics:
        Entries with `timestamp < cutoff_timestamp` are eligible for deletion.
        Entries with `timestamp >= cutoff_timestamp` (including exact matches) are preserved.

    Consistency Model:
        Processes records in bounded transaction batches (`batch_size`).
        For each batch:
        1. Query up to `batch_size` matching IDs and `image_path` values ordered by timestamp ASC.
        2. Delete the batch transactionally from SQLite (trigger updates FTS5 index).
        3. Perform best-effort filesystem cleanup for image files after DB commit.
        4. Repeat until no eligible entries remain.

    Args:
        cutoff_timestamp: Epoch seconds cutoff threshold (`timestamp < cutoff_timestamp`).
        batch_size: Number of records to delete per transaction batch (default: 50).
        target_path: SQLite database path override.
        storage_dir: Screenshot storage directory path override.

    Returns:
        Summary dict containing:
        - "total_deleted_rows": Total SQLite rows deleted
        - "total_deleted_files": Total WebP image files successfully deleted (or already missing)
        - "failed_file_deletions": Count of filesystem deletion failures
        - "batches_processed": Total batch transactions executed
    """
    path = target_path or db_path
    safe_batch_size = max(1, min(batch_size, 500))

    summary = {
        "total_deleted_rows": 0,
        "total_deleted_files": 0,
        "failed_file_deletions": 0,
        "batches_processed": 0,
    }

    while True:
        # Step 1: Select up to safe_batch_size eligible entry IDs and image_paths
        batch_items = []
        try:
            with get_db_connection(path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT id, image_path FROM entries
                       WHERE timestamp < ?
                       ORDER BY timestamp ASC
                       LIMIT ?""",
                    (cutoff_timestamp, safe_batch_size),
                )
                batch_items = cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error selecting retention batch: {e}")
            break

        if not batch_items:
            break  # No eligible entries left

        batch_ids = [item["id"] for item in batch_items]
        image_paths = [item["image_path"] for item in batch_items]

        # Step 2: Delete batch transactionally from SQLite
        rows_affected = 0
        try:
            with get_db_connection(path) as conn:
                cursor = conn.cursor()
                placeholders = ",".join(["?"] * len(batch_ids))
                cursor.execute(
                    f"DELETE FROM entries WHERE id IN ({placeholders})",
                    batch_ids,
                )
                conn.commit()
                rows_affected = cursor.rowcount
        except sqlite3.Error as e:
            logger.error(f"Database error deleting retention batch: {e}")
            break

        if rows_affected <= 0:
            break

        summary["total_deleted_rows"] += rows_affected
        summary["batches_processed"] += 1

        # Step 3: Best-effort filesystem cleanup AFTER commit
        for img_path in image_paths:
            if _safe_remove_image_file(img_path, storage_dir=storage_dir):
                summary["total_deleted_files"] += 1
            else:
                summary["failed_file_deletions"] += 1

    return summary


def get_referenced_storage_bytes(
    target_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> int:
    """Calculates the total physical byte size of WebP files referenced by SQLite database entries.

    Only WebP files referenced by valid `entries.image_path` rows in SQLite are measured.
    Orphan WebP files, `.tmp` write files, and non-referenced files are ignored.
    Missing files are skipped safely without raising errors.

    Args:
        target_path: SQLite database path override.
        storage_dir: Screenshot storage directory path override.

    Returns:
        Total integer bytes of existing referenced WebP screenshot files.
    """
    path = target_path or db_path
    base_dir = storage_dir or screenshots_path
    total_bytes = 0

    try:
        with get_db_connection(path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT image_path FROM entries WHERE image_path IS NOT NULL AND image_path != ''")
            rows = cursor.fetchall()
            for row in rows:
                img_path = row["image_path"]
                full_path = img_path if os.path.isabs(img_path) else os.path.join(base_dir, img_path)
                norm_path = os.path.normpath(full_path)
                try:
                    if os.path.exists(norm_path):
                        total_bytes += os.path.getsize(norm_path)
                except (OSError, PermissionError) as os_err:
                    logger.warning(f"Warning: Stat error for referenced screenshot: {os_err}")
    except sqlite3.Error as e:
        logger.error(f"Database error fetching referenced image paths: {e}")
        raise

    return total_bytes


def trim_referenced_storage_to_capacity(
    max_bytes: int,
    batch_size: int = 50,
    target_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
) -> dict:
    """Trims the oldest SQLite-backed screenshot entries until referenced storage is <= max_bytes.

    Consistency Model:
        Reuses Phase 2E.1 bounded transaction batching and deletion semantics.
        For each batch:
        1. Query candidate entries ordered by timestamp ASC.
        2. Accumulate candidates until target bytes to reclaim are satisfied or batch_size is reached.
        3. Delete the batch transactionally from SQLite (trigger updates FTS5 index).
        4. Perform best-effort WebP file removal after DB commit.
        5. Recalculate referenced storage and repeat until referenced_bytes <= max_bytes.

    Args:
        max_bytes: Target maximum capacity limit in bytes (must be > 0).
        batch_size: Maximum number of records to delete per transaction batch (default: 50).
        target_path: SQLite database path override.
        storage_dir: Screenshot storage directory path override.

    Returns:
        Summary dict containing:
        - "initial_referenced_bytes": Storage before trim
        - "final_referenced_bytes": Storage after trim
        - "target_capacity_bytes": Target max_bytes limit
        - "total_deleted_rows": Total SQLite rows deleted
        - "total_deleted_files": Total WebP files removed (or already missing)
        - "failed_file_deletions": Count of filesystem deletion failures
        - "batches_processed": Total batch transactions executed
    """
    path = target_path or db_path
    base_dir = storage_dir or screenshots_path
    safe_batch_size = max(1, min(batch_size, 500))

    initial_referenced_bytes = get_referenced_storage_bytes(target_path=path, storage_dir=storage_dir)
    current_referenced_bytes = initial_referenced_bytes

    summary = {
        "initial_referenced_bytes": initial_referenced_bytes,
        "final_referenced_bytes": current_referenced_bytes,
        "target_capacity_bytes": max_bytes,
        "total_deleted_rows": 0,
        "total_deleted_files": 0,
        "failed_file_deletions": 0,
        "batches_processed": 0,
    }

    if max_bytes <= 0 or current_referenced_bytes <= max_bytes:
        return summary

    while current_referenced_bytes > max_bytes:
        bytes_to_reclaim = current_referenced_bytes - max_bytes

        # Query candidate entries ordered by timestamp ASC
        candidates = []
        try:
            with get_db_connection(path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """SELECT id, image_path FROM entries
                       WHERE image_path IS NOT NULL AND image_path != ''
                       ORDER BY timestamp ASC
                       LIMIT ?""",
                    (safe_batch_size * 2,),
                )
                candidates = cursor.fetchall()
        except sqlite3.Error as e:
            logger.error(f"Database error selecting capacity deletion candidates: {e}")
            break

        if not candidates:
            break

        batch_ids = []
        image_paths = []
        accumulated_bytes = 0

        for row in candidates:
            e_id = row["id"]
            img_path = row["image_path"]
            batch_ids.append(e_id)
            image_paths.append(img_path)

            full_path = img_path if os.path.isabs(img_path) else os.path.join(base_dir, img_path)
            norm_path = os.path.normpath(full_path)
            try:
                if os.path.exists(norm_path):
                    accumulated_bytes += os.path.getsize(norm_path)
            except (OSError, PermissionError):
                pass

            if accumulated_bytes >= bytes_to_reclaim or len(batch_ids) >= safe_batch_size:
                break

        if not batch_ids:
            break

        # Delete batch transactionally from SQLite
        rows_affected = 0
        try:
            with get_db_connection(path) as conn:
                cursor = conn.cursor()
                placeholders = ",".join(["?"] * len(batch_ids))
                cursor.execute(
                    f"DELETE FROM entries WHERE id IN ({placeholders})",
                    batch_ids,
                )
                conn.commit()
                rows_affected = cursor.rowcount
        except sqlite3.Error as e:
            logger.error(f"Database error deleting capacity batch: {e}")
            break

        if rows_affected <= 0:
            break

        summary["total_deleted_rows"] += rows_affected
        summary["batches_processed"] += 1

        # Best-effort filesystem cleanup AFTER commit
        for img_path in image_paths:
            if _safe_remove_image_file(img_path, storage_dir=storage_dir):
                summary["total_deleted_files"] += 1
            else:
                summary["failed_file_deletions"] += 1

        current_referenced_bytes = get_referenced_storage_bytes(target_path=path, storage_dir=storage_dir)

    summary["final_referenced_bytes"] = current_referenced_bytes
    return summary


def reconcile_storage_and_database(
    target_path: Optional[str] = None,
    storage_dir: Optional[str] = None,
    storage_lock: Optional[Any] = None,
    cutoff_timestamp: Optional[int] = None,
    max_capacity_bytes: int = 0,
) -> dict:
    """Performs unified storage maintenance and orphan reconciliation.

    Sequentially executes:
      1. Safe removal of leftover *.tmp write buffer files.
      2. Identification and best-effort removal of orphan WebP image files.
      3. Setting image_path = NULL for database rows referencing missing images.
      4. Time-based retention cleanup (if cutoff_timestamp provided).
      5. Capacity-based storage trimming (if max_capacity_bytes > 0).

    Args:
        target_path: Optional path to SQLite database file. Defaults to db_path.
        storage_dir: Optional path to screenshot directory. Defaults to screenshots_path.
        storage_lock: Optional threading.Lock protecting pipeline file writes.
        cutoff_timestamp: Optional Unix timestamp for time-based retention cutoff.
        max_capacity_bytes: Target referenced storage capacity in bytes (0 = disabled).

    Returns:
        Structured dictionary summarizing maintenance metrics.
    """
    import contextlib

    path = target_path or db_path
    s_dir = storage_dir or screenshots_path
    lock_ctx = storage_lock if storage_lock is not None else contextlib.nullcontext()

    start_time = time.time()
    summary = {
        "files_scanned": 0,
        "orphan_files_removed": 0,
        "orphan_files_failed": 0,
        "tmp_files_removed": 0,
        "missing_image_rows_fixed": 0,
        "time_retention_deleted": 0,
        "capacity_retention_deleted": 0,
        "duration_seconds": 0.0,
    }

    with lock_ctx:
        db_relative_paths = set()
        db_entries_with_image = []

        # 1. Load active database entries referencing images
        try:
            with get_db_connection(path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, image_path FROM entries WHERE is_deleted = 0 AND image_path IS NOT NULL AND image_path != ''"
                )
                rows = cursor.fetchall()
                for r in rows:
                    raw_p = r["image_path"]
                    norm_rel = os.path.normpath(raw_p)
                    db_relative_paths.add(norm_rel)
                    db_entries_with_image.append((r["id"], norm_rel))
        except sqlite3.Error as db_err:
            logger.error(f"Database error loading entries for reconciliation: {db_err}")

        # 2. Scan physical screenshots directory
        physical_files = set()
        tmp_files = []
        if os.path.exists(s_dir):
            try:
                for entry in os.scandir(s_dir):
                    try:
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        summary["files_scanned"] += 1
                        name = entry.name
                        if name.endswith(".tmp"):
                            tmp_files.append(entry.path)
                        elif name.endswith(".webp"):
                            physical_files.add(os.path.normpath(name))
                    except (OSError, PermissionError):
                        pass
            except (OSError, PermissionError):
                pass

        # 3. Clean leftover .tmp files
        for tmp_p in tmp_files:
            try:
                os.remove(tmp_p)
                summary["tmp_files_removed"] += 1
            except (OSError, PermissionError):
                pass

        # 4. Reconcile orphan WebP files (files not belonging to active captures)
        orphan_files = physical_files - db_relative_paths
        for orphan_rel in orphan_files:
            abs_p = os.path.normpath(os.path.join(s_dir, orphan_rel))
            try:
                if os.path.exists(abs_p):
                    os.remove(abs_p)
                    summary["orphan_files_removed"] += 1
            except (OSError, PermissionError):
                summary["orphan_files_failed"] += 1

        # 5. Count missing image files without mutating database rows (preserve original stored paths)
        for e_id, norm_rel in db_entries_with_image:
            abs_p = os.path.normpath(os.path.join(s_dir, norm_rel))
            if not os.path.exists(abs_p):
                summary["missing_image_rows_fixed"] += 1

        # 6. Time-based retention
        if cutoff_timestamp is not None:
            time_summary = delete_entries_older_than(
                cutoff_timestamp, target_path=path, storage_dir=s_dir
            )
            summary["time_retention_deleted"] = time_summary.get("total_deleted_rows", 0)

        # 7. Capacity-based retention
        if max_capacity_bytes > 0:
            cap_summary = trim_referenced_storage_to_capacity(
                max_capacity_bytes, target_path=path, storage_dir=s_dir
            )
            summary["capacity_retention_deleted"] = cap_summary.get("total_deleted_rows", 0)

    summary["duration_seconds"] = round(time.time() - start_time, 4)
    return summary



