# OpenRecall Phase 5 — Manual Cross-Platform Smoke-Test Checklist

Use this checklist to manually verify OpenRecall on physical or virtual test environments across **Linux (X11 & Wayland)**, **Windows 10/11**, and **macOS**.

---

## Pre-Test Environment Matrix

Record test system configuration before starting:
- **Operating System & Version**: (e.g. Ubuntu 24.04 LTS / Windows 11 23H2 / macOS 14.5 Sonoma)
- **Display Server**: (X11 / Wayland / Win32 GDI / Quartz)
- **Monitors Connected**: (Single / Dual / Multi-monitor)
- **Python Version**: (e.g. Python 3.11.9)
- **Tesseract Installed**: (Yes / No)

---

## 16-Step Verification Checklist

- [ ] **1. Clean Installation**:
  Create a clean virtual environment (`python3 -m venv .venv && source .venv/bin/activate`) and run `pip install .`. Verify `openrecall` command is installed on PATH.

- [ ] **2. Application Startup**:
  Run `openrecall` (or `python3 -m openrecall.app`). Verify startup logs indicate database initialization and background thread startup on port 8082 without exceptions.

- [ ] **3. Local Web UI**:
  Open `http://localhost:8082` in browser. Verify timeline history loads cleanly.

- [ ] **4. Offline Operation**:
  Disconnect network interface or disable Wi-Fi/ethernet. Refresh `http://localhost:8082`. Verify UI loads completely with full CSS styling and SVG icons with zero network requests or timeout delays.

- [ ] **5. Screen Capture Execution**:
  Perform actions on desktop (switch windows, open application). Wait 6–10 seconds. Verify new screenshot cards appear in timeline view.

- [ ] **6. Multi-Monitor Behavior**:
  (If multi-monitor setup) Verify captures record all active displays or primary display when `--primary-monitor-only` flag is passed. Verify monitor badges (e.g. `Mon 2`) render correctly on timeline cards.

- [ ] **7. Active Application / Title Metadata**:
  - **Linux X11**: Verify card badges show active app name (e.g. `Firefox`, `Terminal`) and window title.
  - **Windows**: Verify active window process name and title render correctly when `pywin32` is installed.
  - **macOS**: Verify active app name and title render correctly when `pyobjc` is installed.
  - **Linux Wayland**: Verify active app and title degrade gracefully to `Unknown App` / `Untitled Window` without crashing.

- [ ] **8. Idle Detection Behavior**:
  Leave system untouched for >5 seconds. Verify idle detection provider reports user inactive (`is_user_active(5.0) -> False`) and pauses capture accumulation until input resumes.

- [ ] **9. OCR with Tesseract Installed**:
  (With `tesseract` binary on PATH) Capture a window containing distinct text. Open capture detail page (`/capture/<id>`). Verify extracted text panel contains expected OCR string.

- [ ] **10. OCR Graceful Fallback (Without Tesseract)**:
  Rename or temporarily remove `tesseract` from PATH. Restart `openrecall`. Verify application continues running and capturing frames without crashing, degrading text panel to *"No OCR text extracted for this capture"*.

- [ ] **11. SQLite Database Storage**:
  Inspect database file in OS appdata directory (`recall.db`). Verify `entries` table and `entries_fts` virtual table contain valid timestamp and image path rows.

- [ ] **12. FTS5 Full-Text Search**:
  Type a keyword from a past capture into the search bar. Verify matching records render correctly at `/search?q=<keyword>`.

- [ ] **13. Screenshot Detail View**:
  Click a screenshot card on timeline. Verify full-resolution image, metadata panel, and *"Copy Text"* button operate as expected.

- [ ] **14. Privacy Pause / Resume**:
  Pause capture via privacy API or hotkey. Verify queue is cleared and capture loop stops writing screenshots until resumed.

- [ ] **15. Graceful OS Shutdown**:
  Press `Ctrl+C` in console or send `SIGTERM`. Verify shutdown log shows background capture pipeline and maintenance worker threads joining cleanly within deadline.

- [ ] **16. System Autostart Integration**:
  - **Linux**: Execute autostart enable. Verify `~/.config/autostart/openrecall.desktop` is created.
  - **Windows**: Execute autostart enable. Verify `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\OpenRecall` key is set.
  - **macOS**: Execute autostart enable. Verify `~/Library/LaunchAgents/com.openrecall.app.plist` is created.
