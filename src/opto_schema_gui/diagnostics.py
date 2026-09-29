from __future__ import annotations

import json
import math
import re
import shutil
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
    QListWidget,
    QListWidgetItem,
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
FLATNESS_SUMMARY_FILENAME = "flatness_calibration_summary.json"
FLATNESS_AVERAGE_STACK_FILENAME = "flatness_slice_averages.tif"


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


def _default_flatness_output_root(animal_id: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"F:\\flatness calibration\\{animal_id.strip()}\\{stamp}"


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


def _load_volume_frame_stack(volume_dir: Path, *, exclude_filenames: set[str] | None = None) -> np.ndarray:
    excluded = {name.casefold() for name in (exclude_filenames or set())}
    paths_by_name: dict[str, Path] = {}
    for pattern in ("*.tif", "*.tiff", "*.TIF", "*.TIFF"):
        for path in volume_dir.glob(pattern):
            if path.name.casefold() not in excluded:
                paths_by_name.setdefault(path.name.casefold(), path)
    tiff_paths = [paths_by_name[name] for name in sorted(paths_by_name)]
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


def _slice_average_images(frames: np.ndarray, num_slices: int) -> np.ndarray:
    if frames.shape[0] < num_slices or frames.shape[0] % num_slices:
        raise ValueError(
            f"Frame count {frames.shape[0]} cannot be divided into {num_slices} flatness-calibration slices."
        )
    return frames.reshape(num_slices, frames.shape[0] // num_slices, *frames.shape[1:]).mean(axis=1)


def _sigmoid_with_offset(z: np.ndarray, amplitude: float, midpoint: float, width: float, offset: float) -> np.ndarray:
    exponent = np.clip(-(z - midpoint) / max(abs(width), 1e-9), -700.0, 700.0)
    return offset + amplitude / (1.0 + np.exp(exponent))


def _fit_surface_transition(z_um: np.ndarray, values: np.ndarray) -> dict[str, object]:
    offset0 = float(np.nanmin(values))
    amplitude0 = float(np.nanmax(values) - offset0)
    gradient = np.gradient(values, z_um)
    midpoint0 = float(z_um[int(np.nanargmax(np.abs(gradient)))])
    step = float(np.median(np.abs(np.diff(z_um)))) if z_um.size > 1 else 1.0
    try:
        params, _ = curve_fit(
            _sigmoid_with_offset,
            z_um,
            values,
            p0=[amplitude0 if amplitude0 else 1.0, midpoint0, max(step, 0.1), offset0],
            bounds=([-np.inf, float(z_um.min()), 1e-6, -np.inf], [np.inf, float(z_um.max()), np.inf, np.inf]),
            maxfev=20000,
        )
        amplitude, midpoint, width, offset = [float(value) for value in params]
        return {
            "ok": True,
            "amplitude": amplitude,
            "midpoint_um": midpoint,
            "width_um": width,
            "offset": offset,
            "fitted_intensity": _sigmoid_with_offset(z_um, amplitude, midpoint, width, offset).astype(float).tolist(),
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "midpoint_um": None, "fitted_intensity": []}


def _tile_center_coordinates(
    image_shape: tuple[int, int],
    row_indices: np.ndarray,
    col_indices: np.ndarray,
    fov_corners_um: np.ndarray,
) -> tuple[float, float]:
    """Map tile centres to physical image X/Y using the FOV bounding rectangle."""
    height, width = image_shape
    x_min, y_min = np.min(fov_corners_um, axis=0)
    x_max, y_max = np.max(fov_corners_um, axis=0)
    x_fraction = (float(np.mean(col_indices)) + 0.5) / max(width, 1)
    y_fraction = (float(np.mean(row_indices)) + 0.5) / max(height, 1)
    return float(x_min + x_fraction * (x_max - x_min)), float(y_min + y_fraction * (y_max - y_min))


def analyze_flatness_calibration_root(root_dir: Path) -> dict[str, object]:
    summary_path = root_dir / FLATNESS_SUMMARY_FILENAME
    if not summary_path.is_file():
        raise FileNotFoundError(f"Could not find {FLATNESS_SUMMARY_FILENAME} in {root_dir}")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    acquisition = summary["acquisition"]
    frames = _load_volume_frame_stack(root_dir, exclude_filenames={FLATNESS_AVERAGE_STACK_FILENAME})
    num_slices = int(acquisition["num_slices"])
    z_step_um = float(acquisition["z_step_um"])
    z_positions_um = (np.arange(num_slices, dtype=float) - (num_slices - 1) / 2.0) * z_step_um
    slice_images = _slice_average_images(frames, num_slices)
    tifffile.imwrite(root_dir / FLATNESS_AVERAGE_STACK_FILENAME, slice_images.astype(np.float32))
    fov_corners_um = np.asarray(summary["fov_corners_um"], dtype=float)
    if fov_corners_um.ndim != 2 or fov_corners_um.shape[1] != 2:
        raise ValueError("Flatness calibration has no valid ScanImage FOV coordinates.")
    rows = max(1, int(acquisition["tile_rows"]))
    cols = max(1, int(acquisition["tile_columns"]))
    height, width = slice_images.shape[1:]
    row_chunks = [chunk for chunk in np.array_split(np.arange(height), rows) if chunk.size]
    col_chunks = [chunk for chunk in np.array_split(np.arange(width), cols) if chunk.size]
    tiles: list[dict[str, object]] = []
    for row_index, row_pixels in enumerate(row_chunks):
        for col_index, col_pixels in enumerate(col_chunks):
            profile = slice_images[:, row_pixels[:, None], col_pixels].mean(axis=(1, 2))
            fit = _fit_surface_transition(z_positions_um, profile)
            midpoint = fit.get("midpoint_um")
            transition_slice_index = (
                int(np.argmin(np.abs(z_positions_um - float(midpoint)))) if midpoint is not None else None
            )
            x_um, y_um = _tile_center_coordinates((height, width), row_pixels, col_pixels, fov_corners_um)
            tiles.append(
                {
                    "row": row_index,
                    "column": col_index,
                    "x_um": x_um,
                    "y_um": y_um,
                    "z_positions_um": z_positions_um.astype(float).tolist(),
                    "raw_intensity": profile.astype(float).tolist(),
                    "fit": fit,
                    "transition_slice_index": transition_slice_index,
                }
            )
    valid_tiles = [tile for tile in tiles if tile["fit"].get("midpoint_um") is not None]
    if len(valid_tiles) < 3:
        raise RuntimeError("Fewer than three tiles had usable surface-transition fits; cannot fit a plane.")
    design = np.asarray([[tile["x_um"], tile["y_um"], 1.0] for tile in valid_tiles], dtype=float)
    depths = np.asarray([tile["fit"]["midpoint_um"] for tile in valid_tiles], dtype=float)
    slope_x, slope_y, intercept = np.linalg.lstsq(design, depths, rcond=None)[0]
    prediction = design @ np.asarray([slope_x, slope_y, intercept])
    residual_rms_um = float(np.sqrt(np.mean((depths - prediction) ** 2)))
    plane = {
        "equation": "z_um = slope_dz_dx * x_um + slope_dz_dy * y_um + intercept_um",
        "slope_dz_dx": float(slope_x),
        "slope_dz_dy": float(slope_y),
        "intercept_um": float(intercept),
        "tilt_about_y_deg": float(math.degrees(math.atan(slope_x))),
        "tilt_about_x_deg": float(math.degrees(math.atan(slope_y))),
        "correction_about_y_deg": float(-math.degrees(math.atan(slope_x))),
        "correction_about_x_deg": float(-math.degrees(math.atan(slope_y))),
        "residual_rms_um": residual_rms_um,
    }
    summary["processed_at"] = datetime.now().isoformat(timespec="seconds")
    summary["z_positions_um"] = z_positions_um.astype(float).tolist()
    summary["slice_image_shape"] = [int(height), int(width)]
    summary["slice_average_stack_file"] = FLATNESS_AVERAGE_STACK_FILENAME
    summary["tiles"] = tiles
    summary["plane"] = plane
    _safe_json_dump(summary_path, summary)
    return summary


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
    num_slices: int = 5
    frames_per_slice: int = 10
    log_average_factor: int = 1
    display_average_factor: int = 5
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


@dataclass
class PhotostimGridParams:
    path_name: str
    x_values_um: list[float]
    y_values_um: list[float]
    z_values_um: list[float]
    spiral_width_um: float = 15.0
    spiral_height_um: float = 15.0
    power_percent: float = 30.0
    pause_duration_s: float = 0.010
    stim_duration_s: float = 0.010

    def point_rows_um(self) -> list[list[float]]:
        rows: list[list[float]] = []
        for z_um in self.z_values_um:
            for y_um in self.y_values_um:
                for x_um in self.x_values_um:
                    rows.append([float(x_um), float(y_um), float(z_um), 1.0])
        return rows


@dataclass
class FlatnessCalibrationParams:
    path_name: str
    animal_id: str
    output_root: str
    z_step_um: float = 5.0
    z_range_um: float = 50.0
    frames_per_slice: int = 10
    display_average_factor: int = 5
    tile_rows: int = 10
    tile_columns: int = 10

    @property
    def num_slices(self) -> int:
        return int(round((2.0 * self.z_range_um) / self.z_step_um)) + 1


class FlatnessCalibrationConfigDialog(QDialog):
    def __init__(self, path_names: list[str], default_path_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Acquire Surface Flatness Calibration")
        self.resize(600, 420)
        layout = QVBoxLayout(self)
        info = QLabel(
            "The next step enters focus mode so you can place the surface transition near the middle of the current "
            "field of view. The calibration then preserves the current FOV and zoom, acquires a motor-centred stack, "
            "and calculates the sample tilt from tiled surface-transition fits."
        )
        info.setWordWrap(True)
        layout.addWidget(info)
        form_box = QGroupBox("Acquisition Parameters")
        form = QFormLayout(form_box)
        self.path_combo = QComboBox()
        self.path_combo.addItems(path_names)
        index = self.path_combo.findText(default_path_name)
        if index >= 0:
            self.path_combo.setCurrentIndex(index)
        self.animal_id_edit = QLineEdit()
        self.output_root_edit = QLineEdit()
        self.animal_id_edit.textChanged.connect(self._update_output_root)
        browse_button = QPushButton("Browse…")
        browse_button.clicked.connect(self._browse_output_root)
        output_row = QHBoxLayout()
        output_row.addWidget(self.output_root_edit, 1)
        output_row.addWidget(browse_button)
        output_widget = QWidget()
        output_widget.setLayout(output_row)
        self.z_step_spin = QDoubleSpinBox()
        self.z_step_spin.setRange(0.1, 100.0)
        self.z_step_spin.setDecimals(3)
        self.z_step_spin.setValue(5.0)
        self.z_range_spin = QDoubleSpinBox()
        self.z_range_spin.setRange(1.0, 500.0)
        self.z_range_spin.setDecimals(3)
        self.z_range_spin.setValue(50.0)
        self.frames_per_slice_spin = QSpinBox()
        self.frames_per_slice_spin.setRange(1, 1000)
        self.frames_per_slice_spin.setValue(10)
        self.display_average_spin = QSpinBox()
        self.display_average_spin.setRange(1, 1000)
        self.display_average_spin.setValue(5)
        self.tile_rows_spin = QSpinBox()
        self.tile_rows_spin.setRange(1, 100)
        self.tile_rows_spin.setValue(10)
        self.tile_columns_spin = QSpinBox()
        self.tile_columns_spin.setRange(1, 100)
        self.tile_columns_spin.setValue(10)
        form.addRow("ScanImage path", self.path_combo)
        form.addRow("Animal ID", self.animal_id_edit)
        form.addRow("Output folder", output_widget)
        form.addRow("Z step (um)", self.z_step_spin)
        form.addRow("Range above/below focus (um)", self.z_range_spin)
        form.addRow("Frames per slice", self.frames_per_slice_spin)
        form.addRow("Saved-frame logging", QLabel("All frames (exact average during processing)"))
        form.addRow("Display average", self.display_average_spin)
        form.addRow("Tile rows", self.tile_rows_spin)
        form.addRow("Tile columns", self.tile_columns_spin)
        layout.addWidget(form_box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _update_output_root(self, animal_id: str) -> None:
        if animal_id.strip():
            self.output_root_edit.setText(_default_flatness_output_root(animal_id))

    def _browse_output_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "Select flatness calibration output folder", self.output_root_edit.text())
        if selected:
            self.output_root_edit.setText(selected)

    def _accept_if_valid(self) -> None:
        try:
            self.gather_params()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Flatness Calibration Settings", str(exc))
            return
        self.accept()

    def gather_params(self) -> FlatnessCalibrationParams:
        animal_id = self.animal_id_edit.text().strip()
        if not animal_id:
            raise ValueError("Animal ID is required.")
        output_root = self.output_root_edit.text().strip()
        if not output_root:
            raise ValueError("Output folder is required.")
        path_name = self.path_combo.currentText().strip()
        if not path_name:
            raise ValueError("A ScanImage path must be selected.")
        z_step_um = self.z_step_spin.value()
        z_range_um = self.z_range_spin.value()
        ratio = (2.0 * z_range_um) / z_step_um
        if abs(ratio - round(ratio)) > 1e-6:
            raise ValueError("Twice the Z range must be divisible by the Z step so the stack is centred on focus.")
        return FlatnessCalibrationParams(
            path_name=path_name,
            animal_id=animal_id,
            output_root=output_root,
            z_step_um=z_step_um,
            z_range_um=z_range_um,
            frames_per_slice=self.frames_per_slice_spin.value(),
            display_average_factor=self.display_average_spin.value(),
            tile_rows=self.tile_rows_spin.value(),
            tile_columns=self.tile_columns_spin.value(),
        )


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
        self.setWindowTitle("Acquire SLM Volume for PSF")
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
        self.num_slices_spin.setValue(5)
        self.frames_per_slice_spin = QSpinBox()
        self.frames_per_slice_spin.setRange(1, 10000)
        self.frames_per_slice_spin.setValue(10)
        self.log_average_spin = QSpinBox()
        self.log_average_spin.setRange(1, 10000)
        self.log_average_spin.setValue(1)
        self.log_average_spin.setEnabled(False)
        self.display_average_spin = QSpinBox()
        self.display_average_spin.setRange(1, 10000)
        self.display_average_spin.setValue(5)
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
        form.addRow("Saved-frame logging", QLabel("All frames (exact average during processing)"))
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
        self.frames_per_slice_spin.valueChanged.connect(self._sync_log_average_to_frames_per_slice)
        layout.addWidget(buttons)

    def _sync_log_average_to_frames_per_slice(self) -> None:
        self.log_average_spin.setValue(1)

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
        log_average_factor = 1
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


class PhotostimGridConfigDialog(QDialog):
    def __init__(self, path_names: list[str], default_path_name: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Generate Photostim Grid")
        self.resize(520, 420)
        layout = QVBoxLayout(self)

        info = QLabel(
            "Generate a live photostim grid directly in ScanImage. "
            "This clears existing photostim groups, creates one sequence group containing a pause, the grid stimulation, and a park, then starts the sequence."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        form_box = QGroupBox("Grid Parameters")
        form = QFormLayout(form_box)
        self.path_combo = QComboBox()
        self.path_combo.addItems(path_names)
        if default_path_name:
            index = self.path_combo.findText(default_path_name)
            if index >= 0:
                self.path_combo.setCurrentIndex(index)
        self.x_edit = QLineEdit("[-150:100:150]")
        self.y_edit = QLineEdit("[-150:100:150]")
        self.z_edit = QLineEdit("[0]")
        self.power_spin = QDoubleSpinBox()
        self.power_spin.setRange(0.0, 100.0)
        self.power_spin.setDecimals(3)
        self.power_spin.setValue(30.0)
        self.spiral_width_spin = QDoubleSpinBox()
        self.spiral_width_spin.setRange(0.001, 1000.0)
        self.spiral_width_spin.setDecimals(3)
        self.spiral_width_spin.setValue(15.0)
        self.spiral_height_spin = QDoubleSpinBox()
        self.spiral_height_spin.setRange(0.001, 1000.0)
        self.spiral_height_spin.setDecimals(3)
        self.spiral_height_spin.setValue(15.0)
        self.pause_ms_spin = QDoubleSpinBox()
        self.pause_ms_spin.setRange(0.0, 10000.0)
        self.pause_ms_spin.setDecimals(3)
        self.pause_ms_spin.setValue(10.0)
        self.stim_ms_spin = QDoubleSpinBox()
        self.stim_ms_spin.setRange(0.001, 10000.0)
        self.stim_ms_spin.setDecimals(3)
        self.stim_ms_spin.setValue(10.0)

        form.addRow("Path", self.path_combo)
        form.addRow("X grid (um)", self.x_edit)
        form.addRow("Y grid (um)", self.y_edit)
        form.addRow("Z grid (um)", self.z_edit)
        form.addRow("Laser 3 power (%)", self.power_spin)
        form.addRow("Spiral width (um)", self.spiral_width_spin)
        form.addRow("Spiral height (um)", self.spiral_height_spin)
        form.addRow("Pause (ms)", self.pause_ms_spin)
        form.addRow("Stim duration (ms)", self.stim_ms_spin)
        layout.addWidget(form_box)

        buttons = QDialogButtonBox()
        self.generate_button = buttons.addButton("Generate", QDialogButtonBox.ButtonRole.AcceptRole)
        self.cancel_button = buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        self.generate_button.clicked.connect(self._accept_if_valid)
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(buttons)

    def _accept_if_valid(self) -> None:
        try:
            self.gather_params()
        except ValueError as exc:
            QMessageBox.warning(self, "Invalid Photostim Grid Settings", str(exc))
            return
        self.accept()

    def gather_params(self) -> PhotostimGridParams:
        path_name = self.path_combo.currentText().strip()
        if not path_name:
            raise ValueError("A ScanImage path must be selected.")
        x_values = _parse_axis_values(self.x_edit.text())
        y_values = _parse_axis_values(self.y_edit.text())
        z_values = _parse_axis_values(self.z_edit.text())
        if not x_values or not y_values or not z_values:
            raise ValueError("Grid axes must each contain at least one value.")
        return PhotostimGridParams(
            path_name=path_name,
            x_values_um=x_values,
            y_values_um=y_values,
            z_values_um=z_values,
            spiral_width_um=self.spiral_width_spin.value(),
            spiral_height_um=self.spiral_height_spin.value(),
            power_percent=self.power_spin.value(),
            pause_duration_s=self.pause_ms_spin.value() / 1000.0,
            stim_duration_s=self.stim_ms_spin.value() / 1000.0,
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
        self.acquire_button = QPushButton("Acquire SLM volume for PSF")
        self.generate_grid_button = QPushButton("Generate Photostim Grid")
        self.abort_button = QPushButton("Abort")
        self.open_existing_button = QPushButton("Open Existing Result")
        button_row.addWidget(self.acquire_button)
        button_row.addWidget(self.generate_grid_button)
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
        self.generate_grid_button.clicked.connect(self._show_photostim_grid_dialog)
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
        self.generate_grid_button.setEnabled(not running)
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

    def _show_flatness_calibration_dialog(self) -> None:
        if self._worker_thread is not None and self._worker_thread.is_alive():
            QMessageBox.warning(self, "Diagnostics Busy", "A diagnostic run is already in progress.")
            return
        dialog = FlatnessCalibrationConfigDialog(
            self.scanimage_control.available_path_names(),
            self.scanimage_control.preferred_photostim_path_name(),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        params = dialog.gather_params()
        try:
            self.scanimage_control.start_flatness_focus(params.path_name)
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Start Focus", str(exc))
            return
        confirm = QMessageBox(self)
        confirm.setWindowTitle("Position Surface Transition")
        confirm.setText(
            "Focus mode is now active. Adjust the current frame/zoom and sample position until the surface transition "
            "is approximately centred in the image.\n\nClick Acquire when ready."
        )
        confirm.setInformativeText("The current FOV and zoom will be used unchanged for the centred +/- range Z stack.")
        acquire_button = confirm.addButton("Acquire", QMessageBox.ButtonRole.AcceptRole)
        confirm.addButton(QMessageBox.StandardButton.Cancel)
        confirm.exec()
        try:
            self.scanimage_control.stop_flatness_focus(params.path_name)
        except Exception as exc:
            QMessageBox.critical(self, "Could Not Stop Focus", str(exc))
            return
        if confirm.clickedButton() is not acquire_button:
            self._append_status("Surface flatness calibration cancelled before stack acquisition.")
            return
        self._start_flatness_calibration(params)

    def _show_photostim_grid_dialog(self) -> None:
        dialog = PhotostimGridConfigDialog(
            self.scanimage_control.available_path_names(),
            self.scanimage_control.preferred_photostim_path_name(),
            self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        params = dialog.gather_params()
        self.scanimage_control.generate_diagnostic_photostim_grid(
            params.path_name,
            point_rows_um=params.point_rows_um(),
            spiral_width_um=params.spiral_width_um,
            spiral_height_um=params.spiral_height_um,
            pause_duration_s=params.pause_duration_s,
            stim_duration_s=params.stim_duration_s,
            power_percent=params.power_percent,
        )
        self._append_status(
            f"Requested photostim grid generation on {params.path_name} with {len(params.point_rows_um())} point(s)."
        )

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

    def _start_flatness_calibration(self, params: FlatnessCalibrationParams) -> None:
        root_dir = Path(params.output_root)
        root_dir.mkdir(parents=True, exist_ok=True)
        if any(root_dir.iterdir()):
            QMessageBox.warning(
                self,
                "Output Folder Not Empty",
                f"Choose a new empty run folder for this calibration:\n{root_dir}",
            )
            return
        summary = {
            "tool": "surface_flatness_calibration",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "animal_id": params.animal_id,
            "acquisition": {
                **asdict(params),
                "num_slices": params.num_slices,
                "log_average_factor": 1,
                "output_root": str(root_dir),
            },
        }
        self._current_root_dir = root_dir
        self._cancel_event.clear()
        self._set_running(True)
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self._append_status(f"Starting surface flatness calibration in {root_dir}")

        def progress_callback(done: int, total: int, message: str) -> None:
            self._signals.progress.emit(done, total, message)

        def cancel_check() -> bool:
            return self._cancel_event.is_set()

        def worker() -> None:
            try:
                fov_corners_um = self.scanimage_control.run_flatness_calibration(
                    params.path_name,
                    output_dir=str(root_dir),
                    num_slices=params.num_slices,
                    frames_per_slice=params.frames_per_slice,
                    z_step_um=params.z_step_um,
                    log_average_factor=1,
                    display_average_factor=params.display_average_factor,
                    progress_callback=progress_callback,
                    cancel_check=cancel_check,
                )
                if cancel_check():
                    raise RuntimeError("Flatness calibration aborted.")
                summary["fov_corners_um"] = fov_corners_um
                _safe_json_dump(root_dir / FLATNESS_SUMMARY_FILENAME, summary)
                processed = analyze_flatness_calibration_root(root_dir)
                self._signals.finished.emit(True, ("flatness", processed))
            except Exception as exc:
                self._signals.finished.emit(False, ("flatness", str(exc)))

        self._worker_thread = threading.Thread(target=worker, daemon=True)
        self._worker_thread.start()

    def _handle_progress(self, done: int, total: int, message: str) -> None:
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)
        self._append_status(message)

    def _handle_finished(self, ok: bool, payload: object) -> None:
        self._set_running(False)
        run_type, result = payload if isinstance(payload, tuple) else ("slm_psf", payload)
        if not ok:
            if str(result) in {"SLM PSF diagnostic aborted.", "Flatness calibration aborted."}:
                self._append_status(f"{run_type.replace('_', ' ').title()} acquisition aborted.")
                self._prompt_delete_aborted_data()
                return
            self._append_status(f"{run_type.replace('_', ' ').title()} run failed: {result}")
            QMessageBox.critical(self, "Diagnostic Run Failed", str(result))
            return
        assert isinstance(result, dict)
        if run_type == "flatness":
            self._append_status("Surface flatness acquisition and processing completed.")
            plane = result["plane"]
            self.summary_label.setText(
                "Surface flatness result: "
                f"correct about X by {plane['correction_about_x_deg']:.3f} deg; "
                f"about Y by {plane['correction_about_y_deg']:.3f} deg "
                f"(plane residual RMS {plane['residual_rms_um']:.2f} um)."
            )
            QMessageBox.information(
                self,
                "Surface Flatness Correction",
                "Measured sample tilt:\n"
                f"  about X: {plane['tilt_about_x_deg']:.3f} deg\n"
                f"  about Y: {plane['tilt_about_y_deg']:.3f} deg\n\n"
                "Apply the opposite correction (subject to your stage's axis/sign convention):\n"
                f"  about X: {plane['correction_about_x_deg']:.3f} deg\n"
                f"  about Y: {plane['correction_about_y_deg']:.3f} deg",
            )
            return
        self._append_status("SLM PSF acquisition and processing completed.")
        self._set_summary(result)

    def _request_abort(self) -> None:
        if self._worker_thread is None or not self._worker_thread.is_alive():
            return
        self._cancel_event.set()
        self._append_status("Aborting diagnostic acquisition...")

    def _prompt_delete_aborted_data(self) -> None:
        root_dir = self._current_root_dir
        if root_dir is None or not root_dir.exists():
            return
        answer = QMessageBox.question(
            self,
            "Delete Aborted Data?",
            f"Delete the partially acquired SLM PSF data in:\n{root_dir}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(root_dir)
            self._append_status(f"Deleted aborted SLM PSF data at {root_dir}")
            if self._current_root_dir == root_dir:
                self._current_root_dir = None
                self._current_summary = None
                self.summary_label.setText("No processed SLM PSF dataset loaded.")
                self._set_visualization_enabled(False)
        except Exception as exc:
            QMessageBox.critical(self, "Delete Aborted Data Failed", str(exc))

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


class FlattenWindow(DiagnosticsWidget):
    """Dedicated acquisition and review window for sample-surface flatness."""

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        intro = QGroupBox("Surface Flatness Correction")
        intro_layout = QVBoxLayout(intro)
        description = QLabel(
            "Position the surface transition in focus mode, acquire a centred motor Z stack, then fit a sigmoid "
            "to each image tile. Tile transition depths form the measured surface; a plane fit reports the required "
            "two-axis tilt correction."
        )
        description.setWordWrap(True)
        intro_layout.addWidget(description)
        layout.addWidget(intro)

        action_row = QHBoxLayout()
        self.acquire_flatness_button = QPushButton("Acquire Flatness Calibration")
        self.abort_button = QPushButton("Abort")
        action_row.addWidget(self.acquire_flatness_button)
        action_row.addWidget(self.abort_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        load_box = QGroupBox("Load Calibration")
        load_layout = QHBoxLayout(load_box)
        self.calibration_id_edit = QLineEdit()
        self.calibration_id_edit.setPlaceholderText("Animal ID")
        self.load_calibration_button = QPushButton("Load")
        load_layout.addWidget(QLabel("Animal ID"))
        load_layout.addWidget(self.calibration_id_edit, 1)
        load_layout.addWidget(self.load_calibration_button)
        layout.addWidget(load_box)

        result_box = QGroupBox("Current Result")
        result_layout = QVBoxLayout(result_box)
        self.summary_label = QLabel("No flatness calibration loaded.")
        self.summary_label.setWordWrap(True)
        self.correction_label = QLabel("")
        self.correction_label.setWordWrap(True)
        result_layout.addWidget(self.summary_label)
        result_layout.addWidget(self.correction_label)
        layout.addWidget(result_box)

        view_row = QHBoxLayout()
        self.tile_frames_button = QPushButton("Tile Transition Frames")
        self.plane_views_button = QPushButton("Surface and Plane Views")
        view_row.addWidget(self.tile_frames_button)
        view_row.addWidget(self.plane_views_button)
        view_row.addStretch(1)
        layout.addLayout(view_row)

        status_box = QGroupBox("Run Status")
        status_layout = QVBoxLayout(status_box)
        self.status_label = QLabel("Idle")
        self.status_label.setWordWrap(True)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(0)
        self.log_text = QPlainTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setMaximumBlockCount(500)
        self.log_text.setMinimumHeight(140)
        status_layout.addWidget(self.status_label)
        status_layout.addWidget(self.progress_bar)
        status_layout.addWidget(self.log_text)
        layout.addWidget(status_box, 1)

        self.acquire_flatness_button.clicked.connect(self._show_flatness_calibration_dialog)
        self.abort_button.clicked.connect(self._request_abort)
        self.load_calibration_button.clicked.connect(self._choose_calibration_to_load)
        self.tile_frames_button.clicked.connect(self._show_tile_transition_frames)
        self.plane_views_button.clicked.connect(self._show_surface_and_plane_views)
        self.abort_button.setEnabled(False)
        self.tile_frames_button.setEnabled(False)
        self.plane_views_button.setEnabled(False)
        self._flatness_summary: dict[str, object] | None = None

    def _set_running(self, running: bool) -> None:
        self.acquire_flatness_button.setEnabled(not running)
        self.abort_button.setEnabled(running)
        self.load_calibration_button.setEnabled(not running)

    def _set_visualization_enabled(self, enabled: bool) -> None:
        self.tile_frames_button.setEnabled(enabled)
        self.plane_views_button.setEnabled(enabled)

    def _handle_finished(self, ok: bool, payload: object) -> None:
        super()._handle_finished(ok, payload)
        run_type, result = payload if isinstance(payload, tuple) else ("slm_psf", payload)
        if ok and run_type == "flatness" and isinstance(result, dict):
            self._set_flatness_summary(result)

    def _set_flatness_summary(self, summary: dict[str, object]) -> None:
        self._flatness_summary = summary
        self._current_root_dir = Path(str(summary["acquisition"]["output_root"]))
        plane = summary["plane"]
        tiles = summary.get("tiles", [])
        self.summary_label.setText(
            f"Loaded {len(tiles)} tile fits from {self._current_root_dir}. "
            f"Plane residual RMS: {float(plane['residual_rms_um']):.2f} um."
        )
        self.correction_label.setText(
            "Measured non-flatness: "
            f"{float(plane['tilt_about_x_deg']):.3f} deg about X, "
            f"{float(plane['tilt_about_y_deg']):.3f} deg about Y.\n"
            "Apply opposite correction, after confirming the mechanical sign convention: "
            f"{float(plane['correction_about_x_deg']):.3f} deg about X, "
            f"{float(plane['correction_about_y_deg']):.3f} deg about Y."
        )
        self._set_visualization_enabled(True)

    def _prompt_delete_aborted_data(self) -> None:
        root_dir = self._current_root_dir
        if root_dir is None or not root_dir.exists():
            return
        answer = QMessageBox.question(
            self,
            "Delete Aborted Data?",
            f"Delete the partially acquired flatness-calibration data in:\n{root_dir}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            shutil.rmtree(root_dir)
            self._append_status(f"Deleted aborted flatness calibration at {root_dir}")

    def _choose_calibration_to_load(self) -> None:
        identifier = self.calibration_id_edit.text().strip()
        if not identifier:
            QMessageBox.warning(self, "Animal ID Required", "Enter an animal ID to search for calibrations.")
            return
        root = Path(r"F:\flatness calibration")
        if not root.is_dir():
            QMessageBox.warning(self, "Calibration Folder Missing", f"Could not find {root}")
            return
        candidates: list[tuple[Path, dict[str, object]]] = []
        for summary_path in root.glob(f"**/{FLATNESS_SUMMARY_FILENAME}"):
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if str(summary.get("animal_id", "")).casefold() == identifier.casefold():
                candidates.append((summary_path.parent, summary))
        if not candidates:
            QMessageBox.information(self, "No Calibrations", f"No completed flatness calibrations matched '{identifier}'.")
            return
        candidates.sort(key=lambda entry: str(entry[1].get("created_at", "")), reverse=True)
        dialog = QDialog(self)
        dialog.setWindowTitle("Select Flatness Calibration")
        dialog.resize(760, 360)
        dialog_layout = QVBoxLayout(dialog)
        list_widget = QListWidget()
        for path, summary in candidates:
            plane = summary.get("plane", {})
            label = (
                f"{summary.get('created_at', 'unknown time')} | {path}\n"
                f"X {float(plane.get('correction_about_x_deg', float('nan'))):.3f} deg, "
                f"Y {float(plane.get('correction_about_y_deg', float('nan'))):.3f} deg"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            list_widget.addItem(item)
        dialog_layout.addWidget(list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        dialog_layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted or list_widget.currentItem() is None:
            return
        calibration_dir = Path(str(list_widget.currentItem().data(Qt.ItemDataRole.UserRole)))
        try:
            self._set_flatness_summary(analyze_flatness_calibration_root(calibration_dir))
            self._append_status(f"Loaded flatness calibration from {calibration_dir}")
        except Exception as exc:
            QMessageBox.critical(self, "Load Flatness Calibration Failed", str(exc))

    def _show_tile_transition_frames(self) -> None:
        if self._flatness_summary is None or self._current_root_dir is None:
            return
        stack_file = self._current_root_dir / str(
            self._flatness_summary.get("slice_average_stack_file", FLATNESS_AVERAGE_STACK_FILENAME)
        )
        if not stack_file.is_file():
            QMessageBox.warning(self, "Transition Frames Missing", f"Could not find saved averaged stack:\n{stack_file}")
            return
        stack = _normalize_frame_stack(tifffile.imread(stack_file))
        acquisition = self._flatness_summary["acquisition"]
        row_chunks = [chunk for chunk in np.array_split(np.arange(stack.shape[1]), int(acquisition["tile_rows"])) if chunk.size]
        col_chunks = [chunk for chunk in np.array_split(np.arange(stack.shape[2]), int(acquisition["tile_columns"])) if chunk.size]
        tiles = list(self._flatness_summary.get("tiles", []))
        dialog = MatplotlibDialog("Tile Frames at Fitted Surface Transition", self)
        columns = max(1, len(col_chunks))
        axis_tiles: dict[object, tuple[dict[str, object], np.ndarray]] = {}
        for tile in tiles:
            row = int(tile["row"])
            col = int(tile["column"])
            transition_index = tile.get("transition_slice_index")
            if transition_index is None or row >= len(row_chunks) or col >= len(col_chunks):
                continue
            axis = dialog.figure.add_subplot(len(row_chunks), columns, row * columns + col + 1)
            image = stack[int(transition_index), row_chunks[row][:, None], col_chunks[col]]
            axis.imshow(image, cmap="gray")
            axis_tiles[axis] = (tile, image)
            midpoint = tile["fit"].get("midpoint_um")
            axis.set_title(f"r{row + 1} c{col + 1}\nz={float(midpoint):.1f} um", fontsize=7)
            axis.set_xticks([])
            axis.set_yticks([])

        def show_tile_detail(event) -> None:
            if not event.dblclick or event.inaxes not in axis_tiles:
                return
            tile, image = axis_tiles[event.inaxes]
            self._show_tile_transition_detail(tile, image)

        dialog.canvas.mpl_connect("button_press_event", show_tile_detail)
        dialog.figure.suptitle("Each tile at the Z plane nearest its fitted sigmoid midpoint", fontsize=11)
        dialog.canvas.draw()
        dialog.exec()

    def _show_tile_transition_detail(self, tile: dict[str, object], image: np.ndarray) -> None:
        """Show the selected transition frame alongside its complete Z profile."""
        fit = tile["fit"]
        z_um = np.asarray(tile["z_positions_um"], dtype=float)
        intensity = np.asarray(tile["raw_intensity"], dtype=float)
        midpoint = fit.get("midpoint_um")
        dialog = MatplotlibDialog(
            f"Tile r{int(tile['row']) + 1} c{int(tile['column']) + 1} Surface Transition",
            self,
        )
        image_axis = dialog.figure.add_subplot(121)
        profile_axis = dialog.figure.add_subplot(122)
        image_axis.imshow(image, cmap="gray")
        image_axis.set_title(
            f"Frame nearest fitted midpoint\nZ={float(midpoint):.2f} um" if midpoint is not None else "No fitted midpoint"
        )
        image_axis.set_xticks([])
        image_axis.set_yticks([])
        profile_axis.plot(z_um, intensity, "o-", color="tab:blue", label="Tile mean brightness")
        fitted = np.asarray(fit.get("fitted_intensity", []), dtype=float)
        if fitted.size == z_um.size:
            profile_axis.plot(z_um, fitted, "-", color="tab:red", linewidth=2, label="Sigmoid fit")
        if midpoint is not None:
            profile_axis.axvline(float(midpoint), color="black", linestyle="--", label=f"Midpoint {float(midpoint):.2f} um")
        profile_axis.set_xlabel("Relative Z (um)")
        profile_axis.set_ylabel("Mean tile brightness")
        profile_axis.set_title("Brightness across the full acquired Z range")
        profile_axis.grid(True, alpha=0.3)
        profile_axis.legend(fontsize=8)
        dialog.canvas.draw()
        dialog.exec()

    def _show_surface_and_plane_views(self) -> None:
        if self._flatness_summary is None:
            return
        tiles = [tile for tile in self._flatness_summary.get("tiles", []) if tile["fit"].get("midpoint_um") is not None]
        if not tiles:
            return
        plane = self._flatness_summary["plane"]
        x = np.asarray([float(tile["x_um"]) for tile in tiles])
        y = np.asarray([float(tile["y_um"]) for tile in tiles])
        z = np.asarray([float(tile["fit"]["midpoint_um"]) for tile in tiles])
        slope_x = float(plane["slope_dz_dx"])
        slope_y = float(plane["slope_dz_dy"])
        intercept = float(plane["intercept_um"])
        dialog = MatplotlibDialog("Measured Surface and Fitted Plane", self)
        axis_y = dialog.figure.add_subplot(131)
        axis_x = dialog.figure.add_subplot(132)
        axis_3d = dialog.figure.add_subplot(133, projection="3d")
        y_line = np.linspace(y.min(), y.max(), 100)
        x_centre = float(np.mean(x))
        axis_y.scatter(y, z, c="tab:blue", label="Tile transition")
        axis_y.plot(y_line, slope_x * x_centre + slope_y * y_line + intercept, "r", label="Fitted plane")
        axis_y.set_title(f"Across Y | tilt about X = {float(plane['tilt_about_x_deg']):.3f} deg")
        axis_y.set_xlabel("Y (um)")
        axis_y.set_ylabel("Surface Z (um)")
        axis_y.grid(True, alpha=0.3)
        axis_y.legend(fontsize=8)
        x_line = np.linspace(x.min(), x.max(), 100)
        y_centre = float(np.mean(y))
        axis_x.scatter(x, z, c="tab:blue", label="Tile transition")
        axis_x.plot(x_line, slope_x * x_line + slope_y * y_centre + intercept, "r", label="Fitted plane")
        axis_x.set_title(f"Across X | tilt about Y = {float(plane['tilt_about_y_deg']):.3f} deg")
        axis_x.set_xlabel("X (um)")
        axis_x.set_ylabel("Surface Z (um)")
        axis_x.grid(True, alpha=0.3)
        axis_x.legend(fontsize=8)
        x_grid, y_grid = np.meshgrid(np.linspace(x.min(), x.max(), 20), np.linspace(y.min(), y.max(), 20))
        z_grid = slope_x * x_grid + slope_y * y_grid + intercept
        axis_3d.scatter(x, y, z, c="tab:blue", s=24, label="Measured tile transitions")
        axis_3d.plot_surface(x_grid, y_grid, z_grid, color="tab:red", alpha=0.45, label="Fitted plane")
        axis_3d.set_title("Measured surface and fitted plane")
        axis_3d.set_xlabel("X (um)")
        axis_3d.set_ylabel("Y (um)")
        axis_3d.set_zlabel("Surface Z (um)")
        axis_3d.legend(fontsize=8)
        dialog.canvas.draw()
        dialog.exec()
