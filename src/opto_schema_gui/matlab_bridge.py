from __future__ import annotations

import configparser
import io
import ntpath
import os
import queue
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    import matlab.engine as matlab_engine
except ModuleNotFoundError:
    matlab_engine = None


DEFAULT_CONFIGS_ROOT = Path("configs")


@dataclass
class PathConfig:
    machine_name: str
    config_name: str
    name: str
    directory: Path
    matlab_executable: str
    matlab_flags: list[str]
    simulation_mode: str
    listener_host: str
    listener_port: int
    listener_auto_start: bool
    reply_host: str
    reply_port: int
    local_data_root: str
    remote_data_root: str
    acquisition_folder: str
    hsi_variable: str
    hsictl_variable: str
    motor_data_variable: str
    startup_timeout_s: float
    command_timeout_s: float
    repo_matlab_path: Path
    focus_command: str
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

    @property
    def launch_script(self) -> Path:
        return self.directory / "launch.m"

    @property
    def start_script(self) -> Path:
        return self.directory / "start_script.m"

    @property
    def stop_script(self) -> Path:
        return self.directory / "stop_script.m"


@dataclass
class MachineConfig:
    machine_name: str
    name: str
    directory: Path
    launch_order: list[str]
    launch_delay_s: float
    photostim_path: str | None
    paths: dict[str, PathConfig]


@dataclass
class ExperimentContext:
    exp_id: str
    animal_id: str
    exp_dir: str
    exp_dir_remote: str
    reply_host: str
    reply_port: int


class MatlabSessionError(RuntimeError):
    pass


class MatlabSession:
    def __init__(self, config: PathConfig):
        self.config = config
        self.engine = None
        self.process: subprocess.Popen[str] | None = None
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._reader_thread: threading.Thread | None = None
        self.simulated = False
        self.current_directory = str(config.directory)
        self.started_with_launch = False
        self.command_dir = self.config.directory / ".opto_matlab_bridge"

    def start(self, startup_command: str | None = None) -> None:
        if self.engine is not None or self.process is not None or self.simulated:
            return

        if self.config.simulation_mode == "always":
            self._start_simulated()
            self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
            return

        if matlab_engine is not None:
            try:
                flags = " ".join(self.config.matlab_flags).strip()
                self.engine = matlab_engine.start_matlab(flags)
                self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
                if startup_command:
                    self.eval(startup_command, timeout_s=self.config.startup_timeout_s)
                else:
                    self.eval(
                        f"addpath(genpath({matlab_string(str(self.config.repo_matlab_path))}));",
                        timeout_s=self.config.startup_timeout_s,
                    )
                return
            except Exception as exc:
                if self.config.simulation_mode == "auto":
                    self.engine = None
                    self._start_simulated()
                    self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
                    return
                raise MatlabSessionError(
                    f"Could not start MATLAB Engine for path '{self.config.name}': {exc}"
                ) from exc

        command = [self.config.matlab_executable, *self.config.matlab_flags]
        if startup_command:
            startup_compact = " ".join(line.strip() for line in startup_command.splitlines() if line.strip())
            command.extend(["-r", startup_compact])
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
            if self.config.simulation_mode == "auto":
                self._start_simulated()
                self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
                return
            raise MatlabSessionError(
                f"Could not launch MATLAB for path '{self.config.name}'. "
                f"Executable not found: {self.config.matlab_executable}"
            ) from exc

        assert self.process.stdout is not None
        self._reader_thread = threading.Thread(target=self._pump_output, daemon=True)
        self._reader_thread.start()
        self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
        if startup_command:
            return
        self.eval(
            f"addpath(genpath({matlab_string(str(self.config.repo_matlab_path))}));",
            timeout_s=self.config.startup_timeout_s,
        )

    def stop(self) -> None:
        if self.simulated:
            self.simulated = False
            return
        if self.engine is not None:
            try:
                self.engine.quit()
            finally:
                self.engine = None
            return
        if self.process is None:
            return
        if self.started_with_launch:
            try:
                self.eval("exit", timeout_s=5)
            except Exception:
                pass
        try:
            if self.process is not None:
                self.send_raw("exit\n")
                self.process.wait(timeout=5)
        except Exception:
            self.process.kill()
        finally:
            self.process = None

    def send_raw(self, text: str) -> None:
        if self.simulated:
            return
        if self.process is None or self.process.stdin is None:
            raise MatlabSessionError(f"MATLAB session '{self.config.name}' is not running.")
        self.process.stdin.write(text)
        self.process.stdin.flush()

    def eval(self, command: str, timeout_s: float = 30.0) -> list[str]:
        if self.simulated:
            return self._simulate_eval(command)
        if self.engine is not None:
            return self._eval_via_engine(command, timeout_s)
        if self.process is None:
            raise MatlabSessionError(f"MATLAB session '{self.config.name}' is not running.")
        if self.started_with_launch:
            return self._eval_via_command_files(command, timeout_s)

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
                    f"Timed out waiting for MATLAB path '{self.config.name}' while executing command."
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
                    f"MATLAB command failed in path '{self.config.name}':\n"
                    + stripped[len(f"{token}_ERR:") :]
                )
            if stripped == f"{token}_END":
                return lines
            lines.append(stripped)

    def _eval_via_engine(self, command: str, timeout_s: float) -> list[str]:
        assert self.engine is not None
        output_buffer = io.StringIO()
        error_holder: list[BaseException] = []
        done = threading.Event()

        def worker() -> None:
            try:
                self.engine.eval(command, nargout=0, stdout=output_buffer, stderr=output_buffer)
            except BaseException as exc:
                error_holder.append(exc)
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True).start()
        if not done.wait(timeout_s):
            raise MatlabSessionError(
                f"Timed out waiting for MATLAB path '{self.config.name}' while executing command."
            )
        if error_holder:
            raise MatlabSessionError(
                f"MATLAB command failed in path '{self.config.name}':\n{error_holder[0]}"
            )
        text = output_buffer.getvalue()
        return [line for line in text.splitlines() if line.strip()]

    def _eval_via_command_files(self, command: str, timeout_s: float) -> list[str]:
        self.command_dir.mkdir(parents=True, exist_ok=True)
        command_id = uuid.uuid4().hex
        request_path = self.command_dir / f"request_{command_id}.m"
        result_path = self.command_dir / f"result_{command_id}.txt"
        request_path.write_text(command, encoding="utf-8")
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if result_path.exists():
                try:
                    lines = result_path.read_text(encoding="utf-8").splitlines()
                finally:
                    try:
                        result_path.unlink()
                    except OSError:
                        pass
                    try:
                        request_path.unlink()
                    except OSError:
                        pass
                if not lines:
                    return []
                status = lines[0].strip()
                payload = lines[1:]
                if status == "STATUS:OK":
                    return payload
                if status == "STATUS:ERR":
                    raise MatlabSessionError(
                        f"MATLAB command failed in path '{self.config.name}':\n" + "\n".join(payload)
                    )
                raise MatlabSessionError(
                    f"MATLAB command returned malformed response in path '{self.config.name}': {status}"
                )
            time.sleep(0.1)
        raise MatlabSessionError(
            f"Timed out waiting for MATLAB path '{self.config.name}' while executing command."
        )

    def _pump_output(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self._output_queue.put(line)

    def _start_simulated(self) -> None:
        self.simulated = True
        self.process = None

    def _simulate_eval(self, command: str) -> list[str]:
        outputs: list[str] = []
        command_lower = command.lower()
        disp_messages = _extract_disp_messages(command)
        script_name = _extract_run_script_name(command)
        cwd = _extract_cd_path(command)
        if cwd:
            self.current_directory = cwd

        if "addpath(genpath(" in command_lower:
            return []
        if "importschemapatterns" in command_lower:
            schema_path = _extract_schema_path_from_import(command)
            if schema_path is None:
                return ["Simulated import completed"]
            data = yaml.safe_load(Path(schema_path).read_text()) or {}
            pattern_names = list((data.get("patterns") or {}).keys())
            outputs.append(f"Simulated import from {schema_path}")
            outputs.append(f"Imported {len(pattern_names)} pattern(s)")
            outputs.extend(pattern_names)
            return outputs
        if script_name == "launch.m":
            outputs.append(f"Simulated launch.m executed in {self.current_directory}")
        elif script_name == "start_script.m":
            exp_id = _extract_matlab_string_assignment(command, "expID")
            if exp_id:
                outputs.append(f"Simulated start_script.m executed for {exp_id}")
            else:
                outputs.append("Simulated start_script.m executed")
        elif script_name == "stop_script.m":
            outputs.append("Simulated stop_script.m executed")

        outputs.extend(message for message in disp_messages if message not in outputs)
        if not outputs:
            outputs.append("Simulated MATLAB command completed")
        return outputs


def matlab_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def matlab_literal(value: Any) -> str:
    if isinstance(value, str):
        return matlab_string(value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "[]"
    if isinstance(value, (int, float)):
        return repr(value)
    raise TypeError(f"Unsupported MATLAB literal type: {type(value)!r}")


def configs_root(repo_root: str | Path) -> Path:
    return Path(repo_root).resolve() / DEFAULT_CONFIGS_ROOT


def list_machine_names(repo_root: str | Path) -> list[str]:
    root = configs_root(repo_root)
    if not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def list_config_names(repo_root: str | Path, machine_name: str) -> list[str]:
    machine_dir = configs_root(repo_root) / machine_name
    if not machine_dir.exists():
        return []
    return sorted(path.name for path in machine_dir.iterdir() if path.is_dir())


def autodetect_machine_name(repo_root: str | Path) -> str | None:
    available = {name.lower(): name for name in list_machine_names(repo_root)}
    if not available:
        return None
    candidates = [
        os.environ.get("COMPUTERNAME", ""),
        os.environ.get("HOSTNAME", ""),
        socket.gethostname(),
    ]
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in available:
            return available[key]
    return None


def load_machine_config(repo_root: str | Path, machine_name: str, config_name: str) -> MachineConfig:
    repo_root_path = Path(repo_root).resolve()
    config_dir = configs_root(repo_root_path) / machine_name / config_name
    config_ini = config_dir / "config.ini"
    if not config_ini.is_file():
        raise FileNotFoundError(f"Config file not found: {config_ini}")

    parser = configparser.ConfigParser()
    parser.read(config_ini)
    config_section = parser["config"] if parser.has_section("config") else {}
    launch_order = _split_csv(_get_string(config_section, None, "launch_order", ""))
    launch_delay_s = _get_float(config_section, None, "launch_delay_s", 20.0)
    photostim_path_raw = _get_string(config_section, None, "photostim_path", "").strip()
    photostim_path = photostim_path_raw or None

    paths: dict[str, PathConfig] = {}
    for section_name in parser.sections():
        if not section_name.startswith("path:"):
            continue
        path_name = section_name.split("path:", 1)[1]
        section = parser[section_name]
        path_dir = config_dir / path_name
        if not path_dir.is_dir():
            raise FileNotFoundError(f"Path directory not found: {path_dir}")

        hsi_variable = _get_string(section, None, "hsi_variable", "hSI")
        paths[path_name] = PathConfig(
            machine_name=machine_name,
            config_name=config_name,
            name=path_name,
            directory=path_dir,
            matlab_executable=_get_string(section, None, "matlab_executable", "matlab"),
            matlab_flags=_split_lines(_get_string(section, None, "matlab_flags", "-nodesktop\n-nosplash")),
            simulation_mode=_get_string(section, None, "simulation_mode", "auto"),
            listener_host=_get_string(section, None, "listener_host", "0.0.0.0"),
            listener_port=_get_int(section, None, "listener_port", 0),
            listener_auto_start=_get_bool(section, None, "listener_auto_start", True),
            reply_host=_get_string(section, None, "reply_host", ""),
            reply_port=_get_int(section, None, "reply_port", 0),
            local_data_root=_normalize_data_root(_get_string(section, None, "local_data_root", "./data"), repo_root_path),
            remote_data_root=_get_string(section, None, "remote_data_root", ""),
            acquisition_folder=_get_string(section, None, "acquisition_folder", path_name),
            hsi_variable=hsi_variable,
            hsictl_variable=_get_string(section, None, "hsictl_variable", "hSICtl"),
            motor_data_variable=_get_string(section, None, "motor_data_variable", "siMotorData"),
            startup_timeout_s=_get_float(section, None, "startup_timeout_s", 60.0),
            command_timeout_s=_get_float(section, None, "command_timeout_s", 60.0),
            repo_matlab_path=(repo_root_path / "matlab").resolve(),
            focus_command=_get_string(section, None, "focus_command", f"{hsi_variable}.startFocus();"),
            xy_transform=_get_string(section, None, "xy_transform", "@(xyz)[xyz(1) xyz(2)]"),
            z_transform=_get_string(section, None, "z_transform", "@(xyz)xyz(3)"),
            point_size_xy=_parse_xy_pair(_get_string(section, None, "point_size_xy", "0,0")),
            rotation_degrees=_get_float(section, None, "rotation_degrees", 0.0),
            pause_duration=_get_float(section, None, "pause_duration", 0.010),
            park_duration=_get_float(section, None, "park_duration", 0.010),
            clear_existing=_get_bool(section, None, "clear_existing", True),
            ignore_frequency=_get_bool(section, None, "ignore_frequency", True),
            stimulus_function=_get_string(section, None, "stimulus_function", "point"),
            power_scale_mode=_get_string(section, None, "power_scale_mode", "multiply"),
        )
        _validate_path_scripts(paths[path_name])

    if not paths:
        raise ValueError(f"No [path:<name>] sections found in {config_ini}")

    if not launch_order:
        launch_order = list(paths.keys())
    for path_name in launch_order:
        if path_name not in paths:
            raise ValueError(f"launch_order references unknown path '{path_name}' in {config_ini}")
    if photostim_path and photostim_path not in paths:
        raise ValueError(f"photostim_path '{photostim_path}' is not defined in {config_ini}")

    return MachineConfig(
        machine_name=machine_name,
        name=config_name,
        directory=config_dir,
        launch_order=launch_order,
        launch_delay_s=launch_delay_s,
        photostim_path=photostim_path,
        paths=paths,
    )


def build_import_command(schema_path: str | Path, path_config: PathConfig) -> str:
    schema_expr = matlab_string(str(Path(schema_path).resolve()))
    point_size_expr = f"[{path_config.point_size_xy[0]} {path_config.point_size_xy[1]}]"
    return "\n".join(
        [
            f"importedPatternNames = opto.scanimage.importSchemaPatterns({path_config.hsi_variable}, {schema_expr}, ...",
            f"    ClearExisting={'true' if path_config.clear_existing else 'false'}, ...",
            f"    StimulusFunction={matlab_string(path_config.stimulus_function)}, ...",
            f"    PointSizeXY={point_size_expr}, ...",
            f"    RotationDegrees={path_config.rotation_degrees}, ...",
            f"    PauseDuration={path_config.pause_duration}, ...",
            f"    ParkDuration={path_config.park_duration}, ...",
            f"    XYTransform={path_config.xy_transform}, ...",
            f"    ZTransform={path_config.z_transform}, ...",
            f"    PowerScaleMode={matlab_string(path_config.power_scale_mode)}, ...",
            f"    IgnoreFrequency={'true' if path_config.ignore_frequency else 'false'});",
            "disp('Imported patterns:');",
            "disp(importedPatternNames);",
        ]
    )


def build_run_script_command(
    path_config: PathConfig,
    script_name: str,
    context: dict[str, Any] | None = None,
) -> str:
    script_path = path_config.directory / script_name
    if not script_path.is_file():
        raise FileNotFoundError(f"Script not found: {script_path}")
    lines = [f"cd({matlab_string(str(path_config.directory))});"]
    for name, value in (context or {}).items():
        lines.append(f"{name} = {matlab_literal(value)};")
    lines.append(f"run({matlab_string(script_name)});")
    return "\n".join(lines)


def build_experiment_context(path_config: PathConfig, exp_id: str) -> ExperimentContext:
    animal_id = exp_id[14:] if len(exp_id) >= 15 else ""
    exp_dir = ntpath.join(path_config.local_data_root, animal_id, exp_id, path_config.acquisition_folder)
    if path_config.remote_data_root:
        exp_dir_remote = ntpath.join(
            path_config.remote_data_root,
            animal_id,
            exp_id,
            path_config.acquisition_folder,
        )
    else:
        exp_dir_remote = ""
    return ExperimentContext(
        exp_id=exp_id,
        animal_id=animal_id,
        exp_dir=exp_dir,
        exp_dir_remote=exp_dir_remote,
        reply_host=path_config.reply_host,
        reply_port=path_config.reply_port,
    )


def context_to_matlab_variables(context: ExperimentContext) -> dict[str, Any]:
    return {
        "expID": context.exp_id,
        "animalID": context.animal_id,
        "expDir": context.exp_dir,
        "expDirRemote": context.exp_dir_remote,
        "listenerReplyHost": context.reply_host,
        "listenerReplyPort": context.reply_port,
    }


def _validate_path_scripts(path_config: PathConfig) -> None:
    for script in (path_config.launch_script, path_config.start_script, path_config.stop_script):
        if not script.is_file():
            raise FileNotFoundError(f"Required script not found: {script}")


def _split_lines(value: str) -> list[str]:
    return [line.strip() for line in value.splitlines() if line.strip()]


def _split_csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_xy_pair(value: str) -> tuple[float, float]:
    parts = [part.strip() for part in value.split(",")]
    if len(parts) != 2:
        raise ValueError(f"Expected point_size_xy to contain two comma-separated values, got: {value}")
    return float(parts[0]), float(parts[1])


def _get_string(
    section: configparser.SectionProxy | dict[str, str],
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: str,
) -> str:
    if hasattr(section, "get"):
        value = section.get(option, fallback=None)  # type: ignore[call-arg]
        if value is not None:
            return value
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.get(option)
    return fallback


def _get_float(
    section: configparser.SectionProxy | dict[str, str],
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: float,
) -> float:
    if hasattr(section, "get"):
        value = section.get(option, fallback=None)  # type: ignore[call-arg]
        if value is not None:
            return float(value)
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.getfloat(option)
    return fallback


def _get_int(
    section: configparser.SectionProxy | dict[str, str],
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: int,
) -> int:
    if hasattr(section, "get"):
        value = section.get(option, fallback=None)  # type: ignore[call-arg]
        if value is not None:
            return int(value)
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.getint(option)
    return fallback


def _get_bool(
    section: configparser.SectionProxy | dict[str, str],
    defaults: configparser.SectionProxy | None,
    option: str,
    fallback: bool,
) -> bool:
    if hasattr(section, "get"):
        value = section.get(option, fallback=None)  # type: ignore[call-arg]
        if value is not None:
            return str(value).strip().lower() in {"1", "true", "yes", "on"}
    if defaults is not None and defaults.get(option, fallback=None) is not None:
        return defaults.getboolean(option)
    return fallback


def _normalize_data_root(value: str, repo_root: Path) -> str:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return str(candidate)
    if len(value) >= 2 and value[1] == ":":
        return value
    if value.startswith("\\\\"):
        return value
    return str((repo_root / candidate).resolve())


def _extract_disp_messages(command: str) -> list[str]:
    messages: list[str] = []
    marker = "disp('"
    start = 0
    while True:
        idx = command.find(marker, start)
        if idx < 0:
            return messages
        end = command.find("')", idx + len(marker))
        if end < 0:
            return messages
        messages.append(command[idx + len(marker) : end].replace("''", "'"))
        start = end + 2


def _extract_schema_path_from_import(command: str) -> str | None:
    marker = "importSchemaPatterns("
    idx = command.find(marker)
    if idx < 0:
        return None
    quoted_start = command.find("'", idx)
    if quoted_start < 0:
        return None
    quoted_end = command.find("'", quoted_start + 1)
    if quoted_end < 0:
        return None
    return command[quoted_start + 1 : quoted_end].replace("''", "'")


def _extract_run_script_name(command: str) -> str | None:
    marker = "run('"
    idx = command.find(marker)
    if idx < 0:
        return None
    end = command.find("')", idx + len(marker))
    if end < 0:
        return None
    return command[idx + len(marker) : end]


def _extract_cd_path(command: str) -> str | None:
    marker = "cd('"
    idx = command.find(marker)
    if idx < 0:
        return None
    end = command.find("')", idx + len(marker))
    if end < 0:
        return None
    return command[idx + len(marker) : end]


def _extract_matlab_string_assignment(command: str, variable_name: str) -> str | None:
    marker = f"{variable_name} = '"
    idx = command.find(marker)
    if idx < 0:
        return None
    end = command.find("';", idx + len(marker))
    if end < 0:
        return None
    return command[idx + len(marker) : end]
