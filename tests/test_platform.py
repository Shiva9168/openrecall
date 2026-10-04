import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from openrecall.platform import (
    CaptureMetadata,
    FallbackPlatformProvider,
    LinuxPlatformProvider,
    MacOSPlatformProvider,
    MSSScreenCaptureProvider,
    WaylandScreenCastCaptureProvider,
    WindowsPlatformProvider,
    get_platform_provider,
    get_screen_capture_provider,
    is_frame_valid,
)


class TestPlatformAbstractions(unittest.TestCase):

    def test_capture_metadata_dataclass(self):
        meta = CaptureMetadata(
            timestamp=123456789,
            app_name="Firefox",
            window_title="OpenRecall - GitHub",
            monitor_index=1,
        )
        self.assertEqual(meta.timestamp, 123456789)
        self.assertEqual(meta.app_name, "Firefox")
        self.assertEqual(meta.window_title, "OpenRecall - GitHub")
        self.assertEqual(meta.monitor_index, 1)

    def test_capture_metadata_optional_defaults(self):
        meta = CaptureMetadata(timestamp=1000)
        self.assertIsNone(meta.app_name)
        self.assertIsNone(meta.window_title)
        self.assertEqual(meta.monitor_index, 1)

    def test_get_platform_provider_factory(self):
        with patch("sys.platform", "linux"):
            import openrecall.platform

            openrecall.platform._platform_provider_instance = None
            provider = get_platform_provider()
            self.assertIsInstance(provider, LinuxPlatformProvider)

        with patch("sys.platform", "win32"):
            openrecall.platform._platform_provider_instance = None
            provider = get_platform_provider()
            self.assertIsInstance(provider, WindowsPlatformProvider)

        with patch("sys.platform", "darwin"):
            openrecall.platform._platform_provider_instance = None
            provider = get_platform_provider()
            self.assertIsInstance(provider, MacOSPlatformProvider)

        with patch("sys.platform", "freebsd"):
            openrecall.platform._platform_provider_instance = None
            provider = get_platform_provider()
            self.assertIsInstance(provider, FallbackPlatformProvider)

        # Reset singleton back for current host
        openrecall.platform._platform_provider_instance = None

    def test_fallback_provider_graceful_degradation(self):
        fallback = FallbackPlatformProvider()
        self.assertIsNone(fallback.get_active_app_name())
        self.assertIsNone(fallback.get_active_window_title())
        self.assertEqual(fallback.get_idle_seconds(), 0.0)
        self.assertTrue(fallback.is_user_active())
        self.assertFalse(fallback.enable_startup())
        self.assertFalse(fallback.disable_startup())
        self.assertFalse(fallback.is_startup_enabled())

    def test_linux_wayland_degradation(self):
        with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            provider = LinuxPlatformProvider()
            self.assertTrue(provider.is_wayland)
            # Wayland window title should return None gracefully
            self.assertIsNone(provider.get_active_window_title())

    def test_linux_idle_detection_success(self):
        provider = LinuxPlatformProvider()
        with patch("subprocess.check_output", return_value=b"2000\n"):
            idle = provider.get_idle_seconds()
            self.assertEqual(idle, 2.0)
            self.assertTrue(provider.is_user_active(5.0))

    def test_linux_idle_detection_missing_tool(self):
        provider = LinuxPlatformProvider()
        with patch(
            "subprocess.check_output", side_effect=FileNotFoundError("xprintidle missing")
        ):
            idle = provider.get_idle_seconds()
            self.assertIsNone(idle)
            self.assertTrue(provider.is_user_active(5.0))

    def test_windows_provider_missing_dependencies(self):
        with patch("openrecall.platform.win32gui", None):
            win_provider = WindowsPlatformProvider()
            self.assertIsNone(win_provider.get_active_app_name())
            self.assertIsNone(win_provider.get_active_window_title())
            self.assertIsNone(win_provider.get_idle_seconds())

    def test_macos_provider_missing_dependencies(self):
        with patch("openrecall.platform.NSWorkspace", None):
            mac_provider = MacOSPlatformProvider()
            self.assertIsNone(mac_provider.get_active_app_name())
            self.assertIsNone(mac_provider.get_active_window_title())

    def test_mss_screen_capture_mocked(self):
        mock_sct_instance = MagicMock()
        mock_sct_instance.monitors = [{}, {"width": 10, "height": 10}]
        bgra_img = np.zeros((10, 10, 4), dtype=np.uint8)
        mock_sct_instance.grab.return_value = bgra_img

        mock_mss_context = MagicMock()
        mock_mss_context.__enter__.return_value = mock_sct_instance

        with patch("mss.MSS", return_value=mock_mss_context):
            provider = MSSScreenCaptureProvider()
            shots = provider.take_screenshots(primary_only=True)
            self.assertEqual(len(shots), 1)
            self.assertEqual(shots[0].shape, (10, 10, 3))

    def test_windows_autostart_winreg_mocked(self):
        mock_winreg = MagicMock()
        mock_key = MagicMock()
        mock_winreg.OpenKey.return_value = mock_key
        mock_winreg.QueryValueEx.return_value = ("openrecall", 1)

        with patch.dict(sys.modules, {"winreg": mock_winreg}):
            win_provider = WindowsPlatformProvider()
            self.assertTrue(win_provider.enable_startup())
            self.assertTrue(win_provider.is_startup_enabled())
            self.assertTrue(win_provider.disable_startup())

        mock_winreg.SetValueEx.assert_called_once()
        mock_winreg.DeleteValue.assert_called_once()

    def test_macos_autostart_launchagent_mocked(self):
        mac_provider = MacOSPlatformProvider()

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", MagicMock()), \
             patch("os.makedirs"):
            self.assertTrue(mac_provider.is_startup_enabled())
            self.assertTrue(mac_provider.enable_startup())

        with patch("os.path.exists", return_value=True), \
             patch("os.remove") as mock_remove:
            self.assertTrue(mac_provider.disable_startup())
            mock_remove.assert_called_once()

    def test_linux_autostart_desktop_entry_mocked(self):
        linux_provider = LinuxPlatformProvider()

        with patch("os.path.exists", return_value=True), \
             patch("builtins.open", MagicMock()), \
             patch("os.makedirs"):
            self.assertTrue(linux_provider.is_startup_enabled())
            self.assertTrue(linux_provider.enable_startup())

        with patch("os.path.exists", return_value=True), \
             patch("os.remove") as mock_remove:
            self.assertTrue(linux_provider.disable_startup())
            mock_remove.assert_called_once()

    def test_autostart_command_persistence_options(self):
        """Test Phase 13.9 persistent autostart runtime options generation."""
        from openrecall.platform import _get_autostart_command
        from openrecall.config import parser

        # 1. Default invocation (no extra flags)
        args_default = parser.parse_args([])
        cmd_def = _get_autostart_command(parsed_args=args_default)
        self.assertIn("--background", cmd_def)
        self.assertNotIn("--disable-ocr", cmd_def)
        self.assertNotIn("--ocr-engine", cmd_def)

        # 2. Storage path persistence with spaces
        custom_path = "/data/my custom openrecall/path"
        args_storage = parser.parse_args(["--storage-path", custom_path])
        cmd_storage = _get_autostart_command(storage_path=custom_path, parsed_args=args_storage)
        self.assertIn(f'--storage-path "{os.path.abspath(custom_path)}"', cmd_storage)

        # 3. Disable OCR persistence
        args_no_ocr = parser.parse_args(["--disable-ocr"])
        cmd_no_ocr = _get_autostart_command(parsed_args=args_no_ocr)
        self.assertIn("--disable-ocr", cmd_no_ocr)

        # 4. OCR Engine RapidOCR
        args_rapid = parser.parse_args(["--ocr-engine", "rapidocr"])
        cmd_rapid = _get_autostart_command(parsed_args=args_rapid)
        self.assertIn("--ocr-engine rapidocr", cmd_rapid)

        # 5. OCR Engine Tesseract
        args_tess = parser.parse_args(["--ocr-engine", "tesseract"])
        cmd_tess = _get_autostart_command(parsed_args=args_tess)
        self.assertIn("--ocr-engine tesseract", cmd_tess)

        # 6. OCR Threads
        args_threads = parser.parse_args(["--ocr-engine", "rapidocr", "--ocr-threads", "2"])
        cmd_threads = _get_autostart_command(parsed_args=args_threads)
        self.assertIn("--ocr-engine rapidocr", cmd_threads)
        self.assertIn("--ocr-threads 2", cmd_threads)

        # 7. Combined options: Storage + Max Storage GB + Primary Monitor Only
        args_combined = parser.parse_args([
            "--storage-path", custom_path,
            "--max-storage-gb", "5.0",
            "--primary-monitor-only",
        ])
        cmd_combined = _get_autostart_command(storage_path=custom_path, parsed_args=args_combined)
        self.assertIn(f'--storage-path "{os.path.abspath(custom_path)}"', cmd_combined)
        self.assertIn("--max-storage-gb 5.0", cmd_combined)
        self.assertIn("--primary-monitor-only", cmd_combined)

    def test_autostart_command_update_and_reset_semantics(self):
        """Test Phase 13.9 autostart update and reset replacement semantics."""
        from openrecall.platform import _get_autostart_command
        from openrecall.config import parser

        # Step 1: Configure autostart with --disable-ocr
        args_step1 = parser.parse_args(["--disable-ocr"])
        cmd1 = _get_autostart_command(parsed_args=args_step1)
        self.assertIn("--disable-ocr", cmd1)

        # Step 2: Update autostart with --ocr-engine rapidocr (replaces --disable-ocr)
        args_step2 = parser.parse_args(["--ocr-engine", "rapidocr"])
        cmd2 = _get_autostart_command(parsed_args=args_step2)
        self.assertIn("--ocr-engine rapidocr", cmd2)
        self.assertNotIn("--disable-ocr", cmd2)

        # Step 3: Reset autostart with default openrecall (replaces --ocr-engine rapidocr)
        args_step3 = parser.parse_args([])
        cmd3 = _get_autostart_command(parsed_args=args_step3)
        self.assertNotIn("--ocr-engine", cmd3)
        self.assertNotIn("--disable-ocr", cmd3)

    def test_conflicting_ocr_options_rejected(self):
        """Test Phase 13.9 CLI rejection of conflicting OCR options."""
        from openrecall.config import parser
        with patch("sys.stderr", MagicMock()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--disable-ocr", "--ocr-engine", "rapidocr"])

            with self.assertRaises(SystemExit):
                parser.parse_args(["--disable-ocr", "--ocr-threads", "2"])

    def test_is_frame_valid(self):
        self.assertFalse(is_frame_valid(None))
        self.assertFalse(is_frame_valid("invalid"))
        # Invalid dimensions
        self.assertFalse(is_frame_valid(np.zeros((10, 10), dtype=np.uint8)))
        self.assertFalse(is_frame_valid(np.zeros((10, 10, 3), dtype=np.uint8)))
        # Pitch black screen (max <= 1)
        black_screen = np.zeros((200, 200, 3), dtype=np.uint8)
        self.assertFalse(is_frame_valid(black_screen))
        # Flat single color screen (std < 0.1)
        flat_screen = np.full((200, 200, 3), 128, dtype=np.uint8)
        self.assertFalse(is_frame_valid(flat_screen))
        # Valid desktop frame
        valid_frame = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
        self.assertTrue(is_frame_valid(valid_frame))

    def test_get_screen_capture_provider(self):
        import openrecall.platform
        openrecall.platform._screen_capture_provider_instance = None
        with patch("sys.platform", "linux"), patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}):
            openrecall.platform._screen_capture_provider_instance = None
            provider = get_screen_capture_provider()
            self.assertIsInstance(provider, WaylandScreenCastCaptureProvider)

        openrecall.platform._screen_capture_provider_instance = None
        with patch("sys.platform", "linux"), patch.dict(os.environ, {"WAYLAND_DISPLAY": "", "XDG_SESSION_TYPE": "x11"}):
            openrecall.platform._screen_capture_provider_instance = None
            provider = get_screen_capture_provider()
            self.assertIsInstance(provider, MSSScreenCaptureProvider)

        openrecall.platform._screen_capture_provider_instance = None

    def test_wayland_screencast_capture_provider_degraded_state(self):
        provider = WaylandScreenCastCaptureProvider()
        provider._is_degraded = True
        shots = provider.take_screenshots()
        self.assertEqual(shots, [])  # Returns empty list without calling MSS

    def test_wayland_screencast_persistent_process_single_popen(self):
        valid_frame = np.random.randint(10, 250, (100, 100, 3), dtype=np.uint8)
        provider = WaylandScreenCastCaptureProvider()

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None

        with patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
             patch("os.path.exists", return_value=True), \
             patch("threading.Thread"):
            provider._latest_frame = valid_frame

            # Multiple capture calls
            shots1 = provider.take_screenshots()
            shots2 = provider.take_screenshots()
            shots3 = provider.take_screenshots()

            self.assertEqual(len(shots1), 1)
            self.assertEqual(len(shots2), 1)
            self.assertEqual(len(shots3), 1)
            # Popen must be called ONCE, not per capture interval
            mock_popen.assert_called_once()

    def test_wayland_screencast_degraded_on_dependency_exit(self):
        provider = WaylandScreenCastCaptureProvider()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 2  # Missing GStreamer/PyGObject code 2

        provider._proc = mock_proc
        shots = provider.take_screenshots()
        self.assertEqual(shots, [])
        self.assertTrue(provider._is_degraded)

    def test_wayland_screencast_stop_cleanup(self):
        provider = WaylandScreenCastCaptureProvider()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        provider._proc = mock_proc

        provider.stop()
        mock_proc.terminate.assert_called_once()
        self.assertIsNone(provider._proc)

    def test_wayland_screencast_reader_loop_malformed_header(self):
        import io
        import struct

        provider = WaylandScreenCastCaptureProvider()
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, None, 0]

        # Send invalid magic header
        mock_proc.stdout = io.BytesIO(b"BADM" + struct.pack("<III", 100, 100, 30000))
        provider._reader_loop(mock_proc)
        self.assertIsNone(provider._latest_frame)

    def test_wayland_screencast_reader_loop_oversized_bounds(self):
        import io
        import struct

        provider = WaylandScreenCastCaptureProvider()
        mock_proc = MagicMock()
        mock_proc.poll.side_effect = [None, None, 0]

        # Send frame size exceeding 100MB bound (e.g., 20000x20000)
        mock_proc.stdout = io.BytesIO(b"FRAM" + struct.pack("<III", 20000, 20000, 120_000_000))
        provider._reader_loop(mock_proc)
        self.assertIsNone(provider._latest_frame)

    def test_wayland_screencast_capture_provider_valid_frame(self):
        valid_frame = np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8)
        provider = WaylandScreenCastCaptureProvider()
        with patch.object(provider, "_ensure_started", return_value=True):
            provider._latest_frame = valid_frame
            shots = provider.take_screenshots()
            self.assertEqual(len(shots), 1)
            self.assertEqual(shots[0].shape, (200, 200, 3))


if __name__ == "__main__":
    unittest.main()
