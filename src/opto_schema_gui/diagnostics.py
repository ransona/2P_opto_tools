from __future__ import annotations

import json
import math
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

import numpy as np
import tifffile
from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)
from scipy.optimize import curve_fit

from .scanimage_control import ScanImageControlWidget


SUMMARY_FILENAME = "slm_psf_summary.json"
RESULT_FILENAME = "slm_psf_result.json"


def _format_coord(value: float) -> str:
    return f"{float(value):g}"


def _default_output_root() -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"F:\\slm_psf\\{stamp}"


def _parse_axis_values(raw_text: str) -> list[float]:
    text = raw_text.strip()
    if not text:
        raise ValueError("Axis specification cannot be empty.")
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1].strip()
    if ":" in text and "," not in text and ";" not in text:
        parts = [part.strip() for part in text.split(":")]
        if len(parts) == 2:
            start = float(parts[0])
            stop = float(parts[1])
            step = 1.0 if stop >= start else -1.0
        elif len(parts) == 3:
            start = float(parts[0])
            step = float(parts[1])
            stop = float(parts[2])
        else:
            raise ValueError(f"Invalid MATLAB-style axis specification '{raw_text}'.")
        if step == 0:
            raise ValueError("Axis step cannot be zero.")
        values: list[float] = []
        current = start
        if step > 0:
            while current <= stop + (abs(step) * 1e-9):
                values.append(round(current, 10))
                current += step
        else:
            while current >= stop - (abs(step) * 1e-9):
                values.append(round(current, 10))
                current += step
        if not values:
            raise ValueError(f"Axis specification '{raw_text}' produced no values.")
        return values
    tokens = [token for token in re.split(r"[\s,;]+", text) if token]
    if not tokens:
        raise ValueError(f"Axis specification '{raw_text}' produced no values.")
    return [float(token) for token in tokens]


def _parse_power_values(raw_text: str) -> list[float]:
    tokens = [token for token in re.split(r"[\s,;]+", raw_text.strip()) if token]
    if not tokens:
        raise ValueError("Power vector cannot be empty.")
    return [float(token) for token in tokens]


def _safe_json_dump(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _gaussian_with_offset(x: np.ndarray, amplitude: float, center: float, sigma: float, offset: float) -> np.ndarray:
    return amplitude * np.exp(-((x - center) ** 2) / (2.0 * sigma**2)) + offset


def _normalize_frame_stack(array: np.ndarray) -> np.ndarray:
    data = np.asarray(array)
    if data.ndim < 2:
        raise ValueError("TIFF stack did not contain image frames.")
    if data.ndim == 2:
        return data[np.newaxis, :, :]
    if data.ndim == 3:
        return data
    frame_shape = data.shape[-2:]
    return data.reshape((-1,) + frame_shape)


def _load_volume_frame_stack(volume_dir: Path) -> np.ndarray:
    tiff_paths = sorted(
        [
            *volume_dir.glob("*.tif"),
            *volume_dir.glob("*.tiff"),
            *volume_dir.glob("*.TIF"),
            *volume_dir.glob("*.TIFF"),
        ]
    )
    if not tiff_paths:
        raise FileNotFoundError(f"No TIFF files were found in {volume_dir}")
    frame_blocks: list[np.ndarray] = []
    for path in tiff_paths:
        frame_blocks.append(_normalize_frame_stack(tifffile.imread(path)))
    return np.concatenate(frame_blocks, axis=0) if len(frame_blocks) > 1 else frame_blocks[0]


def _compute_slice_intensity(
    frames: np.ndarray,
    z_positions_um: list[float],
    frames_per_slice: int,
    log_average_factor: int,
) -> list[float]:
    frame_means = frames.reshape(frames.shape[0], -1).mean(axis=1)
    expected_logged = max(1, int(round(frames_per_slice / max(log_average_factor, 1))))
    if frame_means.size == len(z_positions_um):
        return frame_means.astype(float).tolist()
    if frame_means.size == len(z_positions_um) * expected_logged:
        grouped = frame_means.reshape(len(z_positions_um), expected_logged)
        return grouped.mean(axis=1).astype(float).tolist()
    if frame_means.size % len(z_positions_um) == 0:
        per_slice = frame_means.size // len(z_positions_um)
        grouped = frame_means.reshape(len(z_positions_um), per_slice)
        return grouped.mean(axis=1).astype(float).tolist()
    raise ValueError(
        f"Frame count {frame_means.size} does not match the expected slice structure for {len(z_positions_um)} slices."
    )


def analyze_slm_psf_volume(
    volume_dir: Path,
    *,
    x_um: float,
    y_um: float,
    z_um: float,
    z_positions_um: list[float],
    frames_per_slice: int,
    log_average_factor: int,
) -> dict[str, object]:
    frames = _load_volume_frame_stack(volume_dir)
    intensities = _compute_slice_intensity(frames, z_positions_um, frames_per_slice, log_average_factor)
    z_array = np.asarray(z_positions_um, dtype=float)
    intensity_array = np.asarray(intensities, dtype=float)
    baseline0 = float(np.nanmin(intensity_array))
    amplitude0 = float(np.nanmax(intensity_array) - baseline0)
    center0 = float(z_array[int(np.nanargmax(intensity_array))])
    sigma0 = max(1e-6, float(max(np.median(np.diff(z_array)) if z_array.size > 1 else 1.0, 1.0)))
    fit_payload: dict[str, object]
    fwhm_um = math.nan
    try:
        params, _ = curve_fit(
            _gaussian_with_offset,
            z_array,
            intensity_array,
            p0=[amplitude0, center0, sigma0, baseline0],
            maxfev=10000,
        )
        amplitude, center, sigma, offset = [float(value) for value in params]
        fit_curve = _gaussian_with_offset(z_array, amplitude, center, sigma, offset)
        fwhm_um = float(2.0 * math.sqrt(2.0 * math.log(2.0)) * abs(sigma))
        fit_payload = {
            "ok": True,
            "amplitude": amplitude,
            "center_um": center,
            "sigma_um": sigma,
            "offset": offset,
            "fwhm_um": fwhm_um,
            "fitted_intensity": fit_curve.astype(float).tolist(),
        }
    except Exception as exc:
        fit_payload = {
            "ok": False,
            "error": str(exc),
            "fwhm_um": None,
            "fitted_intensity": [],
        }
    result = {
        "x_um": float(x_um),
        "y_um": float(y_um),
        "z_um": float(z_um),
        "z_positions_um": [float(value) for value in z_positions_um],
        "raw_intensity": [float(value) for value in intensities],
        "fit": fit_payload,
        "fwhm_um": None if not math.isfinite(fwhm_um) else fwhm_um,
        "frame_count": int(frames.shape[0]),
        "frame_shape": [int(frames.shape[1]), int(frames.shape[2])],
    }
    _safe_json_dump(volume_dir / RESULT_FILENAME, result)
    return result


def load_slm_psf_summary(root_dir: Path) -> dict[str, object]:
    summary_path = root_dir / SUMMARY_FILENAME
    if not summary_path.is_file():
        raise FileNotFoundError(f"Could not find {SUMMARY_FILENAME} in {root_dir}")
    return json.loads(summary_path.read_text(encoding="utf-8"))


def analyze_slm_psf_root(root_dir: Path) -> dict[str, object]:
    summary = load_slm_psf_summary(root_dir)
    acquisition = summary.get("acquisition", {})
    frames_per_slice = int(acquisition.get("frames_per_slice", 1))
    log_average_factor = int(acquisition.get("log_average_factor", 1))
    volume_results: list[dict[str, object]] = []
    for volume in summary.get("volumes", []):
        volume_dir = root_dir / str(volume["folder_name"])
        result = analyze_slm_psf_volume(
            volume_dir,
            x_um=float(volume["x_um"]),
            y_um=float(volume["y_um"]),
            z_um=float(volume["z_um"]),
            z_positions_um=[float(value) for value in volume["z_positions_um"]],
            frames_per_slice=frames_per_slice,
            log_average_factor=log_average_factor,
        )
        volume["result_file"] = RESULT_FILENAME
        volume["fwhm_um"] = result["fwhm_um"]
        volume_results.append(result)
    summary["processed_at"] = datetime.now().isoformat(timespec="seconds")
    summary["results"] = volume_results
    _safe_json_dump(root_dir / SUMMARY_FILENAME, summary)
    return summary


@dataclass
class SlmPsfAcquisitionParams:
    path_name: str
    output_root: str
    x_values_um: list[float]
    y_values_um: list[float]
    z_values_um: list[float]
    spiral_width_um: float = 30.0
    spiral_height_um: float = 30.0
    pixels_per_line: int = 128
    lines_per_frame: int = 128
    num_slices: int = 201
    frames_per_slice: int = 50
    log_average_factor: int = 50
    display_average_factor: int = 50
    z_step_um: float = 5.0
    sequence_duration_s: float = 0.007
    power_values: list[float] | None = None
    revolutions: float = 5.0

    def __post_init__(self) -> None:
        if self.power_values is None:
            self.power_values = [0.0, 0.0, 1.0]

    def z_positions_for_center(self, center_z_um: float) -> list[float]:
        half_count = self.num_slices // 2
        return [float(center_z_um + (index - half_count) * self.z_step_um) for index in range(self.num_slices)]

    def volume_specs(self, root_dir: Path) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        for x_um in self.x_values_um:
            for y_um in self.y_values_um:
                for z_um in self.z_values_um:
                    folder_name = f"volume_x={_format_coord(x_um)}_y={_format_coord(y_um)}_z={_format_coord(z_um)}"
                    specs.append(
                        {
                            "x_um": float(x_um),
                            "y_um": float(y_um),
                            "z_um": float(z_um),
                            "folder_name": folder_name,
                            "volume_dir": str(root_dir / folder_name),
                            "z_positions_um": self.z_positions_for_center(float(z_um)),
                        }
                    )
        return specs


class _DiagnosticsSignals(QObject):
    progress = pyqtSignal(int, int, str)
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, object)


class MatplotlibDialog(QDialog):
    def __init__(self, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
        from matplotlib.figure import Figure

        self.setWindowTitle(title)
        self.resize(960, 720)
        layout = QVBoxLayout(self)
        self.figure = Figure(constrained_layout=True)
        self.canvas = FigureCanvasQTAgg(self.figure)
        layout.addWidget(self.canvas, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class SlmPsfConfigDialog(QDialog):
    def __init__(
        self,
        path_names: list[str],
        default_path_name: str,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._visualize_existing_folder: str | None = None
        self.setWindowTitle("Acquire SLM Volume")
        self.resize(620, 560)
        layout = QVBoxLayout(self)

        info = QLabel(
            "Use this diagnostic with a thin fluorescent sample placed at the native focal plane. "
            "Acquisition settings default to the requested axial-resolution test values and can be adjusted here."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form_box = QGroupBox("Acquisition Parameters")
        form = QFormLayout(form_box)
        self.path_combo = QComboBox()
        self.path_combo.addItems(path_names)
        if default_path_name:
            index = self.path_combo.findText(default_path_name)
            if index >= 0:
                self.path_combo.setCurrentIndex(index)
        self.output_root_edit = QLineEdit(_default_output_root())
        browse_root_btn = QPushButton("Browse…")
        browse_root_btn.clicked.connect(self._browse_output_root)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_root_edit, 1)
        output_row.addWidget(browse_root_btn)
        output_root_widget = QWidget()
        output_root_widget.setLayout(output_row)

        self.x_edit = QLineEdit("[-200:200:200]")
        self.y_edit = QLineEdit("[-200:200:200]")
        self.z_edit = QLineEdit("[-200:200:200]")
        self.spiral_width_spin = QDoubleSpinBox()
        self.spiral_width_spin.setRange(0.1, 1000.0)
        self.spiral_width_spin.setDecimals(3)
        self.spiral_width_spin.setValue(30.0)
        self.spiral_height_spin = QDoubleSpinBox()
        self.spiral_height_spin.setRange(0.1, 1000.0)
        self.spiral_height_spin.setDecimals(3)
        self.spiral_height_spin.setValue(30.0)
        self.pixels_per_line_spin = QSpinBox()
        self.pixels_per_line_spin.setRange(2, 4096)
        self.pixels_per_line_spin.setValue(128)
        self.lines_per_frame_spin = QSpinBox()
        self.lines_per_frame_spin.setRange(2, 4096)
        self.lines_per_frame_spin.setValue(128)
        self.num_slices_spin = QSpinBox()
        self.num_slices_spin.setRange(1, 5000)
        self.num_slices_spin.setValue(201)
        self.frames_per_slice_spin = QSpinBox()
        self.frames_per_slice_spin.setRange(1, 10000)
        self.frames_per_slice_spin.setValue(50)
        self.log_average_spin = QSpinBox()
        self.log_average_spin.setRange(1, 10000)
        self.log_average_spin.setValue(50)
        self.display_average_spin = QSpinBox()
        self.display_average_spin.setRange(1, 10000)
        self.display_average_spin.setValue(50)
        self.z_step_spin = QDoubleSpinBox()
        self.z_step_spin.setRange(0.001, 1000.0)
        self.z_step_spin.setDecimals(4)
        self.z_step_spin.setValue(5.0)
        self.sequence_duration_ms_spin = QDoubleSpinBox()
        self.sequence_duration_ms_spin.setRange(0.001, 10000.0)
        self.sequence_duration_ms_spin.setDecimals(4)
        self.sequence_duration_ms_spin.setValue(7.0)
        self.power_edit = QLineEdit("0 0 1")

        form.addRow("Path", self.path_combo)
        form.addRow("Output root", output_root_widget)
        form.addRow("X grid (um)", self.x_edit)
        form.addRow("Y grid (um)", self.y_edit)
        form.addRow("Z grid (um)", self.z_edit)
        form.addRow("Spiral width (um)", self.spiral_width_spin)
        form.addRow("Spiral height (um)", self.spiral_height_spin)
        form.addRow("Pixels per line", self.pixels_per_line_spin)
        form.addRow("Lines per frame", self.lines_per_frame_spin)
        form.addRow("Slices", self.num_slices_spin)
        form.addRow("Frames per slice", self.frames_per_slice_spin)
        form.addRow("Saved-frame average", self.log_average_spin)
        form.addRow("Display average", self.display_average_spin)
        form.addRow("Z step (um)", self.z_step_spin)
        form.addRow("Stim duration (ms)", self.sequence_duration_ms_spin)
        form.addRow("Power vector", self.power_edit)
        layout.addWidget(form_box)

        buttons = QDialogButtonBox()
        self.acquire_button = buttons.addButton("Acquire", QDialogButtonBox.ButtonRole.AcceptRole)
        self.visualize_button = buttons.addButton("Visualize Existing…", QDialogButtonBox.ButtonRole.ActionRole)
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.acquire_button.clicked.connect(self._accept_if_valid)
        self.visualize_button.clicked.connect(self._choose_existing_folder)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _browse_output_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select output root", self.output_root_edit.text().strip() or "")
        if selected:
            self.output_root_edit.setText(selected)

    def _choose_existing_folder(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select acquired SLM PSF folder", self.output_root_edit.text().strip() or "")
        if selected:
            self._visualize_existing_folder = selected
            self.done(2)

    def _accept_if_valid(self) -> None:
        try:
            self.gather_params()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid SLM PSF Settings", str(exc))
            return
        self.accept()

    def visualize_existing_folder(self) -> str | None:
        return self._visualize_existing_folder

    def gather_params(self) -> SlmPsfAcquisitionParams:
        path_name = self.path_combo.currentText().strip()
        if not path_name:
            raise ValueError("A ScanImage path must be selected.")
        output_root = self.output_root_edit.text().strip()
        if not output_root:
            raise ValueError("Output root cannot be empty.")
        x_values = _parse_axis_values(self.x_edit.text())
        y_values = _parse_axis_values(self.y_edit.text())
        z_values = _parse_axis_values(self.z_edit.text())
        frames_per_slice = self.frames_per_slice_spin.value()
        log_average_factor = self.log_average_spin.value()
        if frames_per_slice % log_average_factor != 0:
            raise ValueError("Saved-frame average must divide frames per slice exactly.")
        return SlmPsfAcquisitionParams(
            path_name=path_name,
            output_root=output_root,
            x_values_um=x_values,
            y_values_um=y_values,
            z_values_um=z_values,
            spiral_width_um=self.spiral_width_spin.value(),
            spiral_height_um=self.spiral_height_spin.value(),
            pixels_per_line=self.pixels_per_line_spin.value(),
            lines_per_frame=self.lines_per_frame_spin.value(),
            num_slices=self.num_slices_spin.value(),
            frames_per_slice=frames_per_slice,
            log_average_factor=log_average_factor,
            display_average_factor=self.display_average_spin.value(),
            z_step_um=self.z_step_spin.value(),
            sequence_duration_s=self.sequence_duration_ms_spin.value() / 1000.0,
            power_values=_parse_power_values(self.power_edit.text()),
        )


class DiagnosticsWidget(QWidget):
    def __init__(self, scanimage_control: ScanImageControlWidget, parent: QWidget | None = None):
        super().__init__(parent)
        self.scanimage_control = scanimage_control
        self._signals = _DiagnosticsSignals()
        self._signals.progress.connect(self._handle_progress)
        self._signals.status.connect(self._append_status)
        self._signals.finished.connect(self._handle_finished)
        self._current_summary: dict[str, object] | None = None
        self._current_root_dir: Path | None = None
        self._worker_thread: threading.Thread | None = None
        self._cancel_event = threading.Event()
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        intro_box = QGroupBox("Diagnostics")
        intro_layout = QVBoxLayout(intro_box)
        intro_label = QLabel(
            "Use this tab to run diagnostic procedures on the live ScanImage system. "
            "The SLM PSF acquisition drives ScanImage directly, saves one volume per SLM XYZ position, "
            "then computes an axial FWHM estimate for each stimulated coordinate."
        )
        intro_label.setWordWrap(True)
        intro_layout.addWidget(intro_label)
        layout.addWidget(intro_box)

        button_row = QHBoxLayout()
        self.acquire_button = QPushButton("Acquire SLM volume")
        self.abort_button = QPushButton("Abort")
        self.open_existing_button = QPushButton("Open Existing Result")
        button_row.addWidget(self.acquire_button)
        button_row.addWidget(self.abort_button)
        button_row.addWidget(self.open_existing_button)
        button_row.addStretch(1)
        layout.addLayout(button_row)

        progress_box = QGroupBox("Run Status")
        progress_layout = QVBoxLayout(progress_box)
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(500)
        self.log_text.setMinimumHeight(140)
        progress_layout.addWidget(self.status_label)
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.log_text)
        layout.addWidget(progress_box)

        viz_box = QGroupBox("Visualisation")
        viz_layout = QGridLayout(viz_box)
        self.summary_label = QLabel("No processed SLM PSF dataset loaded.")
        self.summary_label.setWordWrap(True)
        self.plot_3d_button = QPushButton("3D FWHM Plot")
        self.plot_cross_section_button = QPushButton("Cross Section")
        self.slice_axis_combo = QComboBox()
        self.slice_axis_combo.addItems(["x", "y", "z"])
        self.fixed_x_combo = QComboBox()
        self.fixed_y_combo = QComboBox()
        self.fixed_z_combo = QComboBox()
        viz_layout.addWidget(self.summary_label, 0, 0, 1, 4)
        viz_layout.addWidget(QLabel("Slice axis"), 1, 0)
        viz_layout.addWidget(self.slice_axis_combo, 1, 1)
        viz_layout.addWidget(self.plot_3d_button, 1, 2)
        viz_layout.addWidget(self.plot_cross_section_button, 1, 3)
        viz_layout.addWidget(QLabel("Fix X"), 2, 0)
        viz_layout.addWidget(self.fixed_x_combo, 2, 1)
        viz_layout.addWidget(QLabel("Fix Y"), 2, 2)
        viz_layout.addWidget(self.fixed_y_combo, 2, 3)
        viz_layout.addWidget(QLabel("Fix Z"), 3, 0)
        viz_layout.addWidget(self.fixed_z_combo, 3, 1)
        layout.addWidget(viz_box)

        self.acquire_button.clicked.connect(self._show_acquisition_dialog)
        self.abort_button.clicked.connect(self._request_abort)
        self.open_existing_button.clicked.connect(self._open_existing_result)
        self.plot_3d_button.clicked.connect(self._show_3d_plot)
        self.plot_cross_section_button.clicked.connect(self._show_cross_section_plot)
        self.slice_axis_combo.currentTextChanged.connect(self._refresh_cross_section_controls)
        self.abort_button.setEnabled(False)
        self._set_visualization_enabled(False)

    def _append_status(self, message: str) -> None:
        self.log_text.appendPlainText(message)
        self.status_label.setText(message)

    def _set_running(self, running: bool) -> None:
        self.acquire_button.setEnabled(not running)
        self.abort_button.setEnabled(running)
        self.open_existing_button.setEnabled(not running)

    def _show_acquisition_dialog(self) -> None:
        dialog = SlmPsfConfigDialog(
            self.scanimage_control.available_path_names(),
            self.scanimage_control.preferred_photostim_path_name(),
            self,
        )
        result = dialog.exec()
        if result == 2:
            folder = dialog.visualize_existing_folder()
            if folder:
                self._load_existing_result(Path(folder))
            return
        if result != QDialog.DialogCode.Accepted:
            return
        params = dialog.gather_params()
        self._start_acquisition(params)

    def _start_acquisition(self, params: SlmPsfAcquisitionParams) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            QMessageBox.warning(self, "Diagnostics Busy", "An SLM PSF diagnostic run is already in progress.")
            return
        root_dir = Path(params.output_root)
        root_dir.mkdir(parents=True, exist_ok=True)
        summary = {
            "tool": "slm_psf",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "acquisition": {
                **asdict(params),
                "output_root": str(root_dir),
            },
            "volumes": params.volume_specs(root_dir),
        }
        _safe_json_dump(root_dir / SUMMARY_FILENAME, summary)
        self._current_root_dir = root_dir
        self._cancel_event.clear()
        self._set_running(True)
        self.progress_bar.setRange(0, len(summary["volumes"]))
        self.progress_bar.setValue(0)
        self._append_status(f"Starting SLM PSF acquisition in {root_dir}")

        def progress_callback(done: int, total: int, message: str) -> None:
            self._signals.progress.emit(done, total, message)

        def cancel_check() -> bool:
            return self._cancel_event.is_set()

        def worker() -> None:
            try:
                self.scanimage_control.run_slm_psf_diagnostic(
                    params.path_name,
                    pixels_per_line=params.pixels_per_line,
                    lines_per_frame=params.lines_per_frame,
                    num_slices=params.num_slices,
                    frames_per_slice=params.frames_per_slice,
                    z_step_um=params.z_step_um,
                    log_average_factor=params.log_average_factor,
                    display_average_factor=params.display_average_factor,
                    sequence_duration_s=params.sequence_duration_s,
                    spiral_width_um=params.spiral_width_um,
                    spiral_height_um=params.spiral_height_um,
                    power_values=params.power_values or [0.0, 0.0, 1.0],
                    volumes=summary["volumes"],
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                )
                if cancel_check():
                    raise RuntimeError("SLM PSF diagnostic aborted.")
                processed_summary = analyze_slm_psf_root(root_dir)
                self._signals.finished.emit(True, processed_summary)
            except Exception as exc:
                self._signals.finished.emit(False, str(exc))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _handle_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)
        self._append_status(message)

    def _handle_finished(self, ok: bool, payload: object) -> None:
        self._set_running(False)
        if not ok:
            if str(payload) == "SLM PSF diagnostic aborted.":
                self._append_status("SLM PSF acquisition aborted.")
                return
            self._append_status(f"SLM PSF run failed: {payload}")
            QMessageBox.critical(self, "SLM PSF Run Failed", str(payload))
            return
        assert isinstance(payload, dict)
        self._append_status("SLM PSF acquisition and processing completed.")
        self._set_summary(payload)

    def _request_abort(self) -> None:
        if self._worker_thread is None or not self._worker_thread.is_alive():
            return
        self._cancel_event.set()
        self._append_status("Aborting SLM PSF acquisition...")

    def _open_existing_result(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select processed SLM PSF folder", "")
        if folder:
            self._load_existing_result(Path(folder))

    def _load_existing_result(self, root_dir: Path) -> None:
        try:
            summary = analyze_slm_psf_root(root_dir)
        except Exception as exc:
            QMessageBox.critical(self, "Load SLM PSF Result Failed", str(exc))
            return
        self._current_root_dir = root_dir
        self._set_summary(summary)
        self._append_status(f"Loaded SLM PSF result from {root_dir}")

    def _set_summary(self, summary: dict[str, object]) -> None:
        self._current_summary = summary
        results = summary.get("results", [])
        valid_fwhm = [entry.get("fwhm_um") for entry in results if entry.get("fwhm_um") is not None]
        if valid_fwhm:
            values = [float(value) for value in valid_fwhm]
            self.summary_label.setText(
                f"Loaded {len(results)} volume(s). FWHM range: {min(values):.3f} to {max(values):.3f} um."
            )
        else:
            self.summary_label.setText(f"Loaded {len(results)} volume(s). No valid Gaussian fits were produced.")
        self._populate_fixed_coordinate_controls()
        self._set_visualization_enabled(bool(results))

    def _populate_fixed_coordinate_controls(self) -> None:
        if self._current_summary is None:
            return
        results = self._current_summary.get("results", [])
        x_values = sorted({float(entry["x_um"]) for entry in results})
        y_values = sorted({float(entry["y_um"]) for entry in results})
        z_values = sorted({float(entry["z_um"]) for entry in results})
        for combo, values in (
            (self.fixed_x_combo, x_values),
            (self.fixed_y_combo, y_values),
            (self.fixed_z_combo, z_values),
        ):
            combo.clear()
            for value in values:
                combo.addItem(_format_coord(value), value)
        self._refresh_cross_section_controls()

    def _refresh_cross_section_controls(self) -> None:
        axis = self.slice_axis_combo.currentText()
        self.fixed_x_combo.setEnabled(axis != "x")
        self.fixed_y_combo.setEnabled(axis != "y")
        self.fixed_z_combo.setEnabled(axis != "z")

    def _set_visualization_enabled(self, enabled: bool) -> None:
        self.plot_3d_button.setEnabled(enabled)
        self.plot_cross_section_button.setEnabled(enabled)
        self.slice_axis_combo.setEnabled(enabled)
        self.fixed_x_combo.setEnabled(enabled)
        self.fixed_y_combo.setEnabled(enabled)
        self.fixed_z_combo.setEnabled(enabled)

    def _current_results(self) -> list[dict[str, object]]:
        if self._current_summary is None:
            return []
        return list(self._current_summary.get("results", []))

    def _show_3d_plot(self) -> None:
        results = self._current_results()
        if not results:
            return
        dialog = MatplotlibDialog("SLM PSF 3D FWHM", self)
        ax = dialog.figure.add_subplot(111, projection="3d")
        xs = np.asarray([float(entry["x_um"]) for entry in results], dtype=float)
        ys = np.asarray([float(entry["y_um"]) for entry in results], dtype=float)
        zs = np.asarray([float(entry["z_um"]) for entry in results], dtype=float)
        fwhm = np.asarray(
            [float(entry["fwhm_um"]) if entry.get("fwhm_um") is not None else np.nan for entry in results],
            dtype=float,
        )
        scatter = ax.scatter(xs, ys, zs, c=fwhm, cmap="viridis", s=70)
        ax.set_xlabel("X (um)")
        ax.set_ylabel("Y (um)")
        ax.set_zlabel("Z (um)")
        ax.set_title("Axial FWHM across SLM XYZ positions")
        dialog.figure.colorbar(scatter, ax=ax, label="FWHM (um)")
        dialog.canvas.draw()
        dialog.exec()

    def _show_cross_section_plot(self) -> None:
        results = self._current_results()
        if not results:
            return
        axis = self.slice_axis_combo.currentText()
        fixed_values = {
            "x": self.fixed_x_combo.currentData(),
            "y": self.fixed_y_combo.currentData(),
            "z": self.fixed_z_combo.currentData(),
        }
        filtered: list[dict[str, object]] = []
        for entry in results:
            match = True
            for fixed_axis in ("x", "y", "z"):
                if fixed_axis == axis:
                    continue
                selected_value = fixed_values[fixed_axis]
                if selected_value is None:
                    continue
                if abs(float(entry[f"{fixed_axis}_um"]) - float(selected_value)) > 1e-9:
                    match = False
                    break
            if match and entry.get("fwhm_um") is not None:
                filtered.append(entry)
        if not filtered:
            QMessageBox.warning(self, "No Data", "No fitted volumes matched the requested cross section.")
            return
        filtered.sort(key=lambda entry: float(entry[f"{axis}_um"]))
        dialog = MatplotlibDialog("SLM PSF Cross Section", self)
        ax = dialog.figure.add_subplot(111)
        coords = np.asarray([float(entry[f"{axis}_um"]) for entry in filtered], dtype=float)
        fwhm = np.asarray([float(entry["fwhm_um"]) for entry in filtered], dtype=float)
        ax.plot(coords, fwhm, "o-k")
        ax.set_xlabel(f"{axis.upper()} (um)")
        ax.set_ylabel("FWHM (um)")
        fixed_desc = ", ".join(
            f"{fixed_axis.upper()}={_format_coord(float(fixed_values[fixed_axis]))}"
            for fixed_axis in ("x", "y", "z")
            if fixed_axis != axis and fixed_values[fixed_axis] is not None
        )
        ax.set_title(f"Cross section along {axis.upper()}" + (f" | {fixed_desc}" if fixed_desc else ""))
        ax.grid(True, alpha=0.3)
        dialog.canvas.draw()
        dialog.exec()
