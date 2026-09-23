"""Platform abstraction layer for OpenRecall.

Provides decoupled interfaces for screen capture, window metadata, idle detection,
and startup integration across Linux, Windows, and macOS.
"""

import abc
import os
import re
import subprocess
import sys
import threading
import time
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


def is_frame_valid(img: Optional[np.ndarray]) -> bool:
    """Validates that a captured screen array contains actual non-black, non-empty desktop pixels."""
    if img is None or not isinstance(img, np.ndarray):
        return False
    if img.ndim < 3 or img.shape[2] < 3 or img.size == 0:
        return False
    if img.shape[0] < 100 or img.shape[1] < 100:
        return False
    # Check if array is completely pitch-black (max pixel value <= 1)
    if int(np.max(img)) <= 1:
        return False
    # Check for non-zero pixel variance (filters out flat single-color screens)
    if float(np.std(img)) < 0.1:
        return False
    return True


class MSSScreenCaptureProvider(ScreenCaptureProvider):
    """Default cross-platform screen capture provider powered by mss."""

    def take_screenshots(self, primary_only: bool = False) -> List[np.ndarray]:
        screenshots: List[np.ndarray] = []
        try:
            with mss.MSS() as sct:
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


class WaylandScreenCastCaptureProvider(ScreenCaptureProvider):
    """Linux Wayland screen capture provider powered by XDG Desktop Portal ScreenCast and PipeWire.

    Provides continuous, silent desktop frame sampling via PyGObject GStreamer (pipewiresrc -> appsink)
    without triggering OS camera shutter sounds or saving intermediate files to disk.
    """

    def __init__(self, appdata_dir: Optional[str] = None):
        self.appdata_dir = appdata_dir
        self._proc: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_count = 0
        self._lock = threading.Lock()
        self._is_degraded = False

    def take_screenshots(self, primary_only: bool = False) -> List[np.ndarray]:
        if self._is_degraded:
            return []

        if not self._ensure_started():
            return []

        # Allow initial frame to populate if process was just started
        start_time = time.time()
        while time.time() - start_time < 6.0:
            with self._lock:
                if self._latest_frame is not None:
                    break
            time.sleep(0.1)

        with self._lock:
            frame = self._latest_frame

        if frame is not None and is_frame_valid(frame):
            return [frame]
        return []

    def _ensure_started(self) -> bool:
        if self._proc is not None:
            if self._proc.poll() is None:
                return True
            else:
                code = self._proc.poll()
                if code in (2, 3):  # 2: no PyGObject/Gst, 3: no DBus
                    self._is_degraded = True
                    return False
                self._proc = None

        if not self.appdata_dir:
            try:
                from openrecall.config import appdata_folder
                self.appdata_dir = appdata_folder
            except Exception:
                self.appdata_dir = os.path.expanduser("~/.local/share/openrecall")

        session_file = os.path.join(self.appdata_dir, "portal_session.json")
        python_candidates = ["/usr/bin/python3", "/usr/bin/python", sys.executable]

        helper_code = "import dbus, dbus.mainloop.glib, os, sys, time, struct, json\nimport gi\ntry:\n    gi.require_version('Gst', '1.0')\n    from gi.repository import Gst, GLib\nexcept Exception:\n    sys.exit(2)\n\nGst.init(None)\ndbus.mainloop.glib.DBusGMainLoop(set_as_default=True)\ntry:\n    bus = dbus.SessionBus()\nexcept Exception:\n    sys.exit(3)\n\nsession_file = sys.argv[1] if len(sys.argv) > 1 else ''\nrestore_token = None\nif session_file and os.path.exists(session_file):\n    try:\n        with open(session_file, 'r') as f:\n            restore_token = json.load(f).get('restore_token')\n    except Exception:\n        pass\n\ntry:\n    portal = bus.get_object('org.freedesktop.portal.Desktop', '/org/freedesktop/portal/desktop')\n    screencast = dbus.Interface(portal, 'org.freedesktop.portal.ScreenCast')\nexcept Exception:\n    sys.exit(3)\n\nloop = GLib.MainLoop()\nstate = {'session_path': None, 'fd': None, 'serial': None}\n\ndef save_restore_token(token):\n    if session_file and token:\n        try:\n            os.makedirs(os.path.dirname(session_file), exist_ok=True)\n            with open(session_file, 'w') as f:\n                json.dump({'restore_token': str(token)}, f)\n        except Exception:\n            pass\n\ndef on_start(response, results):\n    if response == 0:\n        streams = results.get('streams', [])\n        new_token = results.get('restore_token')\n        if new_token:\n            save_restore_token(new_token)\n        if streams:\n            state['serial'] = streams[0][1].get('pipewire-serial', streams[0][0])\n            try:\n                fd_obj = screencast.OpenPipeWireRemote(state['session_path'], {})\n                state['fd'] = fd_obj.take()\n            except Exception:\n                pass\n    loop.quit()\n\ndef on_select_sources(response, results):\n    if response == 0:\n        opts = {'handle_token': f'req_start_{int(time.time())}'}\n        req = screencast.Start(state['session_path'], '', opts)\n        bus.add_signal_receiver(on_start, signal_name='Response', dbus_interface='org.freedesktop.portal.Request', path=req)\n    else:\n        loop.quit()\n\ndef on_create_session(response, results):\n    if response == 0 and 'session_handle' in results:\n        state['session_path'] = str(results['session_handle'])\n        opts = {'types': dbus.UInt32(1), 'multiple': dbus.Boolean(False), 'persist_mode': dbus.UInt32(2), 'handle_token': f'req_select_{int(time.time())}'}\n        if restore_token:\n            opts['restore_token'] = dbus.String(restore_token)\n        req = screencast.SelectSources(state['session_path'], opts)\n        bus.add_signal_receiver(on_select_sources, signal_name='Response', dbus_interface='org.freedesktop.portal.Request', path=req)\n    else:\n        loop.quit()\n\ntry:\n    req = screencast.CreateSession({'session_handle_token': f'sess_{int(time.time())}', 'handle_token': f'req_create_{int(time.time())}'})\n    bus.add_signal_receiver(on_create_session, signal_name='Response', dbus_interface='org.freedesktop.portal.Request', path=req)\n    timeout_id = GLib.timeout_add_seconds(15, loop.quit)\n    loop.run()\n    GLib.source_remove(timeout_id)\nexcept Exception:\n    sys.exit(4)\n\nif state['fd'] is None or state['serial'] is None:\n    if restore_token and session_file and os.path.exists(session_file):\n        try:\n            os.remove(session_file)\n        except Exception:\n            pass\n    sys.exit(5)\n\npipeline_str = 'pipewiresrc name=src keepalive-time=1000 always-copy=true ! videorate ! video/x-raw,framerate=1/2 ! videoconvert ! video/x-raw,format=RGB ! appsink name=sink max-buffers=1 drop=true emit-signals=true'\ntry:\n    pipeline = Gst.parse_launch(pipeline_str)\n    src = pipeline.get_by_name('src')\n    src.set_property('fd', state['fd'])\n    try:\n        src.set_property('path', str(state['serial']))\n    except Exception:\n        src.set_property('target-object', str(state['serial']))\n    sink = pipeline.get_by_name('sink')\n\n    def on_new_sample(sink):\n        sample = sink.emit('pull-sample')\n        if sample:\n            caps = sample.get_caps()\n            s = caps.get_structure(0)\n            w = s.get_int('width')[1]\n            h = s.get_int('height')[1]\n            buf = sample.get_buffer()\n            ok, map_info = buf.map(Gst.MapFlags.READ)\n            if ok:\n                raw_data = bytes(map_info.data)\n                buf.unmap(map_info)\n                hdr = b'FRAM' + struct.pack('<III', w, h, len(raw_data))\n                os.write(1, hdr + raw_data)\n        return Gst.FlowReturn.OK\n\n    sink.connect('new-sample', on_new_sample)\n    bus_gst = pipeline.get_bus()\n    bus_gst.add_signal_watch()\n    main_loop = GLib.MainLoop()\n    def on_bus_message(bus, msg):\n        if msg.type in (Gst.MessageType.ERROR, Gst.MessageType.EOS):\n            main_loop.quit()\n    bus_gst.connect('message', on_bus_message)\n    pipeline.set_state(Gst.State.PLAYING)\n    main_loop.run()\nexcept Exception:\n    sys.exit(6)\n"

        for python_bin in python_candidates:
            if not os.path.exists(python_bin):
                continue
            try:
                proc = subprocess.Popen(
                    [python_bin, "-c", helper_code, session_file],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                )
                self._proc = proc
                self._reader_thread = threading.Thread(
                    target=self._reader_loop, args=(proc,), daemon=True
                )
                self._reader_thread.start()
                return True
            except Exception:
                pass

        self._is_degraded = True
        return False

    def _reader_loop(self, proc: subprocess.Popen):
        import struct

        def _read_exact(stream, n):
            buf = bytearray()
            while len(buf) < n:
                try:
                    chunk = stream.read(n - len(buf))
                except Exception:
                    return None
                if not chunk:
                    return None
                buf.extend(chunk)
            return bytes(buf)

        try:
            while proc.poll() is None:
                magic = _read_exact(proc.stdout, 4)
                if magic != b"FRAM":
                    break
                hdr = _read_exact(proc.stdout, 12)
                if not hdr or len(hdr) < 12:
                    break
                w, h, data_len = struct.unpack("<III", hdr)
                if w <= 0 or h <= 0 or w > 7680 or h > 4320 or data_len > 100_000_000:
                    break
                raw_data = _read_exact(proc.stdout, data_len)
                if raw_data and len(raw_data) == data_len:
                    expected_packed = w * h * 3
                    if len(raw_data) == expected_packed:
                        arr = np.frombuffer(raw_data, dtype=np.uint8).reshape((h, w, 3))
                    elif len(raw_data) > expected_packed and h > 0:
                        stride = len(raw_data) // h
                        arr = np.frombuffer(raw_data, dtype=np.uint8).reshape((h, stride))[:, :w * 3].reshape((h, w, 3))
                    else:
                        continue
                    with self._lock:
                        self._latest_frame = arr
                        self._frame_count += 1
        except Exception:
            pass

    def stop(self):
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=2)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    def __del__(self):
        self.stop()


_screen_capture_provider_instance = None


def get_screen_capture_provider() -> ScreenCaptureProvider:
    """Returns the active screen capture provider instance for the current OS."""
    global _screen_capture_provider_instance
    if _screen_capture_provider_instance is None:
        if sys.platform.startswith("linux"):
            is_wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
            if is_wayland:
                _screen_capture_provider_instance = WaylandScreenCastCaptureProvider()
            else:
                _screen_capture_provider_instance = MSSScreenCaptureProvider()
        else:
            _screen_capture_provider_instance = MSSScreenCaptureProvider()
    return _screen_capture_provider_instance


def _get_autostart_command() -> str:
    """Helper function to resolve executable command for OS autostart registration."""
    import shutil
    if sys.platform == "win32":
        bg_bin = shutil.which("openrecall-bg")
        if bg_bin:
            return f'"{os.path.normpath(bg_bin)}" --background'
        python_dir = os.path.dirname(sys.executable) if sys.executable else ""
        pythonw = os.path.join(python_dir, "pythonw.exe") if python_dir else ""
        if os.path.exists(pythonw):
            return f'"{os.path.normpath(pythonw)}" -m openrecall.app --background'

    openrecall_bin = shutil.which("openrecall")
    if openrecall_bin:
        return f'"{os.path.normpath(openrecall_bin)}" --background'
    python_bin = sys.executable or "python"
    return f'"{os.path.normpath(python_bin)}" -m openrecall.app --background'


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
                program_args = [os.path.normpath(openrecall_bin), "--background"]
            else:
                python_bin = sys.executable or "python"
                program_args = [os.path.normpath(python_bin), "-m", "openrecall.app", "--background"]

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
