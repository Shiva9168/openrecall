import argparse
import os
import sys

# Centralized capture pipeline constants
DOWNSAMPLE_SIZE = (128, 128)
FRAME_CHANGE_THRESHOLD = 0.001  # 0.1% Mean Absolute Difference threshold
CAPTURE_QUEUE_MAX_SIZE = 10     # Strict bounded queue size for 2 GB RAM target
CAPTURE_INTERVAL_SECONDS = 3.0

# Centralized local OCR pipeline constants
OCR_ENGINE = "auto"
OCR_MAX_DIMENSION = 1920        # Bounded max image dimension for OCR to limit CPU/RAM
OCR_LANG = "eng"


parser = argparse.ArgumentParser(description="OpenRecall")

parser.add_argument(
    "--storage-path",
    default=None,
    help="Path to store the screenshots and database",
)

parser.add_argument(
    "--primary-monitor-only",
    action="store_true",
    help="Only record the primary monitor",
    default=False,
)

# Parse args safely with fallback when imported in test runners
try:
    args, _ = parser.parse_known_args()
except Exception:
    args = parser.parse_args([])


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
    if not os.path.exists(path):
        os.makedirs(path)
    return path


if args and args.storage_path:
    appdata_folder = args.storage_path
    screenshots_path = os.path.join(appdata_folder, "screenshots")
    db_path = os.path.join(appdata_folder, "recall.db")
else:
    appdata_folder = get_appdata_folder()
    db_path = os.path.join(appdata_folder, "recall.db")
    screenshots_path = os.path.join(appdata_folder, "screenshots")

if not os.path.exists(screenshots_path):
    try:
        os.makedirs(screenshots_path)
    except Exception:
        pass
