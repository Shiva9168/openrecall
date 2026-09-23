"""General utility functions, single-instance process lock, and platform delegate wrappers."""

import datetime
import os
import sys
import urllib.request
from typing import Optional

from openrecall.platform import (
    CaptureMetadata,
    MSSScreenCaptureProvider,
    get_platform_provider,
)


def human_readable_time(timestamp: int) -> str:
    """Converts a Unix timestamp into a human-readable relative time string."""
    now = datetime.datetime.now()
    dt_object = datetime.datetime.fromtimestamp(timestamp)
    diff = now - dt_object
    if diff.days > 0:
        return f"{diff.days} days ago"
    elif diff.seconds < 60:
        return f"{diff.seconds} seconds ago"
    elif diff.seconds < 3600:
        return f"{diff.seconds // 60} minutes ago"
    else:
        return f"{diff.seconds // 3600} hours ago"


def timestamp_to_human_readable(timestamp: int) -> str:
    """Converts a Unix timestamp into a human-readable date/time string."""
    try:
        dt_object = datetime.datetime.fromtimestamp(timestamp)
        return dt_object.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ""


def get_active_app_name() -> str:
    """Gets active application name for current platform (returns empty string if unavailable)."""
    provider = get_platform_provider()
    return provider.get_active_app_name() or ""


def get_active_window_title() -> str:
    """Gets active window title for current platform (returns empty string if unavailable)."""
    provider = get_platform_provider()
    return provider.get_active_window_title() or ""


def is_user_active() -> bool:
    """Checks if the user is active on the current platform."""
    provider = get_platform_provider()
    return provider.is_user_active()


class SingleInstanceLock:
    """Cross-platform single-instance process lock using OS kernel file locking."""

    def __init__(self, lock_file_path: str):
        self.lock_file_path = os.path.abspath(lock_file_path)
        self.file_handle = None
        self._is_locked = False

    def acquire(self) -> bool:
        """Attempts to acquire exclusive lock. Returns True if acquired, False if another instance holds it."""
        try:
            os.makedirs(os.path.dirname(self.lock_file_path), exist_ok=True)
            self.file_handle = open(self.lock_file_path, "a+")

            if sys.platform == "win32":
                import msvcrt
                try:
                    self.file_handle.seek(0)
                    msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_NBLCK, 1)
                    self._is_locked = True
                    return True
                except (IOError, OSError):
                    return False
            else:
                import fcntl
                try:
                    fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    self._is_locked = True
                    return True
                except (IOError, OSError):
                    return False
        except Exception:
            return False

    def release(self):
        """Releases the lock and closes the lock file."""
        if not self._is_locked or not self.file_handle:
            return
        try:
            if sys.platform == "win32":
                import msvcrt
                try:
                    self.file_handle.seek(0)
                    msvcrt.locking(self.file_handle.fileno(), msvcrt.LK_UNLCK, 1)
                except Exception:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(self.file_handle.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            self.file_handle.close()
        except Exception:
            pass
        finally:
            self.file_handle = None
            self._is_locked = False


def check_existing_instance_running(port: int = 8082, timeout: float = 1.0) -> bool:
    """Checks if an existing OpenRecall instance is responding to /api/health."""
    try:
        url = f"http://127.0.0.1:{port}/api/health"
        req = urllib.request.Request(url, headers={"User-Agent": "OpenRecall-InstanceCheck"})
        with urllib.request.urlopen(req, timeout=timeout) as response:
            if response.status == 200:
                return True
    except Exception:
        pass
    return False
