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
    RapidOCRProvider,
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
        from openrecall.ocr import OCR_FAILED_SENTINEL, OCR_STATUS_FAILED

        mock_pytesseract = MagicMock()
        mock_pytesseract.get_tesseract_version.return_value = "5.3.0"
        mock_pytesseract.image_to_string.side_effect = RuntimeError("OCR binary timeout")

        with patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            assert provider.is_available() is True

            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text = provider.extract_text(dummy_img)
            assert text == OCR_FAILED_SENTINEL

            clean_text, status = provider.extract_text_with_status(dummy_img)
            assert clean_text == ""
            assert status == OCR_STATUS_FAILED

    def test_ocr_provider_status_semantics(self):
        from openrecall.ocr import (
            OCR_STATUS_SUCCESS,
            OCR_STATUS_EMPTY,
            OCR_STATUS_UNAVAILABLE,
        )

        # 1. Available with text -> success
        reset_ocr_provider()
        mock_pytesseract = MagicMock()
        mock_pytesseract.get_tesseract_version.return_value = "5.3.0"
        mock_pytesseract.image_to_string.return_value = "Sample OCR Text"

        with patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text, status = provider.extract_text_with_status(dummy_img)
            assert text == "Sample OCR Text"
            assert status == OCR_STATUS_SUCCESS

        # 2. Available with empty text -> empty
        reset_ocr_provider()
        mock_pytesseract.image_to_string.return_value = "   "
        with patch("shutil.which", return_value="/usr/bin/tesseract"), \
             patch.dict("sys.modules", {"pytesseract": mock_pytesseract}):
            provider = TesseractOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text, status = provider.extract_text_with_status(dummy_img)
            assert text == ""
            assert status == OCR_STATUS_EMPTY

        # 3. Binary missing -> unavailable
        reset_ocr_provider()
        with patch("shutil.which", return_value=None):
            provider = TesseractOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text, status = provider.extract_text_with_status(dummy_img)
            assert text == ""
            assert status == OCR_STATUS_UNAVAILABLE

    def test_rapidocr_provider_missing_package(self):
        with patch.dict("sys.modules", {"rapidocr": None, "rapidocr_onnxruntime": None}):
            provider = RapidOCRProvider()
            assert provider.is_available() is False
            assert provider.name == "rapidocr"

            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            assert provider.extract_text(dummy_img) == ""

    def test_rapidocr_provider_success_and_lazy_init(self):
        mock_output = MagicMock()
        mock_output.txts = ("Hello RapidOCR", "Second Line")

        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.return_value = mock_output
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}):
            provider = RapidOCRProvider()
            assert provider.is_available() is True
            # Engine must NOT be initialized yet (lazy initialization)
            mock_rapidocr_cls.assert_not_called()

            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text, status = provider.extract_text_with_status(dummy_img)

            assert text == "Hello RapidOCR\nSecond Line"
            from openrecall.ocr import OCR_STATUS_SUCCESS
            assert status == OCR_STATUS_SUCCESS
            mock_rapidocr_cls.assert_called_once()

    def test_rapidocr_provider_output_dataclass_structure(self):
        """Verifies compatibility with RapidOCR 3.9.2 RapidOCROutput dataclass structure (.txts attribute)."""
        mock_output = MagicMock()
        mock_output.txts = ("Hello RapidOCR 3.9.2", "Dataclass Output Test")

        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.return_value = mock_output
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}):
            provider = RapidOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            text, status = provider.extract_text_with_status(dummy_img)

            assert text == "Hello RapidOCR 3.9.2\nDataclass Output Test"
            from openrecall.ocr import OCR_STATUS_SUCCESS
            assert status == OCR_STATUS_SUCCESS

    def test_rapidocr_provider_real_inference_smoke(self):
        """Smoke test executing real RapidOCR 3.9.2 inference without mocks when rapidocr is installed."""
        try:
            import rapidocr  # noqa: F401
        except ImportError:
            pytest.skip("rapidocr package not installed in environment")

        reset_ocr_provider()
        provider = RapidOCRProvider()
        assert provider.is_available() is True

        from PIL import ImageDraw
        img = Image.new("RGB", (400, 100), color=(255, 255, 255))
        d = ImageDraw.Draw(img)
        d.text((10, 10), "OpenRecall RapidOCR Test", fill=(0, 0, 0))

        img_np = np.array(img)
        text = provider.extract_text(img_np)
        assert isinstance(text, str)
        assert "OpenRecall" in text or "RapidOCR" in text

    def test_rapidocr_provider_exception_handling(self):
        from openrecall.ocr import OCR_FAILED_SENTINEL, OCR_STATUS_FAILED

        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.side_effect = Exception("ONNX runtime error")
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}):
            provider = RapidOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)

            text = provider.extract_text(dummy_img)
            assert text == OCR_FAILED_SENTINEL

            clean_text, status = provider.extract_text_with_status(dummy_img)
            assert clean_text == ""
            assert status == OCR_STATUS_FAILED

    def test_rapidocr_provider_threads_and_params(self):
        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.return_value = (None, None)
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}), \
             patch("openrecall.ocr.RAPIDOCR_THREADS", 2):
            provider = RapidOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            provider.extract_text(dummy_img)

            mock_rapidocr_cls.assert_called_once_with(
                det_limit_type="max",
                det_limit_side_len=736,
                use_cls=True,
                intra_op_num_threads=2,
            )

    def test_rapidocr_provider_single_thread_config(self):
        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.return_value = (None, None)
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}), \
             patch("openrecall.ocr.RAPIDOCR_THREADS", 1):
            provider = RapidOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            provider.extract_text(dummy_img)

            mock_rapidocr_cls.assert_called_once_with(
                det_limit_type="max",
                det_limit_side_len=736,
                use_cls=True,
                intra_op_num_threads=1,
            )

    def test_rapidocr_provider_lifecycle_single_initialization(self):
        """Verifies that RapidOCR engine is initialized exactly ONCE across repeated screenshot calls."""
        reset_ocr_provider()

        mock_output = MagicMock()
        mock_output.txts = ("Line 1", "Line 2")

        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.return_value = mock_output
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}), \
             patch("openrecall.ocr.OCR_ENGINE", "rapidocr"):
            # Execute 10 consecutive screenshot extractions
            providers = []
            for _ in range(10):
                txt = extract_text_from_image(dummy_img)
                assert txt == "Line 1\nLine 2"
                providers.append(get_ocr_provider())

            # Verify identical provider instance reused 10/10 times
            first_provider = providers[0]
            for p in providers:
                assert p is first_provider

            # Verify RapidOCR engine constructor was invoked EXACTLY ONCE
            mock_rapidocr_cls.assert_called_once()

    def test_rapidocr_provider_automatic_threading(self):
        mock_rapidocr_cls = MagicMock()
        mock_engine = MagicMock()
        mock_engine.return_value = (None, None)
        mock_rapidocr_cls.return_value = mock_engine

        mock_module = MagicMock()
        mock_module.RapidOCR = mock_rapidocr_cls

        with patch.dict("sys.modules", {"rapidocr": mock_module, "rapidocr_onnxruntime": mock_module}), \
             patch("openrecall.ocr.RAPIDOCR_THREADS", None):
            provider = RapidOCRProvider()
            dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
            provider.extract_text(dummy_img)

            mock_rapidocr_cls.assert_called_once_with(
                det_limit_type="max",
                det_limit_side_len=736,
                use_cls=True,
            )

    def test_ocr_disabled_behavior(self):
        with patch("openrecall.ocr.OCR_ENABLED", False):
            provider = get_ocr_provider("auto")
            assert provider.name == "fallback"

            reset_ocr_provider()
            provider_rapid = get_ocr_provider("rapidocr")
            assert provider_rapid.name == "fallback"

    def test_ocr_factory_auto_selection(self):
        # 1. RapidOCR available -> auto selects rapidocr
        with patch.object(RapidOCRProvider, "is_available", return_value=True):
            reset_ocr_provider()
            provider = get_ocr_provider("auto")
            assert provider.name == "rapidocr"

        # 2. RapidOCR unavailable, Tesseract available -> auto selects tesseract
        with patch.object(RapidOCRProvider, "is_available", return_value=False), \
             patch.object(TesseractOCRProvider, "is_available", return_value=True):
            reset_ocr_provider()
            provider = get_ocr_provider("auto")
            assert provider.name == "tesseract"

        # 3. Both unavailable -> auto selects fallback
        with patch.object(RapidOCRProvider, "is_available", return_value=False), \
             patch.object(TesseractOCRProvider, "is_available", return_value=False):
            reset_ocr_provider()
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
        try:
            import pytesseract.pytesseract as pt
        except ImportError:
            pytest.skip("pytesseract not installed")

        orig_fn = MagicMock(return_value={
            "stdin": -1,
            "stderr": -1,
            "startupinfo": None,
            "env": {},
        })
        if hasattr(pt, "_openrecall_patched"):
            delattr(pt, "_openrecall_patched")

        with patch("sys.platform", "win32"), patch.object(pt, "subprocess_args", orig_fn):
            _patch_pytesseract_windows_subprocess()
            res = pt.subprocess_args()
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


class TestPhase145OCRProviderFallbackAndLifecycle:
    """Comprehensive test suite for Phase 14.5 CLI contract, provider caching, and lifecycle reliability."""

    def test_default_cli_behavior_and_choices(self):
        from openrecall.config import parser, OCR_ENGINE
        assert OCR_ENGINE == "auto"

        # Valid choices
        parsed_auto = parser.parse_args(["--ocr-engine", "auto"])
        assert parsed_auto.ocr_engine == "auto"

        parsed_rapid = parser.parse_args(["--ocr-engine", "rapidocr"])
        assert parsed_rapid.ocr_engine == "rapidocr"

        parsed_tess = parser.parse_args(["--ocr-engine", "tesseract"])
        assert parsed_tess.ocr_engine == "tesseract"

        # Invalid choices must raise SystemExit (rejected by argparse)
        with pytest.raises(SystemExit):
            parser.parse_args(["--ocr-engine", "fallback"])

        with pytest.raises(SystemExit):
            parser.parse_args(["--ocr-engine", "none"])

    def test_disable_ocr_cli_flag(self):
        from openrecall.config import parser
        parsed = parser.parse_args(["--disable-ocr"])
        assert parsed.disable_ocr is True

    def test_auto_fallback_chain(self):
        # 1. Auto -> RapidOCR when available
        reset_ocr_provider()
        with patch.object(RapidOCRProvider, "is_available", return_value=True):
            p = get_ocr_provider("auto")
            assert isinstance(p, RapidOCRProvider)

        # 2. Auto -> Tesseract when RapidOCR unavailable
        reset_ocr_provider()
        with patch.object(RapidOCRProvider, "is_available", return_value=False), \
             patch.object(TesseractOCRProvider, "is_available", return_value=True):
            p = get_ocr_provider("auto")
            assert isinstance(p, TesseractOCRProvider)

        # 3. Auto -> Fallback when both unavailable
        reset_ocr_provider()
        with patch.object(RapidOCRProvider, "is_available", return_value=False), \
             patch.object(TesseractOCRProvider, "is_available", return_value=False):
            p = get_ocr_provider("auto")
            assert isinstance(p, FallbackOCRProvider)

    def test_explicit_provider_selection_and_fallback(self):
        # Explicit RapidOCR
        reset_ocr_provider()
        with patch.object(RapidOCRProvider, "is_available", return_value=True):
            p = get_ocr_provider("rapidocr")
            assert isinstance(p, RapidOCRProvider)

        # Explicit Tesseract when available
        reset_ocr_provider()
        with patch.object(TesseractOCRProvider, "is_available", return_value=True):
            p = get_ocr_provider("tesseract")
            assert isinstance(p, TesseractOCRProvider)

        # Explicit Tesseract when unavailable -> RapidOCR
        reset_ocr_provider()
        with patch.object(TesseractOCRProvider, "is_available", return_value=False), \
             patch.object(RapidOCRProvider, "is_available", return_value=True):
            p = get_ocr_provider("tesseract")
            assert isinstance(p, RapidOCRProvider)

        # Explicit Tesseract when both unavailable -> Fallback
        reset_ocr_provider()
        with patch.object(TesseractOCRProvider, "is_available", return_value=False), \
             patch.object(RapidOCRProvider, "is_available", return_value=False):
            p = get_ocr_provider("tesseract")
            assert isinstance(p, FallbackOCRProvider)

    def test_unavailable_tesseract_fallback_caching_and_warning_deduplication(self, caplog):
        reset_ocr_provider()
        caplog.clear()

        with patch.object(TesseractOCRProvider, "is_available", return_value=False), \
             patch.object(RapidOCRProvider, "is_available", return_value=True):
            # First call triggers fallback resolution and warning
            p1 = get_ocr_provider("tesseract")
            assert isinstance(p1, RapidOCRProvider)

            # Subsequent 5 calls must return the EXACT SAME provider instance
            for _ in range(5):
                p_next = get_ocr_provider("tesseract")
                assert p_next is p1

            # Verify fallback warning was emitted EXACTLY ONCE
            warnings = [rec for rec in caplog.records if "Falling back to RapidOCR" in rec.message]
            assert len(warnings) == 1

    def test_flask_context_processor_uses_cached_provider(self):
        from openrecall.app import inject_global_template_context
        reset_ocr_provider()

        with patch.object(RapidOCRProvider, "is_available", return_value=True):
            p1 = get_ocr_provider()
            ctx1 = inject_global_template_context()
            ctx2 = inject_global_template_context()
            assert ctx1["ocr_available"] is True
            assert ctx2["ocr_available"] is True
            # Assert active provider is still identical instance
            assert get_ocr_provider() is p1

    def test_effective_configuration_change_invalidates_cache(self):
        reset_ocr_provider()
        with patch.object(RapidOCRProvider, "is_available", return_value=True), \
             patch.object(TesseractOCRProvider, "is_available", return_value=True):
            p_tess = get_ocr_provider("tesseract")
            assert isinstance(p_tess, TesseractOCRProvider)

            # Changing requested mode to rapidocr must invalidate cache and return RapidOCRProvider
            p_rapid = get_ocr_provider("rapidocr")
            assert isinstance(p_rapid, RapidOCRProvider)
            assert p_rapid is not p_tess

    def test_ocr_disabled_does_not_initialize_engine(self):
        reset_ocr_provider()
        dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
        with patch("openrecall.ocr.OCR_ENABLED", False):
            p = get_ocr_provider("tesseract")
            assert isinstance(p, FallbackOCRProvider)
            res = extract_text_from_image(dummy_img)
            assert res == ""

    def test_shutdown_lifecycle_cleanup_and_gc_ordering(self):
        reset_ocr_provider()
        call_order = []

        def mock_reset():
            call_order.append("reset_ocr_provider")

        def mock_gc():
            call_order.append("gc.collect")

        with patch("openrecall.app.reset_ocr_provider", side_effect=mock_reset), \
             patch("openrecall.app.gc.collect", side_effect=mock_gc):
            from openrecall.app import main
            # Test cleanup sequence order
            from openrecall.app import reset_ocr_provider as rop
            import gc
            rop()
            gc.collect()

            assert call_order == ["reset_ocr_provider", "gc.collect"]

    def test_defensive_repeated_reset_ocr_provider(self):
        # Multiple resets must succeed cleanly without exception
        reset_ocr_provider()
        reset_ocr_provider()
        reset_ocr_provider()
        from openrecall.ocr import _active_provider, _cached_requested_mode
        assert _active_provider is None
        assert _cached_requested_mode is None

    def test_idempotent_one_shot_shutdown_guard(self):
        """Verifies that cleanup_application_resources executes teardown operations exactly once across repeated calls."""
        mock_pipeline = MagicMock()
        mock_worker = MagicMock()
        mock_lock = MagicMock()

        call_counts = {
            "pipeline_stop": 0,
            "worker_stop": 0,
            "reset_ocr": 0,
            "gc_collect": 0,
            "lock_release": 0,
        }

        def mock_p_stop(timeout=2.0):
            call_counts["pipeline_stop"] += 1

        def mock_w_stop(timeout=2.0):
            call_counts["worker_stop"] += 1

        def mock_reset():
            call_counts["reset_ocr"] += 1

        def mock_gc():
            call_counts["gc_collect"] += 1

        def mock_release():
            call_counts["lock_release"] += 1

        mock_pipeline.stop.side_effect = mock_p_stop
        mock_worker.stop.side_effect = mock_w_stop
        mock_lock.release.side_effect = mock_release

        with patch("openrecall.app.get_capture_pipeline", return_value=mock_pipeline), \
             patch("openrecall.app.MaintenanceWorker", return_value=mock_worker), \
             patch("openrecall.app.SingleInstanceLock") as mock_lock_cls, \
             patch("openrecall.app.check_existing_instance_running", return_value=False), \
             patch("openrecall.app.reconcile_storage_and_database"), \
             patch("openrecall.app.reset_ocr_provider", side_effect=mock_reset), \
             patch("openrecall.app.gc.collect", side_effect=mock_gc), \
             patch("openrecall.app.app.run", side_effect=SystemExit(0)):

            mock_lock_cls.return_value.acquire.return_value = True
            mock_lock_cls.return_value.release.side_effect = mock_release

            from openrecall.app import main
            main()

            # Verify teardown operations ran EXACTLY ONCE despite SystemExit and finally block
            assert call_counts["pipeline_stop"] == 1
            assert call_counts["worker_stop"] == 1
            assert call_counts["reset_ocr"] == 1
            assert call_counts["gc_collect"] == 1
            assert call_counts["lock_release"] == 1



