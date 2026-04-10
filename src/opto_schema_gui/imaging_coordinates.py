from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .matlab_bridge import autodetect_machine_name, load_machine_config


@dataclass(frozen=True)
class ScanfieldChoice:
    index: int
    label: str
    roi_name: str
    roi_folder_name: str
    scanfield_name: str
    z_um: float
    pixel_resolution_xy: tuple[int, int]
    pixel_to_ref_transform: np.ndarray


@dataclass(frozen=True)
class MetadataBundle:
    source: str
    exp_dir: Path
    scanfields: tuple[ScanfieldChoice, ...]
    note: str = ""


@dataclass(frozen=True)
class ConvertedPatternCoordinate:
    x_um: float
    y_um: float
    z_um: float
    source: str
    note: str
    exp_dir: Path
    scanfield_label: str


def list_imaging_scanfields(
    repo_root: Path,
    exp_id: str,
    imaging_path: str = "P1",
    photostim_path: str = "PS",
) -> MetadataBundle:
    _require_linux_roi_import()
    imaging_config, _ = _load_v1_configs(repo_root, imaging_path, photostim_path)
    exp_dir = _resolve_experiment_dir(exp_id, imaging_config, imaging_path)

    roi_files = _find_selected_scanfield_roi_files(exp_dir)
    for roi_file in roi_files:
        bundle = _load_scanfields_from_roi_file(roi_file)
        if bundle is not None:
            return MetadataBundle(
                source=f"selectedScanfield.roi ({roi_file.name})",
                exp_dir=exp_dir,
                scanfields=bundle,
            )

    tiff_files = _find_tiff_files(exp_dir)
    for tiff_file in tiff_files:
        bundle = _load_scanfields_from_tiff_header(tiff_file)
        if bundle is not None:
            note = "TIFF fallback metadata used; full scanfield sidecar was not found."
            return MetadataBundle(
                source=f"TIFF header ({tiff_file.name})",
                exp_dir=exp_dir,
                scanfields=bundle,
                note=note,
            )

    raise FileNotFoundError(
        f"Could not find selected scanfield metadata or TIFF files for experiment '{exp_id}' in '{exp_dir}'."
    )


def convert_imaging_pixel_to_pattern_coords(
    repo_root: Path,
    exp_id: str,
    scanfield_index: int,
    x_px: float,
    y_px: float,
    imaging_path: str = "P1",
    photostim_path: str = "PS",
) -> ConvertedPatternCoordinate:
    _require_linux_roi_import()
    bundle = list_imaging_scanfields(repo_root, exp_id, imaging_path=imaging_path, photostim_path=photostim_path)
    _, photostim_config = _load_v1_configs(repo_root, imaging_path, photostim_path)

    if scanfield_index < 1 or scanfield_index > len(bundle.scanfields):
        raise IndexError(
            f"Scanfield index {scanfield_index} is out of range. Available scanfields: 1..{len(bundle.scanfields)}."
        )

    scanfield = bundle.scanfields[scanfield_index - 1]
    x_ref, y_ref = _pixel_zero_based_to_ref_xy(scanfield, x_px, y_px)

    objective_resolution = _normalize_resolution_xy(_load_objective_resolution_um_per_ref(photostim_config))
    x_um = float(x_ref * objective_resolution[0])
    y_um = float(y_ref * objective_resolution[1])

    notes: list[str] = []
    if bundle.note:
        notes.append(bundle.note)
    if len(bundle.scanfields) > 1:
        notes.append("Fast-Z / multi-plane conversion returns nominal plane Z only.")

    return ConvertedPatternCoordinate(
        x_um=x_um,
        y_um=y_um,
        z_um=float(scanfield.z_um),
        source=bundle.source,
        note=" ".join(notes).strip(),
        exp_dir=bundle.exp_dir,
        scanfield_label=scanfield.label,
    )


def _load_v1_configs(repo_root: Path, imaging_path: str, photostim_path: str):
    machine_name = autodetect_machine_name(repo_root) or "ar-lab-si2"
    imaging_machine_config = load_machine_config(repo_root, machine_name, "P1_imaging")
    photostim_machine_config = load_machine_config(repo_root, machine_name, "PS")

    if imaging_path not in imaging_machine_config.paths:
        raise KeyError(f"Imaging path '{imaging_path}' is not defined in config '{imaging_machine_config.name}'.")
    if photostim_path not in photostim_machine_config.paths:
        raise KeyError(f"Photostim path '{photostim_path}' is not defined in config '{photostim_machine_config.name}'.")
    return imaging_machine_config.paths[imaging_path], photostim_machine_config.paths[photostim_path]


def _require_linux_roi_import() -> None:
    if not sys.platform.startswith("linux"):
        raise NotImplementedError("Imaging pixel ROI import is available on Ubuntu only.")


def _resolve_experiment_dir(exp_id: str, imaging_config, imaging_path: str) -> Path:
    animal_id = _animal_id_from_exp_id(exp_id)
    checked: list[Path] = []

    ubuntu_raw_root = Path("/data/Remote_Repository")
    ubuntu_path_root = ubuntu_raw_root / animal_id / exp_id / imaging_path
    checked.append(ubuntu_path_root)
    if ubuntu_path_root.is_dir():
        return ubuntu_path_root

    acquisition_candidates = []
    for raw_root in (imaging_config.local_data_root, imaging_config.remote_data_root):
        acquisition_candidates.extend(_candidate_roots_from_raw(raw_root))

    acquisition_candidates.extend(
        [
            Path("/home/adamranson/data/Repository"),
            Path("/home/adamranson/data/Local_Repository"),
        ]
    )

    seen: set[str] = set()
    for root in acquisition_candidates:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        exp_root = root / animal_id / exp_id
        checked.append(exp_root)
        if exp_root.is_dir():
            return exp_root

    searched = ", ".join(str(path) for path in checked)
    raise FileNotFoundError(f"Could not resolve experiment directory for '{exp_id}'. Checked: {searched}")


def _animal_id_from_exp_id(exp_id: str) -> str:
    parts = exp_id.strip().split("_")
    if len(parts) < 3 or not parts[2]:
        raise ValueError(f"expID '{exp_id}' does not match expected format YYYY-MM-DD_NN_ANIMALID")
    return parts[2]


def _candidate_roots_from_raw(raw_root: str) -> list[Path]:
    if not raw_root:
        return []

    candidates = [Path(raw_root)]
    normalized = raw_root.replace("/", "\\").lower()
    if normalized.startswith("\\\\ar-lab-nas1\\dataserver\\remote_repository"):
        candidates.append(Path("/home/adamranson/data/Repository"))
    if normalized.startswith("f:\\local_repository"):
        candidates.append(Path("/home/adamranson/data/Local_Repository"))
    return candidates


def _list_roi_folder_names(exp_dir: Path) -> list[str]:
    roi_dirs = sorted(
        [path.name for path in exp_dir.iterdir() if path.is_dir() and re.fullmatch(r"R\d{3}", path.name)],
        key=str.lower,
    )
    return roi_dirs


def _find_selected_scanfield_roi_files(exp_dir: Path) -> list[Path]:
    return sorted(exp_dir.rglob("*_selectedScanfield.roi"), key=lambda path: str(path).lower())


def _find_tiff_files(exp_dir: Path) -> list[Path]:
    return sorted(
        [*exp_dir.rglob("*.tif"), *exp_dir.rglob("*.tiff")],
        key=lambda path: str(path).lower(),
    )


def _load_scanfields_from_roi_file(path: Path) -> tuple[ScanfieldChoice, ...] | None:
    roi_folder_names = _list_roi_folder_names(path.parent)
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None

    selected_meta = _find_key_recursive(payload, "selectedScanfieldMetadata")
    if not isinstance(selected_meta, dict):
        return None

    rois = selected_meta.get("rois")
    if not isinstance(rois, list):
        return None

    scanfields: list[ScanfieldChoice] = []
    for roi_idx, roi in enumerate(rois, start=1):
        if not isinstance(roi, dict):
            continue
        roi_name = str(roi.get("roiName") or f"ROI {roi_idx}")
        roi_folder_name = roi_folder_names[roi_idx - 1] if roi_idx - 1 < len(roi_folder_names) else f"R{roi_idx:03d}"
        for sf_idx, scanfield in enumerate(roi.get("scanfields", []), start=1):
            if not isinstance(scanfield, dict):
                continue
            pixel_resolution = _parse_pixel_resolution(scanfield.get("pixelResolutionXY"))
            if pixel_resolution is None:
                continue
            transform = _extract_pixel_to_ref_transform(scanfield)
            if transform is None:
                transform = _build_pixel_to_ref_transform_from_geometry(scanfield, pixel_resolution)
            if transform is None:
                continue
            z_um = _safe_float(scanfield.get("scanfieldZ"), default=0.0)
            scanfield_name = str(scanfield.get("scanfieldName") or f"Scanfield {sf_idx}")
            index = len(scanfields) + 1
            scanfields.append(
                ScanfieldChoice(
                    index=index,
                    label=f"{index}: {roi_folder_name} / {roi_name} / {scanfield_name} / z={z_um:g} um / {pixel_resolution[0]}x{pixel_resolution[1]}",
                    roi_name=roi_name,
                    roi_folder_name=roi_folder_name,
                    scanfield_name=scanfield_name,
                    z_um=z_um,
                    pixel_resolution_xy=pixel_resolution,
                    pixel_to_ref_transform=transform,
                )
            )

    return tuple(scanfields) if scanfields else None


def _load_scanfields_from_tiff_header(path: Path) -> tuple[ScanfieldChoice, ...] | None:
    try:
        header_text = _read_scanimage_header_text(path)
    except Exception:
        return None

    pixels_per_line = _extract_scalar(header_text, r"SI\.hRoiManager\.pixelsPerLine = ([^\r\n]+)")
    lines_per_frame = _extract_scalar(header_text, r"SI\.hRoiManager\.linesPerFrame = ([^\r\n]+)")
    if pixels_per_line is None or lines_per_frame is None:
        return None

    imaging_fov_deg = _extract_matrix(header_text, r"SI\.hRoiManager\.imagingFovDeg = (\[[^\]]+\])")
    if imaging_fov_deg is None or imaging_fov_deg.shape != (4, 2):
        return None

    stack_enabled = _extract_bool(header_text, r"SI\.hStackManager\.enable = (true|false)")
    actual_num_slices = _extract_scalar(header_text, r"SI\.hStackManager\.actualNumSlices = ([^\r\n]+)")
    arbitrary_zs = _extract_vector(header_text, r"SI\.hStackManager\.arbitraryZs = (\[[^\]]+\])")
    if not stack_enabled:
        arbitrary_zs = np.array([0.0], dtype=float)
    elif arbitrary_zs is None or arbitrary_zs.size == 0:
        arbitrary_zs = np.array([0.0], dtype=float)
    elif actual_num_slices is not None and actual_num_slices > 0:
        arbitrary_zs = arbitrary_zs[: int(actual_num_slices)]

    transform = _build_pixel_to_ref_transform_from_corners(
        imaging_fov_deg,
        (int(round(pixels_per_line)), int(round(lines_per_frame))),
    )

    scanfields: list[ScanfieldChoice] = []
    for idx, z_um in enumerate(arbitrary_zs, start=1):
        scanfields.append(
            ScanfieldChoice(
                index=idx,
                label=f"{idx}: TIFF imaging field / z={float(z_um):g} um / {int(round(pixels_per_line))}x{int(round(lines_per_frame))}",
                roi_name="TIFF imaging field",
                scanfield_name="TIFF imaging field",
                z_um=float(z_um),
                pixel_resolution_xy=(int(round(pixels_per_line)), int(round(lines_per_frame))),
                pixel_to_ref_transform=transform,
            )
        )
    return tuple(scanfields)


def _read_scanimage_header_text(path: Path) -> str:
    with path.open("rb") as handle:
        payload = handle.read(2_000_000)
    return payload.decode("latin1", errors="ignore")


def _extract_bool(text: str, pattern: str) -> bool:
    match = re.search(pattern, text)
    if not match:
        return False
    return match.group(1).strip().lower() == "true"


def _extract_scalar(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1).strip().rstrip(";"))
    except ValueError:
        return None


def _extract_vector(text: str, pattern: str) -> np.ndarray | None:
    matrix = _extract_matrix(text, pattern)
    if matrix is None:
        return None
    if matrix.ndim == 2 and matrix.shape[0] == 1:
        return matrix[0]
    if matrix.ndim == 2 and matrix.shape[1] == 1:
        return matrix[:, 0]
    return matrix.reshape(-1)


def _extract_matrix(text: str, pattern: str) -> np.ndarray | None:
    match = re.search(pattern, text)
    if not match:
        return None
    return _parse_matlab_numeric_array(match.group(1))


def _parse_matlab_numeric_array(raw: object) -> np.ndarray | None:
    if isinstance(raw, np.ndarray):
        try:
            return np.asarray(raw, dtype=float)
        except (TypeError, ValueError):
            return None
    if isinstance(raw, list):
        try:
            return np.asarray(raw, dtype=float)
        except (TypeError, ValueError):
            return None
    if isinstance(raw, (int, float)):
        return np.asarray([[float(raw)]], dtype=float)
    if not isinstance(raw, str):
        return None

    text = raw.strip()
    if not text.startswith("[") or not text.endswith("]"):
        return None
    inner = text[1:-1].strip()
    if not inner:
        return np.zeros((0,), dtype=float)

    rows = []
    for row_text in inner.split(";"):
        values = [float(token) for token in row_text.replace(",", " ").split()]
        rows.append(values)
    try:
        return np.asarray(rows, dtype=float)
    except ValueError:
        return None


def _find_key_recursive(value: object, target_key: str) -> object | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() == target_key.lower():
                return child
        for child in value.values():
            result = _find_key_recursive(child, target_key)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _find_key_recursive(child, target_key)
            if result is not None:
                return result
    return None


def _parse_pixel_resolution(raw: object) -> tuple[int, int] | None:
    arr = _parse_matlab_numeric_array(raw)
    if arr is None:
        return None
    flat = arr.reshape(-1)
    if flat.size < 2:
        return None
    x_res = int(round(float(flat[0])))
    y_res = int(round(float(flat[1])))
    if x_res <= 0 or y_res <= 0:
        return None
    return (x_res, y_res)


def _extract_pixel_to_ref_transform(scanfield: dict) -> np.ndarray | None:
    transform = _parse_matlab_numeric_array(scanfield.get("pixelToRefTransform"))
    if transform is None or transform.shape != (3, 3):
        return None
    return np.asarray(transform, dtype=float)


def _build_pixel_to_ref_transform_from_geometry(scanfield: dict, pixel_resolution: tuple[int, int]) -> np.ndarray | None:
    center_xy = _parse_matlab_numeric_array(scanfield.get("centerXY"))
    size_xy = _parse_matlab_numeric_array(scanfield.get("sizeXY"))
    rotation_degrees = _safe_float(scanfield.get("rotationDegrees"), default=0.0)
    if center_xy is None or size_xy is None:
        return None
    center = center_xy.reshape(-1)
    size = size_xy.reshape(-1)
    if center.size < 2 or size.size < 2:
        return None

    x_res, y_res = pixel_resolution
    off = np.array(
        [
            [1.0 / x_res, 0.0, -1.0 / (2.0 * x_res)],
            [0.0, 1.0 / y_res, -1.0 / (2.0 * y_res)],
            [0.0, 0.0, 1.0],
        ],
        dtype=float,
    )

    radians = math.radians(rotation_degrees)
    cos_theta = math.cos(radians)
    sin_theta = math.sin(radians)

    O = np.eye(3, dtype=float)
    O[:, 2] = np.array([-0.5, -0.5, 1.0], dtype=float)
    R = np.eye(3, dtype=float)
    R[0:2, 0:2] = np.array([[cos_theta, -sin_theta], [sin_theta, cos_theta]], dtype=float)
    S = np.diag([float(size[0]), float(size[1]), 1.0]).astype(float)
    C = np.eye(3, dtype=float)
    C[:, 2] = np.array([float(center[0]), float(center[1]), 1.0], dtype=float)
    affine = C @ R @ S @ O
    return affine @ off


def _build_pixel_to_ref_transform_from_corners(corners_ref: np.ndarray, pixel_resolution: tuple[int, int]) -> np.ndarray:
    corners = np.asarray(corners_ref, dtype=float)
    center = (corners[0] + corners[2]) / 2.0
    width_vec = corners[1] - corners[0]
    height_vec = corners[3] - corners[0]
    size = np.array([np.linalg.norm(width_vec), np.linalg.norm(height_vec)], dtype=float)
    rotation_degrees = math.degrees(math.atan2(width_vec[1], width_vec[0]))
    scanfield = {
        "centerXY": center.tolist(),
        "sizeXY": size.tolist(),
        "rotationDegrees": rotation_degrees,
    }
    transform = _build_pixel_to_ref_transform_from_geometry(scanfield, pixel_resolution)
    assert transform is not None
    return transform


def _pixel_zero_based_to_ref_xy(scanfield: ScanfieldChoice, x_px: float, y_px: float) -> tuple[float, float]:
    x_res, y_res = scanfield.pixel_resolution_xy
    if x_px < 0 or x_px >= x_res or y_px < 0 or y_px >= y_res:
        raise ValueError(
            f"Pixel coordinate ({x_px}, {y_px}) is outside scanfield bounds 0..{x_res - 1}, 0..{y_res - 1}."
        )

    pixel_h = np.array([float(x_px) + 1.0, float(y_px) + 1.0, 1.0], dtype=float)
    ref_h = scanfield.pixel_to_ref_transform @ pixel_h
    if abs(ref_h[2]) < 1e-12:
        raise ValueError("Pixel-to-reference transform produced an invalid homogeneous coordinate.")
    return (float(ref_h[0] / ref_h[2]), float(ref_h[1] / ref_h[2]))


def _load_objective_resolution_um_per_ref(path_config) -> object:
    mdf_candidates = sorted(path_config.directory.glob("*MDF*.m"))
    if not mdf_candidates:
        raise FileNotFoundError(f"No MDF file found in '{path_config.directory}'.")
    text = mdf_candidates[0].read_text()
    match = re.search(r"objectiveResolution\s*=\s*([^\r\n;]+)", text)
    if not match:
        raise ValueError(f"objectiveResolution not found in '{mdf_candidates[0]}'.")
    raw = match.group(1).strip()
    try:
        return float(raw)
    except ValueError:
        arr = _parse_matlab_numeric_array(raw)
        if arr is None:
            raise ValueError(f"Could not parse objectiveResolution from '{mdf_candidates[0]}'.") from None
        return arr


def _normalize_resolution_xy(raw: object) -> tuple[float, float]:
    if isinstance(raw, (int, float)):
        return (float(raw), float(raw))
    arr = np.asarray(raw, dtype=float).reshape(-1)
    if arr.size == 1:
        return (float(arr[0]), float(arr[0]))
    if arr.size < 2:
        raise ValueError("objectiveResolution must contain at least one element.")
    return (float(arr[0]), float(arr[1]))


def _safe_float(raw: object, default: float) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default
