from __future__ import annotations

import configparser
import json
import socket
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .legacy_matlab_codec import build_ready_message, extract_legacy_command
from .io import load_schema
from .matlab_bridge import (
    ExperimentContext,
    MachineConfig,
    MatlabSession,
    PathConfig,
    autodetect_machine_name,
    build_experiment_context,
    build_global_preamble,
    build_import_command,
    build_run_script_command,
    build_test_photostim_command,
    context_to_matlab_variables,
    list_config_names,
    list_machine_names,
    load_machine_config,
    matlab_string,
)


class ScanImageSignals(QObject):
    log_message = pyqtSignal(str)
    path_status = pyqtSignal(str, str)
    path_udp_log = pyqtSignal(str, str)


@dataclass
class PreparedPhotostimState:
    schema_path: Path | None = None
    schema_name: str = ""
    exp_id: str = ""
    prepared_seq_nums: list[int] = field(default_factory=list)
    prepared_sequence_names: list[str] = field(default_factory=list)
    imported_pattern_names: list[str] = field(default_factory=list)
    pattern_to_schema_index: dict[str, int] = field(default_factory=dict)
    pattern_to_stimulus_group: dict[str, int] = field(default_factory=dict)

    def reset(self) -> None:
        self.schema_path = None
        self.schema_name = ""
        self.exp_id = ""
        self.prepared_seq_nums = []
        self.prepared_sequence_names = []
        self.imported_pattern_names = []
        self.pattern_to_schema_index = {}
        self.pattern_to_stimulus_group = {}


@dataclass
class PathRuntime:
    path_config: PathConfig
    session: MatlabSession | None = None
    udp_listener: "UdpListener | None" = None
    status: str = "stopped"
    launched: bool = False
    last_context: ExperimentContext | None = None
    prepared_photostim: PreparedPhotostimState = field(default_factory=PreparedPhotostimState)
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass
class PathTabWidgets:
    tab: QWidget
    status_label: QLabel
    listener_label: QLabel
    udp_text: QPlainTextEdit
    launch_btn: QPushButton
    focus_btn: QPushButton
    acquire_btn: QPushButton
    stop_acq_btn: QPushButton
    test_slm_btn: QPushButton
    start_listener_btn: QPushButton
    stop_listener_btn: QPushButton


class UdpListener(threading.Thread):
    def __init__(self, path_name: str, host: str, port: int, signals: _ControlSignals):
        super().__init__(daemon=True)
        self.path_name = path_name
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
            self.signals.log_message.emit(f"[{self.path_name}] UDP listener started on {self.host}:{self.port}")
            while not self._stop_event.is_set():
                try:
                    payload, address = sock.recvfrom(8192)
                except socket.timeout:
                    continue
                except OSError:
                    break
                try:
                    self.signals.udp_message.emit(self.path_name, payload, address)
                except Exception as exc:  # pragma: no cover - defensive thread guard
                    self.signals.log_message.emit(
                        f"[{self.path_name}] UDP listener handler error: {exc}"
                    )
        finally:
            try:
                sock.close()
            except OSError:
                pass
            self.signals.log_message.emit(f"[{self.path_name}] UDP listener stopped")

    def stop(self) -> None:
        self._stop_event.set()
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass

    def send(self, payload: bytes, address: tuple[str, int]) -> None:
        if self._socket is None:
            return
        try:
            self._socket.sendto(payload, address)
        except OSError:
            pass


class _ControlSignals(ScanImageSignals):
    udp_message = pyqtSignal(str, bytes, tuple)


class ScanImageControlWidget(QWidget):
    def __init__(self, schema_path_provider: Callable[[], Path | None], parent: QWidget | None = None):
        super().__init__(parent)
        self.schema_path_provider = schema_path_provider
        self.repo_root = Path(__file__).resolve().parents[2]
        self.signals = _ControlSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.path_status.connect(self._set_path_status)
        self.signals.path_udp_log.connect(self._append_path_udp_log)
        self.signals.udp_message.connect(self._handle_udp_message)
        self.machine_config: MachineConfig | None = None
        self._runtimes: dict[str, PathRuntime] = {}
        self._path_tabs: dict[str, PathTabWidgets] = {}
        self._ignore_combo_changes = False
        self._current_machine_name = ""
        self._current_config_name = ""
        self._last_exp_id = ""
        self.save_root, self.schema_root = self._load_path_roots()
        self._build_ui()
        self.reload_discovery()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        config_box = QGroupBox("ScanImage Config")
        config_layout = QHBoxLayout(config_box)
        button_column = QVBoxLayout()
        button_column.setSpacing(6)
        self.start_config_btn = QPushButton("Start Config")
        self.stop_config_btn = QPushButton("Stop Config")
        self.reload_btn = QPushButton("Reload Configs")
        self.start_config_btn.setStyleSheet("color: #15803d;")
        self.stop_config_btn.setStyleSheet("color: #b91c1c;")
        button_column.addWidget(self.start_config_btn)
        button_column.addWidget(self.stop_config_btn)
        button_column.addWidget(self.reload_btn)
        button_column.addStretch(1)
        config_layout.addLayout(button_column)

        config_form_container = QWidget()
        config_form = QFormLayout(config_form_container)
        self.machine_combo = QComboBox()
        self.config_combo = QComboBox()
        config_form.addRow("Machine", self.machine_combo)
        config_form.addRow("Config", self.config_combo)
        config_layout.addWidget(config_form_container, 1)
        layout.addWidget(config_box)

        self.paths_box = QGroupBox("Paths")
        paths_layout = QVBoxLayout(self.paths_box)
        self.path_tabs = QTabWidget()
        paths_layout.addWidget(self.path_tabs)
        layout.addWidget(self.paths_box, 1)

        log_box = QGroupBox("Debug Log")
        log_layout = QVBoxLayout(log_box)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.clear_log_btn = QPushButton("Clear Log")
        log_layout.addWidget(self.log_text)
        clear_row = QHBoxLayout()
        clear_row.addStretch(1)
        clear_row.addWidget(self.clear_log_btn)
        log_layout.addLayout(clear_row)
        layout.addWidget(log_box, 1)

        self.reload_btn.clicked.connect(self.reload_discovery)
        self.machine_combo.currentTextChanged.connect(self._on_machine_changed)
        self.config_combo.currentTextChanged.connect(self._on_config_changed)
        self.start_config_btn.clicked.connect(self.start_config)
        self.stop_config_btn.clicked.connect(self.stop_config)
        self.clear_log_btn.clicked.connect(self.log_text.clear)

    def reload_discovery(self) -> None:
        machine_names = list_machine_names(self.repo_root)
        self._ignore_combo_changes = True
        self.machine_combo.clear()
        self.machine_combo.addItems(machine_names)
        desired_machine = self._current_machine_name or autodetect_machine_name(self.repo_root)
        if desired_machine and desired_machine in machine_names:
            self.machine_combo.setCurrentText(desired_machine)
        elif machine_names:
            self.machine_combo.setCurrentIndex(0)
        self._ignore_combo_changes = False
        self._populate_configs_for_machine(self.machine_combo.currentText())
        self.signals.log_message.emit(f"Discovered machines: {', '.join(machine_names) if machine_names else 'none'}")

    def _load_path_roots(self) -> tuple[Path, Path]:
        config = configparser.ConfigParser()
        config.read(self.repo_root / "config.ini")
        raw_save_root = config.get("paths", "save_root", fallback="./data")
        raw_schema_root = config.get("paths", "schema_root", fallback="./data")
        save_root = Path(raw_save_root).expanduser()
        if not save_root.is_absolute():
            save_root = (self.repo_root / save_root).resolve()
        schema_root = Path(raw_schema_root).expanduser()
        if not schema_root.is_absolute():
            schema_root = (self.repo_root / schema_root).resolve()
        return save_root, schema_root

    def shutdown(self) -> None:
        for path_name in list(self._runtimes):
            try:
                self._stop_path(path_name)
            except Exception as exc:
                self.signals.log_message.emit(f"[{path_name}] shutdown warning: {exc}")

    def _has_active_paths(self) -> bool:
        return any(runtime.session is not None for runtime in self._runtimes.values())

    def _on_machine_changed(self, machine_name: str) -> None:
        if self._ignore_combo_changes:
            return
        if self._has_active_paths():
            QMessageBox.warning(self, "Machine change blocked", "Stop all paths before switching machine/config.")
            self._reset_combo_selection()
            return
        self._populate_configs_for_machine(machine_name)

    def _populate_configs_for_machine(self, machine_name: str) -> None:
        config_names = list_config_names(self.repo_root, machine_name) if machine_name else []
        self._ignore_combo_changes = True
        self.config_combo.clear()
        self.config_combo.addItems(config_names)
        desired_config = self._current_config_name
        if desired_config and desired_config in config_names:
            self.config_combo.setCurrentText(desired_config)
        elif config_names:
            self.config_combo.setCurrentIndex(0)
        self._ignore_combo_changes = False
        self._load_selected_config()

    def _on_config_changed(self, _: str) -> None:
        if self._ignore_combo_changes:
            return
        if self._has_active_paths():
            QMessageBox.warning(self, "Config change blocked", "Stop all paths before switching machine/config.")
            self._reset_combo_selection()
            return
        self._load_selected_config()

    def _reset_combo_selection(self) -> None:
        self._ignore_combo_changes = True
        if self._current_machine_name:
            self.machine_combo.setCurrentText(self._current_machine_name)
            self._populate_configs_for_machine(self._current_machine_name)
            if self._current_config_name:
                self.config_combo.setCurrentText(self._current_config_name)
        self._ignore_combo_changes = False

    def _load_selected_config(self) -> None:
        machine_name = self.machine_combo.currentText().strip()
        config_name = self.config_combo.currentText().strip()
        if not machine_name or not config_name:
            self.machine_config = None
            self._runtimes = {}
            self._path_tabs = {}
            self.path_tabs.clear()
            self.paths_box.setTitle("Paths")
            return

        try:
            machine_config = load_machine_config(self.repo_root, machine_name, config_name)
        except Exception as exc:
            QMessageBox.warning(self, "Invalid ScanImage config", str(exc))
            self.signals.log_message.emit(f"Failed to load {machine_name}/{config_name}: {exc}")
            return

        self.machine_config = machine_config
        self._current_machine_name = machine_name
        self._current_config_name = config_name
        self._runtimes = {
            path_name: PathRuntime(path_config=path_config)
            for path_name, path_config in machine_config.paths.items()
        }
        self._rebuild_path_tabs()
        self.paths_box.setTitle(f"Paths - {config_name}")
        self.signals.log_message.emit(
            f"Loaded {machine_name}/{config_name} with paths: {', '.join(machine_config.launch_order)}"
        )

    def _rebuild_path_tabs(self) -> None:
        self.path_tabs.clear()
        self._path_tabs = {}
        if self.machine_config is None:
            return
        for path_name in self.machine_config.launch_order:
            self._add_path_tab(path_name)

    def _add_path_tab(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        tab = QWidget()
        layout = QVBoxLayout(tab)

        info_box = QGroupBox("Path")
        info_form = QFormLayout(info_box)
        status_label = QLabel("stopped")
        listener_label = QLabel(self._listener_summary(path_name))
        listener_label.setWordWrap(True)
        info_form.addRow("Status", status_label)
        info_form.addRow("UDP listener", listener_label)
        layout.addWidget(info_box)

        buttons_box = QGroupBox("Actions")
        buttons_layout = QHBoxLayout(buttons_box)
        launch_btn = QPushButton("Launch Path")
        focus_btn = QPushButton("Focus")
        acquire_btn = QPushButton("Acquire")
        stop_acq_btn = QPushButton("Stop")
        test_slm_btn = QPushButton("Test SLM")
        start_listener_btn = QPushButton("Start Listener")
        stop_listener_btn = QPushButton("Stop Listener")
        for button in [
            launch_btn,
            acquire_btn,
            focus_btn,
            stop_acq_btn,
            test_slm_btn,
            start_listener_btn,
            stop_listener_btn,
        ]:
            buttons_layout.addWidget(button)
        buttons_layout.addStretch(1)
        layout.addWidget(buttons_box)

        udp_box = QGroupBox("UDP Messages")
        udp_layout = QVBoxLayout(udp_box)
        udp_text = QPlainTextEdit()
        udp_text.setReadOnly(True)
        udp_layout.addWidget(udp_text)
        layout.addWidget(udp_box, 1)

        widgets = PathTabWidgets(
            tab=tab,
            status_label=status_label,
            listener_label=listener_label,
            udp_text=udp_text,
            launch_btn=launch_btn,
            focus_btn=focus_btn,
            acquire_btn=acquire_btn,
            stop_acq_btn=stop_acq_btn,
            test_slm_btn=test_slm_btn,
            start_listener_btn=start_listener_btn,
            stop_listener_btn=stop_listener_btn,
        )
        self._path_tabs[path_name] = widgets
        self.path_tabs.addTab(tab, path_name)

        launch_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "launch path", self._launch_path))
        focus_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "focus", self._focus_path))
        acquire_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "acquire", self._acquire_path_from_ui))
        stop_acq_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "stop acquisition", self._stop_acquisition))
        test_slm_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "test slm", self._test_photostim_api))
        start_listener_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "start listener", self._start_listener))
        stop_listener_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "stop listener", self._stop_listener))

        self._set_path_status(path_name, runtime.status)
        self._refresh_path_listener_info(path_name)

    def _spawn_action(self, path_name: str, label: str, fn: Callable[[str], None]) -> None:
        threading.Thread(target=self._run_action, args=(path_name, label, fn), daemon=True).start()

    def _run_action(self, path_name: str, label: str, fn: Callable[[str], None]) -> bool:
        self.signals.log_message.emit(f"[{path_name}] {label}")
        try:
            fn(path_name)
        except Exception as exc:
            self.signals.log_message.emit(f"[{path_name}] ERROR: {exc}")
            return False
        return True

    def start_config(self) -> None:
        if self.machine_config is None:
            QMessageBox.warning(self, "No config", "No ScanImage config is selected.")
            return

        def worker() -> None:
            assert self.machine_config is not None
            order = self.machine_config.launch_order
            for index, path_name in enumerate(order):
                if not self._run_action(path_name, "launch path", self._launch_path):
                    break
                if index < len(order) - 1 and self.machine_config.launch_delay_s > 0:
                    delay = self.machine_config.launch_delay_s
                    self.signals.log_message.emit(
                        f"[config] waiting {delay:.1f}s before launching {order[index + 1]}"
                    )
                    time.sleep(delay)

        threading.Thread(target=worker, daemon=True).start()

    def stop_config(self) -> None:
        if self.machine_config is None:
            return

        def worker() -> None:
            assert self.machine_config is not None
            for path_name in reversed(self.machine_config.launch_order):
                self._run_action(path_name, "stop path", self._stop_path)

        threading.Thread(target=worker, daemon=True).start()

    def import_patterns_for_photostim(self) -> None:
        if self.machine_config is None:
            return
        schema_path = self.schema_path_provider()
        if schema_path is None:
            self.signals.log_message.emit("Pattern import cancelled: no saved schema path available")
            return
        if not self.machine_config.photostim_path:
            self.signals.log_message.emit("Pattern import skipped: no photostim path configured")
            return
        path_name = self.machine_config.photostim_path
        self._spawn_action(path_name, "import patterns", lambda name: self._import_patterns(name, schema_path))

    def _ensure_session(self, path_name: str) -> PathRuntime:
        runtime = self._runtimes[path_name]
        with runtime.lock:
            if runtime.session is None:
                runtime.session = MatlabSession(runtime.path_config)
                runtime.session.start(startup_command=self._build_launch_startup_command(runtime.path_config))
                runtime.status = "simulated" if runtime.session.simulated else "ready"
                runtime.launched = True
                self.signals.path_status.emit(path_name, runtime.status)
                if runtime.session.simulated:
                    self.signals.log_message.emit(f"[{path_name}] simulated MATLAB session started")
                else:
                    self.signals.log_message.emit(f"[{path_name}] MATLAB session started")
        if runtime.path_config.listener_auto_start:
            self._start_listener(path_name)
        return runtime

    def _build_launch_startup_command(self, path_config: PathConfig) -> str:
        return "; ".join(
            [
                f"addpath(genpath({matlab_string(str(path_config.repo_matlab_path))}))",
                f"cd({matlab_string(str(path_config.directory))})",
                "run('launch.m')",
            ]
        )

    def _launch_path(self, path_name: str) -> None:
        self._ensure_session(path_name)

    def _stop_path(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        self._stop_listener(path_name)
        with runtime.lock:
            if runtime.session is not None:
                runtime.session.stop()
                runtime.session = None
            runtime.status = "stopped"
            runtime.launched = False
            runtime.last_context = None
            runtime.prepared_photostim.reset()
            self.signals.path_status.emit(path_name, runtime.status)
            self.signals.log_message.emit(f"[{path_name}] MATLAB path stopped")

    def _focus_path(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                "\n".join([build_global_preamble(runtime.path_config), runtime.path_config.focus_command]),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "focus"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _current_exp_id(self) -> str:
        return self._last_exp_id.strip()

    def _acquire_path_from_ui(self, path_name: str) -> None:
        exp_id = self._current_exp_id()
        if not exp_id:
            raise ValueError("Exp ID is required for acquisition")
        self._start_acquisition(path_name, exp_id)

    def _start_acquisition(self, path_name: str, exp_id: str) -> None:
        runtime = self._ensure_session(path_name)
        context = build_experiment_context(runtime.path_config, exp_id)
        self._last_exp_id = exp_id
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_run_script_command(
                    runtime.path_config,
                    "start_script.m",
                    context_to_matlab_variables(context),
                ),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.last_context = context
            runtime.status = "acquiring"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _stop_acquisition(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        context_vars = (
            context_to_matlab_variables(runtime.last_context)
            if runtime.last_context is not None
            else None
        )
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_run_script_command(runtime.path_config, "stop_script.m", context_vars),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "ready"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _test_photostim_api(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_test_photostim_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "photostim test"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _import_patterns(self, path_name: str, schema_path: Path) -> None:
        self._import_pattern_subset(path_name, schema_path, None)

    def _import_pattern_subset(self, path_name: str, schema_path: Path, pattern_names: list[str] | None) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_import_command(schema_path, runtime.path_config, pattern_names=pattern_names),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "patterns imported"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _emit_lines(self, path_name: str, lines: list[str]) -> None:
        if not lines:
            self.signals.log_message.emit(f"[{path_name}] command completed")
            return
        for line in lines:
            cleaned = line.strip()
            if cleaned:
                self.signals.log_message.emit(f"[{path_name}] {cleaned}")

    def _append_log(self, message: str) -> None:
        self.log_text.appendPlainText(f"{self._timestamp()} {message}")
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _set_path_status(self, path_name: str, status: str) -> None:
        runtime = self._runtimes.get(path_name)
        if runtime is not None:
            runtime.status = status
        widgets = self._path_tabs.get(path_name)
        if widgets is not None:
            widgets.status_label.setText(status)
            index = self.path_tabs.indexOf(widgets.tab)
            if index >= 0:
                self.path_tabs.setTabText(index, f"{path_name} [{status}]")

    def _append_path_udp_log(self, path_name: str, message: str) -> None:
        widgets = self._path_tabs.get(path_name)
        if widgets is None:
            return
        widgets.udp_text.appendPlainText(f"{self._timestamp()} {message}")
        scrollbar = widgets.udp_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _timestamp(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _listener_summary(self, path_name: str) -> str:
        runtime = self._runtimes[path_name]
        state = "on" if runtime.udp_listener is not None else "off"
        cfg = runtime.path_config
        return f"{cfg.listener_host}:{cfg.listener_port} [{state}]"

    def _refresh_path_listener_info(self, path_name: str) -> None:
        widgets = self._path_tabs.get(path_name)
        if widgets is None:
            return
        widgets.listener_label.setText(self._listener_summary(path_name))

    def _handle_udp_message(self, path_name: str, payload: bytes, address: tuple) -> None:
        json_message = self._extract_json_command(payload)
        if json_message is not None:
            self._handle_json_udp_message(path_name, json_message, address)
            return

        legacy = extract_legacy_command(payload)
        if legacy is not None:
            self._handle_legacy_udp_message(path_name, legacy, address)
            return

        message = payload.decode("utf-8", errors="replace").strip()
        udp_line = f"[{path_name} udp {address[0]}:{address[1]}] text command={message}"
        self.signals.path_udp_log.emit(path_name, udp_line)
        self.signals.log_message.emit(udp_line)
        if not message:
            return

        command, exp_id = self._parse_plain_udp_command(message)
        if command in {"acquire", "start_acquisition", "grab", "gogo"}:
            exp_id = exp_id or self._current_exp_id()
            if not exp_id:
                self.signals.log_message.emit(f"[{path_name} udp] acquire ignored: no expID provided")
                return
            self._spawn_action(path_name, f"UDP {command}", lambda name: self._start_acquisition(name, exp_id))
            return
        if command == "focus":
            self._spawn_action(path_name, "UDP focus", self._focus_path)
            return
        if command in {"stop", "abort"}:
            self._spawn_action(path_name, f"UDP {command}", self._stop_acquisition)
            return
        if command == "import":
            self.import_patterns_for_photostim()
            return
        self.signals.log_message.emit(f"[{path_name} udp] ignored unknown command '{message}'")

    def _parse_plain_udp_command(self, message: str) -> tuple[str, str | None]:
        stripped = message.strip()
        if ":" in stripped:
            command, exp_id = stripped.split(":", 1)
            return command.strip().lower(), exp_id.strip() or None
        if " " in stripped:
            command, exp_id = stripped.split(None, 1)
            return command.strip().lower(), exp_id.strip() or None
        return stripped.lower(), None

    def _extract_json_command(self, payload: bytes) -> dict[str, object] | None:
        try:
            decoded = payload.decode("utf-8").strip()
        except UnicodeDecodeError:
            return None
        if not decoded.startswith("{"):
            return None
        try:
            parsed = json.loads(decoded)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _handle_json_udp_message(self, path_name: str, message: dict[str, object], address: tuple[str, int]) -> None:
        action = str(message.get("action", "")).strip()
        udp_line = f"[{path_name} udp {address[0]}:{address[1]}] json action={action} payload={message}"
        self.signals.path_udp_log.emit(path_name, udp_line)
        self.signals.log_message.emit(udp_line)

        if action != "prep_patterns":
            self.signals.log_message.emit(f"[{path_name} udp] ignored unknown json action '{action}'")
            return

        photostim_path = self.machine_config.photostim_path if self.machine_config is not None else None
        if not photostim_path:
            self._send_json_reply(
                path_name,
                address,
                {
                    "action": "prep_patterns",
                    "status": "error",
                    "error": "No photostim path configured",
                },
            )
            return

        schema_name = str(message.get("schema_name", "")).strip()
        exp_id = str(message.get("expID", "")).strip()
        seq_num_raw = message.get("seq_num")
        if not schema_name or not exp_id or seq_num_raw is None:
            self._send_json_reply(
                path_name,
                address,
                {
                    "action": "prep_patterns",
                    "status": "error",
                    "schema_name": schema_name,
                    "expID": exp_id,
                    "seq_num": seq_num_raw,
                    "error": "prep_patterns requires schema_name, expID, and seq_num",
                },
            )
            return

        try:
            seq_num = int(seq_num_raw)
            schema_path = self._resolve_schema_path(schema_name, exp_id)
            project = load_schema(schema_path)
            runtime = self._runtimes[photostim_path]
            prep_state = runtime.prepared_photostim
            if (
                prep_state.schema_path is None
                or prep_state.schema_path != schema_path
                or prep_state.exp_id != exp_id
                or prep_state.schema_name != schema_name
            ):
                prep_state.reset()
                prep_state.schema_path = schema_path
                prep_state.schema_name = schema_name
                prep_state.exp_id = exp_id

            if seq_num not in prep_state.prepared_seq_nums:
                prep_state.prepared_seq_nums.append(seq_num)

            prepared_sequence_names, pattern_names, pattern_to_schema_index = self._patterns_for_sequences(
                project,
                prep_state.prepared_seq_nums,
            )
        except Exception as exc:
            self._send_json_reply(
                path_name,
                address,
                {
                    "action": "prep_patterns",
                    "status": "error",
                    "schema_name": schema_name,
                    "expID": exp_id,
                    "seq_num": seq_num_raw,
                    "error": str(exc),
                },
            )
            return

        def worker() -> None:
            ok = self._run_action(
                photostim_path,
                "json prep_patterns",
                lambda name: self._import_pattern_subset(name, schema_path, pattern_names),
            )
            runtime = self._runtimes[photostim_path]
            prep_state = runtime.prepared_photostim
            status = "ready" if ok else "error"
            payload = {
                "action": "prep_patterns",
                "status": status,
                "schema_name": schema_name,
                "expID": exp_id,
                "seq_num": seq_num,
                "prepared_seq_nums": list(prep_state.prepared_seq_nums),
                "prepared_sequence_names": list(prepared_sequence_names),
                "pattern_names": pattern_names,
                "stimulus_groups": [],
            }
            if ok:
                prep_state.prepared_sequence_names = list(prepared_sequence_names)
                prep_state.imported_pattern_names = list(pattern_names)
                prep_state.pattern_to_schema_index = dict(pattern_to_schema_index)
                prep_state.pattern_to_stimulus_group = {
                    pattern_name: index + 1 for index, pattern_name in enumerate(pattern_names)
                }
                payload["stimulus_groups"] = [
                    {
                        "stimulus_group_num": prep_state.pattern_to_stimulus_group[pattern_name],
                        "pattern_name": pattern_name,
                        "pattern_num": prep_state.pattern_to_schema_index[pattern_name],
                    }
                    for pattern_name in pattern_names
                ]
                self.signals.log_message.emit(
                    f"[{photostim_path}] prepared {len(pattern_names)} stimulus group(s) for "
                    f"seq_num(s) {prep_state.prepared_seq_nums}"
                )
            if not ok:
                payload["error"] = "prep_patterns failed"
            self._send_json_reply(path_name, address, payload)

        threading.Thread(target=worker, daemon=True).start()

    def _send_json_reply(self, path_name: str, address: tuple[str, int], payload: dict[str, object]) -> None:
        runtime = self._runtimes.get(path_name)
        if runtime is None or runtime.udp_listener is None:
            self.signals.log_message.emit(f"[{path_name}] could not send JSON reply; listener is not running")
            return
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        runtime.udp_listener.send(encoded, address)
        line = f"[{path_name} udp {address[0]}:{address[1]}] send json {payload}"
        self.signals.path_udp_log.emit(path_name, line)
        self.signals.log_message.emit(line)

    def _resolve_schema_path(self, schema_name: str, exp_id: str) -> Path:
        animal_id = exp_id[14:] if len(exp_id) >= 15 else ""
        if not animal_id:
            raise FileNotFoundError(f"Could not derive animalID from expID '{exp_id}'")

        schema_dir = self.schema_root / animal_id / schema_name
        candidates = [
            schema_dir / "schema.yaml",
            schema_dir / "schema.yml",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate.resolve()

        raise FileNotFoundError(
            f"Schema not found for schema_name '{schema_name}' under '{self.schema_root / animal_id}'"
        )

    def _patterns_for_sequences(
        self,
        project,
        seq_nums: list[int],
    ) -> tuple[list[str], list[str], dict[str, int]]:
        sequence_names = list(project.sequences.keys())
        if not sequence_names:
            raise ValueError("Schema does not contain any sequences")
        if not seq_nums:
            raise ValueError("No seq_num values have been prepared")

        prepared_sequence_names: list[str] = []
        used_pattern_names: set[str] = set()
        for seq_num in seq_nums:
            if seq_num < 1 or seq_num > len(sequence_names):
                raise IndexError(f"seq_num {seq_num} is out of range for {len(sequence_names)} sequence(s)")
            sequence_name = sequence_names[seq_num - 1]
            prepared_sequence_names.append(sequence_name)
            sequence = project.sequences[sequence_name]
            for step in sequence.steps:
                used_pattern_names.add(step.pattern)

        if not used_pattern_names:
            raise ValueError("Prepared sequence set does not reference any patterns")

        schema_pattern_names = list(project.patterns.keys())
        pattern_names = [name for name in schema_pattern_names if name in used_pattern_names]
        if not pattern_names:
            raise ValueError("Prepared sequence set does not reference any schema patterns")

        pattern_to_schema_index = {
            name: index + 1 for index, name in enumerate(schema_pattern_names) if name in used_pattern_names
        }
        return prepared_sequence_names, pattern_names, pattern_to_schema_index

    def _handle_legacy_udp_message(self, path_name: str, message: dict[str, object], address: tuple[str, int]) -> None:
        message_type = str(message.get("messageType", ""))
        command = str(message.get("messageData", ""))
        meta = message.get("meta")
        confirm_id = message.get("confirmID")
        details = [f"type={message_type}", f"command={command}"]
        if meta not in (None, [], ""):
            details.append(f"meta={meta}")
        if confirm_id not in (None, ""):
            details.append(f"confirmID={confirm_id}")
        udp_line = f"[{path_name} udp {address[0]}:{address[1]}] legacy " + " ".join(details)
        self.signals.path_udp_log.emit(path_name, udp_line)
        self.signals.log_message.emit(udp_line)
        if str(message.get("messageType", "")) != "COM":
            self.signals.log_message.emit(f"[{path_name} udp] ignored legacy packet with unsupported messageType")
            return

        command = str(message.get("messageData", "")).upper()
        if command == "READY":
            self.signals.log_message.emit(f"[{path_name} udp] ignored inbound READY packet")
            return

        confirm_id = int(float(message.get("confirmID", 0) or 0))
        ready_payload = build_ready_message(confirm_id)

        def send_ready() -> None:
            runtime = self._runtimes[path_name]
            listener = runtime.udp_listener
            reply_address = address
            cfg = runtime.path_config
            if cfg.reply_host and cfg.reply_port > 0:
                reply_address = (cfg.reply_host, cfg.reply_port)
            if listener is not None:
                listener.send(ready_payload, reply_address)
            ready_line = f"[{path_name} udp {reply_address[0]}:{reply_address[1]}] send legacy READY"
            self.signals.path_udp_log.emit(path_name, ready_line)
            self.signals.log_message.emit(ready_line)

        if command == "GOGO":
            meta = message.get("meta")
            exp_id = None
            if isinstance(meta, list) and meta:
                exp_id = str(meta[0])
            if not exp_id:
                self.signals.log_message.emit(f"[{path_name} udp] ignored legacy GOGO without expID in meta")
                return

            def worker() -> None:
                ok = self._run_action(path_name, "legacy GOGO", lambda name: self._start_acquisition(name, exp_id))
                if ok:
                    send_ready()

            threading.Thread(target=worker, daemon=True).start()
            return

        if command == "STOP":
            def worker() -> None:
                self.signals.log_message.emit(f"[{path_name}] handling legacy STOP")
                ok = self._run_action(path_name, "legacy STOP", self._stop_acquisition)
                if ok:
                    send_ready()
                else:
                    self.signals.log_message.emit(f"[{path_name}] legacy STOP failed before READY")

            threading.Thread(target=worker, daemon=True).start()
            return

        self.signals.log_message.emit(f"[{path_name} udp] ignored unknown legacy command '{command}'")

    def _start_listener(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        with runtime.lock:
            if runtime.udp_listener is not None or runtime.path_config.listener_port <= 0:
                return
            runtime.udp_listener = UdpListener(
                path_name=path_name,
                host=runtime.path_config.listener_host,
                port=runtime.path_config.listener_port,
                signals=self.signals,
            )
            runtime.udp_listener.start()
        self._refresh_path_listener_info(path_name)

    def _stop_listener(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        with runtime.lock:
            if runtime.udp_listener is None:
                return
            runtime.udp_listener.stop()
            runtime.udp_listener = None
        self._refresh_path_listener_info(path_name)
