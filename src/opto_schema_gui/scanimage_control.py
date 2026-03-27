from __future__ import annotations

import configparser
import html
import json
import random
import socket
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
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
    build_abort_photostim_command,
    build_experiment_context,
    build_global_preamble,
    build_import_command,
    build_inspect_photostim_command,
    build_photostim_sequence_status_command,
    build_run_script_command,
    build_software_trigger_command,
    build_test_photostim_command,
    build_trigger_photostim_command,
    context_to_matlab_variables,
    get_machine_default_config_name,
    list_config_names,
    list_machine_names,
    load_machine_config,
    matlab_string,
    matlab_engine,
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
    triggered_seq_num: int | None = None
    triggered_sequence_name: str = ""
    triggered_stimulus_groups: list[int] = field(default_factory=list)
    last_trigger_insert_position: int | None = None
    expected_sequence_position: int | None = None
    remaining_expected_triggers: int | None = None
    ready_sequence_position: int | None = None
    ready_completed_sequences: int | None = None
    leading_park_fired: bool = False

    def reset(self) -> None:
        self.schema_path = None
        self.schema_name = ""
        self.exp_id = ""
        self.prepared_seq_nums = []
        self.prepared_sequence_names = []
        self.imported_pattern_names = []
        self.pattern_to_schema_index = {}
        self.pattern_to_stimulus_group = {}
        self.triggered_seq_num = None
        self.triggered_sequence_name = ""
        self.triggered_stimulus_groups = []
        self.last_trigger_insert_position = None
        self.expected_sequence_position = None
        self.remaining_expected_triggers = None
        self.ready_sequence_position = None
        self.ready_completed_sequences = None
        self.leading_park_fired = False


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
    software_trigger_stop: threading.Event | None = None
    software_trigger_thread: threading.Thread | None = None


@dataclass
class TestSlmPatternWidgets:
    name_edit: QLineEdit
    overall_power_spin: QDoubleSpinBox
    duration_spin: QDoubleSpinBox
    spiral_width_spin: QDoubleSpinBox
    spiral_height_spin: QDoubleSpinBox
    cells_table: QTableWidget


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
    inspect_slm_btn: QPushButton
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


class TestSlmDialog(QDialog):
    def __init__(self, send_callback: Callable[[list[dict[str, object]]], None], parent: QWidget | None = None):
        super().__init__(parent)
        self._send_callback = send_callback
        self.setWindowTitle("Test SLM Patterns")
        self.resize(900, 700)
        self._pattern_widgets: list[TestSlmPatternWidgets] = []
        self._build_ui()
        self._regenerate_patterns()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        config_box = QGroupBox("Random Pattern Settings")
        config_form = QFormLayout(config_box)
        self.pattern_count_spin = QSpinBox()
        self.pattern_count_spin.setRange(1, 50)
        self.pattern_count_spin.setValue(3)
        self.neuron_count_spin = QSpinBox()
        self.neuron_count_spin.setRange(1, 100)
        self.neuron_count_spin.setValue(3)
        self.range_min_spin = QDoubleSpinBox()
        self.range_min_spin.setRange(-100000.0, 100000.0)
        self.range_min_spin.setDecimals(4)
        self.range_min_spin.setValue(-50.0)
        self.range_max_spin = QDoubleSpinBox()
        self.range_max_spin.setRange(-100000.0, 100000.0)
        self.range_max_spin.setDecimals(4)
        self.range_max_spin.setValue(50.0)
        config_form.addRow("Pattern Count", self.pattern_count_spin)
        config_form.addRow("Neurons Per Pattern", self.neuron_count_spin)
        config_form.addRow("Min Coordinate", self.range_min_spin)
        config_form.addRow("Max Coordinate", self.range_max_spin)
        layout.addWidget(config_box)

        self.pattern_tabs = QTabWidget()
        layout.addWidget(self.pattern_tabs, 1)

        button_row = QHBoxLayout()
        self.generate_btn = QPushButton("Gen New Random Pattern")
        self.cross_btn = QPushButton("Gen Test Cross")
        self.send_btn = QPushButton("Send To ScanImage")
        self.cancel_btn = QPushButton("Cancel")
        button_row.addWidget(self.generate_btn)
        button_row.addWidget(self.cross_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.send_btn)
        button_row.addWidget(self.cancel_btn)
        layout.addLayout(button_row)

        self.generate_btn.clicked.connect(self._regenerate_patterns)
        self.cross_btn.clicked.connect(self._generate_cross_patterns)
        self.send_btn.clicked.connect(self._send_if_valid)
        self.cancel_btn.clicked.connect(self.reject)

    def _random_value(self) -> float:
        low = self.range_min_spin.value()
        high = self.range_max_spin.value()
        if low > high:
            low, high = high, low
        return random.uniform(low, high)

    def _regenerate_patterns(self) -> None:
        self.pattern_tabs.clear()
        self._pattern_widgets = []
        pattern_count = self.pattern_count_spin.value()
        neuron_count = self.neuron_count_spin.value()
        for pattern_index in range(pattern_count):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            form_box = QGroupBox(f"Pattern {pattern_index + 1}")
            form = QFormLayout(form_box)
            name_edit = QLineEdit(f"test_pattern_{pattern_index + 1}")
            overall_power_spin = QDoubleSpinBox()
            overall_power_spin.setRange(0.0, 100.0)
            overall_power_spin.setDecimals(4)
            overall_power_spin.setValue(5.0)
            duration_spin = QDoubleSpinBox()
            duration_spin.setRange(0.0001, 9999.0)
            duration_spin.setDecimals(4)
            duration_spin.setValue(0.010)
            spiral_width_spin = QDoubleSpinBox()
            spiral_width_spin.setRange(0.0, 9999.0)
            spiral_width_spin.setDecimals(4)
            spiral_width_spin.setValue(10.0)
            spiral_height_spin = QDoubleSpinBox()
            spiral_height_spin.setRange(0.0, 9999.0)
            spiral_height_spin.setDecimals(4)
            spiral_height_spin.setValue(10.0)
            form.addRow("Name", name_edit)
            form.addRow("Overall Power", overall_power_spin)
            form.addRow("Duration (s)", duration_spin)
            form.addRow("Spiral Width", spiral_width_spin)
            form.addRow("Spiral Height", spiral_height_spin)
            tab_layout.addWidget(form_box)

            cells_table = QTableWidget(neuron_count, 4)
            cells_table.setHorizontalHeaderLabels(["X", "Y", "Z", "Relative Power"])
            cells_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            cells_table.horizontalHeader().setStretchLastSection(True)
            for row in range(neuron_count):
                cells_table.setItem(row, 0, QTableWidgetItem(f"{self._random_value():.4f}"))
                cells_table.setItem(row, 1, QTableWidgetItem(f"{self._random_value():.4f}"))
                cells_table.setItem(row, 2, QTableWidgetItem(f"{self._random_value():.4f}"))
                cells_table.setItem(row, 3, QTableWidgetItem(f"{random.uniform(0.5, 1.5):.4f}"))
            tab_layout.addWidget(QLabel("SLM Target Points"))
            tab_layout.addWidget(cells_table, 1)
            self.pattern_tabs.addTab(tab, f"Pattern {pattern_index + 1}")
            self._pattern_widgets.append(
                TestSlmPatternWidgets(
                    name_edit=name_edit,
                    overall_power_spin=overall_power_spin,
                    duration_spin=duration_spin,
                    spiral_width_spin=spiral_width_spin,
                    spiral_height_spin=spiral_height_spin,
                    cells_table=cells_table,
                )
            )

    def _generate_cross_patterns(self) -> None:
        self.pattern_tabs.clear()
        self._pattern_widgets = []
        pattern_count = self.pattern_count_spin.value()
        neuron_count = max(1, self.neuron_count_spin.value())
        low = self.range_min_spin.value()
        high = self.range_max_spin.value()
        if low > high:
            low, high = high, low
        center = 0.0
        left = low
        right = high
        down = low
        up = high
        base_cross = [
            (center, center, center),
            (center, up, center),
            (center, down, center),
            (left, center, center),
            (right, center, center),
        ]
        for pattern_index in range(pattern_count):
            tab = QWidget()
            tab_layout = QVBoxLayout(tab)
            form_box = QGroupBox(f"Pattern {pattern_index + 1}")
            form = QFormLayout(form_box)
            name_edit = QLineEdit(f"test_cross_{pattern_index + 1}")
            overall_power_spin = QDoubleSpinBox()
            overall_power_spin.setRange(0.0, 100.0)
            overall_power_spin.setDecimals(4)
            overall_power_spin.setValue(5.0)
            duration_spin = QDoubleSpinBox()
            duration_spin.setRange(0.0001, 9999.0)
            duration_spin.setDecimals(4)
            duration_spin.setValue(0.010)
            spiral_width_spin = QDoubleSpinBox()
            spiral_width_spin.setRange(0.0, 9999.0)
            spiral_width_spin.setDecimals(4)
            spiral_width_spin.setValue(10.0)
            spiral_height_spin = QDoubleSpinBox()
            spiral_height_spin.setRange(0.0, 9999.0)
            spiral_height_spin.setDecimals(4)
            spiral_height_spin.setValue(10.0)
            form.addRow("Name", name_edit)
            form.addRow("Overall Power", overall_power_spin)
            form.addRow("Duration (s)", duration_spin)
            form.addRow("Spiral Width", spiral_width_spin)
            form.addRow("Spiral Height", spiral_height_spin)
            tab_layout.addWidget(form_box)

            cells_table = QTableWidget(neuron_count, 4)
            cells_table.setHorizontalHeaderLabels(["X", "Y", "Z", "Relative Power"])
            cells_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            cells_table.horizontalHeader().setStretchLastSection(True)
            for row in range(neuron_count):
                x, y, z = base_cross[row % len(base_cross)]
                cells_table.setItem(row, 0, QTableWidgetItem(f"{x:.4f}"))
                cells_table.setItem(row, 1, QTableWidgetItem(f"{y:.4f}"))
                cells_table.setItem(row, 2, QTableWidgetItem(f"{z:.4f}"))
                cells_table.setItem(row, 3, QTableWidgetItem("1.0000"))
            tab_layout.addWidget(QLabel("SLM Target Points"))
            tab_layout.addWidget(cells_table, 1)
            self.pattern_tabs.addTab(tab, f"Pattern {pattern_index + 1}")
            self._pattern_widgets.append(
                TestSlmPatternWidgets(
                    name_edit=name_edit,
                    overall_power_spin=overall_power_spin,
                    duration_spin=duration_spin,
                    spiral_width_spin=spiral_width_spin,
                    spiral_height_spin=spiral_height_spin,
                    cells_table=cells_table,
                )
            )

    def _send_if_valid(self) -> None:
        try:
            patterns = self.gather_patterns()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Test SLM Pattern", str(exc))
            return
        self._send_callback(patterns)

    def gather_patterns(self) -> list[dict[str, object]]:
        patterns: list[dict[str, object]] = []
        for pattern_index, widgets in enumerate(self._pattern_widgets, start=1):
            cells: list[dict[str, float]] = []
            for row in range(widgets.cells_table.rowCount()):
                values: list[str] = []
                for col in range(widgets.cells_table.columnCount()):
                    item = widgets.cells_table.item(row, col)
                    values.append(item.text().strip() if item is not None else "")
                if any(not value for value in values):
                    raise ValueError(f"Pattern {pattern_index} cell row {row + 1} is incomplete.")
                try:
                    x, y, z, relative_power = (float(value) for value in values)
                except ValueError as exc:
                    raise ValueError(f"Pattern {pattern_index} cell row {row + 1} contains invalid numbers.") from exc
                if relative_power < 0:
                    raise ValueError(
                        f"Pattern {pattern_index} cell row {row + 1} must have Relative Power >= 0."
                    )
                if relative_power > 1:
                    raise ValueError(
                        f"Pattern {pattern_index} cell row {row + 1} must have Relative Power <= 1."
                    )
                cells.append(
                    {
                        "x": x,
                        "y": y,
                        "z": z,
                        "relative_power": relative_power,
                    }
                )
            name = widgets.name_edit.text().strip() or f"test_pattern_{pattern_index}"
            patterns.append(
                {
                    "name": name,
                    "overall_power": widgets.overall_power_spin.value(),
                    "duration_s": widgets.duration_spin.value(),
                    "spiral_width": widgets.spiral_width_spin.value(),
                    "spiral_height": widgets.spiral_height_spin.value(),
                    "cells": cells,
                }
            )
        return patterns


class PhotostimTestDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Test Photostim")
        self.resize(460, 190)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.schema_name_edit = QLineEdit("DEFAULT")
        self.exp_id_edit = QLineEdit("2026-03-25_10_TEST")
        self.seq_num_spin = QSpinBox()
        self.seq_num_spin.setRange(0, 1000000)
        self.seq_num_spin.setValue(0)
        form.addRow("Schema Name", self.schema_name_edit)
        form.addRow("Exp ID", self.exp_id_edit)
        form.addRow("Seq Num (0-based)", self.seq_num_spin)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        self.run_prep_btn = QPushButton("Run Prep")
        self.run_trigger_btn = QPushButton("Run Trigger")
        self.run_abort_btn = QPushButton("Run Abort")
        self.cancel_btn = QPushButton("Cancel")
        button_row.addStretch(1)
        button_row.addWidget(self.run_prep_btn)
        button_row.addWidget(self.run_trigger_btn)
        button_row.addWidget(self.run_abort_btn)
        button_row.addWidget(self.cancel_btn)
        layout.addLayout(button_row)

        self.cancel_btn.clicked.connect(self.reject)

    def values(self) -> tuple[str, str, int]:
        return (
            self.schema_name_edit.text().strip(),
            self.exp_id_edit.text().strip(),
            self.seq_num_spin.value(),
        )


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
        self._debug_history: list[tuple[str, str, bool]] = []
        self._startup_reconnect_prompt_pending = True
        self._startup_reconnect_prompt_shown = False
        self._debug_category_enabled: dict[str, bool] = {
            "general": True,
            "udp": True,
            "software_trigger_times": True,
            "software_trigger_count": True,
            "stimuli": True,
        }
        self.save_root, self.schema_root = self._load_path_roots()
        self._build_ui()
        self.reload_discovery()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        config_box = QGroupBox("ScanImage Config")
        config_layout = QHBoxLayout(config_box)
        button_column = QVBoxLayout()
        button_column.setSpacing(6)
        self.clear_all_logs_btn = QPushButton("All")
        self.start_config_btn = QPushButton("Start Config")
        self.stop_config_btn = QPushButton("Stop Config")
        self.reload_btn = QPushButton("Reload Configs")
        self.test_prep_patterns_btn = QPushButton("Test Photostim")
        self.start_config_btn.setStyleSheet("color: #15803d;")
        self.stop_config_btn.setStyleSheet("color: #b91c1c;")
        button_column.addWidget(self.clear_all_logs_btn)
        button_column.addWidget(self.start_config_btn)
        button_column.addWidget(self.stop_config_btn)
        button_column.addWidget(self.reload_btn)
        button_column.addWidget(self.test_prep_patterns_btn)
        button_column.addStretch(1)
        config_layout.addLayout(button_column)

        config_form_container = QWidget()
        config_form = QFormLayout(config_form_container)
        self.machine_combo = QComboBox()
        self.config_combo = QComboBox()
        self.force_simulated_checkbox = QCheckBox("Force Simulated Mode")
        self.ignore_incomplete_trigger_checkbox = QCheckBox("Send mismatch errors upstream")
        self.ignore_incomplete_trigger_checkbox.setChecked(True)
        self.software_trigger_checkbox = QCheckBox("Software trigger stim seqs")
        config_form.addRow("Machine", self.machine_combo)
        config_form.addRow("Config", self.config_combo)
        config_form.addRow("", self.force_simulated_checkbox)
        config_form.addRow("", self.ignore_incomplete_trigger_checkbox)
        config_form.addRow("", self.software_trigger_checkbox)
        config_layout.addWidget(config_form_container, 1)
        layout.addWidget(config_box)

        self.paths_box = QGroupBox("Paths")
        paths_layout = QVBoxLayout(self.paths_box)
        self.path_tabs = QTabWidget()
        paths_layout.addWidget(self.path_tabs)
        layout.addWidget(self.paths_box, 1)

        log_box = QGroupBox("Debug Log")
        log_layout = QHBoxLayout(log_box)
        filter_box = QGroupBox("Shown")
        filter_layout = QVBoxLayout(filter_box)
        self.show_general_debug_checkbox = QCheckBox("General")
        self.show_udp_debug_checkbox = QCheckBox("UDP commands")
        self.show_trigger_times_debug_checkbox = QCheckBox("Software trigger times")
        self.show_trigger_count_debug_checkbox = QCheckBox("Software trigger count check")
        self.show_stimuli_debug_checkbox = QCheckBox("Stimuli")
        self.clear_log_btn = QPushButton("Clear Debug Output")
        for checkbox in (
            self.show_general_debug_checkbox,
            self.show_udp_debug_checkbox,
            self.show_trigger_times_debug_checkbox,
            self.show_trigger_count_debug_checkbox,
            self.show_stimuli_debug_checkbox,
        ):
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._refresh_debug_log)
            filter_layout.addWidget(checkbox)
        filter_layout.addWidget(self.clear_log_btn)
        filter_layout.addStretch(1)
        log_layout.addWidget(filter_box)
        output_container = QWidget()
        output_layout = QVBoxLayout(output_container)
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        output_layout.addWidget(self.log_text)
        log_layout.addWidget(output_container, 1)
        layout.addWidget(log_box, 1)

        self.reload_btn.clicked.connect(self.reload_discovery)
        self.machine_combo.currentTextChanged.connect(self._on_machine_changed)
        self.config_combo.currentTextChanged.connect(self._on_config_changed)
        self.start_config_btn.clicked.connect(self.start_config)
        self.stop_config_btn.clicked.connect(self.stop_config)
        self.test_prep_patterns_btn.clicked.connect(self._open_photostim_test_dialog)
        self.clear_log_btn.clicked.connect(self.log_text.clear)
        self.clear_all_logs_btn.clicked.connect(self._clear_all_logs)
        self.force_simulated_checkbox.toggled.connect(self._on_force_simulated_toggled)

    def _clear_all_logs(self) -> None:
        self._debug_history.clear()
        self.log_text.clear()
        for widgets in self._path_tabs.values():
            widgets.udp_text.clear()

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

    def _on_force_simulated_toggled(self, checked: bool) -> None:
        mode = "enabled" if checked else "disabled"
        self.signals.log_message.emit(f"[config] Force Simulated Mode {mode}")

    def _open_photostim_test_dialog(self) -> None:
        dialog = PhotostimTestDialog(self)
        def run_mode(mode: str) -> None:
            schema_name, exp_id, seq_num = dialog.values()
            if mode != "abort" and (not schema_name or not exp_id):
                QMessageBox.warning(self, "Invalid photostim input", "Schema Name and Exp ID are required.")
                return
            if mode == "prep":
                self.signals.log_message.emit(
                    f"[config] GUI prep_patterns schema_name={schema_name} expID={exp_id} seq_num={seq_num}"
                )
                self._handle_prep_patterns_request(
                    request_path_name=self.machine_config.photostim_path if self.machine_config else "",
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                    reply_address=None,
                )
            elif mode == "abort":
                self.signals.log_message.emit("[config] GUI abort_photo_stim")
                self._handle_abort_photo_stim_request(
                    request_path_name=self.machine_config.photostim_path if self.machine_config else "",
                    reply_address=None,
                )
            else:
                self.signals.log_message.emit(
                    f"[config] GUI trigger_photo_stim schema_name={schema_name} expID={exp_id} seq_num={seq_num}"
                )
                self._handle_trigger_photo_stim_request(
                    request_path_name=self.machine_config.photostim_path if self.machine_config else "",
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                    reply_address=None,
                )
        dialog.run_prep_btn.clicked.connect(lambda: run_mode("prep"))
        dialog.run_trigger_btn.clicked.connect(lambda: run_mode("trigger"))
        dialog.run_abort_btn.clicked.connect(lambda: run_mode("abort"))
        dialog.exec()

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
        else:
            default_config = get_machine_default_config_name(self.repo_root, machine_name) if machine_name else None
            if default_config and default_config in config_names:
                self.config_combo.setCurrentText(default_config)
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
        if self._startup_reconnect_prompt_pending and not self._startup_reconnect_prompt_shown:
            QTimer.singleShot(0, self._prompt_startup_reconnect_if_available)

    def _rebuild_path_tabs(self) -> None:
        self.path_tabs.clear()
        self._path_tabs = {}
        if self.machine_config is None:
            return
        for path_name in self.machine_config.launch_order:
            self._add_path_tab(path_name)

    def _find_available_shared_paths(self) -> list[str]:
        if self.machine_config is None or matlab_engine is None or self.force_simulated_checkbox.isChecked():
            return []
        try:
            available = set(matlab_engine.find_matlab())
        except Exception:
            return []
        found: list[str] = []
        for path_name in self.machine_config.launch_order:
            path_config = self.machine_config.paths[path_name]
            if path_config.engine_name in available:
                found.append(path_name)
        return found

    def _prompt_startup_reconnect_if_available(self) -> None:
        self._startup_reconnect_prompt_pending = False
        if self._startup_reconnect_prompt_shown or self.machine_config is None or self._has_active_paths():
            return
        available_paths = self._find_available_shared_paths()
        if not available_paths:
            return
        self._startup_reconnect_prompt_shown = True
        joined = ", ".join(available_paths)
        choice = QMessageBox.question(
            self,
            "Reconnect MATLAB",
            f"Found running MATLAB session(s) for: {joined}.\nConnect to them now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            self.signals.log_message.emit(f"[config] existing MATLAB sessions ignored: {joined}")
            return

        def worker() -> None:
            for path_name in available_paths:
                self._run_action(path_name, f"Launching path: {path_name}", self._launch_path)

        threading.Thread(target=worker, daemon=True).start()

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
        inspect_slm_btn = QPushButton("Inspect SLM")
        start_listener_btn = QPushButton("Start Listener")
        stop_listener_btn = QPushButton("Stop Listener")
        for button in [
            launch_btn,
            acquire_btn,
            focus_btn,
            stop_acq_btn,
            test_slm_btn,
            inspect_slm_btn,
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
            inspect_slm_btn=inspect_slm_btn,
            start_listener_btn=start_listener_btn,
            stop_listener_btn=stop_listener_btn,
        )
        self._path_tabs[path_name] = widgets
        self.path_tabs.addTab(tab, path_name)

        launch_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, f"Launching path: {name}", self._launch_path))
        focus_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "focus", self._focus_path))
        acquire_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "acquire", self._acquire_path_from_ui))
        stop_acq_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "stop acquisition", self._stop_acquisition))
        test_slm_btn.clicked.connect(lambda _, name=path_name: self._open_test_slm_dialog(name))
        inspect_slm_btn.clicked.connect(lambda _, name=path_name: self._spawn_action(name, "inspect slm", self._inspect_photostim_api))
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
                if not self._run_action(path_name, f"Launching path: {path_name}", self._launch_path):
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
                session = MatlabSession(
                    runtime.path_config,
                    force_simulated=self.force_simulated_checkbox.isChecked(),
                )
                try:
                    session.start(startup_command=self._build_launch_startup_command(runtime.path_config))
                except Exception:
                    runtime.session = None
                    raise
                runtime.session = session
                if session.simulated:
                    runtime.status = "simulated"
                elif session.attached:
                    runtime.status = "reconnected"
                else:
                    runtime.status = "ready"
                runtime.launched = True
                self.signals.path_status.emit(path_name, runtime.status)
                if session.simulated:
                    mode = "forced simulated mode" if self.force_simulated_checkbox.isChecked() else "simulated mode"
                    self.signals.log_message.emit(f"[{path_name}] MATLAB session started in {mode}")
                elif session.attached:
                    self.signals.log_message.emit(
                        f"[{path_name}] reconnected to existing MATLAB session '{runtime.path_config.engine_name}'"
                    )
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
            self.signals.path_status.emit(path_name, runtime.status)
            self.signals.log_message.emit(f"[{path_name}] MATLAB path disconnected")

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

    def _open_test_slm_dialog(self, path_name: str) -> None:
        dialog = TestSlmDialog(
            lambda patterns: self._spawn_action(
                path_name,
                "test slm",
                lambda name: self._test_photostim_api(name, patterns),
            ),
            self,
        )
        dialog.exec()

    def _test_photostim_api(self, path_name: str, patterns: list[dict[str, object]] | None = None) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_test_photostim_command(runtime.path_config, patterns),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "photostim test"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _inspect_photostim_api(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_inspect_photostim_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "photostim inspect"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)

    def _import_patterns(self, path_name: str, schema_path: Path) -> None:
        self._import_pattern_subset(path_name, schema_path, None)

    def _import_pattern_subset(
        self,
        path_name: str,
        schema_path: Path,
        pattern_names: list[str] | None,
        prepare_sequence: bool = False,
        start_photostim: bool = False,
    ) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_import_command(
                    schema_path,
                    runtime.path_config,
                    pattern_names=pattern_names,
                    prepare_sequence=prepare_sequence,
                    start_photostim=start_photostim,
                ),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "photostim ready" if start_photostim else "patterns imported"
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
        line = f"{self._timestamp()} {message}"
        category = self._categorize_debug_message(message)
        is_error = "ERROR:" in message
        self._debug_history.append((line, category, is_error))
        if not self._should_show_debug_category(category):
            return
        if is_error:
            cursor = self.log_text.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertHtml(f"<span style='color:#b91c1c;'>{html.escape(line)}</span><br>")
            self.log_text.setTextCursor(cursor)
        else:
            self.log_text.append(line)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _categorize_debug_message(self, message: str) -> str:
        lower = message.lower()
        if lower.startswith("[") and "stimuli:" in lower:
            return "stimuli"
        if "software trigger count check" in lower:
            return "software_trigger_count"
        if "software trigger" in lower:
            return "software_trigger_times"
        if " udp " in lower or lower.startswith("[") and "udp" in lower:
            return "udp"
        return "general"

    def _should_show_debug_category(self, category: str) -> bool:
        self._debug_category_enabled = {
            "general": self.show_general_debug_checkbox.isChecked(),
            "udp": self.show_udp_debug_checkbox.isChecked(),
            "software_trigger_times": self.show_trigger_times_debug_checkbox.isChecked(),
            "software_trigger_count": self.show_trigger_count_debug_checkbox.isChecked(),
            "stimuli": self.show_stimuli_debug_checkbox.isChecked(),
        }
        if category == "udp":
            return self.show_udp_debug_checkbox.isChecked()
        if category == "software_trigger_times":
            return self.show_trigger_times_debug_checkbox.isChecked()
        if category == "software_trigger_count":
            return self.show_trigger_count_debug_checkbox.isChecked()
        if category == "stimuli":
            return self.show_stimuli_debug_checkbox.isChecked()
        return self.show_general_debug_checkbox.isChecked()

    def _refresh_debug_log(self) -> None:
        self.log_text.clear()
        for line, category, is_error in self._debug_history:
            if not self._should_show_debug_category(category):
                continue
            if is_error:
                cursor = self.log_text.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                cursor.insertHtml(f"<span style='color:#b91c1c;'>{html.escape(line)}</span><br>")
                self.log_text.setTextCursor(cursor)
            else:
                self.log_text.append(line)
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

        if action == "prep_patterns":
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
                self._handle_prep_patterns_request(
                    request_path_name=path_name,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                    reply_address=address,
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

        if action == "trigger_photo_stim":
            schema_name = str(message.get("schema_name", "")).strip()
            exp_id = str(message.get("expID", "")).strip()
            seq_num_raw = message.get("seq_num")
            if not schema_name or not exp_id or seq_num_raw is None:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "trigger_photo_stim",
                        "status": "error",
                        "schema_name": schema_name,
                        "expID": exp_id,
                        "seq_num": seq_num_raw,
                        "error": "trigger_photo_stim requires schema_name, expID, and seq_num",
                    },
                )
                return
            try:
                seq_num = int(seq_num_raw)
                self._handle_trigger_photo_stim_request(
                    request_path_name=path_name,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                    reply_address=address,
                )
            except Exception as exc:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "trigger_photo_stim",
                        "status": "error",
                        "schema_name": schema_name,
                        "expID": exp_id,
                        "seq_num": seq_num_raw,
                        "error": str(exc),
                    },
                )
            return
        if action == "abort_photo_stim":
            try:
                self._handle_abort_photo_stim_request(
                    request_path_name=path_name,
                    reply_address=address,
                )
            except Exception as exc:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "abort_photo_stim",
                        "status": "error",
                        "error": str(exc),
                    },
                )
            return
        self.signals.log_message.emit(f"[{path_name} udp] ignored unknown json action '{action}'")

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

    def _handle_prep_patterns_request(
        self,
        request_path_name: str,
        schema_name: str,
        exp_id: str,
        seq_num: int,
        reply_address: tuple[str, int] | None,
    ) -> None:
        photostim_path = self.machine_config.photostim_path if self.machine_config is not None else None
        if not photostim_path:
            if reply_address is not None:
                self._send_json_reply(
                    request_path_name,
                    reply_address,
                    {
                        "action": "prep_patterns",
                        "status": "error",
                        "error": "No photostim path configured",
                    },
                )
            else:
                self.signals.log_message.emit("[config] prep_patterns skipped: no photostim path configured")
            return

        schema_path = self._resolve_schema_path(schema_name, exp_id)
        self.signals.log_message.emit("--------------------")
        self.signals.log_message.emit("Pre-building all stimulus groups for experiment")
        self.signals.log_message.emit(f"Loading schema: {schema_path}")
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

        sequence_names = list(project.sequences.keys())
        prep_state.prepared_seq_nums = list(range(len(sequence_names)))
        prepared_sequence_names, pattern_names, pattern_to_schema_index = self._patterns_for_sequences(
            project,
            prep_state.prepared_seq_nums,
        )

        def worker() -> None:
            ok = self._run_action(
                photostim_path,
                "Pre-building all stimulus groups for experiment",
                lambda name: self._import_pattern_subset(
                    name,
                    schema_path,
                    pattern_names,
                    prepare_sequence=True,
                    start_photostim=True,
                ),
            )
            prep_state_local = self._runtimes[photostim_path].prepared_photostim
            status = "ready" if ok else "error"
            payload = {
                "action": "prep_patterns",
                "status": status,
                "schema_name": schema_name,
                "expID": exp_id,
                "seq_num": seq_num,
                "prepared_seq_nums": list(prep_state_local.prepared_seq_nums),
                "prepared_sequence_names": list(prepared_sequence_names),
                "pattern_names": pattern_names,
                "stimulus_groups": [],
            }
            if ok:
                prep_state_local.prepared_sequence_names = list(prepared_sequence_names)
                prep_state_local.imported_pattern_names = list(pattern_names)
                prep_state_local.pattern_to_schema_index = dict(pattern_to_schema_index)
                prep_state_local.pattern_to_stimulus_group = {
                    pattern_name: index + 3 for index, pattern_name in enumerate(pattern_names)
                }
                payload["stimulus_groups"] = [
                    {
                        "stimulus_group_num": prep_state_local.pattern_to_stimulus_group[pattern_name],
                        "pattern_name": pattern_name,
                        "pattern_num": prep_state_local.pattern_to_schema_index[pattern_name],
                    }
                    for pattern_name in pattern_names
                ]
                self.signals.log_message.emit(
                    f"[{photostim_path}] prepared {len(pattern_names)} stimulus group(s) for all schema sequences"
                )
            if not ok:
                payload["error"] = "prep_patterns failed"

            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit(f"[config] gui prep_patterns result={payload}")

        threading.Thread(target=worker, daemon=True).start()

    def _handle_trigger_photo_stim_request(
        self,
        request_path_name: str,
        schema_name: str,
        exp_id: str,
        seq_num: int,
        reply_address: tuple[str, int] | None,
    ) -> None:
        photostim_path = self.machine_config.photostim_path if self.machine_config is not None else None
        if not photostim_path:
            payload = {
                "action": "trigger_photo_stim",
                "status": "error",
                "error": "No photostim path configured",
            }
            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit("[config] trigger_photo_stim skipped: no photostim path configured")
            return

        schema_path = self._resolve_schema_path(schema_name, exp_id)
        self.signals.log_message.emit(f"Loading schema: {schema_path}")
        project = load_schema(schema_path)
        runtime = self._runtimes[photostim_path]
        prep_state = runtime.prepared_photostim
        if prep_state.schema_path != schema_path or prep_state.schema_name != schema_name or prep_state.exp_id != exp_id:
            raise ValueError("trigger_photo_stim requires matching prepared photostim state. Run prep_patterns first.")
        if not prep_state.pattern_to_stimulus_group:
            raise ValueError("No prepared stimulus group mapping is available. Run prep_patterns first.")

        sequence_name, expanded_groups, trigger_times_s, stimulus_pattern_numbers = self._expand_trigger_sequence(
            project,
            seq_num,
            prep_state.pattern_to_stimulus_group,
        )

        def worker() -> None:
            ok = self._run_action(
                photostim_path,
                "json trigger_photo_stim" if reply_address is not None else "gui trigger_photo_stim",
                lambda name: self._trigger_photo_stim_checked(name, expanded_groups),
            )
            prep_state_local = self._runtimes[photostim_path].prepared_photostim
            status = "ready" if ok else "error"
            payload = {
                "action": "trigger_photo_stim",
                "status": status,
                "schema_name": schema_name,
                "expID": exp_id,
                "seq_num": seq_num,
                "sequence_name": sequence_name,
                "stimulus_groups": list(expanded_groups),
            }
            if ok:
                prep_state_local.triggered_seq_num = seq_num
                prep_state_local.triggered_sequence_name = sequence_name
                prep_state_local.triggered_stimulus_groups = list(expanded_groups)
                self.signals.log_message.emit(
                    f"[{photostim_path}] triggered sequence '{sequence_name}' with {len(expanded_groups)} stimulus entries"
                )
            else:
                payload["error"] = "trigger_photo_stim failed"

            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit(f"[config] gui trigger_photo_stim result={payload}")
            if ok and self.software_trigger_checkbox.isChecked():
                self._start_software_trigger_schedule(
                    photostim_path,
                    sequence_name,
                    trigger_times_s,
                    stimulus_pattern_numbers=stimulus_pattern_numbers,
                    request_path_name=request_path_name if reply_address is not None else None,
                    reply_address=reply_address,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                )

        threading.Thread(target=worker, daemon=True).start()

    def _handle_abort_photo_stim_request(
        self,
        request_path_name: str,
        reply_address: tuple[str, int] | None,
    ) -> None:
        photostim_path = self.machine_config.photostim_path if self.machine_config is not None else None
        if not photostim_path:
            payload = {
                "action": "abort_photo_stim",
                "status": "error",
                "error": "No photostim path configured",
            }
            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit("[config] abort_photo_stim skipped: no photostim path configured")
            return

        def worker() -> None:
            ok = self._run_action(
                photostim_path,
                "json abort_photo_stim" if reply_address is not None else "gui abort_photo_stim",
                self._abort_photo_stim,
            )
            payload = {
                "action": "abort_photo_stim",
                "status": "ready" if ok else "error",
            }
            if not ok:
                payload["error"] = "abort_photo_stim failed"

            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit(f"[config] gui abort_photo_stim result={payload}")

        threading.Thread(target=worker, daemon=True).start()

    def _expand_trigger_sequence(
        self,
        project,
        seq_num: int,
        pattern_to_stimulus_group: dict[str, int],
    ) -> tuple[str, list[int], list[float], list[int]]:
        sequence_names = list(project.sequences.keys())
        if not sequence_names:
            raise ValueError("Schema does not contain any sequences")
        if seq_num < 0 or seq_num >= len(sequence_names):
            raise IndexError(f"seq_num {seq_num} is out of range for {len(sequence_names)} sequence(s)")

        sequence_name = sequence_names[seq_num]
        sequence = project.sequences[sequence_name]
        expanded_groups: list[int] = [2]
        trigger_times_s: list[float] = []
        stimulus_pattern_numbers: list[int] = []
        pattern_names = list(project.patterns.keys())
        end_time_s = 0.0
        for step in sequence.steps:
            if step.pattern not in project.patterns:
                raise ValueError(f"Sequence '{sequence_name}' references unknown pattern '{step.pattern}'")
            if step.pattern not in pattern_to_stimulus_group:
                raise ValueError(
                    f"Pattern '{step.pattern}' in sequence '{sequence_name}' has not been prepared yet."
                )
            pattern = project.patterns[step.pattern]
            repeat_count_float = pattern.duration_s * pattern.frequency_hz
            repeat_count = max(1, int(round(repeat_count_float)))
            period_s = 1.0 / pattern.frequency_hz
            expanded_groups.extend([pattern_to_stimulus_group[step.pattern]] * repeat_count)
            trigger_times_s.extend(step.start_s + period_s * index for index in range(repeat_count))
            pattern_number = pattern_names.index(step.pattern) + 1
            stimulus_pattern_numbers.extend([pattern_number] * repeat_count)
            end_time_s = max(end_time_s, step.start_s + pattern.duration_s)

        expanded_groups.append(2)
        trigger_times_s.append(end_time_s)
        return sequence_name, expanded_groups, trigger_times_s, stimulus_pattern_numbers

    def _apply_trigger_sequence(self, path_name: str, sequence_indices: list[int]) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_trigger_photostim_command(runtime.path_config, sequence_indices),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "photostim triggered"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
            return lines

    def _abort_photo_stim(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_abort_photostim_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.prepared_photostim.expected_sequence_position = None
            runtime.prepared_photostim.last_trigger_insert_position = None
            runtime.prepared_photostim.remaining_expected_triggers = None
            runtime.prepared_photostim.ready_sequence_position = None
            runtime.prepared_photostim.ready_completed_sequences = None
            runtime.prepared_photostim.leading_park_fired = False
            runtime.status = "photostim aborted"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
        self._cancel_software_trigger(path_name)

    def _query_photostim_sequence_state(self, path_name: str) -> tuple[bool, int | None, list[int], int | None]:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_photostim_sequence_status_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
        active = False
        position: int | None = None
        sequence: list[int] = []
        completed_sequences: int | None = None
        marker = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line in {"PHOTOSTIM_ACTIVE", "PHOTOSTIM_SEQUENCE_POSITION", "PHOTOSTIM_COMPLETED_SEQUENCES", "PHOTOSTIM_SEQUENCE_SELECTED", "PHOTOSTIM_STATUS_READY"}:
                marker = line
                continue
            if marker == "PHOTOSTIM_ACTIVE":
                try:
                    active = bool(int(float(line)))
                except ValueError:
                    active = False
            elif marker == "PHOTOSTIM_SEQUENCE_POSITION":
                try:
                    position = int(float(line))
                except ValueError:
                    position = None
            elif marker == "PHOTOSTIM_COMPLETED_SEQUENCES":
                try:
                    completed_sequences = int(float(line))
                except ValueError:
                    completed_sequences = None
            elif marker == "PHOTOSTIM_SEQUENCE_SELECTED":
                try:
                    sequence.extend(int(float(part)) for part in line.split())
                except ValueError:
                    pass
        return active, position, sequence, completed_sequences

    def _fire_software_trigger(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_software_trigger_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
        self._emit_lines(path_name, lines)

    def _cancel_software_trigger(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        if runtime.software_trigger_stop is not None:
            runtime.software_trigger_stop.set()
        runtime.software_trigger_stop = None
        runtime.software_trigger_thread = None

    def _start_software_trigger_schedule(
        self,
        path_name: str,
        sequence_name: str,
        trigger_times_s: list[float],
        stimulus_pattern_numbers: list[int],
        request_path_name: str | None = None,
        reply_address: tuple[str, int] | None = None,
        schema_name: str | None = None,
        exp_id: str | None = None,
        seq_num: int | None = None,
    ) -> None:
        self._cancel_software_trigger(path_name)
        runtime = self._runtimes[path_name]
        stop_event = threading.Event()
        runtime.software_trigger_stop = stop_event

        def worker() -> None:
            start_time = time.monotonic()
            if self._debug_category_enabled.get("software_trigger_times", True):
                self.signals.log_message.emit(
                    f"[{path_name}] software trigger schedule started for sequence '{sequence_name}' with {len(trigger_times_s)} trigger(s)"
                )
                self.signals.log_message.emit(
                    f"[{path_name}] software trigger times: "
                    + ", ".join(f"{t:.4f}s" for t in trigger_times_s)
                )
            emitted_stimuli: list[str] = []
            for index, trigger_time_s in enumerate(trigger_times_s, start=1):
                while True:
                    if stop_event.is_set():
                        if self._debug_category_enabled.get("software_trigger_times", True):
                            self.signals.log_message.emit(f"[{path_name}] software trigger schedule cancelled")
                        return
                    remaining = start_time + trigger_time_s - time.monotonic()
                    if remaining <= 0:
                        break
                    time.sleep(min(remaining, 0.05))
                try:
                    self._fire_software_trigger(path_name)
                    if self._debug_category_enabled.get("stimuli", True) and index <= len(stimulus_pattern_numbers):
                        emitted_stimuli.append(str(stimulus_pattern_numbers[index - 1]))
                        self.signals.log_message.emit(f"[{path_name}] Stimuli: {''.join(emitted_stimuli)}")
                    if self._debug_category_enabled.get("software_trigger_times", True):
                        self.signals.log_message.emit(
                            f"[{path_name}] software trigger fired {index}/{len(trigger_times_s)} at t={trigger_time_s:.4f}s"
                        )
                except Exception as exc:
                    self.signals.log_message.emit(f"[{path_name}] ERROR: software trigger failed: {exc}")
                    return
            mismatch_message = self._finalize_pending_photostim_check(path_name, "Software trigger count check")
            if self._debug_category_enabled.get("software_trigger_times", True):
                self.signals.log_message.emit(f"[{path_name}] software trigger schedule completed")
            runtime.software_trigger_stop = None
            runtime.software_trigger_thread = None
            if (
                mismatch_message is not None
                and self.ignore_incomplete_trigger_checkbox.isChecked()
                and request_path_name is not None
                and reply_address is not None
            ):
                payload = {
                    "action": "trigger_photo_stim",
                    "status": "error",
                    "phase": "completion_check",
                    "schema_name": schema_name,
                    "expID": exp_id,
                    "seq_num": seq_num,
                    "sequence_name": sequence_name,
                    "error": mismatch_message,
                }
                self._send_json_reply(request_path_name, reply_address, payload)

        thread = threading.Thread(target=worker, daemon=True)
        runtime.software_trigger_thread = thread
        thread.start()

    def _parse_trigger_insert_position(self, lines: list[str]) -> int | None:
        for index, raw_line in enumerate(lines):
            if raw_line.strip() == "TRIGGER_PHOTOSTIM_INSERT_POSITION":
                if index + 1 >= len(lines):
                    return None
                try:
                    return int(float(lines[index + 1].strip()))
                except ValueError:
                    return None
        return None

    def _wait_for_leading_park_advance(
        self,
        path_name: str,
        baseline_position: int | None,
        baseline_completed_sequences: int | None,
        timeout_s: float = 2.0,
    ) -> tuple[int | None, int | None]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            active, current_position, _, completed_sequences = self._query_photostim_sequence_state(path_name)
            if baseline_completed_sequences is not None and completed_sequences is not None:
                if completed_sequences > baseline_completed_sequences:
                    return current_position, completed_sequences
            if baseline_position is not None and current_position is not None and current_position != baseline_position:
                return current_position, completed_sequences
            if not active:
                return current_position, completed_sequences
            time.sleep(0.02)
        raise RuntimeError("Leading park did not advance photostim sequence before ready.")

    def _wait_for_expected_photostim_completion(
        self,
        path_name: str,
        ready_position: int | None,
        ready_completed_sequences: int | None,
        expected_remaining: int,
        timeout_s: float = 2.0,
    ) -> tuple[bool, int | None, int | None]:
        deadline = time.monotonic() + timeout_s
        last_active = False
        last_position: int | None = None
        last_completed: int | None = None
        while time.monotonic() < deadline:
            active, current_position, _, completed_sequences = self._query_photostim_sequence_state(path_name)
            last_active = active
            last_position = current_position
            last_completed = completed_sequences
            if (
                ready_completed_sequences is not None
                and completed_sequences is not None
                and completed_sequences > ready_completed_sequences
                and not active
            ):
                return active, current_position, completed_sequences
            if ready_position is not None and current_position is not None:
                delivered = max(0, current_position - ready_position)
                if delivered >= expected_remaining:
                    return active, current_position, completed_sequences
            time.sleep(0.02)
        return last_active, last_position, last_completed

    def _finalize_pending_photostim_check(self, path_name: str, label: str) -> str | None:
        runtime = self._ensure_session(path_name)
        prep_state = runtime.prepared_photostim
        expected_remaining = prep_state.remaining_expected_triggers
        ready_position = prep_state.ready_sequence_position
        ready_completed = prep_state.ready_completed_sequences
        if expected_remaining is None or ready_position is None:
            return None
        active, current_position, completed_sequences = self._wait_for_expected_photostim_completion(
            path_name,
            ready_position,
            ready_completed,
            expected_remaining,
        )
        if (
            ready_completed is not None
            and completed_sequences is not None
            and completed_sequences > ready_completed
            and not active
        ):
            delivered_triggers = expected_remaining
        elif current_position is not None:
            delivered_triggers = max(0, current_position - ready_position)
            delivered_triggers = min(delivered_triggers, expected_remaining)
        else:
            return None
        if self._debug_category_enabled.get("software_trigger_count", True):
            self.signals.log_message.emit(
                f"[{path_name}] {label}: {delivered_triggers} stimuli delivered, {expected_remaining} expected"
            )
        prep_state.remaining_expected_triggers = None
        prep_state.ready_sequence_position = None
        prep_state.ready_completed_sequences = None
        prep_state.leading_park_fired = False
        prep_state.expected_sequence_position = None
        prep_state.last_trigger_insert_position = None
        if delivered_triggers != expected_remaining:
            return f"{delivered_triggers} stimuli delivered, {expected_remaining} expected"
        return None

    def _trigger_photo_stim_checked(self, path_name: str, sequence_indices: list[int]) -> None:
        runtime = self._ensure_session(path_name)
        prep_state = runtime.prepared_photostim
        _, current_position, _, _ = self._query_photostim_sequence_state(path_name)
        if not self.software_trigger_checkbox.isChecked():
            mismatch_message = self._finalize_pending_photostim_check(path_name, "Previous photostim sequence check")
            if mismatch_message is not None:
                if self.ignore_incomplete_trigger_checkbox.isChecked():
                    raise RuntimeError(mismatch_message)
                self.signals.log_message.emit(f"[{path_name}] WARNING: {mismatch_message}")
        lines = self._apply_trigger_sequence(path_name, sequence_indices)
        insert_position = self._parse_trigger_insert_position(lines)
        if insert_position is None:
            insert_position = current_position if current_position is not None else 1
        _, position_before_park, _, completed_before_park = self._query_photostim_sequence_state(path_name)
        self._fire_software_trigger(path_name)
        ready_position, ready_completed = self._wait_for_leading_park_advance(
            path_name,
            position_before_park,
            completed_before_park,
        )
        prep_state.last_trigger_insert_position = insert_position
        prep_state.expected_sequence_position = insert_position + len(sequence_indices)
        prep_state.remaining_expected_triggers = max(0, len(sequence_indices) - 1)
        prep_state.ready_sequence_position = ready_position
        prep_state.ready_completed_sequences = ready_completed if ready_completed is not None else completed_before_park
        prep_state.leading_park_fired = True
        self.signals.log_message.emit(f"[{path_name}] leading park fired; trial sequence armed")

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
            if seq_num < 0 or seq_num >= len(sequence_names):
                raise IndexError(f"seq_num {seq_num} is out of range for {len(sequence_names)} sequence(s)")
            sequence_name = sequence_names[seq_num]
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
