```
   ____                   ____                  ____   
  / __ \____  ___  ____  / __ \___  _________ _/ / /   
 / / / / __ \/ _ \/ __ \/ /_/ / _ \/ ___/ __ `/ / /    
/ /_/ / /_/ /  __/ / / / _, _/  __/ /__/ /_/ / / /     
\____/ .___/\___/_/ /_/_/ |_|\___/\___/\__,_/_/_/      
    /_/                                                                                                                         
```
**Enjoy this project?** Show your support by starring it! ⭐️ Thank you!

Join our [Discord](https://discord.gg/RzvCYRgUkx) and/or [Telegram](https://t.me/+5DULWTesqUYwYjY0) community to stay informed of updates!

# Take Control of Your Digital Memory

OpenRecall is a fully open-source, privacy-first alternative to proprietary solutions like Microsoft's Windows Recall or Limitless' Rewind.ai. With OpenRecall, you can easily access your digital history, enhancing your memory and productivity without compromising your privacy.

## What does it do?

OpenRecall captures your digital history through regularly taken snapshots (screenshots). The text and images within these screenshots are analyzed locally and made searchable, allowing you to quickly find specific information by typing relevant keywords into OpenRecall. You can also manually scroll back through your timeline history to revisit past activities.

https://github.com/openrecall/openrecall/assets/16676419/cfc579cb-165b-43e4-9325-9160da6487d2

## Why Choose OpenRecall?

OpenRecall offers several key advantages over closed-source alternatives:

- **Transparency**: OpenRecall is 100% open-source, allowing you to audit the source code for potential backdoors or privacy-invading features.
- **Cross-platform Support**: OpenRecall works on Windows, macOS, and Linux, giving you the freedom to use it on your preferred operating system.
- **Privacy-focused & Offline-First**: Your data is stored locally on your device; no internet connection or cloud service is required.
- **Hardware Compatibility**: OpenRecall is optimized for low-end hardware (~2 GB RAM, CPU-only, no dedicated GPU needed).

## Features

- **Time Travel Timeline**: Revisit and explore your past digital activities seamlessly across Windows, macOS, or Linux.
- **Local-First Search**: Advanced local OCR interprets your history, providing fast full-text FTS5 search capabilities.
- **Pause & Resume Privacy Controls**: Easily pause and resume screen capture directly from the local Web UI or REST API (`/api/pause`, `/api/resume`).
- **Full Control Over Storage**: Your data is stored locally with automatic background storage maintenance and capacity management.

## Platform & Operating System Support

OpenRecall is built using modular platform abstraction providers:

- **Linux (X11)**: Fully supported. Automatic screen capture, multi-monitor enumeration, active application name, window title tracking via `xprop`, and autostart via XDG (`~/.config/autostart`).
- **Linux (Wayland)**: Supported with known limitations. Screen capture operates via XWayland where supported. Security restrictions under pure Wayland mean active application name and window title degrade gracefully to `Unknown App` / `Untitled Window`.
- **Windows (10 / 11)**: Supported. Multi-monitor GDI screen capture, active window title and process tracking via `pywin32`, and autostart via Windows Registry (`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`).
- **macOS**: Supported. Multi-monitor Quartz screen capture, active application and window title tracking via `pyobjc`, and autostart via LaunchAgent (`~/Library/LaunchAgents/com.openrecall.app.plist`). *Note*: macOS 10.15+ requires granting Screen Recording permission in System Settings.

### Offline & Local-First Operation
OpenRecall is 100% offline-first. All OCR, frame diffing, SQLite search, and Web UI styling operate locally on your machine without external CDN network dependencies or cloud API calls.

### Optional Local OCR (Tesseract)
Tesseract OCR is an optional system dependency. If installed and present on your system `PATH`, OpenRecall automatically extracts text from captures. If Tesseract is not installed, OpenRecall degrades gracefully and continues recording visual history without text extraction.

## Get Started

### Prerequisites
- Python 3.9+
- Linux, Windows 10/11, or macOS
- Tesseract OCR (Optional)

To install:
```bash
python3 -m pip install --upgrade openrecall
```

To run:
```bash
openrecall
```
or:
```bash
python3 -m openrecall.app
```
Open your browser to [http://localhost:8082](http://localhost:8082) to access the OpenRecall Web UI.

## Command-Line Arguments

- `--storage-path`: Custom directory path to store screenshots and database (`recall.db`). Default is the user data path for your OS:
  - Linux: `~/.local/share/openrecall`
  - Windows: `%APPDATA%\openrecall`
  - macOS: `~/Library/Application Support/openrecall`
- `--primary-monitor-only` (default: `False`): Only record the primary monitor (rather than individual screenshots for all connected monitors).
- `--max-storage-gb` (default: `0` / disabled): Maximum referenced screenshot storage capacity limit in Gigabytes (e.g. `--max-storage-gb 5.0`). When set, OpenRecall automatically trims the oldest screenshots when storage exceeds the threshold.
- `--enable-autostart` (default: `False`): Explicitly registers per-user autostart for OpenRecall on system boot (Windows Registry, Linux XDG autostart, or macOS LaunchAgent).
- `--disable-autostart` (default: `False`): Explicitly removes per-user autostart registration for OpenRecall.

## Local Web UI & REST API Controls

The OpenRecall Web UI at `http://localhost:8082` includes:
- **Time Travel Timeline Slider**: Interactive horizontal range slider to scrub through desktop visual history with 100ms debounced single-image rendering.
- **Gallery Grid Mode**: Alternate view mode accessible via top navigation toggle (`/?mode=gallery`) presenting a paginated grid of captures.
- **Status Indicator**: Displays whether recording is `Active` or `Paused` in the navigation header.
- **Pause / Resume Controls**: Toggle background capture on or off directly via UI buttons or local POST requests:
  - `POST /api/pause`: Pauses screen capture recording (default interval: 10 seconds).
  - `POST /api/resume`: Resumes screen capture recording.
- **REST API Endpoints**:
  - `GET /api/timeline/bounds`: Returns JSON timeline bounds (`earliest_ts`, `latest_ts`, `total_count`).
  - `GET /api/timeline/at?timestamp=X`: Returns JSON capture record nearest to timestamp `X`.
  - `GET /screenshot/<filename>`: Multi-root screenshot image endpoint.

## Troubleshooting

- **Tesseract OCR not detected**: Install Tesseract using your system package manager (`sudo apt install tesseract-ocr` on Ubuntu/Debian, `brew install tesseract` on macOS, or the installer on Windows). OpenRecall will automatically detect Tesseract on your `PATH`.
- **Port 8082 already in use**: Ensure another instance of OpenRecall is not already running.
- **Wayland Window Titles showing 'Untitled Window'**: Under pure Wayland sessions, Linux security policies prevent external process window title inspection. OpenRecall degrades gracefully and continues capturing visual screenshots.

## Uninstall Instructions

To uninstall OpenRecall and remove all stored data:

1. Uninstall the package:
   ```bash
   python3 -m pip uninstall openrecall
   ```

2. Remove stored data:
   - On Windows:
     ```cmd
     rmdir /s %APPDATA%\openrecall
     ```
   - On macOS:
     ```bash
     rm -rf ~/Library/Application\ Support/openrecall
     ```
   - On Linux:
     ```bash
     rm -rf ~/.local/share/openrecall
     ```

*Note*: If you specified a custom storage path using `--storage-path`, ensure you remove that directory as well.

## Contribute

As an open-source project, we welcome contributions from the community. If you'd like to help improve OpenRecall, please submit a pull request or open an issue on our GitHub repository.

## Contact the Maintainers
mail@datatalk.be

## License

OpenRecall is released under the [AGPLv3](https://opensource.org/licenses/AGPL-3.0), ensuring that it remains open and accessible to everyone.
