"""Local OCR abstraction and engine management for OpenRecall.

Provides a pluggable, decoupled interface for optical character recognition
without deep learning framework overhead (no PyTorch/TensorFlow/doctr).
Supports Tesseract OCR with graceful fallback when binaries are unavailable.
"""

from abc import ABC, abstractmethod
import logging
import os
import shutil
from typing import Optional, Union

import numpy as np
from PIL import Image, ImageOps

from openrecall.config import (
    OCR_ENABLED,
    OCR_ENGINE,
    OCR_LANG,
    OCR_MAX_DIMENSION,
    OCR_TIMEOUT_SECONDS,
    RAPIDOCR_DET_LIMIT_SIDE_LEN,
    RAPIDOCR_DET_LIMIT_TYPE,
    RAPIDOCR_THREADS,
    RAPIDOCR_USE_CLS,
)

logger = logging.getLogger(__name__)

# OCR Execution Status Constants
OCR_STATUS_SUCCESS = "success"
OCR_STATUS_EMPTY = "empty"
OCR_STATUS_FAILED = "failed"
OCR_STATUS_UNAVAILABLE = "unavailable"

# Sentinel stored in database entries when OCR processing times out or fails
OCR_FAILED_SENTINEL = "__OCR_FAILED__"


class OCRProvider(ABC):
    """Abstract base class for local OCR engine providers."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Returns the human-readable provider identifier."""
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """Checks whether the OCR engine and its underlying binaries are available."""
        pass

    @abstractmethod
    def extract_text(self, image: Union[np.ndarray, Image.Image]) -> str:
        """Extracts text from a single image buffer or PIL Image.

        Args:
            image: Input image as a numpy ndarray (RGB/BGR/Grayscale) or PIL Image.

        Returns:
            Extracted text string, '__OCR_FAILED__' on execution failure, or empty string if no text.
        """
        pass

    def extract_text_with_status(self, image: Union[np.ndarray, Image.Image]) -> tuple[str, str]:
        """Extracts text and returns tuple of (extracted_text, ocr_status)."""
        text = self.extract_text(image)
        if not self.is_available():
            return "", OCR_STATUS_UNAVAILABLE
        if text == OCR_FAILED_SENTINEL:
            return "", OCR_STATUS_FAILED
        if text.strip():
            return text.strip(), OCR_STATUS_SUCCESS
        return "", OCR_STATUS_EMPTY


def _preprocess_image_for_ocr(
    image: Union[np.ndarray, Image.Image],
    max_dimension: int = OCR_MAX_DIMENSION,
) -> Optional[Image.Image]:
    """Converts numpy array or PIL image to an optimized PIL Image for OCR.

    Downscales high-resolution images (e.g. 4K screenshots) to bounded dimensions
    using Lanczos filtering to preserve small text glyph edges, converts to grayscale ('L' mode),
    and applies autocontrast to optimize text-to-background contrast for Tesseract.
    """
    if image is None:
        return None

    try:
        if isinstance(image, np.ndarray):
            if image.size == 0 or image.ndim < 2:
                return None
            if image.ndim == 3 and image.shape[2] >= 3:
                g = (0.2989 * image[..., 0] + 0.5870 * image[..., 1] + 0.1140 * image[..., 2]).astype(np.uint8)
                pil_img = Image.fromarray(g, mode="L")
            else:
                pil_img = Image.fromarray(image)
        elif isinstance(image, Image.Image):
            pil_img = image
        else:
            return None

        w, h = pil_img.size
        if w < 8 or h < 8:
            return None

        if pil_img.mode != "L":
            pil_img = pil_img.convert("L")

        if max_dimension and max_dimension > 0 and (w > max_dimension or h > max_dimension):
            ratio = min(max_dimension / w, max_dimension / h)
            new_w = max(1, int(w * ratio))
            new_h = max(1, int(h * ratio))
            resample_filter = getattr(Image.Resampling, "LANCZOS", Image.Resampling.BILINEAR)
            pil_img = pil_img.resize((new_w, new_h), resample_filter)

        try:
            pil_img = ImageOps.autocontrast(pil_img)
        except Exception:
            pass

        return pil_img
    except Exception as e:
        logger.error(f"Error preprocessing image for OCR: {e}")
        return None


def _patch_pytesseract_windows_subprocess():
    """Patches pytesseract subprocess_args on Windows to include CREATE_NO_WINDOW flag.

    Suppresses tesseract.exe console window flashes when running in background mode
    or under pythonw.exe/gui_scripts on Windows.
    """
    import sys
    if sys.platform != "win32":
        return

    try:
        import pytesseract.pytesseract as pt
    except ImportError:
        return

    if getattr(pt, "_openrecall_patched", False):
        return

    orig_subprocess_args = pt.subprocess_args

    def safe_subprocess_args(include_stdout=True):
        kwargs = orig_subprocess_args(include_stdout=include_stdout)
        import subprocess
        create_no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | create_no_window
        return kwargs

    pt.subprocess_args = safe_subprocess_args
    pt._openrecall_patched = True


_patch_pytesseract_windows_subprocess()


class RapidOCRProvider(OCRProvider):
    """Local RapidOCR provider using ONNX Runtime CPU backend with lazy initialization."""

    _cached_availability: Optional[bool] = None

    def __init__(
        self,
        det_limit_type: Optional[str] = None,
        det_limit_side_len: Optional[int] = None,
        use_cls: Optional[bool] = None,
        threads: Optional[int] = None,
    ):
        self._det_limit_type = det_limit_type if det_limit_type is not None else RAPIDOCR_DET_LIMIT_TYPE
        self._det_limit_side_len = det_limit_side_len if det_limit_side_len is not None else RAPIDOCR_DET_LIMIT_SIDE_LEN
        self._use_cls = use_cls if use_cls is not None else RAPIDOCR_USE_CLS
        self._threads = threads if threads is not None else RAPIDOCR_THREADS
        self._engine = None
        self._initialized = False

    @property
    def name(self) -> str:
        return "rapidocr"

    def is_available(self) -> bool:
        if RapidOCRProvider._cached_availability is not None:
            return RapidOCRProvider._cached_availability

        try:
            from rapidocr import RapidOCR  # noqa: F401
            RapidOCRProvider._cached_availability = True
            return True
        except ImportError:
            try:
                from rapidocr_onnxruntime import RapidOCR  # noqa: F401
                RapidOCRProvider._cached_availability = True
                return True
            except ImportError:
                logger.info("RapidOCR package (rapidocr / rapidocr_onnxruntime) not found.")
                RapidOCRProvider._cached_availability = False
                return False
        except Exception as e:
            logger.warning(f"RapidOCR availability check failed: {e}")
            RapidOCRProvider._cached_availability = False
            return False

    def _get_engine(self):
        if self._initialized:
            return self._engine

        self._initialized = True
        if not self.is_available():
            self._engine = None
            return None

        try:
            try:
                from rapidocr import RapidOCR
            except ImportError:
                from rapidocr_onnxruntime import RapidOCR

            init_kwargs = {
                "det_limit_type": self._det_limit_type,
                "det_limit_side_len": self._det_limit_side_len,
                "use_cls": self._use_cls,
            }
            if self._threads is not None and self._threads > 0:
                init_kwargs["intra_op_num_threads"] = self._threads

            try:
                logger.info("Initializing RapidOCR engine (lazy load)...")
                self._engine = RapidOCR(**init_kwargs)
            except TypeError:
                self._engine = RapidOCR()

            logger.info("RapidOCR engine initialized successfully.")
            return self._engine
        except Exception as e:
            logger.error(f"Failed to initialize RapidOCR engine: {e}")
            self._engine = None
            return None

    def extract_text(self, image: Union[np.ndarray, Image.Image]) -> str:
        if not OCR_ENABLED:
            return ""

        if not self.is_available():
            return ""

        engine = self._get_engine()
        if engine is None:
            return OCR_FAILED_SENTINEL

        try:
            if isinstance(image, Image.Image):
                img_input = np.array(image)
            elif isinstance(image, np.ndarray):
                img_input = image
            else:
                return ""

            if img_input.size == 0 or img_input.ndim < 2:
                return ""

            results = engine(img_input)

            if results is None:
                return ""

            extracted_lines = []

            # Modern RapidOCR 3.9+ returns a RapidOCROutput object (with .txts attribute)
            if hasattr(results, "txts"):
                txts = results.txts
                if txts:
                    for t in txts:
                        if t and isinstance(t, str) and t.strip():
                            extracted_lines.append(t.strip())
            # Dict output fallback if returned by any backend wrapper
            elif isinstance(results, dict) and "txts" in results:
                txts = results.get("txts")
                if txts:
                    for t in txts:
                        if t and isinstance(t, str) and t.strip():
                            extracted_lines.append(t.strip())
            # Legacy RapidOCR 1.x output format: (results_list, elapse) or list of [box, text, score]
            elif isinstance(results, (tuple, list)):
                ocr_items = results[0] if len(results) > 0 and isinstance(results[0], (list, tuple)) else results
                if ocr_items:
                    for item in ocr_items:
                        if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
                            txt = str(item[1]).strip()
                            if txt:
                                extracted_lines.append(txt)

            return "\n".join(extracted_lines)
        except Exception as e:
            logger.error(f"RapidOCR extraction failed: {e}")
            return OCR_FAILED_SENTINEL


class TesseractOCRProvider(OCRProvider):
    """Local Tesseract OCR provider using pytesseract binding."""

    _cached_availability: Optional[bool] = None

    def __init__(self, lang: str = OCR_LANG, timeout: float = OCR_TIMEOUT_SECONDS):
        self._lang = lang
        self._timeout = timeout

    @property
    def name(self) -> str:
        return "tesseract"

    def is_available(self) -> bool:
        if TesseractOCRProvider._cached_availability is not None:
            return TesseractOCRProvider._cached_availability

        tesseract_bin = shutil.which("tesseract")
        if not tesseract_bin:
            logger.info("Tesseract binary not found on system PATH.")
            TesseractOCRProvider._cached_availability = False
            return False

        try:
            import pytesseract

            _patch_pytesseract_windows_subprocess()
            pytesseract.get_tesseract_version()
            TesseractOCRProvider._cached_availability = True
            logger.info(f"Tesseract OCR engine initialized successfully (path: {tesseract_bin}).")
            return True
        except Exception as e:
            logger.warning(f"Tesseract OCR availability check failed: {e}")
            TesseractOCRProvider._cached_availability = False
            return False

    def extract_text(self, image: Union[np.ndarray, Image.Image]) -> str:
        if not OCR_ENABLED:
            return ""

        if not self.is_available():
            return ""

        pil_img = _preprocess_image_for_ocr(image)
        if pil_img is None:
            return ""

        try:
            import pytesseract

            _patch_pytesseract_windows_subprocess()

            text = pytesseract.image_to_string(
                pil_img,
                lang=self._lang,
                timeout=self._timeout,
            )
            return text.strip()
        except Exception as e:
            err_name = type(e).__name__
            if "Timeout" in err_name or "Tesseract" in err_name or "timeout" in str(e).lower():
                logger.warning(f"Tesseract OCR process warning or timeout: {e}")
            else:
                logger.error(f"Tesseract OCR extraction failed: {e}")
            return OCR_FAILED_SENTINEL


class FallbackOCRProvider(OCRProvider):
    """Fallback dummy OCR provider returning empty text when no OCR engine is present or OCR is disabled."""

    @property
    def name(self) -> str:
        return "fallback"

    def is_available(self) -> bool:
        return True

    def extract_text(self, image: Union[np.ndarray, Image.Image]) -> str:
        return ""


_active_provider: Optional[OCRProvider] = None


def get_ocr_provider(provider_name: Optional[str] = None) -> OCRProvider:
    """Factory function returning the configured or best available OCRProvider instance."""
    global _active_provider

    if not OCR_ENABLED:
        return FallbackOCRProvider()

    if provider_name is None:
        provider_name = os.getenv("OPENRECALL_OCR_ENGINE", OCR_ENGINE)

    if provider_name in ("none", "fallback", "disabled"):
        return FallbackOCRProvider()

    if _active_provider is not None and (provider_name in ("auto", None) or provider_name == _active_provider.name):
        return _active_provider

    if provider_name == "rapidocr":
        provider = RapidOCRProvider()
        if provider.is_available():
            _active_provider = provider
            return provider
        logger.warning("Requested 'rapidocr' OCR provider is not available. Falling back to Tesseract or Fallback.")
        tesseract = TesseractOCRProvider()
        if tesseract.is_available():
            _active_provider = tesseract
            return tesseract
        _active_provider = FallbackOCRProvider()
        return _active_provider

    if provider_name == "tesseract":
        provider = TesseractOCRProvider()
        if provider.is_available():
            _active_provider = provider
            return provider
        logger.warning("Requested 'tesseract' OCR provider is not available. Falling back to RapidOCR or Fallback.")
        rapidocr = RapidOCRProvider()
        if rapidocr.is_available():
            _active_provider = rapidocr
            return rapidocr
        _active_provider = FallbackOCRProvider()
        return _active_provider

    # "auto" mode: try RapidOCR (bundled primary), then Tesseract (alternative), then Fallback
    rapidocr_provider = RapidOCRProvider()
    if rapidocr_provider.is_available():
        _active_provider = rapidocr_provider
        return rapidocr_provider

    tesseract_provider = TesseractOCRProvider()
    if tesseract_provider.is_available():
        _active_provider = tesseract_provider
        return tesseract_provider

    _active_provider = FallbackOCRProvider()
    return _active_provider


def reset_ocr_provider() -> None:
    """Resets cached active OCR provider instance (useful for testing)."""
    global _active_provider
    _active_provider = None
    TesseractOCRProvider._cached_availability = None
    RapidOCRProvider._cached_availability = None


def extract_text_from_image(image: Union[np.ndarray, Image.Image]) -> str:
    """Extracts text from an image using the active OCR engine."""
    provider = get_ocr_provider()
    return provider.extract_text(image)
