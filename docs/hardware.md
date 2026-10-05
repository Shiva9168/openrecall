# OpenRecall Hardware & Compatibility Guide

This guide details practical hardware recommendations, OCR engine resource considerations, bundled package components, and validated test environments for OpenRecall v0.9.0.

---

## Hardware Recommendations

OpenRecall is designed for local processing using your system's CPU.

### RAM
- **4 GB RAM is the practical recommended baseline.**
- This recommendation is a practical baseline to ensure smooth operation alongside daily desktop applications. It is not a hard minimum requirement or a performance guarantee. Actual memory usage varies based on display resolution, capture frequency, OCR engine choice, and active workload.

### CPU
- OpenRecall processes screen captures, image scaling, and text extraction locally on the CPU.
- A multi-core desktop or laptop processor is recommended.
- A dedicated GPU is not required.

### GPU
- Dedicated GPU hardware (such as CUDA or NVIDIA GPUs) is **not required** for normal operation.

---

## OCR Engine Guidance

Text recognition (OCR) is typically the most resource-intensive task performed by OpenRecall. Configuration options are available to align performance with your system resources.

### RapidOCR
- Bundled with OpenRecall as the primary OCR engine.
- Runs locally on the CPU.
- Can be relatively demanding on CPU and RAM, especially on lower-end systems.
- Input images are automatically capped at a 1440px maximum dimension before OCR processing.
- Thread usage can be tuned using `--ocr-threads`:
  ```bash
  openrecall --ocr-threads 2
  ```

### Tesseract
> **Tesseract is NOT bundled with OpenRecall and must be installed separately.**

- An alternative OCR engine option that may be preferable on some lower-resource systems.
- Enable via CLI:
  ```bash
  openrecall --ocr-engine tesseract
  ```
- **Windows Setup**: On Windows, Tesseract must be added to the system `PATH` so OpenRecall can locate the executable.

### Disable OCR
If OCR is not needed or system resources are severely limited, OCR processing can be disabled entirely:
```bash
openrecall --disable-ocr
```

### Auto Engine Selection (`--ocr-engine auto`)
The default engine setting follows an availability fallback sequence:
```text
RapidOCR → Tesseract → built-in fallback
```
`auto` is availability/fallback based. It does not benchmark CPU performance or dynamically inspect available system RAM.

---

## Bundled Components

| Component | Bundled | Notes |
| :--- | :--- | :--- |
| **RapidOCR** | **Yes** | Primary local OCR engine |
| **Tesseract** | **No** | Install separately; Windows users must add it to `PATH` |
| **OCR disabled** | **Available** | Disable via `--disable-ocr` |

---

## Tested Hardware & Environments

The following tables list real-world environments where OpenRecall has been run and validated during development.

### Physical Systems Tested

| OS | Hardware | RAM | Display | Status |
| :--- | :--- | ---: | :--- | :--- |
| Windows 10 Pro | AMD Ryzen 5 3500 desktop | 48 GB | — | ✅ Tested |
| Linux Mint 22 Cinnamon | Lenovo ThinkPad T540p, Intel Core i5 | 8 GB | X11 | ✅ Tested |

### Virtual Machines Tested

| OS | Environment | Allocation | Display | Status |
| :--- | :--- | :--- | :--- | :--- |
| Windows 10 Home | VMware Workstation | 2 vCPU, 4 GB RAM | — | ✅ VM Tested |
| Ubuntu 26.04 LTS | VMware Workstation | 4 vCPU, 8 GB RAM | Wayland | ✅ VM Tested |

> Tested configurations are examples of systems where OpenRecall has been run and validated. They are not guarantees for every system with similar hardware.

---

## Awaiting Testing

### Windows 11
- Testing planned.
- Physical runtime validation has not yet been completed.
- Update this document when physical testing is complete.

### macOS
- Not physically tested yet.
- Community testing is welcome.
- Contributors are encouraged to report:
  - Mac model
  - macOS version
  - RAM
  - OpenRecall / OCR behavior
  - Any capture or startup observations

---

## Storage Considerations

- Storage consumption depends on screen activity, resolution, capture interval, and retention settings.
- Static / unchanged screens are automatically skipped to reduce disk usage.
- Storage growth can be capped using `--max-storage-gb`:
  ```bash
  openrecall --max-storage-gb 5
  ```
- Older screenshots and records are automatically removed when the configured capacity limit is reached.

---

## Low-Resource Settings

### If RapidOCR Is Too Heavy

1. **Try Tesseract**:
   ```bash
   openrecall --ocr-engine tesseract
   ```
   *(On Windows, install Tesseract separately and add it to `PATH`.)*

2. **Reduce RapidOCR Threads**:
   ```bash
   openrecall --ocr-threads 2
   ```

3. **Disable OCR**:
   ```bash
   openrecall --disable-ocr
   ```

> These settings can reduce resource usage, but the best configuration depends on the system and workload.

---

## Privacy & Local Processing

- **Local Capture**: Screenshots remain on the local device.
- **Local OCR & Search**: Text extraction and search queries run strictly on the local CPU.
- **No Cloud Services**: No cloud OCR service or remote GPU processing is required.
- **Localhost Bound**: Web UI is bound strictly to `127.0.0.1:8082`.
- Core application operation requires no internet connectivity.