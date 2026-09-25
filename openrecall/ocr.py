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
from PIL import Image

from openrecall.config import OCR_ENGINE, OCR_LANG, OCR_MAX_DIMENSION

logger = logging.getLogger(__name__)


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
            Extracted text string, or empty string on failure.
        """
        pass


def _preprocess_image_for_ocr(
    image: Union[np.ndarray, Image.Image],
    max_dimension: int = OCR_MAX_DIMENSION,
) -> Optional[Image.Image]:
    """Converts numpy array or PIL image to an optimized PIL Image for OCR.

    Downscales high-resolution images (e.g. 4K screenshots) to bounded dimensions
    and converts to grayscale ('L' mode) to preserve small text legibility while
    keeping RAM and CPU overhead low on 2 GB systems.
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
            pil_img = pil_img.resize((new_w, new_h), Image.Resampling.BILINEAR)

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


class TesseractOCRProvider(OCRProvider):
    """Local Tesseract OCR provider using pytesseract binding."""

    _cached_availability: Optional[bool] = None

    def __init__(self, lang: str = OCR_LANG, timeout: float = 10.0):
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
            return ""


class FallbackOCRProvider(OCRProvider):
    """Fallback dummy OCR provider returning empty text when no OCR engine is present."""

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

    if provider_name is None:
        provider_name = os.getenv("OPENRECALL_OCR_ENGINE", OCR_ENGINE)

    if _active_provider is not None and provider_name in ("auto", None):
        return _active_provider

    if provider_name == "tesseract":
        provider = TesseractOCRProvider()
        if provider.is_available():
            _active_provider = provider
            return provider
        logger.warning("Requested 'tesseract' OCR provider is not available. Falling back to FallbackOCRProvider.")
        _active_provider = FallbackOCRProvider()
        return _active_provider

    if provider_name in ("fallback", "none"):
        _active_provider = FallbackOCRProvider()
        return _active_provider

    # "auto" mode: try Tesseract, fallback if unavailable
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


def extract_text_from_image(image: Union[np.ndarray, Image.Image]) -> str:
    """Extracts text from an image using the active OCR engine."""
    provider = get_ocr_provider()
    return provider.extract_text(image)
