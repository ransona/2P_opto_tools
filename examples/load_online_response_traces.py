"""Load exported online-analysis traces by pipeline cell ID and condition.

Example:
    python examples/load_online_response_traces.py \
        /path/to/online_analysis_responses.json 42 0
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


def load_online_response_export(path: str | Path) -> dict[str, object]:
    """Read one experiment's final online-analysis JSON export."""
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValueError("Not a supported online-analysis response export.")
    return payload


def _as_float_array(values: object) -> np.ndarray:
    if not isinstance(values, list):
        return np.empty(0, dtype=float)
    return np.asarray([np.nan if value is None else float(value) for value in values], dtype=float)


def traces_for_pipeline_cell_condition(
    export: dict[str, object],
    pipeline_cell_id: int,
    condition_index: int,
    *,
    sample_count: int = 240,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a shared time base and trial-by-time trace matrix for one cell/condition.

    Individual trials can have different frame counts. Each trace is interpolated
    only inside its sampled time range; unavailable bins remain NaN.
    """
    cells = export.get("cells", [])
    if not isinstance(cells, list):
        raise ValueError("Export has no cell mapping.")
    matching_cells = [
        cell
        for cell in cells
        if isinstance(cell, dict)
        and isinstance(cell.get("origin"), dict)
        and cell["origin"].get("pipeline_cell_id") == pipeline_cell_id
    ]
    if len(matching_cells) != 1:
        raise ValueError(
            f"Expected exactly one exported cell for pipeline_cell_id={pipeline_cell_id}; "
            f"found {len(matching_cells)}."
        )
    return traces_for_online_roi_condition(
        export,
        str(matching_cells[0]["online_roi_name"]),
        condition_index,
        sample_count=sample_count,
    )


def traces_for_online_roi_condition(
    export: dict[str, object],
    online_roi_name: str,
    condition_index: int,
    *,
    sample_count: int = 240,
) -> tuple[np.ndarray, np.ndarray]:
    """Return a shared time base and trial-by-time matrix for one online ROI."""

    settings = export.get("online_analysis", {})
    if not isinstance(settings, dict):
        raise ValueError("Export has no online-analysis settings.")
    time_base = np.linspace(
        -float(settings["pre_s"]),
        float(settings["post_s"]),
        max(2, int(sample_count)),
    )

    traces: list[np.ndarray] = []
    trials = export.get("trials", [])
    if not isinstance(trials, list):
        raise ValueError("Export has no trial records.")
    for trial in trials:
        if not isinstance(trial, dict) or int(trial.get("condition_index", -1)) != condition_index:
            continue
        cell_record = (
            trial.get("cells", {}).get(online_roi_name)
            if isinstance(trial.get("cells"), dict)
            else None
        )
        if not isinstance(cell_record, dict):
            continue
        times = _as_float_array(cell_record.get("time_s"))
        values = _as_float_array(cell_record.get("value"))
        valid = np.isfinite(times) & np.isfinite(values)
        trace = np.full(time_base.shape, np.nan, dtype=float)
        if np.count_nonzero(valid) >= 2:
            times = times[valid]
            values = values[valid]
            order = np.argsort(times)
            times = times[order]
            values = values[order]
            unique_times, unique_indices = np.unique(times, return_index=True)
            values = values[unique_indices]
            in_range = (time_base >= unique_times[0]) & (time_base <= unique_times[-1])
            trace[in_range] = np.interp(time_base[in_range], unique_times, values)
        traces.append(trace)

    matrix = np.vstack(traces) if traces else np.empty((0, time_base.size), dtype=float)
    return time_base, matrix


def load_all_traces_nested(
    export: dict[str, object],
    *,
    sample_count: int = 240,
) -> dict[int, dict[int | str, dict[str, object]]]:
    """Return ``condition_index -> pipeline ID -> cell metadata/time/traces``.

    Manually added cells have no pipeline ID and are keyed by their online ROI
    name instead.
    """
    result: dict[int, dict[int | str, dict[str, object]]] = {}
    cells = export.get("cells", [])
    conditions = export.get("conditions", [])
    if not isinstance(cells, list) or not isinstance(conditions, list):
        return result
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        condition_index = int(condition["condition_index"])
        per_cell: dict[int | str, dict[str, object]] = {}
        for cell in cells:
            if not isinstance(cell, dict):
                continue
            origin = cell.get("origin", {})
            pipeline_id = origin.get("pipeline_cell_id") if isinstance(origin, dict) else None
            key: int | str = int(pipeline_id) if pipeline_id is not None else str(cell["online_roi_name"])
            time_base, traces = traces_for_online_roi_condition(
                export,
                str(cell["online_roi_name"]),
                condition_index,
                sample_count=sample_count,
            )
            per_cell[key] = {
                "cell": cell,
                "time_s": time_base,
                "traces": traces,
            }
        result[condition_index] = per_cell
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_json", type=Path)
    parser.add_argument("pipeline_cell_id", type=int)
    parser.add_argument("condition_index", type=int)
    args = parser.parse_args()

    export = load_online_response_export(args.export_json)
    time_s, traces = traces_for_pipeline_cell_condition(
        export,
        args.pipeline_cell_id,
        args.condition_index,
    )
    all_traces = load_all_traces_nested(export)
    print(f"time_s shape: {time_s.shape}")
    print(f"traces shape: {traces.shape}")
    print(f"conditions loaded: {sorted(all_traces)}")


if __name__ == "__main__":
    main()
