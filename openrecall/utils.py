"""General utility functions and legacy platform delegate wrappers."""

import datetime
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
