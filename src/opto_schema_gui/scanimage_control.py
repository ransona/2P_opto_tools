from __future__ import annotations

import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
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
    QVBoxLayout,
    QWidget,
)

from .legacy_matlab_codec import build_ready_message, extract_legacy_command
from .matlab_bridge import (
    MatlabSession,
    MatlabSessionError,
    MachineConfig,
    PathConfig,
    ExperimentContext,
    autodetect_machine_name,
    build_experiment_context,
    build_import_command,
    build_run_script_command,
    context_to_matlab_variables,
    list_config_names,
    list_machine_names,
    load_machine_config,
)


class ScanImageSignals(QObject):
    log_message = pyqtSignal(str)
    path_status = pyqtSignal(str, str)
    udp_message = pyqtSignal(str, bytes, tuple)
    refresh_listener_info = pyqtSignal()


@dataclass
class PathRuntime:
    path_config: PathConfig
    session: MatlabSession | None = None
    udp_listener: "UdpListener | None" = None
    status: str = "stopped"
    launched: bool = False
    last_context: ExperimentContext | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)


class UdpListener(threading.Thread):
    def __init__(self, path_name: str, host: str, port: int, signals: ScanImageSignals):
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
                self.signals.udp_message.emit(self.path_name, payload, address)
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


class ScanImageControlWidget(QWidget):
    def __init__(
        self,
        schema_path_provider: Callable[[], Path | None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.schema_path_provider = schema_path_provider
        self.repo_root = Path(__file__).resolve().parents[2]
        self.signals = ScanImageSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.path_status.connect(self._set_path_status)
        self.signals.udp_message.connect(self._handle_udp_message)
        self.signals.refresh_listener_info.connect(self._refresh_listener_info)
        self.machine_config: MachineConfig | None = None
        self._runtimes: dict[str, PathRuntime] = {}
        self._ignore_combo_changes = False
        self._current_machine_name = ""
        self._current_config_name = ""
        self._build_ui()
        self.reload_discovery()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        config_box = QGroupBox("ScanImage Config")
        config_form = QFormLayout(config_box)
        self.machine_combo = QComboBox()
        self.config_combo = QComboBox()
        self.reload_btn = QPushButton("Reload Configs")
        self.photostim_label = QLabel("Photostim path: none")
        self.photostim_label.setWordWrap(True)
        config_row = QHBoxLayout()
        config_row.addWidget(self.reload_btn)
        config_row.addStretch(1)
        config_form.addRow("Machine", self.machine_combo)
        config_form.addRow("Config", self.config_combo)
        config_form.addRow("Photostim path", self.photostim_label)
        config_form.addRow("", config_row)
        layout.addWidget(config_box)

        run_box = QGroupBox("Config Actions")
        run_layout = QGridLayout(run_box)
        self.exp_id_edit = QLineEdit("2014-01-01_01_TEST")
        self.start_config_btn = QPushButton("Start Config")
        self.stop_config_btn = QPushButton("Stop Config")
        self.import_patterns_btn = QPushButton("Import Patterns")
        run_layout.addWidget(QLabel("Exp ID"), 0, 0)
        run_layout.addWidget(self.exp_id_edit, 0, 1, 1, 2)
        run_layout.addWidget(self.start_config_btn, 1, 0)
        run_layout.addWidget(self.stop_config_btn, 1, 1)
        run_layout.addWidget(self.import_patterns_btn, 1, 2)
        layout.addWidget(run_box)

        paths_box = QGroupBox("Paths")
        paths_layout = QVBoxLayout(paths_box)
        self.path_list = QListWidget()
        self.path_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        paths_layout.addWidget(self.path_list)
        path_buttons = QGridLayout()
        self.launch_path_btn = QPushButton("Launch Path")
        self.stop_path_btn = QPushButton("Stop Path")
        self.focus_btn = QPushButton("Focus")
        self.acquire_btn = QPushButton("Acquire")
        self.stop_acq_btn = QPushButton("Stop Acquisition")
        self.start_listener_btn = QPushButton("Start Listener")
        self.stop_listener_btn = QPushButton("Stop Listener")
        button_specs = [
            (self.launch_path_btn, 0, 0),
            (self.stop_path_btn, 0, 1),
            (self.focus_btn, 1, 0),
            (self.acquire_btn, 1, 1),
            (self.stop_acq_btn, 2, 0),
            (self.start_listener_btn, 2, 1),
            (self.stop_listener_btn, 3, 0),
        ]
        for button, row, col in button_specs:
            path_buttons.addWidget(button, row, col)
        path_buttons.setColumnStretch(2, 1)
        paths_layout.addLayout(path_buttons)
        layout.addWidget(paths_box)

        udp_box = QGroupBox("UDP")
        udp_layout = QFormLayout(udp_box)
        self.listener_info_label = QLabel("No paths configured")
        self.listener_info_label.setWordWrap(True)
        self.udp_help_label = QLabel(
            "Each path has its own listener. Python handles legacy MATLAB-serialized COM/GOGO and COM/STOP packets and replies with READY."
        )
        self.udp_help_label.setWordWrap(True)
        udp_layout.addRow("Listeners", self.listener_info_label)
        udp_layout.addRow("", self.udp_help_label)
        layout.addWidget(udp_box)

        log_box = QGroupBox("Debug Log")
        log_layout = QVBoxLayout(log_box)
        self.log_list = QListWidget()
        self.clear_log_btn = QPushButton("Clear Log")
        log_layout.addWidget(self.log_list)
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
        self.import_patterns_btn.clicked.connect(self.import_patterns_for_photostim)
        self.launch_path_btn.clicked.connect(lambda: self._run_for_selected("launch path", self._launch_path))
        self.stop_path_btn.clicked.connect(lambda: self._run_for_selected("stop path", self._stop_path))
        self.focus_btn.clicked.connect(lambda: self._run_for_selected("focus", self._focus_path))
        self.acquire_btn.clicked.connect(lambda: self._run_for_selected("acquire", self._acquire_path_from_ui))
        self.stop_acq_btn.clicked.connect(lambda: self._run_for_selected("stop acquisition", self._stop_acquisition))
        self.start_listener_btn.clicked.connect(lambda: self._run_for_selected("start listener", self._start_listener))
        self.stop_listener_btn.clicked.connect(lambda: self._run_for_selected("stop listener", self._stop_listener))
        self.clear_log_btn.clicked.connect(self.log_list.clear)

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
            self.path_list.clear()
            self.photostim_label.setText("Photostim path: none")
            self.signals.refresh_listener_info.emit()
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
        self.path_list.clear()
        for path_name in machine_config.launch_order:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, path_name)
            item.setSelected(True)
            self.path_list.addItem(item)
            self._set_path_status(path_name, "stopped")
        photostim_text = machine_config.photostim_path or "none"
        self.photostim_label.setText(f"Photostim path: {photostim_text}")
        self.signals.log_message.emit(
            f"Loaded {machine_name}/{config_name} with paths: {', '.join(machine_config.launch_order)}"
        )
        self.signals.refresh_listener_info.emit()

    def _selected_path_names(self) -> list[str]:
        items = self.path_list.selectedItems()
        if not items:
            return list(self._runtimes)
        return [item.data(Qt.ItemDataRole.UserRole) for item in items]

    def _run_for_selected(self, label: str, fn: Callable[[str], None]) -> None:
        path_names = self._selected_path_names()
        if not path_names:
            QMessageBox.warning(self, "No paths", "No ScanImage paths are configured.")
            return
        for path_name in path_names:
            threading.Thread(
                target=self._run_action,
                args=(path_name, label, fn),
                daemon=True,
            ).start()

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
        photostim_path = self.machine_config.photostim_path
        threading.Thread(
            target=self._run_action,
            args=(photostim_path, "import patterns", lambda name: self._import_patterns(name, schema_path)),
            daemon=True,
        ).start()

    def _ensure_session(self, path_name: str) -> PathRuntime:
        runtime = self._runtimes[path_name]
        with runtime.lock:
            if runtime.session is None:
                runtime.session = MatlabSession(runtime.path_config)
                runtime.session.start()
                runtime.status = "simulated" if runtime.session.simulated else "matlab"
                self.signals.path_status.emit(path_name, runtime.status)
                if runtime.session.simulated:
                    self.signals.log_message.emit(f"[{path_name}] simulated MATLAB session started")
                else:
                    self.signals.log_message.emit(f"[{path_name}] MATLAB session started")
        if runtime.path_config.listener_auto_start:
            self._start_listener(path_name)
        return runtime

    def _ensure_path_launched(self, path_name: str) -> PathRuntime:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            if runtime.launched:
                return runtime
            lines = runtime.session.eval(
                build_run_script_command(runtime.path_config, "launch.m"),
                timeout_s=runtime.path_config.startup_timeout_s,
            )
            runtime.launched = True
            runtime.status = "ready"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
        return runtime

    def _launch_path(self, path_name: str) -> None:
        self._ensure_path_launched(path_name)

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
            self.signals.path_status.emit(path_name, runtime.status)
            self.signals.log_message.emit(f"[{path_name}] MATLAB path stopped")

    def _focus_path(self, path_name: str) -> None:
        runtime = self._ensure_path_launched(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                runtime.path_config.focus_command,
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "focus"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _current_exp_id(self) -> str:
        return self.exp_id_edit.text().strip()

    def _acquire_path_from_ui(self, path_name: str) -> None:
        exp_id = self._current_exp_id()
        if not exp_id:
            raise ValueError("Exp ID is required for acquisition")
        self._start_acquisition(path_name, exp_id)

    def _start_acquisition(self, path_name: str, exp_id: str) -> None:
        runtime = self._ensure_path_launched(path_name)
        context = build_experiment_context(runtime.path_config, exp_id)
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
        runtime = self._ensure_path_launched(path_name)
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

    def _import_patterns(self, path_name: str, schema_path: Path) -> None:
        runtime = self._ensure_path_launched(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_import_command(schema_path, runtime.path_config),
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
        self.log_list.addItem(message)
        self.log_list.scrollToBottom()

    def _set_path_status(self, path_name: str, status: str) -> None:
        for index in range(self.path_list.count()):
            item = self.path_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == path_name:
                item.setText(f"{path_name} [{status}]")
                break

    def _handle_udp_message(self, path_name: str, payload: bytes, address: tuple) -> None:
        legacy = extract_legacy_command(payload)
        if legacy is not None:
            self._handle_legacy_udp_message(path_name, legacy, address)
            return

        message = payload.decode("utf-8", errors="replace").strip()
        self.signals.log_message.emit(f"[{path_name} udp {address[0]}:{address[1]}] {message}")
        if not message:
            return

        command, exp_id = self._parse_plain_udp_command(message)
        if command in {"acquire", "start_acquisition", "grab", "gogo"}:
            exp_id = exp_id or self._current_exp_id()
            if not exp_id:
                self.signals.log_message.emit(f"[{path_name} udp] acquire ignored: no expID provided")
                return
            threading.Thread(
                target=self._run_action,
                args=(path_name, f"UDP {command}", lambda name: self._start_acquisition(name, exp_id)),
                daemon=True,
            ).start()
            return
        if command == "focus":
            threading.Thread(
                target=self._run_action,
                args=(path_name, "UDP focus", self._focus_path),
                daemon=True,
            ).start()
            return
        if command in {"stop", "abort"}:
            threading.Thread(
                target=self._run_action,
                args=(path_name, f"UDP {command}", self._stop_acquisition),
                daemon=True,
            ).start()
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

    def _handle_legacy_udp_message(
        self,
        path_name: str,
        message: dict[str, object],
        address: tuple[str, int],
    ) -> None:
        self.signals.log_message.emit(f"[{path_name} udp {address[0]}:{address[1]}] legacy {message}")
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
            self.signals.log_message.emit(
                f"[{path_name} udp {reply_address[0]}:{reply_address[1]}] sent legacy READY"
            )

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
                ok = self._run_action(path_name, "legacy STOP", self._stop_acquisition)
                if ok:
                    send_ready()

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
        self.signals.refresh_listener_info.emit()

    def _stop_listener(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        with runtime.lock:
            if runtime.udp_listener is None:
                return
            runtime.udp_listener.stop()
            runtime.udp_listener = None
        self.signals.refresh_listener_info.emit()

    def _refresh_listener_info(self) -> None:
        if not self._runtimes:
            self.listener_info_label.setText("No paths configured")
            return
        lines = []
        for path_name in self.machine_config.launch_order if self.machine_config else self._runtimes.keys():
            runtime = self._runtimes[path_name]
            active = "on" if runtime.udp_listener is not None else "off"
            cfg = runtime.path_config
            lines.append(f"{path_name}: {cfg.listener_host}:{cfg.listener_port} [{active}]")
        self.listener_info_label.setText("\n".join(lines))
