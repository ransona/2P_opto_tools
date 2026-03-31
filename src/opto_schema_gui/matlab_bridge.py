from __future__ import annotations

import configparser
import io
import json
import ntpath
import os
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

try:
    import matlab.engine as matlab_engine
except ModuleNotFoundError:
    matlab_engine = None


DEFAULT_CONFIGS_ROOT = Path("configs")
MACHINE_CONFIG_FILENAME = "machine.ini"


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
    engine_name: str
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
    sequence_block_duration_s: float
    trial_waveform_output_port: str
    trial_waveform_photostim_trigger_term: str
    trial_waveform_start_trigger_port: str
    trial_waveform_start_trigger_edge: str
    trial_waveform_sample_rate_hz: float
    trial_waveform_pulse_width_ms: float

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


@dataclass
class MachineUiConfig:
    default_config: str | None
    screen_index: int | None
    start_maximized: bool


class MatlabSessionError(RuntimeError):
    pass


class MatlabSession:
    def __init__(self, config: PathConfig, force_simulated: bool = False):
        self.config = config
        self.force_simulated = force_simulated
        self.engine = None
        self.simulated = False
        self.current_directory = str(config.directory)
        self.started_with_launch = False
        self.attached = False
        self.launch_process = None
        self.sim_sequence: list[int] = []
        self.sim_sequence_position = 1

    def start(self, startup_command: str | None = None) -> None:
        if self.engine is not None or self.simulated:
            return

        if self.force_simulated or self.config.simulation_mode == "always":
            self._start_simulated()
            self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
            return

        if matlab_engine is None:
            if self.config.simulation_mode == "auto":
                self._start_simulated()
                self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
                return
            raise MatlabSessionError(
                "matlab.engine is not installed in this Python environment. "
                "Install the MATLAB Engine for Python to run live ScanImage control."
            )

        connected = self._try_connect_existing()
        if connected:
            self.attached = True
            self.started_with_launch = False
            try:
                self._validate_connected_session()
                self._set_working_directory()
                return
            except Exception:
                self.engine = None
                self.attached = False

        self.attached = False
        self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
        try:
            self._launch_external_and_connect(startup_command)
        except MatlabSessionError:
            if self.config.simulation_mode == "auto":
                self._start_simulated()
                return
            raise

    def stop(self) -> None:
        if self.simulated:
            self.simulated = False
            return
        if self.engine is not None:
            self.engine = None
            self.attached = False
            return

    def eval(self, command: str, timeout_s: float = 30.0) -> list[str]:
        if self.simulated:
            return self._simulate_eval(command)
        if self.engine is not None:
            return self._eval_via_engine(command, timeout_s)
        raise MatlabSessionError(f"MATLAB session '{self.config.name}' is not running.")

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
        text = output_buffer.getvalue()
        if error_holder:
            detail = str(error_holder[0])
            if text.strip():
                raise MatlabSessionError(
                    f"MATLAB command failed in path '{self.config.name}'.\nOutput before error:\n{text}\nError:\n{detail}"
                )
            raise MatlabSessionError(
                f"MATLAB command failed in path '{self.config.name}':\n{detail}"
            )
        return [line for line in text.splitlines() if line.strip()]

    def _start_simulated(self) -> None:
        self.simulated = True
        self.process = None
        self.sim_sequence = []
        self.sim_sequence_position = 1

    def _try_connect_existing(self) -> bool:
        assert matlab_engine is not None
        engine_name = self.config.engine_name
        try:
            available = matlab_engine.find_matlab()
        except Exception:
            available = ()
        if engine_name not in available:
            return False
        try:
            self.engine = self._connect_matlab_with_timeout(engine_name, timeout_s=5.0)
        except Exception as exc:
            self.engine = None
            return False
        return True

    @staticmethod
    def _connect_matlab_with_timeout(engine_name: str, timeout_s: float):
        assert matlab_engine is not None
        result: list[object] = []
        error_holder: list[BaseException] = []
        done = threading.Event()

        def worker() -> None:
            try:
                result.append(matlab_engine.connect_matlab(engine_name))
            except BaseException as exc:
                error_holder.append(exc)
            finally:
                done.set()

        threading.Thread(target=worker, daemon=True).start()
        if not done.wait(timeout_s):
            raise MatlabSessionError(
                f"Timed out connecting to shared MATLAB engine '{engine_name}'."
            )
        if error_holder:
            raise error_holder[0]
        if not result:
            raise MatlabSessionError(
                f"Connecting to shared MATLAB engine '{engine_name}' returned no session."
            )
        return result[0]

    def _share_engine(self) -> None:
        if self.engine is None:
            return
        try:
            self.engine.eval(
                f"matlab.engine.shareEngine({matlab_string(self.config.engine_name)});",
                nargout=0,
                stdout=io.StringIO(),
                stderr=io.StringIO(),
            )
        except BaseException as exc:
            raise MatlabSessionError(
                f"MATLAB session for path '{self.config.name}' could not be shared as '{self.config.engine_name}': {exc}"
            ) from exc

    def _validate_connected_session(self) -> None:
        lines = self.eval(
            "\n".join(
                [
                    build_global_preamble(self.config),
                    f"assert(exist({matlab_string(self.config.hsi_variable)}, 'var') == 1, 'Missing {self.config.hsi_variable} in MATLAB workspace.');",
                    f"assert(exist({matlab_string(self.config.hsictl_variable)}, 'var') == 1, 'Missing {self.config.hsictl_variable} in MATLAB workspace.');",
                    f"assert(~isempty({self.config.hsi_variable}), '{self.config.hsi_variable} is empty.');",
                    f"assert(isprop({self.config.hsi_variable}, 'hPhotostim'), '{self.config.hsi_variable} is not a valid ScanImage handle.');",
                    "disp('MATLAB reconnect validation passed');",
                ]
            ),
            timeout_s=self.config.command_timeout_s,
        )
        if not lines:
            return

    def _launch_external_and_connect(self, startup_command: str | None) -> None:
        assert matlab_engine is not None
        matlab_cmd = [self.config.matlab_executable, *self.config.matlab_flags]
        startup = self._build_startup_command(startup_command)
        matlab_cmd.extend(["-r", startup])
        try:
            self.launch_process = subprocess.Popen(matlab_cmd, cwd=str(self.config.directory))
        except Exception as exc:
            raise MatlabSessionError(
                f"Could not launch MATLAB process for path '{self.config.name}': {exc}"
            ) from exc

        deadline = time.monotonic() + self.config.startup_timeout_s
        last_error = None
        while time.monotonic() < deadline:
            try:
                available = matlab_engine.find_matlab()
            except Exception as exc:
                last_error = exc
                available = ()
            if self.config.engine_name in available:
                try:
                    self.engine = self._connect_matlab_with_timeout(self.config.engine_name, timeout_s=5.0)
                    self._validate_connected_session()
                    self._set_working_directory()
                    return
                except Exception as exc:
                    last_error = exc
            time.sleep(1.0)
        raise MatlabSessionError(
            f"Timed out waiting to connect to shared MATLAB engine '{self.config.engine_name}' "
            f"for path '{self.config.name}'. Last error: {last_error}"
        )

    def _build_startup_command(self, startup_command: str | None) -> str:
        commands: list[str] = []
        if startup_command:
            commands.append(startup_command)
        else:
            commands.append(f"addpath(genpath({matlab_string(str(self.config.repo_matlab_path))}))")
        commands.append(f"matlab.engine.shareEngine({matlab_string(self.config.engine_name)})")
        body = "; ".join(commands)
        return (
            "try; "
            + body
            + "; catch ME; disp(getReport(ME,'extended')); end"
        )

    def _set_working_directory(self) -> None:
        if self.simulated:
            self.current_directory = str(self.config.directory)
            return
        if self.engine is None:
            return
        target_dir = str(self.config.directory)
        self.eval(f"cd({matlab_string(target_dir)})", timeout_s=self.config.command_timeout_s)
        self.current_directory = target_dir

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
        if "prepareschemaphotostim" in command_lower:
            schema_path = _extract_schema_path_from_import(command)
            if schema_path is None:
                return ["Simulated photostim prep completed"]
            data = yaml.safe_load(Path(schema_path).read_text()) or {}
            patterns = data.get("patterns") or {}
            pattern_names = list(patterns.keys())
            pattern_numbers = [
                index + 1
                for index, name in enumerate(pattern_names)
            ]
            outputs.append(f"Simulated photostim prep from {schema_path}")
            outputs.append("Reserved stimulus groups: 1=BLANK, 2=PARK")
            for pattern_name, pattern_number in zip(pattern_names, pattern_numbers):
                outputs.append(f"Prepared P{pattern_number} from pattern '{pattern_name}' as stimulus group {pattern_number + 2}")
            self.sim_sequence = list(range(1, len(pattern_names) + 3))
            self.sim_sequence_position = 1
            outputs.append("Simulated photostim mask generation ready")
            return outputs
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
        if "trigger_photostim_sequence" in command_lower:
            sequence_values = _extract_numeric_vector_assignment(command, "triggerSequence")
            insert_position = 1
            self.sim_sequence = list(sequence_values)
            self.sim_sequence_position = 2 if len(self.sim_sequence) > 1 else 1
            outputs.append("TRIGGER_PHOTOSTIM_INSERT_POSITION")
            outputs.append(str(insert_position))
            outputs.append("TRIGGER_PHOTOSTIM_SEQUENCE")
            outputs.append(str(sequence_values))
            outputs.append("TRIGGER_PHOTOSTIM_NUM_SEQUENCES")
            outputs.append("1")
            outputs.append("TRIGGER_PHOTOSTIM_READY")
            return outputs
        if "photostimtrialtriggertimessec" in command_lower and "trial_waveform_ready" in command_lower:
            outputs.append("TRIAL_WAVEFORM_READY")
            return outputs
        if "trial_waveform_armed" in command_lower:
            outputs.append("TRIAL_WAVEFORM_ARMED")
            return outputs
        if "trial_waveform_started" in command_lower:
            outputs.append("TRIAL_WAVEFORM_STARTED")
            return outputs
        if "trial_waveform_task_active" in command_lower:
            outputs.append("TRIAL_WAVEFORM_TASK_ACTIVE")
            outputs.append("0")
            outputs.append("TRIAL_WAVEFORM_TASK_DONE")
            outputs.append("1")
            outputs.append("TRIAL_WAVEFORM_STATUS_READY")
            return outputs
        if "trial_waveform_stopped" in command_lower:
            outputs.append("TRIAL_WAVEFORM_STOPPED")
            return outputs
        if "raw_vdaq_do_test_active" in command_lower:
            outputs.append("RAW_VDAQ_DO_TEST_ACTIVE")
            outputs.append("0")
            outputs.append("RAW_VDAQ_DO_TEST_DONE")
            outputs.append("1")
            outputs.append("RAW_VDAQ_DO_TEST_STATUS_READY")
            return outputs
        if "test stim waveform external start" in command_lower:
            outputs.extend(
                [
                    "----------",
                    "Test stim waveform external start",
                    "Configured waveform output port:",
                    self.config.trial_waveform_output_port,
                    "Configured waveform start trigger port:",
                    self.config.trial_waveform_start_trigger_port,
                    "Configured photostim trigger term:",
                    self.config.trial_waveform_photostim_trigger_term,
                    "Photostim active before test:",
                    "1",
                    "Photostim sequence position before test:",
                    str(self.sim_sequence_position),
                    "Photostim completed sequences before test:",
                    "0",
                    "Waveform task pulse width sec:",
                    str(self.config.trial_waveform_pulse_width_ms / 1000.0),
                    "Waveform task total duration sec:",
                    "0.61",
                    "Waveform external-start trigger times sec:",
                    "[0.1 0.2 0.3 0.4 0.5]",
                    "TRIAL_WAVEFORM_READY_FOR_EXTERNAL_START",
                ]
            )
            return outputs
        if "test stim waveform" in command_lower:
            outputs.extend(
                [
                    "----------",
                    "Test stim waveform",
                    "Photostim active before test:",
                    "1",
                    "Photostim mode before test:",
                    "sequence",
                    "Photostim trigger term before test:",
                    self.config.trial_waveform_output_port.split("/")[-1],
                    "Photostim sequence position before test:",
                    str(self.sim_sequence_position),
                    "Photostim completed sequences before test:",
                    "0",
                    "Photostim selected sequence before test:",
                    str(self.sim_sequence),
                    "Single-pulse width sweep sec:",
                    "[0.01 0.05 0.1 0.25 0.5]",
                    "Single pulse width sec:",
                    "0.01",
                    "Waveform single-pulse test task started",
                    "Single pulse advanced photostim:",
                    "1",
                    "Single pulse width sec:",
                    "0.05",
                    "Waveform single-pulse test task started",
                    "Single pulse advanced photostim:",
                    "1",
                    "Single pulse width sec:",
                    "0.1",
                    "Waveform single-pulse test task started",
                    "Single pulse advanced photostim:",
                    "1",
                    "Single pulse width sec:",
                    "0.25",
                    "Waveform single-pulse test task started",
                    "Single pulse advanced photostim:",
                    "1",
                    "Single pulse width sec:",
                    "0.5",
                    "Waveform single-pulse test task started",
                    "Single pulse advanced photostim:",
                    "1",
                    "Waveform task pulse width sec:",
                    str(self.config.trial_waveform_pulse_width_ms / 1000.0),
                    "Waveform task total duration sec:",
                    "0.61",
                    "Waveform train test task started",
                    "Waveform train advanced photostim count:",
                    "5",
                ]
            )
            self.sim_sequence_position += 5
            return outputs
        if "hps.triggerstim()" in command_lower:
            self.sim_sequence_position += 1
            outputs.append("SOFTWARE_TRIGGER_FIRED")
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


def matlab_double_quoted_string(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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


def get_machine_default_config_name(repo_root: str | Path, machine_name: str) -> str | None:
    return load_machine_ui_config(repo_root, machine_name).default_config


def load_machine_ui_config(repo_root: str | Path, machine_name: str) -> MachineUiConfig:
    machine_dir = configs_root(repo_root) / machine_name
    machine_ini = machine_dir / MACHINE_CONFIG_FILENAME
    if not machine_ini.is_file():
        return MachineUiConfig(default_config=None, screen_index=None, start_maximized=True)

    parser = configparser.ConfigParser()
    parser.read(machine_ini)
    section = parser["machine"] if parser.has_section("machine") else {}
    default_config = _get_string(section, None, "default_config", "").strip()
    raw_screen_index = _get_string(section, None, "screen_index", "").strip()
    screen_index = None
    if raw_screen_index:
        screen_index = int(raw_screen_index)
    start_maximized = _get_bool(section, None, "start_maximized", True)
    return MachineUiConfig(
        default_config=default_config or None,
        screen_index=screen_index,
        start_maximized=start_maximized,
    )


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
            engine_name=_get_string(section, None, "engine_name", f"opto_{path_name}"),
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
            sequence_block_duration_s=_get_float(section, None, "sequence_block_duration_s", 0.25),
            trial_waveform_output_port=_get_string(section, None, "trial_waveform_output_port", "/vDAQ0/D1.7"),
            trial_waveform_photostim_trigger_term=_get_string(
                section, None, "trial_waveform_photostim_trigger_term", "D1.7"
            ),
            trial_waveform_start_trigger_port=_get_string(
                section, None, "trial_waveform_start_trigger_port", "/vDAQ0/D0.6"
            ),
            trial_waveform_start_trigger_edge=_get_string(
                section, None, "trial_waveform_start_trigger_edge", "rising"
            ),
            trial_waveform_sample_rate_hz=_get_float(section, None, "trial_waveform_sample_rate_hz", 2_000_000.0),
            trial_waveform_pulse_width_ms=_get_float(section, None, "trial_waveform_pulse_width_ms", 10.0),
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


def build_import_command(
    schema_path: str | Path,
    path_config: PathConfig,
    pattern_names: list[str] | None = None,
    prepare_sequence: bool = False,
    start_photostim: bool = False,
    schema_json_path: str | Path | None = None,
) -> str:
    schema_expr = matlab_string(str(Path(schema_path).resolve()))
    point_size_expr = f"[{path_config.point_size_xy[0]} {path_config.point_size_xy[1]}]"
    if prepare_sequence or start_photostim:
        if schema_json_path is not None:
            schema_json_expr = matlab_string(str(Path(schema_json_path).resolve()))
            schema_load_line = f"schemaData = jsondecode(fileread({schema_json_expr}));"
        else:
            schema_payload = yaml.safe_load(Path(schema_path).read_text()) or {}
            schema_payload_expr = matlab_string(json.dumps(schema_payload, separators=(",", ":")))
            schema_load_line = f"schemaData = jsondecode({schema_payload_expr});"
        lines = [
            build_global_preamble(path_config),
            schema_load_line,
            f"[importedPatternNames, importedPatternNumbers] = opto.scanimage.prepareSchemaPhotostim({path_config.hsi_variable}, schemaData, ...",
            "    PreStimPauseDuration=0.001, ...",
            "    BlankDuration=0.001, ...",
            "    ParkDuration=0.001, ...",
            f"    TriggerTerm={matlab_string(path_config.trial_waveform_photostim_trigger_term)}, ...",
            "    MinCenterDistanceUm=15, ...",
            "    Revolutions=5);",
            "disp('Prepared schema photostim patterns used by sequence groups:');",
            "disp(importedPatternNames);",
            "disp(importedPatternNumbers);",
        ]
        return '\n'.join(lines)
    lines = [
            build_global_preamble(path_config),
            f"hPs = {path_config.hsi_variable}.hPhotostim;",
        ]
    pattern_names_expr = "strings(0, 1)"
    if pattern_names:
        quoted = "; ".join(matlab_double_quoted_string(name) for name in pattern_names)
        pattern_names_expr = f"[{quoted}]"
    lines.extend(
        [
            f"importedPatternNames = opto.scanimage.importSchemaPatterns({path_config.hsi_variable}, {schema_expr}, ...",
            f"    PatternNames={pattern_names_expr}, ...",
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
    return "\n".join(lines)


def build_schema_payload_load_command(
    path_config: PathConfig,
    schema_json_path: str | Path,
    schema_var_name: str = "schemaData",
) -> str:
    schema_json_expr = matlab_string(str(Path(schema_json_path).resolve()))
    lines = [
        build_global_preamble(path_config),
        f"{schema_var_name} = jsondecode(fileread({schema_json_expr}));",
        f"disp('SCHEMA_PAYLOAD_READY');",
        f"disp(fieldnames({schema_var_name}));",
    ]
    return "\n".join(lines)


def build_prepare_schema_photostim_command(
    path_config: PathConfig,
    schema_var_name: str = "schemaData",
) -> str:
    lines = [
        build_global_preamble(path_config),
        f"[importedPatternNames, importedPatternNumbers] = opto.scanimage.prepareSchemaPhotostim({path_config.hsi_variable}, {schema_var_name}, ...",
        "    PreStimPauseDuration=0.001, ...",
        "    BlankDuration=0.001, ...",
        "    ParkDuration=0.001, ...",
        f"    BlockDuration={path_config.sequence_block_duration_s}, ...",
        f"    TriggerTerm={matlab_string(path_config.trial_waveform_photostim_trigger_term)}, ...",
        "    MinCenterDistanceUm=15, ...",
        "    Revolutions=5);",
        "disp('Prepared schema photostim patterns used by sequence groups:');",
        "disp(importedPatternNames);",
        "disp(importedPatternNumbers);",
    ]
    return "\n".join(lines)


def build_global_preamble(path_config: PathConfig) -> str:
    names = [path_config.hsi_variable, path_config.hsictl_variable]
    if path_config.motor_data_variable:
        names.append(path_config.motor_data_variable)
    return "global " + " ".join(dict.fromkeys(name for name in names if name)) + ";"


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


def _matlab_matrix(rows: list[list[float]]) -> str:
    if not rows:
        return "zeros(0, 4)"
    return "[" + "; ".join(" ".join(repr(value) for value in row) for row in rows) + "]"


def build_test_photostim_command(
    path_config: PathConfig,
    patterns: list[dict[str, Any]] | None = None,
) -> str:
    hsi = path_config.hsi_variable
    if patterns is None:
        patterns = [
            {
                "name": "TEST SLM Group 1",
                "duration_s": 0.010,
                "overall_power": 5.0,
                "spiral_width": 10.0,
                "spiral_height": 10.0,
                "cells": [
                    {"x": 0.25, "y": 0.45, "z": 0.0, "relative_power": 1.0},
                    {"x": 0.35, "y": 0.55, "z": 0.0, "relative_power": 1.0},
                ],
            },
            {
                "name": "TEST SLM Group 2",
                "duration_s": 0.010,
                "overall_power": 5.0,
                "spiral_width": 10.0,
                "spiral_height": 10.0,
                "cells": [
                    {"x": 0.65, "y": 0.35, "z": 0.0, "relative_power": 1.0},
                    {"x": 0.75, "y": 0.50, "z": 0.0, "relative_power": 1.0},
                    {"x": 0.85, "y": 0.65, "z": 0.0, "relative_power": 1.0},
                ],
            },
        ]

    group_lines: list[str] = []
    sequence_indices: list[str] = []
    for index, pattern in enumerate(patterns, start=1):
        cells = pattern.get("cells", [])
        if not cells:
            continue
        point_rows = [
            [
                float(cell["x"]),
                float(cell["y"]),
                float(cell["z"]),
                float(cell.get("relative_power", 1.0)),
            ]
            for cell in cells
        ]
        points_um = _matlab_matrix(point_rows)
        power_fractions = "[" + " ".join(repr(row[3]) for row in point_rows) + "]"
        group_name = matlab_string(str(pattern.get("name", f"TEST SLM Group {index}")))
        duration_s = float(pattern.get("duration_s", 0.010))
        overall_power = float(pattern.get("overall_power", 5.0))
        spiral_width = float(pattern.get("spiral_width", 0.0))
        spiral_height = float(pattern.get("spiral_height", 0.0))
        group_lines.extend(
            [
                f"hGroup{index} = scanimage.mroi.RoiGroup({group_name});",
                f"spiralWidthUm = {spiral_width};",
                f"spiralHeightUm = {spiral_height};",
                f"sf = scanimage.mroi.scanfield.fields.StimulusField();",
                f"pointsUm = {points_um};",
                "pointsRef = pointsUm;",
                "if isscalar(resXY); pointsRef(:,1:2) = pointsUm(:,1:2) ./ [resXY resXY]; else; pointsRef(:,1) = pointsUm(:,1) ./ resXY(1); pointsRef(:,2) = pointsUm(:,2) ./ resXY(2); end",
                "weights = pointsRef(:,4);",
                "weightSum = sum(weights);",
                "assert(weightSum > 0, 'SLM point weights must sum to a positive value.');",
                "centerRef = sum(pointsRef(:,1:2) .* weights, 1) ./ weightSum;",
                "if isscalar(resXY); sizeRef = [spiralWidthUm spiralHeightUm] ./ [resXY resXY]; else; sizeRef = [spiralWidthUm spiralHeightUm] ./ resXY(1:2); end",
                "sf.centerXY = centerRef;",
                "sf.sizeXY = sizeRef;",
                f"sf.duration = {duration_s};",
                "sf.repetitions = 1;",
                "sf.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral;",
                "sf.stimparams = {'revolutions', 5, 'direction', 'outward'};",
                "sf.slmPattern = [pointsRef(:,1:2) - centerRef, pointsRef(:,3:4)];",
                "if ismethod(sf, 'recenterGalvoOntoSlmPattern'); sf.recenterGalvoOntoSlmPattern(); end",
                f"if isprop(sf,'powerFractions'); sf.powerFractions = {power_fractions}; end",
                "powers = zeros(1, nBeams);",
                f"powers(3) = {overall_power};",
                "sf.powers = powers;",
                "roi = scanimage.mroi.Roi();",
                "roi.add(0, sf);",
                f"hGroup{index}.add(roi);",
                f"hPs.stimRoiGroups(end + 1) = hGroup{index};",
                f"disp('TEST_GROUP_{index}_ROI_COUNT');",
                f"disp(numel(hGroup{index}.rois));",
                f"disp('TEST_GROUP_{index}_POINT_COUNT');",
                f"disp(size(sf.slmPattern, 1));",
            ]
        )
        sequence_indices.append(str(index))

    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            f"assert(~isempty({hsi}.objectiveResolution), 'objectiveResolution is not set in ScanImage.');",
            "hPs = " + hsi + ".hPhotostim;",
            f"resXY = {hsi}.objectiveResolution;",
            "assert(isprop(hPs,'hasSlm') && hPs.hasSlm, 'No SLM available in the photostim configuration.');",
            "hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0);",
            "hPs.sequenceSelectedStimuli = [];",
            "nBeams = 1;",
            "try; ss = hPs.stimScannerset; if most.idioms.isValidObj(ss); nBeams = numel(ss.beams); end; catch; nBeams = 1; end",
            "assert(nBeams >= 3, sprintf('Photostim expects at least 3 beams; only %d configured.', nBeams));",
            *group_lines,
            "hPs.stimulusMode = 'sequence';",
            f"hPs.sequenceSelectedStimuli = [{ ' '.join(sequence_indices) if sequence_indices else '' }];",
            "hPs.numSequences = 1;",
            "if isprop(hPs,'autoTriggerPeriod'); hPs.autoTriggerPeriod = 0; end",
            "if isprop(hPs,'stimImmediately'); hPs.stimImmediately = false; end",
            "disp('TEST_PHOTOSTIM_GROUP_COUNT');",
            "disp(numel(hPs.stimRoiGroups));",
            "disp('TEST_STIMULUS_MODE');",
            "disp(string(hPs.stimulusMode));",
            "disp('TEST_SEQUENCE_SELECTED_STIMULI');",
            "disp(hPs.sequenceSelectedStimuli);",
            "disp('TEST_NUM_SEQUENCES');",
            "disp(hPs.numSequences);",
        ]
    )


def build_inspect_photostim_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "assert(~isempty(hPs.stimRoiGroups), 'No photostim stimulus groups exist to inspect.');",
            "rg = hPs.stimRoiGroups(1);",
            "disp('INSPECT_GROUP_NAME');",
            "disp(string(rg.name));",
            "disp('INSPECT_GROUP_ROI_COUNT');",
            "disp(numel(rg.rois));",
            "assert(~isempty(rg.rois), 'Stimulus group has no ROIs.');",
            "roi = rg.rois(1);",
            "disp('INSPECT_ROI_SCANFIELD_COUNT');",
            "disp(numel(roi.scanfields));",
            "assert(~isempty(roi.scanfields), 'Stimulus ROI has no scanfields.');",
            "sf = roi.scanfields(1);",
            "disp('INSPECT_SCANFIELD_CLASS');",
            "disp(class(sf));",
            "disp('INSPECT_CENTER_XY');",
            "if isprop(sf,'centerXY'); disp(sf.centerXY); else; disp('missing'); end",
            "disp('INSPECT_SIZE_XY');",
            "if isprop(sf,'sizeXY'); disp(sf.sizeXY); else; disp('missing'); end",
            "disp('INSPECT_POWERS');",
            "if isprop(sf,'powers'); disp(sf.powers); else; disp('missing'); end",
            "disp('INSPECT_STIMPARAMS');",
            "if isprop(sf,'stimparams'); disp(sf.stimparams); else; disp('missing'); end",
            "disp('INSPECT_SLMPATTERN');",
            "if isprop(sf,'slmPattern'); disp(sf.slmPattern); else; disp('missing'); end",
            "disp('INSPECT_RELEVANT_SCANFIELD_PROPS');",
            "sfProps = string(properties(sf));",
            "disp(sfProps(contains(lower(sfProps),'slm') | contains(lower(sfProps),'point') | contains(lower(sfProps),'target') | contains(lower(sfProps),'weight') | contains(lower(sfProps),'power')));",
        ]
    )


def build_trigger_photostim_command(
    path_config: PathConfig,
    stimulus_group_indices: list[int],
) -> str:
    hsi = path_config.hsi_variable
    sequence_expr = "[" + " ".join(str(int(idx)) for idx in stimulus_group_indices) + "]"

    lines = [
        build_global_preamble(path_config),
        f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
        "hPs = " + hsi + ".hPhotostim;",
        f"trialTail = {sequence_expr};",
        "assert(~isempty(trialTail), 'Trigger sequence must contain at least one prepared stimulus group.');",
        "assert(all(trialTail >= 1) && all(trialTail <= numel(hPs.stimRoiGroups)), 'Trigger sequence references an invalid stimulus group.');",
        "currentSequence = hPs.sequenceSelectedStimuli;",
        "if isempty(currentSequence); currentSequence = 2; end",
        "currentPosition = [];",
        "if ~isempty(hPs.sequencePosition); currentPosition = double(hPs.sequencePosition); end",
        "if isempty(currentPosition) || currentPosition < 1 || currentPosition > numel(currentSequence);",
        "    currentPosition = 1;",
        "end",
        "preservedPrefix = currentSequence(1:currentPosition);",
        "triggerSequence = [preservedPrefix(:).' trialTail(:).'];",
        "disp('TRIGGER_PHOTOSTIM_INSERT_POSITION');",
        "disp(double(currentPosition + 1));",
        "disp('TRIGGER_PHOTOSTIM_IDLE_POSITION');",
        "disp(double(currentPosition + numel(trialTail)));",
        "hPs.sequenceSelectedStimuli = triggerSequence;",
        "hPs.numSequences = 1;",
        "if isprop(hPs,'stimImmediately'); hPs.stimImmediately = false; end",
        "if ~hPs.active;",
            "    hPs.start();",
        "end",
        "disp('TRIGGER_PHOTOSTIM_SEQUENCE');",
        "disp(triggerSequence);",
        "disp('TRIGGER_PHOTOSTIM_MODE');",
        "disp(string(hPs.stimulusMode));",
        "disp('TRIGGER_PHOTOSTIM_NUM_SEQUENCES');",
        "disp(double(hPs.numSequences));",
        "disp('TRIGGER_PHOTOSTIM_ACTIVE');",
        "disp(double(hPs.active));",
        "disp('TRIGGER_PHOTOSTIM_SEQUENCE_POSITION');",
        "if isempty(hPs.sequencePosition); disp('NaN'); else; disp(double(hPs.sequencePosition)); end",
        "disp('TRIGGER_PHOTOSTIM_READY');",
    ]
    return "\n".join(lines)


def build_photostim_sequence_status_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "disp('PHOTOSTIM_ACTIVE');",
            "disp(double(hPs.active));",
            "disp('PHOTOSTIM_SEQUENCE_POSITION');",
            "if isempty(hPs.sequencePosition); disp('NaN'); else; disp(double(hPs.sequencePosition)); end",
            "disp('PHOTOSTIM_COMPLETED_SEQUENCES');",
            "if isempty(hPs.completedSequences); disp('NaN'); else; disp(double(hPs.completedSequences)); end",
            "disp('PHOTOSTIM_SEQUENCE_SELECTED');",
            "disp(hPs.sequenceSelectedStimuli);",
            "disp('PHOTOSTIM_STATUS_TEXT');",
            "disp(string(hPs.status));",
            "disp('PHOTOSTIM_STATUS_READY');",
        ]
    )


def build_abort_photostim_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "if hPs.active;",
            "    hPs.abort();",
            "end",
            "disp('ABORT_PHOTOSTIM_READY');",
        ]
    )


def build_software_trigger_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "hPs.triggerStim();",
            "disp('SOFTWARE_TRIGGER_FIRED');",
        ]
    )


def build_prepare_trial_waveform_command(
    path_config: PathConfig,
    trigger_times_s: list[float],
    external_start: bool,
) -> str:
    trigger_times_expr = "[]" if not trigger_times_s else "[" + " ".join(repr(float(v)) for v in trigger_times_s) + "]"
    total_duration_s = (
        (max(trigger_times_s) if trigger_times_s else 0.0)
        + (path_config.trial_waveform_pulse_width_ms / 1000.0)
        + 0.05
    )
    start_trigger_expr = (
        matlab_string(path_config.trial_waveform_start_trigger_port.split("/")[-1]) if external_start else "''"
    )
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"trialTriggerTimesSec = {trigger_times_expr};",
            f"trialPulseWidthSec = {path_config.trial_waveform_pulse_width_ms / 1000.0!r};",
            f"trialTotalDurationSec = {total_duration_s!r};",
            "trialWaveformMode = "
            + matlab_string("external trigger" if external_start else "software start")
            + ";",
            "disp(['Preparing waveform to advance stimulus groups: mode=' trialWaveformMode "
            + "', pulses=' num2str(numel(trialTriggerTimesSec)) "
            + "', pulse_width_s=' num2str(trialPulseWidthSec, '%.4f') "
            + "', total_duration_s=' num2str(trialTotalDurationSec, '%.4f')]);",
            "do_task = opto.scanimage.testVdaqDoTriggeredByDi("
            + f"'outputLine', {matlab_string(path_config.trial_waveform_output_port.split('/')[-1])}, "
            + f"'startTrigger', {start_trigger_expr}, "
            + f"'sampleRate_Hz', {path_config.trial_waveform_sample_rate_hz!r}, "
            + "'pulseTimes_s', trialTriggerTimesSec, "
            + "'pulseWidth_s', trialPulseWidthSec, "
            + "'taskName', 'Opto Photostim Trial DO', "
            + "'taskVarName', 'optoPhotostimTrialDoTask', "
            + f"'startTriggerEdge', {matlab_string(path_config.trial_waveform_start_trigger_edge)}, "
            + "'autoStart', false);",
            "disp('TRIAL_WAVEFORM_READY');",
        ]
    )


def build_start_trial_waveform_command(path_config: PathConfig) -> str:
    return "\n".join(
        [
            build_global_preamble(path_config),
            "assert(evalin('base', 'exist(''optoPhotostimTrialDoTask'',''var'')'), 'Prepared trial waveform task was not found.');",
            "do_task = evalin('base', 'optoPhotostimTrialDoTask');",
            "assert(most.idioms.isValidObj(do_task), 'Prepared trial waveform task is invalid.');",
            "disp('Starting waveform playback to advance remaining stimulus groups');",
            "do_task.start();",
            "disp('TRIAL_WAVEFORM_STARTED');",
        ]
    )


def build_arm_trial_waveform_command(path_config: PathConfig) -> str:
    return "\n".join(
        [
            build_global_preamble(path_config),
            "assert(evalin('base', 'exist(''optoPhotostimTrialDoTask'',''var'')'), 'Prepared trial waveform task was not found.');",
            "do_task = evalin('base', 'optoPhotostimTrialDoTask');",
            "assert(most.idioms.isValidObj(do_task), 'Prepared trial waveform task is invalid.');",
            "disp('Arming waveform to advance stimulus groups from external start');",
            "do_task.start();",
            "disp('TRIAL_WAVEFORM_ARMED');",
        ]
    )


def build_trial_waveform_status_command(path_config: PathConfig) -> str:
    return "\n".join(
        [
            build_global_preamble(path_config),
            "do_task_exists = evalin('base', 'exist(''optoPhotostimTrialDoTask'',''var'')');",
            "if do_task_exists; do_task = evalin('base', 'optoPhotostimTrialDoTask'); else; do_task = []; end",
            "disp('TRIAL_WAVEFORM_TASK_ACTIVE');",
            "if most.idioms.isValidObj(do_task); disp(double(do_task.active)); else; disp(0); end",
            "disp('TRIAL_WAVEFORM_TASK_DONE');",
            "if most.idioms.isValidObj(do_task); disp(double(~do_task.active)); else; disp(1); end",
            "disp('TRIAL_WAVEFORM_STATUS_READY');",
        ]
    )


def build_stop_trial_waveform_command(path_config: PathConfig) -> str:
    return "\n".join(
        [
            build_global_preamble(path_config),
            "if evalin('base', 'exist(''optoPhotostimTrialDoTask'',''var'')');",
            "    do_task = evalin('base', 'optoPhotostimTrialDoTask');",
            "    if most.idioms.isValidObj(do_task);",
            "        try; do_task.abort(); catch; end",
            "        try; delete(do_task); catch; end",
            "    end",
            "    evalin('base', 'clear optoPhotostimTrialDoTask');",
            "end",
            "disp('TRIAL_WAVEFORM_STOPPED');",
        ]
    )


def build_test_stim_waveform_command(
    path_config: PathConfig,
    pulse_times_s: list[float],
    pulse_width_s: float,
) -> str:
    hsi = path_config.hsi_variable
    pulse_times_expr = "[" + " ".join(repr(float(v)) for v in pulse_times_s) + "]"
    total_duration_s = (max(pulse_times_s) if pulse_times_s else 0.0) + pulse_width_s + 0.1
    return "\n".join(
        [
            build_global_preamble(path_config),
            "disp('----------');",
            "disp('Test stim waveform');",
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "disp('Photostim trigger term before test:');",
            "disp(string(hPs.stimTriggerTerm));",
            "disp('Configured waveform output port:');",
            f"disp({matlab_string(path_config.trial_waveform_output_port)});",
            "disp('Configured photostim trigger term:');",
            f"disp({matlab_string(path_config.trial_waveform_photostim_trigger_term)});",
            "disp('Photostim active before test:');",
            "disp(double(hPs.active));",
            "disp('Photostim mode before test:');",
            "disp(string(hPs.stimulusMode));",
            "disp('Photostim sequence position before test:');",
            "if isempty(hPs.sequencePosition); disp('NaN'); else; disp(double(hPs.sequencePosition)); end",
            "disp('Photostim completed sequences before test:');",
            "if isempty(hPs.completedSequences); disp('NaN'); else; disp(double(hPs.completedSequences)); end",
            "disp('Photostim selected sequence before test:');",
            "disp(hPs.sequenceSelectedStimuli);",
            "sequencePositionBefore = [];",
            "completedSequencesBefore = [];",
            "if ~isempty(hPs.sequencePosition); sequencePositionBefore = double(hPs.sequencePosition); end",
            "if ~isempty(hPs.completedSequences); completedSequencesBefore = double(hPs.completedSequences); end",
            "disp(['Photostim sequence position summary before test: ' most.idioms.ifthenelse(isempty(sequencePositionBefore), 'NaN', num2str(sequencePositionBefore))]);",
            "disp('Waveform task pulse width sec:');",
            f"disp({pulse_width_s!r});",
            "disp('Waveform task total duration sec:');",
            f"disp({total_duration_s!r});",
            "disp('Waveform trigger times sec:');",
            f"disp({pulse_times_expr});",
            f"do_task = opto.scanimage.testVdaqDoTriggeredByDi('outputLine', {matlab_string(path_config.trial_waveform_output_port.split('/')[-1])}, 'startTrigger', '', 'sampleRate_Hz', {path_config.trial_waveform_sample_rate_hz!r}, 'pulseTimes_s', {pulse_times_expr}, 'pulseWidth_s', {pulse_width_s!r});",
            "t0 = tic;",
            f"while most.idioms.isValidObj(do_task) && double(do_task.active) && toc(t0) < {max(2.0, total_duration_s + 1.0)!r}; pause(0.01); end",
            "disp('Raw DoTask active after train:');",
            "if most.idioms.isValidObj(do_task); disp(double(do_task.active)); else; disp(0); end",
            "pause(0.2);",
            "disp('Photostim sequence position after train:');",
            "if isempty(hPs.sequencePosition); disp('NaN'); else; disp(double(hPs.sequencePosition)); end",
            "disp('Photostim completed sequences after train:');",
            "if isempty(hPs.completedSequences); disp('NaN'); else; disp(double(hPs.completedSequences)); end",
            "deliveredCount = 0;",
            "if ~isempty(sequencePositionBefore) && ~isempty(hPs.sequencePosition); deliveredCount = max(0, double(hPs.sequencePosition) - sequencePositionBefore); end",
            "if deliveredCount == 0 && ~isempty(completedSequencesBefore) && ~isempty(hPs.completedSequences) && double(hPs.completedSequences) > completedSequencesBefore; deliveredCount = 1; end",
            "sequencePositionAfter = [];",
            "if ~isempty(hPs.sequencePosition); sequencePositionAfter = double(hPs.sequencePosition); end",
            "disp(['Photostim sequence position summary after train: ' most.idioms.ifthenelse(isempty(sequencePositionAfter), 'NaN', num2str(sequencePositionAfter))]);",
            "disp(['Photostim sequence position delta: ' most.idioms.ifthenelse(isempty(sequencePositionBefore) || isempty(sequencePositionAfter), 'NaN', num2str(sequencePositionAfter - sequencePositionBefore))]);",
            "disp('Stim sequence advanced count:');",
            "disp(deliveredCount);",
            "disp('Self-contained waveform diagnostic complete');",
        ]
    )


def build_test_stim_waveform_external_start_command_configurable(
    path_config: PathConfig,
    pulse_times_s: list[float],
    pulse_width_s: float,
) -> str:
    hsi = path_config.hsi_variable
    pulse_times_expr = "[" + " ".join(repr(float(v)) for v in pulse_times_s) + "]"
    total_duration_s = (max(pulse_times_s) if pulse_times_s else 0.0) + pulse_width_s + 0.1
    return "\n".join(
        [
            build_global_preamble(path_config),
            "disp('----------');",
            "disp('Test stim waveform external start');",
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "disp('Configured waveform output port:');",
            f"disp({matlab_string(path_config.trial_waveform_output_port)});",
            "disp('Configured waveform start trigger port:');",
            f"disp({matlab_string(path_config.trial_waveform_start_trigger_port)});",
            "disp('Configured photostim trigger term:');",
            f"disp({matlab_string(path_config.trial_waveform_photostim_trigger_term)});",
            "disp('Photostim active before test:');",
            "disp(double(hPs.active));",
            "disp('Photostim sequence position before test:');",
            "if isempty(hPs.sequencePosition); disp('NaN'); else; disp(double(hPs.sequencePosition)); end",
            "disp('Photostim completed sequences before test:');",
            "if isempty(hPs.completedSequences); disp('NaN'); else; disp(double(hPs.completedSequences)); end",
            "sequencePositionBefore = [];",
            "if ~isempty(hPs.sequencePosition); sequencePositionBefore = double(hPs.sequencePosition); end",
            "disp(['Photostim sequence position summary before test: ' most.idioms.ifthenelse(isempty(sequencePositionBefore), 'NaN', num2str(sequencePositionBefore))]);",
            "disp('Waveform task pulse width sec:');",
            f"disp({pulse_width_s!r});",
            "disp('Waveform task total duration sec:');",
            f"disp({total_duration_s!r});",
            "disp('Waveform external-start trigger times sec:');",
            f"disp({pulse_times_expr});",
            f"do_task = opto.scanimage.testVdaqDoTriggeredByDi('outputLine', {matlab_string(path_config.trial_waveform_output_port.split('/')[-1])}, 'startTrigger', {matlab_string(path_config.trial_waveform_start_trigger_port.split('/')[-1])}, 'sampleRate_Hz', {path_config.trial_waveform_sample_rate_hz!r}, 'pulseTimes_s', {pulse_times_expr}, 'pulseWidth_s', {pulse_width_s!r});",
            "disp('TRIAL_WAVEFORM_READY_FOR_EXTERNAL_START');",
        ]
    )


def build_raw_vdaq_do_test_status_command() -> str:
    return "\n".join(
        [
            "disp('RAW_VDAQ_DO_TEST_ACTIVE');",
            "if evalin('base', 'exist(''optoPhotostimDebugDoTask'',''var'')');",
            "    do_task = evalin('base', 'optoPhotostimDebugDoTask');",
            "    if most.idioms.isValidObj(do_task); disp(double(do_task.active)); else; disp(0); end",
            "else; disp(0); end",
            "disp('RAW_VDAQ_DO_TEST_DONE');",
            "if evalin('base', 'exist(''optoPhotostimDebugDoTask'',''var'')');",
            "    do_task = evalin('base', 'optoPhotostimDebugDoTask');",
            "    if most.idioms.isValidObj(do_task); disp(double(~do_task.active)); else; disp(1); end",
            "else; disp(1); end",
            "disp('RAW_VDAQ_DO_TEST_STATUS_READY');",
        ]
    )


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
    idx = -1
    for marker in ("importSchemaPatterns(", "prepareSchemaPhotostim("):
        idx = command.find(marker)
        if idx >= 0:
            break
    if idx < 0:
        return None
    quoted_start = command.find("'", idx)
    if quoted_start < 0:
        return None
    quoted_end = command.find("'", quoted_start + 1)
    if quoted_end < 0:
        return None
    return command[quoted_start + 1 : quoted_end].replace("''", "'")


def _extract_numeric_vector_assignment(command: str, var_name: str) -> list[int]:
    marker = f"{var_name} = ["
    start = command.find(marker)
    if start == -1:
        return []
    start += len(marker)
    end = command.find("]", start)
    if end == -1:
        return []
    raw = command[start:end].strip()
    if not raw:
        return []
    values: list[int] = []
    for token in raw.replace(",", " ").split():
        try:
            values.append(int(float(token)))
        except ValueError:
            continue
    return values


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
