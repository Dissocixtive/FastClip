import sys
import os
import json
import time
import threading
from collections import deque
from pathlib import Path

import numpy as np
import pyaudio
from pynput import keyboard as pynput_keyboard

from PyQt5.QtCore import (
    Qt, QTimer, pyqtSignal, QObject, QUrl, QPropertyAnimation
)
from PyQt5.QtGui import (
    QIcon, QFont, QColor, QPalette
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QSlider, QComboBox, QFileDialog, QGroupBox,
    QFormLayout, QDialog, QDialogButtonBox, QSystemTrayIcon, QMenu,
    QStyle, QMessageBox, QLineEdit, QCheckBox, QSpinBox,
    QStyleFactory, QTabWidget, QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect
)
from PyQt5.QtMultimedia import QMediaPlayer, QMediaContent
from PyQt5.QtMultimediaWidgets import QVideoWidget

# Захват экрана: пробуем dxcam (нужен opencv-python), иначе mss
try:
    import cv2
    import dxcam
    HAS_DXCAM = True
except ImportError:
    HAS_DXCAM = False
    import mss

# MoviePy
from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
try:
    from moviepy.audio.io.AudioArrayClip import AudioArrayClip
    HAS_AUDIO_ARRAY_CLIP = True
except ImportError:
    HAS_AUDIO_ARRAY_CLIP = False
    from moviepy.audio.AudioClip import AudioClip  # Нужно для from_array


# ----------------------------------------------------------------------
# Settings manager
# ----------------------------------------------------------------------
class Settings:
    """Loads/saves user settings as JSON."""
    def __init__(self):
        self.app_name = "InstantReplay"
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA", "."))
        else:
            base = Path.home() / ".config"
        self.config_dir = base / self.app_name
        self.config_dir.mkdir(parents=True, exist_ok=True)
        self.config_file = self.config_dir / "settings.json"

        self.data = {
            "save_path": str(Path.home() / "Videos"),
            "mic_device_index": 0,
            "mic_volume": 0.8,
            "fps": 30,
            "quality": "medium",
            "save_hotkey": "ctrl+s",
            "overlay_hotkey": "ctrl+v",
            "replay_duration": 30
        }
        self.load()

    def load(self):
        if self.config_file.exists():
            try:
                with open(self.config_file, "r") as f:
                    self.data.update(json.load(f))
            except Exception:
                pass

    def save(self):
        with open(self.config_file, "w") as f:
            json.dump(self.data, f, indent=2)

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value
        self.save()


# ----------------------------------------------------------------------
# Audio device enumeration helper
# ----------------------------------------------------------------------
def get_audio_devices():
    p = pyaudio.PyAudio()
    devices = []
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if info.get("maxInputChannels", 0) > 0:
            devices.append((i, info["name"]))
    p.terminate()
    return devices


# ----------------------------------------------------------------------
# Capture Manager
# ----------------------------------------------------------------------
class CaptureManager(QObject):
    frame_ready = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.running = False
        self.lock = threading.Lock()
        self.video_frames = deque()
        self.audio_chunks = deque()
        self.video_thread = None
        self.audio_thread = None
        self.audio_stream = None
        self.pyaudio_instance = None
        self.audio_sample_rate = 44100
        self.audio_channels = 2
        self.screen_width = 0
        self.screen_height = 0
        self.use_dxcam = HAS_DXCAM
        self.sct = None
        self.camera = None
        self.last_trim_time = 0

    def start_capture(self):
        if self.running:
            return
        self.running = True

        if self.use_dxcam:
            try:
                self.camera = dxcam.create(output_idx=0, output_color="BGR")
                test_frame = self.camera.grab()
                if test_frame is not None:
                    self.screen_height, self.screen_width = test_frame.shape[:2]
                else:
                    self.error_occurred.emit("Failed to capture screen with dxcam")
                    self.running = False
                    return
            except Exception as e:
                self.error_occurred.emit(f"dxcam error: {e}. Falling back to mss.")
                self.use_dxcam = False
                import mss as mss_module
                self.sct = mss_module.mss()
                with self.sct as sct:
                    mon = sct.monitors[1]
                    self.screen_width = mon["width"]
                    self.screen_height = mon["height"]
        else:
            import mss as mss_module
            self.sct = mss_module.mss()
            with self.sct as sct:
                mon = sct.monitors[1]
                self.screen_width = mon["width"]
                self.screen_height = mon["height"]

        self.video_thread = threading.Thread(target=self._video_loop, daemon=True)
        self.video_thread.start()

        self.audio_thread = threading.Thread(target=self._audio_loop, daemon=True)
        self.audio_thread.start()

    def stop_capture(self):
        self.running = False
        if self.audio_stream:
            self.audio_stream.stop_stream()
            self.audio_stream.close()
            self.audio_stream = None
        if self.pyaudio_instance:
            self.pyaudio_instance.terminate()
            self.pyaudio_instance = None
        if self.video_thread:
            self.video_thread.join(timeout=1)
        if self.audio_thread:
            self.audio_thread.join(timeout=1)

    def _video_loop(self):
        fps = self.settings.get("fps", 30)
        interval = 1.0 / fps
        next_time = time.perf_counter()
        while self.running:
            now = time.perf_counter()
            if now >= next_time:
                try:
                    if self.use_dxcam:
                        frame = self.camera.grab()
                    else:
                        sct_img = self.sct.grab(self.sct.monitors[1])
                        frame = np.array(sct_img)
                        if frame.shape[2] == 4:
                            frame = frame[:, :, :3]
                except Exception as e:
                    self.error_occurred.emit(f"Screen capture error: {e}")
                    break

                if frame is not None:
                    timestamp = time.time()
                    with self.lock:
                        self.video_frames.append((timestamp, frame))
                next_time += interval

                # Периодическая очистка буфера (каждые 2 секунды)
                now_trim = time.time()
                if now_trim - self.last_trim_time > 2.0:
                    self.trim_buffers(self.settings.get("replay_duration", 30) + 5)
                    self.last_trim_time = now_trim
            else:
                time.sleep(0.001)

    def _audio_loop(self):
        try:
            self.pyaudio_instance = pyaudio.PyAudio()
            device_index = self.settings.get("mic_device_index", 0)
            self.audio_stream = self.pyaudio_instance.open(
                format=pyaudio.paInt16,
                channels=self.audio_channels,
                rate=self.audio_sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=1024,
                stream_callback=self._audio_callback
            )
            self.audio_stream.start_stream()
            while self.running and self.audio_stream.is_active():
                time.sleep(0.1)
                # Периодическая очистка
                if time.time() - self.last_trim_time > 2.0:
                    self.trim_buffers(self.settings.get("replay_duration", 30) + 5)
                    self.last_trim_time = time.time()
        except Exception as e:
            self.error_occurred.emit(f"Audio error: {e}")
        finally:
            if self.audio_stream:
                self.audio_stream.stop_stream()
                self.audio_stream.close()

    def _audio_callback(self, in_data, frame_count, time_info, status):
        timestamp = time.time()
        with self.lock:
            self.audio_chunks.append((timestamp, in_data))
        return (None, pyaudio.paContinue)

    def trim_buffers(self, duration):
        now = time.time()
        cutoff = now - duration
        with self.lock:
            while self.video_frames and self.video_frames[0][0] < cutoff:
                self.video_frames.popleft()
            while self.audio_chunks and self.audio_chunks[0][0] < cutoff:
                self.audio_chunks.popleft()

    def get_replay_data(self, duration):
        self.trim_buffers(duration)
        with self.lock:
            frames = [f for ts, f in self.video_frames]
            audio_parts = [d for ts, d in self.audio_chunks]
        audio_data = b''.join(audio_parts) if audio_parts else b''
        return frames, audio_data

    def get_audio_array(self, audio_data):
        samples = np.frombuffer(audio_data, dtype=np.int16)
        samples = samples.reshape(-1, self.audio_channels).astype(np.float32) / 32768.0
        return samples


# ----------------------------------------------------------------------
# Replay Saver
# ----------------------------------------------------------------------
class ReplaySaver(QObject):
    finished = pyqtSignal(str)
    error_occurred = pyqtSignal(str)

    def __init__(self, capture_manager, settings, parent=None):
        super().__init__(parent)
        self.capture = capture_manager
        self.settings = settings

    def save_replay(self):
        duration = self.settings.get("replay_duration", 30)
        frames, audio_data = self.capture.get_replay_data(duration)
        if not frames:
            self.error_occurred.emit("No video data captured")
            return

        save_dir = self.settings.get("save_path")
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        file_path = os.path.join(save_dir, f"replay_{timestamp}.mp4")

        worker = threading.Thread(
            target=self._encode_worker,
            args=(frames, audio_data, file_path),
            daemon=True
        )
        worker.start()

    def _encode_worker(self, frames, audio_data, file_path):
        try:
            fps = self.settings.get("fps", 30)
            quality = self.settings.get("quality", "medium")
            crf_map = {"low": 23, "medium": 18, "high": 15}
            crf = crf_map.get(quality, 18)

            rgb_frames = [frame[:, :, ::-1] for frame in frames]
            video_clip = ImageSequenceClip(rgb_frames, fps=fps)

            if audio_data:
                audio_samples = self.capture.get_audio_array(audio_data)
                if audio_samples.shape[0] > 0:
                    sample_rate = self.capture.audio_sample_rate
                    # Приводим к двумерному массиву (samples, channels)
                    if audio_samples.ndim == 1:
                        audio_samples = audio_samples[:, np.newaxis]
                    total_samples = audio_samples.shape[0]
                    duration = total_samples / sample_rate

                    if HAS_AUDIO_ARRAY_CLIP:
                        # MoviePy 1.x
                        audio_clip = AudioArrayClip(audio_samples, fps=sample_rate)
                    else:
                        # MoviePy 2.x: make_frame должна принимать как скаляр, так и массив
                        def make_frame(t):
                            idx = np.floor(np.asarray(t) * sample_rate).astype(int)
                            idx = np.clip(idx, 0, total_samples - 1)
                            return audio_samples[idx]  # вернёт (..., nchannels)

                        audio_clip = AudioClip(make_frame, duration=duration, fps=sample_rate)

                    if hasattr(video_clip, 'with_audio'):
                        video_clip = video_clip.with_audio(audio_clip)
                    else:
                        video_clip = video_clip.set_audio(audio_clip)

            video_clip.write_videofile(
                file_path,
                codec='libx264',
                audio_codec='aac',
                ffmpeg_params=[
                    '-crf', str(crf),
                    '-preset', 'veryfast',
                    '-pix_fmt', 'yuv420p'
                ]
            )
            self.finished.emit(file_path)
        except Exception as e:
            self.error_occurred.emit(f"Encoding failed: {e}")


# ----------------------------------------------------------------------
# Settings Dialog
# ----------------------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Settings")
        self.setMinimumWidth(450)

        main_layout = QVBoxLayout(self)
        tabs = QTabWidget()
        main_layout.addWidget(tabs)

        # General tab
        gen_widget = QWidget()
        gen_layout = QFormLayout(gen_widget)

        self.save_path_edit = QLineEdit()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse_save_path)
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.save_path_edit)
        path_layout.addWidget(browse_btn)
        gen_layout.addRow("Save path:", path_layout)

        self.replay_duration_spin = QSpinBox()
        self.replay_duration_spin.setRange(5, 120)
        self.replay_duration_spin.setSuffix(" sec")
        gen_layout.addRow("Replay duration:", self.replay_duration_spin)

        self.save_hotkey_edit = QLineEdit()
        gen_layout.addRow("Save replay hotkey:", self.save_hotkey_edit)
        self.overlay_hotkey_edit = QLineEdit()
        gen_layout.addRow("Overlay hotkey:", self.overlay_hotkey_edit)

        self.fps_combo = QComboBox()
        self.fps_combo.addItems(["15", "30", "60"])
        gen_layout.addRow("FPS:", self.fps_combo)

        self.quality_combo = QComboBox()
        self.quality_combo.addItems(["low", "medium", "high"])
        gen_layout.addRow("Quality:", self.quality_combo)

        tabs.addTab(gen_widget, "General")

        # Audio tab
        audio_widget = QWidget()
        audio_layout = QFormLayout(audio_widget)

        self.mic_combo = QComboBox()
        audio_devices = get_audio_devices()
        for idx, name in audio_devices:
            self.mic_combo.addItem(name, idx)
        audio_layout.addRow("Microphone:", self.mic_combo)

        self.mic_volume_slider = QSlider(Qt.Horizontal)
        self.mic_volume_slider.setRange(0, 100)
        self.mic_volume_slider.setTickInterval(10)
        self.mic_volume_label = QLabel("80%")
        self.mic_volume_slider.valueChanged.connect(
            lambda v: self.mic_volume_label.setText(f"{v}%")
        )
        vol_layout = QHBoxLayout()
        vol_layout.addWidget(self.mic_volume_slider)
        vol_layout.addWidget(self.mic_volume_label)
        audio_layout.addRow("Mic volume:", vol_layout)

        tabs.addTab(audio_widget, "Audio")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        main_layout.addWidget(buttons)

        self.load_settings()

    def browse_save_path(self):
        dir_path = QFileDialog.getExistingDirectory(self, "Select Save Directory")
        if dir_path:
            self.save_path_edit.setText(dir_path)

    def load_settings(self):
        self.save_path_edit.setText(self.settings.get("save_path"))
        self.replay_duration_spin.setValue(self.settings.get("replay_duration", 30))
        self.save_hotkey_edit.setText(self.settings.get("save_hotkey", "ctrl+s"))
        self.overlay_hotkey_edit.setText(self.settings.get("overlay_hotkey", "ctrl+v"))

        fps = str(self.settings.get("fps", 30))
        idx = self.fps_combo.findText(fps)
        if idx >= 0:
            self.fps_combo.setCurrentIndex(idx)

        qual = self.settings.get("quality", "medium")
        idx = self.quality_combo.findText(qual)
        if idx >= 0:
            self.quality_combo.setCurrentIndex(idx)

        mic_idx = self.settings.get("mic_device_index", 0)
        for i in range(self.mic_combo.count()):
            if self.mic_combo.itemData(i) == mic_idx:
                self.mic_combo.setCurrentIndex(i)
                break

        vol = int(self.settings.get("mic_volume", 0.8) * 100)
        self.mic_volume_slider.setValue(vol)

    def save_settings(self):
        self.settings.set("save_path", self.save_path_edit.text())
        self.settings.set("replay_duration", self.replay_duration_spin.value())
        self.settings.set("save_hotkey", self.save_hotkey_edit.text())
        self.settings.set("overlay_hotkey", self.overlay_hotkey_edit.text())
        self.settings.set("fps", int(self.fps_combo.currentText()))
        self.settings.set("quality", self.quality_combo.currentText())
        if self.mic_combo.currentIndex() >= 0:
            self.settings.set("mic_device_index", self.mic_combo.currentData())
        self.settings.set("mic_volume", self.mic_volume_slider.value() / 100.0)

    def accept(self):
        self.save_settings()
        super().accept()


# ----------------------------------------------------------------------
# Replay Player Window
# ----------------------------------------------------------------------
class ReplayPlayer(QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Replay Viewer")
        self.resize(800, 600)

        self.media_player = QMediaPlayer(self)
        self.video_widget = QVideoWidget()
        self.media_player.setVideoOutput(self.video_widget)

        self.play_btn = QPushButton("Play")
        self.play_btn.setCheckable(True)
        self.stop_btn = QPushButton("Stop")
        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.addWidget(self.video_widget)

        control_layout = QHBoxLayout()
        control_layout.addWidget(self.play_btn)
        control_layout.addWidget(self.stop_btn)
        control_layout.addWidget(self.position_slider)
        layout.addLayout(control_layout)

        self.play_btn.clicked.connect(self.toggle_play)
        self.stop_btn.clicked.connect(self.media_player.stop)
        self.media_player.stateChanged.connect(self.on_state_changed)
        self.media_player.positionChanged.connect(self.on_position_changed)
        self.media_player.durationChanged.connect(self.on_duration_changed)
        self.position_slider.sliderMoved.connect(self.media_player.setPosition)

    def open_file(self, file_path):
        url = QUrl.fromLocalFile(file_path)
        self.media_player.setMedia(QMediaContent(url))
        self.play_btn.setText("Play")
        self.play_btn.setChecked(False)

    def toggle_play(self, checked):
        if self.media_player.state() == QMediaPlayer.PlayingState:
            self.media_player.pause()
        else:
            self.media_player.play()

    def on_state_changed(self, state):
        if state == QMediaPlayer.PlayingState:
            self.play_btn.setText("Pause")
            self.play_btn.setChecked(True)
        else:
            self.play_btn.setText("Play")
            self.play_btn.setChecked(False)

    def on_position_changed(self, position):
        self.position_slider.blockSignals(True)
        self.position_slider.setValue(position)
        self.position_slider.blockSignals(False)

    def on_duration_changed(self, duration):
        self.position_slider.setRange(0, duration)


# ----------------------------------------------------------------------
# Overlay Window (современный дизайн + анимация)
# ----------------------------------------------------------------------
class OverlayWindow(QWidget):
    def __init__(self, app, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.app = app
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Replay Overlay")
        self.setFixedSize(300, 200)

        # Градиентный фон и скруглённые углы (через stylesheet)
        self.setStyleSheet("""
            OverlayWindow {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                    stop:0 #3A3153, stop:1 #2C2543);
                border-radius: 15px;
                border: 2px solid #5B4B8C;
            }
            QPushButton {
                background-color: rgba(91, 75, 140, 200);
                color: #EAE0FF;
                border: none;
                padding: 8px 16px;
                border-radius: 8px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: rgba(110, 93, 166, 230);
            }
            QPushButton:pressed {
                background-color: rgba(70, 55, 120, 230);
            }
            QLabel {
                background: transparent;
                color: #D8D0F0;
                font-size: 14px;
                font-weight: bold;
            }
        """)

        # Тень
        self.shadow = QGraphicsDropShadowEffect(self)
        self.shadow.setBlurRadius(20)
        self.shadow.setColor(QColor(0, 0, 0, 150))
        self.shadow.setOffset(0, 5)
        self.setGraphicsEffect(self.shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(15, 15, 15, 15)

        self.status_label = QLabel("⚡ Replay Buffer Active")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.status_label)

        self.save_btn = QPushButton("💾 Save Replay")
        self.save_btn.clicked.connect(self.on_save)
        layout.addWidget(self.save_btn)

        self.view_btn = QPushButton("▶️ Open Viewer")
        self.view_btn.clicked.connect(self.on_open_viewer)
        layout.addWidget(self.view_btn)

        self.settings_btn = QPushButton("⚙️ Settings")
        self.settings_btn.clicked.connect(self.on_settings)
        layout.addWidget(self.settings_btn)

        self.exit_btn = QPushButton("❌ Exit")
        self.exit_btn.clicked.connect(self.on_exit)
        layout.addWidget(self.exit_btn)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_status)
        self.timer.start(1000)

        # Анимация появления/исчезновения (изменение размера)
        self.anim_show = QPropertyAnimation(self, b"windowOpacity")
        self.anim_show.setDuration(200)
        self.anim_show.setStartValue(0.0)
        self.anim_show.setEndValue(1.0)

        self.anim_hide = QPropertyAnimation(self, b"windowOpacity")
        self.anim_hide.setDuration(150)
        self.anim_hide.setStartValue(1.0)
        self.anim_hide.setEndValue(0.0)

    def show(self):
        super().show()
        self.anim_show.stop()
        self.anim_show.start()

    def hide(self):
        self.anim_hide.stop()
        self.anim_hide.finished.connect(super().hide)
        self.anim_hide.start()

    def update_status(self):
        if self.app.capture and self.app.capture.running:
            self.status_label.setText("⚡ Replay Buffer Active")
        else:
            self.status_label.setText("⏸ Capture Stopped")

    def on_save(self):
        self.app.save_replay()

    def on_open_viewer(self):
        self.app.show_player()

    def on_settings(self):
        self.app.open_settings()

    def on_exit(self):
        self.app.quit_app()


# ----------------------------------------------------------------------
# Main Application
# ----------------------------------------------------------------------
class InstantReplayApp(QApplication):
    def __init__(self, argv):
        super().__init__(argv)
        self.setQuitOnLastWindowClosed(False)

        self.settings = Settings()
        self.capture = CaptureManager(self.settings)
        self.capture.error_occurred.connect(self.on_capture_error)

        self.saver = ReplaySaver(self.capture, self.settings)
        self.saver.finished.connect(self.on_replay_saved)
        self.saver.error_occurred.connect(self.on_save_error)

        self.player = None
        self.overlay = OverlayWindow(self)
        self.overlay.hide()

        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(self.style().standardIcon(QStyle.SP_ComputerIcon))
        tray_menu = QMenu()
        show_action = tray_menu.addAction("Show Overlay")
        show_action.triggered.connect(self.toggle_overlay)
        settings_action = tray_menu.addAction("Settings")
        settings_action.triggered.connect(self.open_settings)
        tray_menu.addSeparator()
        quit_action = tray_menu.addAction("Exit")
        quit_action.triggered.connect(self.quit_app)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

        self.hotkey_listener = None
        self.register_hotkeys()
        self.start_capture()

    def start_capture(self):
        self.capture.start_capture()

    def register_hotkeys(self):
        try:
            save_hk = self.settings.get("save_hotkey", "ctrl+s")
            overlay_hk = self.settings.get("overlay_hotkey", "ctrl+v")

            # Преобразуем "ctrl+s" в формат pynput: "<ctrl>+s"
            def parse_hotkey(hk):
                parts = hk.lower().split("+")
                key = parts[-1]
                modifiers = parts[:-1]
                if modifiers:
                    return "<" + ">+<".join(modifiers) + ">+" + key
                return key

            save_combo = parse_hotkey(save_hk)
            overlay_combo = parse_hotkey(overlay_hk)

            self.hotkey_listener = pynput_keyboard.GlobalHotKeys({
                save_combo: self.on_hotkey_save,
                overlay_combo: self.on_hotkey_overlay
            })
            self.hotkey_listener.start()
        except Exception as e:
            QMessageBox.warning(None, "Hotkey Error", f"Could not register hotkeys: {e}")

    def unregister_hotkeys(self):
        if self.hotkey_listener:
            self.hotkey_listener.stop()

    def re_register_hotkeys(self):
        self.unregister_hotkeys()
        self.register_hotkeys()

    def on_hotkey_save(self):
        # Вызывается из потока pynput, перекидываем в главный поток
        QTimer.singleShot(0, self.save_replay)

    def on_hotkey_overlay(self):
        QTimer.singleShot(0, self.toggle_overlay)

    def save_replay(self):
        self.saver.save_replay()

    def toggle_overlay(self):
        if self.overlay.isVisible():
            self.overlay.hide()
        else:
            self.overlay.show()
            self.overlay.raise_()
            self.overlay.activateWindow()

    def show_player(self):
        if not self.player:
            self.player = ReplayPlayer()
            self.player.show()
        else:
            self.player.show()
            self.player.raise_()

    def open_settings(self):
        dlg = SettingsDialog(self.settings)
        if dlg.exec_() == QDialog.Accepted:
            self.re_register_hotkeys()
            self.restart_capture()

    def restart_capture(self):
        self.capture.stop_capture()
        self.start_capture()

    def on_capture_error(self, msg):
        QMessageBox.critical(None, "Capture Error", msg)

    def on_replay_saved(self, path):
        self.tray_icon.showMessage(
            "Replay Saved",
            f"Saved to {os.path.basename(path)}",
            QSystemTrayIcon.Information,
            3000
        )

    def on_save_error(self, msg):
        QMessageBox.warning(None, "Save Error", msg)

    def quit_app(self):
        self.capture.stop_capture()
        self.unregister_hotkeys()
        self.tray_icon.hide()
        self.quit()


# ----------------------------------------------------------------------
def main():
    app = InstantReplayApp(sys.argv)
    app.setStyle(QStyleFactory.create("Fusion"))
    app.setStyleSheet("""
        QMainWindow, QDialog, QWidget {
            background-color: #2C2543;
            color: #D8D0F0;
        }
        QMenuBar {
            background-color: #3A3153;
        }
        QMenuBar::item:selected {
            background-color: #5B4B8C;
        }
        QMenu {
            background-color: #3A3153;
            border: 1px solid #5B4B8C;
        }
        QMenu::item:selected {
            background-color: #5B4B8C;
        }
    """)
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()