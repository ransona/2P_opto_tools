from __future__ import annotations

import hashlib
import configparser
import json
import socket
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import yaml

from PyQt6.QtCore import QObject, Qt, QRectF, QSize, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .io import load_schema, save_schema
from .models import SCHEMA_TIME_QUANTUM_S, CellSpec, ExperimentProject, Pattern, Sequence, SequenceStep
from .matlab_bridge import autodetect_machine_name, load_machine_ui_config
from .scanimage_control import ScanImageControlWidget


@dataclass
class GuiControlConfig:
    enabled: bool
    host: str
    port: int


def _stable_color(key: str) -> QColor:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    hue = int(digest[:4], 16) % 360
    color = QColor()
    color.setHsv(hue, 150, 210)
    return color


def _unique_name(base: str, existing: dict[str, object]) -> str:
    if base not in existing:
        return base
    suffix = 2
    candidate = f"{base}_{suffix}"
    while candidate in existing:
        suffix += 1
        candidate = f"{base}_{suffix}"
    return candidate


def _selected_or_last_row(table: QTableWidget) -> int | None:
    rows = sorted({index.row() for index in table.selectedIndexes()})
    if rows:
        return rows[-1]
    if table.rowCount():
        return table.rowCount() - 1
    return None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_path() -> Path:
    return _repo_root() / "config.ini"


def _load_save_root() -> Path:
    config = configparser.ConfigParser()
    config.read(_config_path())
    raw_root = config.get("paths", "save_root", fallback="./data")
    return _resolve_config_path(raw_root)


def _resolve_config_path(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (_repo_root() / path).resolve()
    return path


def _load_schema_root() -> Path:
    config = configparser.ConfigParser()
    config.read(_config_path())
    raw_root = config.get("paths", "schema_root", fallback=None)
    if not raw_root:
        return _load_save_root()
    return _resolve_config_path(raw_root)


def _load_gui_control_config() -> GuiControlConfig:
    config = configparser.ConfigParser()
    config.read(_config_path())
    section = config["gui_control"] if config.has_section("gui_control") else None
    if section is None:
        return GuiControlConfig(enabled=True, host="0.0.0.0", port=1816)
    enabled = section.getboolean("enabled", fallback=True)
    host = section.get("host", fallback="0.0.0.0")
    port = section.getint("port", fallback=1816)
    return GuiControlConfig(enabled=enabled, host=host, port=port)


class GuiControlSignals(QObject):
    udp_message = pyqtSignal(bytes, tuple)


class GuiControlListener(threading.Thread):
    def __init__(self, host: str, port: int, signals: GuiControlSignals):
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
            while not self._stop_event.is_set():
                try:
                    payload, address = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                self.signals.udp_message.emit(payload, address)
        finally:
            try:
                sock.close()
            except OSError:
                pass

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


class TimelinePreview(QWidget):
    def __init__(self, project: ExperimentProject, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.sequence_name = ""
        self.setMinimumHeight(220)

    def set_sequence(self, name: str) -> None:
        self.sequence_name = name
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(700, 240)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#111827"))

        if self.sequence_name not in self.project.sequences:
            painter.setPen(QColor("#e5e7eb"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Select a sequence to preview it")
            return

        sequence = self.project.sequences[self.sequence_name]
        if not sequence.steps:
            painter.setPen(QColor("#e5e7eb"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Sequence is empty")
            return

        steps = sorted(sequence.steps, key=lambda step: step.start_s)
        intervals = []
        max_end = 0.0
        for step in steps:
            pattern = self.project.patterns.get(step.pattern)
            if not pattern:
                continue
            end_s = step.start_s + pattern.duration_s
            intervals.append((step.start_s, end_s, step.pattern))
            max_end = max(max_end, end_s)

        if max_end <= 0:
            return

        left = 80
        right = 24
        top = 40
        row_h = 34
        width = max(1, self.width() - left - right)

        painter.setPen(QColor("#cbd5e1"))
        painter.drawText(16, 22, f"Sequence: {self.sequence_name}")
        painter.setPen(QPen(QColor("#475569"), 1))
        painter.drawLine(left, top - 10, left + width, top - 10)

        for idx, (start_s, end_s, pattern_name) in enumerate(intervals):
            y = top + idx * row_h
            x = left + int((start_s / max_end) * width)
            w = max(4, int(((end_s - start_s) / max_end) * width))
            color = _stable_color(pattern_name)
            painter.setPen(QPen(color.darker(130), 1))
            painter.setBrush(color)
            painter.drawRoundedRect(QRectF(x, y, w, 22), 5, 5)
            painter.setPen(QColor("#0f172a"))
            painter.drawText(x + 8, y + 15, f"{pattern_name}  {start_s:.3f}s → {end_s:.3f}s")

        painter.setPen(QColor("#94a3b8"))
        painter.drawText(16, self.height() - 18, f"Total duration: {max_end:.3f}s")


class PatternEditor(QWidget):
    def __init__(self, project: ExperimentProject, on_dirty, on_commit=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.on_dirty = on_dirty
        self.on_commit = on_commit
        self.current_name = ""
        self._loading = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form_box = QGroupBox("Pattern")
        form = QFormLayout(form_box)

        self.name_edit = QLineEdit()
        self.duration_spin = QDoubleSpinBox()
        self.duration_spin.setRange(SCHEMA_TIME_QUANTUM_S, 9999.0)
        self.duration_spin.setDecimals(3)
        self.duration_spin.setSingleStep(SCHEMA_TIME_QUANTUM_S)
        self.duration_spin.setValue(1.0)

        self.freq_spin = QDoubleSpinBox()
        self.freq_spin.setRange(0.001, 999999.0)
        self.freq_spin.setDecimals(4)
        self.freq_spin.setValue(10.0)

        self.duty_cycle_spin = QDoubleSpinBox()
        self.duty_cycle_spin.setRange(0.0, 1.0)
        self.duty_cycle_spin.setDecimals(4)
        self.duty_cycle_spin.setSingleStep(0.05)
        self.duty_cycle_spin.setValue(0.2)

        self.power_spin = QDoubleSpinBox()
        self.power_spin.setRange(0.0, 100.0)
        self.power_spin.setDecimals(2)
        self.power_spin.setValue(20.0)

        self.spiral_width_spin = QDoubleSpinBox()
        self.spiral_width_spin.setRange(0.0, 9999.0)
        self.spiral_width_spin.setDecimals(4)
        self.spiral_width_spin.setValue(10.0)

        self.spiral_height_spin = QDoubleSpinBox()
        self.spiral_height_spin.setRange(0.0, 9999.0)
        self.spiral_height_spin.setDecimals(4)
        self.spiral_height_spin.setValue(10.0)

        self.notes_edit = QLineEdit()
        self.clear_btn = QPushButton("Clear")

        form.addRow("Name", self.name_edit)
        form.addRow("Duration (s)", self.duration_spin)
        form.addRow("Frequency (Hz)", self.freq_spin)
        form.addRow("Duty Cycle", self.duty_cycle_spin)
        form.addRow("Power (%)", self.power_spin)
        form.addRow("Spiral Width", self.spiral_width_spin)
        form.addRow("Spiral Height", self.spiral_height_spin)
        form.addRow("Notes", self.notes_edit)
        form.addRow("", self.clear_btn)

        layout.addWidget(form_box)

        self.cells_table = QTableWidget(0, 5)
        self.cells_table.setHorizontalHeaderLabels(["Label", "X", "Y", "Z", "Power scale"])
        self.cells_table.horizontalHeader().setStretchLastSection(True)
        self.cells_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cells_table.itemChanged.connect(self._on_form_changed)
        layout.addWidget(QLabel("Cells in this pattern"))
        layout.addWidget(self.cells_table)

        button_row = QHBoxLayout()
        self.add_row_btn = QPushButton("Add Cell")
        self.remove_row_btn = QPushButton("Remove Cell")
        self.copy_btn = QPushButton("Copy Pattern")
        button_row.addWidget(self.add_row_btn)
        button_row.addWidget(self.remove_row_btn)
        button_row.addWidget(self.copy_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.add_row_btn.clicked.connect(self.add_cell_row)
        self.remove_row_btn.clicked.connect(self.remove_selected_cell_rows)
        self.copy_btn.clicked.connect(self.copy_current_pattern)
        self.clear_btn.clicked.connect(self.clear_form)
        self.name_edit.editingFinished.connect(self._on_form_changed)
        self.duration_spin.valueChanged.connect(self._on_form_changed)
        self.freq_spin.valueChanged.connect(self._on_form_changed)
        self.duty_cycle_spin.valueChanged.connect(self._on_form_changed)
        self.power_spin.valueChanged.connect(self._on_form_changed)
        self.spiral_width_spin.valueChanged.connect(self._on_form_changed)
        self.spiral_height_spin.valueChanged.connect(self._on_form_changed)
        self.notes_edit.editingFinished.connect(self._on_form_changed)

    def add_cell_row(self, cell: CellSpec | None = None) -> None:
        row = self.cells_table.rowCount()
        self.cells_table.insertRow(row)

        values = cell or CellSpec(label=f"cell{row + 1}", x=0, y=0, z=0)
        widgets = [
            QTableWidgetItem(values.label),
            QTableWidgetItem(str(values.x)),
            QTableWidgetItem(str(values.y)),
            QTableWidgetItem(str(values.z)),
            QTableWidgetItem(str(values.power_scale)),
        ]
        for col, widget in enumerate(widgets):
            self.cells_table.setItem(row, col, widget)
        self.cells_table.selectRow(row)
        if not self._loading:
            self._on_form_changed()

    def remove_selected_cell_rows(self) -> None:
        row = _selected_or_last_row(self.cells_table)
        if row is not None:
            self.cells_table.removeRow(row)
            if not self._loading:
                self._on_form_changed()

    def _on_form_changed(self, *_args) -> None:
        if self._loading:
            return
        self._highlight_duplicate_coordinates()
        self.commit_current_pattern(silent=True)
        self.on_dirty()

    def _highlight_duplicate_coordinates(self) -> None:
        coords_to_rows: dict[tuple[float, float, float], list[int]] = {}
        for row in range(self.cells_table.rowCount()):
            items = [self.cells_table.item(row, col) for col in range(1, 4)]
            if any(item is None or not item.text().strip() for item in items):
                continue
            try:
                coord = tuple(float(item.text().strip()) for item in items)  # type: ignore[arg-type]
            except ValueError:
                continue
            coords_to_rows.setdefault(coord, []).append(row)

        duplicate_rows = {row for rows in coords_to_rows.values() if len(rows) > 1 for row in rows}
        for row in range(self.cells_table.rowCount()):
            is_duplicate = row in duplicate_rows
            for col in range(self.cells_table.columnCount()):
                item = self.cells_table.item(row, col)
                if item is None:
                    continue
                item.setBackground(QColor("#fecaca" if is_duplicate else "#ffffff"))

    def load_pattern(self, name: str) -> None:
        pattern = self.project.patterns[name]
        self.current_name = name
        self._loading = True
        self.name_edit.setText(pattern.name)
        self.duration_spin.setValue(pattern.duration_s)
        self.freq_spin.setValue(pattern.frequency_hz)
        self.duty_cycle_spin.setValue(pattern.duty_cycle)
        self.power_spin.setValue(pattern.power_percent)
        self.spiral_width_spin.setValue(pattern.spiral_width)
        self.spiral_height_spin.setValue(pattern.spiral_height)
        self.notes_edit.setText(pattern.notes)
        self.cells_table.setRowCount(0)
        for cell in pattern.cells:
            self.add_cell_row(cell)
        self._loading = False
        if self.cells_table.rowCount():
            self.cells_table.selectRow(0)
        self._highlight_duplicate_coordinates()

    def clear_form(self) -> None:
        self.current_name = ""
        self._loading = True
        self.name_edit.clear()
        self.duration_spin.setValue(1.0)
        self.freq_spin.setValue(10.0)
        self.duty_cycle_spin.setValue(0.2)
        self.power_spin.setValue(20.0)
        self.spiral_width_spin.setValue(10.0)
        self.spiral_height_spin.setValue(10.0)
        self.notes_edit.clear()
        self.cells_table.setRowCount(0)
        self._loading = False
        self.cells_table.clearSelection()
        self._highlight_duplicate_coordinates()

    def copy_current_pattern(self) -> None:
        try:
            pattern = self.gather_pattern()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid pattern", str(exc))
            return
        if not pattern.name:
            QMessageBox.warning(self, "Invalid pattern", "Pattern name cannot be empty.")
            return
        copied = Pattern(
            name=_unique_name(f"{pattern.name}_copy", self.project.patterns),
            duration_s=pattern.duration_s,
            frequency_hz=pattern.frequency_hz,
            power_percent=pattern.power_percent,
            duty_cycle=pattern.duty_cycle,
            spiral_width=pattern.spiral_width,
            spiral_height=pattern.spiral_height,
            notes=pattern.notes,
            cells=[CellSpec(**cell.as_dict()) for cell in pattern.cells],
        )
        self.project.patterns[copied.name] = copied
        if self.on_commit is not None:
            self.on_commit()

    def gather_pattern(self) -> Pattern:
        cells: list[CellSpec] = []
        for row in range(self.cells_table.rowCount()):
            def item(c: int) -> str:
                widget = self.cells_table.item(row, c)
                return widget.text().strip() if widget is not None else ""

            if not item(0) or not item(1) or not item(2) or not item(3):
                raise ValueError(f"Pattern cell row {row + 1} is incomplete.")
            cells.append(
                CellSpec(
                    label=item(0),
                    x=float(item(1)),
                    y=float(item(2)),
                    z=float(item(3)),
                    power_scale=float(item(4) or "1.0"),
                )
            )
        return Pattern(
            name=self.name_edit.text().strip(),
            duration_s=self.duration_spin.value(),
            frequency_hz=self.freq_spin.value(),
            power_percent=self.power_spin.value(),
            duty_cycle=self.duty_cycle_spin.value(),
            spiral_width=self.spiral_width_spin.value(),
            spiral_height=self.spiral_height_spin.value(),
            notes=self.notes_edit.text().strip(),
            cells=cells,
        )

    def commit_current_pattern(self, silent: bool = False) -> bool:
        try:
            pattern = self.gather_pattern()
        except ValueError as exc:
            if not silent:
                QMessageBox.warning(self, "Invalid pattern", str(exc))
            return False
        if not pattern.name:
            if not silent:
                QMessageBox.warning(self, "Invalid pattern", "Pattern name cannot be empty.")
            return False
        if self._has_duplicate_coordinates():
            if not silent:
                QMessageBox.warning(self, "Invalid pattern", "Duplicate cell coordinates are not allowed.")
            return False
        if self.current_name and self.current_name != pattern.name and self.current_name in self.project.patterns:
            self.project.patterns.pop(self.current_name)
            for sequence in self.project.sequences.values():
                for step in sequence.steps:
                    if step.pattern == self.current_name:
                        step.pattern = pattern.name
        self.project.patterns[pattern.name] = pattern
        self.current_name = pattern.name
        return True

    def save_current_pattern(self) -> bool:
        return self.commit_current_pattern(silent=False)

    def _has_duplicate_coordinates(self) -> bool:
        coords = set()
        for row in range(self.cells_table.rowCount()):
            items = [self.cells_table.item(row, col) for col in range(1, 4)]
            if any(item is None or not item.text().strip() for item in items):
                continue
            try:
                coord = tuple(float(item.text().strip()) for item in items)  # type: ignore[arg-type]
            except ValueError:
                continue
            if coord in coords:
                return True
            coords.add(coord)
        return False


class SequenceEditor(QWidget):
    def __init__(self, project: ExperimentProject, preview: TimelinePreview, on_dirty, on_commit=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.preview = preview
        self.on_dirty = on_dirty
        self.on_commit = on_commit
        self.current_name = ""
        self._loading = False
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Orientation.Horizontal)

        editor_panel = QWidget()
        editor_layout = QVBoxLayout(editor_panel)

        form_box = QGroupBox("Sequence")
        form = QFormLayout(form_box)
        self.name_edit = QLineEdit()
        self.notes_edit = QLineEdit()
        self.clear_btn = QPushButton("Clear")
        form.addRow("Name", self.name_edit)
        form.addRow("Notes", self.notes_edit)
        form.addRow("", self.clear_btn)
        editor_layout.addWidget(form_box)

        self.steps_table = QTableWidget(0, 4)
        self.steps_table.setHorizontalHeaderLabels(["Pattern", "Start (s)", "End (s)", "Duration (s)"])
        self.steps_table.horizontalHeader().setStretchLastSection(True)
        self.steps_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        editor_layout.addWidget(QLabel("Sequence steps"))
        editor_layout.addWidget(self.steps_table)

        button_row = QHBoxLayout()
        self.pattern_select = QComboBox()
        self.start_spin = QDoubleSpinBox()
        self.start_spin.setRange(0.0, 999999.0)
        self.start_spin.setDecimals(3)
        self.start_spin.setSingleStep(SCHEMA_TIME_QUANTUM_S)
        self.start_spin.setValue(0.0)
        self.add_step_btn = QPushButton("Add Step")
        self.remove_step_btn = QPushButton("Remove Step")
        self.copy_btn = QPushButton("Copy Sequence")
        self.save_btn = QPushButton("Save Sequence")
        button_row.addWidget(QLabel("Pattern"))
        button_row.addWidget(self.pattern_select, 2)
        button_row.addWidget(QLabel("Start"))
        button_row.addWidget(self.start_spin)
        button_row.addWidget(self.add_step_btn)
        button_row.addWidget(self.remove_step_btn)
        button_row.addWidget(self.copy_btn)
        button_row.addStretch(1)
        button_row.addWidget(self.save_btn)
        editor_layout.addLayout(button_row)

        self.add_step_btn.clicked.connect(self.add_step)
        self.remove_step_btn.clicked.connect(self.remove_selected_step_rows)
        self.copy_btn.clicked.connect(self.copy_current_sequence)
        self.clear_btn.clicked.connect(self.clear_form)
        self.name_edit.editingFinished.connect(self._on_form_changed)
        self.notes_edit.editingFinished.connect(self._on_form_changed)
        self.start_spin.valueChanged.connect(self._on_form_changed)
        self.steps_table.itemChanged.connect(self._on_form_changed)
        self.save_btn.clicked.connect(self.save_current_sequence)

        splitter.addWidget(editor_panel)
        splitter.addWidget(self.preview)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        layout.addWidget(splitter)

    def refresh_pattern_choices(self) -> None:
        current = self.pattern_select.currentText()
        self.pattern_select.clear()
        self.pattern_select.addItems(list(self.project.patterns.keys()))
        if current:
            index = self.pattern_select.findText(current)
            if index >= 0:
                self.pattern_select.setCurrentIndex(index)

    def load_sequence(self, name: str) -> None:
        sequence = self.project.sequences[name]
        self.current_name = name
        self._loading = True
        self.name_edit.setText(sequence.name)
        self.notes_edit.setText(sequence.notes)
        self.steps_table.setRowCount(0)
        for step in sorted(sequence.steps, key=lambda step: step.start_s):
            self._insert_step_row(step)
        self._loading = False
        self._refresh_preview()
        self._sync_start_spin_to_end()

    def _insert_step_row(self, step: SequenceStep) -> None:
        pattern = self.project.patterns.get(step.pattern)
        if pattern is None:
            return
        row = self.steps_table.rowCount()
        self.steps_table.insertRow(row)
        end_s = step.start_s + pattern.duration_s
        values = [
            QTableWidgetItem(step.pattern),
            QTableWidgetItem(f"{step.start_s:.4f}"),
            QTableWidgetItem(f"{end_s:.4f}"),
            QTableWidgetItem(f"{pattern.duration_s:.4f}"),
        ]
        for col, widget in enumerate(values):
            widget.setFlags(widget.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.steps_table.setItem(row, col, widget)
        self.steps_table.selectRow(row)

    def add_step(self) -> None:
        pattern_name = self.pattern_select.currentText().strip()
        if not pattern_name:
            return
        start_s = self._current_end_time()
        step = SequenceStep(pattern=pattern_name, start_s=start_s)
        existing = self.gather_sequence(allow_partial=True)
        existing.steps.append(step)
        existing.steps.sort(key=lambda s: s.start_s)
        self._loading = True
        self._set_sequence_steps(existing.steps)
        self._loading = False
        self.commit_current_sequence(silent=True)
        self._sync_start_spin_to_end()
        self.on_dirty()

    def remove_selected_step_rows(self) -> None:
        rows = sorted({index.row() for index in self.steps_table.selectedIndexes()}, reverse=True)
        if rows:
            self._loading = True
            for row in rows:
                self.steps_table.removeRow(row)
        elif self.steps_table.rowCount():
            self._loading = True
            self.steps_table.removeRow(self.steps_table.rowCount() - 1)
        self._loading = False
        self._refresh_preview()
        self.commit_current_sequence(silent=True)
        self._sync_start_spin_to_end()
        self.on_dirty()

    def _set_sequence_steps(self, steps: list[SequenceStep]) -> None:
        self.steps_table.setRowCount(0)
        for step in steps:
            self._insert_step_row(step)
        self._refresh_preview()
        self._sync_start_spin_to_end()

    def _on_form_changed(self, *_args) -> None:
        if self._loading:
            return
        self.commit_current_sequence(silent=True)
        self.on_dirty()

    def _current_end_time(self) -> float:
        end_time = 0.0
        for row in range(self.steps_table.rowCount()):
            pattern_item = self.steps_table.item(row, 0)
            start_item = self.steps_table.item(row, 1)
            if pattern_item is None or start_item is None:
                continue
            pattern = self.project.patterns.get(pattern_item.text().strip())
            if pattern is None:
                continue
            try:
                start_s = float(start_item.text())
            except ValueError:
                continue
            end_time = max(end_time, start_s + pattern.duration_s)
        return end_time

    def gather_sequence(self, allow_partial: bool = False) -> Sequence:
        steps: list[SequenceStep] = []
        for row in range(self.steps_table.rowCount()):
            pattern_item = self.steps_table.item(row, 0)
            start_item = self.steps_table.item(row, 1)
            if pattern_item is None or start_item is None:
                continue
            pattern_name = pattern_item.text().strip()
            if pattern_name not in self.project.patterns:
                if allow_partial:
                    continue
                raise ValueError(f"Unknown pattern '{pattern_name}'.")
            try:
                start_s = float(start_item.text())
            except ValueError as exc:
                if allow_partial:
                    continue
                raise ValueError(f"Sequence step {row + 1} has an invalid start time.") from exc
            steps.append(SequenceStep(pattern=pattern_name, start_s=start_s))
        return Sequence(
            name=self.name_edit.text().strip(),
            steps=steps,
            notes=self.notes_edit.text().strip(),
        )

    def commit_current_sequence(self, silent: bool = False) -> bool:
        try:
            sequence = self.gather_sequence()
        except ValueError as exc:
            if not silent:
                QMessageBox.warning(self, "Invalid sequence", str(exc))
            return False
        if not sequence.name:
            if not silent:
                QMessageBox.warning(self, "Invalid sequence", "Sequence name cannot be empty.")
            return False
        sequence.steps.sort(key=lambda step: step.start_s)
        if self.current_name and self.current_name != sequence.name and self.current_name in self.project.sequences:
            self.project.sequences.pop(self.current_name)
        self.project.sequences[sequence.name] = sequence
        self.current_name = sequence.name
        self._refresh_preview()
        self._sync_start_spin_to_end()
        if self.on_commit is not None:
            self.on_commit()
        return True

    def save_current_sequence(self) -> bool:
        return self.commit_current_sequence(silent=False)

    def _refresh_preview(self) -> None:
        self.preview.set_sequence(self.name_edit.text().strip())

    def clear_form(self) -> None:
        self.current_name = ""
        self._loading = True
        self.name_edit.clear()
        self.notes_edit.clear()
        self.steps_table.setRowCount(0)
        self.preview.set_sequence("")
        self.steps_table.clearSelection()
        self._loading = False
        self._sync_start_spin_to_end()

    def copy_current_sequence(self) -> None:
        try:
            sequence = self.gather_sequence()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid sequence", str(exc))
            return
        if not sequence.name:
            QMessageBox.warning(self, "Invalid sequence", "Sequence name cannot be empty.")
            return
        copied = Sequence(
            name=_unique_name(f"{sequence.name}_copy", self.project.sequences),
            steps=[SequenceStep(pattern=step.pattern, start_s=step.start_s) for step in sequence.steps],
            notes=sequence.notes,
        )
        self.project.sequences[copied.name] = copied
        if self.on_commit is not None:
            self.on_commit()

    def _sync_start_spin_to_end(self) -> None:
        end_s = self._current_end_time()
        self.start_spin.blockSignals(True)
        self.start_spin.setValue(end_s)
        self.start_spin.blockSignals(False)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Opto Schema GUI")
        self.project = ExperimentProject()
        self.save_root = _load_save_root()
        self.schema_root = _load_schema_root()
        self.schema_file_path = ""
        self.last_schema_load_dir = self.schema_root
        self.pattern_dirty = False
        self.sequence_dirty = False
        self._suppress_dirty_updates = False

        self.preview = TimelinePreview(self.project)
        self.pattern_editor = PatternEditor(self.project, self.mark_pattern_dirty, self.refresh_lists)
        self.sequence_editor = SequenceEditor(self.project, self.preview, self.mark_sequence_dirty, self.refresh_lists)
        self.scanimage_control = ScanImageControlWidget(self.ensure_schema_path_for_external_use)

        self.pattern_list = QListWidget()
        self.sequence_list = QListWidget()
        self.pattern_list.currentItemChanged.connect(self._pattern_selected)
        self.sequence_list.currentItemChanged.connect(self._sequence_selected)

        self.gui_control_config = _load_gui_control_config()
        self.gui_control_signals = GuiControlSignals()
        self.gui_control_signals.udp_message.connect(self._handle_gui_control_message)
        self.gui_control_listener: GuiControlListener | None = None

        self._build_ui()
        self.refresh_lists()
        self._start_gui_control_listener()

    def _build_ui(self) -> None:
        toolbar = QToolBar("File")
        self.addToolBar(toolbar)

        new_btn = QPushButton("New")
        load_schema_btn = QPushButton("Load Schema")
        save_schema_btn = QPushButton("Save Schema")

        toolbar.addWidget(new_btn)
        toolbar.addWidget(load_schema_btn)
        toolbar.addWidget(save_schema_btn)

        new_btn.clicked.connect(self.new_project)
        load_schema_btn.clicked.connect(self.load_schema_dialog)
        save_schema_btn.clicked.connect(self.save_schema_dialog)

        schema_left = QWidget()
        schema_left_layout = QVBoxLayout(schema_left)

        project_box = QGroupBox("Project")
        project_form = QFormLayout(project_box)
        self.animal_edit = QLineEdit("TEST")
        self.project_edit = QLineEdit("DEFAULT")
        self.save_path_label = QLabel()
        self.save_path_label.setWordWrap(True)
        self.animal_edit.textChanged.connect(self.update_save_path_label)
        self.project_edit.textChanged.connect(self.update_save_path_label)
        project_form.addRow("Animal ID", self.animal_edit)
        project_form.addRow("Project", self.project_edit)
        project_form.addRow("Save root", QLabel(str(self.save_root)))
        project_form.addRow("Default schema path", self.save_path_label)
        schema_left_layout.addWidget(project_box)

        pattern_box = QGroupBox("Patterns")
        pattern_layout = QVBoxLayout(pattern_box)
        pattern_layout.addWidget(self.pattern_list)
        pattern_buttons = QHBoxLayout()
        self.add_pattern_btn = QPushButton("Add")
        self.copy_pattern_btn = QPushButton("Copy")
        self.delete_pattern_btn = QPushButton("Delete")
        pattern_buttons.addWidget(self.add_pattern_btn)
        pattern_buttons.addWidget(self.copy_pattern_btn)
        pattern_buttons.addWidget(self.delete_pattern_btn)
        pattern_layout.addLayout(pattern_buttons)

        sequence_box = QGroupBox("Sequences")
        sequence_layout = QVBoxLayout(sequence_box)
        sequence_layout.addWidget(self.sequence_list)
        sequence_buttons = QHBoxLayout()
        self.add_sequence_btn = QPushButton("Add")
        self.copy_sequence_btn = QPushButton("Copy")
        self.delete_sequence_btn = QPushButton("Delete")
        sequence_buttons.addWidget(self.add_sequence_btn)
        sequence_buttons.addWidget(self.copy_sequence_btn)
        sequence_buttons.addWidget(self.delete_sequence_btn)
        sequence_layout.addLayout(sequence_buttons)

        schema_left_layout.addWidget(pattern_box, 1)
        schema_left_layout.addWidget(sequence_box, 1)

        self.schema_editor_tabs = QTabWidget()
        self.schema_editor_tabs.addTab(self.pattern_editor, "Pattern Editor")
        self.schema_editor_tabs.addTab(self.sequence_editor, "Sequence Editor")

        schema_splitter = QSplitter()
        schema_splitter.addWidget(schema_left)
        schema_splitter.addWidget(self.schema_editor_tabs)
        schema_splitter.setStretchFactor(1, 1)

        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self.scanimage_control, "ScanImage Control")
        self.main_tabs.addTab(schema_splitter, "Stimulation Schema")
        self.setCentralWidget(self.main_tabs)

        self.add_pattern_btn.clicked.connect(self.add_pattern)
        self.copy_pattern_btn.clicked.connect(self.copy_pattern)
        self.delete_pattern_btn.clicked.connect(self.delete_pattern)
        self.add_sequence_btn.clicked.connect(self.add_sequence)
        self.copy_sequence_btn.clicked.connect(self.copy_sequence)
        self.delete_sequence_btn.clicked.connect(self.delete_sequence)
        self.update_save_path_label()

    def _start_gui_control_listener(self) -> None:
        if not self.gui_control_config.enabled or self.gui_control_config.port <= 0:
            self.scanimage_control.signals.log_message.emit("[gui control] disabled")
            return
        if self.gui_control_listener is not None:
            return
        self.gui_control_listener = GuiControlListener(
            self.gui_control_config.host,
            self.gui_control_config.port,
            self.gui_control_signals,
        )
        self.gui_control_listener.start()
        self.scanimage_control.signals.log_message.emit(
            f"[gui control] UDP listener started on {self.gui_control_config.host}:{self.gui_control_config.port}"
        )

    def _stop_gui_control_listener(self) -> None:
        if self.gui_control_listener is None:
            return
        self.gui_control_listener.stop()
        self.gui_control_listener = None
        self.scanimage_control.signals.log_message.emit("[gui control] UDP listener stopped")

    def _send_gui_control_reply(self, address: tuple[str, int], payload: dict[str, object]) -> None:
        if self.gui_control_listener is None:
            return
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.gui_control_listener.send(encoded, address)

    def _set_main_tab(self, tab_value: object) -> dict[str, object]:
        if isinstance(tab_value, int):
            index = tab_value
        else:
            target = str(tab_value).strip()
            index = -1
            for idx in range(self.main_tabs.count()):
                if self.main_tabs.tabText(idx).lower() == target.lower():
                    index = idx
                    break
        if index < 0 or index >= self.main_tabs.count():
            raise ValueError(f"Unknown main tab '{tab_value}'")
        self.main_tabs.setCurrentIndex(index)
        return {
            "index": self.main_tabs.currentIndex(),
            "label": self.main_tabs.tabText(self.main_tabs.currentIndex()),
        }

    def _remote_set_state(self, values: dict[str, object]) -> dict[str, object]:
        applied: dict[str, object] = {}
        if "main_tab" in values:
            applied["main_tab"] = self._set_main_tab(values["main_tab"])
        if "animal_id" in values:
            self.animal_edit.setText(str(values["animal_id"]))
            applied["animal_id"] = self.animal_edit.text()
        if "project_name" in values:
            self.project_edit.setText(str(values["project_name"]))
            applied["project_name"] = self.project_edit.text()
        applied.update(self.scanimage_control.set_remote_state(values))
        return applied

    def _schema_state(self) -> dict[str, object]:
        return {
            "animal_id": self.animal_id(),
            "project_name": self.project_name(),
            "schema_file_path": self.schema_file_path,
            "schema_save_path": str(self.schema_save_path()),
            "pattern_dirty": self.pattern_dirty,
            "sequence_dirty": self.sequence_dirty,
            "current_pattern": self._current_item_name(self.pattern_list),
            "current_sequence": self._current_item_name(self.sequence_list),
            "pattern_names": list(self.project.patterns.keys()),
            "sequence_names": list(self.project.sequences.keys()),
        }

    def _remote_state(self) -> dict[str, object]:
        return {
            "window_title": self.windowTitle(),
            "main_tab": {
                "index": self.main_tabs.currentIndex(),
                "label": self.main_tabs.tabText(self.main_tabs.currentIndex()),
            },
            "schema": self._schema_state(),
            "scanimage": self.scanimage_control.get_remote_state(),
        }

    def _remote_invoke(self, command: str, payload: dict[str, object]) -> dict[str, object]:
        normalized = command.strip().lower()
        if normalized == "save_schema_default":
            return {"ok": self._save_schema_to_default(force=True)}
        if normalized == "ensure_schema_path":
            path = self.ensure_schema_path_for_external_use()
            return {"schema_path": str(path) if path is not None else ""}
        return self.scanimage_control.invoke_remote_action(
            normalized,
            path_name=str(payload["path_name"]) if "path_name" in payload and payload["path_name"] is not None else None,
            exp_id=str(payload["exp_id"]) if "exp_id" in payload and payload["exp_id"] is not None else None,
        )

    def _handle_gui_control_message(self, payload: bytes, address: tuple[str, int]) -> None:
        response: dict[str, object]
        try:
            request = json.loads(payload.decode("utf-8"))
            if not isinstance(request, dict):
                raise ValueError("GUI control payload must be a JSON object")
            action = str(request.get("action", "")).strip()
            response = {"action": action, "status": "ready"}
            if "request_id" in request:
                response["request_id"] = request["request_id"]

            if action == "ping":
                response["data"] = {"ok": True}
            elif action == "get_state":
                response["data"] = self._remote_state()
            elif action == "set_state":
                values = request.get("values")
                if not isinstance(values, dict):
                    raise ValueError("set_state requires a 'values' object")
                response["data"] = self._remote_set_state(values)
            elif action == "invoke":
                command = str(request.get("command", "")).strip()
                if not command:
                    raise ValueError("invoke requires 'command'")
                response["data"] = self._remote_invoke(command, request)
            elif action == "get_debug_log":
                scope = str(request.get("scope", "global")).strip().lower()
                last_n = int(request.get("last_n", 200))
                if scope == "global":
                    lines = self.scanimage_control.get_debug_log_lines(last_n)
                elif scope == "path_udp":
                    path_name = str(request.get("path_name", "")).strip()
                    if not path_name:
                        raise ValueError("get_debug_log scope=path_udp requires path_name")
                    lines = self.scanimage_control.get_path_udp_log_lines(path_name, last_n)
                else:
                    raise ValueError(f"Unknown log scope '{scope}'")
                response["data"] = {"scope": scope, "lines": lines}
            elif action == "matlab_eval":
                path_name = str(request.get("path_name", "")).strip()
                command = str(request.get("command", ""))
                if not path_name or not command:
                    raise ValueError("matlab_eval requires path_name and command")
                timeout_s = request.get("timeout_s")
                prepend_preamble = bool(request.get("prepend_preamble", True))
                lines = self.scanimage_control.eval_matlab_command(
                    path_name,
                    command,
                    timeout_s=float(timeout_s) if timeout_s is not None else None,
                    prepend_preamble=prepend_preamble,
                )
                response["data"] = {"path_name": path_name, "lines": lines}
            elif action == "respond_prompt":
                prompt_id = str(request.get("prompt_id", "")).strip()
                choice = str(request.get("choice", "")).strip()
                handled = self.scanimage_control.respond_remote_prompt(prompt_id, choice)
                response["data"] = {"handled": handled}
            else:
                raise ValueError(f"Unknown action '{action}'")
        except Exception as exc:
            response = {
                "action": "error",
                "status": "error",
                "error": str(exc),
            }
            try:
                decoded = json.loads(payload.decode("utf-8"))
                if isinstance(decoded, dict) and "request_id" in decoded:
                    response["request_id"] = decoded["request_id"]
            except Exception:
                pass
        self._send_gui_control_reply(address, response)

    def refresh_lists(self) -> None:
        current_pattern = self._current_item_name(self.pattern_list)
        current_sequence = self._current_item_name(self.sequence_list)

        self.pattern_list.blockSignals(True)
        self.sequence_list.blockSignals(True)
        self.pattern_list.clear()
        self.sequence_list.clear()

        for index, name in enumerate(self.project.patterns):
            item = QListWidgetItem(f"{index}: {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.pattern_list.addItem(item)
        for index, name in enumerate(self.project.sequences):
            item = QListWidgetItem(f"{index}: {name}")
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.sequence_list.addItem(item)

        self.pattern_list.blockSignals(False)
        self.sequence_list.blockSignals(False)

        self.sequence_editor.refresh_pattern_choices()

        if current_pattern:
            self._select_item_by_name(self.pattern_list, current_pattern)
        elif self.pattern_list.count():
            self.pattern_list.setCurrentRow(0)

        if current_sequence:
            self._select_item_by_name(self.sequence_list, current_sequence)
        elif self.sequence_list.count():
            self.sequence_list.setCurrentRow(0)

        self.update_status()
        self.preview.update()
        self.sequence_editor._refresh_preview()

    def update_status(self) -> None:
        errors = self.project.validate()
        dirty_bits = []
        if self.pattern_dirty:
            dirty_bits.append("patterns modified")
        if self.sequence_dirty:
            dirty_bits.append("sequences modified")
        message = " | ".join(dirty_bits + (errors[:3] if errors else [])) if (dirty_bits or errors) else "Project valid"
        self.statusBar().showMessage(message)

    def update_save_path_label(self) -> None:
        self.save_path_label.setText(str(self.schema_save_path()))

    def animal_id(self) -> str:
        value = self.animal_edit.text().strip()
        return value or "TEST"

    def project_name(self) -> str:
        value = self.project_edit.text().strip()
        return value or "DEFAULT"

    def project_dir(self) -> Path:
        return self.save_root / self.animal_id() / self.project_name()

    def schema_save_path(self) -> Path:
        return self.project_dir() / "schema.yaml"

    def mark_pattern_dirty(self) -> None:
        if self._suppress_dirty_updates:
            return
        self.pattern_dirty = True
        self.update_status()

    def mark_sequence_dirty(self) -> None:
        if self._suppress_dirty_updates:
            return
        self.sequence_dirty = True
        self.update_status()

    def clear_dirty(self) -> None:
        self.pattern_dirty = False
        self.sequence_dirty = False
        self.update_status()

    def _pattern_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:  # noqa: ARG002
        if not current:
            return
        name = self._item_name(current)
        if name in self.project.patterns:
            self.schema_editor_tabs.setCurrentWidget(self.pattern_editor)
            self.pattern_editor.load_pattern(name)

    def _sequence_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:  # noqa: ARG002
        if not current:
            return
        name = self._item_name(current)
        if name in self.project.sequences:
            self.schema_editor_tabs.setCurrentWidget(self.sequence_editor)
            self.sequence_editor.load_sequence(name)
            self.preview.set_sequence(name)

    def add_pattern(self) -> None:
        name = _unique_name("pattern", self.project.patterns)
        self.project.patterns[name] = Pattern(
            name=name,
            duration_s=1.0,
            frequency_hz=10.0,
            power_percent=20.0,
            duty_cycle=0.2,
            spiral_width=10.0,
            spiral_height=10.0,
            cells=[CellSpec(label="cell1", x=0, y=0, z=0)],
        )
        self.pattern_dirty = True
        self.refresh_lists()
        self._select_item_by_name(self.pattern_list, name)
        self.main_tabs.setCurrentIndex(1)
        self.schema_editor_tabs.setCurrentWidget(self.pattern_editor)

    def copy_pattern(self) -> None:
        current = self.pattern_list.currentItem()
        if not current:
            return
        name = self._item_name(current)
        pattern = self.project.patterns[name]
        copied = Pattern(
            name=_unique_name(f"{name}_copy", self.project.patterns),
            duration_s=pattern.duration_s,
            frequency_hz=pattern.frequency_hz,
            power_percent=pattern.power_percent,
            duty_cycle=pattern.duty_cycle,
            spiral_width=pattern.spiral_width,
            spiral_height=pattern.spiral_height,
            notes=pattern.notes,
            cells=[CellSpec(label=cell.label, x=cell.x, y=cell.y, z=cell.z, power_scale=cell.power_scale) for cell in pattern.cells],
        )
        self.project.patterns[copied.name] = copied
        self.pattern_dirty = True
        self.refresh_lists()
        self._select_item_by_name(self.pattern_list, copied.name)
        self.main_tabs.setCurrentIndex(1)
        self.schema_editor_tabs.setCurrentWidget(self.pattern_editor)

    def delete_pattern(self) -> None:
        current = self.pattern_list.currentItem()
        if not current:
            return
        name = self._item_name(current)
        if QMessageBox.question(self, "Delete pattern", f"Delete pattern '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.project.patterns.pop(name, None)
        for sequence in self.project.sequences.values():
            sequence.steps = [step for step in sequence.steps if step.pattern != name]
        self.pattern_dirty = True
        self.sequence_dirty = True
        self.refresh_lists()

    def add_sequence(self) -> None:
        name = _unique_name("sequence", self.project.sequences)
        self.project.sequences[name] = Sequence(name=name)
        self.sequence_dirty = True
        self.refresh_lists()
        self._select_item_by_name(self.sequence_list, name)
        self.main_tabs.setCurrentIndex(1)
        self.schema_editor_tabs.setCurrentWidget(self.sequence_editor)

    def copy_sequence(self) -> None:
        current = self.sequence_list.currentItem()
        if not current:
            return
        name = self._item_name(current)
        sequence = self.project.sequences[name]
        copied = Sequence(
            name=_unique_name(f"{name}_copy", self.project.sequences),
            steps=[SequenceStep(pattern=step.pattern, start_s=step.start_s) for step in sequence.steps],
            notes=sequence.notes,
        )
        self.project.sequences[copied.name] = copied
        self.sequence_dirty = True
        self.refresh_lists()
        self._select_item_by_name(self.sequence_list, copied.name)
        self.main_tabs.setCurrentIndex(1)
        self.schema_editor_tabs.setCurrentWidget(self.sequence_editor)

    def delete_sequence(self) -> None:
        current = self.sequence_list.currentItem()
        if not current:
            return
        name = self._item_name(current)
        if QMessageBox.question(self, "Delete sequence", f"Delete sequence '{name}'?") != QMessageBox.StandardButton.Yes:
            return
        self.project.sequences.pop(name, None)
        self.sequence_dirty = True
        self.refresh_lists()

    def _item_name(self, item: QListWidgetItem | None) -> str:
        if item is None:
            return ""
        value = item.data(Qt.ItemDataRole.UserRole)
        return value if isinstance(value, str) else item.text()

    def _current_item_name(self, widget: QListWidget) -> str:
        return self._item_name(widget.currentItem())

    def _select_item_by_name(self, widget: QListWidget, name: str) -> None:
        for index in range(widget.count()):
            item = widget.item(index)
            if self._item_name(item) == name:
                widget.setCurrentItem(item)
                return

    def new_project(self) -> None:
        if self.pattern_dirty or self.sequence_dirty:
            choice = QMessageBox.question(
                self,
                "New project",
                "You have unsaved changes. Save them before creating a new project?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Save:
                if not self._save_schema_to_default(force=False):
                    return
        self._suppress_dirty_updates = True
        try:
            self.project = ExperimentProject()
            self.schema_file_path = ""
            self.pattern_editor.project = self.project
            self.sequence_editor.project = self.project
            self.preview.project = self.project
            self.pattern_editor.current_name = ""
            self.sequence_editor.current_name = ""
            self.pattern_dirty = False
            self.sequence_dirty = False
            self.pattern_editor.clear_form()
            self.sequence_editor.clear_form()
            self.refresh_lists()
        finally:
            self._suppress_dirty_updates = False
        self.clear_dirty()

    def load_schema_dialog(self) -> None:
        if self.pattern_dirty or self.sequence_dirty:
            choice = QMessageBox.question(
                self,
                "Load schema",
                "You have unsaved changes. Save them before loading a schema file?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice == QMessageBox.StandardButton.Cancel:
                return
            if choice == QMessageBox.StandardButton.Save:
                if not self._save_schema_to_default(force=False):
                    return
        start_dir = self.last_schema_load_dir if self.last_schema_load_dir else self.schema_root
        path, _ = QFileDialog.getOpenFileName(self, "Load schema YAML", str(start_dir), "YAML (*.yaml *.yml)")
        if not path:
            return
        self._suppress_dirty_updates = True
        try:
            self.project = load_schema(path)
            self.pattern_editor.project = self.project
            self.sequence_editor.project = self.project
            self.preview.project = self.project
            self.schema_file_path = path
            self.pattern_editor.current_name = ""
            self.sequence_editor.current_name = ""
            self.pattern_editor.clear_form()
            self.sequence_editor.clear_form()
            self.pattern_dirty = False
            self.sequence_dirty = False
            self.last_schema_load_dir = Path(path).resolve().parent
            self.refresh_lists()
        finally:
            self._suppress_dirty_updates = False
        self.clear_dirty()
        self.main_tabs.setCurrentIndex(1)
        self.schema_editor_tabs.setCurrentWidget(self.pattern_editor)

    def save_schema_dialog(self) -> bool:
        default_path = self.schema_save_path()
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save schema",
            str(default_path),
            "YAML (*.yaml *.yml)",
        )
        if not path:
            return False
        target = Path(path)
        if target.exists():
            choice = QMessageBox.question(
                self,
                "Overwrite schema",
                f"'{target}' already exists. Overwrite it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False
        self._suppress_dirty_updates = True
        try:
            if self.pattern_dirty and not self.pattern_editor.save_current_pattern():
                return False
            if self.sequence_dirty and not self.sequence_editor.save_current_sequence():
                return False
            target.parent.mkdir(parents=True, exist_ok=True)
            save_schema(target, self.project)
            self.schema_file_path = str(target)
            self.pattern_dirty = False
            self.sequence_dirty = False
            self.update_save_path_label()
            self.refresh_lists()
        finally:
            self._suppress_dirty_updates = False
        self.clear_dirty()
        return True

    def _save_schema_to_default(self, force: bool = False) -> bool:
        if not force and not (self.pattern_dirty or self.sequence_dirty):
            return True
        self._suppress_dirty_updates = True
        try:
            if self.pattern_dirty and not self.pattern_editor.save_current_pattern():
                return False
            if self.sequence_dirty and not self.sequence_editor.save_current_sequence():
                return False
            path = self.schema_save_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            save_schema(path, self.project)
            self.schema_file_path = str(path)
            self.pattern_dirty = False
            self.sequence_dirty = False
            self.refresh_lists()
        finally:
            self._suppress_dirty_updates = False
        self.clear_dirty()
        return True

    def ensure_schema_path_for_external_use(self) -> Path | None:
        if self.pattern_dirty or self.sequence_dirty:
            choice = QMessageBox.question(
                self,
                "Save schema",
                "External ScanImage commands need a saved schema file. Save the schema now?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice != QMessageBox.StandardButton.Save:
                return None
            if not self.save_schema_dialog():
                return None
        elif not self.schema_file_path:
            choice = QMessageBox.question(
                self,
                "Save schema",
                "This schema has not been saved to disk yet. Save it now for external ScanImage commands?",
                QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if choice != QMessageBox.StandardButton.Save:
                return None
            if not self.save_schema_dialog():
                return None
        if not self.schema_file_path:
            return None
        return Path(self.schema_file_path)

    def _resolve_dirty_switch(
        self,
        dirty: bool,
        save_fn,
        current: QListWidgetItem,
        previous: QListWidgetItem | None,
        widget: QListWidget,
        label: str,
    ) -> bool:
        if not dirty:
            return True
        choice = QMessageBox.question(
            self,
            f"Unsaved {label}s",
            f"You have unsaved {label} changes. Save them before switching?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Save:
            if not save_fn():
                self._restore_selection(widget, previous)
                return False
            return True
        if choice == QMessageBox.StandardButton.Cancel:
            self._restore_selection(widget, previous)
            return False
        return True

    def _restore_selection(self, widget: QListWidget, previous: QListWidgetItem | None) -> None:
        widget.blockSignals(True)
        if previous is None:
            widget.clearSelection()
        else:
            widget.setCurrentItem(previous)
        widget.blockSignals(False)

    def closeEvent(self, event) -> None:  # noqa: N802
        if not (self.pattern_dirty or self.sequence_dirty):
            self._stop_gui_control_listener()
            self.scanimage_control.shutdown()
            event.accept()
            return
        choice = QMessageBox.question(
            self,
            "Unsaved changes",
            "Save unsaved changes before exit?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if choice == QMessageBox.StandardButton.Cancel:
            event.ignore()
            return
        if choice == QMessageBox.StandardButton.Save:
            if not self._save_schema_to_default(force=False):
                event.ignore()
                return
        self._stop_gui_control_listener()
        self.scanimage_control.shutdown()
        event.accept()


def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    screen_index = None
    start_maximized = True
    machine_name = autodetect_machine_name(_repo_root())
    if machine_name:
        machine_ui = load_machine_ui_config(_repo_root(), machine_name)
        screen_index = machine_ui.screen_index
        start_maximized = machine_ui.start_maximized

    screens = app.screens()
    if screen_index is not None and 0 <= screen_index < len(screens):
        geometry = screens[screen_index].availableGeometry()
        window.setGeometry(geometry)
    else:
        window.resize(1400, 900)

    if start_maximized:
        window.showMaximized()
    else:
        window.show()
    sys.exit(app.exec())
