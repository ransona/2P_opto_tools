from __future__ import annotations

import json
import math
import pickle
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


@dataclass(frozen=True)
class ResolvedProcessedCell:
    exp_id: str
    processed_path: Path
    processed_cell_id: int
    imaging_path: str
    roi_folder_name: str
    depth_value: int
    local_cell_index: int
    x_px: float
    y_px: float
    scanfield_index: int
    scanfield_label: str
    note: str


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


def resolve_processed_cell_to_imaging_pixel(
    repo_root: Path,
    exp_id: str,
    processed_cell_id: int,
    channel: int = 0,
    default_imaging_path: str = "P1",
    photostim_path: str = "PS",
) -> ResolvedProcessedCell:
    _require_linux_roi_import()
    if processed_cell_id < 0:
        raise ValueError("Processed cell ID must be >= 0.")

    processed_data, processed_path = _load_processed_s2p_pickle(exp_id, channel)
    neuron_count = _processed_neuron_count(processed_data)
    if processed_cell_id >= neuron_count:
        raise IndexError(
            f"Processed cell ID {processed_cell_id} is out of range for '{processed_path.name}'. "
            f"Available cells: 0..{neuron_count - 1}."
        )

    has_scanpath = "allScanpaths" in processed_data and processed_data["allScanpaths"] is not None
    has_siroi = "allSIRois" in processed_data and processed_data["allSIRois"] is not None

    scanpath_number = _row_scalar(processed_data.get("allScanpaths"), processed_cell_id, default=1)
    siroi_number = _row_scalar(processed_data.get("allSIRois"), processed_cell_id, default=1)
    depth_value = _row_scalar(processed_data.get("Depths"), processed_cell_id, default=0)
    imaging_path = f"P{scanpath_number}" if has_scanpath else default_imaging_path
    roi_folder_name = f"R{siroi_number:03d}" if has_siroi else "R001"

    group_key = (
        imaging_path,
        siroi_number if has_siroi else None,
        depth_value,
    )
    local_cell_index = _local_cell_index_for_processed_row(processed_data, processed_cell_id, default_imaging_path)
    roi_pixels = _lookup_processed_roi_pixels(processed_data, processed_cell_id, default_imaging_path)
    fov_shape = _lookup_processed_fov_shape(processed_data, processed_cell_id, default_imaging_path)
    ypix, xpix = np.unravel_index(roi_pixels.astype(int), fov_shape)
    x_px = float(np.mean(xpix))
    y_px = float(np.mean(ypix))

    processed_debug_lines = [
        f"Processed file: {processed_path}",
        f"Resolved imaging path: {imaging_path}",
        f"Resolved ROI folder: {roi_folder_name}",
        f"Resolved depth value: {depth_value}",
        f"Resolved local ROI index within group: {local_cell_index}",
        f"Resolved centroid pixel: ({x_px:.2f}, {y_px:.2f})",
        f"Group key: {group_key}",
    ]

    try:
        bundle = list_imaging_scanfields(
            repo_root,
            exp_id,
            imaging_path=imaging_path,
            photostim_path=photostim_path,
        )
    except Exception as exc:
        raise type(exc)(f"{exc}\n" + "\n".join(processed_debug_lines)) from exc
    scanfield_index = _match_processed_cell_to_scanfield_index(
        bundle,
        depth_value=depth_value,
        roi_folder_name=roi_folder_name if has_siroi else None,
        depth_is_one_based=has_scanpath,
    )
    scanfield = bundle.scanfields[scanfield_index - 1]

    debug_lines = processed_debug_lines + [f"Matched scanfield: {scanfield.label}"]

    return ResolvedProcessedCell(
        exp_id=exp_id,
        processed_path=processed_path,
        processed_cell_id=processed_cell_id,
        imaging_path=imaging_path,
        roi_folder_name=roi_folder_name,
        depth_value=depth_value,
        local_cell_index=local_cell_index,
        x_px=x_px,
        y_px=y_px,
        scanfield_index=scanfield_index,
        scanfield_label=scanfield.label,
        note="\n".join(debug_lines),
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


def _load_processed_s2p_pickle(exp_id: str, channel: int) -> tuple[dict, Path]:
    animal_id = _animal_id_from_exp_id(exp_id)
    candidates = [
        Path("/home/adamranson/data/Repository") / animal_id / exp_id / "recordings" / f"s2p_ch{channel}.pickle",
        Path("/home/adamranson/data/Local_Repository") / animal_id / exp_id / "recordings" / f"s2p_ch{channel}.pickle",
        Path("/home/adamranson/data/tif_meso/processed_repository") / animal_id / exp_id / "recordings" / f"s2p_ch{channel}.pickle",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        with candidate.open("rb") as handle:
            payload = pickle.load(handle)
        if not isinstance(payload, dict):
            raise TypeError(f"Processed file '{candidate}' did not contain a dict payload.")
        return payload, candidate
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise FileNotFoundError(f"Could not find processed s2p pickle for '{exp_id}'. Checked: {searched}")


def _processed_neuron_count(processed_data: dict) -> int:
    for key in ("F", "dF", "Spikes", "Depths"):
        value = processed_data.get(key)
        if value is None:
            continue
        array = np.asarray(value)
        if array.ndim == 0:
            continue
        return int(array.shape[0])
    raise ValueError("Processed s2p payload did not contain any row-aligned neuron arrays.")


def _row_scalar(value: object, row_index: int, default: int) -> int:
    if value is None:
        return default
    array = np.asarray(value)
    if array.size == 0:
        return default
    flat = array.reshape(array.shape[0], -1)
    return int(flat[row_index, 0])


def _local_cell_index_for_processed_row(processed_data: dict, processed_cell_id: int, default_imaging_path: str) -> int:
    has_scanpath = "allScanpaths" in processed_data and processed_data["allScanpaths"] is not None
    has_siroi = "allSIRois" in processed_data and processed_data["allSIRois"] is not None
    target_path = _row_scalar(processed_data.get("allScanpaths"), processed_cell_id, default=1) if has_scanpath else None
    target_roi = _row_scalar(processed_data.get("allSIRois"), processed_cell_id, default=1) if has_siroi else None
    target_depth = _row_scalar(processed_data.get("Depths"), processed_cell_id, default=0)

    count = 0
    for row_index in range(processed_cell_id):
        row_path = _row_scalar(processed_data.get("allScanpaths"), row_index, default=1) if has_scanpath else None
        row_roi = _row_scalar(processed_data.get("allSIRois"), row_index, default=1) if has_siroi else None
        row_depth = _row_scalar(processed_data.get("Depths"), row_index, default=0)
        if row_path == target_path and row_roi == target_roi and row_depth == target_depth:
            count += 1
    return count


def _lookup_processed_roi_pixels(processed_data: dict, processed_cell_id: int, default_imaging_path: str) -> np.ndarray:
    roi_pix = processed_data.get("AllRoiPix")
    if not isinstance(roi_pix, dict):
        raise ValueError("Processed payload is missing AllRoiPix.")

    local_cell_index = _local_cell_index_for_processed_row(processed_data, processed_cell_id, default_imaging_path)
    depth_value = _row_scalar(processed_data.get("Depths"), processed_cell_id, default=0)
    if "allScanpaths" in processed_data and processed_data["allScanpaths"] is not None:
        scanpath_number = _row_scalar(processed_data.get("allScanpaths"), processed_cell_id, default=1)
        siroi_number = _row_scalar(processed_data.get("allSIRois"), processed_cell_id, default=1)
        try:
            roi_list = roi_pix[int(scanpath_number)][int(siroi_number)][int(depth_value)]
        except Exception as exc:
            raise KeyError(
                "Processed AllRoiPix layout did not match expected mesoscope structure "
                "{scanpath -> si_roi -> depth -> [roi pixels]}."
            ) from exc
    else:
        if int(depth_value) not in roi_pix:
            available = sorted(roi_pix.keys())
            raise KeyError(
                f"Processed AllRoiPix does not contain depth key {int(depth_value)}. "
                f"Available keys: {available}. This looks like an unsupported legacy processed layout."
            )
        roi_list = roi_pix[int(depth_value)]

    if local_cell_index < 0 or local_cell_index >= len(roi_list):
        raise IndexError(
            f"Local ROI index {local_cell_index} is out of range for processed cell {processed_cell_id}."
        )
    return np.asarray(roi_list[local_cell_index])


def _lookup_processed_fov_shape(processed_data: dict, processed_cell_id: int, default_imaging_path: str) -> tuple[int, int]:
    all_fov = processed_data.get("AllFOV")
    if not isinstance(all_fov, dict):
        raise ValueError("Processed payload is missing AllFOV.")

    depth_value = _row_scalar(processed_data.get("Depths"), processed_cell_id, default=0)
    if "allScanpaths" in processed_data and processed_data["allScanpaths"] is not None:
        scanpath_number = _row_scalar(processed_data.get("allScanpaths"), processed_cell_id, default=1)
        siroi_number = _row_scalar(processed_data.get("allSIRois"), processed_cell_id, default=1)
        try:
            image = np.asarray(all_fov[int(scanpath_number)][int(siroi_number)][int(depth_value)])
        except Exception as exc:
            raise KeyError(
                "Processed AllFOV layout did not match expected mesoscope structure "
                "{scanpath -> si_roi -> depth -> image}."
            ) from exc
    else:
        if int(depth_value) not in all_fov:
            available = sorted(all_fov.keys())
            raise KeyError(
                f"Processed AllFOV does not contain depth key {int(depth_value)}. "
                f"Available keys: {available}. This looks like an unsupported legacy processed layout."
            )
        image = np.asarray(all_fov[int(depth_value)])
    return int(image.shape[0]), int(image.shape[1])


def _match_processed_cell_to_scanfield_index(
    bundle: MetadataBundle,
    depth_value: int,
    roi_folder_name: str | None,
    depth_is_one_based: bool,
) -> int:
    candidates = list(bundle.scanfields)
    if roi_folder_name:
        roi_filtered = [scanfield for scanfield in candidates if scanfield.roi_folder_name == roi_folder_name]
        if roi_filtered:
            candidates = roi_filtered

    exact_z = [scanfield for scanfield in candidates if math.isclose(scanfield.z_um, float(depth_value), rel_tol=0.0, abs_tol=1e-9)]
    if len(exact_z) == 1:
        return exact_z[0].index

    if len(candidates) == 1:
        return candidates[0].index

    ordered = sorted(candidates, key=lambda scanfield: scanfield.index)
    if depth_is_one_based:
        candidate_position = depth_value - 1
    else:
        candidate_position = depth_value
    if 0 <= candidate_position < len(ordered):
        return ordered[candidate_position].index

    candidate_labels = ", ".join(scanfield.label for scanfield in ordered)
    raise ValueError(
        f"Could not uniquely match processed cell to scanfield. Candidates: {candidate_labels}"
    )


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
