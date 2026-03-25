from __future__ import annotations

import socket
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .matlab_bridge import (
    DEFAULT_CONFIG_PATH,
    MatlabSession,
    MatlabSessionError,
    SessionConfig,
    build_import_command,
    parse_session_configs,
)


class ScanImageSignals(QObject):
    log_message = pyqtSignal(str)
    session_status = pyqtSignal(str, str)
    udp_message = pyqtSignal(str, tuple)


@dataclass(slots=True)
class SessionRuntime:
    config: SessionConfig
    session: MatlabSession | None = None
    status: str = "stopped"
    lock: threading.Lock = field(default_factory=threading.Lock)


class UdpListener(threading.Thread):
    def __init__(self, host: str, port: int, signals: ScanImageSignals):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.signals = signals
        self._stop_event = threading.Event()
        self._socket: socket.socket | None = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket = sock
        try:
            sock.bind((self.host, self.port))
            sock.settimeout(0.2)
            self.signals.log_message.emit(f"UDP listener started on {self.host}:{self.port}")
            while not self._stop_event.is_set():
                try:
                    payload, address = sock.recvfrom(8192)
                except socket.timeout:
                    continue
                except OSError:
                    break
                message = payload.decode("utf-8", errors="replace").strip()
                self.signals.udp_message.emit(message, address)
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self.signals.log_message.emit("UDP listener stopped")

    def stop(self) -> None:
        self._stop_event.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass


class ScanImageControlWidget(QWidget):
    def __init__(
        self,
        schema_path_provider: Callable[[], Path | None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.schema_path_provider = schema_path_provider
        self.config_path = (Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_PATH).resolve()
        self.signals = ScanImageSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.session_status.connect(self._set_session_status)
        self.signals.udp_message.connect(self._handle_udp_message)
        self._udp_listener: UdpListener | None = None
        self._runtimes: dict[str, SessionRuntime] = {}
        self._build_ui()
        self.reload_config()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        config_box = QGroupBox("ScanImage Bridge")
        config_form = QFormLayout(config_box)
        self.config_path_label = QLabel(str(self.config_path))
        self.config_path_label.setWordWrap(True)
        self.reload_config_btn = QPushButton("Reload Config")
        config_form.addRow("Config", self.config_path_label)
        config_form.addRow("", self.reload_config_btn)
        layout.addWidget(config_box)

        sessions_box = QGroupBox("Sessions")
        sessions_layout = QVBoxLayout(sessions_box)
        self.session_list = QListWidget()
        self.session_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        sessions_layout.addWidget(self.session_list)

        session_buttons = QGridLayout()
        self.start_matlab_btn = QPushButton("Start MATLAB")
        self.stop_matlab_btn = QPushButton("Stop MATLAB")
        self.start_scanimage_btn = QPushButton("Start ScanImage")
        self.stop_scanimage_btn = QPushButton("Stop ScanImage")
        self.import_patterns_btn = QPushButton("Import Patterns")
        self.focus_btn = QPushButton("Focus")
        self.acquire_btn = QPushButton("Acquire")
        self.stop_acq_btn = QPushButton("Stop Acquisition")

        button_specs = [
            (self.start_matlab_btn, 0, 0),
            (self.stop_matlab_btn, 0, 1),
            (self.start_scanimage_btn, 1, 0),
            (self.stop_scanimage_btn, 1, 1),
            (self.import_patterns_btn, 2, 0),
            (self.focus_btn, 2, 1),
            (self.acquire_btn, 3, 0),
            (self.stop_acq_btn, 3, 1),
        ]
        for button, row, col in button_specs:
            session_buttons.addWidget(button, row, col)
        sessions_layout.addLayout(session_buttons)
        layout.addWidget(sessions_box)

        udp_box = QGroupBox("UDP Trigger")
        udp_layout = QGridLayout(udp_box)
        self.udp_host_edit = QLineEdit("0.0.0.0")
        self.udp_port_spin = QSpinBox()
        self.udp_port_spin.setRange(1, 65535)
        self.udp_port_spin.setValue(5005)
        self.udp_start_btn = QPushButton("Start UDP Listener")
        self.udp_stop_btn = QPushButton("Stop UDP Listener")
        self.udp_stop_btn.setEnabled(False)
        self.udp_help_label = QLabel(
            "Accepted UDP messages: acquire, start_acquisition, focus, stop, import, start_scanimage, stop_scanimage"
        )
        self.udp_help_label.setWordWrap(True)
        udp_layout.addWidget(QLabel("Host"), 0, 0)
        udp_layout.addWidget(self.udp_host_edit, 0, 1)
        udp_layout.addWidget(QLabel("Port"), 1, 0)
        udp_layout.addWidget(self.udp_port_spin, 1, 1)
        udp_layout.addWidget(self.udp_start_btn, 2, 0)
        udp_layout.addWidget(self.udp_stop_btn, 2, 1)
        udp_layout.addWidget(self.udp_help_label, 3, 0, 1, 2)
        layout.addWidget(udp_box)

        log_box = QGroupBox("Debug Log")
        log_layout = QVBoxLayout(log_box)
        self.log_list = QListWidget()
        self.clear_log_btn = QPushButton("Clear Log")
        log_layout.addWidget(self.log_list)
        log_button_row = QHBoxLayout()
        log_button_row.addStretch(1)
        log_button_row.addWidget(self.clear_log_btn)
        log_layout.addLayout(log_button_row)
        layout.addWidget(log_box, 1)

        self.reload_config_btn.clicked.connect(self.reload_config)
        self.start_matlab_btn.clicked.connect(lambda: self._run_for_selected("start MATLAB", self._start_matlab))
        self.stop_matlab_btn.clicked.connect(lambda: self._run_for_selected("stop MATLAB", self._stop_matlab))
        self.start_scanimage_btn.clicked.connect(lambda: self._run_for_selected("start ScanImage", self._start_scanimage))
        self.stop_scanimage_btn.clicked.connect(lambda: self._run_for_selected("stop ScanImage", self._stop_scanimage))
        self.import_patterns_btn.clicked.connect(self.import_patterns_for_selected)
        self.focus_btn.clicked.connect(lambda: self._run_for_selected("start focus", self._start_focus))
        self.acquire_btn.clicked.connect(lambda: self._run_for_selected("start acquisition", self._start_acquisition))
        self.stop_acq_btn.clicked.connect(lambda: self._run_for_selected("stop acquisition", self._stop_acquisition))
        self.udp_start_btn.clicked.connect(self.start_udp_listener)
        self.udp_stop_btn.clicked.connect(self.stop_udp_listener)
        self.clear_log_btn.clicked.connect(self.log_list.clear)

    def reload_config(self) -> None:
        if any(runtime.session is not None for runtime in self._runtimes.values()):
            QMessageBox.warning(self, "Reload config", "Stop all MATLAB sessions before reloading the ScanImage config.")
            return

        try:
            configs = parse_session_configs(self.config_path, Path(__file__).resolve().parents[2])
        except Exception as exc:
            QMessageBox.warning(self, "Invalid config", str(exc))
            self.signals.log_message.emit(f"Failed to load ScanImage config: {exc}")
            return

        self._runtimes = {config.name: SessionRuntime(config=config) for config in configs}
        self.session_list.clear()
        for config in configs:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, config.name)
            item.setSelected(True)
            self.session_list.addItem(item)
            self._set_session_status(config.name, "stopped")
        self.signals.log_message.emit(f"Loaded ScanImage config for sessions: {', '.join(self._runtimes)}")

    def shutdown(self) -> None:
        self.stop_udp_listener()
        for name in list(self._runtimes):
            try:
                self._stop_matlab(name)
            except Exception as exc:
                self.signals.log_message.emit(f"[{name}] shutdown warning: {exc}")

    def start_udp_listener(self) -> None:
        if self._udp_listener is not None:
            return
        host = self.udp_host_edit.text().strip() or "0.0.0.0"
        port = self.udp_port_spin.value()
        self._udp_listener = UdpListener(host, port, self.signals)
        self._udp_listener.start()
        self.udp_start_btn.setEnabled(False)
        self.udp_stop_btn.setEnabled(True)

    def stop_udp_listener(self) -> None:
        if self._udp_listener is None:
            return
        self._udp_listener.stop()
        self._udp_listener = None
        self.udp_start_btn.setEnabled(True)
        self.udp_stop_btn.setEnabled(False)

    def _selected_session_names(self) -> list[str]:
        items = self.session_list.selectedItems()
        if not items:
            return list(self._runtimes)
        return [item.data(Qt.ItemDataRole.UserRole) for item in items]

    def _run_for_selected(self, label: str, fn: Callable[[str], None]) -> None:
        session_names = self._selected_session_names()
        if not session_names:
            QMessageBox.warning(self, "No sessions", "No ScanImage sessions are configured.")
            return

        for session_name in session_names:
            worker = threading.Thread(
                target=self._run_action,
                args=(session_name, label, fn),
                daemon=True,
            )
            worker.start()

    def import_patterns_for_selected(self) -> None:
        schema_path = self.schema_path_provider()
        if schema_path is None:
            self.signals.log_message.emit("Pattern import cancelled: no saved schema path available")
            return
        self._run_for_selected(
            "import patterns",
            lambda session_name: self._import_patterns(session_name, schema_path),
        )

    def _run_action(self, session_name: str, label: str, fn: Callable[[str], None]) -> None:
        self.signals.log_message.emit(f"[{session_name}] {label}")
        try:
            fn(session_name)
        except Exception as exc:
            self.signals.log_message.emit(f"[{session_name}] ERROR: {exc}")

    def _ensure_session(self, session_name: str) -> SessionRuntime:
        runtime = self._runtimes[session_name]
        with runtime.lock:
            if runtime.session is None:
                runtime.session = MatlabSession(runtime.config)
                runtime.session.start()
                runtime.status = "matlab"
                self.signals.session_status.emit(session_name, runtime.status)
                self.signals.log_message.emit(f"[{session_name}] MATLAB session started")
        return runtime

    def _start_matlab(self, session_name: str) -> None:
        self._ensure_session(session_name)

    def _stop_matlab(self, session_name: str) -> None:
        runtime = self._runtimes[session_name]
        with runtime.lock:
            if runtime.session is not None:
                runtime.session.stop()
                runtime.session = None
            runtime.status = "stopped"
            self.signals.session_status.emit(session_name, runtime.status)
            self.signals.log_message.emit(f"[{session_name}] MATLAB session stopped")

    def _start_scanimage(self, session_name: str) -> None:
        runtime = self._ensure_session(session_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                runtime.config.launch_scanimage_command,
                timeout_s=runtime.config.command_timeout_s,
            )
            runtime.status = "scanimage"
            self.signals.session_status.emit(session_name, runtime.status)
            self._emit_lines(session_name, lines)

    def _stop_scanimage(self, session_name: str) -> None:
        runtime = self._ensure_session(session_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                runtime.config.shutdown_scanimage_command,
                timeout_s=runtime.config.command_timeout_s,
            )
            runtime.status = "matlab"
            self.signals.session_status.emit(session_name, runtime.status)
            self._emit_lines(session_name, lines)

    def _import_patterns(self, session_name: str, schema_path: Path) -> None:
        runtime = self._ensure_session(session_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_import_command(schema_path, runtime.config),
                timeout_s=runtime.config.command_timeout_s,
            )
            runtime.status = "patterns imported"
            self.signals.session_status.emit(session_name, runtime.status)
            self._emit_lines(session_name, lines)

    def _start_focus(self, session_name: str) -> None:
        runtime = self._ensure_session(session_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                runtime.config.focus_command,
                timeout_s=runtime.config.command_timeout_s,
            )
            runtime.status = "focus"
            self.signals.session_status.emit(session_name, runtime.status)
            self._emit_lines(session_name, lines)

    def _start_acquisition(self, session_name: str) -> None:
        runtime = self._ensure_session(session_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                runtime.config.acquire_command,
                timeout_s=runtime.config.command_timeout_s,
            )
            runtime.status = "acquiring"
            self.signals.session_status.emit(session_name, runtime.status)
            self._emit_lines(session_name, lines)

    def _stop_acquisition(self, session_name: str) -> None:
        runtime = self._ensure_session(session_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                runtime.config.stop_command,
                timeout_s=runtime.config.command_timeout_s,
            )
            runtime.status = "scanimage"
            self.signals.session_status.emit(session_name, runtime.status)
            self._emit_lines(session_name, lines)

    def _emit_lines(self, session_name: str, lines: list[str]) -> None:
        if not lines:
            self.signals.log_message.emit(f"[{session_name}] command completed")
            return
        for line in lines:
            cleaned = line.strip()
            if cleaned:
                self.signals.log_message.emit(f"[{session_name}] {cleaned}")

    def _append_log(self, message: str) -> None:
        self.log_list.addItem(message)
        self.log_list.scrollToBottom()

    def _set_session_status(self, session_name: str, status: str) -> None:
        for index in range(self.session_list.count()):
            item = self.session_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == session_name:
                item.setText(f"{session_name} [{status}]")
                break

    def _handle_udp_message(self, message: str, address: tuple) -> None:
        self.signals.log_message.emit(f"[udp {address[0]}:{address[1]}] {message}")
        normalized = message.strip().lower()
        mapping: dict[str, Callable[[str], None]] = {
            "acquire": self._start_acquisition,
            "start_acquisition": self._start_acquisition,
            "grab": self._start_acquisition,
            "focus": self._start_focus,
            "stop": self._stop_acquisition,
            "abort": self._stop_acquisition,
            "start_scanimage": self._start_scanimage,
            "stop_scanimage": self._stop_scanimage,
        }
        if normalized == "import":
            self.import_patterns_for_selected()
            return
        action = mapping.get(normalized)
        if action is None:
            self.signals.log_message.emit(f"[udp] ignored unknown command '{message}'")
            return
        self._run_for_selected(f"UDP {normalized}", action)
