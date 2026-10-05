# OpenRecall Developer Guide

Welcome to the OpenRecall developer documentation. This guide provides a comprehensive overview of the codebase architecture, environment setup, frontend styling workflows, testing procedures, and contribution guidelines for developers working on OpenRecall.

---

## 1. Project Overview

OpenRecall is a privacy-first desktop capture and recall application. It captures screen activity locally, analyzes images and text using local OCR engines, indexes metadata into a local SQLite database with full-text search (FTS5), and presents an interactive timeline history via a local Flask Web UI.

### Core Design Principles:
- **Local & Offline by Design**: OpenRecall's application data processing, OCR, search, storage, and Web UI operate locally. The application does not require cloud APIs, telemetry, analytics, or remote services for its core functionality.
- **Low-Resource Engineering**: OpenRecall is developed with low-resource, CPU-only systems in mind. A system with **4 GB RAM is a practical recommended baseline**, although actual resource usage depends on the operating system, capture configuration, OCR engine, and desktop workload.
- **Modular Platform Abstraction**: Unified interface across Linux (Wayland & X11), Windows 10/11, and macOS.
- **Privacy Controls**: Native global capture pause/resume controls (`/api/pause`, `/api/resume`).

---

## 2. Development Requirements

### Environment Prerequisites
- **Python**: Python 3.9 or higher.
- **Package & Environment Manager**: `uv` (recommended) or standard `python3 -m venv` / `pip`.
- **Node.js & npm** *(Optional)*: Node.js (v18+) is required **only** if you are modifying frontend CSS source files (`input.css`) and rebuilding Tailwind CSS assets.
- **Operating Systems**: Linux (Ubuntu, Debian, Fedora, Mint), Windows 10/11, or macOS.
- **Storage**: Sufficient local disk space for virtual environments, test databases, builds, and screenshot data.

> **Note**: Runtime dependencies are declared in `pyproject.toml`. Node.js/npm is not required at runtime.

---

## 3. Installation & Development Setup

1. **Clone the Repository**:
   ```bash
   git clone https://github.com/Shiva9168/openrecall.git
   cd openrecall
   ```

2. **Create & Activate Virtual Environment**:
   Using `uv`:
   ```bash
   uv venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   ```

3. **Install Development Dependencies**:
   Install OpenRecall in editable mode along with development dependencies (`pytest`, `psutil`):
   ```bash
   uv pip install -e ".[dev]"
   ```

4. **Run OpenRecall in Development**:
   Launch the application directly using `uv` or Python:
   ```bash
   uv run openrecall
   # or
   python3 -m openrecall.app
   ```
   Open your browser to `http://localhost:8082` to interact with the local Web UI.

---

## 4. Repository Structure

```text
openrecall/
├── openrecall/              # Core application package
│   ├── app.py               # Flask Web UI, REST API routes, & CLI entry point
│   ├── config.py            # CLI argument parsing, constants, & logging setup
│   ├── database.py          # SQLite connection pooling & FTS5 search schema (v3)
│   ├── maintenance.py       # Database schema migration (v0 -> v3) & storage caps
│   ├── nlp.py               # Text normalization & FTS5 search query tokenizer
│   ├── ocr.py               # RapidOCR primary engine & Tesseract fallback lifecycle
│   ├── platform.py          # Platform abstraction (Linux X11/Wayland, Windows, macOS)
│   ├── privacy.py           # Capture pause/resume state management
│   ├── screenshot.py        # Screen capture, 128x128 downsampling, & MAD diffing
│   ├── utils.py             # Logging setup & GUI/background stream safety wrappers
│   └── static/              # Web UI static assets
│       └── css/
│           ├── input.css    # Tailwind CSS source directives
│           └── output.css   # Compiled CSS bundle (tracked in git & packaged)
├── tests/                   # Automated unit & integration test suite (pytest)
├── docs/                    # User & platform documentation (hardware, encryption, etc.)
├── pyproject.toml           # Project build metadata & tool configuration
├── setup.py                 # Setuptools fallback package configuration
├── DEVELOPMENT.md           # Developer & contributor documentation
└── ROADMAP.md               # High-level project phase history & roadmap
```

### Key Application Modules:
- [openrecall/app.py](openrecall/app.py): CLI entry point `main()`, Flask route handling (`/`, `/?mode=gallery`, `/capture/<id>`, `/search`, `/screenshot/<filename>`), REST controls (`/api/pause`, `/api/resume`, `/api/timeline/bounds`, `/api/timeline/at`), and inline HTML template rendering via `render_template_string()`.
- [openrecall/config.py](openrecall/config.py): Command-line argument parser (`ArgumentParser`) with custom `OpenRecallHelpFormatter`, storage path determination, and runtime flag constants.
- [openrecall/database.py](openrecall/database.py): SQLite database manager (`recall.db`), table initialization (`entries`), and full-text search virtual tables (`entries_fts`).
- [openrecall/maintenance.py](openrecall/maintenance.py): Legacy v0 → v3 schema migration routines (`openrecall --migrate`), SHA-256 preservation copy generation, and storage capacity trimming (`--max-storage-gb`).
- [openrecall/ocr.py](openrecall/ocr.py): Local OCR provider manager (`get_ocr_provider`). Uses **RapidOCR** (ONNX Runtime CPU inference) as the primary engine and system **Tesseract** as an optional fallback.
- [openrecall/platform.py](openrecall/platform.py): OS abstraction providers (`ScreenCaptureProvider`, `ActiveWindowProvider`, `StartupIntegrationProvider`) for Windows Win32/GDI, Linux X11/Wayland Portal ScreenCast, and macOS Quartz/LaunchAgent.
- [openrecall/screenshot.py](openrecall/screenshot.py): Screenshot capture pipeline, `128x128` downsampled grayscale thumbnail generation, and Mean Absolute Difference (MAD) frame-change detection.

---

## 5. System Architecture

```text
       Desktop Screen Capture (mss / Portal)
                         │
                         ▼
        Privacy Decision (Pause / Resume)
                         │
                         ▼
    Frame Change Detection (128x128 Downsampled MAD)
                         │
                         ▼
      Processing & OCR (RapidOCR / Tesseract)
                         │
                         ▼
   Local Storage & Index (SQLite v3 + FTS5 Search)
                         │
                         ▼
     Local Web UI & REST API (Flask @ 127.0.0.1:8082)
```

### Key Processing Flow:
1. **Screen Capture**: Periodically grabs desktop screenshots via `mss` (Windows GDI, Linux X11, macOS Quartz) or XDG Desktop Portal ScreenCast / PipeWire continuous streaming (Linux Wayland).
2. **Privacy Evaluation**: Evaluates global pause state (`/api/pause`) before queueing frames.
3. **Frame Change Detection**: Downsamples captured frames to `128x128` grayscale thumbnails and computes Mean Absolute Difference (MAD). Unchanged frames below threshold are discarded.
4. **OCR Analysis**: Extracts text locally using RapidOCR or Tesseract fallback.
5. **Local Storage**: Saves full-resolution WebP screenshots to `<storage-path>/screenshots/` and records metadata + extracted text in `<storage-path>/recall.db`.
6. **Web UI & REST API**: Serves timeline scrubbing, gallery mode, search, and privacy controls at `http://localhost:8082`.

---

## 6. Frontend Development

The OpenRecall frontend is served directly by the local Flask application. Templates are embedded within [openrecall/app.py](openrecall/app.py) using `render_template_string()` and styled using **Tailwind CSS**.

### Frontend Asset Structure:
```text
openrecall/static/css/
├── input.css     # Tailwind CSS source input file (directives)
└── output.css    # Compiled, minified CSS bundle (tracked in git & served by Flask)
```

### Tailwind Build Workflow:
The project uses the Tailwind CSS v3 CLI to compile `input.css` into `output.css`.

```text
input.css  ──( Tailwind CLI v3 )──>  output.css  ──( Tracked & Packaged )──>  Flask Web UI
```

If you modify Tailwind utility classes in `app.py` templates or add styles to `input.css`, rebuild `output.css` using:

```bash
npx tailwindcss@3 -i openrecall/static/css/input.css -o openrecall/static/css/output.css --minify
```

> **Important**: `output.css` is a generated file that is both tracked in git and packaged in distribution wheels. Contributors should edit `input.css` or template classes and recompile `output.css` rather than manually editing `output.css`.

---

## 7. Backend ↔ Frontend Architecture

```text
Browser UI (http://localhost:8082)
       │
       ▼  (HTTP GET / POST)
Flask Web Server (127.0.0.1:8082)
       │
       ├── GET /                  -> Timeline View (Range slider; mode=gallery for Gallery Grid)
       ├── GET /capture/<entry_id> -> Detail Page (Full resolution image & extracted text)
       ├── GET /search?q=<query>  -> Search Results (SQLite FTS5 text search)
       ├── GET /screenshot/<file> -> Local screenshot image server
       ├── POST /api/pause        -> Pause background capture recording
       ├── POST /api/resume       -> Resume background capture recording
       ├── GET /api/timeline/bounds -> JSON timeline bounds (earliest_ts, latest_ts, total_count)
       └── GET /api/timeline/at   -> JSON timestamp lookup endpoint
```

All HTTP requests bind strictly to `127.0.0.1` and operate locally.

---

## 8. Testing & Quality Verification

### Running Public Unit Tests
OpenRecall includes an automated `pytest` suite under `tests/`:

```bash
uv run pytest tests/ -q
```

Additional internal validation scripts exist for development verification.

### Frontend Verification Checklist
When making Web UI or template changes, verify:
1. Flask application starts cleanly without template syntax errors.
2. Timeline (`/`), Gallery (`/?mode=gallery`), Capture Detail (`/capture/<id>`), and Search (`/search?q=...`) pages render correctly.
3. If styles were added or changed, `npx tailwindcss@3` was executed to rebuild `output.css`.
4. Browser developer console shows zero missing static asset or JavaScript errors.
5. `uv run pytest tests/ -q` passes completely.

---

## 9. OCR Engine Development

OCR provider logic lives in [openrecall/ocr.py](openrecall/ocr.py):

- **Auto Mode (`--ocr-engine auto`)**: Tries RapidOCR first; if unavailable, tries Tesseract; if unavailable, falls back to `FallbackOCRProvider` gracefully.
- **RapidOCR (`--ocr-engine rapidocr`)**: Primary built-in OCR engine using ONNX Runtime CPU inference. Images are capped at a 1440px maximum dimension before OCR processing. CPU threads can be configured via `--ocr-threads`.
- **Tesseract (`--ocr-engine tesseract`)**: Secondary fallback engine when `tesseract` binary is detected on system `PATH`.
- **Graceful Fallback (`--disable-ocr`)**: Screen capture continues recording visual history cleanly without text extraction if OCR is disabled or unavailable.

---

## 10. Platform Abstraction Development

Platform-specific logic lives in [openrecall/platform.py](openrecall/platform.py):

- **Windows**: Multi-monitor GDI capture via `mss`, process/title tracking via `pywin32`, and Registry autostart (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OpenRecall`).
- **Linux X11**: Capture via `mss`, active window title tracking via `xprop`, and XDG Autostart (`~/.config/autostart/openrecall.desktop`).
- **Linux Wayland**: Capture via XDG Desktop Portal ScreenCast and PipeWire continuous streaming (`WaylandScreenCastCaptureProvider`), persistent session restoration tokens, and XDG Autostart. Active application and title tracking degrade gracefully under Wayland security boundaries (`Unknown App` / `Untitled Window`).
- **macOS**: Quartz screen capture via `mss`, window tracking via `pyobjc`, and LaunchAgent autostart (`~/Library/LaunchAgents/com.openrecall.app.plist`).

### Autostart Persistence (Phase 13.9)
When testing autostart integration, ensure that `StartupIntegrationProvider.enable_startup()` correctly serializes explicit runtime flags (`--storage-path`, `--ocr-engine`, `--ocr-threads`, `--disable-ocr`, `--max-storage-gb`, `--primary-monitor-only`).

---

## 11. Database & Migration Development

Database schema definitions and migration logic:
- **v3 Schema**: Defined in [openrecall/database.py](openrecall/database.py) using SQLite with FTS5 virtual tables (`entries_fts`).
- **Schema v0 → v3 Migration**: Implemented in [openrecall/maintenance.py](openrecall/maintenance.py). Automatically detects legacy v0 databases on normal startup and prompts the user to run explicit migration.
- **Migration CLI Tooling**:
  ```bash
  # Read-only audit of legacy storage:
  openrecall --audit-legacy-storage --storage-path /path/to/legacy_dir

  # Execute explicit schema migration:
  openrecall --migrate --storage-path /path/to/legacy_dir
  ```
  Migration creates a SHA-256 verified preservation copy (`recall_legacy_v0_<timestamp>.db`) prior to modifying the database.

---

## 12. Build & Packaging Workflow

OpenRecall uses `setuptools.build_meta` specified in `pyproject.toml`.

### Building Packages
To generate source distributions (`.tar.gz`) and wheel packages (`.whl`):

```bash
uv build
```

Generated artifacts are placed in `dist/`:
- `dist/openrecall-0.9.0.tar.gz`
- `dist/openrecall-0.9.0-py3-none-any.whl`

### Packaging Verification:
- Verify that `openrecall/static/css/output.css` is present inside the generated wheel (`python3 -m zipfile -l dist/openrecall-0.9.0-py3-none-any.whl`).
- Ensure no internal `.md/` development workspace files or internal test scripts are packaged.

---

## 13. Code Quality & Contribution Guidelines

1. **Focus & Scope**: Keep pull requests focused on a single feature, bug fix, or documentation enhancement.
2. **Offline Integrity**: Maintain local-first operation. Do not introduce cloud services, analytics, telemetry, or external network dependencies.
3. **Cross-Platform Safety**: Avoid platform-specific assumptions. Test behavior across Linux, Windows, and macOS.
4. **Clean Commit Hygiene**: Write clear commit titles. Never commit personal file paths, local databases, screenshot files, virtual environments, or credentials.
5. **Testing**: Run `uv run pytest tests/ -q` to verify all public unit tests pass before submitting a pull request.
