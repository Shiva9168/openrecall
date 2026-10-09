# OpenRecall Tested Hardware & Environments

This document lists real-world hardware, virtual machines, and operating system configurations that have been tested and validated with OpenRecall.

---

## Scope & Platform Support

- **Project Platform Support**: OpenRecall supports Windows, macOS, and Linux desktop environments.
- **Tested Configurations**: This document lists hardware and software configurations that have been physically run and verified during testing.
- **Compatibility**: Systems not listed here are not necessarily unsupported. OpenRecall can run on a wide variety of hardware configurations.
- **Community Contributions**: The tested configurations list grows through real-world testing and community contributions.

---

## Tested Configurations

| Device / System | CPU | RAM | GPU | OS | Display Server / Environment | Capture | OCR | Notes |
| :--- | :--- | ---: | :--- | :--- | :--- | :--- | :--- | :--- |
| Custom Desktop | AMD Ryzen 5 3500 (6-Core) | 48 GB | NVIDIA GeForce RTX 3070 | Windows 10 Pro x64 | Win32 Desktop | ✅ Win32 GDI | ✅ RapidOCR / Tesseract | Physical system tested. Full startup, capture, FTS, and autostart validated. |
| Lenovo LOQ 15 | Intel Core i5 13th Gen | 16 GB | NVIDIA RTX 3050 | Windows 11 Pro | Win32 Desktop | ✅ Win32 GDI | ✅ RapidOCR / Tesseract | Physical system tested. Multi-monitor capture tested and validated. |
| Lenovo ThinkPad T540p | Intel Core i5 | 8 GB | Integrated Intel HD | Linux Mint 22 Cinnamon | X11 | ✅ X11 (`mss`) | ✅ RapidOCR / Tesseract | Physical laptop tested. Single-monitor X11 capture (`mss`) and `xprop` window tracking validated. |
| VM (VMware Workstation) | 2 vCPUs | 4 GB | Virtual VGA | Windows 10 Home x64 | Win32 Desktop | ✅ Win32 GDI | ✅ RapidOCR / Tesseract | Virtual machine tested. Validated under low-resource 4 GB RAM baseline. |
| VM (VMware Workstation) | 4 vCPUs | 8 GB | Virtual SVGA | Ubuntu 26.04 LTS | Wayland | ✅ Portal ScreenCast / PipeWire | ✅ RapidOCR / Tesseract | Virtual machine tested. Native XDG Desktop Portal continuous streaming validated. |

---

## macOS Testing

macOS support is part of OpenRecall's cross-platform goal, but macOS hardware has not yet been included in the project's tested hardware configurations.

macOS users are welcome to contribute testing results, including device/model, Apple Silicon or Intel architecture, macOS version, capture behavior, OCR behavior, and any limitations encountered.

---

## Contributing a Tested Configuration

We welcome community reports of real-world hardware and operating systems tested with OpenRecall! macOS and additional Windows/Linux configurations are especially welcome.

To submit a tested configuration, open an issue or pull request on GitHub with the following details:

- **Device / Model**: (e.g. `Lenovo ThinkPad T14`, `Custom Desktop`, `MacBook Air`)
- **CPU**: (e.g. `Intel Core i7-1165G7`, `AMD Ryzen 7 5800H`, `Apple M1`)
- **RAM**: (e.g. `8 GB`, `16 GB`)
- **GPU**: (e.g. `Integrated Intel Iris Xe`, `NVIDIA GTX 1650`, `None`)
- **OS & Version**: (e.g. `Ubuntu 24.04 LTS`, `Windows 11 23H2`, `macOS 14 Sonoma`)
- **Display Server / Desktop**: (e.g. `Wayland (GNOME)`, `X11 (XFCE)`, `Win32`)
- **Screen Capture Status**: (e.g. `Working`, `Issues with multi-monitor`)
- **OCR Status**: (e.g. `RapidOCR working`, `Tesseract fallback used`)
- **OpenRecall Version**: (e.g. `v0.9.0`)
- **Notes & Limitations**: (Any observations on CPU usage, memory, or autostart)
