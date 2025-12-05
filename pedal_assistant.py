"""
PedalAssistant - Game Controller Axis Monitor with Audio Alerts
Supports multiple handlers per axis with individual threshold and sound settings.
"""

import customtkinter as ctk
import pygame
import numpy as np
import sounddevice as sd
import threading
import time
import uuid
import json
import os
import sys
import winreg
from typing import Dict, Optional, List, Callable
from dataclasses import dataclass, field, asdict
import pystray
from PIL import Image, ImageDraw
import comtypes
from comtypes import CLSCTX_ALL, GUID
from ctypes import POINTER, cast, HRESULT, c_void_p, c_wchar_p, windll, byref, sizeof, Structure, create_string_buffer, WINFUNCTYPE, c_long, c_int
from ctypes.wintypes import DWORD, LPCWSTR, HWND, UINT, WPARAM, LPARAM, HANDLE, BOOL, LPVOID, MSG

# Autostart registry key
AUTOSTART_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_APP_NAME = "PedalAssistant"

# Settings file path
SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

# Configure appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# Localization
TRANSLATIONS = {
    "en": {
        "app_title": "PedalAssistant - Game Controller Axis Monitor",
        "header": "🎮 PedalAssistant",
        "subtitle": "Axis monitor with customizable audio alerts",
        "game_device": "Game device:",
        "no_devices": "No devices connected",
        "device_axes": "Device axes:",
        "select_device": "Select a device from the list above",
        "no_axes": "Device has no axes",
        "add_handler": "+ Add handler",
        "delete_handler": "Delete handler",
        "restart_app": "Restart application",
        "save_settings": "Save settings",
        "load_settings": "Load settings",
        "autostart": "Autostart",
        "autostart_tooltip": "Start minimized when system boots",
        "range": "Range:",
        "frequency": "Freq:",
        "volume": "Vol:",
        "waveform": "Wave:",
        "show_hide": "Show/Hide",
        "exit": "Exit",
        "axis": "Axis",
        "about": "© 2025 Ilya Marchenko (newilia)\ngithub.com/newilia/PedalAssistant",
    },
    "ru": {
        "app_title": "PedalAssistant - Монитор осей игровых устройств",
        "header": "🎮 PedalAssistant",
        "subtitle": "Монитор осей с настраиваемыми звуковыми оповещениями",
        "game_device": "Игровое устройство:",
        "no_devices": "Нет подключенных устройств",
        "device_axes": "Оси устройства:",
        "select_device": "Выберите устройство из списка выше",
        "no_axes": "Устройство не имеет осей",
        "add_handler": "+ Добавить обработчик",
        "delete_handler": "Удалить обработчик",
        "restart_app": "Перезапустить программу",
        "save_settings": "Сохранить настройки",
        "load_settings": "Загрузить настройки",
        "autostart": "Автозапуск",
        "autostart_tooltip": "Запускать свёрнутым при старте системы",
        "range": "Диапазон:",
        "frequency": "Частота:",
        "volume": "Громк:",
        "waveform": "Форма:",
        "show_hide": "Показать/Скрыть",
        "exit": "Выход",
        "axis": "Ось",
        "about": "© 2025 Ilya Marchenko (newilia)\ngithub.com/newilia/PedalAssistant",
    }
}

# Current language (will be loaded from settings)
current_language = "en"


def tr(key: str) -> str:
    """Get translated string for current language."""
    return TRANSLATIONS.get(current_language, TRANSLATIONS["en"]).get(key, key)


# Windows Core Audio API interfaces for device change notifications
class PROPERTYKEY(comtypes.Structure):
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class IMMNotificationClient(comtypes.IUnknown):
    _iid_ = GUID("{7991EEC9-7E89-4D85-8390-6C703CEC60C0}")
    _methods_ = [
        comtypes.COMMETHOD([], HRESULT, "OnDeviceStateChanged",
                          (["in"], LPCWSTR, "pwstrDeviceId"),
                          (["in"], DWORD, "dwNewState")),
        comtypes.COMMETHOD([], HRESULT, "OnDeviceAdded",
                          (["in"], LPCWSTR, "pwstrDeviceId")),
        comtypes.COMMETHOD([], HRESULT, "OnDeviceRemoved",
                          (["in"], LPCWSTR, "pwstrDeviceId")),
        comtypes.COMMETHOD([], HRESULT, "OnDefaultDeviceChanged",
                          (["in"], DWORD, "flow"),
                          (["in"], DWORD, "role"),
                          (["in"], LPCWSTR, "pwstrDefaultDeviceId")),
        comtypes.COMMETHOD([], HRESULT, "OnPropertyValueChanged",
                          (["in"], LPCWSTR, "pwstrDeviceId"),
                          (["in"], PROPERTYKEY, "key")),
    ]


class IMMDeviceEnumerator(comtypes.IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        comtypes.COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                          (["in"], DWORD, "dataFlow"),
                          (["in"], DWORD, "dwStateMask"),
                          (["out"], POINTER(c_void_p), "ppDevices")),
        comtypes.COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                          (["in"], DWORD, "dataFlow"),
                          (["in"], DWORD, "role"),
                          (["out"], POINTER(c_void_p), "ppEndpoint")),
        comtypes.COMMETHOD([], HRESULT, "GetDevice",
                          (["in"], LPCWSTR, "pwstrId"),
                          (["out"], POINTER(c_void_p), "ppDevice")),
        comtypes.COMMETHOD([], HRESULT, "RegisterEndpointNotificationCallback",
                          (["in"], POINTER(IMMNotificationClient), "pClient")),
        comtypes.COMMETHOD([], HRESULT, "UnregisterEndpointNotificationCallback",
                          (["in"], POINTER(IMMNotificationClient), "pClient")),
    ]


class MMDeviceEnumerator(comtypes.CoClass):
    _reg_clsid_ = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    _com_interfaces_ = [IMMDeviceEnumerator]


class AudioDeviceNotificationClient(comtypes.COMObject):
    """COM object that receives audio device change notifications."""
    _com_interfaces_ = [IMMNotificationClient]
    
    def __init__(self, on_device_changed: Callable):
        super().__init__()
        self._on_device_changed = on_device_changed
    
    def OnDeviceStateChanged(self, pwstrDeviceId, dwNewState):
        return 0
    
    def OnDeviceAdded(self, pwstrDeviceId):
        return 0
    
    def OnDeviceRemoved(self, pwstrDeviceId):
        return 0
    
    def OnDefaultDeviceChanged(self, flow, role, pwstrDefaultDeviceId):
        # flow: 0 = eRender (output), 1 = eCapture (input)
        # role: 0 = eConsole, 1 = eMultimedia, 2 = eCommunications
        if flow == 0 and role == 0:  # Default output device for console apps
            self._on_device_changed()
        return 0
    
    def OnPropertyValueChanged(self, pwstrDeviceId, key):
        return 0


# Windows constants for device notifications
WM_DEVICECHANGE = 0x0219
DBT_DEVICEARRIVAL = 0x8000
DBT_DEVICEREMOVECOMPLETE = 0x8004
DBT_DEVTYP_DEVICEINTERFACE = 0x00000005
DEVICE_NOTIFY_WINDOW_HANDLE = 0x00000000

# HID device class GUID (for game controllers)
GUID_DEVINTERFACE_HID = GUID("{4D1E55B2-F16F-11CF-88CB-001111000030}")


class DEV_BROADCAST_DEVICEINTERFACE(Structure):
    _fields_ = [
        ("dbcc_size", DWORD),
        ("dbcc_devicetype", DWORD),
        ("dbcc_reserved", DWORD),
        ("dbcc_classguid", GUID),
        ("dbcc_name", c_wchar_p * 1),
    ]


class DeviceNotificationMonitor:
    """Monitors USB HID device connections/disconnections using Windows messages."""
    
    def __init__(self, on_device_change: Callable):
        self._on_device_change = on_device_change
        self._hwnd = None
        self._notification_handle = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._wndproc = None
    
    def start(self):
        """Start monitoring device changes in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop monitoring."""
        self._running = False
        if self._hwnd:
            try:
                windll.user32.PostMessageW(self._hwnd, 0x0012, 0, 0)  # WM_QUIT
            except Exception:
                pass
    
    def _message_loop(self):
        """Create hidden window and run message loop."""
        try:
            # Define window class
            WNDPROC = WINFUNCTYPE(c_long, HWND, UINT, WPARAM, LPARAM)
            
            class WNDCLASS(Structure):
                _fields_ = [
                    ("style", UINT),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", c_int),
                    ("cbWndExtra", c_int),
                    ("hInstance", HANDLE),
                    ("hIcon", HANDLE),
                    ("hCursor", HANDLE),
                    ("hbrBackground", HANDLE),
                    ("lpszMenuName", LPCWSTR),
                    ("lpszClassName", LPCWSTR),
                ]
            
            def wndproc(hwnd, msg, wparam, lparam):
                if msg == WM_DEVICECHANGE:
                    if wparam in (DBT_DEVICEARRIVAL, DBT_DEVICEREMOVECOMPLETE):
                        # Device connected or disconnected
                        self._on_device_change()
                return windll.user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            
            self._wndproc = WNDPROC(wndproc)
            
            hInstance = windll.kernel32.GetModuleHandleW(None)
            className = "PedalAssistantDeviceMonitor"
            
            wc = WNDCLASS()
            wc.lpfnWndProc = self._wndproc
            wc.hInstance = hInstance
            wc.lpszClassName = className
            
            if not windll.user32.RegisterClassW(byref(wc)):
                return
            
            # Create hidden window
            self._hwnd = windll.user32.CreateWindowExW(
                0, className, "DeviceMonitor", 0,
                0, 0, 0, 0, None, None, hInstance, None
            )
            
            if not self._hwnd:
                return
            
            # Register for HID device notifications
            dbi = DEV_BROADCAST_DEVICEINTERFACE()
            dbi.dbcc_size = sizeof(DEV_BROADCAST_DEVICEINTERFACE)
            dbi.dbcc_devicetype = DBT_DEVTYP_DEVICEINTERFACE
            dbi.dbcc_classguid = GUID_DEVINTERFACE_HID
            
            self._notification_handle = windll.user32.RegisterDeviceNotificationW(
                self._hwnd,
                byref(dbi),
                DEVICE_NOTIFY_WINDOW_HANDLE
            )
            
            # Message loop
            msg = MSG()
            while self._running:
                ret = windll.user32.GetMessageW(byref(msg), None, 0, 0)
                if ret <= 0:
                    break
                windll.user32.TranslateMessage(byref(msg))
                windll.user32.DispatchMessageW(byref(msg))
            
        except Exception as e:
            print(f"Device monitor error: {e}")
        finally:
            if self._notification_handle:
                windll.user32.UnregisterDeviceNotification(self._notification_handle)
            if self._hwnd:
                windll.user32.DestroyWindow(self._hwnd)


@dataclass
class AlertHandler:
    """Configuration for a single alert handler."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    min_threshold: float = 1.0
    max_threshold: float = 1.0
    frequency: int = 440
    volume: float = 0.5
    waveform: str = "sine"  # sine, sawtooth, square
    is_triggered: bool = False
    
    def check_trigger(self, value: float) -> bool:
        """Check if value is within threshold range."""
        return self.min_threshold <= value <= self.max_threshold


class AudioMixer:
    """Mixes and plays multiple tones simultaneously."""
    
    def __init__(self):
        self.sample_rate = 44100
        self._stream: Optional[sd.OutputStream] = None
        self._lock = threading.Lock()
        self._phases: Dict[str, float] = {}  # handler_id -> phase
        self._active_handlers: Dict[str, AlertHandler] = {}  # handler_id -> handler
        self._current_device_name: Optional[str] = None
        self._device_change_pending = False
        
        # Setup Windows audio device change notification
        self._notification_client: Optional[AudioDeviceNotificationClient] = None
        self._device_enumerator = None
        self._setup_device_notifications()
        
        self._open_stream()
    
    def _setup_device_notifications(self):
        """Setup COM notifications for audio device changes."""
        try:
            comtypes.CoInitialize()
            self._device_enumerator = comtypes.CoCreateInstance(
                MMDeviceEnumerator._reg_clsid_,
                IMMDeviceEnumerator,
                CLSCTX_ALL
            )
            self._notification_client = AudioDeviceNotificationClient(
                self._on_device_changed_callback
            )
            self._device_enumerator.RegisterEndpointNotificationCallback(
                self._notification_client
            )
        except Exception as e:
            print(f"Failed to setup audio device notifications: {e}")
    
    def _on_device_changed_callback(self):
        """Called by COM when default audio device changes."""
        self._device_change_pending = True
    
    def _get_default_device_name(self) -> Optional[str]:
        """Get name of default output device."""
        try:
            device_idx = sd.default.device[1]
            if device_idx is not None:
                device_info = sd.query_devices(device_idx)
                return device_info.get('name')
            return None
        except Exception:
            return None
    
    def _generate_tone(self, handler: AlertHandler, frames: int) -> np.ndarray:
        """Generate samples for a single tone."""
        handler_id = handler.id
        if handler_id not in self._phases:
            self._phases[handler_id] = 0.0
        
        phase = self._phases[handler_id]
        t = (np.arange(frames) + phase) / self.sample_rate
        self._phases[handler_id] = (phase + frames) % self.sample_rate
        
        freq = handler.frequency
        if handler.waveform == "sine":
            samples = np.sin(2 * np.pi * freq * t)
        elif handler.waveform == "sawtooth":
            samples = 2 * (freq * t % 1) - 1
        elif handler.waveform == "square":
            samples = np.sign(np.sin(2 * np.pi * freq * t))
        else:
            samples = np.sin(2 * np.pi * freq * t)
        
        return samples * handler.volume
    
    def _audio_callback(self, outdata, frames, time_info, status):
        """Mix all active tones."""
        if self._lock.acquire(blocking=False):
            try:
                if self._active_handlers:
                    mixed = np.zeros(frames, dtype=np.float32)
                    for handler in self._active_handlers.values():
                        mixed += self._generate_tone(handler, frames)
                    
                    # Normalize to prevent clipping
                    max_val = np.max(np.abs(mixed))
                    if max_val > 1.0:
                        mixed /= max_val
                    
                    outdata[:, 0] = mixed.astype(np.float32)
                else:
                    outdata.fill(0)
            finally:
                self._lock.release()
        else:
            outdata.fill(0)
    
    def _close_stream(self):
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
    
    def _open_stream(self):
        try:
            self._current_device_name = self._get_default_device_name()
            self._stream = sd.OutputStream(
                samplerate=self.sample_rate,
                channels=1,
                callback=self._audio_callback,
                blocksize=512,
                latency='low',
                device=None
            )
            self._stream.start()
            return True
        except Exception as e:
            print(f"Audio error: {e}")
            self._stream = None
            return False
    
    def check_device_change(self):
        """Check if device change notification was received and handle it."""
        if not self._device_change_pending:
            return
        
        self._device_change_pending = False
        print("Audio device change detected, switching...")
        
        # Close stream
        self._close_stream()
        time.sleep(0.1)
        
        # Reinitialize sounddevice to get fresh device info
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        
        # Get new device name and reopen stream
        new_device_name = self._get_default_device_name()
        print(f"Audio device changed: {self._current_device_name} -> {new_device_name}")
        
        with self._lock:
            self._open_stream()
    
    def start_handler(self, handler: AlertHandler):
        """Start playing a handler's tone."""
        with self._lock:
            if handler.id not in self._active_handlers:
                self._phases[handler.id] = 0.0
                self._active_handlers[handler.id] = handler
    
    def stop_handler(self, handler_id: str):
        """Stop a handler's tone."""
        with self._lock:
            if handler_id in self._active_handlers:
                del self._active_handlers[handler_id]
    
    def update_handler(self, handler: AlertHandler):
        """Update handler settings if it's playing."""
        with self._lock:
            if handler.id in self._active_handlers:
                self._active_handlers[handler.id] = handler
    
    def reinitialize(self):
        """Force reinitialize audio stream to current default device."""
        # Close stream outside of lock to let callback finish
        self._close_stream()
        
        # Wait for stream to fully close
        time.sleep(0.1)
        
        # Force sounddevice to re-query audio devices
        try:
            sd._terminate()
            sd._initialize()
        except Exception:
            pass
        
        # Open new stream
        with self._lock:
            self._current_device_name = None
            self._phases.clear()
            self._open_stream()
    
    def cleanup(self):
        with self._lock:
            self._active_handlers.clear()
        self._close_stream()
        
        # Unregister device change notifications
        try:
            if self._device_enumerator and self._notification_client:
                self._device_enumerator.UnregisterEndpointNotificationCallback(
                    self._notification_client
                )
        except Exception:
            pass


class JoystickReader:
    """Reads joystick input in a background thread."""
    
    def __init__(self):
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._joystick: Optional[pygame.joystick.Joystick] = None
        self._axis_values: List[float] = []
        self._num_axes = 0
        
        pygame.init()
        pygame.joystick.init()
    
    def get_devices(self) -> List[str]:
        with self._lock:
            was_running = self._running
            if was_running:
                self._stop_thread()
            
            pygame.joystick.quit()
            pygame.joystick.init()
            
            devices = []
            for i in range(pygame.joystick.get_count()):
                try:
                    joy = pygame.joystick.Joystick(i)
                    devices.append(f"{i}: {joy.get_name()}")
                except pygame.error:
                    pass
            
            return devices
    
    def select_device(self, device_idx: int) -> int:
        with self._lock:
            self._stop_thread()
            
            try:
                self._joystick = pygame.joystick.Joystick(device_idx)
                self._joystick.init()
                self._num_axes = self._joystick.get_numaxes()
                self._axis_values = [0.0] * self._num_axes
                
                self._running = True
                self._thread = threading.Thread(target=self._read_loop, daemon=True)
                self._thread.start()
                
                return self._num_axes
            except pygame.error:
                self._joystick = None
                self._num_axes = 0
                self._axis_values = []
                return 0
    
    def clear_device(self):
        with self._lock:
            self._stop_thread()
            self._joystick = None
            self._num_axes = 0
            self._axis_values = []
    
    def _stop_thread(self):
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=0.5)
        self._thread = None
    
    def _read_loop(self):
        while self._running:
            try:
                pygame.event.pump()
                
                if self._joystick:
                    values = []
                    for i in range(self._num_axes):
                        raw = self._joystick.get_axis(i)
                        values.append((raw + 1.0) / 2.0)
                    
                    with self._lock:
                        self._axis_values = values
            except pygame.error:
                pass
            
            time.sleep(0.001)
    
    def get_axis_values(self) -> List[float]:
        with self._lock:
            return self._axis_values.copy()
    
    def cleanup(self):
        with self._lock:
            self._stop_thread()
        pygame.quit()


class CTkToolTip:
    """Simple tooltip for customtkinter widgets."""
    
    def __init__(self, widget, text: str, delay: int = 400):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tooltip_window = None
        self._after_id = None
        
        self.widget.bind("<Enter>", self._on_enter, add="+")
        self.widget.bind("<Leave>", self._on_leave, add="+")
        self.widget.bind("<ButtonPress>", self._on_leave, add="+")
    
    def _on_enter(self, event=None):
        self._cancel_scheduled()
        self._after_id = self.widget.after(self.delay, self._show_tooltip)
    
    def _on_leave(self, event=None):
        self._cancel_scheduled()
        self._hide_tooltip()
    
    def _cancel_scheduled(self):
        if self._after_id:
            self.widget.after_cancel(self._after_id)
            self._after_id = None
    
    def _show_tooltip(self):
        if self.tooltip_window:
            return
        
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        
        self.tooltip_window = tw = ctk.CTkToplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        
        # Tooltip label
        label = ctk.CTkLabel(
            tw,
            text=self.text,
            font=ctk.CTkFont(size=14),
            fg_color="#333333",
            corner_radius=6,
            text_color="#FFFFFF",
            padx=10,
            pady=5
        )
        label.pack()
        
        tw.update_idletasks()
        tw_width = tw.winfo_width()
        tw.wm_geometry(f"+{x - tw_width // 2}+{y}")
    
    def _hide_tooltip(self):
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None


class RangeSlider(ctk.CTkFrame):
    """Custom range slider with two handles for min/max values."""
    
    def __init__(self, parent, from_=0, to=1, min_val=0, max_val=1,
                 color="#4ECDC4", command=None, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.from_ = from_
        self.to = to
        self.min_val = min_val
        self.max_val = max_val
        self.color = color
        self.command = command
        self._dragging = None  # 'min', 'max', or 'range'
        self._drag_start_x = 0
        self._drag_start_min = 0
        self._drag_start_max = 0
        
        self.configure(fg_color="transparent", height=24)
        
        self.canvas = ctk.CTkCanvas(self, height=24, bg="#252525", highlightthickness=0)
        self.canvas.pack(fill="x", expand=True)
        
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
    
    def _value_to_x(self, value):
        width = self.canvas.winfo_width()
        if width <= 1:
            return 0
        ratio = (value - self.from_) / (self.to - self.from_)
        return int(ratio * (width - 20)) + 10
    
    def _x_to_value(self, x):
        width = self.canvas.winfo_width()
        if width <= 20:
            return self.from_
        ratio = (x - 10) / (width - 20)
        ratio = max(0, min(1, ratio))
        return self.from_ + ratio * (self.to - self.from_)
    
    def _redraw(self):
        self.canvas.delete("all")
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        
        if width <= 1:
            return
        
        # Track background
        self.canvas.create_rectangle(10, height//2-3, width-10, height//2+3,
                                    fill="#444444", outline="")
        
        # Active range
        x1 = self._value_to_x(self.min_val)
        x2 = self._value_to_x(self.max_val)
        self.canvas.create_rectangle(x1, height//2-3, x2, height//2+3,
                                    fill=self.color, outline="")
        
        # Min handle
        self.canvas.create_oval(x1-6, height//2-6, x1+6, height//2+6,
                               fill=self.color, outline="#ffffff", width=2, tags="min_handle")
        
        # Max handle
        self.canvas.create_oval(x2-6, height//2-6, x2+6, height//2+6,
                               fill=self.color, outline="#ffffff", width=2, tags="max_handle")
    
    def _on_click(self, event):
        x = event.x
        x_min = self._value_to_x(self.min_val)
        x_max = self._value_to_x(self.max_val)
        
        # Check which handle is closer
        dist_min = abs(x - x_min)
        dist_max = abs(x - x_max)
        
        if dist_min <= 10 and dist_min <= dist_max:
            self._dragging = 'min'
        elif dist_max <= 10:
            self._dragging = 'max'
        elif x_min < x < x_max:
            self._dragging = 'range'
            self._drag_start_x = x
            self._drag_start_min = self.min_val
            self._drag_start_max = self.max_val
        else:
            # Click outside - move nearest handle
            if dist_min < dist_max:
                self._dragging = 'min'
                self._update_value(event)
            else:
                self._dragging = 'max'
                self._update_value(event)
    
    def _on_drag(self, event):
        if self._dragging:
            self._update_value(event)
    
    def _on_release(self, event):
        self._dragging = None
    
    def _update_value(self, event):
        if self._dragging == 'min':
            new_val = self._x_to_value(event.x)
            new_val = max(self.from_, min(self.to, new_val))
            # Push max if min exceeds it
            if new_val > self.max_val:
                self.max_val = new_val
            self.min_val = new_val
        elif self._dragging == 'max':
            new_val = self._x_to_value(event.x)
            new_val = max(self.from_, min(self.to, new_val))
            # Push min if max goes below it
            if new_val < self.min_val:
                self.min_val = new_val
            self.max_val = new_val
        elif self._dragging == 'range':
            dx = event.x - self._drag_start_x
            width = self.canvas.winfo_width() - 20
            if width > 0:
                dv = dx / width * (self.to - self.from_)
                range_size = self._drag_start_max - self._drag_start_min
                
                new_min = self._drag_start_min + dv
                new_max = self._drag_start_max + dv
                
                if new_min < self.from_:
                    new_min = self.from_
                    new_max = self.from_ + range_size
                if new_max > self.to:
                    new_max = self.to
                    new_min = self.to - range_size
                
                self.min_val = new_min
                self.max_val = new_max
        
        self._redraw()
        if self.command:
            self.command(self.min_val, self.max_val)
    
    def set(self, min_val, max_val):
        self.min_val = min_val
        self.max_val = max_val
        self._redraw()
    
    def get(self):
        return (self.min_val, self.max_val)


class HandlerWidget(ctk.CTkFrame):
    """Widget for configuring a single alert handler."""
    
    # Custom order: red, green, blue, yellow, cyan, purple/pink (more saturated & darker)
    ZONE_COLORS = [
        "#D96B6B",  # red
        "#6BD98B",  # green
        "#6B9FD9",  # blue
        "#D9C86B",  # yellow
        "#6BC8D9",  # cyan
        "#C86BD9",  # purple/pink
        "#D99A6B",  # orange (extra)
        "#9A6BD9",  # violet (extra)
    ]
    
    def __init__(self, parent, handler: AlertHandler, color_index: int,
                 on_delete: Callable, on_update: Callable, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.handler = handler
        self.on_delete = on_delete
        self.on_update = on_update
        self.color = self.ZONE_COLORS[color_index % len(self.ZONE_COLORS)]
        
        self.configure(fg_color="#252525", corner_radius=8)
        self._create_widgets()
    
    def _darken_color(self, hex_color: str, factor: float) -> str:
        """Darken a hex color by a factor (0.0 = black, 1.0 = original)."""
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        return f"#{r:02x}{g:02x}{b:02x}"
    
    def _create_widgets(self):
        # Header row with color indicator and delete button
        self.header_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.header_frame.pack(fill="x", padx=10, pady=(8, 4))
        
        # Color indicator
        self.color_indicator = ctk.CTkLabel(
            self.header_frame,
            text="●",
            font=ctk.CTkFont(size=20),
            text_color=self.color
        )
        self.color_indicator.pack(side="left")
        
        # Delete button
        self.delete_btn = ctk.CTkButton(
            self.header_frame,
            text="✕",
            width=28,
            height=28,
            fg_color="transparent",
            hover_color="#FF4444",
            text_color="#CCCCCC",
            command=lambda: self.on_delete(self.handler.id),
            font=ctk.CTkFont(size=15)
        )
        self.delete_btn.pack(side="right")
        CTkToolTip(self.delete_btn, tr("delete_handler"))
        
        # Controls container
        self.controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.controls_frame.pack(fill="x", padx=10, pady=(0, 8))
        
        # Row 1: Threshold range
        row1 = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        
        ctk.CTkLabel(row1, text=tr("range"), font=ctk.CTkFont(size=15),
                    text_color="#CCCCCC", width=70, anchor="w").pack(side="left")
        
        self.min_entry = ctk.CTkEntry(row1, width=50, height=28, font=ctk.CTkFont(size=15),
                                      justify="center", fg_color="#333333", border_width=1)
        self.min_entry.insert(0, f"{int(self.handler.min_threshold*100)}")
        self.min_entry.pack(side="left", padx=(2, 0))
        self.min_entry.bind("<Return>", self._on_min_entry)
        self.min_entry.bind("<FocusOut>", self._on_min_entry)
        
        self.range_slider = RangeSlider(
            row1, from_=0, to=1,
            min_val=self.handler.min_threshold,
            max_val=self.handler.max_threshold,
            color=self.color,
            command=self._on_range_change
        )
        self.range_slider.pack(side="left", fill="x", expand=True, padx=(4, 4))
        
        self.max_entry = ctk.CTkEntry(row1, width=50, height=28, font=ctk.CTkFont(size=15),
                                      justify="center", fg_color="#333333", border_width=1)
        self.max_entry.insert(0, f"{int(self.handler.max_threshold*100)}")
        self.max_entry.pack(side="left", padx=(0, 2))
        self.max_entry.bind("<Return>", self._on_max_entry)
        self.max_entry.bind("<FocusOut>", self._on_max_entry)
        
        # Row 2: Sound settings (freq, vol, waveform)
        row2 = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        row2.pack(fill="x", pady=2)
        
        # Frequency
        ctk.CTkLabel(row2, text=tr("frequency"), font=ctk.CTkFont(size=15),
                    text_color="#CCCCCC", width=70, anchor="w").pack(side="left")
        
        self.freq_slider = ctk.CTkSlider(
            row2, from_=100, to=2000, number_of_steps=190,
            command=self._on_freq_slider_change,
            progress_color=self.color, button_color=self.color,
            width=70
        )
        self.freq_slider.set(self.handler.frequency)
        self.freq_slider.pack(side="left", fill="x", expand=True, padx=(2, 0))
        
        self.freq_entry = ctk.CTkEntry(row2, width=55, height=28, font=ctk.CTkFont(size=15),
                                       justify="center", fg_color="#333333", border_width=1)
        self.freq_entry.insert(0, f"{self.handler.frequency}")
        self.freq_entry.pack(side="left", padx=(4, 0))
        self.freq_entry.bind("<Return>", self._on_freq_entry)
        self.freq_entry.bind("<FocusOut>", self._on_freq_entry)
        
        # Volume
        ctk.CTkLabel(row2, text=tr("volume"), font=ctk.CTkFont(size=15),
                    text_color="#CCCCCC").pack(side="left", padx=(8, 0))
        
        self.vol_slider = ctk.CTkSlider(
            row2, from_=0, to=1, number_of_steps=100,
            command=self._on_vol_slider_change,
            progress_color=self.color, button_color=self.color,
            width=70
        )
        self.vol_slider.set(self.handler.volume)
        self.vol_slider.pack(side="left", fill="x", expand=True, padx=(2, 0))
        
        self.vol_entry = ctk.CTkEntry(row2, width=50, height=28, font=ctk.CTkFont(size=15),
                                      justify="center", fg_color="#333333", border_width=1)
        self.vol_entry.insert(0, f"{int(self.handler.volume*100)}")
        self.vol_entry.pack(side="left", padx=(4, 0))
        self.vol_entry.bind("<Return>", self._on_vol_entry)
        self.vol_entry.bind("<FocusOut>", self._on_vol_entry)
        
        # Waveform (same row)
        ctk.CTkLabel(row2, text=tr("waveform"), font=ctk.CTkFont(size=15),
                    text_color="#CCCCCC").pack(side="left", padx=(8, 0))
        
        # Create darker version of color for waveform selector
        dark_color = self._darken_color(self.color, 0.6)
        self.waveform_menu = ctk.CTkSegmentedButton(
            row2,
            values=["sine", "saw", "square"],
            command=self._on_waveform_change,
            font=ctk.CTkFont(size=15),
            selected_color=dark_color,
            selected_hover_color=dark_color
        )
        self.waveform_menu.set(self._waveform_to_short(self.handler.waveform))
        self.waveform_menu.pack(side="left", padx=(2, 0))
    
    def _update_entry(self, entry, value: str):
        """Update entry text without triggering events."""
        entry.delete(0, "end")
        entry.insert(0, value)
    
    def _on_range_change(self, min_val, max_val):
        """Handle range slider change."""
        self.handler.min_threshold = min_val
        self.handler.max_threshold = max_val
        self._update_entry(self.min_entry, f"{int(min_val*100)}")
        self._update_entry(self.max_entry, f"{int(max_val*100)}")
        self.on_update()
    
    def _on_min_entry(self, event=None):
        try:
            value = int(self.min_entry.get())
            value = max(0, min(100, value)) / 100.0
            if value > self.handler.max_threshold:
                value = self.handler.max_threshold
            self.handler.min_threshold = value
            self._update_entry(self.min_entry, f"{int(value*100)}")
            self.range_slider.set(self.handler.min_threshold, self.handler.max_threshold)
            self.on_update()
        except ValueError:
            self._update_entry(self.min_entry, f"{int(self.handler.min_threshold*100)}")
    
    def _on_max_entry(self, event=None):
        try:
            value = int(self.max_entry.get())
            value = max(0, min(100, value)) / 100.0
            if value < self.handler.min_threshold:
                value = self.handler.min_threshold
            self.handler.max_threshold = value
            self._update_entry(self.max_entry, f"{int(value*100)}")
            self.range_slider.set(self.handler.min_threshold, self.handler.max_threshold)
            self.on_update()
        except ValueError:
            self._update_entry(self.max_entry, f"{int(self.handler.max_threshold*100)}")
    
    def _on_freq_slider_change(self, value):
        self.handler.frequency = int(value)
        self._update_entry(self.freq_entry, f"{int(value)}")
        self.on_update()
    
    def _on_freq_entry(self, event=None):
        try:
            value = int(self.freq_entry.get())
            value = max(100, min(2000, value))
            self.handler.frequency = value
            self.freq_slider.set(value)
            self._update_entry(self.freq_entry, f"{value}")
            self.on_update()
        except ValueError:
            self._update_entry(self.freq_entry, f"{self.handler.frequency}")
    
    def _on_vol_slider_change(self, value):
        self.handler.volume = value
        self._update_entry(self.vol_entry, f"{int(value*100)}")
        self.on_update()
    
    def _on_vol_entry(self, event=None):
        try:
            value = int(self.vol_entry.get())
            value = max(0, min(100, value)) / 100.0
            self.handler.volume = value
            self.vol_slider.set(value)
            self._update_entry(self.vol_entry, f"{int(value*100)}")
            self.on_update()
        except ValueError:
            self._update_entry(self.vol_entry, f"{int(self.handler.volume*100)}")
    
    def _waveform_to_short(self, waveform: str) -> str:
        """Convert full waveform name to short."""
        return {"sine": "sine", "sawtooth": "saw", "square": "square"}.get(waveform, waveform)
    
    def _waveform_from_short(self, short: str) -> str:
        """Convert short waveform name to full."""
        return {"sine": "sine", "saw": "sawtooth", "square": "square"}.get(short, short)
    
    def _on_waveform_change(self, value):
        self.handler.waveform = self._waveform_from_short(value)
        self.on_update()
    
    def set_triggered(self, triggered: bool):
        """Update visual state when triggered."""
        if triggered:
            self.configure(fg_color="#3D2525")
            self.color_indicator.configure(text_color="#FF0000")
        else:
            self.configure(fg_color="#252525")
            self.color_indicator.configure(text_color=self.color)


class AxisWidget(ctk.CTkFrame):
    """Widget for displaying an axis with multiple handlers."""
    
    # Custom order: red, green, blue, yellow, cyan, purple/pink (more saturated & darker)
    AXIS_COLORS = [
        ("#D96B6B", "#2D1C1C"),  # red
        ("#6BD98B", "#1C2D1F"),  # green
        ("#6B9FD9", "#1C222D"),  # blue
        ("#D9C86B", "#2D2A1C"),  # yellow
        ("#6BC8D9", "#1C282D"),  # cyan
        ("#C86BD9", "#281C2D"),  # purple/pink
        ("#D99A6B", "#2D221C"),  # orange (extra)
        ("#9A6BD9", "#221C2D"),  # violet (extra)
    ]
    
    def __init__(self, parent, axis_index: int, axis_name: str,
                 audio_mixer: AudioMixer, **kwargs):
        super().__init__(parent, **kwargs)
        
        self.axis_index = axis_index
        self.axis_name = axis_name
        self.audio_mixer = audio_mixer
        self.value = 0.0
        self.handlers: List[AlertHandler] = []
        self.handler_widgets: List[HandlerWidget] = []
        
        color_idx = axis_index % len(self.AXIS_COLORS)
        self.main_color, self.bg_color = self.AXIS_COLORS[color_idx]
        
        self.configure(fg_color="transparent")
        self._create_widgets()
    
    def _create_widgets(self):
        # Header row: label + progress bar + add button
        self.header_row = ctk.CTkFrame(self, fg_color="transparent")
        self.header_row.pack(fill="x", pady=(2, 0))
        
        # Axis label
        self.label = ctk.CTkLabel(
            self.header_row,
            text=f"{tr('axis')} {self.axis_index}: {self.axis_name}",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=self.main_color,
            width=105
        )
        self.label.pack(side="left")
        
        # Add handler button
        self.add_btn = ctk.CTkButton(
            self.header_row,
            text="+",
            command=self._add_handler,
            font=ctk.CTkFont(size=18, weight="bold"),
            fg_color="#333333",
            hover_color="#444444",
            width=32,
            height=28
        )
        self.add_btn.pack(side="right", padx=(4, 0))
        CTkToolTip(self.add_btn, tr("add_handler"))
        
        # Progress bar container
        self.bar_frame = ctk.CTkFrame(self.header_row, fg_color=self.bg_color, height=28, corner_radius=6)
        self.bar_frame.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.bar_frame.pack_propagate(False)
        
        # Value bar (canvas for custom drawing)
        self.canvas = ctk.CTkCanvas(
            self.bar_frame,
            height=24,
            bg=self.bg_color,
            highlightthickness=0
        )
        self.canvas.pack(fill="x", padx=2, pady=2)
        self.canvas.bind("<Configure>", self._on_resize)
        
        # Handlers container (initially hidden, shown when handlers are added)
        self.handlers_frame = ctk.CTkFrame(self, fg_color="transparent")
    
    def _add_handler(self):
        handler = AlertHandler()
        self.handlers.append(handler)
        self._rebuild_handler_widgets()
    
    def _delete_handler(self, handler_id: str):
        # Stop audio if playing
        self.audio_mixer.stop_handler(handler_id)
        
        # Remove handler
        self.handlers = [h for h in self.handlers if h.id != handler_id]
        self._rebuild_handler_widgets()
    
    def _on_handler_update(self):
        """Called when any handler settings change."""
        self._draw_bar()
        # Update audio if handler is currently playing
        for handler in self.handlers:
            if handler.is_triggered:
                self.audio_mixer.update_handler(handler)
    
    def _rebuild_handler_widgets(self):
        # Destroy existing widgets
        for widget in self.handler_widgets:
            widget.destroy()
        self.handler_widgets.clear()
        
        # Recreate widgets
        for i, handler in enumerate(self.handlers):
            widget = HandlerWidget(
                self.handlers_frame,
                handler,
                i,
                on_delete=self._delete_handler,
                on_update=self._on_handler_update
            )
            widget.pack(fill="x", pady=2)
            self.handler_widgets.append(widget)
        
        # Show/hide handlers_frame based on whether there are handlers
        if self.handlers:
            self.handlers_frame.pack(fill="x")
        else:
            self.handlers_frame.pack_forget()
        
        self._draw_bar()
    
    def _on_resize(self, event):
        self._draw_bar()
    
    def update_value(self, value: float):
        """Update axis value and check handlers."""
        new_value = max(0.0, min(1.0, value))
        value_changed = abs(new_value - self.value) > 0.001
        self.value = new_value
        
        for i, handler in enumerate(self.handlers):
            was_triggered = handler.is_triggered
            is_triggered = handler.check_trigger(self.value)
            
            if is_triggered != was_triggered:
                handler.is_triggered = is_triggered
                
                # Update audio
                if is_triggered:
                    self.audio_mixer.start_handler(handler)
                else:
                    self.audio_mixer.stop_handler(handler.id)
                
                # Update widget visual
                if i < len(self.handler_widgets):
                    self.handler_widgets[i].set_triggered(is_triggered)
        
        # Only redraw if value changed significantly
        if value_changed:
            self._draw_bar()
    
    def _draw_bar(self):
        """Draw the axis value bar with handler zones."""
        self.canvas.delete("all")
        
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        
        if width <= 1:
            return
        
        # Draw handler zones (background)
        for i, handler in enumerate(self.handlers):
            color = HandlerWidget.ZONE_COLORS[i % len(HandlerWidget.ZONE_COLORS)]
            x1 = int(width * handler.min_threshold)
            x2 = int(width * handler.max_threshold)
            
            # Semi-transparent zone
            zone_color = self._dim_color(color, 0.3)
            self.canvas.create_rectangle(
                x1, 0, x2, height,
                fill=zone_color,
                outline=""
            )
            
            # Zone borders
            self.canvas.create_line(x1, 0, x1, height, fill=color, width=2)
            self.canvas.create_line(x2, 0, x2, height, fill=color, width=2)
        
        # Draw value bar
        bar_width = int(width * self.value)
        if bar_width > 0:
            self.canvas.create_rectangle(
                0, 4, bar_width, height - 4,
                fill=self.main_color,
                outline=""
            )
        
        # Draw current value line
        self.canvas.create_line(
            bar_width, 0, bar_width, height,
            fill="#FFFFFF",
            width=2
        )
    
    def _dim_color(self, hex_color: str, factor: float) -> str:
        """Dim a hex color by a factor."""
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        
        r = int(r * factor)
        g = int(g * factor)
        b = int(b * factor)
        
        return f"#{r:02x}{g:02x}{b:02x}"
    
    def get_triggered_count(self) -> int:
        """Return number of currently triggered handlers."""
        return sum(1 for h in self.handlers if h.is_triggered)
    
    def cleanup(self):
        """Stop all handlers."""
        for handler in self.handlers:
            if handler.is_triggered:
                self.audio_mixer.stop_handler(handler.id)


class PedalAssistantApp(ctk.CTk):
    """Main application window."""
    
    def __init__(self, start_minimized: bool = False):
        # Load language setting before UI creation
        self._load_language_setting()
        
        super().__init__()
        
        self.title(tr("app_title"))
        self.geometry("900x750")
        self.minsize(425, 325)
        
        self.joystick_reader = JoystickReader()
        self.audio_mixer = AudioMixer()
        
        self.axis_widgets: List[AxisWidget] = []
        self.running = True
        self._device_change_pending = False
        
        # Monitor for game controller connections/disconnections
        self.device_monitor = DeviceNotificationMonitor(self._on_game_device_change)
        self.device_monitor.start()
        
        # System tray (always visible)
        self.tray_icon: Optional[pystray.Icon] = None
        self._create_tray_icon()
        threading.Thread(target=self.tray_icon.run, daemon=True).start()
        
        self._create_ui()
        self._refresh_devices(apply_saved_settings=True)  # Load settings on startup
        self._update_loop()
        
        # Handle window close and minimize
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_minimize)
        
        # Start minimized if requested (autostart mode)
        if start_minimized:
            self.withdraw()
    
    def _create_tray_icon(self):
        """Create system tray icon."""
        # Create icon image
        icon_size = 64
        image = Image.new('RGBA', (icon_size, icon_size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # Draw a gamepad-like icon
        draw.ellipse([4, 4, icon_size-4, icon_size-4], fill='#4ECDC4')
        draw.ellipse([12, 12, icon_size-12, icon_size-12], fill='#1a1a1a')
        draw.ellipse([24, 24, icon_size-24, icon_size-24], fill='#4ECDC4')
        
        # Create tray menu
        menu = pystray.Menu(
            pystray.MenuItem(tr("show_hide"), self._show_from_tray, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(tr("exit"), self._quit_from_tray)
        )
        
        self.tray_icon = pystray.Icon(
            "PedalAssistant",
            image,
            "PedalAssistant",
            menu
        )
    
    def _on_minimize(self, event=None):
        """Handle window minimize - hide to tray."""
        if self.state() == 'iconic':  # Window is being minimized
            self.withdraw()  # Hide window
    
    def _show_from_tray(self, icon=None, item=None):
        """Show/toggle window from tray."""
        self.after(0, self._toggle_window)
    
    def _toggle_window(self):
        """Toggle window visibility."""
        if self.winfo_viewable():
            self.withdraw()
        else:
            self._restore_window()
    
    def _restore_window(self):
        """Restore the window."""
        self.deiconify()  # Show window
        self.state('normal')
        self.lift()  # Bring to front
        self.focus_force()
    
    def _quit_from_tray(self, icon=None, item=None):
        """Quit application from tray."""
        self.after(0, self._on_close)
    
    def _create_ui(self):
        # Main container
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Header row with title and controls
        self.header_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.header_frame.pack(fill="x", pady=(0, 15))
        
        # Title on the left
        self.title_frame = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.title_frame.pack(side="left")
        
        self.header = ctk.CTkLabel(
            self.title_frame,
            text="🎮 PedalAssistant",
            font=ctk.CTkFont(family="Segoe UI", size=26, weight="bold"),
            text_color="#4ECDC4"
        )
        self.header.pack(anchor="w")
        CTkToolTip(self.header, tr("about"))
        
        self.subtitle = ctk.CTkLabel(
            self.title_frame,
            text=tr("subtitle"),
            font=ctk.CTkFont(size=15),
            text_color="#CCCCCC"
        )
        self.subtitle.pack(anchor="w")
        
        # Controls on the right
        self.controls_frame = ctk.CTkFrame(self.header_frame, fg_color="transparent")
        self.controls_frame.pack(side="right")
        
        # Language selector
        self.lang_selector = ctk.CTkSegmentedButton(
            self.controls_frame,
            values=["EN", "RU"],
            command=self._on_language_change,
            font=ctk.CTkFont(size=15),
            width=80,
            selected_color="#2D7A73",
            selected_hover_color="#256560"
        )
        self.lang_selector.set("EN" if current_language == "en" else "RU")
        self.lang_selector.pack(side="left", padx=(0, 10))
        
        # Autostart checkbox
        self.autostart_var = ctk.BooleanVar(value=self._get_autostart())
        self.autostart_checkbox = ctk.CTkCheckBox(
            self.controls_frame,
            text=tr("autostart"),
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
            font=ctk.CTkFont(size=15),
            checkbox_width=22,
            checkbox_height=22
        )
        self.autostart_checkbox.pack(side="left", padx=(0, 10))
        CTkToolTip(self.autostart_checkbox, tr("autostart_tooltip"))
        
        # Buttons
        self.load_btn = ctk.CTkButton(
            self.controls_frame,
            text="📂",
            width=38,
            height=38,
            command=self._load_and_apply_settings,
            font=ctk.CTkFont(size=18),
            fg_color="#555555",
            hover_color="#666666"
        )
        self.load_btn.pack(side="left", padx=(0, 5))
        CTkToolTip(self.load_btn, tr("load_settings"))
        
        self.save_btn = ctk.CTkButton(
            self.controls_frame,
            text="💾",
            width=38,
            height=38,
            command=self._save_settings,
            font=ctk.CTkFont(size=18),
            fg_color="#555555",
            hover_color="#666666"
        )
        self.save_btn.pack(side="left", padx=(0, 5))
        CTkToolTip(self.save_btn, tr("save_settings"))
        
        self.restart_btn = ctk.CTkButton(
            self.controls_frame,
            text="🔄",
            width=38,
            height=38,
            command=self._restart_app,
            font=ctk.CTkFont(size=18),
            fg_color="#555555",
            hover_color="#666666"
        )
        self.restart_btn.pack(side="left")
        CTkToolTip(self.restart_btn, tr("restart_app"))
        
        # Device selection
        self.device_frame = ctk.CTkFrame(self.main_frame, fg_color="#1a1a1a", corner_radius=12)
        self.device_frame.pack(fill="x", pady=(0, 15))
        
        self.device_inner = ctk.CTkFrame(self.device_frame, fg_color="transparent")
        self.device_inner.pack(fill="x", padx=15, pady=12)
        
        self.device_label = ctk.CTkLabel(
            self.device_inner,
            text=tr("game_device"),
            font=ctk.CTkFont(size=15, weight="bold")
        )
        self.device_label.pack(side="left", padx=(0, 10))
        
        self.device_dropdown = ctk.CTkComboBox(
            self.device_inner,
            values=["Нет устройств"],
            command=self._on_device_select,
            font=ctk.CTkFont(size=15),
            dropdown_font=ctk.CTkFont(size=15),
            height=34,
            state="readonly"
        )
        self.device_dropdown.pack(side="left", fill="x", expand=True)
        
        # Axes container (scrollable)
        self.axes_frame = ctk.CTkFrame(self.main_frame, fg_color="#1a1a1a", corner_radius=12)
        self.axes_frame.pack(fill="both", expand=True)
        
        self.axes_label = ctk.CTkLabel(
            self.axes_frame,
            text=tr("device_axes"),
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.axes_label.pack(anchor="w", padx=15, pady=(15, 5))
        
        self.axes_scroll = ctk.CTkScrollableFrame(
            self.axes_frame,
            fg_color="transparent"
        )
        self.axes_scroll.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        
        # Make content stretch to full width
        self.axes_scroll.columnconfigure(0, weight=1)
        
        # Bind canvas width to inner frame width
        def on_canvas_configure(event):
            canvas_width = event.width
            self.axes_scroll._parent_canvas.itemconfig(
                self.axes_scroll._parent_canvas.find_all()[0],
                width=canvas_width
            )
            self._update_scrollbar_visibility()
        
        self.axes_scroll._parent_canvas.bind("<Configure>", on_canvas_configure)
        
        self.no_device_label = ctk.CTkLabel(
            self.axes_scroll,
            text=tr("select_device"),
            font=ctk.CTkFont(size=15),
            text_color="#666666"
        )
        self.no_device_label.pack(pady=50)
    
    def _update_scrollbar_visibility(self, event=None):
        """Hide scrollbar when content fits, show when it doesn't."""
        try:
            canvas = self.axes_scroll._parent_canvas
            scrollbar = self.axes_scroll._scrollbar
            
            # Get content height and visible height
            canvas.update_idletasks()
            content_height = canvas.bbox("all")[3] if canvas.bbox("all") else 0
            visible_height = canvas.winfo_height()
            
            # Show/hide scrollbar based on content
            if content_height <= visible_height:
                scrollbar.grid_remove()
            else:
                scrollbar.grid()
        except Exception:
            pass
    
    def _refresh_devices(self, apply_saved_settings: bool = False):
        # Reinitialize audio to switch to current default device
        self.audio_mixer.reinitialize()
        
        devices = self.joystick_reader.get_devices()
        settings = self._load_settings() if apply_saved_settings else None
        
        if not devices:
            self.device_dropdown.configure(values=[tr("no_devices")])
            self.device_dropdown.set(tr("no_devices"))
            self._clear_axes()
        else:
            self.device_dropdown.configure(values=devices)
            
            # Try to select saved device by name (ignoring index)
            selected_device = devices[0]
            if settings and "device_name" in settings:
                saved_name = settings["device_name"]
                # Find device with matching name
                for device in devices:
                    # Extract name from "index: name" format
                    if ": " in device:
                        current_name = device.split(": ", 1)[1]
                        if current_name == saved_name:
                            selected_device = device
                            break
            
            self.device_dropdown.set(selected_device)
            self._on_device_select(selected_device, settings)
    
    def _on_device_select(self, selection: str, settings: dict = None):
        if selection.startswith("Нет") or selection == tr("no_devices"):
            self._clear_axes()
            return
        
        # Skip if same device already selected
        if hasattr(self, '_current_device_selection') and self._current_device_selection == selection:
            return
        
        try:
            device_idx = int(selection.split(":")[0])
            num_axes = self.joystick_reader.select_device(device_idx)
            if num_axes > 0:
                self._current_device_selection = selection
                self._create_axis_widgets(num_axes)
                # Apply saved settings if provided
                if settings:
                    self._apply_settings(settings)
            else:
                self._clear_axes()
        except ValueError as e:
            print(f"Error selecting device: {e}")
            self._clear_axes()
    
    def _clear_axes(self):
        for widget in self.axis_widgets:
            widget.cleanup()
            widget.destroy()
        self.axis_widgets.clear()
        self.joystick_reader.clear_device()
        self._current_device_selection = None
        
        # Remove old no_device_label if exists
        if hasattr(self, 'no_device_label') and self.no_device_label.winfo_exists():
            self.no_device_label.destroy()
        
        self.no_device_label = ctk.CTkLabel(
            self.axes_scroll,
            text=tr("select_device"),
            font=ctk.CTkFont(size=15),
            text_color="#666666"
        )
        self.no_device_label.pack(pady=50)
    
    def _create_axis_widgets(self, num_axes: int):
        for widget in self.axis_widgets:
            widget.cleanup()
            widget.destroy()
        self.axis_widgets.clear()
        
        if hasattr(self, 'no_device_label'):
            self.no_device_label.destroy()
        
        if num_axes == 0:
            self.no_device_label = ctk.CTkLabel(
                self.axes_scroll,
                text=tr("no_axes"),
                font=ctk.CTkFont(size=15),
                text_color="#666666"
            )
            self.no_device_label.pack(pady=50)
            return
        
        axis_names = ["X", "Y", "Z", "Rx", "Ry", "Rz", "Slider 1", "Slider 2"]
        
        for i in range(num_axes):
            axis_name = axis_names[i] if i < len(axis_names) else f"Axis {i}"
            widget = AxisWidget(self.axes_scroll, i, axis_name, self.audio_mixer)
            widget.grid(row=i, column=0, sticky="ew", pady=2)
            self.axis_widgets.append(widget)
    
    def _on_game_device_change(self):
        """Called when a game controller is connected or disconnected."""
        self._device_change_pending = True
    
    def _update_loop(self):
        if not self.running:
            return
        
        self.audio_mixer.check_device_change()
        
        # Check if game controller was connected/disconnected
        if self._device_change_pending:
            self._device_change_pending = False
            # Delay the refresh slightly to let the device fully initialize
            self.after(500, lambda: self._refresh_devices(apply_saved_settings=True))
        
        axis_values = self.joystick_reader.get_axis_values()
        
        if axis_values and self.axis_widgets:
            for i, widget in enumerate(self.axis_widgets):
                if i < len(axis_values):
                    widget.update_value(axis_values[i])
        
        self.after(33, self._update_loop)  # 30 FPS
    
    def _save_settings(self):
        """Save current settings to file."""
        # Extract device name without index (e.g., "0: Device Name" -> "Device Name")
        device_selection = self.device_dropdown.get()
        device_name = device_selection.split(": ", 1)[1] if ": " in device_selection else device_selection
        
        settings = {
            "language": current_language,
            "device_name": device_name,
            "axes": {}
        }
        
        for widget in self.axis_widgets:
            axis_handlers = []
            for handler in widget.handlers:
                axis_handlers.append({
                    "min_threshold": handler.min_threshold,
                    "max_threshold": handler.max_threshold,
                    "frequency": handler.frequency,
                    "volume": handler.volume,
                    "waveform": handler.waveform
                })
            settings["axes"][str(widget.axis_index)] = axis_handlers
        
        try:
            with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(settings, f, indent=2, ensure_ascii=False)
            
            # Brief visual feedback on save button
            self.save_btn.configure(text="✓")
            self.after(1000, lambda: self.save_btn.configure(text="💾"))
        except Exception as e:
            print(f"Error saving settings: {e}")
    
    def _load_and_apply_settings(self):
        """Load settings from file and apply them."""
        settings = self._load_settings()
        if settings:
            self._apply_settings(settings)
            # Brief visual feedback on load button
            self.load_btn.configure(text="✓")
            self.after(1000, lambda: self.load_btn.configure(text="📂"))
    
    def _load_language_setting(self):
        """Load language setting from file before UI creation."""
        global current_language
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    if "language" in settings:
                        current_language = settings["language"]
            except Exception:
                pass
    
    def _load_settings(self):
        """Load settings from file."""
        if not os.path.exists(SETTINGS_FILE):
            return None
        
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading settings: {e}")
            return None
    
    def _apply_settings(self, settings: dict):
        """Apply loaded settings to axes."""
        if not settings or "axes" not in settings:
            return
        
        for widget in self.axis_widgets:
            axis_key = str(widget.axis_index)
            if axis_key in settings["axes"]:
                handlers_data = settings["axes"][axis_key]
                
                # Clear existing handlers
                for handler in widget.handlers:
                    widget.audio_mixer.stop_handler(handler.id)
                widget.handlers.clear()
                
                # Create handlers from settings
                for handler_data in handlers_data:
                    handler = AlertHandler(
                        min_threshold=handler_data.get("min_threshold", 1.0),
                        max_threshold=handler_data.get("max_threshold", 1.0),
                        frequency=handler_data.get("frequency", 440),
                        volume=handler_data.get("volume", 0.5),
                        waveform=handler_data.get("waveform", "sine")
                    )
                    widget.handlers.append(handler)
                
                # Rebuild widgets
                widget._rebuild_handler_widgets()
    
    def _get_autostart(self) -> bool:
        """Check if autostart is enabled in Windows registry."""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, AUTOSTART_APP_NAME)
                return True
            except FileNotFoundError:
                return False
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False
    
    def _set_autostart(self, enabled: bool):
        """Enable or disable autostart in Windows registry."""
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_REG_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    # Get path to pythonw and script for minimized autostart
                    app_dir = os.path.dirname(os.path.abspath(__file__))
                    script_path = os.path.join(app_dir, "pedal_assistant.py")
                    pythonw_path = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                    winreg.SetValueEx(key, AUTOSTART_APP_NAME, 0, winreg.REG_SZ, 
                                     f'"{pythonw_path}" "{script_path}" --minimized')
                else:
                    try:
                        winreg.DeleteValue(key, AUTOSTART_APP_NAME)
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
        except Exception as e:
            print(f"Error setting autostart: {e}")
    
    def _on_autostart_toggle(self):
        """Handle autostart checkbox toggle."""
        self._set_autostart(self.autostart_var.get())
    
    def _on_language_change(self, value):
        """Handle language change - save and restart."""
        global current_language
        new_lang = "en" if value == "EN" else "ru"
        if new_lang != current_language:
            current_language = new_lang
            # Save current settings with new language
            self._save_settings()
            # Restart to apply language change
            self._restart_app()
    
    def _restart_app(self):
        """Restart the application."""
        # Get the current script path
        script_path = os.path.abspath(__file__)
        
        # Prepare arguments
        args = [sys.executable, script_path]
        
        # Close current instance
        self._on_close()
        
        # Start new instance
        os.execv(sys.executable, args)
    
    def _on_close(self):
        self.running = False
        if self.tray_icon and self.tray_icon.visible:
            self.tray_icon.stop()
        self.device_monitor.stop()
        for widget in self.axis_widgets:
            widget.cleanup()
        self.audio_mixer.cleanup()
        self.joystick_reader.cleanup()
        self.destroy()


def main():
    start_minimized = "--minimized" in sys.argv
    app = PedalAssistantApp(start_minimized=start_minimized)
    app.mainloop()


if __name__ == "__main__":
    main()

