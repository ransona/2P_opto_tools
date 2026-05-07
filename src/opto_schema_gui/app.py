from __future__ import annotations

import hashlib
import configparser
import getpass
import json
import re
import socket
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import yaml

from PyQt6.QtCore import QObject, Qt, QRect, QRectF, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
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
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from .imaging_coordinates import (
    ProcessedFovGroup,
    ScanfieldChoice,
    convert_imaging_pixel_to_pattern_coords,
    load_processed_cell_overlay,
    list_processed_channels,
    list_processed_fov_groups,
    list_imaging_scanfields,
    resolve_processed_cell_to_imaging_pixel,
)
from .io import load_schema, save_schema
from .models import SCHEMA_TIME_QUANTUM_S, CellSpec, ExperimentProject, Pattern, Sequence, SequenceStep
from .matlab_bridge import autodetect_machine_name
from .scanimage_control import ScanImageControlWidget


@dataclass
class GuiControlConfig:
    enabled: bool
    host: str
    port: int


def _preferred_startup_geometry(app: QApplication) -> QRect | None:
    if sys.platform.startswith("linux"):
        geometry = _linux_monitor_geometry()
        if geometry is not None:
            return geometry
    screen = app.primaryScreen()
    return screen.availableGeometry() if screen is not None else None


def _linux_monitor_geometry() -> QRect | None:
    try:
        result = subprocess.run(
            ["xrandr", "--listactivemonitors"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None

    monitors: list[tuple[bool, int, int, int, int, str]] = []
    pattern = re.compile(
        r"^\s*\d+:\s+\+(?P<primary>\*)?(?P<name>\S+)\s+"
        r"(?P<width>\d+)(?:/\d+)?x(?P<height>\d+)(?:/\d+)?\+(?P<x>-?\d+)\+(?P<y>-?\d+)"
    )
    for line in result.stdout.splitlines():
        match = pattern.match(line)
        if not match:
            continue
        monitors.append(
            (
                bool(match.group("primary")),
                int(match.group("x")),
                int(match.group("y")),
                int(match.group("width")),
                int(match.group("height")),
                match.group("name"),
            )
        )
    if not monitors:
        return None

    primary = next((monitor for monitor in monitors if monitor[0]), monitors[0])
    _is_primary, x, y, width, height, name = primary

    # X forwarding / spanned desktops sometimes expose one fake ultra-wide "default" monitor.
    # In that case, fall back to a reasonable single-monitor-sized region centered within it.
    if len(monitors) == 1 and name == "default" and width >= 3000:
        single_width = min(width, 1920)
        x = x + max(0, (width - single_width) // 2)
        width = single_width

    return QRect(x, y, width, height)


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


def _lowest_available_numbered_name(prefix: str, existing: dict[str, object]) -> str:
    used_numbers: set[int] = set()
    for name in existing:
        match = re.fullmatch(rf"{re.escape(prefix)}(\d+)", name)
        if match is not None:
            used_numbers.add(int(match.group(1)))
    candidate = 1
    while candidate in used_numbers:
        candidate += 1
    return f"{prefix}{candidate}"


def _selected_or_last_row(table: QTableWidget) -> int | None:
    rows = sorted({index.row() for index in table.selectedIndexes()})
    if rows:
        return rows[-1]
    if table.rowCount():
        return table.rowCount() - 1
    return None


def _read_only_item(text: str) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return item


def _cell_origin_metadata(cell: CellSpec) -> dict[str, object]:
    return {
        "origin_exp_id": cell.origin_exp_id,
        "origin_user_id": cell.origin_user_id,
        "origin_processed_cell_id": cell.origin_processed_cell_id,
        "origin_imaging_path": cell.origin_imaging_path,
        "origin_roi_folder_name": cell.origin_roi_folder_name,
        "origin_plane_index": cell.origin_plane_index,
        "origin_z_um": cell.origin_z_um,
    }


class RoiOverlayDialog(QDialog):
    def __init__(self, title: str, pixmap: QPixmap, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Cell ROI Overlay")
        self._pixmap = pixmap
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(title))
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image_label, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        buttons.button(QDialogButtonBox.StandardButton.Close).clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(900, 900)
        self._refresh_pixmap()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()

    def _refresh_pixmap(self) -> None:
        if self._pixmap.isNull():
            self.image_label.clear()
            return
        scaled = self._pixmap.scaled(
            self.image_label.size() if self.image_label.size().isValid() else self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.image_label.setPixmap(scaled)


class ClickableImageLabel(QLabel):
    image_clicked = pyqtSignal(int, int)

    def __init__(self, empty_text: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self._source_pixmap: QPixmap | None = None
        self._image_width = 0
        self._image_height = 0
        self._empty_text = empty_text
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 240)
        self.setStyleSheet("background:#111827; color:#cbd5e1; border:1px solid #475569;")
        self.setWordWrap(True)
        self._refresh_pixmap()

    def set_image(self, pixmap: QPixmap | None, image_shape: tuple[int, int] | None, empty_text: str | None = None) -> None:
        self._source_pixmap = pixmap
        if image_shape is None:
            self._image_height = 0
            self._image_width = 0
        else:
            self._image_height = int(image_shape[0])
            self._image_width = int(image_shape[1])
        if empty_text is not None:
            self._empty_text = empty_text
        self._refresh_pixmap()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._refresh_pixmap()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        super().mousePressEvent(event)
        if self._source_pixmap is None or self._image_width <= 0 or self._image_height <= 0:
            return
        display = self.pixmap()
        if display is None or display.isNull():
            return
        pix_width = display.width()
        pix_height = display.height()
        rect = self.contentsRect()
        left = rect.x() + max(0, (rect.width() - pix_width) // 2)
        top = rect.y() + max(0, (rect.height() - pix_height) // 2)
        x = event.position().x()
        y = event.position().y()
        if x < left or y < top or x >= left + pix_width or y >= top + pix_height:
            return
        rel_x = (x - left) / max(1, pix_width)
        rel_y = (y - top) / max(1, pix_height)
        img_x = min(self._image_width - 1, max(0, int(rel_x * self._image_width)))
        img_y = min(self._image_height - 1, max(0, int(rel_y * self._image_height)))
        self.image_clicked.emit(img_x, img_y)

    def _refresh_pixmap(self) -> None:
        if self._source_pixmap is None or self._source_pixmap.isNull():
            self.clear()
            self.setText(self._empty_text)
            return
        self.setText("")
        scaled = self._source_pixmap.scaled(
            self.contentsRect().size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.setPixmap(scaled)


class MultiCellActivityPlotWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._cell_payloads: list[dict[str, object]] = []
        self._normalization_mode = "scaled"
        self._display_mode = "all"
        self._pre_s = 1.0
        self._post_s = 3.0
        self._y_max_override: float | None = None
        self._placeholder_text = "No online activity data yet"
        self.setMinimumHeight(220)

    def set_plot_data(
        self,
        cell_payloads: list[dict[str, object]],
        *,
        normalization_mode: str,
        display_mode: str,
        pre_s: float,
        post_s: float,
        y_max_override: float | None,
        placeholder_text: str,
    ) -> None:
        self._cell_payloads = cell_payloads
        self._normalization_mode = normalization_mode
        self._display_mode = display_mode
        self._pre_s = max(0.0, float(pre_s))
        self._post_s = max(0.1, float(post_s))
        self._y_max_override = y_max_override if y_max_override is None else float(y_max_override)
        self._placeholder_text = placeholder_text
        self.updateGeometry()
        self.update()

    def sizeHint(self) -> QSize:
        row_height = 28 if self._display_mode == "heat" else 44
        summary_height = 0 if self._display_mode == "heat" else 110
        return QSize(1100, max(220, 64 + summary_height + row_height * max(1, len(self._cell_payloads))))

    def _normalize_series(
        self,
        times: list[float],
        values: list[float],
    ) -> tuple[np.ndarray, np.ndarray] | None:
        x = np.asarray(times, dtype=float)
        y = np.asarray(values, dtype=float)
        if x.size == 0 or y.size == 0 or x.size != y.size:
            return None
        valid = np.isfinite(x) & np.isfinite(y)
        if not np.any(valid):
            return None
        x = x[valid]
        y = y[valid]
        order = np.argsort(x)
        x = x[order]
        y = y[order]
        if self._normalization_mode == "dff":
            if y.size >= 3:
                kernel = np.ones(5, dtype=float) / 5.0
                smooth = np.convolve(y, kernel, mode="same")
            else:
                smooth = y
            f0 = float(np.nanpercentile(smooth, 5.0))
            if not np.isfinite(f0) or abs(f0) < 1e-9:
                transformed = np.zeros_like(y)
            else:
                transformed = (y - f0) / f0
            return x, transformed
        return x, y

    def _series_matrix(
        self,
        series: list[tuple[np.ndarray, np.ndarray]],
        grid: np.ndarray,
    ) -> np.ndarray | None:
        if not series:
            return None
        stacked: list[np.ndarray] = []
        for x, y in series:
            if x.size == 0:
                continue
            interp = np.full(grid.shape, np.nan, dtype=float)
            lo = max(grid[0], x[0])
            hi = min(grid[-1], x[-1])
            if hi < lo:
                continue
            mask = (grid >= lo) & (grid <= hi)
            interp[mask] = np.interp(grid[mask], x, y)
            stacked.append(interp)
        if not stacked:
            return None
        return np.vstack(stacked)

    def _draw_polyline(self, painter: QPainter, points: list[tuple[float, float]], pen: QPen) -> None:
        if len(points) < 2:
            return
        painter.setPen(pen)
        for start, end in zip(points[:-1], points[1:], strict=False):
            painter.drawLine(int(start[0]), int(start[1]), int(end[0]), int(end[1]))

    def _draw_shaded_band(
        self,
        painter: QPainter,
        xs: np.ndarray,
        lower: np.ndarray,
        upper: np.ndarray,
        map_point,
        color: QColor,
    ) -> None:
        valid = np.isfinite(xs) & np.isfinite(lower) & np.isfinite(upper)
        if np.count_nonzero(valid) < 2:
            return
        xs = xs[valid]
        lower = lower[valid]
        upper = upper[valid]
        upper_points = [map_point(float(xv), float(yv)) for xv, yv in zip(xs, upper, strict=False)]
        lower_points = [map_point(float(xv), float(yv)) for xv, yv in zip(xs[::-1], lower[::-1], strict=False)]
        polygon = upper_points + lower_points
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPolygon(QPolygonF([QRectF(px, py, 0.0, 0.0).topLeft() for px, py in polygon]))

    def _line_limits(self) -> tuple[float, float]:
        observed_min = 0.0
        observed_max = 1.0
        have_values = False
        for payload in self._cell_payloads:
            completed_trials = payload.get("completed_trials", [])
            for trial in completed_trials:
                normalized = self._normalize_series(list(trial.get("times", [])), list(trial.get("values", [])))
                if normalized is None:
                    continue
                _x, y = normalized
                if y.size:
                    observed_min = min(observed_min, float(np.nanmin(y)))
                    observed_max = max(observed_max, float(np.nanmax(y)))
                    have_values = True
            current_trial = payload.get("current_trial")
            if isinstance(current_trial, dict):
                normalized = self._normalize_series(list(current_trial.get("times", [])), list(current_trial.get("values", [])))
                if normalized is not None:
                    _x, y = normalized
                    if y.size:
                        observed_min = min(observed_min, float(np.nanmin(y)))
                        observed_max = max(observed_max, float(np.nanmax(y)))
                        have_values = True
        if not have_values:
            return 0.0, 1.0
        if self._y_max_override is not None:
            return observed_min, max(self._y_max_override, observed_min + 1e-6)
        if abs(observed_max - observed_min) < 1e-9:
            return observed_min - 0.5, observed_max + 0.5
        margin = 0.1 * (observed_max - observed_min)
        return observed_min - margin, observed_max + margin

    def _heat_color(self, value: float, vmin: float, vmax: float) -> QColor:
        if not np.isfinite(value):
            return QColor("#0f172a")
        span = max(1e-9, vmax - vmin)
        clipped = min(vmax, max(vmin, value))
        t = (clipped - vmin) / span
        return QColor(int(20 + 235 * t), int(24 + 180 * t), int(40 + 40 * (1.0 - t)))

    def _draw_y_axis_labels(
        self,
        painter: QPainter,
        rect: QRect,
        y_min: float,
        y_max: float,
        *,
        x_offset: int = 40,
    ) -> None:
        painter.setPen(QColor("#334155"))
        painter.drawText(
            QRect(rect.left() - x_offset, rect.top() - 8, x_offset - 6, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{y_max:.2g}",
        )
        painter.drawText(
            QRect(rect.left() - x_offset, rect.bottom() - 8, x_offset - 6, 16),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"{y_min:.2g}",
        )

    def _render_line_mode(self, painter: QPainter, plot_rect: QRect) -> None:
        x_min = -self._pre_s
        x_max = self._post_s
        y_min, y_max = self._line_limits()
        label_width = 180
        time_axis_height = 26
        row_gap = 6
        summary_height = 96
        summary_gap = 14
        summary_rect = plot_rect.adjusted(label_width, 8, -12, -(plot_rect.height() - 8 - summary_height))
        rows_rect = plot_rect.adjusted(label_width, summary_height + summary_gap + 8, -12, -time_axis_height)
        row_count = max(1, len(self._cell_payloads))
        row_height = max(18.0, (rows_rect.height() - row_gap * (row_count - 1)) / row_count)

        def map_point(x_value: float, y_value: float, row_index: int) -> tuple[float, float]:
            row_top = rows_rect.top() + row_index * (row_height + row_gap)
            x_norm = (x_value - x_min) / max(1e-9, x_max - x_min)
            y_norm = (y_value - y_min) / max(1e-9, y_max - y_min)
            px = rows_rect.left() + x_norm * rows_rect.width()
            py = row_top + row_height - y_norm * row_height
            return px, py

        zero_x = rows_rect.left() + ((0.0 - x_min) / max(1e-9, x_max - x_min)) * rows_rect.width()
        painter.setPen(QPen(QColor("#475569"), 3))
        painter.drawLine(int(zero_x), summary_rect.top(), int(zero_x), rows_rect.bottom())

        painter.setPen(QPen(QColor("#e2e8f0"), 1))
        for row_index in range(row_count):
            row_bottom = rows_rect.top() + row_index * (row_height + row_gap) + row_height
            painter.drawLine(rows_rect.left(), int(row_bottom), rows_rect.right(), int(row_bottom))

        grid = np.linspace(x_min, x_max, 240)
        summary_mean_rows: list[np.ndarray] = []
        summary_current_rows: list[np.ndarray] = []
        for row_index, payload in enumerate(self._cell_payloads):
            label = str(payload.get("label", "")).strip() or str(payload.get("roi_name", ""))
            row_mid = rows_rect.top() + row_index * (row_height + row_gap) + row_height / 2.0
            painter.setPen(QColor("#0f172a"))
            painter.drawText(
                QRect(plot_rect.left() + 6, int(row_mid - 12), label_width - 18, 24),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                label,
            )
            completed_series = [
                normalized
                for trial in payload.get("completed_trials", [])
                if (normalized := self._normalize_series(list(trial.get("times", [])), list(trial.get("values", [])))) is not None
            ]
            current_series = None
            current_trial = payload.get("current_trial")
            if isinstance(current_trial, dict):
                current_series = self._normalize_series(list(current_trial.get("times", [])), list(current_trial.get("values", [])))
            all_series = completed_series + ([current_series] if current_series is not None else [])
            matrix = self._series_matrix(all_series, grid)
            mean_trace = None
            sem_trace = None
            if matrix is not None:
                with np.errstate(invalid="ignore"):
                    mean_trace = np.nanmean(matrix, axis=0)
                    counts = np.sum(np.isfinite(matrix), axis=0)
                    sem_trace = np.nanstd(matrix, axis=0) / np.sqrt(np.maximum(counts, 1))
                summary_mean_rows.append(mean_trace)

            if self._display_mode == "all":
                for series in completed_series:
                    x_vals, y_vals = series
                    points = [map_point(float(xv), float(yv), row_index) for xv, yv in zip(x_vals, y_vals, strict=False)]
                    self._draw_polyline(painter, points, QPen(QColor("#cbd5e1"), 1))

            if self._display_mode == "mean_error" and mean_trace is not None and sem_trace is not None:
                self._draw_shaded_band(
                    painter,
                    grid,
                    mean_trace - sem_trace,
                    mean_trace + sem_trace,
                    lambda xv, yv: map_point(xv, yv, row_index),
                    QColor(148, 163, 184, 90),
                )

            if mean_trace is not None:
                points = [
                    map_point(float(xv), float(yv), row_index)
                    for xv, yv in zip(grid, mean_trace, strict=False)
                    if np.isfinite(yv)
                ]
                self._draw_polyline(painter, points, QPen(QColor("#111827"), 2))

            if current_series is not None:
                x_vals, y_vals = current_series
                points = [map_point(float(xv), float(yv), row_index) for xv, yv in zip(x_vals, y_vals, strict=False)]
                self._draw_polyline(painter, points, QPen(QColor("#15803d"), 2))
                current_matrix = self._series_matrix([current_series], grid)
                if current_matrix is not None:
                    summary_current_rows.append(current_matrix[0])

        if summary_mean_rows:
            summary_matrix = np.vstack(summary_mean_rows)
            with np.errstate(invalid="ignore"):
                summary_mean = np.nanmean(summary_matrix, axis=0)
                summary_sem = np.nanstd(summary_matrix, axis=0) / np.sqrt(np.maximum(np.sum(np.isfinite(summary_matrix), axis=0), 1))

            def map_summary_point(x_value: float, y_value: float) -> tuple[float, float]:
                x_norm = (x_value - x_min) / max(1e-9, x_max - x_min)
                y_norm = (y_value - y_min) / max(1e-9, y_max - y_min)
                px = summary_rect.left() + x_norm * summary_rect.width()
                py = summary_rect.bottom() - y_norm * summary_rect.height()
                return px, py

            painter.setPen(QColor("#0f172a"))
            painter.drawText(QRect(plot_rect.left() + 6, summary_rect.top(), label_width - 18, 24), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, "Mean all ROIs")
            painter.setPen(QPen(QColor("#e2e8f0"), 1))
            for frac in (0.0, 0.5, 1.0):
                y_line = summary_rect.top() + frac * summary_rect.height()
                painter.drawLine(summary_rect.left(), int(y_line), summary_rect.right(), int(y_line))
            if self._display_mode == "mean_error":
                self._draw_shaded_band(
                    painter,
                    grid,
                    summary_mean - summary_sem,
                    summary_mean + summary_sem,
                    map_summary_point,
                    QColor(148, 163, 184, 90),
                )
            points = [
                map_summary_point(float(xv), float(yv))
                for xv, yv in zip(grid, summary_mean, strict=False)
                if np.isfinite(yv)
            ]
            self._draw_polyline(painter, points, QPen(QColor("#111827"), 2))
            if summary_current_rows:
                current_summary = np.nanmean(np.vstack(summary_current_rows), axis=0)
                points = [
                    map_summary_point(float(xv), float(yv))
                    for xv, yv in zip(grid, current_summary, strict=False)
                    if np.isfinite(yv)
                ]
                self._draw_polyline(painter, points, QPen(QColor("#15803d"), 2))
            painter.setPen(QColor("#0f172a"))
            painter.drawRect(summary_rect)
            self._draw_y_axis_labels(painter, summary_rect, y_min, y_max)

        painter.setPen(QColor("#0f172a"))
        painter.drawRect(rows_rect)
        self._draw_y_axis_labels(painter, rows_rect, y_min, y_max)
        painter.drawText(rows_rect.left(), plot_rect.bottom() - 6, f"{x_min:.1f}s")
        painter.drawText(int(zero_x) - 8, plot_rect.bottom() - 6, "0")
        painter.drawText(rows_rect.right() - 28, plot_rect.bottom() - 6, f"{x_max:.1f}s")

    def _render_heat_mode(self, painter: QPainter, plot_rect: QRect) -> None:
        x_min = -self._pre_s
        x_max = self._post_s
        grid = np.linspace(x_min, x_max, 240)
        label_width = 180
        colorbar_width = 18
        colorbar_gap = 10
        heat_rect = plot_rect.adjusted(label_width, 8, -(colorbar_width + colorbar_gap + 12), -28)
        row_count = max(1, len(self._cell_payloads))
        row_height = max(12.0, heat_rect.height() / row_count)
        observed_min = 0.0
        observed_max = 1.0
        mean_rows: list[np.ndarray] = []
        for payload in self._cell_payloads:
            all_series = [
                normalized
                for trial in payload.get("completed_trials", [])
                if (normalized := self._normalize_series(list(trial.get("times", [])), list(trial.get("values", [])))) is not None
            ]
            current_trial = payload.get("current_trial")
            if isinstance(current_trial, dict):
                normalized = self._normalize_series(list(current_trial.get("times", [])), list(current_trial.get("values", [])))
                if normalized is not None:
                    all_series.append(normalized)
            matrix = self._series_matrix(all_series, grid)
            if matrix is None:
                mean_trace = np.full(grid.shape, np.nan, dtype=float)
            else:
                with np.errstate(invalid="ignore"):
                    mean_trace = np.nanmean(matrix, axis=0)
            mean_rows.append(mean_trace)
            valid = mean_trace[np.isfinite(mean_trace)]
            if valid.size:
                observed_min = min(observed_min, float(np.nanmin(valid)))
                observed_max = max(observed_max, float(np.nanmax(valid)))

        heat_min = min(0.0, observed_min)
        heat_max = self._y_max_override if self._y_max_override is not None else observed_max
        cell_width = max(1.0, heat_rect.width() / max(1, grid.size))
        for row_index, (payload, mean_trace) in enumerate(zip(self._cell_payloads, mean_rows, strict=False)):
            label = str(payload.get("label", "")).strip() or str(payload.get("roi_name", ""))
            row_top = heat_rect.top() + row_index * row_height
            painter.setPen(QColor("#0f172a"))
            painter.drawText(
                QRect(plot_rect.left() + 6, int(row_top), label_width - 18, int(row_height)),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight,
                label,
            )
            for col_index, value in enumerate(mean_trace):
                left = heat_rect.left() + col_index * cell_width
                painter.fillRect(QRectF(left, row_top, cell_width + 1.0, row_height), self._heat_color(float(value), heat_min, heat_max))

        zero_x = heat_rect.left() + ((0.0 - x_min) / max(1e-9, x_max - x_min)) * heat_rect.width()
        painter.setPen(QPen(QColor("#ffffff"), 3))
        painter.drawLine(int(zero_x), heat_rect.top(), int(zero_x), heat_rect.bottom())
        painter.setPen(QColor("#0f172a"))
        painter.drawRect(heat_rect)
        painter.drawText(heat_rect.left(), plot_rect.bottom() - 6, f"{x_min:.1f}s")
        painter.drawText(int(zero_x) - 8, plot_rect.bottom() - 6, "0")
        painter.drawText(heat_rect.right() - 28, plot_rect.bottom() - 6, f"{x_max:.1f}s")

        colorbar_rect = QRect(
            heat_rect.right() + colorbar_gap,
            heat_rect.top(),
            colorbar_width,
            heat_rect.height(),
        )
        for offset in range(colorbar_rect.height()):
            frac = 1.0 - (offset / max(1, colorbar_rect.height() - 1))
            value = heat_min + frac * (heat_max - heat_min)
            painter.fillRect(
                colorbar_rect.left(),
                colorbar_rect.top() + offset,
                colorbar_rect.width(),
                1,
                self._heat_color(value, heat_min, heat_max),
            )
        painter.setPen(QColor("#0f172a"))
        painter.drawRect(colorbar_rect)
        painter.drawText(
            QRect(colorbar_rect.right() + 4, colorbar_rect.top() - 8, 56, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{heat_max:.2g}",
        )
        painter.drawText(
            QRect(colorbar_rect.right() + 4, colorbar_rect.bottom() - 8, 56, 16),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            f"{heat_min:.2g}",
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        plot_rect = self.rect().adjusted(8, 8, -8, -8)
        if plot_rect.width() <= 10 or plot_rect.height() <= 10:
            return
        if not self._cell_payloads:
            painter.setPen(QColor("#64748b"))
            painter.drawText(plot_rect, Qt.AlignmentFlag.AlignCenter, self._placeholder_text)
            return
        if self._display_mode == "heat":
            self._render_heat_mode(painter, plot_rect)
        else:
            self._render_line_mode(painter, plot_rect)


class OnlineActivityWidget(QWidget):
    def __init__(self, scanimage_control: ScanImageControlWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.scanimage_control = scanimage_control
        self._suppress_refresh = False
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        controls_row = QHBoxLayout()
        self.condition_combo = QComboBox()
        self.auto_jump_checkbox = QCheckBox("Auto-jump to current condition")
        self.auto_jump_checkbox.setChecked(True)
        self.path_combo = QComboBox()
        self.channel_spin = QSpinBox()
        self.channel_spin.setRange(1, 8)
        self.roi_diameter_spin = QSpinBox()
        self.roi_diameter_spin.setRange(1, 512)
        self.pre_spin = QDoubleSpinBox()
        self.pre_spin.setRange(0.0, 60.0)
        self.pre_spin.setDecimals(2)
        self.pre_spin.setSingleStep(0.1)
        self.post_spin = QDoubleSpinBox()
        self.post_spin.setRange(0.1, 120.0)
        self.post_spin.setDecimals(2)
        self.post_spin.setSingleStep(0.1)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Scaled", "scaled")
        self.mode_combo.addItem("dF/F", "dff")
        self.display_combo = QComboBox()
        self.display_combo.addItem("All traces", "all")
        self.display_combo.addItem("Mean only", "mean")
        self.display_combo.addItem("Mean + shaded error", "mean_error")
        self.display_combo.addItem("Heat map", "heat")
        self.y_max_edit = QLineEdit()
        self.y_max_edit.setPlaceholderText("auto")
        self.y_max_edit.setMaximumWidth(90)
        controls_row.addWidget(QLabel("Condition"))
        controls_row.addWidget(self.condition_combo, 1)
        controls_row.addWidget(self.auto_jump_checkbox)
        controls_row.addWidget(QLabel("Path"))
        controls_row.addWidget(self.path_combo)
        controls_row.addWidget(QLabel("Channel"))
        controls_row.addWidget(self.channel_spin)
        controls_row.addWidget(QLabel("ROI diameter px"))
        controls_row.addWidget(self.roi_diameter_spin)
        controls_row.addWidget(QLabel("Pre s"))
        controls_row.addWidget(self.pre_spin)
        controls_row.addWidget(QLabel("Post s"))
        controls_row.addWidget(self.post_spin)
        controls_row.addWidget(QLabel("Normalization"))
        controls_row.addWidget(self.mode_combo)
        controls_row.addWidget(QLabel("Display"))
        controls_row.addWidget(self.display_combo)
        controls_row.addWidget(QLabel("Y max"))
        controls_row.addWidget(self.y_max_edit)
        layout.addLayout(controls_row)

        self.status_label = QLabel("Online Analysis disabled")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.plot_widget = MultiCellActivityPlotWidget()
        self.scroll_area.setWidget(self.plot_widget)
        layout.addWidget(self.scroll_area, 1)

        self.condition_combo.currentIndexChanged.connect(self.refresh)
        self.path_combo.currentIndexChanged.connect(self._push_settings)
        self.channel_spin.valueChanged.connect(self._push_settings)
        self.roi_diameter_spin.valueChanged.connect(self._push_settings)
        self.pre_spin.valueChanged.connect(self._push_settings)
        self.post_spin.valueChanged.connect(self._push_settings)
        self.mode_combo.currentIndexChanged.connect(self.refresh)
        self.display_combo.currentIndexChanged.connect(self.refresh)
        self.y_max_edit.textChanged.connect(self.refresh)

    def _selected_condition_index(self) -> int | None:
        data = self.condition_combo.currentData()
        return int(data) if data is not None else None

    def _push_settings(self) -> None:
        if self._suppress_refresh:
            return
        imaging_path = str(self.path_combo.currentData() or self.path_combo.currentText() or "").strip()
        self.scanimage_control.set_online_analysis_settings(
            imaging_path=imaging_path,
            channel=self.channel_spin.value(),
            roi_diameter_px=self.roi_diameter_spin.value(),
            pre_s=self.pre_spin.value(),
            post_s=self.post_spin.value(),
        )

    def refresh(self) -> None:
        selected_condition = self._selected_condition_index()
        snapshot = self.scanimage_control.get_online_analysis_snapshot(selected_condition)

        self._suppress_refresh = True
        try:
            current_condition_index = snapshot.get("current_condition_index")
            conditions = list(snapshot.get("conditions", []))
            available_paths = [str(path) for path in snapshot.get("available_imaging_paths", [])]
            requested_path = str(snapshot.get("requested_imaging_path", "")).strip()
            active_path = str(snapshot.get("imaging_path", "")).strip()
            desired_index = selected_condition
            if self.auto_jump_checkbox.isChecked() and current_condition_index is not None:
                desired_index = int(current_condition_index)
            elif selected_condition is None:
                snap_selected = snapshot.get("selected_condition_index")
                desired_index = int(snap_selected) if snap_selected is not None else None

            self.condition_combo.blockSignals(True)
            self.condition_combo.clear()
            for condition in conditions:
                label = str(condition.get("label", f"Condition {condition.get('index', '?')}"))
                supported = bool(condition.get("supported"))
                reason = str(condition.get("reason", "")).strip()
                if not supported and reason:
                    label = f"{label} [unsupported: {reason}]"
                self.condition_combo.addItem(label, int(condition["index"]))
            combo_index = self.condition_combo.findData(desired_index) if desired_index is not None else -1
            if combo_index >= 0:
                self.condition_combo.setCurrentIndex(combo_index)
            elif self.condition_combo.count():
                self.condition_combo.setCurrentIndex(0)
            self.condition_combo.blockSignals(False)

            selected_path = requested_path or active_path
            self.path_combo.blockSignals(True)
            self.path_combo.clear()
            for path_name in available_paths:
                self.path_combo.addItem(path_name, path_name)
            path_index = self.path_combo.findData(selected_path) if selected_path else -1
            if path_index >= 0:
                self.path_combo.setCurrentIndex(path_index)
            elif self.path_combo.count():
                self.path_combo.setCurrentIndex(0)
            self.path_combo.blockSignals(False)

            self.channel_spin.setValue(int(snapshot.get("channel", 1)))
            self.roi_diameter_spin.setValue(int(snapshot.get("roi_diameter_px", 11)))
            self.pre_spin.setValue(float(snapshot.get("pre_s", 1.0)))
            self.post_spin.setValue(float(snapshot.get("post_s", 3.0)))
        finally:
            self._suppress_refresh = False

        enabled = bool(snapshot.get("enabled"))
        configured = bool(snapshot.get("configured"))
        status_bits = []
        if not enabled:
            status_bits.append("Online Analysis disabled")
        else:
            status_bits.append("Online Analysis enabled")
        exp_id = str(snapshot.get("exp_id", "")).strip()
        imaging_path = str(snapshot.get("imaging_path", "")).strip()
        current_trial_stimulus_id = snapshot.get("current_trial_stimulus_id")
        if exp_id:
            status_bits.append(f"expID={exp_id}")
        if imaging_path:
            status_bits.append(f"path={imaging_path}")
        if current_trial_stimulus_id is not None:
            status_bits.append(f"current trial type ID={current_trial_stimulus_id}")
        if configured:
            status_bits.append("configured")
        else:
            status_bits.append("not configured")
        last_error = str(snapshot.get("last_error", "")).strip()
        if last_error:
            status_bits.append(f"error: {last_error}")
        self.status_label.setText(" | ".join(status_bits))

        cells = list(snapshot.get("cells", []))
        normalization_mode = str(self.mode_combo.currentData() or "scaled")
        display_mode = str(self.display_combo.currentData() or "all")
        pre_s = float(snapshot.get("pre_s", 1.0))
        post_s = float(snapshot.get("post_s", 3.0))
        y_max_override = None
        raw_y_max = self.y_max_edit.text().strip()
        if raw_y_max:
            try:
                y_max_override = float(raw_y_max)
            except ValueError:
                y_max_override = None
        if not enabled:
            self.plot_widget.set_plot_data(
                [],
                normalization_mode=normalization_mode,
                display_mode=display_mode,
                pre_s=pre_s,
                post_s=post_s,
                y_max_override=None,
                placeholder_text="Enable Online Analysis next to Save Schema to activate live ROI plotting.",
            )
            return
        self.plot_widget.set_plot_data(
            cells,
            normalization_mode=normalization_mode,
            display_mode=display_mode,
            pre_s=pre_s,
            post_s=post_s,
            y_max_override=y_max_override,
            placeholder_text="No plottable cells for the selected condition yet.",
        )


def _parse_cell_id_list(raw_text: str) -> list[int]:
    values: list[int] = []
    for token in raw_text.replace("\n", ",").split(","):
        stripped = token.strip()
        if not stripped:
            continue
        if ":" in stripped:
            parts = [part.strip() for part in stripped.split(":")]
            if len(parts) != 2 or not parts[0] or not parts[1]:
                raise ValueError(
                    f"Invalid range '{stripped}'. Use inclusive ranges like '1:10'."
                )
            try:
                start = int(parts[0])
                end = int(parts[1])
            except ValueError as exc:
                raise ValueError(
                    f"Invalid range '{stripped}'. Use inclusive ranges like '1:10'."
                ) from exc
            if start < 0 or end < 0:
                raise ValueError("Cell IDs must be >= 0.")
            if end < start:
                raise ValueError(
                    f"Invalid range '{stripped}'. Range end must be >= start."
                )
            values.extend(range(start, end + 1))
            continue
        try:
            value = int(stripped)
        except ValueError as exc:
            raise ValueError(
                f"Invalid cell ID '{stripped}'. Use integers and inclusive ranges like '1:10,13,15:20'."
            ) from exc
        if value < 0:
            raise ValueError("Cell IDs must be >= 0.")
        values.append(value)
    if not values:
        raise ValueError("Enter at least one processed cell ID.")
    return values


def _default_origin_user_id() -> str:
    return getpass.getuser() if sys.platform.startswith("linux") else ""


def _origin_user_options(saved_user_id: str = "") -> list[str]:
    options: list[str] = []
    if sys.platform.startswith("linux"):
        current_user = getpass.getuser()
        options.append(current_user)
        for home_dir in sorted(Path("/home").iterdir(), key=lambda path: path.name.lower()):
            if not home_dir.is_dir():
                continue
            user_id = home_dir.name
            if user_id in options:
                continue
            try:
                has_data_root = (
                    (home_dir / "data" / "Repository").is_dir()
                    or (home_dir / "data" / "Local_Repository").is_dir()
                    or (home_dir / "data" / "tif_meso" / "processed_repository").is_dir()
                )
            except PermissionError:
                continue
            if has_data_root:
                options.append(user_id)
    if saved_user_id and saved_user_id not in options:
        options.append(saved_user_id)
    if not options:
        options.append("")
    return options


def _scanfield_plane_index(scanfields: tuple[ScanfieldChoice, ...], scanfield_index: int) -> int:
    matched = next((scanfield for scanfield in scanfields if scanfield.index == scanfield_index), None)
    if matched is None:
        return max(scanfield_index - 1, 0)
    roi_scanfields = [scanfield for scanfield in scanfields if scanfield.roi_folder_name == matched.roi_folder_name]
    ordered = sorted(roi_scanfields, key=lambda scanfield: scanfield.index)
    for plane_index, scanfield in enumerate(ordered):
        if scanfield.index == matched.index:
            return plane_index
    return max(scanfield_index - 1, 0)


def _format_origin_from_scanfield(
    imaging_path: str,
    scanfields: tuple[ScanfieldChoice, ...],
    scanfield_index: int,
    x_px: float,
    y_px: float,
    z_um: float | None = None,
) -> str:
    matched = next((scanfield for scanfield in scanfields if scanfield.index == scanfield_index), None)
    if matched is None:
        return ""
    plane_index = _scanfield_plane_index(scanfields, scanfield_index)
    display_z_um = float(z_um) if z_um is not None else float(matched.z_um)
    return (
        f"{imaging_path} {matched.roi_folder_name} plane{plane_index} "
        f"x={x_px:.1f} y={y_px:.1f} z={display_z_um:g}"
    )


def _step_end_s(step: SequenceStep, patterns: dict[str, Pattern]) -> float:
    return float(step.start_s) + float(patterns[step.pattern].duration_s)


def _sorted_steps(steps: list[SequenceStep]) -> list[SequenceStep]:
    return sorted(steps, key=lambda step: step.start_s)


def _sequence_overlap_pairs(sequence: Sequence, patterns: dict[str, Pattern]) -> list[tuple[SequenceStep, SequenceStep]]:
    overlaps: list[tuple[SequenceStep, SequenceStep]] = []
    last_step: SequenceStep | None = None
    last_end_s: float | None = None
    for step in _sorted_steps(sequence.steps):
        if step.pattern not in patterns:
            continue
        if last_step is not None and last_end_s is not None and step.start_s < last_end_s:
            overlaps.append((last_step, step))
        last_step = step
        last_end_s = _step_end_s(step, patterns)
    return overlaps


def _shift_steps_to_avoid_overlap(steps: list[SequenceStep], patterns: dict[str, Pattern]) -> list[SequenceStep]:
    shifted: list[SequenceStep] = []
    last_end_s: float | None = None
    for step in _sorted_steps([SequenceStep(pattern=s.pattern, start_s=s.start_s) for s in steps]):
        if step.pattern not in patterns:
            shifted.append(step)
            continue
        if last_end_s is not None and step.start_s < last_end_s:
            step.start_s = last_end_s
        last_end_s = _step_end_s(step, patterns)
        shifted.append(step)
    return shifted


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _config_path() -> Path:
    return _repo_root() / "config.ini"


def _load_save_root() -> Path:
    config = configparser.ConfigParser()
    config.read(_config_path())
    raw_root = config.get("paths", "save_root", fallback="./data")
    return _resolve_config_path(raw_root)


def _windows_schema_root() -> str:
    return r"\\ar-lab-nas1\DataServer\opto_schemas"


def _ubuntu_schema_root() -> Path:
    return Path("/mnt/nas2/opto_schemas")


def _roi_coordinate_import_enabled() -> bool:
    return sys.platform.startswith("linux")


def _normalize_unc_path(raw_path: str) -> str:
    return raw_path.replace("/", "\\").rstrip("\\").lower()


def _resolve_config_path(raw_path: str) -> Path:
    normalized = _normalize_unc_path(raw_path)
    if sys.platform.startswith("linux") and normalized == _normalize_unc_path(_windows_schema_root()):
        return _ubuntu_schema_root()
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


class ImagingPixelImportDialog(QDialog):
    def __init__(self, repo_root: Path, default_exp_id: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.repo_root = repo_root
        self.bundle = None
        self._resolved_cell_note = ""
        self._resolved_origin = ""
        self._resolved_processed_cell_id: int | None = None
        self._resolved_imaging_path = ""
        self._resolved_roi_folder_name = ""
        self._resolved_plane_index: int | None = None
        self.setWindowTitle("Add Imaging Pixel")
        self._build_ui()
        default_exp_id = default_exp_id.strip()
        if default_exp_id:
            self.exp_id_edit.setText(default_exp_id)
            self.load_scanfields(show_error_dialog=False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.exp_id_edit = QLineEdit()
        self.imaging_path_combo = QComboBox()
        self.imaging_path_combo.addItems(["P1"])
        self.scanfield_combo = QComboBox()
        self.scanfield_combo.setEnabled(False)
        self.x_spin = QDoubleSpinBox()
        self.x_spin.setRange(0.0, 100000.0)
        self.x_spin.setDecimals(3)
        self.y_spin = QDoubleSpinBox()
        self.y_spin.setRange(0.0, 100000.0)
        self.y_spin.setDecimals(3)
        self.cell_id_spin = QSpinBox()
        self.cell_id_spin.setRange(0, 1_000_000)
        self.label_edit = QLineEdit()
        self.info_label = QLabel("Enter an experiment ID, load scanfields, then supply 0-based pixel X/Y.")
        self.info_label.setWordWrap(True)
        self.load_btn = QPushButton("Load Scanfields")
        self.resolve_cell_btn = QPushButton("Resolve Processed Cell ID")

        form.addRow("Experiment ID", self.exp_id_edit)
        form.addRow("Imaging Path", self.imaging_path_combo)
        form.addRow("Scanfield", self.scanfield_combo)
        form.addRow("Processed Cell ID", self.cell_id_spin)
        form.addRow("Pixel X (0-based)", self.x_spin)
        form.addRow("Pixel Y (0-based)", self.y_spin)
        form.addRow("Cell Label", self.label_edit)
        form.addRow("", self.load_btn)
        form.addRow("", self.resolve_cell_btn)
        form.addRow("Info", self.info_label)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.load_btn.clicked.connect(self.load_scanfields)
        self.resolve_cell_btn.clicked.connect(self.resolve_processed_cell)
        self.exp_id_edit.textChanged.connect(self._clear_loaded_scanfields)

    def _clear_loaded_scanfields(self) -> None:
        self.bundle = None
        self._resolved_cell_note = ""
        self._resolved_origin = ""
        self._resolved_processed_cell_id = None
        self._resolved_imaging_path = ""
        self._resolved_roi_folder_name = ""
        self._resolved_plane_index = None
        self.scanfield_combo.clear()
        self.scanfield_combo.setEnabled(False)

    def load_scanfields(self, show_error_dialog: bool = True) -> None:
        exp_id = self.exp_id_edit.text().strip()
        if not exp_id:
            QMessageBox.warning(self, "Missing experiment ID", "Enter an experiment ID first.")
            return
        try:
            bundle = list_imaging_scanfields(
                self.repo_root,
                exp_id,
                imaging_path=self.imaging_path_combo.currentText().strip() or "P1",
            )
        except Exception as exc:
            self.info_label.setText(str(exc))
            if show_error_dialog:
                QMessageBox.warning(self, "Failed to load imaging metadata", str(exc))
            return

        self.bundle = bundle
        self.scanfield_combo.clear()
        for scanfield in bundle.scanfields:
            self.scanfield_combo.addItem(scanfield.label, scanfield.index)
        self.scanfield_combo.setEnabled(True)
        note = bundle.note if bundle.note else ""
        pieces = [f"Source: {bundle.source}", f"Experiment dir: {bundle.exp_dir}"]
        if note:
            pieces.append(note)
        if self._resolved_cell_note:
            pieces.append(self._resolved_cell_note)
        self.info_label.setText("\n".join(pieces))

        if not self.label_edit.text().strip():
            self.label_edit.setText(f"img_{exp_id}_cell")

    def resolve_processed_cell(self) -> None:
        exp_id = self.exp_id_edit.text().strip()
        if not exp_id:
            QMessageBox.warning(self, "Missing experiment ID", "Enter an experiment ID first.")
            return
        try:
            resolved = resolve_processed_cell_to_imaging_pixel(
                self.repo_root,
                exp_id,
                processed_cell_id=self.cell_id_spin.value(),
                default_imaging_path=self.imaging_path_combo.currentText().strip() or "P1",
            )
            self.imaging_path_combo.setCurrentText(resolved.imaging_path)
            self.load_scanfields(show_error_dialog=True)
            if self.bundle is None:
                raise ValueError("Scanfields could not be loaded after resolving the processed cell.")
            combo_index = self.scanfield_combo.findData(resolved.scanfield_index)
            if combo_index < 0:
                raise ValueError(
                    f"Resolved scanfield index {resolved.scanfield_index} was not available in the loaded scanfield list."
                )
            self.scanfield_combo.setCurrentIndex(combo_index)
            self.x_spin.setValue(resolved.x_px)
            self.y_spin.setValue(resolved.y_px)
            self._resolved_cell_note = resolved.note
            self._resolved_origin = resolved.origin
            self._resolved_processed_cell_id = resolved.processed_cell_id
            self._resolved_imaging_path = resolved.imaging_path
            self._resolved_roi_folder_name = resolved.roi_folder_name
            self._resolved_plane_index = resolved.plane_index
            pieces = [f"Source: {self.bundle.source}", f"Experiment dir: {self.bundle.exp_dir}"]
            if self.bundle.note:
                pieces.append(self.bundle.note)
            pieces.append(resolved.note)
            self.info_label.setText("\n".join(pieces))
            if not self.label_edit.text().strip():
                self.label_edit.setText(f"cell_{resolved.processed_cell_id}")
        except Exception as exc:
            self._resolved_cell_note = ""
            self._resolved_origin = ""
            self._resolved_processed_cell_id = None
            self._resolved_imaging_path = ""
            self._resolved_roi_folder_name = ""
            self._resolved_plane_index = None
            self.info_label.setText(str(exc))
            QMessageBox.warning(self, "Failed to resolve processed cell ID", str(exc))

    def result_data(self) -> tuple[str, CellSpec, str, str]:
        exp_id = self.exp_id_edit.text().strip()
        if not exp_id:
            raise ValueError("Experiment ID is required.")
        if self.bundle is None or self.scanfield_combo.count() == 0:
            raise ValueError("Load scanfields before importing a coordinate.")

        scanfield_index = int(self.scanfield_combo.currentData())
        result = convert_imaging_pixel_to_pattern_coords(
            self.repo_root,
            exp_id,
            scanfield_index=scanfield_index,
            x_px=self.x_spin.value(),
            y_px=self.y_spin.value(),
            imaging_path=self.imaging_path_combo.currentText().strip() or "P1",
        )
        matched_scanfield = self.bundle.scanfields[scanfield_index - 1]
        imaging_path = self.imaging_path_combo.currentText().strip() or "P1"
        plane_index = _scanfield_plane_index(self.bundle.scanfields, scanfield_index)
        label = self.label_edit.text().strip() or f"img_{exp_id}_{scanfield_index}"
        origin = self._resolved_origin or _format_origin_from_scanfield(
            imaging_path,
            self.bundle.scanfields,
            scanfield_index,
            self.x_spin.value(),
            self.y_spin.value(),
            result.z_um,
        )
        cell = CellSpec(
            label=label,
            x=result.x_um,
            y=result.y_um,
            z=result.z_um,
            origin=origin,
            origin_exp_id=exp_id,
            origin_user_id=self.parent().project.origin_user_id if hasattr(self.parent(), "project") and self._resolved_processed_cell_id is not None else "",
            origin_processed_cell_id=self._resolved_processed_cell_id,
            origin_imaging_path=self._resolved_imaging_path or imaging_path,
            origin_roi_folder_name=self._resolved_roi_folder_name or matched_scanfield.roi_folder_name,
            origin_plane_index=self._resolved_plane_index if self._resolved_plane_index is not None else plane_index,
            origin_z_um=result.plane_z_um,
        )
        return exp_id, cell, result.source, result.note


class ProcessedCellGroupImportDialog(QDialog):
    def __init__(
        self,
        repo_root: Path,
        default_exp_id: str = "",
        default_user_id: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.repo_root = repo_root
        self.default_user_id = default_user_id.strip()
        self._cells: list[CellSpec] = []
        self._details = ""
        self.setWindowTitle("Add Cells by Cell ID")
        self._build_ui()
        default_exp_id = default_exp_id.strip()
        if default_exp_id:
            self.exp_id_edit.setText(default_exp_id)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.exp_id_edit = QLineEdit()
        self.cell_ids_edit = QLineEdit()
        self.cell_ids_edit.setPlaceholderText("e.g. 0, 3, 4, 12")
        self.label_prefix_edit = QLineEdit("cell_")
        self.info_label = QLabel("Enter comma-separated processed cell IDs, then click OK.")
        self.info_label.setWordWrap(True)

        form.addRow("Experiment ID", self.exp_id_edit)
        form.addRow("Cell IDs", self.cell_ids_edit)
        form.addRow("Label Prefix", self.label_prefix_edit)
        form.addRow("Info", self.info_label)
        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_with_resolution)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.exp_id_edit.textChanged.connect(self._clear_results)
        self.cell_ids_edit.textChanged.connect(self._clear_results)
        self.label_prefix_edit.textChanged.connect(self._clear_results)

    def _clear_results(self) -> None:
        self._cells = []
        self._details = ""

    def _accept_with_resolution(self) -> None:
        self.resolve_cells()
        if self._cells:
            self.accept()

    def resolve_cells(self) -> None:
        exp_id = self.exp_id_edit.text().strip()
        if not exp_id:
            QMessageBox.warning(self, "Missing experiment ID", "Enter an experiment ID first.")
            return
        try:
            processed_cell_ids = _parse_cell_id_list(self.cell_ids_edit.text())
        except ValueError as exc:
            self.info_label.setText(str(exc))
            QMessageBox.warning(self, "Invalid cell IDs", str(exc))
            return

        label_prefix = self.label_prefix_edit.text()
        resolved_cells: list[CellSpec] = []
        detail_lines: list[str] = []
        try:
            for processed_cell_id in processed_cell_ids:
                resolved = resolve_processed_cell_to_imaging_pixel(
                    self.repo_root,
                    exp_id,
                    processed_cell_id=processed_cell_id,
                    user_id=self.default_user_id or None,
                    default_imaging_path="P1",
                )
                converted = convert_imaging_pixel_to_pattern_coords(
                    self.repo_root,
                    exp_id,
                    scanfield_index=resolved.scanfield_index,
                    x_px=resolved.x_px,
                    y_px=resolved.y_px,
                    imaging_path=resolved.imaging_path,
                )
                resolved_cells.append(
                    CellSpec(
                        label=f"{label_prefix}{processed_cell_id}",
                        x=converted.x_um,
                        y=converted.y_um,
                        z=converted.z_um,
                        origin=resolved.origin,
                        origin_exp_id=exp_id,
                        origin_user_id=self.default_user_id or "",
                        origin_processed_cell_id=processed_cell_id,
                        origin_imaging_path=resolved.imaging_path,
                        origin_roi_folder_name=resolved.roi_folder_name,
                        origin_plane_index=resolved.plane_index,
                        origin_z_um=resolved.plane_z_um,
                    )
                )
                detail_lines.append(f"cell {processed_cell_id}: {resolved.origin}")
        except Exception as exc:
            self._cells = []
            self._details = ""
            self.info_label.setText(str(exc))
            QMessageBox.warning(self, "Failed to resolve processed cell IDs", str(exc))
            return

        self._cells = resolved_cells
        self._details = "\n".join(detail_lines)
        self.info_label.setText(self._details or "Resolved 0 cells.")

    def result_data(self) -> tuple[str, list[CellSpec], str]:
        exp_id = self.exp_id_edit.text().strip()
        if not exp_id:
            raise ValueError("Experiment ID is required.")
        if not self._cells:
            raise ValueError("Resolve at least one processed cell ID before importing.")
        return exp_id, list(self._cells), self._details

class AddCellsFromFovDialog(QDialog):
    def __init__(
        self,
        repo_root: Path,
        default_exp_id: str = "",
        default_user_id: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.repo_root = repo_root
        self.default_user_id = default_user_id.strip()
        self._groups: tuple[ProcessedFovGroup, ...] = ()
        self._selected_cells: dict[int, CellSpec] = {}
        self.setWindowTitle("Add from FOV")
        self._build_ui()
        if default_exp_id.strip():
            self.exp_id_edit.setText(default_exp_id.strip())
            self._reload_groups(show_error_dialog=False)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        controls_box = QGroupBox("Processed FOV")
        controls_layout = QVBoxLayout(controls_box)
        top_row = QHBoxLayout()
        self.exp_id_edit = QLineEdit()
        self.channel_combo = QComboBox()
        self.path_combo = QComboBox()
        self.roi_combo = QComboBox()
        self.plane_combo = QComboBox()
        self.info_label = QLabel("Choose a processed experiment and FOV, then click cells in either image.")
        self.info_label.setWordWrap(True)

        top_row.addWidget(QLabel("Experiment ID"))
        top_row.addWidget(self.exp_id_edit, 2)
        top_row.addWidget(QLabel("Path"))
        top_row.addWidget(self.path_combo)
        top_row.addWidget(QLabel("Channel"))
        top_row.addWidget(self.channel_combo)
        top_row.addWidget(QLabel("ROI"))
        top_row.addWidget(self.roi_combo)
        top_row.addWidget(QLabel("Plane"))
        top_row.addWidget(self.plane_combo)
        top_row.addStretch(1)
        controls_layout.addLayout(top_row)
        controls_layout.addWidget(self.info_label)
        layout.addWidget(controls_box)

        images_row = QHBoxLayout()
        left_panel = QVBoxLayout()
        left_panel.addWidget(QLabel("ROI masks"))
        self.roi_map_label = ClickableImageLabel("No ROI map loaded.")
        left_panel.addWidget(self.roi_map_label, 1)
        right_panel = QVBoxLayout()
        right_panel.addWidget(QLabel("Mean FOV"))
        self.mean_image_label = ClickableImageLabel("No mean image loaded.")
        right_panel.addWidget(self.mean_image_label, 1)
        images_row.addLayout(left_panel, 1)
        images_row.addLayout(right_panel, 1)
        layout.addLayout(images_row, 1)

        selected_box = QGroupBox("Selected cells")
        selected_layout = QVBoxLayout(selected_box)
        self.selected_table = QTableWidget(0, 6)
        self.selected_table.setHorizontalHeaderLabels(["Label", "X", "Y", "Z", "Power scale", "Origin"])
        self.selected_table.horizontalHeader().setStretchLastSection(True)
        self.selected_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        selected_layout.addWidget(self.selected_table)
        selected_buttons = QHBoxLayout()
        self.remove_selected_btn = QPushButton("Remove Selected")
        selected_buttons.addWidget(self.remove_selected_btn)
        selected_buttons.addStretch(1)
        selected_layout.addLayout(selected_buttons)
        layout.addWidget(selected_box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_ready)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.exp_id_edit.textChanged.connect(self._reload_groups)
        self.channel_combo.currentIndexChanged.connect(self._reload_groups_for_channel)
        self.path_combo.currentIndexChanged.connect(self._refresh_roi_options)
        self.roi_combo.currentIndexChanged.connect(self._refresh_plane_options)
        self.plane_combo.currentIndexChanged.connect(self._refresh_images)
        self.roi_map_label.image_clicked.connect(self._handle_image_click)
        self.mean_image_label.image_clicked.connect(self._handle_image_click)
        self.remove_selected_btn.clicked.connect(self._remove_selected_cell)
        self.resize(1400, 900)

    def _accept_if_ready(self) -> None:
        if not self._selected_cells:
            QMessageBox.warning(self, "No cells selected", "Select at least one cell before clicking OK.")
            return
        self.accept()

    def _reload_groups(self, show_error_dialog: bool = True) -> None:
        exp_id = self.exp_id_edit.text().strip()
        self._groups = ()
        self.channel_combo.blockSignals(True)
        self.channel_combo.clear()
        self.channel_combo.blockSignals(False)
        self.path_combo.clear()
        self.roi_combo.clear()
        self.plane_combo.clear()
        self.roi_map_label.set_image(None, None, "No ROI map loaded.")
        self.mean_image_label.set_image(None, None, "No mean image loaded.")
        if not exp_id:
            self.info_label.setText("Enter an experiment ID to load processed FOVs.")
            return
        try:
            channels = list_processed_channels(exp_id, user_id=self.default_user_id or None)
            if not channels:
                resolved_user_id = (self.default_user_id or getpass.getuser()).strip()
                raise FileNotFoundError(
                    f"No processed s2p_ch*.pickle files were found for '{exp_id}' from user '{resolved_user_id}'."
                )
            current_channel = self.channel_combo.currentData()
            self.channel_combo.blockSignals(True)
            self.channel_combo.clear()
            for channel in channels:
                self.channel_combo.addItem(f"ch{channel}", channel)
            channel_index = self.channel_combo.findData(current_channel)
            self.channel_combo.setCurrentIndex(channel_index if channel_index >= 0 else 0)
            self.channel_combo.blockSignals(False)
            self._reload_groups_for_channel(show_error_dialog=show_error_dialog)
        except Exception as exc:
            self.info_label.setText(str(exc))
            if show_error_dialog:
                QMessageBox.warning(self, "Failed to load processed FOVs", str(exc))

    def _reload_groups_for_channel(self, _index: int | None = None, show_error_dialog: bool = True) -> None:
        exp_id = self.exp_id_edit.text().strip()
        channel = self.channel_combo.currentData()
        self._groups = ()
        self.path_combo.clear()
        self.roi_combo.clear()
        self.plane_combo.clear()
        self.roi_map_label.set_image(None, None, "No ROI map loaded.")
        self.mean_image_label.set_image(None, None, "No mean image loaded.")
        if not exp_id or channel is None:
            return
        try:
            self._groups = list_processed_fov_groups(
                self.repo_root,
                exp_id,
                user_id=self.default_user_id or None,
                channel=int(channel),
            )
            path_names = sorted({group.imaging_path for group in self._groups})
            self.path_combo.blockSignals(True)
            self.path_combo.clear()
            for path_name in path_names:
                self.path_combo.addItem(path_name)
            self.path_combo.blockSignals(False)
            self._refresh_roi_options()
            self.info_label.setText(f"Loaded {len(self._groups)} processed FOV group(s) for {exp_id}.")
        except Exception as exc:
            self.info_label.setText(str(exc))
            if show_error_dialog:
                QMessageBox.warning(self, "Failed to load processed FOVs", str(exc))

    def _refresh_roi_options(self) -> None:
        path_name = self.path_combo.currentText().strip()
        groups = [group for group in self._groups if group.imaging_path == path_name] if path_name else []
        roi_names = sorted({group.roi_folder_name for group in groups})
        self.roi_combo.blockSignals(True)
        self.roi_combo.clear()
        for roi_name in roi_names:
            self.roi_combo.addItem(roi_name)
        self.roi_combo.blockSignals(False)
        self._refresh_plane_options()

    def _refresh_plane_options(self) -> None:
        candidates = self._current_group_candidates()
        self.plane_combo.blockSignals(True)
        self.plane_combo.clear()
        for group in candidates:
            self.plane_combo.addItem(
                f"plane{group.plane_index} (z={group.z_um:.1f}, true z {group.true_z_start_um:.1f}:{group.true_z_end_um:.1f})",
                group.plane_index,
            )
        self.plane_combo.blockSignals(False)
        self._refresh_images()

    def _current_group_candidates(self) -> list[ProcessedFovGroup]:
        path_name = self.path_combo.currentText().strip()
        roi_name = self.roi_combo.currentText().strip()
        return [
            group
            for group in self._groups
            if group.imaging_path == path_name and group.roi_folder_name == roi_name
        ]

    def _current_group(self) -> ProcessedFovGroup | None:
        plane_index = self.plane_combo.currentData()
        for group in self._current_group_candidates():
            if group.plane_index == plane_index:
                return group
        return None

    def _refresh_images(self) -> None:
        group = self._current_group()
        if group is None:
            self.roi_map_label.set_image(None, None, "No ROI map loaded.")
            self.mean_image_label.set_image(None, None, "No mean image loaded.")
            return
        self.roi_map_label.set_image(
            self._build_roi_map_pixmap(group.mean_image, group.roi_map),
            group.mean_image.shape,
        )
        self.mean_image_label.set_image(
            self._build_mean_image_pixmap(group.mean_image),
            group.mean_image.shape,
        )
        self.info_label.setText(f"{group.title} | {len(group.processed_cell_ids)} cells")

    def _handle_image_click(self, x_px: int, y_px: int) -> None:
        group = self._current_group()
        if group is None:
            return
        if x_px < 0 or y_px < 0 or y_px >= group.roi_map.shape[0] or x_px >= group.roi_map.shape[1]:
            return
        label_value = int(round(float(group.roi_map[y_px, x_px])))
        if label_value <= 0:
            self.info_label.setText(f"{group.title} | No cell at x={x_px}, y={y_px}")
            return
        local_index = label_value - 1
        if local_index < 0 or local_index >= len(group.processed_cell_ids):
            self.info_label.setText(f"{group.title} | ROI label {label_value} did not map to a processed cell.")
            return
        processed_cell_id = int(group.processed_cell_ids[local_index])
        if processed_cell_id in self._selected_cells:
            self.info_label.setText(f"{group.title} | cell {processed_cell_id} already selected")
            return
        try:
            resolved = resolve_processed_cell_to_imaging_pixel(
                self.repo_root,
                group.exp_id,
                processed_cell_id=processed_cell_id,
                user_id=group.user_id,
                channel=group.channel,
                default_imaging_path=group.imaging_path,
            )
            converted = convert_imaging_pixel_to_pattern_coords(
                self.repo_root,
                group.exp_id,
                scanfield_index=resolved.scanfield_index,
                x_px=resolved.x_px,
                y_px=resolved.y_px,
                imaging_path=resolved.imaging_path,
            )
            cell = CellSpec(
                label=f"cell_{processed_cell_id}",
                x=converted.x_um,
                y=converted.y_um,
                z=converted.z_um,
                origin=resolved.origin,
                origin_exp_id=group.exp_id,
                origin_user_id=group.user_id,
                origin_processed_cell_id=processed_cell_id,
                origin_imaging_path=resolved.imaging_path,
                origin_roi_folder_name=resolved.roi_folder_name,
                origin_plane_index=resolved.plane_index,
                origin_z_um=resolved.plane_z_um,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Failed to resolve clicked cell", str(exc))
            return
        self._selected_cells[processed_cell_id] = cell
        self._append_selected_cell_row(cell)
        self.info_label.setText(f"{group.title} | added cell {processed_cell_id}: {resolved.origin}")

    def _append_selected_cell_row(self, cell: CellSpec) -> None:
        row = self.selected_table.rowCount()
        self.selected_table.insertRow(row)
        items = [
            _read_only_item(cell.label),
            _read_only_item(f"{cell.x:g}"),
            _read_only_item(f"{cell.y:g}"),
            _read_only_item(f"{cell.z:g}"),
            _read_only_item(f"{cell.power_scale:g}"),
            _read_only_item(cell.origin),
        ]
        for col, item in enumerate(items):
            if col == 5:
                item.setData(Qt.ItemDataRole.UserRole, _cell_origin_metadata(cell))
            item.setData(Qt.ItemDataRole.UserRole + 1, cell.origin_processed_cell_id)
            self.selected_table.setItem(row, col, item)
        self.selected_table.selectRow(row)

    def _remove_selected_cell(self) -> None:
        row = _selected_or_last_row(self.selected_table)
        if row is None:
            return
        processed_cell_id = None
        item = self.selected_table.item(row, 0)
        if item is not None:
            processed_cell_id = item.data(Qt.ItemDataRole.UserRole + 1)
        if isinstance(processed_cell_id, int):
            self._selected_cells.pop(processed_cell_id, None)
        self.selected_table.removeRow(row)

    def result_data(self) -> tuple[str, list[CellSpec]]:
        exp_id = self.exp_id_edit.text().strip()
        if not exp_id:
            raise ValueError("Experiment ID is required.")
        if not self._selected_cells:
            raise ValueError("Select at least one cell from the FOV before importing.")
        return exp_id, list(self._selected_cells.values())

    def _build_mean_image_pixmap(self, mean_image: np.ndarray) -> QPixmap:
        image = np.asarray(mean_image, dtype=float)
        finite = np.isfinite(image)
        if not np.any(finite):
            normalized = np.zeros_like(image, dtype=np.uint8)
        else:
            valid = image[finite]
            low = float(np.percentile(valid, 1))
            high = float(np.percentile(valid, 99))
            if high <= low:
                high = low + 1.0
            clipped = np.clip(image, low, high)
            normalized = np.round((clipped - low) / (high - low) * 255.0).astype(np.uint8)
        rgb = np.repeat(normalized[:, :, None], 3, axis=2)
        qimage = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        return QPixmap.fromImage(qimage)

    def _build_roi_map_pixmap(self, mean_image: np.ndarray, roi_map: np.ndarray) -> QPixmap:
        image = np.asarray(mean_image, dtype=float)
        labels = np.asarray(roi_map, dtype=int)
        finite = np.isfinite(image)
        if not np.any(finite):
            normalized = np.zeros_like(image, dtype=np.uint8)
        else:
            valid = image[finite]
            low = float(np.percentile(valid, 1))
            high = float(np.percentile(valid, 99))
            if high <= low:
                high = low + 1.0
            clipped = np.clip(image, low, high)
            normalized = np.round((clipped - low) / (high - low) * 255.0).astype(np.uint8)
        rgb = np.repeat(normalized[:, :, None], 3, axis=2).astype(np.uint8)
        for label_value in (int(value) for value in np.unique(labels) if int(value) > 0):
            mask = labels == label_value
            color = _stable_color(f"roi_{label_value}")
            overlay = np.array([color.red(), color.green(), color.blue()], dtype=float)
            rgb[mask] = np.round(rgb[mask].astype(float) * 0.45 + overlay * 0.55).astype(np.uint8)
            boundary = self._roi_boundary(mask)
            rgb[boundary, 0] = 255
            rgb[boundary, 1] = 255
            rgb[boundary, 2] = 255
        qimage = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        return QPixmap.fromImage(qimage)

    def _roi_boundary(self, mask: np.ndarray) -> np.ndarray:
        boundary = mask.copy()
        interior = mask.copy()
        interior[1:-1, 1:-1] = (
            mask[1:-1, 1:-1]
            & mask[:-2, 1:-1]
            & mask[2:, 1:-1]
            & mask[1:-1, :-2]
            & mask[1:-1, 2:]
            & mask[:-2, :-2]
            & mask[:-2, 2:]
            & mask[2:, :-2]
            & mask[2:, 2:]
        )
        boundary &= ~interior
        return boundary


class PatternEditor(QWidget):
    def __init__(
        self,
        project: ExperimentProject,
        repo_root: Path,
        on_dirty,
        on_commit=None,
        resolve_sequence_overlaps=None,
        on_live_commit=None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.project = project
        self.repo_root = repo_root
        self.on_dirty = on_dirty
        self.on_commit = on_commit
        self.resolve_sequence_overlaps = resolve_sequence_overlaps
        self.on_live_commit = on_live_commit
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

        self.cells_table = QTableWidget(0, 6)
        self.cells_table.setHorizontalHeaderLabels(["Label", "X", "Y", "Z", "Power scale", "Origin"])
        self.cells_table.horizontalHeader().setStretchLastSection(True)
        self.cells_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.cells_table.itemChanged.connect(self._on_form_changed)
        self.cells_table.itemDoubleClicked.connect(self._show_cell_roi_overlay)
        layout.addWidget(QLabel("Cells in this pattern"))
        layout.addWidget(self.cells_table)

        button_row = QHBoxLayout()
        self.add_row_btn = QPushButton("Add Cell")
        self.add_imaging_pixel_btn = QPushButton("Add Imaging Pixel")
        self.add_cells_by_id_btn = QPushButton("Add Cells by ID")
        self.add_from_fov_btn = QPushButton("Add from FOV")
        self.add_imaging_pixel_btn.setEnabled(_roi_coordinate_import_enabled())
        self.add_cells_by_id_btn.setEnabled(_roi_coordinate_import_enabled())
        self.add_from_fov_btn.setEnabled(_roi_coordinate_import_enabled())
        if not _roi_coordinate_import_enabled():
            self.add_imaging_pixel_btn.setToolTip("Imaging pixel ROI import is available on Ubuntu only.")
            self.add_cells_by_id_btn.setToolTip("Processed cell ROI import is available on Ubuntu only.")
            self.add_from_fov_btn.setToolTip("Processed FOV import is available on Ubuntu only.")
        self.remove_row_btn = QPushButton("Remove Cell")
        self.copy_btn = QPushButton("Copy Pattern")
        button_row.addWidget(self.add_row_btn)
        button_row.addWidget(self.add_imaging_pixel_btn)
        button_row.addWidget(self.add_cells_by_id_btn)
        button_row.addWidget(self.add_from_fov_btn)
        button_row.addWidget(self.remove_row_btn)
        button_row.addWidget(self.copy_btn)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        self.add_row_btn.clicked.connect(self.add_cell_row)
        self.add_imaging_pixel_btn.clicked.connect(self.add_imaging_pixel)
        self.add_cells_by_id_btn.clicked.connect(self.add_cells_by_id)
        self.add_from_fov_btn.clicked.connect(self.add_cells_from_fov)
        self.remove_row_btn.clicked.connect(self.remove_selected_cell_rows)
        self.copy_btn.clicked.connect(self.copy_current_pattern)
        self.clear_btn.clicked.connect(self.clear_form)
        self.name_edit.textChanged.connect(self._on_form_changed)
        self.duration_spin.valueChanged.connect(self._on_form_changed)
        self.freq_spin.valueChanged.connect(self._on_form_changed)
        self.duty_cycle_spin.valueChanged.connect(self._on_form_changed)
        self.power_spin.valueChanged.connect(self._on_form_changed)
        self.spiral_width_spin.valueChanged.connect(self._on_form_changed)
        self.spiral_height_spin.valueChanged.connect(self._on_form_changed)
        self.notes_edit.textChanged.connect(self._on_form_changed)

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
            _read_only_item(values.origin),
        ]
        for col, widget in enumerate(widgets):
            if col == 5:
                widget.setData(Qt.ItemDataRole.UserRole, _cell_origin_metadata(values))
            self.cells_table.setItem(row, col, widget)
        self.cells_table.selectRow(row)
        if not self._loading:
            self._on_form_changed()

    def add_imaging_pixel(self) -> None:
        if not _roi_coordinate_import_enabled():
            QMessageBox.information(self, "Unavailable on this platform", "Imaging pixel ROI import is available on Ubuntu only.")
            return
        dialog = ImagingPixelImportDialog(self.repo_root, self.project.origin_exp_id, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            exp_id, cell, source, note = dialog.result_data()
        except Exception as exc:
            QMessageBox.warning(self, "Failed to convert imaging coordinate", str(exc))
            return
        self.add_cell_row(cell)
        details = [f"Added cell from {exp_id}", f"Source: {source}"]
        if note:
            details.append(note)
        QMessageBox.information(self, "Imaging pixel imported", "\n".join(details))

    def add_cells_by_id(self) -> None:
        if not _roi_coordinate_import_enabled():
            QMessageBox.information(self, "Unavailable on this platform", "Processed cell ROI import is available on Ubuntu only.")
            return
        dialog = ProcessedCellGroupImportDialog(
            self.repo_root,
            self.project.origin_exp_id,
            self.project.origin_user_id,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            _exp_id, cells, _details = dialog.result_data()
        except Exception as exc:
            QMessageBox.warning(self, "Failed to resolve processed cell IDs", str(exc))
            return
        for cell in cells:
            self.add_cell_row(cell)

    def add_cells_from_fov(self) -> None:
        if not _roi_coordinate_import_enabled():
            QMessageBox.information(self, "Unavailable on this platform", "Processed FOV import is available on Ubuntu only.")
            return
        dialog = AddCellsFromFovDialog(
            self.repo_root,
            self.project.origin_exp_id,
            self.project.origin_user_id,
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            _exp_id, cells = dialog.result_data()
        except Exception as exc:
            QMessageBox.warning(self, "Failed to import cells from FOV", str(exc))
            return
        for cell in cells:
            self.add_cell_row(cell)

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
        if self.commit_current_pattern(silent=True) and self.on_live_commit is not None:
            self.on_live_commit()
        self.on_dirty()

    def _show_cell_roi_overlay(self, item: QTableWidgetItem) -> None:
        row = item.row()
        origin_item = self.cells_table.item(row, 5)
        metadata = origin_item.data(Qt.ItemDataRole.UserRole) if origin_item is not None else None
        if not isinstance(metadata, dict):
            QMessageBox.information(
                self,
                "ROI overlay unavailable",
                "This cell does not have structured processed-origin metadata. Re-import the cell from processed data to view its ROI overlay.",
            )
            return
        origin_exp_id = str(metadata.get("origin_exp_id") or "")
        origin_user_id = str(metadata.get("origin_user_id") or "")
        processed_cell_id = metadata.get("origin_processed_cell_id")
        if not origin_exp_id or processed_cell_id is None:
            QMessageBox.information(
                self,
                "ROI overlay unavailable",
                "This cell does not have structured processed-origin metadata. Re-import the cell from processed data to view its ROI overlay.",
            )
            return
        try:
            overlay = load_processed_cell_overlay(
                exp_id=origin_exp_id,
                processed_cell_id=int(processed_cell_id),
                user_id=origin_user_id or None,
                imaging_path=str(metadata.get("origin_imaging_path") or ""),
                roi_folder_name=str(metadata.get("origin_roi_folder_name") or ""),
                plane_index=int(metadata["origin_plane_index"]) if metadata.get("origin_plane_index") is not None else None,
                z_um=float(metadata["origin_z_um"]) if metadata.get("origin_z_um") is not None else None,
            )
        except Exception as exc:
            QMessageBox.warning(self, "Failed to load ROI overlay", str(exc))
            return
        pixmap = self._build_roi_overlay_pixmap(overlay.mean_image, overlay.roi_pixels)
        title = f"{self.cells_table.item(row, 0).text()} | {overlay.title}"
        dialog = RoiOverlayDialog(title, pixmap, self)
        dialog.exec()

    def _build_roi_overlay_pixmap(self, mean_image: np.ndarray, roi_pixels: np.ndarray) -> QPixmap:
        image = np.asarray(mean_image, dtype=float)
        if image.ndim != 2:
            raise ValueError(f"Expected a 2D mean image, got shape {image.shape}.")
        finite = np.isfinite(image)
        if not np.any(finite):
            normalized = np.zeros_like(image, dtype=np.uint8)
        else:
            valid = image[finite]
            low = float(np.percentile(valid, 1))
            high = float(np.percentile(valid, 99))
            if high <= low:
                high = low + 1.0
            clipped = np.clip(image, low, high)
            normalized = np.round((clipped - low) / (high - low) * 255.0).astype(np.uint8)
        rgb = np.repeat(normalized[:, :, None], 3, axis=2)
        mask = np.zeros(image.shape, dtype=bool)
        ypix, xpix = np.unravel_index(roi_pixels.astype(int), image.shape)
        mask[ypix, xpix] = True
        boundary = self._roi_boundary(mask)
        rgb[boundary, 0] = 255
        rgb[boundary, 1] = 64
        rgb[boundary, 2] = 64
        qimage = QImage(
            rgb.data,
            rgb.shape[1],
            rgb.shape[0],
            rgb.strides[0],
            QImage.Format.Format_RGB888,
        ).copy()
        return QPixmap.fromImage(qimage)

    def _roi_boundary(self, mask: np.ndarray) -> np.ndarray:
        boundary = mask.copy()
        interior = mask.copy()
        interior[1:-1, 1:-1] = (
            mask[1:-1, 1:-1]
            & mask[:-2, 1:-1]
            & mask[2:, 1:-1]
            & mask[1:-1, :-2]
            & mask[1:-1, 2:]
            & mask[:-2, :-2]
            & mask[:-2, 2:]
            & mask[2:, :-2]
            & mask[2:, 2:]
        )
        boundary &= ~interior
        return boundary

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
            origin_item = self.cells_table.item(row, 5)
            origin_meta = origin_item.data(Qt.ItemDataRole.UserRole) if origin_item is not None else {}
            if not isinstance(origin_meta, dict):
                origin_meta = {}
            cells.append(
                CellSpec(
                    label=item(0),
                    x=float(item(1)),
                    y=float(item(2)),
                    z=float(item(3)),
                    power_scale=float(item(4) or "1.0"),
                    origin=item(5),
                    origin_exp_id=str(origin_meta.get("origin_exp_id", "")),
                    origin_user_id=str(origin_meta.get("origin_user_id", "")),
                    origin_processed_cell_id=origin_meta.get("origin_processed_cell_id"),
                    origin_imaging_path=str(origin_meta.get("origin_imaging_path", "")),
                    origin_roi_folder_name=str(origin_meta.get("origin_roi_folder_name", "")),
                    origin_plane_index=origin_meta.get("origin_plane_index"),
                    origin_z_um=origin_meta.get("origin_z_um"),
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
        if not self.commit_current_pattern(silent=False):
            return False
        if self.resolve_sequence_overlaps is not None:
            return bool(self.resolve_sequence_overlaps())
        return True

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
    def __init__(self, project: ExperimentProject, preview: TimelinePreview, on_dirty, on_commit=None, on_live_commit=None, parent: QWidget | None = None):
        super().__init__(parent)
        self.project = project
        self.preview = preview
        self.on_dirty = on_dirty
        self.on_commit = on_commit
        self.on_live_commit = on_live_commit
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
        button_row.addWidget(QLabel("Pattern"))
        button_row.addWidget(self.pattern_select, 2)
        button_row.addWidget(QLabel("Start"))
        button_row.addWidget(self.start_spin)
        button_row.addWidget(self.add_step_btn)
        button_row.addWidget(self.remove_step_btn)
        button_row.addWidget(self.copy_btn)
        button_row.addStretch(1)
        editor_layout.addLayout(button_row)

        self.add_step_btn.clicked.connect(self.add_step)
        self.remove_step_btn.clicked.connect(self.remove_selected_step_rows)
        self.copy_btn.clicked.connect(self.copy_current_sequence)
        self.clear_btn.clicked.connect(self.clear_form)
        self.name_edit.textChanged.connect(self._on_form_changed)
        self.notes_edit.textChanged.connect(self._on_form_changed)
        self.start_spin.valueChanged.connect(self._on_form_changed)
        self.steps_table.itemChanged.connect(self._on_steps_table_item_changed)

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
            if col == 1:
                widget.setData(Qt.ItemDataRole.UserRole, float(step.start_s))
            else:
                widget.setFlags(widget.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.steps_table.setItem(row, col, widget)
        self.steps_table.selectRow(row)

    def add_step(self) -> None:
        pattern_name = self.pattern_select.currentText().strip()
        if not pattern_name:
            return
        start_s = self.start_spin.value()
        existing = self.gather_sequence(allow_partial=True)
        steps = [SequenceStep(pattern=step.pattern, start_s=step.start_s) for step in existing.steps]
        inserted_start_s = start_s
        insert_priority = 0
        overlapping_step = self._find_step_covering_time(steps, start_s)
        if overlapping_step is not None:
            pattern = self.project.patterns[overlapping_step.pattern]
            end_s = overlapping_step.start_s + pattern.duration_s
            prompt = QMessageBox(self)
            prompt.setIcon(QMessageBox.Icon.Question)
            prompt.setWindowTitle("Insert overlapping step")
            prompt.setText(
                f"New step at {start_s:.3f}s overlaps existing pattern '{overlapping_step.pattern}' "
                f"({overlapping_step.start_s:.3f}s to {end_s:.3f}s)."
            )
            before_btn = prompt.addButton("Insert Before", QMessageBox.ButtonRole.AcceptRole)
            after_btn = prompt.addButton("Insert After", QMessageBox.ButtonRole.ActionRole)
            cancel_btn = prompt.addButton(QMessageBox.StandardButton.Cancel)
            prompt.setDefaultButton(before_btn)
            prompt.exec()
            clicked = prompt.clickedButton()
            if clicked == cancel_btn:
                return
            if clicked == before_btn:
                inserted_start_s = overlapping_step.start_s
                insert_priority = -1
            else:
                inserted_start_s = end_s
                insert_priority = 0
        steps = self._insert_step_with_shift(
            steps,
            SequenceStep(pattern=pattern_name, start_s=inserted_start_s),
            insert_priority=insert_priority,
        )
        self._loading = True
        self._set_sequence_steps(steps)
        self._loading = False
        if self.commit_current_sequence(silent=True) and self.on_live_commit is not None:
            self.on_live_commit()
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
        if self.commit_current_sequence(silent=True) and self.on_live_commit is not None:
            self.on_live_commit()
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
        if self.commit_current_sequence(silent=True) and self.on_live_commit is not None:
            self.on_live_commit()
        self.on_dirty()

    def _on_steps_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        if item.column() != 1:
            self._on_form_changed()
            return
        old_start_s = item.data(Qt.ItemDataRole.UserRole)
        try:
            start_s = float(item.text())
        except ValueError:
            QMessageBox.warning(self, "Invalid start time", "Sequence step start time must be numeric.")
            self._restore_step_start(item, old_start_s)
            return
        if start_s < 0:
            QMessageBox.warning(self, "Invalid start time", "Sequence step start time cannot be negative.")
            self._restore_step_start(item, old_start_s)
            return
        grid_ms = SCHEMA_TIME_QUANTUM_S * 1000.0
        snapped_steps = round(start_s / SCHEMA_TIME_QUANTUM_S)
        if abs(start_s - snapped_steps * SCHEMA_TIME_QUANTUM_S) > 1e-9:
            QMessageBox.warning(
                self,
                "Invalid start time",
                f"Sequence step start time must be on the {grid_ms:.0f} ms grid.",
            )
            self._restore_step_start(item, old_start_s)
            return
        try:
            sequence = self.gather_sequence()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid sequence", str(exc))
            self._restore_step_start(item, old_start_s)
            return
        overlaps = _sequence_overlap_pairs(sequence, self.project.patterns)
        if overlaps:
            previous_step, overlapping_step = overlaps[0]
            choice = QMessageBox.question(
                self,
                "Overlapping sequence items",
                "This change creates overlapping sequence items.\n\n"
                f"'{previous_step.pattern}' overlaps '{overlapping_step.pattern}'.\n\n"
                "Fix by shifting each subsequent item forward enough to remove overlap?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                self._restore_step_start(item, old_start_s)
                return
            sequence.steps = _shift_steps_to_avoid_overlap(sequence.steps, self.project.patterns)
        else:
            sequence.steps = _sorted_steps(sequence.steps)
        self._loading = True
        self._set_sequence_steps(sequence.steps)
        self._loading = False
        if self.commit_current_sequence(silent=True) and self.on_live_commit is not None:
            self.on_live_commit()
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

    def _restore_step_start(self, item: QTableWidgetItem, start_s: object) -> None:
        self._loading = True
        if isinstance(start_s, (int, float)):
            item.setText(f"{float(start_s):.4f}")
        else:
            item.setText("0.0000")
        self._loading = False

    def _find_step_covering_time(self, steps: list[SequenceStep], start_s: float) -> SequenceStep | None:
        for step in _sorted_steps(steps):
            pattern = self.project.patterns.get(step.pattern)
            if pattern is None:
                continue
            end_s = step.start_s + pattern.duration_s
            if step.start_s <= start_s < end_s:
                return step
        return None

    def _insert_step_with_shift(
        self,
        steps: list[SequenceStep],
        new_step: SequenceStep,
        insert_priority: int = 0,
    ) -> list[SequenceStep]:
        tagged_steps = [(step.start_s, 1, idx, SequenceStep(pattern=step.pattern, start_s=step.start_s)) for idx, step in enumerate(steps)]
        tagged_steps.append((new_step.start_s, insert_priority, len(tagged_steps), SequenceStep(pattern=new_step.pattern, start_s=new_step.start_s)))
        ordered_steps = [step for _, _, _, step in sorted(tagged_steps, key=lambda entry: (entry[0], entry[1], entry[2]))]
        return _shift_steps_to_avoid_overlap(ordered_steps, self.project.patterns)

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
        return True

    def save_current_sequence(self) -> bool:
        try:
            sequence = self.gather_sequence()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid sequence", str(exc))
            return False
        overlaps = _sequence_overlap_pairs(sequence, self.project.patterns)
        if overlaps:
            previous_step, overlapping_step = overlaps[0]
            choice = QMessageBox.question(
                self,
                "Overlapping sequence items",
                "This sequence contains overlapping items.\n\n"
                f"'{previous_step.pattern}' overlaps '{overlapping_step.pattern}'.\n\n"
                "Fix by shifting each subsequent item forward enough to remove overlap?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if choice != QMessageBox.StandardButton.Yes:
                return False
            sequence.steps = _shift_steps_to_avoid_overlap(sequence.steps, self.project.patterns)
            self._loading = True
            self._set_sequence_steps(sequence.steps)
            self._loading = False
            self.on_dirty()
        ok = self.commit_current_sequence(silent=False)
        if ok and self.on_commit is not None:
            self.on_commit()
        return ok

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
        self.repo_root = _repo_root()
        self.project = ExperimentProject()
        self.save_root = _load_save_root()
        self.schema_root = _load_schema_root()
        self.schema_file_path = ""
        self.last_schema_load_dir = self.schema_root
        self.pattern_dirty = False
        self.sequence_dirty = False
        self.project_dirty = False
        self._suppress_dirty_updates = False
        self._initial_layout_applied = False

        self.preview = TimelinePreview(self.project)
        self._suppress_selection_load = False
        self.pattern_editor = PatternEditor(
            self.project,
            self.repo_root,
            self.mark_pattern_dirty,
            self.refresh_lists,
            self._resolve_sequence_overlaps_after_pattern_edit,
            self._refresh_lists_live,
        )
        self.sequence_editor = SequenceEditor(
            self.project,
            self.preview,
            self.mark_sequence_dirty,
            self.refresh_lists,
            self._refresh_lists_live,
        )
        self.scanimage_control = ScanImageControlWidget(
            self.ensure_schema_path_for_external_use,
            lambda: self.project,
        )
        self.online_activity_widget = OnlineActivityWidget(self.scanimage_control)

        self.pattern_list = QListWidget()
        self.sequence_list = QListWidget()
        self.pattern_list.currentItemChanged.connect(self._pattern_selected)
        self.sequence_list.currentItemChanged.connect(self._sequence_selected)

        self.gui_control_config = _load_gui_control_config()
        self.gui_control_signals = GuiControlSignals()
        self.gui_control_signals.udp_message.connect(self._handle_gui_control_message)
        self.gui_control_listener: GuiControlListener | None = None

        self._build_ui()
        self._set_origin_user_id_options(self.project.origin_user_id, use_linux_default=True)
        self.refresh_lists()
        self._start_gui_control_listener()

    def _build_ui(self) -> None:
        toolbar = QToolBar("File")
        self.addToolBar(toolbar)

        new_btn = QPushButton("New")
        load_schema_btn = QPushButton("Load Schema")
        save_schema_btn = QPushButton("Save Schema")
        self.online_analysis_checkbox = QCheckBox("Online Analysis")

        toolbar.addWidget(new_btn)
        toolbar.addWidget(load_schema_btn)
        toolbar.addWidget(save_schema_btn)
        toolbar.addWidget(self.online_analysis_checkbox)

        new_btn.clicked.connect(self.new_project)
        load_schema_btn.clicked.connect(self.load_schema_dialog)
        save_schema_btn.clicked.connect(self.save_schema_dialog)
        self.online_analysis_checkbox.setChecked(self.scanimage_control.online_analysis_enabled())
        self.online_analysis_checkbox.toggled.connect(self.scanimage_control.set_online_analysis_enabled)

        schema_left = QWidget()
        schema_left_layout = QVBoxLayout(schema_left)

        project_box = QGroupBox("Project")
        project_form = QFormLayout(project_box)
        self.animal_edit = QLineEdit("TEST")
        self.project_edit = QLineEdit("DEFAULT")
        self.origin_exp_id_edit = QLineEdit()
        self.origin_user_id_combo = QComboBox()
        self.save_path_label = QLabel()
        self.save_path_label.setWordWrap(True)
        self.animal_edit.textChanged.connect(self.update_save_path_label)
        self.project_edit.textChanged.connect(self.update_save_path_label)
        self.origin_exp_id_edit.textChanged.connect(self._origin_exp_id_changed)
        self.origin_user_id_combo.currentTextChanged.connect(self._origin_user_id_changed)
        project_form.addRow("Animal ID", self.animal_edit)
        project_form.addRow("Project", self.project_edit)
        project_form.addRow("Origin ExpID", self.origin_exp_id_edit)
        project_form.addRow("Origin userID", self.origin_user_id_combo)
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

        self.schema_splitter = QSplitter()
        self.schema_splitter.addWidget(schema_left)
        self.schema_splitter.addWidget(self.schema_editor_tabs)
        self.schema_splitter.setStretchFactor(1, 1)

        self.main_tabs = QTabWidget()
        self.main_tabs.addTab(self.scanimage_control, "ScanImage Control")
        self.main_tabs.addTab(self.schema_splitter, "Stimulation Schema")
        self.main_tabs.addTab(self.online_activity_widget, "Online activity")
        self.setCentralWidget(self.main_tabs)

        self.add_pattern_btn.clicked.connect(self.add_pattern)
        self.copy_pattern_btn.clicked.connect(self.copy_pattern)
        self.delete_pattern_btn.clicked.connect(self.delete_pattern)
        self.add_sequence_btn.clicked.connect(self.add_sequence)
        self.copy_sequence_btn.clicked.connect(self.copy_sequence)
        self.delete_sequence_btn.clicked.connect(self.delete_sequence)
        self.update_save_path_label()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._initial_layout_applied:
            self._initial_layout_applied = True
            QTimer.singleShot(0, self._apply_initial_layout)
            QTimer.singleShot(100, self._apply_initial_layout)

    def _apply_initial_layout(self) -> None:
        central = self.centralWidget()
        if central is not None:
            central.updateGeometry()
            if central.layout() is not None:
                central.layout().activate()
        self.main_tabs.updateGeometry()
        self.scanimage_control.updateGeometry()
        self.schema_editor_tabs.updateGeometry()
        total_width = max(1, self.schema_splitter.width())
        left_width = max(280, int(total_width * 0.24))
        right_width = max(480, total_width - left_width)
        self.schema_splitter.setSizes([left_width, right_width])
        for splitter in self.findChildren(QSplitter):
            sizes = splitter.sizes()
            if sizes:
                splitter.setSizes([max(1, size) for size in sizes])
        self.updateGeometry()
        self.repaint()
        QTimer.singleShot(0, self._trigger_post_show_resize)

    def _trigger_post_show_resize(self) -> None:
        width = self.width()
        height = self.height()
        if width <= 0 or height <= 1:
            return
        self.resize(width, height - 1)
        self.resize(width, height)

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
        if "origin_exp_id" in values:
            self.origin_exp_id_edit.setText(str(values["origin_exp_id"]))
            applied["origin_exp_id"] = self.origin_exp_id_edit.text()
        if "origin_user_id" in values:
            self._set_origin_user_id_options(str(values["origin_user_id"]), use_linux_default=False)
            self.project.origin_user_id = self.origin_user_id_combo.currentText().strip()
            applied["origin_user_id"] = self.origin_user_id_combo.currentText()
        applied.update(self.scanimage_control.set_remote_state(values))
        return applied

    def _schema_state(self) -> dict[str, object]:
        return {
            "animal_id": self.animal_id(),
            "project_name": self.project_name(),
            "schema_file_path": self.schema_file_path,
            "schema_save_path": str(self.schema_save_path()),
            "origin_exp_id": self.project.origin_exp_id,
            "origin_user_id": self.project.origin_user_id,
            "pattern_dirty": self.pattern_dirty,
            "sequence_dirty": self.sequence_dirty,
            "current_pattern": self._current_item_name(self.pattern_list),
            "current_sequence": self._current_item_name(self.sequence_list),
            "pattern_names": list(self.project.patterns.keys()),
            "sequence_names": list(self.project.sequences.keys()),
        }

    def _set_origin_user_id_options(self, selected_user_id: str, use_linux_default: bool) -> None:
        if selected_user_id.strip():
            target_user_id = selected_user_id.strip()
        elif use_linux_default and sys.platform.startswith("linux"):
            target_user_id = _default_origin_user_id()
        else:
            target_user_id = ""
        options = _origin_user_options(target_user_id)
        if not target_user_id and "" not in options:
            options = [""] + options
        self.origin_user_id_combo.blockSignals(True)
        self.origin_user_id_combo.clear()
        for option in options:
            self.origin_user_id_combo.addItem(option)
        index = self.origin_user_id_combo.findText(target_user_id)
        if index < 0 and target_user_id:
            self.origin_user_id_combo.addItem(target_user_id)
            index = self.origin_user_id_combo.findText(target_user_id)
        if index >= 0:
            self.origin_user_id_combo.setCurrentIndex(index)
        elif self.origin_user_id_combo.count():
            self.origin_user_id_combo.setCurrentIndex(0)
        self.origin_user_id_combo.blockSignals(False)

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
        current_pattern = self.pattern_editor.current_name or self._current_item_name(self.pattern_list)
        current_sequence = self.sequence_editor.current_name or self._current_item_name(self.sequence_list)

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
        if self.project_dirty:
            dirty_bits.append("project modified")
        if self.pattern_dirty:
            dirty_bits.append("patterns modified")
        if self.sequence_dirty:
            dirty_bits.append("sequences modified")
        message = " | ".join(dirty_bits + (errors[:3] if errors else [])) if (dirty_bits or errors) else "Project valid"
        self.statusBar().showMessage(message)

    def update_save_path_label(self) -> None:
        self.save_path_label.setText(str(self.schema_save_path()))

    def _origin_exp_id_changed(self) -> None:
        value = self.origin_exp_id_edit.text().strip()
        if self.project.origin_exp_id == value:
            return
        self.project.origin_exp_id = value
        if self._suppress_dirty_updates:
            return
        self.project_dirty = True
        self.update_status()

    def _origin_user_id_changed(self, value: str) -> None:
        normalized = value.strip()
        if self.project.origin_user_id == normalized:
            return
        self.project.origin_user_id = normalized
        if self._suppress_dirty_updates:
            return
        self.project_dirty = True
        self.update_status()

    def _refresh_lists_live(self) -> None:
        self._suppress_selection_load = True
        try:
            self.refresh_lists()
        finally:
            self._suppress_selection_load = False

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
        self.project_dirty = False
        self.update_status()

    def _resolve_sequence_overlaps_after_pattern_edit(self) -> bool:
        affected_sequences: list[tuple[str, SequenceStep, SequenceStep]] = []
        for sequence_name, sequence in self.project.sequences.items():
            overlaps = _sequence_overlap_pairs(sequence, self.project.patterns)
            if overlaps:
                affected_sequences.append((sequence_name, overlaps[0][0], overlaps[0][1]))
        if not affected_sequences:
            return True

        preview_lines = [
            f"{sequence_name}: '{previous_step.pattern}' overlaps '{overlapping_step.pattern}'"
            for sequence_name, previous_step, overlapping_step in affected_sequences[:5]
        ]
        if len(affected_sequences) > 5:
            preview_lines.append(f"... and {len(affected_sequences) - 5} more")
        choice = QMessageBox.question(
            self,
            "Pattern edit causes sequence overlap",
            "This pattern edit causes overlapping sequence items.\n\n"
            + "\n".join(preview_lines)
            + "\n\nFix by shifting each subsequent item forward enough to remove overlap?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if choice != QMessageBox.StandardButton.Yes:
            return False

        for sequence_name, sequence in list(self.project.sequences.items()):
            if _sequence_overlap_pairs(sequence, self.project.patterns):
                sequence.steps = _shift_steps_to_avoid_overlap(sequence.steps, self.project.patterns)
                self.project.sequences[sequence_name] = sequence
        self.sequence_dirty = True
        self.refresh_lists()
        self.update_status()
        return True

    def _pattern_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:  # noqa: ARG002
        if self._suppress_selection_load or not current:
            return
        name = self._item_name(current)
        if name in self.project.patterns:
            self.schema_editor_tabs.setCurrentWidget(self.pattern_editor)
            self.pattern_editor.load_pattern(name)

    def _sequence_selected(self, current: QListWidgetItem, previous: QListWidgetItem) -> None:  # noqa: ARG002
        if self._suppress_selection_load or not current:
            return
        name = self._item_name(current)
        if name in self.project.sequences:
            self.schema_editor_tabs.setCurrentWidget(self.sequence_editor)
            self.sequence_editor.load_sequence(name)
            self.preview.set_sequence(name)

    def add_pattern(self) -> None:
        name = _lowest_available_numbered_name("P", self.project.patterns)
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
            cells=[
                CellSpec(
                    label=cell.label,
                    x=cell.x,
                    y=cell.y,
                    z=cell.z,
                    power_scale=cell.power_scale,
                    origin=cell.origin,
                )
                for cell in pattern.cells
            ],
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
        name = _lowest_available_numbered_name("S", self.project.sequences)
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
        if self.project_dirty or self.pattern_dirty or self.sequence_dirty:
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
            self.origin_exp_id_edit.setText("")
            self._set_origin_user_id_options("", use_linux_default=True)
            self.pattern_editor.project = self.project
            self.sequence_editor.project = self.project
            self.preview.project = self.project
            self.pattern_editor.current_name = ""
            self.sequence_editor.current_name = ""
            self.pattern_dirty = False
            self.sequence_dirty = False
            self.project_dirty = False
            self.pattern_editor.clear_form()
            self.sequence_editor.clear_form()
            self.refresh_lists()
        finally:
            self._suppress_dirty_updates = False
        self.clear_dirty()

    def load_schema_dialog(self) -> None:
        if self.project_dirty or self.pattern_dirty or self.sequence_dirty:
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
            self.origin_exp_id_edit.setText(self.project.origin_exp_id)
            self._set_origin_user_id_options(self.project.origin_user_id, use_linux_default=False)
            self.pattern_dirty = False
            self.sequence_dirty = False
            self.project_dirty = False
            self.last_schema_load_dir = Path(path).resolve().parent
            self.refresh_lists()
        finally:
            self._suppress_dirty_updates = False
        self.clear_dirty()
        self.main_tabs.setCurrentIndex(1)
        self.schema_editor_tabs.setCurrentWidget(self.pattern_editor)

    def save_schema_dialog(self) -> bool:
        default_path = self.schema_save_path()
        default_dir = default_path.parent
        default_dir.mkdir(parents=True, exist_ok=True)
        dialog_path = default_path if default_path.exists() else default_dir / default_path.name
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save schema",
            str(dialog_path),
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
        if not force and not (self.project_dirty or self.pattern_dirty or self.sequence_dirty):
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
        if self.project_dirty or self.pattern_dirty or self.sequence_dirty:
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
    geometry = _preferred_startup_geometry(app)
    if geometry is not None:
        target_width = max(1200, int(round(geometry.width() * 0.8)))
        target_height = max(800, int(round(geometry.height() * 0.8)))
        target_width = min(target_width, geometry.width())
        target_height = min(target_height, geometry.height())
        target_x = geometry.x() + max(0, (geometry.width() - target_width) // 2)
        target_y = geometry.y() + max(0, (geometry.height() - target_height) // 2)
        window.setGeometry(target_x, target_y, target_width, target_height)
    window.show()
    sys.exit(app.exec())
