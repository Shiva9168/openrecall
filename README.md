```
   ____                   ____                  ____   
  / __ \____  ___  ____  / __ \___  _________ _/ / /   
 / / / / __ \/ _ \/ __ \/ /_/ / _ \/ ___/ __ `/ / /    
/ /_/ / /_/ /  __/ / / / _, _/  __/ /__/ /_/ / / /     
\____/ .___/\___/_/ /_/_/ |_|\___/\___/\__,_/_/_/      
    /_/                                                                                                                         
```

# OpenRecall — Take Control of Your Digital Memory

OpenRecall is a local, privacy-first desktop memory assistant. It captures desktop screen activity locally, analyzes text and images using built-in OCR engines, indexes screen history into a local SQLite database with full-text search (FTS5), and provides an interactive time-travel timeline and gallery grid via a local Flask Web UI.

> **Project Lineage & Attribution**: This repository is an extensive, independent overhaul of the original [OpenRecall project](https://github.com/openrecall/openrecall) originally created by the OpenRecall contributors. It is released under the GNU Affero General Public License v3.0 (AGPLv3). The original OpenRecall authors are not affiliated with, do not maintain, and do not endorse this overhaul repository.

---

## Free & Open Source

OpenRecall is **100% free to use and open source**. The full source code is publicly available under the GNU Affero General Public License v3.0 (AGPLv3).

---

## What Does It Do?

OpenRecall captures periodic snapshots of your desktop activity. Extracted text and image metadata are indexed locally, allowing you to:
- **Search Past Activity**: Instantly locate past documents, code snippets, web pages, or conversations using full-text search.
- **Scrub Through Time**: Interactively navigate back through visual desktop history using a debounced timeline slider or paginated gallery grid.
- **Control Privacy**: Pause and resume capture instantly via UI buttons or local REST API endpoints (`/api/pause`, `/api/resume`).

---

## Overhaul Highlights

- **100% Local & Offline**: All screen capture, frame change detection, OCR, SQLite indexing, and Web UI serving run locally on your machine. Zero cloud APIs, zero telemetry, zero analytics, and zero external network calls.
- **Built-in Local OCR**: Uses **RapidOCR** (ONNX Runtime CPU inference) as the primary OCR engine with zero external system dependencies. Supports system **Tesseract** as an alternative fallback.
- **Fast Full-Text Search (FTS5)**: Search indexed OCR text and active window titles instantly via SQLite FTS5.
- **Timeline & Gallery Views**: Scrub through history with a 100ms debounced slider or view paginated screenshot cards in Gallery Mode (`/?mode=gallery`).
- **Storage Cap Management**: Set storage limits (e.g. `--max-storage-gb 5.0`) to automatically trim the oldest screenshots when storage thresholds are reached.
- **Persistent System Autostart**: Enable automatic system startup on boot (`--enable-autostart`) with automatic serialization of explicit runtime flags across Windows Registry, Linux XDG Autostart, and macOS LaunchAgent.
- **Legacy Database Migration**: Automated schema v0 → v3 migration tooling (`openrecall --migrate`, `--audit-legacy-storage`) with SHA-256 preservation copies.

---

## Platform Support Matrix

OpenRecall uses modular platform abstraction providers across major desktop environments:

| Platform / Display Server | Status | Details |
| :--- | :--- | :--- |
| **Windows 10 x64** | **Physically Validated** | Win32 GDI display capture, process title tracking via `pywin32`, Registry autostart. |
| **Ubuntu Wayland** | **Physically Validated** | Native XDG Desktop Portal ScreenCast & PipeWire continuous streaming capture provider (`WaylandScreenCastCaptureProvider`), persistent session restoration tokens, XDG Autostart. Title tracking degrades gracefully under Wayland security boundaries. |
| **Linux Mint X11** | **Physically Validated** | Multi-monitor X11 capture via `mss`, window title tracking via `xprop`, XDG Autostart. |
| **Windows 11** | **Compatibility Reviewed** | Code & packaging compatibility reviewed; physical runtime validation not performed. |
| **macOS (Quartz)** | **Compatibility Reviewed** | Quartz screen capture via `mss`, window tracking via `pyobjc`, LaunchAgent autostart. Code & packaging compatibility reviewed; physical runtime validation not performed. |

---

## Hardware & Resource Guidelines

OpenRecall is designed and optimized with low-resource, CPU-only systems in mind.
- **4 GB RAM is a practical recommended baseline** for running the application comfortably alongside everyday desktop applications.
- **Note**: 4 GB RAM is a practical guideline, not a hard minimum requirement or a performance guarantee. Actual resource consumption depends on operating system overhead, display resolution, capture frequency, OCR engine selection, and active workload.

---

## Installation & Setup

### Prerequisites
- **Python**: 3.9 or higher
- **Supported OS**: Windows 10/11, Linux (X11 or Wayland), or macOS

### Release Installation

To install OpenRecall from the built release package (`.whl`):
```bash
python3 -m pip install openrecall-0.9.0-py3-none-any.whl
```

### Development Setup

To clone the repository for development or build from source:
```bash
git clone https://github.com/Shiva9168/openrecall.git
cd openrecall
uv pip install -e ".[dev]"
```
For detailed developer instructions, see [DEVELOPMENT.md](DEVELOPMENT.md).

### Uninstall

To uninstall OpenRecall:
```bash
python3 -m pip uninstall openrecall
```

> **Note on Data Privacy**: Uninstalling the Python package removes the application binaries, but does **not** automatically delete your captured screen history, SQLite database, or screenshots storage directory (`recall.db`, appdata folder). Captured data remains safely on disk and must be manually removed by the user if deletion is desired.

---

## Quick Start

To launch OpenRecall:
```bash
openrecall
```
or:
```bash
python3 -m openrecall.app
```

Open your browser to [http://localhost:8082](http://localhost:8082) to access the local Web UI. All requests bind strictly to `127.0.0.1:8082`.

---

## Command-Line Reference

OpenRecall provides comprehensive command-line configuration options:

| Flag | Description | Default |
| :--- | :--- | :--- |
| `--storage-path PATH` | Custom directory path for screenshots and database (`recall.db`). | OS AppData path |
| `--ocr-engine {auto,rapidocr,tesseract}` | Select local OCR engine (`auto` tries RapidOCR, then Tesseract, then Fallback). | `auto` |
| `--ocr-threads N` | Number of CPU threads for RapidOCR inference. | Auto |
| `--disable-ocr` | Disable local OCR text extraction completely. | `False` |
| `--max-storage-gb GB` | Storage cap limit in Gigabytes (e.g. `5.0`). Automatically trims oldest screenshots. | `None` (0 / unlimited) |
| `--primary-monitor-only` | Record primary display only (rather than all connected displays). | `False` |
| `--enable-autostart` | Enable automatic system startup on boot (persists explicit runtime options). | `False` |
| `--disable-autostart` | Disable automatic system startup on boot. | `False` |
| `--background` | Run silently in the background without a console window. | `False` |
| `--stop` | Stop running background OpenRecall instance gracefully. | `False` |
| `--migrate` | Execute explicit schema v0 → v3 database migration. | `False` |
| `--audit-legacy-storage` | Perform a read-only audit of legacy storage directory. | `False` |

---

## Local OCR Architecture

- **Primary Engine (RapidOCR)**: Integrated via `rapidocr` package using ONNX Runtime CPU inference. Operates locally with zero system binary dependencies. Images are capped at a 1440px maximum dimension before OCR processing.
- **Alternative Engine (Tesseract)**: Secondary fallback engine used when `tesseract` is installed on system `PATH` and requested.
- **Engine Selection (`--ocr-engine auto`)**: Tries RapidOCR first; if unavailable, tries Tesseract; if unavailable, falls back gracefully to a non-crashing fallback provider. Auto mode is availability/fallback based (it does not benchmark machine resources).
- **CPU Thread Tuning**: Use `--ocr-threads N` to adjust CPU thread allocation for RapidOCR.
- **Graceful Fallback**: Passing `--disable-ocr` skips OCR text extraction while screen capture continues recording visual history.

---

## Autostart Integration & Persistent Options

OpenRecall integrates natively with platform autostart mechanisms:
- **Windows**: Registry Key (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OpenRecall`)
- **Linux**: XDG Autostart File (`~/.config/autostart/openrecall.desktop`)
- **macOS**: LaunchAgent Plist (`~/Library/LaunchAgents/com.openrecall.app.plist`)

When enabling autostart with custom flags:
```bash
openrecall --enable-autostart --storage-path /custom/path --ocr-engine rapidocr --ocr-threads 2
```
Explicit runtime options (`--storage-path`, `--ocr-engine`, `--ocr-threads`, `--disable-ocr`, `--max-storage-gb`, `--primary-monitor-only`) are persisted into the system startup configuration. Re-running `--enable-autostart` replaces previous settings; running `--enable-autostart` without custom options resets autostart to default parameters. The autostart management commands themselves are not persisted; only the supported runtime options listed above are stored in the platform's startup configuration.

---

## Legacy Database Migration

If you are upgrading from an older OpenRecall release (schema v0), OpenRecall detects legacy database structures on startup and stops normal startup until you explicitly run the migration command.

Before migrating, it is strongly recommended that you manually back up your legacy database/storage directory. OpenRecall also creates a SHA-256-verified preservation copy as part of the migration process.

```bash
# Perform a read-only audit of legacy storage:
openrecall --audit-legacy-storage --storage-path /path/to/legacy_dir

# Execute explicit schema migration (v0 -> v3):
openrecall --migrate --storage-path /path/to/legacy_dir
```

- **Preservation Copy**: Generates a SHA-256 verified backup copy (`recall_legacy_v0_<timestamp>.db`) before modifying data.
- **Idempotent**: Running `--migrate` on a current v3 database safely outputs a no-op message.

---

## Privacy & Security Guarantees

- **100% Local Execution**: All processing, image downsampling, OCR text extraction, SQLite search, and Web UI serving occur on your device.
- **Localhost Binding**: The web server binds strictly to `127.0.0.1:8082` (not exposed to LAN or external networks).
- **Zero Telemetry**: No tracking, no analytics, no external update checks, and no remote cloud connections.
- **Pause & Resume Controls**: Instantly pause or resume background screen capture via Web UI buttons or local REST POST requests (`/api/pause`, `/api/resume`).

---

## Project Status & Documentation

This repository represents the **v0.9.0 overhaul release-preparation stage** of OpenRecall.

For additional documentation:
- [DEVELOPMENT.md](DEVELOPMENT.md) — Contributor guide, codebase architecture, and testing procedures.
- [ROADMAP.md](ROADMAP.md) — Major development phase history and project roadmap.
- [docs/encryption.md](docs/encryption.md) — Guide for running OpenRecall on encrypted storage (BitLocker, LUKS, macOS encrypted disk image).

---

## Contributing

Contributions are welcome! Please read [DEVELOPMENT.md](DEVELOPMENT.md) for details on setting up your development workspace, running tests (`uv run pytest tests/ -q`), and submitting pull requests.

---

## License & Attribution

OpenRecall is licensed under the [GNU Affero General Public License v3.0 (AGPLv3)](LICENSE).

This repository is an extensive overhaul of the original [OpenRecall codebase](https://github.com/openrecall/openrecall), copyright © original OpenRecall contributors. We gratefully acknowledge the foundational work of the original OpenRecall project.
