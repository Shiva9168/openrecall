"""Unit tests and benchmarks for Phase 1E local OCR pipeline and provider abstraction."""

import os
import shutil
import time
from unittest.mock import MagicMock, patch

import numpy as np
from PIL import Image
import pytest

from openrecall.config import OCR_MAX_DIMENSION
from openrecall.ocr import (
    FallbackOCRProvider,
    OCRProvider,
    TesseractOCRProvider,
    _preprocess_image_for_ocr,
    extract_text_from_image,
    get_ocr_provider,
    reset_ocr_provider,
)


@pytest.fixture(autouse=True)
def cleanup_ocr_provider():
    """Ensure clean OCR provider state before and after each test."""
    reset_ocr_provider()
    yield
    reset_ocr_provider()


class TestOCRProviders:
    """Test suite for OCR provider abstraction and concrete implementations."""

    def test_fallback_ocr_provider(self):
        provider = FallbackOCRProvider()
        assert provider.name == "fallback"
        assert provider.is_available() is True

        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
        assert provider.extract_text(dummy_img) == ""
        assert provider.extract_text(None) == ""

    def test_tesseract_ocr_provider_missing_binary(self):
        with patch("shutil.which", return_value=None):
            provider = TesseractOCRProvider()
            assert provider.is_available() is False
            assert provider.name == "tesseract"

            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            assert provider.extract_text(dummy_img) == ""

    def test_tesseract_ocr_provider_success(self):
        mock_pytesseract = MagicMock()
        mock_pytesseract.get_tesseract_version.return_value = "5.3.0"
        mock_pytesseract.image_to_string.return_value = "Hello OpenRecall OCR  \n"

        with patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            assert provider.is_available() is True

            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text = provider.extract_text(dummy_img)

            assert text == "Hello OpenRecall OCR"
            mock_pytesseract.image_to_string.assert_called_once()

    def test_tesseract_ocr_provider_exception_handling(self):
        mock_pytesseract = MagicMock()
        mock_pytesseract.get_tesseract_version.return_value = "5.3.0"
        mock_pytesseract.image_to_string.side_effect = RuntimeError("OCR binary timeout")

        with patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            assert provider.is_available() is True

            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text = provider.extract_text(dummy_img)
            assert text == ""

    def test_ocr_factory_auto_fallback(self):
        with patch("shutil.which", return_value=None):
            provider = get_ocr_provider("auto")
            assert provider.name == "fallback"

    def test_ocr_factory_explicit_fallback(self):
        provider = get_ocr_provider("fallback")
        assert provider.name == "fallback"

        reset_ocr_provider()
        provider_none = get_ocr_provider("none")
        assert provider_none.name == "fallback"

    def test_ocr_factory_env_var_override(self):
        with patch.dict(os.environ, {"OPENRECALL_OCR_ENGINE": "fallback"}):
            provider = get_ocr_provider()
            assert provider.name == "fallback"

    def test_windows_tesseract_subprocess_creationflags_patch(self):
        """Verifies that pytesseract subprocess_args on Windows includes CREATE_NO_WINDOW flag."""
        from openrecall.ocr import _patch_pytesseract_windows_subprocess

        mock_pt = MagicMock()
        mock_pt.subprocess_args.return_value = {
            "stdin": -1,
            "stderr": -1,
            "startupinfo": None,
            "env": {},
        }
        mock_pt._openrecall_patched = False

        with patch("sys.platform", "win32"), patch.dict("sys.modules", {"pytesseract.pytesseract": mock_pt}):
            _patch_pytesseract_windows_subprocess()
            res = mock_pt.subprocess_args()
            assert "creationflags" in res
            assert res["creationflags"] & 0x08000000 == 0x08000000


class TestOCRImagePreprocessing:
    """Test suite for OCR image preprocessing and bounded downscaling."""

    def test_preprocess_invalid_inputs(self):
        assert _preprocess_image_for_ocr(None) is None
        assert _preprocess_image_for_ocr(np.array([])) is None
        assert _preprocess_image_for_ocr("not_an_image") is None

    def test_preprocess_standard_image(self):
        img_array = np.zeros((600, 800, 3), dtype=np.uint8)
        pil_img = _preprocess_image_for_ocr(img_array, max_dimension=1920)

        assert isinstance(pil_img, Image.Image)
        assert pil_img.size == (800, 600)
        assert pil_img.mode == "L"

    def test_preprocess_downscales_4k_image(self):
        # 4K image: 3840 x 2160
        img_array = np.zeros((2160, 3840, 3), dtype=np.uint8)
        pil_img = _preprocess_image_for_ocr(img_array, max_dimension=1920)

        assert isinstance(pil_img, Image.Image)
        assert pil_img.mode == "L"
        w, h = pil_img.size
        assert max(w, h) <= 1920
        # Check aspect ratio preservation: 3840 / 2160 == 1.7777...
        assert abs((w / h) - (3840 / 2160)) < 0.01

    def test_tesseract_single_pass_extraction(self):
        """Verifies extract_text uses clean single-pass Tesseract invocation without unnecessary fallback overhead."""
        mock_pytesseract = MagicMock()
        mock_pytesseract.get_tesseract_version.return_value = "5.3.0"
        mock_pytesseract.image_to_string.return_value = "Single Pass OCR Text"

        with patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text = provider.extract_text(dummy_img)

            assert text == "Single Pass OCR Text"
            mock_pytesseract.image_to_string.assert_called_once()

    def test_backward_compatible_extract_text_from_image(self):
        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch("openrecall.ocr.get_ocr_provider") as mock_get_provider:
            mock_provider = MagicMock()
            mock_provider.extract_text.return_value = "Extracted Text"
            mock_get_provider.return_value = mock_provider

            res = extract_text_from_image(dummy_img)
            assert res == "Extracted Text"
            mock_provider.extract_text.assert_called_once_with(dummy_img)


class TestOCRBenchmarking:
    """Performance and RAM benchmarking suite for OCR operations."""

    def test_ocr_cold_start_and_warm_latency(self):
        reset_ocr_provider()

        # Cold start timing
        t0 = time.perf_counter()
        provider = get_ocr_provider("fallback")
        cold_start_ms = (time.perf_counter() - t0) * 1000.0

        assert cold_start_ms < 50.0  # Cold start initialization under 50 ms

        # Warm execution timing over 100 iterations
        dummy_img = np.zeros((1080, 1920, 3), dtype=np.uint8)
        t_start = time.perf_counter()
        iterations = 100
        for _ in range(iterations):
            _ = provider.extract_text(dummy_img)
        t_end = time.perf_counter()

        avg_warm_ms = ((t_end - t_start) / iterations) * 1000.0
        assert avg_warm_ms < 5.0  # Fallback/preprocess pipeline warm overhead under 5 ms

    def test_ocr_memory_overhead(self):
        import gc
        import psutil

        provider = FallbackOCRProvider()
        high_res_4k = np.random.randint(0, 255, (2160, 3840, 3), dtype=np.uint8)
        gc.collect()

        process = psutil.Process()
        rss_before = process.memory_info().rss / (1024 * 1024)

        for _ in range(20):
            _ = _preprocess_image_for_ocr(high_res_4k, max_dimension=1920)
            _ = provider.extract_text(high_res_4k)

        gc.collect()
        rss_after = process.memory_info().rss / (1024 * 1024)
        rss_delta = rss_after - rss_before

        # Ensure memory delta remains bounded (< 15 MB growth)
        assert rss_delta < 15.0

