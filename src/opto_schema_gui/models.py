from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List

SCHEMA_TIME_QUANTUM_S = 0.05


def _is_quantized(value: float, quantum: float = SCHEMA_TIME_QUANTUM_S, tol: float = 1e-9) -> bool:
    if quantum <= 0:
        return True
    steps = round(value / quantum)
    return abs(value - (steps * quantum)) <= tol


@dataclass
class CellSpec:
    label: str
    x: float
    y: float
    z: float
    power_scale: float = 1.0

    def as_dict(self) -> dict:
        data = {
            "label": self.label,
            "x": self.x,
            "y": self.y,
            "z": self.z,
            "power_scale": self.power_scale,
        }
        return data


@dataclass
class Pattern:
    name: str
    duration_s: float
    frequency_hz: float
    power_percent: float
    duty_cycle: float = 0.2
    spiral_width: float = 10.0
    spiral_height: float = 10.0
    notes: str = ""
    cells: List[CellSpec] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "duration_s": self.duration_s,
            "frequency_hz": self.frequency_hz,
            "duty_cycle": self.duty_cycle,
            "power_percent": self.power_percent,
            "spiral_width": self.spiral_width,
            "spiral_height": self.spiral_height,
            "notes": self.notes,
            "cells": [cell.as_dict() for cell in self.cells],
        }


@dataclass
class SequenceStep:
    pattern: str
    start_s: float

    def as_dict(self, end_s: float) -> dict:
        return {
            "pattern": self.pattern,
            "start_s": self.start_s,
            "end_s": end_s,
        }


@dataclass
class Sequence:
    name: str
    steps: List[SequenceStep] = field(default_factory=list)
    notes: str = ""

    def as_dict(self, patterns: Dict[str, Pattern]) -> dict:
        serialized_steps = []
        for step in self.steps:
            pattern = patterns[step.pattern]
            serialized_steps.append(step.as_dict(step.start_s + pattern.duration_s))
        return {
            "name": self.name,
            "notes": self.notes,
            "steps": serialized_steps,
        }


@dataclass
class ExperimentProject:
    patterns: Dict[str, Pattern] = field(default_factory=dict)
    sequences: Dict[str, Sequence] = field(default_factory=dict)

    def validate(self) -> list[str]:
        errors: list[str] = []

        for name, pattern in self.patterns.items():
            if not name.strip():
                errors.append("Pattern name cannot be empty.")
            if pattern.duration_s <= 0:
                errors.append(f"Pattern '{name}' must have duration > 0.")
            if not _is_quantized(pattern.duration_s):
                errors.append(
                    f"Pattern '{name}' must have duration in {SCHEMA_TIME_QUANTUM_S * 1000:.0f} ms increments."
                )
            if pattern.frequency_hz <= 0:
                errors.append(f"Pattern '{name}' must have frequency > 0.")
            if pattern.duty_cycle < 0 or pattern.duty_cycle > 1:
                errors.append(f"Pattern '{name}' must have duty cycle in [0, 1].")
            if pattern.power_percent < 0:
                errors.append(f"Pattern '{name}' must have power >= 0.")
            if not pattern.cells:
                errors.append(f"Pattern '{name}' must contain at least one cell.")
            seen = set()
            for cell in pattern.cells:
                key = (cell.x, cell.y, cell.z)
                if key in seen:
                    errors.append(f"Pattern '{name}' contains duplicate coordinates {key}.")
                seen.add(key)
                if cell.power_scale < 0:
                    errors.append(f"Pattern '{name}' cell '{cell.label}' must have power scale >= 0.")
                if cell.power_scale > 1:
                    errors.append(f"Pattern '{name}' cell '{cell.label}' must have power scale <= 1.")

        for name, sequence in self.sequences.items():
            if not name.strip():
                errors.append("Sequence name cannot be empty.")
            last_end = None
            ordered_steps = sorted(sequence.steps, key=lambda step: step.start_s)
            if ordered_steps != sequence.steps:
                errors.append(f"Sequence '{name}' steps must be sorted by start time.")
            for step in sequence.steps:
                if step.pattern not in self.patterns:
                    errors.append(f"Sequence '{name}' references unknown pattern '{step.pattern}'.")
                    continue
                if step.start_s < 0:
                    errors.append(f"Sequence '{name}' contains a negative start time.")
                    continue
                if not _is_quantized(step.start_s):
                    errors.append(
                        f"Sequence '{name}' step '{step.pattern}' must start on a {SCHEMA_TIME_QUANTUM_S * 1000:.0f} ms grid."
                    )
                pattern = self.patterns[step.pattern]
                end_s = step.start_s + pattern.duration_s
                if last_end is not None and step.start_s < last_end:
                    errors.append(
                        f"Sequence '{name}' has overlapping steps around '{step.pattern}'."
                    )
                last_end = end_s

        return errors
