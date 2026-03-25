from __future__ import annotations

import configparser
import queue
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_CONFIG_PATH = Path("scanimage_bridge.ini")


@dataclass(slots=True)
class SessionConfig:
    name: str
    matlab_executable: str
    matlab_flags: list[str]
    startup_timeout_s: float
    command_timeout_s: float
    repo_matlab_path: Path
    startup_commands: list[str]
    launch_scanimage_command: str
    shutdown_scanimage_command: str
    focus_command: str
    acquire_command: str
    stop_command: str
    hsi_variable: str
    xy_transform: str
    z_transform: str
    point_size_xy: tuple[float, float]
    rotation_degrees: float
    pause_duration: float
    park_duration: float
    clear_existing: bool
    ignore_frequency: bool
    stimulus_function: str
    power_scale_mode: str


class MatlabSessionError(RuntimeError):
    pass


class MatlabSession:
    def __init__(self, config: SessionConfig):
        self.config = config
        self.process: subprocess.Popen[str] | None = None
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None

    def start(self) -> None:
        if self.process is not None:
            return

        command = [self.config.matlab_executable, *self.config.matlab_flags]
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise MatlabSessionError(
                f"Could not launch MATLAB for session '{self.config.name}'. "
                f"Executable not found: {self.config.matlab_executable}"
            ) from exc
        assert self.process.stdout is not None
        self._reader_thread = threading.Thread(target=self._pump_output, daemon=True)
        self._reader_thread.start()

        self.eval(
            f"addpath(genpath({matlab_string(str(self.config.repo_matlab_path))}));",
            timeout_s=self.config.startup_timeout_s,
        )
        for command_text in self.config.startup_commands:
            self.eval(command_text, timeout_s=self.config.startup_timeout_s)

    def stop(self) -> None:
        if self.process is None:
            return
        try:
            self.send_raw("exit\n")
            self.process.wait(timeout=5)
        except Exception:
            self.process.kill()
        finally:
            self.process = None

    def send_raw(self, text: str) -> None:
        if self.process is None or self.process.stdin is None:
            raise MatlabSessionError(f"MATLAB session '{self.config.name}' is not running.")
        self.process.stdin.write(text)
        self.process.stdin.flush()

    def eval(self, command: str, timeout_s: float = 30.0) -> list[str]:
        if self.process is None:
            raise MatlabSessionError(f"MATLAB session '{self.config.name}' is not running.")

        token = f"CODEX_{uuid.uuid4().hex}"
        wrapped = "\n".join(
            [
                f"fprintf('{token}_BEGIN\\n');",
                "try",
                command,
                f"fprintf('{token}_OK\\n');",
                "catch ME",
                f"fprintf(2, '{token}_ERR:%s\\n', getReport(ME, 'extended', 'hyperlinks', 'off'));",
                "end",
                f"fprintf('{token}_END\\n');",
                "",
            ]
        )
        self.send_raw(wrapped)

        lines: list[str] = []
        saw_begin = False
        while True:
            try:
                line = self._output_queue.get(timeout=timeout_s)
            except queue.Empty as exc:
                raise MatlabSessionError(
                    f"Timed out waiting for MATLAB session '{self.config.name}' while executing command."
                ) from exc

            stripped = line.rstrip("\n")
            if stripped == f"{token}_BEGIN":
                saw_begin = True
                continue
            if not saw_begin:
                continue
            if stripped == f"{token}_OK":
                continue
            if stripped.startswith(f"{token}_ERR:"):
                raise MatlabSessionError(
                    f"MATLAB command failed in session '{self.config.name}':\n"
                    + stripped[len(f"{token}_ERR:") :]
                )
            if stripped == f"{token}_END":
                return lines
            lines.append(stripped)

    def _pump_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._output_queue.put(line)


def matlab_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def matlab_bool(value: bool) -> str:
    return "true" if value else "false"


def parse_session_configs(config_path: str | Path, repo_root: str | Path) -> list[SessionConfig]:
    parser = configparser.ConfigParser()
    read_files = parser.read(config_path)
    if not read_files:
        raise FileNotFoundError(f"Could not read config file: {config_path}")

    repo_root = Path(repo_root).resolve()
    defaults = parser["matlab"] if parser.has_section("matlab") else None
    session_configs: list[SessionConfig] = []

    for section_name in parser.sections():
        if not section_name.startswith("session:"):
            continue
        section = parser[section_name]
        matlab_executable = _get_string(section, defaults, "matlab_executable", "matlab")
        matlab_flags = _split_lines(_get_string(section, defaults, "matlab_flags", "-nodesktop\n-nosplash"))
        startup_commands = _split_lines(_get_string(section, defaults, "startup_commands", ""))
        repo_matlab_path = Path(_get_string(section, defaults, "repo_matlab_path", str(repo_root / "matlab"))).resolve()

        session_configs.append(
            SessionConfig(
                name=section_name.split("session:", 1)[1],
                matlab_executable=matlab_executable,
                matlab_flags=matlab_flags,
                startup_timeout_s=_get_float(section, defaults, "startup_timeout_s", 60.0),
                command_timeout_s=_get_float(section, defaults, "command_timeout_s", 60.0),
                repo_matlab_path=repo_matlab_path,
                startup_commands=startup_commands,
                launch_scanimage_command=_get_string(section, defaults, "launch_scanimage_command", "scanimage;"),
                shutdown_scanimage_command=_get_string(
                    section,
                    defaults,
                    "shutdown_scanimage_command",
                    "if exist(''hSI'',''var''); try delete(hSI); catch; end; clear hSI; end",
                ),
                focus_command=_get_string(section, defaults, "focus_command", "hSI.startFocus();"),
                acquire_command=_get_string(section, defaults, "acquire_command", "hSI.startGrab();"),
                stop_command=_get_string(section, defaults, "stop_command", "hSI.abort();"),
                hsi_variable=_get_string(section, defaults, "hsi_variable", "hSI"),
                xy_transform=_get_string(section, defaults, "xy_transform", "@(xyz)[xyz(1) xyz(2)]"),
                z_transform=_get_string(section, defaults, "z_transform", "@(xyz)xyz(3)"),
                point_size_xy=_parse_xy_pair(_get_string(section, defaults, "point_size_xy", "0,0")),
                rotation_degrees=_get_float(section, defaults, "rotation_degrees", 0.0),
                pause_duration=_get_float(section, defaults, "pause_duration", 0.010),
                park_duration=_get_float(section, defaults, "park_duration", 0.010),
                clear_existing=_get_bool(section, defaults, "clear_existing", True),
                ignore_frequency=_get_bool(section, defaults, "ignore_frequency", True),
                stimulus_function=_get_string(section, defaults, "stimulus_function", "point"),
                power_scale_mode=_get_string(section, defaults, "power_scale_mode", "multiply"),
            )
        )

    if not session_configs:
        raise ValueError(f"No [session:<name>] sections found in {config_path}")

    return session_configs


def build_import_command(schema_path: str | Path, session: SessionConfig) -> str:
    schema_expr = matlab_string(str(Path(schema_path).resolve()))
    point_size_expr = f"[{session.point_size_xy[0]} {session.point_size_xy[1]}]"

    return "\n".join(
        [
            f"importedPatternNames = opto.scanimage.importSchemaPatterns({session.hsi_variable}, {schema_expr}, ...",
            f"    ClearExisting={matlab_bool(session.clear_existing)}, ...",
            f"    StimulusFunction={matlab_string(session.stimulus_function)}, ...",
            f"    PointSizeXY={point_size_expr}, ...",
            f"    RotationDegrees={session.rotation_degrees}, ...",
            f"    PauseDuration={session.pause_duration}, ...",
            f"    ParkDuration={session.park_duration}, ...",
            f"    XYTransform={session.xy_transform}, ...",
            f"    ZTransform={session.z_transform}, ...",
            f"    PowerScaleMode={matlab_string(session.power_scale_mode)}, ...",
            f"    IgnoreFrequency={matlab_bool(session.ignore_frequency)});",
            "disp('Imported patterns:');",
            "disp(importedPatternNames);",
        ]
    )


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _parse_xy_pair(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"Expected point_size_xy to contain two comma-separated values, got: {value}")
    return float(parts[0]), float(parts[1])


def session_names(configs: Iterable[SessionConfig]) -> list[str]:
    return [config.name for config in configs]


def _get_string(
    section: configparser.SectionProxy,
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: str,
) -> str:
    if section.get(option, fallback=None) is not None:
        return section.get(option)
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.get(option)
    return fallback


def _get_float(
    section: configparser.SectionProxy,
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: float,
) -> float:
    if section.get(option, fallback=None) is not None:
        return section.getfloat(option)
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.getfloat(option)
    return fallback


def _get_bool(
    section: configparser.SectionProxy,
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: bool,
) -> bool:
    if section.get(option, fallback=None) is not None:
        return section.getboolean(option)
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.getboolean(option)
    return fallback
