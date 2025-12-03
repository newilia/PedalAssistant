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
from typing import Dict, Optional, List, Callable
from dataclasses import dataclass, field

# Configure appearance
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


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
    
    DEVICE_CHECK_INTERVAL = 1.0
    
    def __init__(self):
        self.sample_rate = 44100
        self._stream: Optional[sd.OutputStream] = None
        self._lock = threading.Lock()
        self._phases: Dict[str, float] = {}  # handler_id -> phase
        self._active_handlers: Dict[str, AlertHandler] = {}  # handler_id -> handler
        self._current_device = None
        self._last_device_check = 0.0
        
        self._open_stream()
    
    def _get_default_device(self) -> Optional[int]:
        try:
            return sd.default.device[1]
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
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
    
    def _open_stream(self):
        self._close_stream()
        try:
            self._current_device = self._get_default_device()
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
        current_time = time.time()
        if current_time - self._last_device_check < self.DEVICE_CHECK_INTERVAL:
            return
        
        self._last_device_check = current_time
        new_device = self._get_default_device()
        
        if new_device != self._current_device:
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
    
    def cleanup(self):
        with self._lock:
            self._active_handlers.clear()
        self._close_stream()


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


class HandlerWidget(ctk.CTkFrame):
    """Widget for configuring a single alert handler."""
    
    ZONE_COLORS = [
        "#FF6B6B", "#4ECDC4", "#FFE66D", "#95E1D3",
        "#F38181", "#AA96DA", "#FCBAD3", "#A8D8EA"
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
            text_color="#888888",
            command=lambda: self.on_delete(self.handler.id),
            font=ctk.CTkFont(size=14)
        )
        self.delete_btn.pack(side="right")
        
        # Controls container
        self.controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.controls_frame.pack(fill="x", padx=10, pady=(0, 8))
        
        # Row 1: Thresholds
        row1 = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        row1.pack(fill="x", pady=2)
        
        # Min threshold
        ctk.CTkLabel(row1, text="Мин:", font=ctk.CTkFont(size=13),
                    text_color="#888888", width=36).pack(side="left")
        
        self.min_slider = ctk.CTkSlider(
            row1, from_=0, to=1, number_of_steps=100,
            command=self._on_min_slider_change,
            progress_color=self.color, button_color=self.color, width=140
        )
        self.min_slider.set(self.handler.min_threshold)
        self.min_slider.pack(side="left", padx=(2, 0))
        
        self.min_entry = ctk.CTkEntry(row1, width=50, height=26, font=ctk.CTkFont(size=13),
                                      justify="center", fg_color="#333333", border_width=1)
        self.min_entry.insert(0, f"{int(self.handler.min_threshold*100)}")
        self.min_entry.pack(side="left", padx=(4, 0))
        self.min_entry.bind("<Return>", self._on_min_entry)
        self.min_entry.bind("<FocusOut>", self._on_min_entry)
        
        # Max threshold
        ctk.CTkLabel(row1, text="Макс:", font=ctk.CTkFont(size=13),
                    text_color="#888888", width=42).pack(side="left", padx=(12, 0))
        
        self.max_slider = ctk.CTkSlider(
            row1, from_=0, to=1, number_of_steps=100,
            command=self._on_max_slider_change,
            progress_color=self.color, button_color=self.color, width=140
        )
        self.max_slider.set(self.handler.max_threshold)
        self.max_slider.pack(side="left", padx=(2, 0))
        
        self.max_entry = ctk.CTkEntry(row1, width=50, height=26, font=ctk.CTkFont(size=13),
                                      justify="center", fg_color="#333333", border_width=1)
        self.max_entry.insert(0, f"{int(self.handler.max_threshold*100)}")
        self.max_entry.pack(side="left", padx=(4, 0))
        self.max_entry.bind("<Return>", self._on_max_entry)
        self.max_entry.bind("<FocusOut>", self._on_max_entry)
        
        # Row 2: Sound settings
        row2 = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        row2.pack(fill="x", pady=2)
        
        # Frequency
        ctk.CTkLabel(row2, text="Частота:", font=ctk.CTkFont(size=13),
                    text_color="#888888", width=55).pack(side="left")
        
        self.freq_slider = ctk.CTkSlider(
            row2, from_=100, to=2000, number_of_steps=190,
            command=self._on_freq_slider_change,
            progress_color=self.color, button_color=self.color, width=140
        )
        self.freq_slider.set(self.handler.frequency)
        self.freq_slider.pack(side="left", padx=(2, 0))
        
        self.freq_entry = ctk.CTkEntry(row2, width=55, height=26, font=ctk.CTkFont(size=13),
                                       justify="center", fg_color="#333333", border_width=1)
        self.freq_entry.insert(0, f"{self.handler.frequency}")
        self.freq_entry.pack(side="left", padx=(4, 0))
        self.freq_entry.bind("<Return>", self._on_freq_entry)
        self.freq_entry.bind("<FocusOut>", self._on_freq_entry)
        
        # Volume
        ctk.CTkLabel(row2, text="Громк:", font=ctk.CTkFont(size=13),
                    text_color="#888888", width=48).pack(side="left", padx=(12, 0))
        
        self.vol_slider = ctk.CTkSlider(
            row2, from_=0, to=1, number_of_steps=100,
            command=self._on_vol_slider_change,
            progress_color=self.color, button_color=self.color, width=140
        )
        self.vol_slider.set(self.handler.volume)
        self.vol_slider.pack(side="left", padx=(2, 0))
        
        self.vol_entry = ctk.CTkEntry(row2, width=50, height=26, font=ctk.CTkFont(size=13),
                                      justify="center", fg_color="#333333", border_width=1)
        self.vol_entry.insert(0, f"{int(self.handler.volume*100)}")
        self.vol_entry.pack(side="left", padx=(4, 0))
        self.vol_entry.bind("<Return>", self._on_vol_entry)
        self.vol_entry.bind("<FocusOut>", self._on_vol_entry)
        
        # Row 3: Waveform
        row3 = ctk.CTkFrame(self.controls_frame, fg_color="transparent")
        row3.pack(fill="x", pady=(2, 0))
        
        ctk.CTkLabel(row3, text="Форма:", font=ctk.CTkFont(size=13),
                    text_color="#888888", width=48).pack(side="left")
        
        self.waveform_menu = ctk.CTkSegmentedButton(
            row3,
            values=["sine", "sawtooth", "square"],
            command=self._on_waveform_change,
            font=ctk.CTkFont(size=12),
            selected_color=self.color,
            selected_hover_color=self.color
        )
        self.waveform_menu.set(self.handler.waveform)
        self.waveform_menu.pack(side="left", padx=(2, 0))
    
    def _update_entry(self, entry, value: str):
        """Update entry text without triggering events."""
        entry.delete(0, "end")
        entry.insert(0, value)
    
    def _on_min_slider_change(self, value):
        self.handler.min_threshold = value
        self._update_entry(self.min_entry, f"{int(value*100)}")
        if value > self.handler.max_threshold:
            self.handler.max_threshold = value
            self.max_slider.set(value)
            self._update_entry(self.max_entry, f"{int(value*100)}")
        self.on_update()
    
    def _on_min_entry(self, event=None):
        try:
            value = int(self.min_entry.get())
            value = max(0, min(100, value)) / 100.0
            self.handler.min_threshold = value
            self.min_slider.set(value)
            self._update_entry(self.min_entry, f"{int(value*100)}")
            if value > self.handler.max_threshold:
                self.handler.max_threshold = value
                self.max_slider.set(value)
                self._update_entry(self.max_entry, f"{int(value*100)}")
            self.on_update()
        except ValueError:
            self._update_entry(self.min_entry, f"{int(self.handler.min_threshold*100)}")
    
    def _on_max_slider_change(self, value):
        self.handler.max_threshold = value
        self._update_entry(self.max_entry, f"{int(value*100)}")
        if value < self.handler.min_threshold:
            self.handler.min_threshold = value
            self.min_slider.set(value)
            self._update_entry(self.min_entry, f"{int(value*100)}")
        self.on_update()
    
    def _on_max_entry(self, event=None):
        try:
            value = int(self.max_entry.get())
            value = max(0, min(100, value)) / 100.0
            self.handler.max_threshold = value
            self.max_slider.set(value)
            self._update_entry(self.max_entry, f"{int(value*100)}")
            if value < self.handler.min_threshold:
                self.handler.min_threshold = value
                self.min_slider.set(value)
                self._update_entry(self.min_entry, f"{int(value*100)}")
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
    
    def _on_waveform_change(self, value):
        self.handler.waveform = value
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
    
    AXIS_COLORS = [
        ("#FF6B6B", "#2D1F1F"), ("#4ECDC4", "#1F2D2C"),
        ("#FFE66D", "#2D2B1F"), ("#95E1D3", "#1F2D29"),
        ("#F38181", "#2D1F1F"), ("#AA96DA", "#231F2D"),
        ("#FCBAD3", "#2D1F26"), ("#A8D8EA", "#1F262D"),
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
        # Axis label
        self.label = ctk.CTkLabel(
            self,
            text=f"Ось {self.axis_index}: {self.axis_name}",
            font=ctk.CTkFont(family="Consolas", size=15, weight="bold"),
            text_color=self.main_color
        )
        self.label.pack(anchor="w", pady=(8, 2))
        
        # Progress bar container
        self.bar_frame = ctk.CTkFrame(self, fg_color=self.bg_color, height=30, corner_radius=6)
        self.bar_frame.pack(fill="x", pady=2)
        self.bar_frame.pack_propagate(False)
        
        # Value bar (canvas for custom drawing)
        self.canvas = ctk.CTkCanvas(
            self.bar_frame,
            height=26,
            bg=self.bg_color,
            highlightthickness=0
        )
        self.canvas.pack(fill="x", padx=2, pady=2)
        self.canvas.bind("<Configure>", self._on_resize)
        
        # Handlers container
        self.handlers_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.handlers_frame.pack(fill="x", pady=(4, 0))
        
        # Add handler button
        self.add_btn = ctk.CTkButton(
            self.handlers_frame,
            text="+ Добавить обработчик",
            command=self._add_handler,
            font=ctk.CTkFont(size=13),
            fg_color="#333333",
            hover_color="#444444",
            height=32
        )
        self.add_btn.pack(fill="x", pady=(4, 8))
    
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
            widget.pack(fill="x", pady=2, before=self.add_btn)
            self.handler_widgets.append(widget)
        
        self._draw_bar()
    
    def _on_resize(self, event):
        self._draw_bar()
    
    def update_value(self, value: float):
        """Update axis value and check handlers."""
        self.value = max(0.0, min(1.0, value))
        
        for i, handler in enumerate(self.handlers):
            was_triggered = handler.is_triggered
            is_triggered = handler.check_trigger(self.value)
            handler.is_triggered = is_triggered
            
            # Update audio
            if is_triggered and not was_triggered:
                self.audio_mixer.start_handler(handler)
            elif not is_triggered and was_triggered:
                self.audio_mixer.stop_handler(handler.id)
            
            # Update widget visual
            if i < len(self.handler_widgets):
                self.handler_widgets[i].set_triggered(is_triggered)
        
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
    
    def __init__(self):
        super().__init__()
        
        self.title("PedalAssistant - Монитор осей игровых устройств")
        self.geometry("900x750")
        self.minsize(850, 650)
        
        self.joystick_reader = JoystickReader()
        self.audio_mixer = AudioMixer()
        
        self.axis_widgets: List[AxisWidget] = []
        self.running = True
        
        self._create_ui()
        self._refresh_devices()
        self._update_loop()
        
        self.protocol("WM_DELETE_WINDOW", self._on_close)
    
    def _create_ui(self):
        # Main container
        self.main_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.main_frame.pack(fill="both", expand=True, padx=20, pady=20)
        
        # Header
        self.header = ctk.CTkLabel(
            self.main_frame,
            text="🎮 PedalAssistant",
            font=ctk.CTkFont(family="Segoe UI", size=30, weight="bold"),
            text_color="#4ECDC4"
        )
        self.header.pack(pady=(0, 5))
        
        self.subtitle = ctk.CTkLabel(
            self.main_frame,
            text="Монитор осей с настраиваемыми звуковыми оповещениями",
            font=ctk.CTkFont(size=15),
            text_color="#666666"
        )
        self.subtitle.pack(pady=(0, 20))
        
        # Device selection
        self.device_frame = ctk.CTkFrame(self.main_frame, fg_color="#1a1a1a", corner_radius=12)
        self.device_frame.pack(fill="x", pady=(0, 15))
        
        self.device_inner = ctk.CTkFrame(self.device_frame, fg_color="transparent")
        self.device_inner.pack(fill="x", padx=15, pady=15)
        
        self.device_label = ctk.CTkLabel(
            self.device_inner,
            text="Игровое устройство:",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.device_label.pack(anchor="w")
        
        self.device_select_frame = ctk.CTkFrame(self.device_inner, fg_color="transparent")
        self.device_select_frame.pack(fill="x", pady=(5, 0))
        
        self.device_dropdown = ctk.CTkComboBox(
            self.device_select_frame,
            values=["Нет устройств"],
            command=self._on_device_select,
            font=ctk.CTkFont(size=15),
            dropdown_font=ctk.CTkFont(size=15),
            height=38
        )
        self.device_dropdown.pack(side="left", fill="x", expand=True)
        
        self.refresh_btn = ctk.CTkButton(
            self.device_select_frame,
            text="🔄",
            width=40,
            height=38,
            command=self._refresh_devices,
            font=ctk.CTkFont(size=18)
        )
        self.refresh_btn.pack(side="right", padx=(10, 0))
        
        # Status bar
        self.status_frame = ctk.CTkFrame(self.main_frame, fg_color="#1a1a1a", corner_radius=12, height=55)
        self.status_frame.pack(fill="x", pady=(0, 15))
        self.status_frame.pack_propagate(False)
        
        self.status_inner = ctk.CTkFrame(self.status_frame, fg_color="transparent")
        self.status_inner.pack(fill="both", expand=True, padx=15)
        
        self.status_indicator = ctk.CTkLabel(
            self.status_inner,
            text="●",
            font=ctk.CTkFont(size=22),
            text_color="#4ECDC4"
        )
        self.status_indicator.pack(side="left")
        
        self.status_label = ctk.CTkLabel(
            self.status_inner,
            text="Готово",
            font=ctk.CTkFont(size=15),
            text_color="#888888"
        )
        self.status_label.pack(side="left", padx=(10, 0))
        
        self.triggered_label = ctk.CTkLabel(
            self.status_inner,
            text="",
            font=ctk.CTkFont(size=14),
            text_color="#FF6B6B"
        )
        self.triggered_label.pack(side="right")
        
        # Axes container (scrollable)
        self.axes_frame = ctk.CTkFrame(self.main_frame, fg_color="#1a1a1a", corner_radius=12)
        self.axes_frame.pack(fill="both", expand=True)
        
        self.axes_label = ctk.CTkLabel(
            self.axes_frame,
            text="Оси устройства:",
            font=ctk.CTkFont(size=16, weight="bold")
        )
        self.axes_label.pack(anchor="w", padx=15, pady=(15, 5))
        
        self.axes_scroll = ctk.CTkScrollableFrame(
            self.axes_frame,
            fg_color="transparent"
        )
        self.axes_scroll.pack(fill="both", expand=True, padx=15, pady=(0, 15))
        
        self.no_device_label = ctk.CTkLabel(
            self.axes_scroll,
            text="Выберите устройство из списка выше",
            font=ctk.CTkFont(size=15),
            text_color="#666666"
        )
        self.no_device_label.pack(pady=50)
    
    def _refresh_devices(self):
        devices = self.joystick_reader.get_devices()
        
        if not devices:
            self.device_dropdown.configure(values=["Нет подключенных устройств"])
            self.device_dropdown.set("Нет подключенных устройств")
            self._clear_axes()
        else:
            self.device_dropdown.configure(values=devices)
            self.device_dropdown.set(devices[0])
            self._on_device_select(devices[0])
    
    def _on_device_select(self, selection: str):
        if selection.startswith("Нет"):
            self._clear_axes()
            return
        
        try:
            device_idx = int(selection.split(":")[0])
            num_axes = self.joystick_reader.select_device(device_idx)
            if num_axes > 0:
                self._create_axis_widgets(num_axes)
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
        
        self.no_device_label = ctk.CTkLabel(
            self.axes_scroll,
            text="Выберите устройство из списка выше",
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
                text="Устройство не имеет осей",
                font=ctk.CTkFont(size=15),
                text_color="#666666"
            )
            self.no_device_label.pack(pady=50)
            return
        
        axis_names = ["X", "Y", "Z", "Rx", "Ry", "Rz", "Slider 1", "Slider 2"]
        
        for i in range(num_axes):
            axis_name = axis_names[i] if i < len(axis_names) else f"Axis {i}"
            widget = AxisWidget(self.axes_scroll, i, axis_name, self.audio_mixer)
            widget.pack(fill="x", pady=2)
            self.axis_widgets.append(widget)
    
    def _update_loop(self):
        if not self.running:
            return
        
        self.audio_mixer.check_device_change()
        
        axis_values = self.joystick_reader.get_axis_values()
        
        total_triggered = 0
        
        if axis_values and self.axis_widgets:
            for i, widget in enumerate(self.axis_widgets):
                if i < len(axis_values):
                    widget.update_value(axis_values[i])
                    total_triggered += widget.get_triggered_count()
        
        # Update status
        if total_triggered > 0:
            self.status_indicator.configure(text_color="#FF6B6B")
            self.status_label.configure(text="Сработало обработчиков:")
            self.triggered_label.configure(text=str(total_triggered))
        else:
            self.status_indicator.configure(text_color="#4ECDC4")
            self.status_label.configure(text="Готово")
            self.triggered_label.configure(text="")
        
        self.after(33, self._update_loop)
    
    def _on_close(self):
        self.running = False
        for widget in self.axis_widgets:
            widget.cleanup()
        self.audio_mixer.cleanup()
        self.joystick_reader.cleanup()
        self.destroy()


def main():
    app = PedalAssistantApp()
    app.mainloop()


if __name__ == "__main__":
    main()

