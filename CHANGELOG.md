# Changelog

This changelog records notable user-facing changes, privacy improvements, performance optimizations, and platform additions in this independent OpenRecall overhaul repository.

---

## Lineage & Project Attribution

This repository is an extensive, independent overhaul of the original [OpenRecall project](https://github.com/openrecall/openrecall), released under the GNU Affero General Public License v3.0 (AGPLv3).

The original OpenRecall authors are not affiliated with, do not maintain, and do not endorse this overhaul repository.

---

## [0.9.0] — Unreleased (Upcoming Overhaul Release)

Version 0.9.0 is a major overhaul of OpenRecall focused on local privacy, fast performance, cross-platform stability, built-in text recognition (OCR), persistent startup settings, and safe database migration.

### Core Architecture & Performance
- **Lighter & Faster Local Operation**: Removed heavy machine learning dependencies (such as PyTorch and Transformers) to make OpenRecall significantly lighter, faster to launch, and easier to run on standard desktop systems.
- **Low-Resource Design**: Redesigned the application for low-resource computers. **4 GB RAM is the practical recommended baseline** for running OpenRecall comfortably alongside your daily applications.
- **Fast Local Search & Storage**: Replaced complex database components with a local SQLite database that powers instant full-text search across your captured screen history.
- **Smarter Screen-Change Detection**: Added efficient screen-change detection that automatically skips duplicate or static screens, reducing background CPU usage and saving disk space.
- **Automatic Storage Limits**: Introduced an optional storage limit option (`--max-storage-gb`) that automatically removes the oldest screenshots when your chosen disk space threshold is reached.
- **100% Offline & Local**: All screen capture, text extraction, search indexing, and web browsing run entirely on your own computer. OpenRecall requires zero cloud services, zero account sign-ups, and zero internet connectivity.

### Privacy & Local Data Control
- **100% Local Execution**: Your screen captures and extracted text never leave your computer.
- **Local-Only Web Interface**: The local web interface binds exclusively to your own computer (`127.0.0.1:8082`) and is never accessible over your local network (LAN) or the internet.
- **Instant Pause & Resume Controls**: Added quick pause and resume controls directly in the web header and local API endpoints (`/api/pause`, `/api/resume`), allowing you to pause capture instantly whenever needed.
- **Single-Instance Protection**: Added automatic background locking to prevent multiple copies of OpenRecall from running at the same time.
- **No Tracking or Analytics**: OpenRecall includes zero telemetry, zero analytics tracking, and zero remote update checks.

### Local Text Recognition (OCR)
- **Built-in RapidOCR Engine**: Integrated **RapidOCR** (using ONNX Runtime CPU inference) as the primary text extraction engine. It runs locally out of the box with zero external software installation required.
- **Tesseract Fallback Option**: Retained support for system **Tesseract** as an alternative text extraction engine if installed on your system.
- **Automatic Fallback Mode**: The default `--ocr-engine auto` setting tries RapidOCR first, falls back to Tesseract if available, and uses a safe built-in fallback if neither is available.
- **Smart Image Scaling**: Large desktop screenshots are automatically scaled to a 1440-pixel maximum dimension before text extraction to keep small text readable while avoiding CPU timeouts.
- **Configurable Settings**: Added options to choose your preferred OCR engine (`--ocr-engine`), set CPU thread usage (`--ocr-threads`), or turn off text extraction completely (`--disable-ocr`).

### Timeline, Search & Gallery
- **Interactive Time-Travel Timeline**: Added a smooth timeline slider that lets you scrub back through your visual desktop history in real time.
- **Gallery View**: Introduced Gallery Mode (`/?mode=gallery`), presenting your captured history as a grid of responsive screenshot cards.
- **Fast Search & Word Highlighting**: Search instantly across extracted screen text, application names, and window titles, with matching words highlighted in search results and screenshot detail views.
- **Resilient Screenshot Display**: Added clean fallback handling so database entries remain readable even if an individual screenshot image file is missing from disk.

### Cross-Platform Support
- **Windows 10 x64 (Physically Validated)**: Tested and physically validated on Windows 10 x64. Features Win32 screen capture, window title tracking, and Windows Registry autostart.
- **Ubuntu Wayland (Physically Validated)**: Tested and physically validated on Ubuntu Linux under Wayland. Features native desktop portal streaming capture and XDG Autostart.
- **Linux Mint X11 (Physically Validated)**: Tested and physically validated on Linux Mint under X11. Features multi-monitor X11 screen capture, window title tracking, and XDG Autostart.
- **Windows 11 (Compatibility Reviewed)**: Code and package compatibility reviewed; physical runtime validation not performed.
- **macOS (Compatibility Reviewed)**: Code and package compatibility reviewed for Quartz screen capture and LaunchAgent autostart; physical runtime validation not performed.

### Background Operation & System Autostart
- **Quiet Background Mode**: OpenRecall can run silently in the background (`--background`) without keeping a terminal window open.
- **Easy Shutdown Command**: Added `--stop` to safely stop any running background instance of OpenRecall.
- **Persistent Autostart Settings**: Enabling system autostart (`--enable-autostart`) saves your custom startup options (such as custom storage paths, OCR preferences, and storage limits) so they persist automatically across system reboots.

### Legacy Database Migration
- **Explicit Database Migration**: Added `openrecall --migrate` to safely upgrade older OpenRecall databases (from earlier release versions) to the current database format.
- **Read-Only Storage Audit**: Added `openrecall --audit-legacy-storage` to let you inspect your older database and screenshot files before making any changes.
- **Automatic Backup Copy**: Creates a digital fingerprint-verified backup copy (`recall-legacy.db`) using a SHA-256 checksum before updating your database.
- **Zero Screenshot Changes**: Your existing screenshot files are **never** moved, renamed, copied, or deleted during migration.
- **Startup Protection**: OpenRecall halts normal startup when an older database is detected, ensuring you must explicitly choose to run the migration command.
- **Safe to Re-run**: Running `--migrate` on a database that is already up to date is completely safe and makes no changes.

### Security Review
- **Code Audit Results**: Completed a thorough security review of all production code with **zero high-priority (P0, P1, or P2) security findings identified**.
- **Security Defenses**: Audited production code for safer database queries, protection against unsafe web content, safe path handling, safe system command execution, local-only network access, and clean dependency management.

### Packaging & Documentation
- **Clean Installation Packages**: Improved Python packaging to generate clean standalone release packages (`.whl` and `.tar.gz`).
- **User & Developer Documentation**: Created a detailed Developer Guide ([DEVELOPMENT.md](DEVELOPMENT.md)), Project Roadmap ([ROADMAP.md](ROADMAP.md)), Legacy Migration Guide ([docs/migration.md](docs/migration.md)), and Encrypted Storage Guide ([docs/encryption.md](docs/encryption.md)).

---

## [0.8.0 and earlier]

Versions 0.8.0 and earlier refer to the original upstream OpenRecall project releases created by the original OpenRecall contributors.

For historical commit logs and documentation for version 0.8.0 and earlier, please refer to the original [upstream OpenRecall repository](https://github.com/openrecall/openrecall).
