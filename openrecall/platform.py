"""Platform abstraction layer for OpenRecall.

Provides decoupled interfaces for screen capture, window metadata, idle detection,
and startup integration across Linux, Windows, and macOS.
"""

import abc
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import List, Optional

import mss
import numpy as np

# Optional imports for Windows/macOS
try:
    import psutil
    import win32api
    import win32gui
    import win32process
except ImportError:
    psutil = None
    win32gui = None
    win32process = None
    win32api = None

try:
    from AppKit import NSWorkspace
except ImportError:
    NSWorkspace = None

try:
    from Quartz import (
        CGWindowListCopyWindowInfo,
        kCGNullWindowID,
        kCGWindowListOptionOnScreenOnly,
    )
except ImportError:
    CGWindowListCopyWindowInfo = None
    kCGNullWindowID = None
    kCGWindowListOptionOnScreenOnly = None


@dataclass(frozen=True)
class CaptureMetadata:
    """Immutable data model representing metadata attached to a captured frame."""

    timestamp: int
    app_name: Optional[str] = None
    window_title: Optional[str] = None
    monitor_index: int = 1
    platform_name: str = sys.platform


class ScreenCaptureProvider(abc.ABC):
    """Abstract provider for capturing monitor screenshots."""

    @abc.abstractmethod
    def take_screenshots(self, primary_only: bool = False) -> List[np.ndarray]:
        """Captures screenshots of connected monitors as RGB NumPy arrays."""
        pass


class WindowMetadataProvider(abc.ABC):
    """Abstract provider for retrieving active window metadata."""

    @abc.abstractmethod
    def get_active_app_name(self) -> Optional[str]:
        """Returns the active application name, or None if unavailable."""
        pass

    @abc.abstractmethod
    def get_active_window_title(self) -> Optional[str]:
        """Returns the active window title, or None if unavailable."""
        pass


class IdleDetectionProvider(abc.ABC):
    """Abstract provider for checking user idle time."""

    @abc.abstractmethod
    def get_idle_seconds(self) -> Optional[float]:
        """Returns seconds since last user input, or None if unavailable."""
        pass

    def is_user_active(self, threshold_seconds: float = 5.0) -> bool:
        """Returns True if user was active within threshold_seconds (default 5s)."""
        idle = self.get_idle_seconds()
        if idle is None:
            return True  # Graceful fallback if idle check is unsupported
        return idle < threshold_seconds


class StartupIntegrationProvider(abc.ABC):
    """Abstract provider for OS startup registration."""

    @abc.abstractmethod
    def enable_startup(self) -> bool:
        """Enables launching OpenRecall on system startup."""
        pass

    @abc.abstractmethod
    def disable_startup(self) -> bool:
        """Disables launching OpenRecall on system startup."""
        pass

    @abc.abstractmethod
    def is_startup_enabled(self) -> bool:
        """Checks if OpenRecall is configured to launch on system startup."""
        pass


class MSSScreenCaptureProvider(ScreenCaptureProvider):
    """Default cross-platform screen capture provider powered by mss."""

    def take_screenshots(self, primary_only: bool = False) -> List[np.ndarray]:
        screenshots: List[np.ndarray] = []
        try:
            with mss.mss() as sct:
                monitor_indices = [1] if primary_only else range(1, len(sct.monitors))
                for idx in monitor_indices:
                    if idx < len(sct.monitors):
                        monitor_info = sct.monitors[idx]
                        sct_img = sct.grab(monitor_info)
                        screenshot = np.array(sct_img)[:, :, [2, 1, 0]]  # BGRA to RGB
                        screenshots.append(screenshot)
        except Exception:
            return []
        return screenshots


def _get_autostart_command() -> str:
    """Helper function to resolve executable command for OS autostart registration."""
    import shutil
    openrecall_bin = shutil.which("openrecall")
    if openrecall_bin:
        return f'"{os.path.normpath(openrecall_bin)}"'
    python_bin = sys.executable or "python"
    return f'"{os.path.normpath(python_bin)}" -m openrecall.app'


class LinuxPlatformProvider(
    WindowMetadataProvider, IdleDetectionProvider, StartupIntegrationProvider
):
    """Linux platform backend supporting X11 and Wayland graceful fallback."""

    def __init__(self):
        self.is_wayland = bool(os.environ.get("WAYLAND_DISPLAY"))

    def get_active_app_name(self) -> Optional[str]:
        if self.is_wayland:
            return self._get_active_app_name_proc_fallback()
        return self._get_active_app_name_x11()

    def get_active_window_title(self) -> Optional[str]:
        if self.is_wayland:
            return None  # Graceful degradation on Wayland
        return self._get_active_window_title_x11()

    def get_idle_seconds(self) -> Optional[float]:
        try:
            output = subprocess.check_output(["xprintidle"], timeout=1).decode()
            return int(output.strip()) / 1000.0
        except Exception:
            return None

    def enable_startup(self) -> bool:
        try:
            autostart_dir = os.path.expanduser("~/.config/autostart")
            os.makedirs(autostart_dir, exist_ok=True)
            desktop_file = os.path.join(autostart_dir, "openrecall.desktop")
            cmd = _get_autostart_command()
            with open(desktop_file, "w") as f:
                f.write(
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=OpenRecall\n"
                    f"Exec={cmd}\n"
                    "Hidden=false\n"
                    "NoDisplay=false\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
            return True
        except Exception:
            return False

    def disable_startup(self) -> bool:
        try:
            desktop_file = os.path.expanduser("~/.config/autostart/openrecall.desktop")
            if os.path.exists(desktop_file):
                os.remove(desktop_file)
            return True
        except Exception:
            return False

    def is_startup_enabled(self) -> bool:
        desktop_file = os.path.expanduser("~/.config/autostart/openrecall.desktop")
        return os.path.exists(desktop_file)

    def _get_active_app_name_x11(self) -> Optional[str]:
        try:
            proc = subprocess.Popen(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = proc.communicate(timeout=1)
            if proc.returncode != 0:
                return None
            match = re.search(rb"window id # (0x[0-9a-fA-F]+)", stdout)
            if not match:
                return None
            window_id = match.group(1).decode("utf-8")

            class_proc = subprocess.Popen(
                ["xprop", "-id", window_id, "WM_CLASS"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = class_proc.communicate(timeout=1)
            if class_proc.returncode != 0:
                return None
            match = re.search(
                rb'WM_CLASS\(STRING\) = "([^"]+)"(?:, "([^"]+)")?', stdout
            )
            if match:
                return match.group(1).decode("utf-8")
        except Exception:
            pass
        return None

    def _get_active_window_title_x11(self) -> Optional[str]:
        try:
            proc = subprocess.Popen(
                ["xprop", "-root", "_NET_ACTIVE_WINDOW"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = proc.communicate(timeout=1)
            if proc.returncode != 0:
                return None
            match = re.search(rb"window id # (0x[0-9a-fA-F]+)", stdout)
            if not match:
                return None
            window_id = match.group(1).decode("utf-8")

            for prop in ["_NET_WM_NAME", "WM_NAME"]:
                title_proc = subprocess.Popen(
                    ["xprop", "-id", window_id, prop],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                stdout, _ = title_proc.communicate(timeout=1)
                if title_proc.returncode == 0:
                    match = re.search(rb' = "([^"]*)"', stdout)
                    if match:
                        return match.group(1).decode("utf-8", errors="replace")
        except Exception:
            pass
        return None

    def _get_active_app_name_proc_fallback(self) -> Optional[str]:
        return None


class WindowsPlatformProvider(
    WindowMetadataProvider, IdleDetectionProvider, StartupIntegrationProvider
):
    """Windows platform backend (verified when pywin32 is present)."""

    def get_active_app_name(self) -> Optional[str]:
        if not all([psutil, win32gui, win32process]):
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if not pid:
                return None
            return psutil.Process(pid).name()
        except Exception:
            return None

    def get_active_window_title(self) -> Optional[str]:
        if win32gui is None:
            return None
        try:
            hwnd = win32gui.GetForegroundWindow()
            if not hwnd:
                return None
            return win32gui.GetWindowText(hwnd) or None
        except Exception:
            return None

    def get_idle_seconds(self) -> Optional[float]:
        if win32api is None:
            return None
        try:
            last_input = win32api.GetLastInputInfo()
            current_tick = win32api.GetTickCount()
            return (current_tick - last_input) / 1000.0
        except Exception:
            return None

    def enable_startup(self) -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            cmd = _get_autostart_command()
            winreg.SetValueEx(key, "OpenRecall", 0, winreg.REG_SZ, cmd)
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def disable_startup(self) -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_SET_VALUE,
            )
            try:
                winreg.DeleteValue(key, "OpenRecall")
            except FileNotFoundError:
                pass
            winreg.CloseKey(key)
            return True
        except Exception:
            return False

    def is_startup_enabled(self) -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Run",
                0,
                winreg.KEY_READ,
            )
            try:
                val, _ = winreg.QueryValueEx(key, "OpenRecall")
                winreg.CloseKey(key)
                return bool(val)
            except FileNotFoundError:
                winreg.CloseKey(key)
                return False
        except Exception:
            return False


class MacOSPlatformProvider(
    WindowMetadataProvider, IdleDetectionProvider, StartupIntegrationProvider
):
    """macOS platform backend (verified when pyobjc is present)."""

    def _get_launchagent_path(self) -> str:
        home = os.path.expanduser("~")
        return os.path.join(home, "Library", "LaunchAgents", "com.openrecall.app.plist")

    def get_active_app_name(self) -> Optional[str]:
        if NSWorkspace is None:
            return None
        try:
            active_app = NSWorkspace.sharedWorkspace().activeApplication()
            return active_app.get("NSApplicationName")
        except Exception:
            return None

    def get_active_window_title(self) -> Optional[str]:
        if CGWindowListCopyWindowInfo is None:
            return None
        try:
            app_name = self.get_active_app_name()
            if not app_name:
                return None
            window_list = CGWindowListCopyWindowInfo(
                kCGWindowListOptionOnScreenOnly, kCGNullWindowID
            )
            for window in window_list:
                if (
                    window.get("kCGWindowOwnerName") == app_name
                    and window.get("kCGWindowLayer") == 0
                ):
                    title = window.get("kCGWindowName")
                    if title:
                        return title
        except Exception:
            pass
        return None

    def get_idle_seconds(self) -> Optional[float]:
        try:
            output = subprocess.check_output(
                ["ioreg", "-c", "IOHIDSystem", "-r", "-k", "HIDIdleTime"], timeout=1
            ).decode()
            for line in output.splitlines():
                if "HIDIdleTime" in line:
                    return int(line.split("=")[-1].strip()) / 1_000_000_000.0
        except Exception:
            pass
        return None

    def enable_startup(self) -> bool:
        try:
            plist_path = self._get_launchagent_path()
            dir_name = os.path.dirname(plist_path)
            if not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)

            import shutil

            openrecall_bin = shutil.which("openrecall")
            if openrecall_bin:
                program_args = [os.path.normpath(openrecall_bin)]
            else:
                python_bin = sys.executable or "python"
                program_args = [os.path.normpath(python_bin), "-m", "openrecall.app"]

            plist_content = (
                '<?xml version="1.0" encoding="UTF-8"?>\n'
                '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
                '<plist version="1.0">\n'
                '<dict>\n'
                '    <key>Label</key>\n'
                '    <string>com.openrecall.app</string>\n'
                '    <key>ProgramArguments</key>\n'
                '    <array>\n'
            )
            for arg in program_args:
                plist_content += f'        <string>{arg}</string>\n'
            plist_content += (
                '    </array>\n'
                '    <key>RunAtLoad</key>\n'
                '    <true/>\n'
                '    <key>KeepAlive</key>\n'
                '    <false/>\n'
                '</dict>\n'
                '</plist>\n'
            )

            with open(plist_path, "w", encoding="utf-8") as f:
                f.write(plist_content)
            return True
        except Exception:
            return False

    def disable_startup(self) -> bool:
        try:
            plist_path = self._get_launchagent_path()
            if os.path.exists(plist_path):
                os.remove(plist_path)
            return True
        except Exception:
            return False

    def is_startup_enabled(self) -> bool:
        plist_path = self._get_launchagent_path()
        return os.path.exists(plist_path)


class FallbackPlatformProvider(
    WindowMetadataProvider, IdleDetectionProvider, StartupIntegrationProvider
):
    """Safe fallback provider used for unsupported platforms or missing OS tools."""

    def get_active_app_name(self) -> Optional[str]:
        return None

    def get_active_window_title(self) -> Optional[str]:
        return None

    def get_idle_seconds(self) -> Optional[float]:
        return 0.0

    def enable_startup(self) -> bool:
        return False

    def disable_startup(self) -> bool:
        return False

    def is_startup_enabled(self) -> bool:
        return False


_platform_provider_instance = None


def get_platform_provider():
    """Returns the active platform provider instance for the current OS."""
    global _platform_provider_instance
    if _platform_provider_instance is None:
        if sys.platform.startswith("linux"):
            _platform_provider_instance = LinuxPlatformProvider()
        elif sys.platform == "win32":
            _platform_provider_instance = WindowsPlatformProvider()
        elif sys.platform == "darwin":
            _platform_provider_instance = MacOSPlatformProvider()
        else:
            _platform_provider_instance = FallbackPlatformProvider()
    return _platform_provider_instance
