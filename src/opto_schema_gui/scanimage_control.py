from __future__ import annotations

import configparser
import math
import html
import json
import random
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml
from PyQt6.QtCore import QObject, QTimer, pyqtSignal
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    build_prepare_schema_photostim_command,
    build_prepare_trial_waveform_command,
    build_photostim_sequence_status_command,
    build_raw_vdaq_do_test_status_command,
    build_run_script_command,
    build_schema_payload_load_command,
    build_software_trigger_command,
    build_start_trial_waveform_command,
    build_stop_trial_waveform_command,
    build_test_stim_waveform_command,
    build_test_stim_waveform_external_start_command_configurable,
    build_test_photostim_command,
    build_trial_waveform_status_command,
    build_trigger_photostim_command,
    build_arm_trial_waveform_command,
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
    waveform_test_result = pyqtSignal(str, int, int, int)


@dataclass
class PreparedPhotostimState:
    schema_path: Path | None = None
    schema_name: str = ""
    exp_id: str = ""
    prepared_seq_nums: list[int] = field(default_factory=list)
    prepared_sequence_names: list[str] = field(default_factory=list)
    imported_pattern_names: list[str] = field(default_factory=list)
    pattern_to_schema_index: dict[str, int] = field(default_factory=dict)
    sequence_to_stimulus_group: dict[str, int] = field(default_factory=dict)
    sequence_to_stimulus_groups: dict[str, list[int]] = field(default_factory=dict)
    triggered_seq_num: int | None = None
    triggered_sequence_name: str = ""
    triggered_stimulus_groups: list[int] = field(default_factory=list)
    triggered_insert_position: int | None = None
    triggered_idle_position: int | None = None
    remaining_expected_triggers: int | None = None
    ready_sequence_position: int | None = None
    ready_completed_sequences: int | None = None
    waveform_expected_done_time_s: float | None = None

    def reset(self) -> None:
        self.schema_path = None
        self.schema_name = ""
        self.exp_id = ""
        self.prepared_seq_nums = []
        self.prepared_sequence_names = []
        self.imported_pattern_names = []
        self.pattern_to_schema_index = {}
        self.sequence_to_stimulus_group = {}
        self.sequence_to_stimulus_groups = {}
        self.triggered_seq_num = None
        self.triggered_sequence_name = ""
        self.triggered_stimulus_groups = []
        self.triggered_insert_position = None
        self.triggered_idle_position = None
        self.remaining_expected_triggers = None
        self.ready_sequence_position = None
        self.ready_completed_sequences = None
        self.waveform_expected_done_time_s = None


@dataclass
class ExperimentTrackingState:
    exp_id: str = ""
    schema_name: str = ""
    params: dict[str, object] = field(default_factory=dict)
    stimulus_conditions: list[dict[str, object]] = field(default_factory=list)
    current_trial_index: int | None = None
    current_stimulus_condition: dict[str, object] | None = None

    def reset(self) -> None:
        self.exp_id = ""
        self.schema_name = ""
        self.params = {}
        self.stimulus_conditions = []
        self.current_trial_index = None
        self.current_stimulus_condition = None


@dataclass
class PathRuntime:
    path_config: PathConfig
    session: MatlabSession | None = None
    udp_listener: "UdpListener | None" = None
    status: str = "stopped"
    launched: bool = False
    last_context: ExperimentContext | None = None
    prepared_photostim: PreparedPhotostimState = field(default_factory=PreparedPhotostimState)
    experiment_tracking: ExperimentTrackingState = field(default_factory=ExperimentTrackingState)
    lock: threading.Lock = field(default_factory=threading.Lock)
    software_trigger_stop: threading.Event | None = None
    software_trigger_thread: threading.Thread | None = None
    waveform_monitor_stop: threading.Event | None = None
    waveform_monitor_thread: threading.Thread | None = None


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
    external_trial_trigger_label: QLabel | None
    photostim_trigger_input_label: QLabel | None
    photostim_trigger_output_label: QLabel | None
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
    remote_prompt_response = pyqtSignal(str, str, object)


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
        self.resize(460, 220)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.schema_name_edit = QLineEdit("DEFAULT")
        self.exp_id_edit = QLineEdit("2026-03-25_10_TEST")
        self.seq_num_spin = QSpinBox()
        self.seq_num_spin.setRange(0, 1000000)
        self.seq_num_spin.setValue(0)
        form.addRow("Schema Name", self.schema_name_edit)
        form.addRow("Exp ID", self.exp_id_edit)
        form.addRow("Trigger Seq Num (0-based)", self.seq_num_spin)
        layout.addLayout(form)

        note = QLabel("Run Prep pre-builds all schema sequences. Seq Num is only used by Run Trigger.")
        note.setWordWrap(True)
        layout.addWidget(note)

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


class StimWaveformTestDialog(QDialog):
    def __init__(
        self,
        path_name: str,
        preview_callback: Callable[[], tuple[bool, int | None, list[int]]],
        stimulate_callback: Callable[[float, float, float], None],
        result_signal: pyqtSignal,
        action_label: str = "Stimulate",
        require_sequence_capacity: bool = True,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._path_name = path_name
        self._preview_callback = preview_callback
        self._stimulate_callback = stimulate_callback
        self._require_sequence_capacity = require_sequence_capacity
        self.setWindowTitle("Test Stim Waveform")
        self.resize(460, 260)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(0.1, 1000.0)
        self.freq_spin.setDecimals(3)
        self.freq_spin.setValue(10.0)
        self.duty_spin = QDoubleSpinBox()
        self.duty_spin.setRange(0.001, 1.0)
        self.duty_spin.setDecimals(3)
        self.duty_spin.setSingleStep(0.01)
        self.duty_spin.setValue(0.1)
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(0.001, 60.0)
        self.duration_spin.setDecimals(3)
        self.duration_spin.setValue(0.5)
        form.addRow("Frequency (Hz)", self.freq_spin)
        form.addRow("Duty Cycle", self.duty_spin)
        form.addRow("Duration (s)", self.duration_spin)
        layout.addLayout(form)

        info_form = QFormLayout()
        self.generated_count_label = QLabel("-")
        self.pulse_width_label = QLabel("-")
        self.remaining_entries_label = QLabel("-")
        self.enough_entries_label = QLabel("-")
        self.advanced_count_label = QLabel("-")
        info_form.addRow("Pulses Generated", self.generated_count_label)
        info_form.addRow("Pulse Width (ms)", self.pulse_width_label)
        if require_sequence_capacity:
            info_form.addRow("Remaining Seq Entries", self.remaining_entries_label)
            info_form.addRow("Enough Entries", self.enough_entries_label)
        info_form.addRow("Seq Advanced", self.advanced_count_label)
        layout.addLayout(info_form)

        button_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.stimulate_btn = QPushButton(action_label)
        self.cancel_btn = QPushButton("Cancel")
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_btn)
        button_row.addWidget(self.stimulate_btn)
        button_row.addWidget(self.cancel_btn)
        layout.addLayout(button_row)

        self.freq_spin.valueChanged.connect(self._refresh_preview)
        self.duty_spin.valueChanged.connect(self._refresh_preview)
        self.duration_spin.valueChanged.connect(self._refresh_preview)
        self.refresh_btn.clicked.connect(self._refresh_preview)
        self.stimulate_btn.clicked.connect(self._stimulate)
        self.cancel_btn.clicked.connect(self.reject)
        result_signal.connect(self._handle_result)
        self._refresh_preview()

    def _requested_pulse_count(self) -> int:
        return max(1, int(round(self.freq_spin.value() * self.duration_spin.value())))

    def _refresh_preview(self) -> None:
        requested_pulses = self._requested_pulse_count()
        pulse_width_ms = 1000.0 * self.duty_spin.value() / self.freq_spin.value()
        active, position, sequence = self._preview_callback()
        remaining_entries = 0
        if position is not None:
            remaining_entries = max(0, len(sequence) - position + 1)
        enough = active and (remaining_entries >= requested_pulses if self._require_sequence_capacity else True)
        self.generated_count_label.setText(str(requested_pulses))
        self.pulse_width_label.setText(f"{pulse_width_ms:.3f}")
        if self._require_sequence_capacity:
            self.remaining_entries_label.setText(str(remaining_entries))
            self.enough_entries_label.setText("Yes" if enough else "No")
        self.stimulate_btn.setEnabled(enough)

    def _stimulate(self) -> None:
        if not self.stimulate_btn.isEnabled():
            QMessageBox.warning(
                self,
                "Not ready",
                "Photostim is not active or there are not enough remaining stimulus groups after the current position.",
            )
            return
        self.advanced_count_label.setText("Running...")
        self._stimulate_callback(self.freq_spin.value(), self.duty_spin.value(), self.duration_spin.value())

    def _handle_result(self, path_name: str, before_position: int, after_position: int, delta: int) -> None:
        if path_name != self._path_name:
            return
        self.advanced_count_label.setText(str(delta))
        self._refresh_preview()


class ScanImageControlWidget(QWidget):
    def __init__(self, schema_path_provider: Callable[[], Path | None], parent: QWidget | None = None):
        super().__init__(parent)
        self.schema_path_provider = schema_path_provider
        self.repo_root = Path(__file__).resolve().parents[2]
        self.signals = _ControlSignals()
        self.signals.log_message.connect(self._append_log)
        self.signals.path_status.connect(self._set_path_status)
        self.signals.path_udp_log.connect(self._append_path_udp_log)
        self.signals.waveform_test_result.connect(lambda *_: None)
        self.signals.udp_message.connect(self._handle_udp_message)
        self.signals.remote_prompt_response.connect(self._handle_remote_prompt_response)
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
        self._startup_reconnect_prompt_box: QMessageBox | None = None
        self._startup_reconnect_prompt_paths: list[str] = []
        self._remote_prompt_token = 0
        self._remote_prompt_waiters: dict[int, tuple[threading.Event, dict[str, object]]] = {}
        self._debug_category_enabled: dict[str, bool] = {
            "general": True,
            "udp": True,
            "experiment": True,
            "software_trigger_times": True,
            "software_trigger_count": True,
            "stimuli": True,
        }
        self._gui_state_path = self.repo_root / ".gui_state.ini"
        self.save_root, self.schema_root = self._load_path_roots()
        self._build_ui()
        self._load_gui_state()
        self.reload_discovery()

    @staticmethod
    def _sequence_position_delta(before_position: int | None, after_position: int | None) -> int:
        if before_position is None or after_position is None:
            return 0
        return max(0, after_position - before_position)

    def _format_photostim_state(
        self,
        active: bool,
        position: int | None,
        completed_sequences: int | None,
        sequence: list[int] | None = None,
    ) -> str:
        parts = [
            f"active={int(active)}",
            f"sequencePosition={'NaN' if position is None else position}",
            f"completedSequences={'NaN' if completed_sequences is None else completed_sequences}",
        ]
        if sequence is not None:
            preview = " ".join(str(v) for v in sequence[:12])
            if len(sequence) > 12:
                preview = preview + " ..."
            parts.append(f"sequenceHead=[{preview}]")
        return ", ".join(parts)

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
        self.update_restart_btn = QPushButton("Update And Restart")
        self.test_prep_patterns_btn = QPushButton("Test Photostim")
        self.test_stim_waveform_btn = QPushButton("Test stim waveform")
        self.test_stim_waveform_external_btn = QPushButton("Test stim waveform ext")
        self.start_config_btn.setStyleSheet("color: #15803d;")
        self.stop_config_btn.setStyleSheet("color: #b91c1c;")
        button_column.addWidget(self.clear_all_logs_btn)
        button_column.addWidget(self.start_config_btn)
        button_column.addWidget(self.stop_config_btn)
        button_column.addWidget(self.reload_btn)
        button_column.addWidget(self.update_restart_btn)
        button_column.addWidget(self.test_prep_patterns_btn)
        button_column.addWidget(self.test_stim_waveform_btn)
        button_column.addWidget(self.test_stim_waveform_external_btn)
        button_column.addStretch(1)
        config_layout.addLayout(button_column)

        config_form_container = QWidget()
        config_form = QFormLayout(config_form_container)
        self.machine_combo = QComboBox()
        self.config_combo = QComboBox()
        self.force_simulated_checkbox = QCheckBox("Force Simulated Mode")
        self.ignore_incomplete_trigger_checkbox = QCheckBox("Send mismatch errors upstream")
        self.ignore_incomplete_trigger_checkbox.setChecked(True)
        self.trigger_mode_combo = QComboBox()
        self.trigger_mode_combo.addItem("Software trigger (debug)", "software")
        self.trigger_mode_combo.addItem("Hardware external trigger", "hardware")
        self.trigger_mode_combo.setCurrentIndex(1)
        config_form.addRow("Machine", self.machine_combo)
        config_form.addRow("Config", self.config_combo)
        config_form.addRow("", self.force_simulated_checkbox)
        config_form.addRow("", self.ignore_incomplete_trigger_checkbox)
        config_form.addRow("Trigger mode", self.trigger_mode_combo)
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
        self.show_experiment_debug_checkbox = QCheckBox("Experiment info")
        self.show_trigger_times_debug_checkbox = QCheckBox("Software trigger times")
        self.show_trigger_count_debug_checkbox = QCheckBox("Trigger count check")
        self.show_stimuli_debug_checkbox = QCheckBox("Stimuli")
        self.clear_log_btn = QPushButton("Clear Debug Output")
        for checkbox in (
            self.show_general_debug_checkbox,
            self.show_udp_debug_checkbox,
            self.show_experiment_debug_checkbox,
            self.show_trigger_times_debug_checkbox,
            self.show_trigger_count_debug_checkbox,
            self.show_stimuli_debug_checkbox,
        ):
            checkbox.setChecked(True)
            checkbox.toggled.connect(self._refresh_debug_log)
            checkbox.toggled.connect(self._save_gui_state)
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
        self.update_restart_btn.clicked.connect(self._update_and_restart)
        self.test_prep_patterns_btn.clicked.connect(self._open_photostim_test_dialog)
        self.test_stim_waveform_btn.clicked.connect(self._run_test_stim_waveform)
        self.test_stim_waveform_external_btn.clicked.connect(self._run_test_stim_waveform_external)
        self.clear_log_btn.clicked.connect(self._clear_all_logs)
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

    def _load_gui_state(self) -> None:
        parser = configparser.ConfigParser()
        if not self._gui_state_path.is_file():
            return
        parser.read(self._gui_state_path)
        if not parser.has_section("debug"):
            return
        section = parser["debug"]
        mapping = (
            ("general", self.show_general_debug_checkbox),
            ("udp", self.show_udp_debug_checkbox),
            ("experiment", self.show_experiment_debug_checkbox),
            ("software_trigger_times", self.show_trigger_times_debug_checkbox),
            ("software_trigger_count", self.show_trigger_count_debug_checkbox),
            ("stimuli", self.show_stimuli_debug_checkbox),
        )
        for key, checkbox in mapping:
            if key in section:
                checkbox.setChecked(section.getboolean(key, fallback=checkbox.isChecked()))

    def _save_gui_state(self, *_args) -> None:
        parser = configparser.ConfigParser()
        parser["debug"] = {
            "general": str(self.show_general_debug_checkbox.isChecked()).lower(),
            "udp": str(self.show_udp_debug_checkbox.isChecked()).lower(),
            "experiment": str(self.show_experiment_debug_checkbox.isChecked()).lower(),
            "software_trigger_times": str(self.show_trigger_times_debug_checkbox.isChecked()).lower(),
            "software_trigger_count": str(self.show_trigger_count_debug_checkbox.isChecked()).lower(),
            "stimuli": str(self.show_stimuli_debug_checkbox.isChecked()).lower(),
        }
        with self._gui_state_path.open("w", encoding="utf-8") as handle:
            parser.write(handle)

    def _on_force_simulated_toggled(self, checked: bool) -> None:
        mode = "enabled" if checked else "disabled"
        self.signals.log_message.emit(f"[config] Force Simulated Mode {mode}")

    def _current_trigger_mode(self) -> str:
        data = self.trigger_mode_combo.currentData()
        return str(data) if data is not None else "software"

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

    def _run_git_command(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )

    def _relaunch_command(self) -> list[str]:
        if sys.argv and sys.argv[0]:
            launcher = Path(sys.argv[0])
            if not launcher.is_absolute():
                launcher = (self.repo_root / launcher).resolve()
        else:
            launcher = self.repo_root / "run_pattern_builder_gui.py"
        return [sys.executable, str(launcher), *sys.argv[1:]]

    def _log_process_output(self, prefix: str, completed: subprocess.CompletedProcess[str]) -> None:
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        if stdout:
            for line in stdout.splitlines():
                cleaned = line.strip()
                if cleaned:
                    self.signals.log_message.emit(f"{prefix} {cleaned}")
        if stderr:
            for line in stderr.splitlines():
                cleaned = line.strip()
                if cleaned:
                    self.signals.log_message.emit(f"{prefix} stderr: {cleaned}")

    @staticmethod
    def _is_ignorable_git_status_entry(entry: str) -> bool:
        if not entry or len(entry) < 4:
            return False
        path = entry[3:].strip().strip('"')
        return path.endswith(".pyc") and "__pycache__/" in path

    def _discard_tracked_git_changes(self, entries: list[str]) -> bool:
        tracked_paths = [entry[3:].strip().strip('"') for entry in entries if len(entry) >= 4]
        if not tracked_paths:
            return True
        self.signals.log_message.emit("[update] Discarding tracked local changes before pull")
        for entry in entries[:10]:
            self.signals.log_message.emit(f"[update] reset {entry}")
        restore_result = self._run_git_command(["restore", "--source=HEAD", "--", *tracked_paths])
        self._log_process_output("[update]", restore_result)
        return restore_result.returncode == 0

    def _update_and_restart(self) -> None:
        status_result = self._run_git_command(["status", "--porcelain", "--untracked-files=no"])
        if status_result.returncode != 0:
            self._log_process_output("[update]", status_result)
            QMessageBox.critical(
                self,
                "Update failed",
                "Could not inspect git status. See debug log for details.",
            )
            return
        dirty_entries = [
            line.rstrip()
            for line in status_result.stdout.splitlines()
            if line.strip() and not self._is_ignorable_git_status_entry(line.rstrip())
        ]
        if dirty_entries:
            if not self._discard_tracked_git_changes(dirty_entries):
                QMessageBox.critical(
                    self,
                    "Update failed",
                    "Could not discard tracked local changes before pulling. See debug log for details.",
                )
                return

        self.signals.log_message.emit("[update] Running git pull --ff-only")
        pull_result = self._run_git_command(["pull", "--ff-only"])
        self._log_process_output("[update]", pull_result)
        if pull_result.returncode != 0:
            QMessageBox.critical(
                self,
                "Update failed",
                "git pull --ff-only failed. See debug log for details.",
            )
            return

        relaunch_cmd = self._relaunch_command()
        self.signals.log_message.emit(f"[update] Relaunching: {' '.join(relaunch_cmd)}")
        try:
            popen_kwargs: dict[str, object] = {
                "cwd": self.repo_root,
                "start_new_session": True,
            }
            if sys.platform.startswith("win"):
                creationflags = 0
                creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
                creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                if creationflags:
                    popen_kwargs["creationflags"] = creationflags
            subprocess.Popen(relaunch_cmd, **popen_kwargs)
        except Exception as exc:
            self.signals.log_message.emit(f"[update] relaunch failed: {exc}")
            QMessageBox.critical(
                self,
                "Restart failed",
                f"Could not relaunch the application:\n{exc}",
            )
            return

        self.signals.log_message.emit("[update] Restarting application")
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            app.quit()

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
        self._startup_reconnect_prompt_paths = list(available_paths)
        joined = ", ".join(self._startup_reconnect_prompt_paths)
        box = QMessageBox(self)
        box.setWindowTitle("Reconnect MATLAB")
        box.setText(f"Found running MATLAB session(s) for: {joined}.\nConnect to them now?")
        yes_button = box.addButton(QMessageBox.StandardButton.Yes)
        no_button = box.addButton(QMessageBox.StandardButton.No)
        box.setDefaultButton(yes_button)
        self._startup_reconnect_prompt_box = box
        yes_button.clicked.connect(lambda: self._resolve_startup_reconnect_prompt(True))
        no_button.clicked.connect(lambda: self._resolve_startup_reconnect_prompt(False))
        box.open()

    def _resolve_startup_reconnect_prompt(self, should_reconnect: bool) -> None:
        available_paths = list(self._startup_reconnect_prompt_paths)
        joined = ", ".join(available_paths)
        box = self._startup_reconnect_prompt_box
        self._startup_reconnect_prompt_box = None
        self._startup_reconnect_prompt_paths = []
        if box is not None:
            box.hide()
            box.deleteLater()
        if not should_reconnect:
            if joined:
                self.signals.log_message.emit(f"[config] existing MATLAB sessions ignored: {joined}")
            return

        def worker() -> None:
            for path_name in available_paths:
                self._run_action(path_name, f"Launching path: {path_name}", self._launch_path)

        threading.Thread(target=worker, daemon=True).start()

    def pending_remote_prompt(self) -> dict[str, object] | None:
        if self._startup_reconnect_prompt_box is not None and self._startup_reconnect_prompt_paths:
            return {
                "prompt_id": "startup_reconnect",
                "message": (
                    "Found running MATLAB session(s) for: "
                    + ", ".join(self._startup_reconnect_prompt_paths)
                    + ". Connect to them now?"
                ),
                "choices": ["yes", "no"],
                "paths": list(self._startup_reconnect_prompt_paths),
            }
        return None

    def respond_remote_prompt(self, prompt_id: str, choice: str) -> bool:
        if threading.current_thread() is threading.main_thread():
            return self._respond_remote_prompt_ui(prompt_id, choice)

        self._remote_prompt_token += 1
        token = self._remote_prompt_token
        done = threading.Event()
        holder: dict[str, object] = {}
        self._remote_prompt_waiters[token] = (done, holder)
        self.signals.remote_prompt_response.emit(prompt_id, choice, token)
        if not done.wait(5.0):
            self._remote_prompt_waiters.pop(token, None)
            raise TimeoutError("Timed out waiting for GUI thread to handle remote prompt")
        error = holder.get("error")
        if isinstance(error, BaseException):
            raise error
        return bool(holder.get("handled", False))

    def _respond_remote_prompt_ui(self, prompt_id: str, choice: str) -> bool:
        if prompt_id != "startup_reconnect" or self._startup_reconnect_prompt_box is None:
            return False
        normalized = choice.strip().lower()
        if normalized not in {"yes", "no"}:
            raise ValueError("Prompt choice must be 'yes' or 'no'")
        self._resolve_startup_reconnect_prompt(normalized == "yes")
        return True

    def _handle_remote_prompt_response(self, prompt_id: str, choice: str, token: object) -> None:
        waiter = self._remote_prompt_waiters.get(int(token))
        if waiter is None:
            return
        done, holder = waiter
        try:
            holder["handled"] = self._respond_remote_prompt_ui(prompt_id, choice)
        except Exception as exc:
            holder["error"] = exc
        finally:
            done.set()
            self._remote_prompt_waiters.pop(int(token), None)

    def get_debug_log_lines(self, last_n: int = 200) -> list[str]:
        lines = [f"{timestamp} {message}" for timestamp, message, _ in self._debug_history]
        if last_n > 0:
            return lines[-last_n:]
        return lines

    def get_path_udp_log_lines(self, path_name: str, last_n: int = 200) -> list[str]:
        widgets = self._path_tabs.get(path_name)
        if widgets is None:
            raise KeyError(f"Unknown path '{path_name}'")
        lines = [line for line in widgets.udp_text.toPlainText().splitlines() if line.strip()]
        if last_n > 0:
            return lines[-last_n:]
        return lines

    def get_remote_state(self) -> dict[str, object]:
        path_states: dict[str, object] = {}
        for path_name, runtime in self._runtimes.items():
            path_states[path_name] = {
                "status": runtime.status,
                "launched": runtime.launched,
                "listener_on": runtime.udp_listener is not None,
                "engine_name": runtime.path_config.engine_name,
                "listener_host": runtime.path_config.listener_host,
                "listener_port": runtime.path_config.listener_port,
                "experiment_tracking": {
                    "exp_id": runtime.experiment_tracking.exp_id,
                    "schema_name": runtime.experiment_tracking.schema_name,
                    "current_trial_index": runtime.experiment_tracking.current_trial_index,
                    "stimulus_condition_count": len(runtime.experiment_tracking.stimulus_conditions),
                    "current_stimulus_condition": runtime.experiment_tracking.current_stimulus_condition,
                    "params": runtime.experiment_tracking.params,
                },
            }
        return {
            "machine": self.machine_combo.currentText(),
            "config": self.config_combo.currentText(),
            "force_simulated": self.force_simulated_checkbox.isChecked(),
            "send_mismatch_errors_upstream": self.ignore_incomplete_trigger_checkbox.isChecked(),
            "trigger_mode": {
                "label": self.trigger_mode_combo.currentText(),
                "value": self._current_trigger_mode(),
            },
            "photostim_path": self.machine_config.photostim_path if self.machine_config is not None else "",
            "paths": path_states,
            "pending_prompt": self.pending_remote_prompt(),
        }

    def set_remote_state(self, values: dict[str, object]) -> dict[str, object]:
        applied: dict[str, object] = {}
        if "machine" in values:
            machine = str(values["machine"])
            if self._has_active_paths() and machine != self.machine_combo.currentText():
                raise ValueError("Stop all paths before switching machine")
            index = self.machine_combo.findText(machine)
            if index < 0:
                raise ValueError(f"Unknown machine '{machine}'")
            self.machine_combo.setCurrentIndex(index)
            applied["machine"] = self.machine_combo.currentText()
        if "config" in values:
            config = str(values["config"])
            if self._has_active_paths() and config != self.config_combo.currentText():
                raise ValueError("Stop all paths before switching config")
            index = self.config_combo.findText(config)
            if index < 0:
                raise ValueError(f"Unknown config '{config}'")
            self.config_combo.setCurrentIndex(index)
            applied["config"] = self.config_combo.currentText()
        if "force_simulated" in values:
            checked = bool(values["force_simulated"])
            self.force_simulated_checkbox.setChecked(checked)
            applied["force_simulated"] = self.force_simulated_checkbox.isChecked()
        if "send_mismatch_errors_upstream" in values:
            checked = bool(values["send_mismatch_errors_upstream"])
            self.ignore_incomplete_trigger_checkbox.setChecked(checked)
            applied["send_mismatch_errors_upstream"] = self.ignore_incomplete_trigger_checkbox.isChecked()
        if "trigger_mode" in values:
            trigger_mode = str(values["trigger_mode"]).strip()
            trigger_mode = {
                "waveform_software": "software",
                "waveform_external": "hardware",
            }.get(trigger_mode, trigger_mode)
            index = self.trigger_mode_combo.findData(trigger_mode)
            if index < 0:
                index = self.trigger_mode_combo.findText(trigger_mode)
            if index < 0:
                raise ValueError(f"Unknown trigger mode '{trigger_mode}'")
            self.trigger_mode_combo.setCurrentIndex(index)
            applied["trigger_mode"] = self._current_trigger_mode()
        return applied

    def invoke_remote_action(
        self,
        action: str,
        path_name: str | None = None,
        exp_id: str | None = None,
    ) -> dict[str, object]:
        normalized = action.strip().lower()
        if normalized == "reload_configs":
            self.reload_discovery()
            return {"started": True}
        if normalized == "start_config":
            self.start_config()
            return {"started": True}
        if normalized == "stop_config":
            self.stop_config()
            return {"started": True}
        if normalized == "update_and_restart":
            self._update_and_restart()
            return {"started": True}
        if normalized == "import_patterns":
            self.import_patterns_for_photostim()
            return {"started": True}
        if not path_name:
            raise ValueError("path_name is required for path-scoped actions")
        if path_name not in self._runtimes:
            raise ValueError(f"Unknown path '{path_name}'")
        if normalized in {"launch_path", "start_path"}:
            self._spawn_action(path_name, f"Launching path: {path_name}", self._launch_path)
            return {"started": True}
        if normalized == "stop_path":
            self._spawn_action(path_name, "stop path", self._stop_path)
            return {"started": True}
        if normalized == "focus_path":
            self._spawn_action(path_name, "focus", self._focus_path)
            return {"started": True}
        if normalized == "acquire":
            if not exp_id:
                raise ValueError("exp_id is required for acquire")
            self._spawn_action(path_name, "acquire", lambda name: self._start_acquisition(name, exp_id))
            return {"started": True}
        if normalized == "stop_acquisition":
            self._spawn_action(path_name, "stop acquisition", self._stop_acquisition)
            return {"started": True}
        if normalized == "inspect_slm":
            self._spawn_action(path_name, "inspect slm", self._inspect_photostim_api)
            return {"started": True}
        if normalized == "start_listener":
            self._spawn_action(path_name, "start listener", self._start_listener)
            return {"started": True}
        if normalized == "stop_listener":
            self._spawn_action(path_name, "stop listener", self._stop_listener)
            return {"started": True}
        raise ValueError(f"Unknown action '{action}'")

    def eval_matlab_command(
        self,
        path_name: str,
        command: str,
        timeout_s: float | None = None,
        prepend_preamble: bool = True,
    ) -> list[str]:
        runtime = self._ensure_session(path_name)
        matlab_command = command
        if prepend_preamble:
            matlab_command = "\n".join([build_global_preamble(runtime.path_config), command])
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                matlab_command,
                timeout_s=timeout_s if timeout_s is not None else runtime.path_config.command_timeout_s,
            )
            self._emit_lines(path_name, lines)
            return lines

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
        external_trial_trigger_label: QLabel | None = None
        photostim_trigger_input_label: QLabel | None = None
        photostim_trigger_output_label: QLabel | None = None
        if self.machine_config is not None and path_name == self.machine_config.photostim_path:
            external_trial_trigger_label = QLabel(runtime.path_config.trial_waveform_start_trigger_port)
            photostim_trigger_input_label = QLabel(runtime.path_config.trial_waveform_photostim_trigger_term)
            photostim_trigger_output_label = QLabel(runtime.path_config.trial_waveform_output_port)
            info_form.addRow("External trial trigger input", external_trial_trigger_label)
            info_form.addRow("Photostim trigger input", photostim_trigger_input_label)
            info_form.addRow("Photostim trigger output", photostim_trigger_output_label)
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
            external_trial_trigger_label=external_trial_trigger_label,
            photostim_trigger_input_label=photostim_trigger_input_label,
            photostim_trigger_output_label=photostim_trigger_output_label,
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
        self._cancel_software_trigger(path_name)
        self._cancel_waveform_monitor(path_name)
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

    def _run_test_stim_waveform(self) -> None:
        if self.machine_config is None or not self.machine_config.photostim_path:
            self.signals.log_message.emit("Test stim waveform skipped: no photostim path configured")
            return
        path_name = self.machine_config.photostim_path
        dialog = StimWaveformTestDialog(
            path_name,
            lambda: self._get_waveform_test_preview(path_name),
            lambda frequency_hz, duty_cycle, duration_s: self._spawn_action(
                path_name,
                "test stim waveform",
                lambda name: self._test_stim_waveform_configured(name, frequency_hz, duty_cycle, duration_s),
            ),
            self.signals.waveform_test_result,
            "Stimulate",
            True,
            self,
        )
        dialog.exec()

    def _run_test_stim_waveform_external(self) -> None:
        if self.machine_config is None or not self.machine_config.photostim_path:
            self.signals.log_message.emit("Test stim waveform ext skipped: no photostim path configured")
            return
        path_name = self.machine_config.photostim_path
        dialog = StimWaveformTestDialog(
            path_name,
            lambda: self._get_waveform_test_preview(path_name),
            lambda frequency_hz, duty_cycle, duration_s: self._spawn_action(
                path_name,
                "test stim waveform external start",
                lambda name: self._test_stim_waveform_external_configured(name, frequency_hz, duty_cycle, duration_s),
            ),
            self.signals.waveform_test_result,
            "Arm External",
            False,
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

    def _test_stim_waveform(self, path_name: str) -> None:
        self._test_stim_waveform_configured(path_name, 10.0, 0.1, 0.5)

    def _test_stim_waveform_configured(
        self,
        path_name: str,
        frequency_hz: float,
        duty_cycle: float,
        duration_s: float,
    ) -> None:
        runtime = self._ensure_session(path_name)
        _, before_position, _, _, _ = self._query_photostim_sequence_state(path_name)
        pulse_count = max(1, int(round(frequency_hz * duration_s)))
        pulse_times_s = [((idx + 1) / frequency_hz) for idx in range(pulse_count)]
        pulse_width_s = duty_cycle / frequency_hz
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_test_stim_waveform_command(runtime.path_config, pulse_times_s, pulse_width_s),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "stim waveform test"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
        time.sleep((max(pulse_times_s) if pulse_times_s else 0.0) + pulse_width_s + 2.0)
        _, after_position, _, _, _ = self._query_photostim_sequence_state(path_name)
        before_value = 0 if before_position is None else before_position
        after_value = 0 if after_position is None else after_position
        delta = self._sequence_position_delta(before_position, after_position)
        self.signals.waveform_test_result.emit(path_name, before_value, after_value, delta)

    def _test_stim_waveform_external(self, path_name: str) -> None:
        self._test_stim_waveform_external_configured(path_name, 10.0, 0.1, 0.5)

    def _test_stim_waveform_external_configured(
        self,
        path_name: str,
        frequency_hz: float,
        duty_cycle: float,
        duration_s: float,
    ) -> None:
        runtime = self._ensure_session(path_name)
        _, position_before, _, completed_before, _ = self._query_photostim_sequence_state(path_name)
        pulse_count = max(1, int(round(frequency_hz * duration_s)))
        pulse_times_s = [((idx + 1) / frequency_hz) for idx in range(pulse_count)]
        pulse_width_s = duty_cycle / frequency_hz
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_test_stim_waveform_external_start_command_configurable(
                    runtime.path_config, pulse_times_s, pulse_width_s
                ),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "stim waveform ext test"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
        self.signals.log_message.emit(
            f"[{path_name}] Pulse external trial trigger input {runtime.path_config.trial_waveform_start_trigger_port} now"
        )
        self._start_test_waveform_external_monitor(
            path_name,
            position_before=position_before,
            completed_before=completed_before,
            expected_duration_s=(max(pulse_times_s) if pulse_times_s else 0.0) + pulse_width_s + 0.1,
        )

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
        schema_json_path: Path | None = None
        if prepare_sequence or start_photostim:
            schema_payload = yaml.safe_load(schema_path.read_text()) or {}
            schema_json_path = runtime.path_config.directory / "_opto_schema_payload.json"
            schema_json_path.write_text(json.dumps(schema_payload, separators=(",", ":")))
        with runtime.lock:
            assert runtime.session is not None
            if prepare_sequence or start_photostim:
                assert schema_json_path is not None
                lines = runtime.session.eval(
                    build_schema_payload_load_command(runtime.path_config, schema_json_path=schema_json_path),
                    timeout_s=runtime.path_config.command_timeout_s,
                )
                self._emit_lines(path_name, lines)
                lines = runtime.session.eval(
                    build_prepare_schema_photostim_command(runtime.path_config),
                    timeout_s=runtime.path_config.command_timeout_s,
                )
            else:
                lines = runtime.session.eval(
                    build_import_command(
                        schema_path,
                        runtime.path_config,
                        pattern_names=pattern_names,
                        prepare_sequence=prepare_sequence,
                        start_photostim=start_photostim,
                        schema_json_path=schema_json_path,
                    ),
                    timeout_s=runtime.path_config.command_timeout_s,
                )
            runtime.status = "photostim ready" if start_photostim else "patterns imported"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
        if schema_json_path is not None:
            try:
                schema_json_path.unlink(missing_ok=True)
            except Exception:
                pass

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
        if "updated experiment parameters" in lower or "current trial set to index" in lower:
            return "experiment"
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
            "experiment": self.show_experiment_debug_checkbox.isChecked(),
            "software_trigger_times": self.show_trigger_times_debug_checkbox.isChecked(),
            "software_trigger_count": self.show_trigger_count_debug_checkbox.isChecked(),
            "stimuli": self.show_stimuli_debug_checkbox.isChecked(),
        }
        if category == "udp":
            return self.show_udp_debug_checkbox.isChecked()
        if category == "experiment":
            return self.show_experiment_debug_checkbox.isChecked()
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
        udp_line = f"[{path_name} udp {address[0]}:{address[1]}] received text command={message}"
        self.signals.path_udp_log.emit(path_name, udp_line)
        self.signals.log_message.emit(f"[{path_name}] received text UDP command '{message}'")
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
        udp_line = f"[{path_name} udp {address[0]}:{address[1]}] received action={action}"
        self.signals.path_udp_log.emit(path_name, udp_line)

        if action == "update_experiment_params":
            exp_id = str(message.get("expID", "")).strip()
            schema_name = str(message.get("schema_name", "")).strip()
            self.signals.log_message.emit(
                f"[{path_name}] updated experiment parameters for expID='{exp_id}' "
                f"schema='{schema_name}'"
            )
            try:
                self._handle_update_experiment_params_request(
                    request_path_name=path_name,
                    message=message,
                    reply_address=address,
                )
            except Exception as exc:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "update_experiment_params",
                        "status": "error",
                        "error": str(exc),
                    },
                )
            return

        if action == "start_trial":
            trial_index_raw = message.get("trial_index")
            self.signals.log_message.emit(
                f"[{path_name}] updated current trial selection to index={trial_index_raw}"
            )
            try:
                self._handle_start_trial_request(
                    request_path_name=path_name,
                    message=message,
                    reply_address=address,
                )
            except Exception as exc:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "start_trial",
                        "status": "error",
                        "error": str(exc),
                    },
                )
            return

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
            seq_nums_raw = message.get("seq_nums")
            self.signals.log_message.emit(
                f"[{path_name}] requested photostim prep for schema='{schema_name}' expID='{exp_id}'"
            )
            if isinstance(seq_nums_raw, list):
                seq_nums = seq_nums_raw
            elif seq_nums_raw is None:
                seq_nums = []
            else:
                seq_nums = [seq_nums_raw]
            if not schema_name or not exp_id or not seq_nums:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "prep_patterns",
                        "status": "error",
                        "schema_name": schema_name,
                        "expID": exp_id,
                        "seq_nums": seq_nums_raw,
                        "error": "prep_patterns requires schema_name, expID, and seq_nums",
                    },
                )
                return

            try:
                seq_num = int(seq_nums[0])
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
                        "seq_nums": seq_nums_raw,
                        "error": str(exc),
                    },
                )
            return

        if action == "trigger_photo_stim":
            schema_name = str(message.get("schema_name", "")).strip()
            exp_id = str(message.get("expID", "")).strip()
            seq_num_raw = message.get("seq_num")
            self.signals.log_message.emit(
                f"[{path_name}] requested photostim trigger for schema='{schema_name}' expID='{exp_id}' seq_num={seq_num_raw}"
            )
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
        if action == "check_idle":
            self.signals.log_message.emit(f"[{path_name}] requested photostim idle check")
            try:
                self._handle_check_idle_request(
                    request_path_name=path_name,
                    reply_address=address,
                )
            except Exception as exc:
                self._send_json_reply(
                    path_name,
                    address,
                    {
                        "action": "check_idle",
                        "status": "error",
                        "error": str(exc),
                    },
                )
            return
        if action == "abort_photo_stim":
            self.signals.log_message.emit(f"[{path_name}] requested photostim abort")
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

    def _handle_update_experiment_params_request(
        self,
        request_path_name: str,
        message: dict[str, object],
        reply_address: tuple[str, int],
    ) -> None:
        runtime = self._runtimes[request_path_name]
        tracking = runtime.experiment_tracking
        exp_id = str(message.get("expID", "")).strip()
        schema_name = str(message.get("schema_name", "")).strip()
        conditions_raw = message.get("stimulus_conditions")
        if conditions_raw is None:
            raise ValueError("update_experiment_params requires stimulus_conditions")
        if not isinstance(conditions_raw, list):
            raise ValueError("update_experiment_params requires stimulus_conditions to be a list")
        stimulus_conditions: list[dict[str, object]] = []
        for idx, item in enumerate(conditions_raw):
            if not isinstance(item, dict):
                raise ValueError(f"stimulus_conditions[{idx}] must be an object")
            stimulus_conditions.append(dict(item))

        tracking.reset()
        tracking.exp_id = exp_id
        tracking.schema_name = schema_name
        tracking.params = {k: v for k, v in message.items() if k != "action"}
        tracking.stimulus_conditions = stimulus_conditions

        self._send_json_reply(
            request_path_name,
            reply_address,
            {
                "action": "update_experiment_params",
                "status": "ready",
                "expID": exp_id,
                "schema_name": schema_name,
                "stimulus_condition_count": len(stimulus_conditions),
            },
        )

    def _handle_start_trial_request(
        self,
        request_path_name: str,
        message: dict[str, object],
        reply_address: tuple[str, int],
    ) -> None:
        runtime = self._runtimes[request_path_name]
        tracking = runtime.experiment_tracking
        if not tracking.params:
            raise ValueError("start_trial requires prior update_experiment_params")
        trial_index_raw = message.get("trial_index")
        if trial_index_raw is None:
            raise ValueError("start_trial requires trial_index")
        trial_index = int(trial_index_raw)
        if trial_index < 0 or trial_index >= len(tracking.stimulus_conditions):
            raise IndexError(
                f"trial_index {trial_index} is out of range for {len(tracking.stimulus_conditions)} stimulus condition(s)"
            )
        tracking.current_trial_index = trial_index
        tracking.current_stimulus_condition = dict(tracking.stimulus_conditions[trial_index])
        selected_stimulus_id = tracking.current_stimulus_condition.get("stimulus_id")
        self.signals.log_message.emit(
            f"[{request_path_name}] current trial set to index={trial_index}"
            + (f" stimulus_id={selected_stimulus_id}" if selected_stimulus_id is not None else "")
        )

        self._send_json_reply(
            request_path_name,
            reply_address,
            {
                "action": "start_trial",
                "status": "ready",
                "expID": tracking.exp_id,
                "schema_name": tracking.schema_name,
                "trial_index": trial_index,
                "stimulus_condition": tracking.current_stimulus_condition,
            },
        )

    def _send_json_reply(self, path_name: str, address: tuple[str, int], payload: dict[str, object]) -> None:
        runtime = self._runtimes.get(path_name)
        if runtime is None or runtime.udp_listener is None:
            self.signals.log_message.emit(f"[{path_name}] could not send JSON reply; listener is not running")
            return
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        runtime.udp_listener.send(encoded, address)
        action = str(payload.get("action", "")).strip() or "unknown"
        status = str(payload.get("status", "")).strip() or "unknown"
        line = f"[{path_name} udp {address[0]}:{address[1]}] sent action={action} status={status}"
        self.signals.path_udp_log.emit(path_name, line)
        self.signals.log_message.emit(f"[{path_name}] sent UDP reply for '{action}' with status '{status}'")

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
                prep_state_local.sequence_to_stimulus_group = {}
                prep_state_local.sequence_to_stimulus_groups = {}
                next_group_num = 3
                photostim_block_duration_s = self._runtimes[photostim_path].path_config.sequence_block_duration_s
                for sequence_name in prepared_sequence_names:
                    sequence = project.sequences[sequence_name]
                    sequence_end_s = 0.0
                    for step in sequence.steps:
                        pattern = project.patterns[step.pattern]
                        sequence_end_s = max(sequence_end_s, float(step.start_s) + float(pattern.duration_s))
                    block_group_count = max(1, int(math.ceil(sequence_end_s / photostim_block_duration_s)))
                    step_groups = list(range(next_group_num, next_group_num + block_group_count))
                    prep_state_local.sequence_to_stimulus_groups[sequence_name] = step_groups
                    prep_state_local.sequence_to_stimulus_group[sequence_name] = step_groups[0]
                    next_group_num += block_group_count
                payload["stimulus_groups"] = [
                    {
                        "stimulus_group_nums": prep_state_local.sequence_to_stimulus_groups[sequence_name],
                        "sequence_name": sequence_name,
                        "seq_num": index,
                    }
                    for index, sequence_name in enumerate(prepared_sequence_names)
                ]
                self.signals.log_message.emit(f"[{photostim_path}] prepared sequence block stimulus groups for {len(prepared_sequence_names)} sequence(s)")
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
        if not prep_state.sequence_to_stimulus_group:
            raise ValueError("No prepared stimulus group mapping is available. Run prep_patterns first.")

        sequence_name, stimulus_group_nums, trigger_times_s, stimulus_pattern_numbers = self._resolve_trigger_groups(
            project,
            seq_num,
            prep_state.sequence_to_stimulus_groups,
        )

        def worker() -> None:
            ok = self._run_action(
                photostim_path,
                "json trigger_photo_stim" if reply_address is not None else "gui trigger_photo_stim",
                lambda name: self._trigger_photo_stim_checked(
                    name,
                    stimulus_group_nums,
                    trigger_times_s,
                    stimulus_pattern_numbers,
                    sequence_name,
                    request_path_name=request_path_name if reply_address is not None else None,
                    reply_address=reply_address,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                ),
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
                "stimulus_group_count": len(stimulus_group_nums),
            }
            if ok:
                insert_position = prep_state_local.triggered_insert_position
                idle_position = prep_state_local.triggered_idle_position
                prep_state_local.triggered_seq_num = seq_num
                prep_state_local.triggered_sequence_name = sequence_name
                prep_state_local.triggered_stimulus_groups = list(stimulus_group_nums)
                self.signals.log_message.emit(
                    f"[{photostim_path}] triggered sequence '{sequence_name}' via {len(stimulus_group_nums)} stimulus groups "
                    f"(insert_position={insert_position}, idle_position={idle_position})"
                )
            else:
                payload["error"] = "trigger_photo_stim failed"

            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit(f"[config] gui trigger_photo_stim result={payload}")

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

    def _handle_check_idle_request(
        self,
        request_path_name: str,
        reply_address: tuple[str, int] | None,
    ) -> None:
        photostim_path = self.machine_config.photostim_path if self.machine_config is not None else None
        if not photostim_path:
            payload = {
                "action": "check_idle",
                "status": "error",
                "error": "No photostim path configured",
            }
            if reply_address is not None:
                self._send_json_reply(request_path_name, reply_address, payload)
            else:
                self.signals.log_message.emit("[config] check_idle skipped: no photostim path configured")
            return

        def worker() -> None:
            ok = self._run_action(
                photostim_path,
                "json check_idle" if reply_address is not None else "gui check_idle",
                lambda name: self._check_photostim_idle(name, request_path_name, reply_address),
            )
            if not ok and reply_address is None:
                self.signals.log_message.emit("[config] gui check_idle failed")

        threading.Thread(target=worker, daemon=True).start()

    def _resolve_trigger_groups(
        self,
        project,
        seq_num: int,
        sequence_to_stimulus_groups: dict[str, list[int]],
    ) -> tuple[str, list[int], list[float], list[int]]:
        sequence_names = list(project.sequences.keys())
        if not sequence_names:
            raise ValueError("Schema does not contain any sequences")
        if seq_num < 0 or seq_num >= len(sequence_names):
            raise IndexError(f"seq_num {seq_num} is out of range for {len(sequence_names)} sequence(s)")

        sequence_name = sequence_names[seq_num]
        if sequence_name not in sequence_to_stimulus_groups:
            raise ValueError(f"Sequence '{sequence_name}' has not been prepared yet.")
        sequence = project.sequences[sequence_name]
        stimulus_group_nums = list(sequence_to_stimulus_groups[sequence_name])
        photostim_path = self.machine_config.photostim_path if self.machine_config is not None else None
        if not photostim_path:
            raise ValueError("No photostim path configured")
        block_duration_s = self._runtimes[photostim_path].path_config.sequence_block_duration_s
        if block_duration_s <= 0:
            raise ValueError("Configured sequence block duration must be positive.")

        planned_group_nums: list[int] = []
        trigger_times_s: list[float] = []
        stimulus_pattern_numbers: list[int] = []
        schema_pattern_names = list(project.patterns.keys())
        sequence_end_s = 0.0
        for step in sequence.steps:
            pattern = project.patterns[step.pattern]
            sequence_end_s = max(sequence_end_s, float(step.start_s) + float(pattern.duration_s))
        expected_block_count = max(1, int(math.ceil(sequence_end_s / block_duration_s)))
        if expected_block_count != len(stimulus_group_nums):
            raise ValueError(
                f"Prepared block mapping for sequence '{sequence_name}' contains {len(stimulus_group_nums)} groups but {expected_block_count} are required."
            )

        for block_idx, stimulus_group_num in enumerate(stimulus_group_nums):
            block_start_s = block_idx * block_duration_s
            block_end_s = block_start_s + block_duration_s
            planned_group_nums.append(stimulus_group_num)

            representative_pattern_num = 0
            for step in sequence.steps:
                pattern = project.patterns[step.pattern]
                step_start_s = float(step.start_s)
                step_end_s = step_start_s + float(pattern.duration_s)
                if min(block_end_s, step_end_s) > max(block_start_s, step_start_s):
                    representative_pattern_num = schema_pattern_names.index(step.pattern) + 1
                    break
            stimulus_pattern_numbers.append(representative_pattern_num)

        planned_group_nums.append(2)
        planned_group_nums.append(2)
        trigger_times_s = [block_duration_s * idx for idx in range(len(planned_group_nums))]
        return sequence_name, planned_group_nums, trigger_times_s, stimulus_pattern_numbers

    def _apply_trigger_sequence(self, path_name: str, stimulus_group_nums: list[int]) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_trigger_photostim_command(
                    runtime.path_config,
                    stimulus_group_nums,
                ),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.status = "photostim triggered"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
            return lines

    def _extract_marker_int(self, lines: list[str], marker_name: str) -> int | None:
        marker = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line == marker_name:
                marker = marker_name
                continue
            if marker == marker_name:
                try:
                    return int(float(line))
                except ValueError:
                    return None
        return None

    def _abort_photo_stim(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_abort_photostim_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            runtime.prepared_photostim.triggered_seq_num = None
            runtime.prepared_photostim.triggered_sequence_name = ""
            runtime.prepared_photostim.triggered_stimulus_groups = []
            runtime.prepared_photostim.triggered_insert_position = None
            runtime.prepared_photostim.triggered_idle_position = None
            runtime.prepared_photostim.remaining_expected_triggers = None
            runtime.prepared_photostim.ready_sequence_position = None
            runtime.prepared_photostim.ready_completed_sequences = None
            runtime.status = "photostim aborted"
            self.signals.path_status.emit(path_name, runtime.status)
            self._emit_lines(path_name, lines)
        self._cancel_software_trigger(path_name)
        self._cancel_waveform_monitor(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_stop_trial_waveform_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
        self._emit_lines(path_name, lines)

    def _query_photostim_sequence_state(self, path_name: str) -> tuple[bool, int | None, list[int], int | None, str]:
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
        status_text = ""
        marker = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line in {
                "PHOTOSTIM_ACTIVE",
                "PHOTOSTIM_SEQUENCE_POSITION",
                "PHOTOSTIM_COMPLETED_SEQUENCES",
                "PHOTOSTIM_SEQUENCE_SELECTED",
                "PHOTOSTIM_STATUS_TEXT",
                "PHOTOSTIM_STATUS_READY",
            }:
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
            elif marker == "PHOTOSTIM_STATUS_TEXT":
                status_text = line
        return active, position, sequence, completed_sequences, status_text

    def _check_photostim_idle(
        self,
        path_name: str,
        request_path_name: str | None = None,
        reply_address: tuple[str, int] | None = None,
    ) -> None:
        runtime = self._ensure_session(path_name)
        prep_state = runtime.prepared_photostim
        active, position, sequence, completed_sequences, status_text = self._query_photostim_sequence_state(path_name)
        idle_position = prep_state.triggered_idle_position
        insert_position = prep_state.triggered_insert_position
        block_duration_s = float(runtime.path_config.sequence_block_duration_s)
        has_pending_trial = idle_position is not None and insert_position is not None
        expected_idle_after_s: float | None = None
        if not active:
            idle = True
            running = False
            reason = "photostim_inactive"
            expected_idle_after_s = 0.0
        elif not has_pending_trial:
            idle = True
            running = False
            reason = "no_pending_trial"
            expected_idle_after_s = 0.0
        elif position is None:
            idle = False
            running = True
            reason = "pending_trial_unknown_position"
        elif position >= idle_position:
            idle = True
            running = False
            reason = "terminal_idle_park_reached"
            expected_idle_after_s = 0.0
        elif position < insert_position:
            idle = False
            running = True
            reason = "waiting_for_trial_start"
        else:
            idle = False
            running = True
            reason = "trial_running_or_armed"
            expected_idle_after_s = max(0, idle_position - position) * block_duration_s

        payload: dict[str, object] = {
            "action": "check_idle",
            "status": "ready",
            "idle": idle,
            "running": running,
            "reason": reason,
            "expected_idle_after_s": expected_idle_after_s,
            "photostim_active": active,
            "sequence_position": position,
            "completed_sequences": completed_sequences,
            "photostim_status": status_text,
            "triggered_seq_num": prep_state.triggered_seq_num,
            "triggered_sequence_name": prep_state.triggered_sequence_name,
            "triggered_insert_position": insert_position,
            "triggered_idle_position": idle_position,
            "triggered_stimulus_group_count": len(prep_state.triggered_stimulus_groups),
        }
        self.signals.log_message.emit(
            f"[{path_name}] check_idle -> idle={int(idle)} running={int(running)} "
            f"position={'NaN' if position is None else position} "
            f"idle_position={'NaN' if idle_position is None else idle_position} "
            f"reason={reason} "
            f"expected_idle_after_s={'NaN' if expected_idle_after_s is None else f'{expected_idle_after_s:.3f}'}"
        )
        if reply_address is not None and request_path_name is not None:
            self._send_json_reply(request_path_name, reply_address, payload)

    def _get_waveform_test_preview(self, path_name: str) -> tuple[bool, int | None, list[int]]:
        active, position, sequence, _, _ = self._query_photostim_sequence_state(path_name)
        return active, position, sequence

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

    def _cancel_waveform_monitor(self, path_name: str) -> None:
        runtime = self._runtimes[path_name]
        if runtime.waveform_monitor_stop is not None:
            runtime.waveform_monitor_stop.set()
        runtime.waveform_monitor_stop = None
        runtime.waveform_monitor_thread = None

    def _query_trial_waveform_status(self, path_name: str) -> tuple[bool, bool]:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_trial_waveform_status_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
        active = False
        done = True
        marker = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line in {"TRIAL_WAVEFORM_TASK_ACTIVE", "TRIAL_WAVEFORM_TASK_DONE", "TRIAL_WAVEFORM_STATUS_READY"}:
                marker = line
                continue
            if marker == "TRIAL_WAVEFORM_TASK_ACTIVE":
                try:
                    active = bool(int(float(line)))
                except ValueError:
                    active = False
            elif marker == "TRIAL_WAVEFORM_TASK_DONE":
                try:
                    done = bool(int(float(line)))
                except ValueError:
                    done = True
        return active, done

    def _query_raw_vdaq_do_test_status(self, path_name: str) -> tuple[bool, bool]:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_raw_vdaq_do_test_status_command(),
                timeout_s=runtime.path_config.command_timeout_s,
            )
        active = False
        done = True
        marker = None
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue
            if line in {"RAW_VDAQ_DO_TEST_ACTIVE", "RAW_VDAQ_DO_TEST_DONE", "RAW_VDAQ_DO_TEST_STATUS_READY"}:
                marker = line
                continue
            if marker == "RAW_VDAQ_DO_TEST_ACTIVE":
                try:
                    active = bool(int(float(line)))
                except ValueError:
                    active = False
            elif marker == "RAW_VDAQ_DO_TEST_DONE":
                try:
                    done = bool(int(float(line)))
                except ValueError:
                    done = True
        return active, done

    def _prepare_trial_waveform(self, path_name: str, trigger_times_s: list[float], external_start: bool) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_prepare_trial_waveform_command(runtime.path_config, trigger_times_s, external_start),
                timeout_s=runtime.path_config.command_timeout_s,
            )
            if external_start:
                lines.extend(
                    runtime.session.eval(
                        build_arm_trial_waveform_command(runtime.path_config),
                        timeout_s=runtime.path_config.command_timeout_s,
                    )
                )
        self._emit_lines(path_name, lines)

    def _start_trial_waveform(self, path_name: str) -> None:
        runtime = self._ensure_session(path_name)
        with runtime.lock:
            assert runtime.session is not None
            lines = runtime.session.eval(
                build_start_trial_waveform_command(runtime.path_config),
                timeout_s=runtime.path_config.command_timeout_s,
            )
        self._emit_lines(path_name, lines)

    def _start_waveform_software_playback(
        self,
        path_name: str,
        sequence_name: str,
        request_path_name: str | None = None,
        reply_address: tuple[str, int] | None = None,
        schema_name: str | None = None,
        exp_id: str | None = None,
        seq_num: int | None = None,
    ) -> None:
        self._cancel_waveform_monitor(path_name)
        runtime = self._runtimes[path_name]
        stop_event = threading.Event()
        runtime.waveform_monitor_stop = stop_event

        def worker() -> None:
            try:
                self._start_trial_waveform(path_name)
                self._monitor_waveform_completion(
                    path_name,
                    sequence_name,
                    stop_event,
                    request_path_name=request_path_name,
                    reply_address=reply_address,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                    wait_for_start=False,
                )
            finally:
                runtime.waveform_monitor_stop = None
                runtime.waveform_monitor_thread = None

        thread = threading.Thread(target=worker, daemon=True)
        runtime.waveform_monitor_thread = thread
        thread.start()

    def _start_waveform_external_monitor(
        self,
        path_name: str,
        sequence_name: str,
        request_path_name: str | None = None,
        reply_address: tuple[str, int] | None = None,
        schema_name: str | None = None,
        exp_id: str | None = None,
        seq_num: int | None = None,
    ) -> None:
        self._cancel_waveform_monitor(path_name)
        runtime = self._runtimes[path_name]
        stop_event = threading.Event()
        runtime.waveform_monitor_stop = stop_event

        def worker() -> None:
            try:
                self._monitor_waveform_completion(
                    path_name,
                    sequence_name,
                    stop_event,
                    request_path_name=request_path_name,
                    reply_address=reply_address,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                    wait_for_start=True,
                )
            finally:
                runtime.waveform_monitor_stop = None
                runtime.waveform_monitor_thread = None

        thread = threading.Thread(target=worker, daemon=True)
        runtime.waveform_monitor_thread = thread
        thread.start()

    def _start_test_waveform_external_monitor(
        self,
        path_name: str,
        position_before: int | None,
        completed_before: int | None,
        expected_duration_s: float,
    ) -> None:
        runtime = self._runtimes[path_name]
        stop_event = threading.Event()

        def worker() -> None:
            start_deadline = time.monotonic() + 30.0
            waveform_started = False
            while not stop_event.is_set() and time.monotonic() < start_deadline:
                active, done = self._query_raw_vdaq_do_test_status(path_name)
                if active or not done:
                    waveform_started = True
                    self.signals.log_message.emit(f"[{path_name}] External waveform start detected")
                    break
                time.sleep(0.02)
            if not waveform_started:
                self.signals.log_message.emit(
                    f"[{path_name}] ERROR: External waveform start was not detected on {runtime.path_config.trial_waveform_start_trigger_port}"
                )
                self.signals.waveform_test_result.emit(
                    path_name,
                    0 if position_before is None else position_before,
                    0 if position_before is None else position_before,
                    0,
                )
                return

            finish_wait = time.monotonic() + max(2.0, expected_duration_s + 2.0)
            while not stop_event.is_set() and time.monotonic() < finish_wait:
                time.sleep(0.05)

            active_after, position_after, _, completed_after, _ = self._query_photostim_sequence_state(path_name)
            delivered_count = self._sequence_position_delta(position_before, position_after)
            waveform_advanced = False
            if position_before is not None and position_after is not None and position_after > position_before:
                waveform_advanced = True
            if (
                not waveform_advanced
                and completed_before is not None
                and completed_after is not None
                and completed_after > completed_before
            ):
                waveform_advanced = True
            self.signals.log_message.emit(f"[{path_name}] Photostim active after external waveform test: {int(active_after)}")
            self.signals.log_message.emit(
                f"[{path_name}] Photostim sequence position after external waveform test: "
                + ("NaN" if position_after is None else str(position_after))
            )
            self.signals.log_message.emit(
                f"[{path_name}] Photostim completed sequences after external waveform test: "
                + ("NaN" if completed_after is None else str(completed_after))
            )
            self.signals.log_message.emit(
                f"[{path_name}] Photostim sequence position summary: "
                + ("NaN" if position_before is None else str(position_before))
                + " -> "
                + ("NaN" if position_after is None else str(position_after))
            )
            self.signals.log_message.emit(f"[{path_name}] Photostim sequence position delta: {delivered_count}")
            self.signals.log_message.emit(f"[{path_name}] Waveform external-start advanced photostim: {int(waveform_advanced)}")
            self.signals.log_message.emit(f"[{path_name}] Waveform external-start advanced photostim count: {delivered_count}")
            self.signals.waveform_test_result.emit(
                path_name,
                0 if position_before is None else position_before,
                0 if position_after is None else position_after,
                delivered_count,
            )

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

    def _monitor_waveform_completion(
        self,
        path_name: str,
        sequence_name: str,
        stop_event: threading.Event,
        request_path_name: str | None = None,
        reply_address: tuple[str, int] | None = None,
        schema_name: str | None = None,
        exp_id: str | None = None,
        seq_num: int | None = None,
        wait_for_start: bool = False,
    ) -> None:
        runtime = self._runtimes[path_name]
        prep_state = runtime.prepared_photostim
        started = not wait_for_start
        start_deadline = time.monotonic() + 60.0
        if wait_for_start:
            while not stop_event.is_set() and time.monotonic() < start_deadline:
                active, done = self._query_trial_waveform_status(path_name)
                if active or not done:
                    started = True
                    break
                time.sleep(0.02)
            if not started:
                self.signals.log_message.emit(
                    f"[{path_name}] ERROR: waveform external start was not detected for sequence '{sequence_name}'"
                )
                return

        finish_wait_s = max(2.0, float(prep_state.waveform_expected_done_time_s or 0.0) + 2.0)
        finish_deadline = time.monotonic() + finish_wait_s
        while not stop_event.is_set() and time.monotonic() < finish_deadline:
            time.sleep(0.05)

        mismatch_message = self._finalize_pending_photostim_check(path_name, "Waveform trigger count check")
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

    def _wait_for_leading_park_advance(
        self,
        path_name: str,
        baseline_position: int | None,
        baseline_completed_sequences: int | None,
        timeout_s: float = 20.0,
    ) -> tuple[int | None, int | None]:
        deadline = time.monotonic() + timeout_s
        last_active = False
        last_position: int | None = None
        last_completed: int | None = None
        last_sequence: list[int] = []
        while time.monotonic() < deadline:
            active, current_position, sequence, completed_sequences, _ = self._query_photostim_sequence_state(path_name)
            last_active = active
            last_position = current_position
            last_completed = completed_sequences
            last_sequence = sequence
            if baseline_completed_sequences is not None and completed_sequences is not None:
                if completed_sequences > baseline_completed_sequences:
                    self.signals.log_message.emit(
                        f"[{path_name}] Leading park advance detected: "
                        f"{self._format_photostim_state(active, current_position, completed_sequences, sequence)}"
                    )
                    return current_position, completed_sequences
            if baseline_position is not None and current_position is not None and current_position != baseline_position:
                self.signals.log_message.emit(
                    f"[{path_name}] Leading park advance detected: "
                    f"{self._format_photostim_state(active, current_position, completed_sequences, sequence)}"
                )
                return current_position, completed_sequences
            time.sleep(0.02)
        raise RuntimeError(
            "Leading park did not advance photostim sequence before ready. "
            + self._format_photostim_state(last_active, last_position, last_completed, last_sequence)
        )

    def _wait_for_expected_photostim_completion(
        self,
        path_name: str,
        ready_position: int | None,
        ready_completed_sequences: int | None,
        expected_remaining: int,
        timeout_s: float = 2.0,
    ) -> tuple[bool, int | None, int | None, int]:
        deadline = time.monotonic() + timeout_s
        last_active = False
        last_position: int | None = None
        last_completed: int | None = None
        max_delivered = 0
        while time.monotonic() < deadline:
            active, current_position, _, completed_sequences, _ = self._query_photostim_sequence_state(path_name)
            last_active = active
            last_position = current_position
            last_completed = completed_sequences
            if ready_position is not None and current_position is not None:
                delivered = max(0, current_position - ready_position)
                if delivered > max_delivered:
                    max_delivered = delivered
            if (
                ready_completed_sequences is not None
                and completed_sequences is not None
                and completed_sequences > ready_completed_sequences
                and not active
            ):
                return active, current_position, completed_sequences, expected_remaining
            if max_delivered >= expected_remaining:
                return active, current_position, completed_sequences, max_delivered
            time.sleep(0.02)
        return last_active, last_position, last_completed, max_delivered

    def _finalize_pending_photostim_check(self, path_name: str, label: str) -> str | None:
        runtime = self._ensure_session(path_name)
        prep_state = runtime.prepared_photostim
        expected_remaining = prep_state.remaining_expected_triggers
        ready_position = prep_state.ready_sequence_position
        ready_completed = prep_state.ready_completed_sequences
        if expected_remaining is None or ready_position is None:
            return None
        timeout_s = 2.0
        if prep_state.waveform_expected_done_time_s is not None:
            timeout_s = max(timeout_s, float(prep_state.waveform_expected_done_time_s) + 1.0)
        active, current_position, completed_sequences, max_delivered = self._wait_for_expected_photostim_completion(
            path_name,
            ready_position,
            ready_completed,
            expected_remaining,
            timeout_s=timeout_s,
        )
        if (
            ready_completed is not None
            and completed_sequences is not None
            and completed_sequences > ready_completed
            and not active
        ):
            delivered_triggers = expected_remaining
        else:
            delivered_triggers = min(max_delivered, expected_remaining)
        if self._debug_category_enabled.get("software_trigger_count", True):
            self.signals.log_message.emit(
                f"[{path_name}] {label}: {delivered_triggers} stimuli delivered, {expected_remaining} expected"
            )
        prep_state.remaining_expected_triggers = None
        prep_state.ready_sequence_position = None
        prep_state.ready_completed_sequences = None
        prep_state.waveform_expected_done_time_s = None
        if delivered_triggers != expected_remaining:
            return f"{delivered_triggers} stimuli delivered, {expected_remaining} expected"
        return None

    def _trigger_photo_stim_checked(
        self,
        path_name: str,
        stimulus_group_nums: list[int],
        trigger_times_s: list[float],
        stimulus_pattern_numbers: list[int],
        sequence_name: str,
        request_path_name: str | None = None,
        reply_address: tuple[str, int] | None = None,
        schema_name: str | None = None,
        exp_id: str | None = None,
        seq_num: int | None = None,
    ) -> None:
        if not stimulus_group_nums:
            raise ValueError("No prepared stimulus groups are available for this sequence.")
        runtime = self._ensure_session(path_name)
        prep_state = runtime.prepared_photostim
        self._cancel_software_trigger(path_name)
        self._cancel_waveform_monitor(path_name)
        if len(trigger_times_s) != len(stimulus_group_nums):
            raise ValueError("Trigger timing does not match the planned triggered stimulus sequence.")
        trigger_lines = self._apply_trigger_sequence(path_name, stimulus_group_nums)
        prep_state.triggered_insert_position = self._extract_marker_int(trigger_lines, "TRIGGER_PHOTOSTIM_INSERT_POSITION")
        prep_state.triggered_idle_position = self._extract_marker_int(trigger_lines, "TRIGGER_PHOTOSTIM_IDLE_POSITION")

        prep_state.remaining_expected_triggers = None
        prep_state.ready_sequence_position = None
        prep_state.ready_completed_sequences = None
        prep_state.waveform_expected_done_time_s = None

        baseline_active, baseline_position, _, baseline_completed, _ = self._query_photostim_sequence_state(path_name)
        if not baseline_active:
            raise RuntimeError("Photostim is not active after programming the trigger sequence.")

        software_mode = self._current_trigger_mode() != "hardware"
        if software_mode:
            self.signals.log_message.emit(
                f"[{path_name}] Leading park baseline: "
                + self._format_photostim_state(baseline_active, baseline_position, baseline_completed, stimulus_group_nums)
            )
            if len(trigger_times_s) > 1:
                remaining_trigger_times_s = [max(0.0, t) for t in trigger_times_s[1:]]
                prep_state.waveform_expected_done_time_s = remaining_trigger_times_s[-1]
                self._prepare_trial_waveform(path_name, remaining_trigger_times_s, external_start=False)
            else:
                remaining_trigger_times_s = []
            self._fire_software_trigger(path_name)
            ready_position, ready_completed = self._wait_for_leading_park_advance(
                path_name,
                baseline_position,
                baseline_completed,
                timeout_s=max(5.0, 4.0 * self._runtimes[path_name].path_config.sequence_block_duration_s),
            )
            prep_state.ready_sequence_position = ready_position
            prep_state.ready_completed_sequences = ready_completed
            prep_state.remaining_expected_triggers = max(0, len(trigger_times_s) - 1)
            if remaining_trigger_times_s:
                self._start_waveform_software_playback(
                    path_name,
                    sequence_name,
                    request_path_name=request_path_name,
                    reply_address=reply_address,
                    schema_name=schema_name,
                    exp_id=exp_id,
                    seq_num=seq_num,
                )
            self.signals.log_message.emit(
                f"[{path_name}] leading park software-triggered before ready"
            )
        else:
            prep_state.ready_sequence_position = baseline_position
            prep_state.ready_completed_sequences = baseline_completed
            prep_state.remaining_expected_triggers = len(trigger_times_s)
            prep_state.waveform_expected_done_time_s = trigger_times_s[-1] if trigger_times_s else 0.0
            self._prepare_trial_waveform(path_name, trigger_times_s, external_start=True)
            self._start_waveform_external_monitor(
                path_name,
                sequence_name,
                request_path_name=request_path_name,
                reply_address=reply_address,
                schema_name=schema_name,
                exp_id=exp_id,
                seq_num=seq_num,
            )
            self.signals.log_message.emit(
                f"[{path_name}] sequence armed and waiting for external trigger train"
            )

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
        udp_line = f"[{path_name} udp {address[0]}:{address[1]}] received legacy command={command or 'UNKNOWN'}"
        self.signals.path_udp_log.emit(path_name, udp_line)
        self.signals.log_message.emit(f"[{path_name}] received legacy UDP command '{command or 'UNKNOWN'}'")
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
            ready_line = f"[{path_name} udp {reply_address[0]}:{reply_address[1]}] sent legacy READY"
            self.signals.path_udp_log.emit(path_name, ready_line)
            self.signals.log_message.emit(f"[{path_name}] sent legacy READY reply")

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
