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
    WindowsPlatformProvider,
    get_platform_provider,
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


if __name__ == "__main__":
    unittest.main()
