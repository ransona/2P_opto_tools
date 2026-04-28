from __future__ import annotations

from pathlib import Path

import yaml

from .models import CellSpec, ExperimentProject, Pattern, Sequence, SequenceStep


def load_schema(path: str | Path) -> ExperimentProject:
    data = yaml.safe_load(Path(path).read_text()) or {}

    project_block = data.get("project", {})
    patterns_block = data.get("patterns", {})
    sequences_block = data.get("sequences", {})

    patterns: dict[str, Pattern] = {}
    for name, pattern_data in patterns_block.items():
        cells = [
            CellSpec(
                label=cell.get("label", ""),
                x=cell["x"],
                y=cell["y"],
                z=cell["z"],
                power_scale=cell.get("power_scale", 1.0),
                origin=str(cell.get("origin", "") or ""),
            )
            for cell in pattern_data.get("cells", [])
        ]
        patterns[name] = Pattern(
            name=name,
            duration_s=pattern_data.get("duration_s", 0.0),
            frequency_hz=pattern_data.get("frequency_hz", 0.0),
            power_percent=pattern_data.get("power_percent", 0.0),
            duty_cycle=pattern_data.get("duty_cycle", 0.2),
            spiral_width=pattern_data.get("spiral_width", 10.0),
            spiral_height=pattern_data.get("spiral_height", 10.0),
            notes=pattern_data.get("notes", ""),
            cells=cells,
        )

    sequences: dict[str, Sequence] = {}
    for name, sequence_data in sequences_block.items():
        steps = [
            SequenceStep(pattern=step["pattern"], start_s=step["start_s"])
            for step in sequence_data.get("steps", [])
        ]
        sequences[name] = Sequence(
            name=name,
            steps=steps,
            notes=sequence_data.get("notes", ""),
        )

    return ExperimentProject(
        patterns=patterns,
        sequences=sequences,
        origin_exp_id=str(project_block.get("origin_exp_id", "") or ""),
        origin_user_id=str(project_block.get("origin_user_id", "") or ""),
    )


def save_schema(path: str | Path, project: ExperimentProject) -> None:
    payload = {
        "version": 1,
        "project": {
            "origin_exp_id": project.origin_exp_id,
            "origin_user_id": project.origin_user_id,
        },
        "patterns": {name: pattern.as_dict() for name, pattern in project.patterns.items()},
        "sequences": {
            name: sequence.as_dict(project.patterns)
            for name, sequence in project.sequences.items()
        },
    }
    Path(path).write_text(yaml.safe_dump(payload, sort_keys=False))
