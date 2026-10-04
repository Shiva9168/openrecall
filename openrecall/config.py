import argparse
import os
import sys
from typing import Optional


class SafeStreamWrapper:
    """Safe stream wrapper that catches write/flush errors on GUI/background processes."""

    def __init__(self, target_stream=None):
        self.target_stream = target_stream

    def write(self, text):
        if self.target_stream is not None:
            try:
                return self.target_stream.write(text)
            except Exception:
                pass

    def flush(self):
        if self.target_stream is not None:
            try:
                return self.target_stream.flush()
            except Exception:
                pass

    def isatty(self):
        if self.target_stream is not None:
            try:
                return self.target_stream.isatty()
            except Exception:
                pass
        return False


def ensure_valid_standard_streams():
    """Guarantees sys.stdout and sys.stderr are safe, non-crashing stream objects."""
    if getattr(sys, "stdout", None) is None or not hasattr(sys.stdout, "write"):
        sys.stdout = SafeStreamWrapper(None)
    else:
        sys.stdout = SafeStreamWrapper(sys.stdout)

    if getattr(sys, "stderr", None) is None or not hasattr(sys.stderr, "write"):
        sys.stderr = SafeStreamWrapper(None)
    else:
        sys.stderr = SafeStreamWrapper(sys.stderr)


ensure_valid_standard_streams()

# Centralized capture pipeline constants
DOWNSAMPLE_SIZE = (128, 128)
FRAME_CHANGE_THRESHOLD = 0.001  # 0.1% Mean Absolute Difference threshold
CAPTURE_QUEUE_MAX_SIZE = 10     # Strict bounded queue size for 2 GB RAM target
CAPTURE_INTERVAL_SECONDS = 10.0

# Centralized local OCR pipeline constants
OCR_ENABLED: bool = True
OCR_ENGINE: str = "auto"
OCR_MAX_DIMENSION: int = 1440        # Bounded max image dimension for OCR to preserve small text font legibility while avoiding timeouts
OCR_LANG: str = "eng"
OCR_TIMEOUT_SECONDS: float = 10.0

# RapidOCR benchmarked default constants
RAPIDOCR_DET_LIMIT_TYPE: str = "max"
RAPIDOCR_DET_LIMIT_SIDE_LEN: int = 736
RAPIDOCR_USE_CLS: bool = True
RAPIDOCR_THREADS: Optional[int] = None  # None = automatic/native engine default (2 threads is low-resource option)

# Centralized storage capacity configuration defaults
MAX_STORAGE_BYTES_DEFAULT: int = 0  # 0 = disabled / unlimited

# Centralized storage maintenance defaults
MAINTENANCE_ENABLED: bool = True
MAINTENANCE_INTERVAL_SECONDS: int = 86400  # Conservative 24-hour default


def parse_max_storage_gb(gb_val: Optional[float]) -> int:
    """Parses a capacity limit in GB to integer bytes.

    Args:
        gb_val: Capacity limit in decimal Gigabytes (e.g. 5.0). None or 0.0 means disabled.

    Returns:
        Target capacity limit in integer bytes (0 = disabled).

    Raises:
        ValueError: If gb_val is negative.
    """
    if gb_val is None or gb_val == 0.0:
        return 0
    if gb_val < 0.0:
        raise ValueError("Maximum storage capacity cannot be negative.")
    return int(gb_val * 1_000_000_000)


parser = argparse.ArgumentParser(description="OpenRecall")

parser.add_argument(
    "--storage-path",
    default=None,
    help="Path to store the screenshots and database",
)

parser.add_argument(
    "--migrate",
    action="store_true",
    default=False,
    help="Migrate a legacy OpenRecall database to the current schema",
)

parser.add_argument(
    "--audit-legacy-storage",
    action="store_true",
    default=False,
    help="Perform a read-only audit of legacy database and screenshot storage",
)

parser.add_argument(
    "--primary-monitor-only",
    action="store_true",
    help="Only record the primary monitor",
    default=False,
)

parser.add_argument(
    "--max-storage-gb",
    type=float,
    default=None,
    help="Maximum referenced screenshot storage limit in Gigabytes (e.g. 5.0). Default is 0 (disabled).",
)

parser.add_argument(
    "--enable-autostart",
    action="store_true",
    default=False,
    help="Enable automatic startup on system boot",
)

parser.add_argument(
    "--disable-autostart",
    action="store_true",
    default=False,
    help="Disable automatic startup on system boot",
)

parser.add_argument(
    "--background",
    action="store_true",
    default=False,
    help="Run OpenRecall silently in the background without a visible console window",
)

parser.add_argument(
    "--stop",
    action="store_true",
    default=False,
    help="Stop running background OpenRecall instance gracefully",
)

parser.add_argument(
    "--ocr-engine",
    choices=["auto", "rapidocr", "tesseract"],
    default="auto",
    help="Select local OCR engine (auto, rapidocr, tesseract)",
)

parser.add_argument(
    "--ocr-threads",
    type=int,
    default=None,
    help="Number of CPU threads for RapidOCR inference (e.g. 2 for low-resource mode, default: auto)",
)

parser.add_argument(
    "--disable-ocr",
    action="store_true",
    default=False,
    help="Disable local OCR text extraction completely",
)

# Parse args safely with fallback when imported in test runners
try:
    args, _ = parser.parse_known_args()
except Exception:
    args = parser.parse_args([])

if getattr(args, "disable_ocr", False):
    OCR_ENABLED = False

if getattr(args, "ocr_engine", None):
    OCR_ENGINE = args.ocr_engine

if getattr(args, "ocr_threads", None) is not None:
    if args.ocr_threads <= 0:
        raise ValueError("--ocr-threads must be a positive integer (> 0).")
    RAPIDOCR_THREADS = args.ocr_threads

is_bg_entry = False
if sys.argv and sys.argv[0]:
    base_name = os.path.basename(sys.argv[0]).lower()
    if "openrecall-bg" in base_name or "openrecall_bg" in base_name:
        is_bg_entry = True

if is_bg_entry and hasattr(args, "background"):
    args.background = True

if sys.platform == "win32" and (is_bg_entry or getattr(args, "background", False)):
    try:
        import ctypes
        ctypes.windll.kernel32.FreeConsole()
    except Exception:
        pass
    sys.stdout = SafeStreamWrapper(None)
    sys.stderr = SafeStreamWrapper(None)


def get_appdata_folder(app_name="openrecall"):
    if sys.platform == "win32":
        appdata = os.getenv("APPDATA")
        if not appdata:
            raise EnvironmentError("APPDATA environment variable is not set.")
        path = os.path.join(appdata, app_name)
    elif sys.platform == "darwin":
        home = os.path.expanduser("~")
        path = os.path.join(home, "Library", "Application Support", app_name)
    else:
        home = os.path.expanduser("~")
        path = os.path.join(home, ".local", "share", app_name)
    abs_path = os.path.abspath(os.path.normpath(path))
    if not os.path.exists(abs_path):
        os.makedirs(abs_path, exist_ok=True)
    return abs_path


if args and args.storage_path:
    appdata_folder = os.path.abspath(os.path.expanduser(os.path.normpath(args.storage_path)))
    screenshots_path = os.path.abspath(os.path.join(appdata_folder, "screenshots"))
    db_path = os.path.abspath(os.path.join(appdata_folder, "recall.db"))
else:
    appdata_folder = os.path.abspath(get_appdata_folder())
    db_path = os.path.abspath(os.path.join(appdata_folder, "recall.db"))
    screenshots_path = os.path.abspath(os.path.join(appdata_folder, "screenshots"))

if not os.path.exists(screenshots_path):
    try:
        os.makedirs(screenshots_path, exist_ok=True)
    except Exception:
        pass

try:
    from openrecall.utils import setup_logging
    setup_logging(appdata_folder)
except Exception:
    pass

