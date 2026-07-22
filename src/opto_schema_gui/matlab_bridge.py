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
from typing import Any, Callable

import yaml

try:
    import matlab.engine as matlab_engine
except ModuleNotFoundError:
    matlab_engine = None


MACHINE_CONFIG_FILENAME = "machine.ini"
CONFIG_REGISTRY_KEY = r"Software\2POptoTools"
CONFIG_REGISTRY_VALUE = "ConfigRoot"
CONFIG_ROOT_FILE = Path(".config") / "2p_opto_tools" / "config_root.txt"


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
    min_center_distance_um: float
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


class MatlabSessionCancelled(MatlabSessionError):
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
        self._launch_output_lines: list[str] = []
        self._launch_output_lock = threading.Lock()
        self.sim_sequence: list[int] = []
        self.sim_sequence_position = 1

    def start(
        self,
        startup_command: str | None = None,
        *,
        status_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if self.engine is not None or self.simulated:
            return

        if self.force_simulated:
            if status_callback is not None:
                status_callback(f"Starting simulated MATLAB session for config {self.config.config_name}")
            self._start_simulated()
            self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
            return

        if matlab_engine is None:
            raise MatlabSessionError(
                "matlab.engine is not installed in this Python environment. "
                "Install the MATLAB Engine for Python to run live ScanImage control."
            )

        if status_callback is not None:
            status_callback(
                f"Waiting for shared MATLAB engine '{self.config.engine_name}'"
            )
        connected = self._try_connect_existing(status_callback=status_callback, cancel_event=cancel_event)
        if connected:
            self.attached = True
            self.started_with_launch = False
            try:
                if status_callback is not None:
                    status_callback("Validating ScanImage hSI in the connected MATLAB session")
                self._validate_scanimage_started(status_callback=status_callback)
                if status_callback is not None:
                    status_callback("ScanImage hSI validated; changing MATLAB working directory")
                self._set_working_directory(status_callback=status_callback)
                if status_callback is not None:
                    status_callback("MATLAB session and ScanImage are ready")
                return
            except Exception as exc:
                if status_callback is not None:
                    status_callback(f"Existing-session startup check failed: {type(exc).__name__}: {exc}")
                self.engine = None
                self.attached = False

        self.attached = False
        self.started_with_launch = bool(startup_command and "run('launch.m')" in startup_command)
        self._launch_external_and_connect(
            startup_command,
            status_callback=status_callback,
            cancel_event=cancel_event,
        )

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

    def _try_connect_existing(
        self,
        *,
        status_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        assert matlab_engine is not None
        engine_name = self.config.engine_name
        try:
            available = matlab_engine.find_matlab()
        except Exception:
            available = ()
        if engine_name not in available:
            return False
        try:
            if status_callback is not None:
                status_callback(f"Connecting to existing MATLAB session '{engine_name}'")
            self.engine = self._connect_matlab_with_timeout(
                engine_name,
                timeout_s=5.0,
                cancel_event=cancel_event,
            )
        except Exception:
            self.engine = None
            return False
        return True

    @staticmethod
    def _connect_matlab_with_timeout(
        engine_name: str,
        timeout_s: float,
        cancel_event: threading.Event | None = None,
    ):
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
        if not MatlabSession._wait_for_event(done, timeout_s, cancel_event):
            if cancel_event is not None and cancel_event.is_set():
                raise MatlabSessionCancelled(
                    f"Cancelled while connecting to shared MATLAB engine '{engine_name}'."
                )
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

    def _validate_scanimage_started(
        self,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        if status_callback is not None:
            status_callback("Starting MATLAB hSI readiness command")
        lines = self.eval(
            "\n".join(
                [
                    build_global_preamble(self.config),
                    f"assert(exist({matlab_string(self.config.hsi_variable)}, 'var') == 1, 'Missing {self.config.hsi_variable} in MATLAB workspace.');",
                    f"assert(~isempty({self.config.hsi_variable}), '{self.config.hsi_variable} is empty.');",
                    "disp('ScanImage hSI detected');",
                ]
            ),
            timeout_s=self.config.command_timeout_s,
        )
        if status_callback is not None:
            status_callback("MATLAB hSI readiness command returned successfully")
        if not lines:
            return

    @staticmethod
    def _wait_for_event(
        done: threading.Event,
        timeout_s: float,
        cancel_event: threading.Event | None,
        poll_interval_s: float = 0.1,
    ) -> bool:
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return done.is_set()
            if cancel_event is not None and cancel_event.is_set():
                return False
            if done.wait(min(poll_interval_s, remaining)):
                return True

    def _launch_external_and_connect(
        self,
        startup_command: str | None,
        *,
        status_callback: Callable[[str], None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> None:
        assert matlab_engine is not None
        matlab_cmd = [self.config.matlab_executable, *self.config.matlab_flags]
        startup = self._build_startup_command(startup_command)
        matlab_cmd.extend(["-r", startup])
        if status_callback is not None:
            status_callback(
                f"MATLAB is launching; waiting for shared engine '{self.config.engine_name}'"
            )
        try:
            self.launch_process = subprocess.Popen(
                matlab_cmd,
                cwd=str(self.config.directory),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except Exception as exc:
            raise MatlabSessionError(
                f"Could not launch MATLAB process for path '{self.config.name}': {exc}"
            ) from exc

        self._launch_output_lines = []
        if self.launch_process.stdout is not None:
            threading.Thread(target=self._capture_launch_output, daemon=True).start()

        deadline = time.monotonic() + self.config.startup_timeout_s
        last_error = None
        process_exit_code: int | None = None
        while time.monotonic() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise MatlabSessionCancelled(
                    f"Cancelled while waiting for MATLAB session '{self.config.engine_name}' "
                    f"for path '{self.config.name}'."
                )
            if self.launch_process is not None:
                return_code = self.launch_process.poll()
                if return_code is not None:
                    process_exit_code = return_code
            try:
                available = matlab_engine.find_matlab()
            except Exception as exc:
                last_error = exc
                available = ()
            if self.config.engine_name in available:
                try:
                    if status_callback is not None:
                        status_callback(
                            f"MATLAB engine found; connecting and waiting for ScanImage hSI"
                        )
                    self.engine = self._connect_matlab_with_timeout(
                        self.config.engine_name,
                        timeout_s=5.0,
                        cancel_event=cancel_event,
                    )
                    self._validate_scanimage_started(status_callback=status_callback)
                    if status_callback is not None:
                        status_callback("ScanImage hSI validated; changing MATLAB working directory")
                    self._set_working_directory(status_callback=status_callback)
                    if status_callback is not None:
                        status_callback("MATLAB session and ScanImage are ready")
                    return
                except Exception as exc:
                    last_error = exc
                    if status_callback is not None:
                        status_callback(
                            f"ScanImage startup check failed; retrying: {type(exc).__name__}: {exc}"
                        )
            elif status_callback is not None:
                status_callback(
                    f"Waiting for MATLAB session '{self.config.engine_name}' for config {self.config.config_name}"
                )
            time.sleep(1.0)
        detail = (
            f"Timed out waiting for ScanImage hSI in shared MATLAB engine '{self.config.engine_name}' "
            f"for path '{self.config.name}'."
        )
        if process_exit_code is not None:
            detail += f" MATLAB launcher process exited with code {process_exit_code}."
        if last_error is not None:
            detail += f" Last error: {last_error}"
        startup_output = self._format_launch_output()
        if startup_output:
            detail += f"\nMATLAB startup output:\n{startup_output}"
        raise MatlabSessionError(detail)

    def _capture_launch_output(self) -> None:
        if self.launch_process is None or self.launch_process.stdout is None:
            return
        try:
            for raw_line in self.launch_process.stdout:
                line = raw_line.rstrip()
                if not line:
                    continue
                with self._launch_output_lock:
                    self._launch_output_lines.append(line)
                    if len(self._launch_output_lines) > 200:
                        self._launch_output_lines = self._launch_output_lines[-200:]
        except Exception:
            return

    def _format_launch_output(self) -> str:
        with self._launch_output_lock:
            return "\n".join(self._launch_output_lines).strip()

    def _build_startup_command(self, startup_command: str | None) -> str:
        commands: list[str] = [
            f"matlab.engine.shareEngine({matlab_string(self.config.engine_name)})"
        ]
        if startup_command:
            commands.append(startup_command)
        else:
            commands.append(f"addpath(genpath({matlab_string(str(self.config.repo_matlab_path))}))")
        body = "; ".join(commands)
        return (
            "try; "
            + body
            + "; catch ME; disp(getReport(ME,'extended')); end"
        )

    def _set_working_directory(
        self,
        *,
        status_callback: Callable[[str], None] | None = None,
    ) -> None:
        if self.simulated:
            self.current_directory = str(self.config.directory)
            return
        if self.engine is None:
            return
        target_dir = str(self.config.directory)
        if status_callback is not None:
            status_callback(f"Changing MATLAB working directory to {target_dir}")
        self.eval(f"cd({matlab_string(target_dir)})", timeout_s=self.config.command_timeout_s)
        self.current_directory = target_dir
        if status_callback is not None:
            status_callback("MATLAB working directory changed")

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


def _portable_config_root_file() -> Path:
    return Path.home() / CONFIG_ROOT_FILE


def get_config_root_setting() -> Path | None:
    """Return the configured machine config root, if it exists and is usable."""
    raw_value = ""
    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONFIG_REGISTRY_KEY) as key:
                raw_value, _ = winreg.QueryValueEx(key, CONFIG_REGISTRY_VALUE)
        except (FileNotFoundError, OSError):
            raw_value = ""
    else:
        try:
            raw_value = _portable_config_root_file().read_text(encoding="utf-8").strip()
        except OSError:
            raw_value = ""

    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    candidate = Path(raw_value).expanduser().resolve()
    return candidate if candidate.is_dir() else None


def set_config_root_setting(config_root: str | Path) -> Path:
    """Persist and return the selected machine config root."""
    root = Path(config_root).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Configuration folder does not exist: {root}")
    if not is_config_root(root):
        raise ValueError(f"Configuration folder contains no usable machine configs: {root}")

    if os.name == "nt":
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, CONFIG_REGISTRY_KEY) as key:
            winreg.SetValueEx(key, CONFIG_REGISTRY_VALUE, 0, winreg.REG_SZ, str(root))
    else:
        setting_file = _portable_config_root_file()
        setting_file.parent.mkdir(parents=True, exist_ok=True)
        setting_file.write_text(str(root), encoding="utf-8")
    return root


def is_config_root(config_root: str | Path) -> bool:
    """Return whether a folder contains at least one usable machine config folder."""
    root = Path(config_root).expanduser()
    if not root.is_dir():
        return False
    try:
        machine_dirs = (path for path in root.iterdir() if path.is_dir())
        return any(
            (machine_dir / MACHINE_CONFIG_FILENAME).is_file()
            or any((config_dir / "config.ini").is_file() for config_dir in machine_dir.iterdir() if config_dir.is_dir())
            for machine_dir in machine_dirs
        )
    except OSError:
        return False


def configs_root(repo_root: str | Path) -> Path | None:
    """Return the registered external config root, or None until one is selected."""
    del repo_root  # Kept in the API for callers that also use repo-relative paths.
    return get_config_root_setting()


def list_machine_names(repo_root: str | Path) -> list[str]:
    root = configs_root(repo_root)
    if root is None or not root.exists():
        return []
    return sorted(path.name for path in root.iterdir() if path.is_dir())


def list_config_names(repo_root: str | Path, machine_name: str) -> list[str]:
    root = configs_root(repo_root)
    if root is None:
        return []
    machine_dir = root / machine_name
    if not machine_dir.exists():
        return []
    return sorted(path.name for path in machine_dir.iterdir() if path.is_dir())


def get_machine_default_config_name(repo_root: str | Path, machine_name: str) -> str | None:
    return load_machine_ui_config(repo_root, machine_name).default_config


def load_machine_ui_config(repo_root: str | Path, machine_name: str) -> MachineUiConfig:
    root = configs_root(repo_root)
    if root is None:
        return MachineUiConfig(default_config=None, screen_index=None, start_maximized=True)
    machine_dir = root / machine_name
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
    root = configs_root(repo_root)
    if root is None:
        raise FileNotFoundError("No external configuration folder has been selected")
    config_dir = root / machine_name / config_name
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
            min_center_distance_um=_get_float(section, None, "min_center_distance_um", 15.0),
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
            f"    MinCenterDistanceUm={path_config.min_center_distance_um:.12g}, ...",
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
    seq_num: int,
    trial_seq_nums: list[int],
    schema_var_name: str = "schemaData",
    *,
    configure_sequence: bool = True,
    start_photostim: bool = True,
    pre_stim_pause_duration: float = 0.001,
    blank_duration: float = 0.001,
    park_duration: float = 0.001,
    block_duration: float | None = None,
    prefix_blank_to_sequence: bool = False,
    embed_blank_and_park_in_stim_group: bool = False,
    single_epoch_pattern: bool = False,
    num_sequences: float = 1.0,
) -> str:
    trial_seq_nums_expr = "[]" if not trial_seq_nums else "[" + " ".join(str(int(v)) for v in trial_seq_nums) + "]"
    resolved_block_duration = path_config.sequence_block_duration_s if block_duration is None else float(block_duration)
    resolved_num_sequences = "Inf" if math.isinf(float(num_sequences)) else f"{float(num_sequences):.12g}"
    lines = [
        build_global_preamble(path_config),
        f"[importedPatternNames, importedPatternNumbers] = opto.scanimage.prepareSchemaPhotostim({path_config.hsi_variable}, {schema_var_name}, ...",
        f"    SequenceIndex={seq_num}, ...",
        f"    TrialSequenceIndices={trial_seq_nums_expr}, ...",
        f"    PreStimPauseDuration={float(pre_stim_pause_duration):.12g}, ...",
        f"    BlankDuration={float(blank_duration):.12g}, ...",
        f"    ParkDuration={float(park_duration):.12g}, ...",
        f"    BlockDuration={resolved_block_duration:.12g}, ...",
        f"    TriggerTerm={matlab_string(path_config.trial_waveform_photostim_trigger_term)}, ...",
        f"    ConfigureSequence={'true' if configure_sequence else 'false'}, ...",
        f"    StartPhotostim={'true' if start_photostim else 'false'}, ...",
        f"    PrefixBlankToSequence={'true' if prefix_blank_to_sequence else 'false'}, ...",
        f"    EmbedBlankAndParkInStimGroup={'true' if embed_blank_and_park_in_stim_group else 'false'}, ...",
        f"    SingleEpochPattern={'true' if single_epoch_pattern else 'false'}, ...",
        f"    NumSequences={resolved_num_sequences}, ...",
        f"    MinCenterDistanceUm={path_config.min_center_distance_um:.12g}, ...",
        "    Revolutions=5);",
        "disp('Prepared schema photostim patterns used by sequence groups:');",
        "disp(importedPatternNames);",
        "disp(importedPatternNumbers);",
    ]
    return "\n".join(lines)


def build_begin_slm_psf_diagnostic_command(
    path_config: PathConfig,
    *,
    pixels_per_line: int,
    lines_per_frame: int,
    num_slices: int,
    frames_per_slice: int,
    z_step_um: float,
    log_average_factor: int,
    display_average_factor: int,
) -> str:
    hsi = path_config.hsi_variable
    lines = [
        build_global_preamble(path_config),
        f"hSI = {hsi};",
        "assert(~isempty(hSI), 'ScanImage handle is not available.');",
        "assert(~hSI.active, 'Stop imaging before starting SLM PSF diagnostics.');",
        "hPs = hSI.hPhotostim;",
        "assert(~hPs.active, 'Stop photostim before starting SLM PSF diagnostics.');",
        "backup = struct();",
        "backup.valid = true;",
        "backup.motorZ = NaN;",
        "try; backup.motorZ = double(hSI.hMotors.samplePosition(3)); catch; end",
        "backup.logFilePath = '';",
        "backup.logFileStem = '';",
        "backup.logFramesPerFile = [];",
        "backup.logAverageFactor = [];",
        "backup.displayRollingAverageFactor = [];",
        "backup.displayRollingAverageFactorLock = [];",
        "backup.loggingEnable = [];",
        "backup.channelSave = [];",
        "backup.motionManagerEnable = [];",
        "try; backup.logFilePath = hSI.hScan2D.logFilePath; catch; end",
        "try; backup.logFileStem = hSI.hScan2D.logFileStem; catch; end",
        "try; backup.logFramesPerFile = hSI.hScan2D.logFramesPerFile; catch; end",
        "try; backup.logAverageFactor = hSI.hScan2D.logAverageFactor; catch; end",
        "try; backup.displayRollingAverageFactor = hSI.hDisplay.displayRollingAverageFactor; catch; end",
        "try; backup.displayRollingAverageFactorLock = hSI.hDisplay.displayRollingAverageFactorLock; catch; end",
        "try; backup.loggingEnable = hSI.hChannels.loggingEnable; catch; end",
        "try; backup.channelSave = hSI.hChannels.channelSave; catch; end",
        "try; backup.motionManagerEnable = logical(hSI.hMotionManager.enable); catch; end",
        "backup.stackEnable = [];",
        "backup.stackMode = '';",
        "backup.stackDefinition = '';",
        "backup.stackActuator = '';",
        "backup.centeredStack = [];",
        "backup.stackZStepSize = [];",
        "backup.numSlices = [];",
        "backup.framesPerSlice = [];",
        "backup.arbitraryZs = [];",
        "backup.arbitraryZsAreSampleRelative = [];",
        "try; backup.stackEnable = hSI.hStackManager.enable; catch; end",
        "try; backup.stackMode = char(string(hSI.hStackManager.stackMode)); catch; end",
        "try; backup.stackDefinition = char(string(hSI.hStackManager.stackDefinition)); catch; end",
        "try; backup.stackActuator = char(string(hSI.hStackManager.stackActuator)); catch; end",
        "try; backup.centeredStack = logical(hSI.hStackManager.centeredStack); catch; end",
        "try; backup.stackZStepSize = double(hSI.hStackManager.stackZStepSize); catch; end",
        "try; backup.numSlices = hSI.hStackManager.numSlices; catch; end",
        "try; backup.framesPerSlice = hSI.hStackManager.framesPerSlice; catch; end",
        "try; backup.arbitraryZs = double(hSI.hStackManager.arbitraryZs(:)); catch; end",
        "try; backup.arbitraryZsAreSampleRelative = logical(hSI.hStackManager.arbitraryZsAreSampleRelative); catch; end",
        "backup.mroiEnable = [];",
        "backup.scanZoomFactor = [];",
        "backup.roiGroup = [];",
        "try; backup.mroiEnable = hSI.hRoiManager.mroiEnable; catch; end",
        "try; backup.scanZoomFactor = hSI.hRoiManager.scanZoomFactor; catch; end",
        "try;",
        "    rg = hSI.hRoiManager.roiGroupMroi;",
        "    if ~isempty(rg) && most.idioms.isValidObj(rg); backup.roiGroup = rg.copy(); end",
        "catch; end",
        "backup.photostimStimulusMode = '';",
        "backup.photostimSequenceSelectedStimuli = [];",
        "backup.photostimNumSequences = [];",
        "backup.photostimStimImmediately = [];",
        "backup.photostimMonitoring = [];",
        "backup.photostimLogging = [];",
        "backup.photostimStimTriggerTerm = [];",
        "backup.photostimGroups = [];",
        "try; backup.photostimStimulusMode = char(string(hPs.stimulusMode)); catch; end",
        "try; backup.photostimSequenceSelectedStimuli = double(hPs.sequenceSelectedStimuli(:).'); catch; end",
        "try; backup.photostimNumSequences = hPs.numSequences; catch; end",
        "try; backup.photostimStimImmediately = hPs.stimImmediately; catch; end",
        "try; backup.photostimMonitoring = hPs.monitoring; catch; end",
        "try; backup.photostimLogging = hPs.logging; catch; end",
        "try; backup.photostimStimTriggerTerm = hPs.stimTriggerTerm; catch; end",
        "try; backup.photostimGroups = hPs.stimRoiGroups; catch; end",
        "assignin('base', 'optoSlmPsfBackup', backup);",
        "if isempty(backup.roiGroup) || ~most.idioms.isValidObj(backup.roiGroup)",
        "    rgSource = hSI.hRoiManager.currentRoiGroup;",
        "else",
        "    rgSource = backup.roiGroup;",
        "end",
        "assert(~isempty(rgSource) && most.idioms.isValidObj(rgSource), 'Current ROI group is not available.');",
        "rgOut = rgSource.copy();",
        f"pixRes = [{int(pixels_per_line)} {int(lines_per_frame)}];",
        "for iRoi = 1:numel(rgOut.rois)",
        "    roi = rgOut.rois(iRoi);",
        "    for iSf = 1:numel(roi.scanfields)",
        "        sf = roi.scanfields(iSf);",
        "        if isprop(sf, 'pixelResolutionXY')",
        "            sf.pixelResolutionXY = pixRes;",
        "        end",
        "    end",
        "end",
        "hSI.hRoiManager.roiGroupMroi = rgOut;",
        "hSI.hRoiManager.mroiEnable = true;",
        "hSI.hStackManager.enable = true;",
        "hSI.hStackManager.stackMode = 'slow';",
        "hSI.hStackManager.stackActuator = 'motor';",
        "hSI.hStackManager.stackDefinition = 'uniform';",
        "hSI.hStackManager.centeredStack = true;",
        f"hSI.hStackManager.stackZStepSize = {float(z_step_um)!r};",
        f"hSI.hStackManager.numSlices = {int(num_slices)};",
        f"hSI.hStackManager.framesPerSlice = {int(frames_per_slice)};",
        f"hSI.hScan2D.logAverageFactor = {int(log_average_factor)};",
        f"hSI.hDisplay.displayRollingAverageFactor = {int(display_average_factor)};",
        "hSI.hDisplay.displayRollingAverageFactorLock = true;",
        "hSI.hChannels.loggingEnable = true;",
        "try; hSI.hMotionManager.enable = false; catch; end",
        f"hSI.hScan2D.logFramesPerFile = max(1, {int(num_slices)});",
        "disp('SLM_PSF_BEGIN_READY');",
        f"disp({float(z_step_um)!r});",
    ]
    return "\n".join(lines)


def build_run_slm_psf_volume_command(
    path_config: PathConfig,
    *,
    x_um: float,
    y_um: float,
    z_um: float,
    volume_dir: str,
    pixels_per_line: int,
    lines_per_frame: int,
    num_slices: int,
    frames_per_slice: int,
    z_step_um: float,
    sequence_duration_s: float,
    spiral_width_um: float,
    spiral_height_um: float,
    power_values: list[float],
    revolutions: float = 5.0,
) -> str:
    hsi = path_config.hsi_variable
    power_row = " ".join(repr(float(value)) for value in power_values)
    lines = [
        build_global_preamble(path_config),
        f"hSI = {hsi};",
        "backup = evalin('base', 'optoSlmPsfBackup');",
        "assert(isstruct(backup) && isfield(backup, 'valid') && backup.valid, 'SLM PSF diagnostics were not initialized.');",
        "assert(~hSI.active, 'ScanImage imaging is already active.');",
        "hPs = hSI.hPhotostim;",
        "if hPs.active; hPs.abort(); pause(0.1); end",
        f"volumeDir = {matlab_string(volume_dir)};",
        "if exist(volumeDir, 'dir') ~= 7; mkdir(volumeDir); end",
        "hSI.hChannels.loggingEnable = true;",
        "try; hSI.hMotionManager.enable = false; catch; end",
        f"pixRes = [{int(pixels_per_line)} {int(lines_per_frame)}];",
        "rgOut = hSI.hRoiManager.roiGroupMroi.copy();",
        "for iRoi = 1:numel(rgOut.rois)",
        "    roi = rgOut.rois(iRoi);",
        "    for iSf = 1:numel(roi.scanfields)",
        "        sfIm = roi.scanfields(iSf);",
        "        if isprop(sfIm, 'pixelResolutionXY')",
        "            sfIm.pixelResolutionXY = pixRes;",
        "        end",
        "    end",
        "end",
        "hSI.hRoiManager.roiGroupMroi = rgOut;",
        "hSI.hRoiManager.mroiEnable = true;",
        "hSI.hStackManager.enable = true;",
        "hSI.hStackManager.stackMode = 'slow';",
        "hSI.hStackManager.stackActuator = 'motor';",
        "hSI.hStackManager.stackDefinition = 'uniform';",
        "hSI.hStackManager.centeredStack = true;",
        f"hSI.hStackManager.stackZStepSize = {float(z_step_um)!r};",
        f"hSI.hStackManager.numSlices = {int(num_slices)};",
        f"hSI.hStackManager.framesPerSlice = {int(frames_per_slice)};",
        f"hSI.hMotors.moveSample([NaN NaN {float(z_um)!r}]);",
        "resXY = hSI.objectiveResolution;",
        "if isscalar(resXY); resXY = [resXY resXY]; end",
        f"centerRef = [{float(x_um)!r} ./ resXY(1), {float(y_um)!r} ./ resXY(2)];",
        f"sizeRef = [{float(spiral_width_um)!r} ./ resXY(1), {float(spiral_height_um)!r} ./ resXY(2)];",
        "nBeams = 1;",
        "try; ss = hPs.stimScannerset; if most.idioms.isValidObj(ss); nBeams = numel(ss.beams); end; catch; nBeams = 1; end",
        "beamPowers = zeros(1, nBeams);",
        f"powerTemplate = [{power_row}];",
        "beamPowers(1:min(numel(powerTemplate), nBeams)) = powerTemplate(1:min(numel(powerTemplate), nBeams));",
        "hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0);",
        "hGroup = scanimage.mroi.RoiGroup('SLM_PSF');",
        "sf = scanimage.mroi.scanfield.fields.StimulusField();",
        "sf.centerXY = centerRef;",
        "sf.sizeXY = sizeRef;",
        f"sf.duration = {float(sequence_duration_s)!r};",
        "sf.repetitions = 1;",
        "sf.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral;",
        f"sf.stimparams = {{'revolutions', {float(revolutions)!r}, 'direction', 'outward'}};",
        f"sf.slmPattern = [centerRef {float(z_um)!r} 1];",
        "sf.powers = beamPowers;",
        "roi = scanimage.mroi.Roi();",
        "roi.add(0, sf);",
        "hGroup.add(roi);",
        "hPs.stimRoiGroups(end + 1) = hGroup;",
        "hPs.stimulusMode = 'sequence';",
        "hPs.sequenceSelectedStimuli = 1;",
        "hPs.numSequences = inf;",
        "if isprop(hPs, 'stimImmediately'); hPs.stimImmediately = false; end",
        "if isprop(hPs, 'monitoring'); hPs.monitoring = false; end",
        "if isprop(hPs, 'logging'); hPs.logging = false; end",
        "hPs.stimTriggerTerm = 'frame';",
        "disp('SLM_PSF_VOLUME_READY');",
        f"disp([{float(x_um)!r} {float(y_um)!r} {float(z_um)!r}]);",
        "hPs.start();",
        "t0 = tic();",
        "while ~hPs.active && toc(t0) < 5",
        "    pause(0.05);",
        "    drawnow;",
        "end",
        "assert(hPs.active, 'Photostim did not become active.');",
        "hSI.hScan2D.logFilePath = volumeDir;",
        "hSI.hScan2D.logFileStem = 'volume';",
        "hSI.startGrab();",
        "disp('SLM_PSF_VOLUME_STARTED');",
        f"disp([{float(x_um)!r} {float(y_um)!r} {float(z_um)!r}]);",
    ]
    return "\n".join(lines)


def build_check_slm_psf_volume_status_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    lines = [
        build_global_preamble(path_config),
        f"hSI = {hsi};",
        "hPs = hSI.hPhotostim;",
        "acqActive = false;",
        "psActive = false;",
        "try; acqActive = logical(hSI.active); catch; end",
        "try; psActive = logical(hPs.active); catch; end",
        "if ~acqActive && psActive",
        "    try; hPs.abort(); pause(0.05); catch; end",
        "    try; psActive = logical(hPs.active); catch; psActive = false; end",
        "end",
        "disp('SLM_PSF_STATUS_ACTIVE');",
        "disp(double(acqActive));",
        "disp('SLM_PSF_STATUS_PHOTOSTIM_ACTIVE');",
        "disp(double(psActive));",
    ]
    return "\n".join(lines)


def build_restore_slm_psf_diagnostic_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    lines = [
        build_global_preamble(path_config),
        f"hSI = {hsi};",
        "if evalin('base', \"exist('optoSlmPsfBackup', 'var')\")",
        "    backup = evalin('base', 'optoSlmPsfBackup');",
        "else",
        "    backup = struct('valid', false);",
        "end",
        "if ~isstruct(backup) || ~isfield(backup, 'valid') || ~backup.valid",
        "    disp('SLM_PSF_RESTORE_DONE');",
        "    return;",
        "end",
        "hPs = hSI.hPhotostim;",
        "try; if hSI.active; hSI.abort(); pause(0.1); end; catch; end",
        "try; if hPs.active; hPs.abort(); pause(0.1); end; catch; end",
        "try; if ischar(backup.logFilePath) || isstring(backup.logFilePath); hSI.hScan2D.logFilePath = char(backup.logFilePath); end; catch; end",
        "try; if ischar(backup.logFileStem) || isstring(backup.logFileStem); hSI.hScan2D.logFileStem = char(backup.logFileStem); end; catch; end",
        "try; if ~isempty(backup.logFramesPerFile); hSI.hScan2D.logFramesPerFile = backup.logFramesPerFile; end; catch; end",
        "try; if ~isempty(backup.logAverageFactor); hSI.hScan2D.logAverageFactor = backup.logAverageFactor; end; catch; end",
        "try; if ~isempty(backup.displayRollingAverageFactorLock); hSI.hDisplay.displayRollingAverageFactorLock = logical(backup.displayRollingAverageFactorLock); end; catch; end",
        "try; if ~isempty(backup.displayRollingAverageFactor); hSI.hDisplay.displayRollingAverageFactor = backup.displayRollingAverageFactor; end; catch; end",
        "try; if ~isempty(backup.loggingEnable); hSI.hChannels.loggingEnable = logical(backup.loggingEnable); end; catch; end",
        "try; if ~isempty(backup.channelSave); hSI.hChannels.channelSave = backup.channelSave; end; catch; end",
        "try; if ~isempty(backup.motionManagerEnable); hSI.hMotionManager.enable = logical(backup.motionManagerEnable); end; catch; end",
        "try; if ~isempty(backup.roiGroup) && most.idioms.isValidObj(backup.roiGroup); hSI.hRoiManager.roiGroupMroi = backup.roiGroup; end; catch; end",
        "try; if ~isempty(backup.mroiEnable); hSI.hRoiManager.mroiEnable = logical(backup.mroiEnable); end; catch; end",
        "try; if ~isempty(backup.scanZoomFactor); hSI.hRoiManager.scanZoomFactor = backup.scanZoomFactor; end; catch; end",
        "try; if ~isempty(backup.stackEnable); hSI.hStackManager.enable = logical(backup.stackEnable); end; catch; end",
        "try; if ischar(backup.stackMode) || isstring(backup.stackMode); if strlength(string(backup.stackMode)) > 0; hSI.hStackManager.stackMode = char(string(backup.stackMode)); end; end; catch; end",
        "try; if ischar(backup.stackDefinition) || isstring(backup.stackDefinition); if strlength(string(backup.stackDefinition)) > 0; hSI.hStackManager.stackDefinition = char(string(backup.stackDefinition)); end; end; catch; end",
        "try; if ischar(backup.stackActuator) || isstring(backup.stackActuator); if strlength(string(backup.stackActuator)) > 0; hSI.hStackManager.stackActuator = char(string(backup.stackActuator)); end; end; catch; end",
        "try; if ~isempty(backup.centeredStack); hSI.hStackManager.centeredStack = logical(backup.centeredStack); end; catch; end",
        "try; if ~isempty(backup.stackZStepSize); hSI.hStackManager.stackZStepSize = backup.stackZStepSize; end; catch; end",
        "try; if ~isempty(backup.numSlices); hSI.hStackManager.numSlices = backup.numSlices; end; catch; end",
        "try; if ~isempty(backup.framesPerSlice); hSI.hStackManager.framesPerSlice = backup.framesPerSlice; end; catch; end",
        "try; if ~isempty(backup.arbitraryZs); hSI.hStackManager.arbitraryZs = backup.arbitraryZs(:); end; catch; end",
        "try; if ~isempty(backup.arbitraryZsAreSampleRelative); hSI.hStackManager.arbitraryZsAreSampleRelative = logical(backup.arbitraryZsAreSampleRelative); end; catch; end",
        "try; if ~isempty(backup.motorZ) && isfinite(backup.motorZ); hSI.hMotors.moveSample([NaN NaN backup.motorZ]); end; catch; end",
        "try; if isfield(backup, 'photostimGroups'); hPs.stimRoiGroups = backup.photostimGroups; end; catch; end",
        "try; if isfield(backup, 'photostimStimulusMode') && strlength(string(backup.photostimStimulusMode)) > 0; hPs.stimulusMode = char(string(backup.photostimStimulusMode)); end; catch; end",
        "try; if isfield(backup, 'photostimSequenceSelectedStimuli'); hPs.sequenceSelectedStimuli = backup.photostimSequenceSelectedStimuli; end; catch; end",
        "try; if isfield(backup, 'photostimNumSequences') && ~isempty(backup.photostimNumSequences); hPs.numSequences = backup.photostimNumSequences; end; catch; end",
        "try; if isfield(backup, 'photostimStimImmediately') && ~isempty(backup.photostimStimImmediately); hPs.stimImmediately = logical(backup.photostimStimImmediately); end; catch; end",
        "try; if isfield(backup, 'photostimMonitoring') && ~isempty(backup.photostimMonitoring); hPs.monitoring = logical(backup.photostimMonitoring); end; catch; end",
        "try; if isfield(backup, 'photostimLogging') && ~isempty(backup.photostimLogging); hPs.logging = logical(backup.photostimLogging); end; catch; end",
        "try; if isfield(backup, 'photostimStimTriggerTerm'); hPs.stimTriggerTerm = backup.photostimStimTriggerTerm; end; catch; end",
        "evalin('base', 'clear optoSlmPsfBackup');",
        "disp('SLM_PSF_RESTORE_DONE');",
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
                "sf.slmPattern = [pointsRef(:,1:2), pointsRef(:,3:4)];",
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


def build_generate_photostim_grid_command(
    path_config: PathConfig,
    *,
    point_rows_um: list[list[float]],
    spiral_width_um: float,
    spiral_height_um: float,
    pause_duration_s: float,
    stim_duration_s: float,
    power_percent: float,
    revolutions: float = 5.0,
) -> str:
    hsi = path_config.hsi_variable
    points_um = _matlab_matrix(point_rows_um)
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"hSI = {hsi};",
            "assert(~isempty(hSI) && isprop(hSI, 'hPhotostim') && ~isempty(hSI.hPhotostim), 'ScanImage photostim handle is not available.');",
            "assert(~isempty(hSI.objectiveResolution), 'objectiveResolution is not set in ScanImage.');",
            "hPs = hSI.hPhotostim;",
            "if hPs.active; hPs.abort(); pause(0.1); end",
            "resXY = hSI.objectiveResolution;",
            "assert(isprop(hPs,'hasSlm') && hPs.hasSlm, 'No SLM available in the photostim configuration.');",
            "hPs.stimulusMode = 'sequence';",
            "hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0);",
            "hPs.sequenceSelectedStimuli = [];",
            "nBeams = 1;",
            "try; ss = hPs.stimScannerset; if most.idioms.isValidObj(ss); nBeams = numel(ss.beams); end; catch; nBeams = 1; end",
            "assert(nBeams >= 3, sprintf('Photostim expects at least 3 beams; only %d configured.', nBeams));",
            f"pointsUm = {points_um};",
            "assert(size(pointsUm,2) == 4 && size(pointsUm,1) >= 1, 'Photostim grid requires at least one XYZ point.');",
            "pointsRef = pointsUm;",
            "if isscalar(resXY); pointsRef(:,1:2) = pointsUm(:,1:2) ./ [resXY resXY]; else; pointsRef(:,1) = pointsUm(:,1) ./ resXY(1); pointsRef(:,2) = pointsUm(:,2) ./ resXY(2); end",
            "weights = pointsRef(:,4);",
            "weightSum = sum(weights);",
            "assert(weightSum > 0, 'Photostim grid weights must sum to a positive value.');",
            "centerRef = sum(pointsRef(:,1:2) .* weights, 1) ./ weightSum;",
            f"spiralWidthUm = {float(spiral_width_um):.12g};",
            f"spiralHeightUm = {float(spiral_height_um):.12g};",
            "if isscalar(resXY); sizeRef = [spiralWidthUm spiralHeightUm] ./ [resXY resXY]; else; sizeRef = [spiralWidthUm spiralHeightUm] ./ resXY(1:2); end",
            "hGroup = scanimage.mroi.RoiGroup('DIAGNOSTIC_PHOTOSTIM_GRID');",
            "sfPause = scanimage.mroi.scanfield.fields.StimulusField();",
            "sfPause.centerXY = [0 0];",
            "sfPause.sizeXY = [0 0];",
            "sfPause.stimfcnhdl = @scanimage.mroi.stimulusfunctions.pause;",
            "sfPause.stimparams = {'poweredPause', false};",
            f"sfPause.duration = {float(pause_duration_s):.12g};",
            "sfPause.repetitions = 1;",
            "sfPause.powers = zeros(1, nBeams);",
            "roiPause = scanimage.mroi.Roi();",
            "roiPause.add(0, sfPause);",
            "hGroup.add(roiPause);",
            "sf = scanimage.mroi.scanfield.fields.StimulusField();",
            "sf.centerXY = centerRef;",
            "sf.sizeXY = sizeRef;",
            f"sf.duration = {float(stim_duration_s):.12g};",
            "sf.repetitions = 1;",
            "sf.stimfcnhdl = @scanimage.mroi.stimulusfunctions.logspiral;",
            f"sf.stimparams = {{'revolutions', {float(revolutions):.12g}, 'direction', 'outward'}};",
            "sf.slmPattern = [pointsRef(:,1:2), pointsRef(:,3:4)];",
            "powers = zeros(1, nBeams);",
            f"powers(3) = {float(power_percent):.12g};",
            "sf.powers = powers;",
            "roiStim = scanimage.mroi.Roi();",
            "roiStim.add(0, sf);",
            "hGroup.add(roiStim);",
            "sfPark = scanimage.mroi.scanfield.fields.StimulusField();",
            "sfPark.centerXY = [0 0];",
            "sfPark.sizeXY = [0 0];",
            "sfPark.stimfcnhdl = @scanimage.mroi.stimulusfunctions.park;",
            "sfPark.stimparams = {};",
            f"sfPark.duration = {float(path_config.park_duration):.12g};",
            "sfPark.repetitions = 1;",
            "sfPark.powers = zeros(1, nBeams);",
            "roiPark = scanimage.mroi.Roi();",
            "roiPark.add(0, sfPark);",
            "hGroup.add(roiPark);",
            "hPs.stimRoiGroups(end + 1) = hGroup;",
            "hPs.sequenceSelectedStimuli = 1;",
            "hPs.numSequences = Inf;",
            "if isprop(hPs,'autoTriggerPeriod'); hPs.autoTriggerPeriod = 0; end",
            "if isprop(hPs,'stimImmediately'); hPs.stimImmediately = true; end",
            "disp('DIAGNOSTIC_PHOTOSTIM_GRID_POINT_COUNT');",
            "disp(size(pointsUm,1));",
            "disp('DIAGNOSTIC_PHOTOSTIM_GRID_SEQUENCE');",
            "disp(hPs.sequenceSelectedStimuli);",
            "disp('DIAGNOSTIC_PHOTOSTIM_GRID_NUM_SEQUENCES');",
            "disp(hPs.numSequences);",
            "hPs.start();",
            "disp('DIAGNOSTIC_PHOTOSTIM_GRID_READY');",
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


def build_trigger_photostim_command(path_config: PathConfig, stimulus_group_indices: list[int]) -> str:
    hsi = path_config.hsi_variable
    sequence_expr = "[" + " ".join(str(int(idx)) for idx in stimulus_group_indices) + "]"

    lines = [
        build_global_preamble(path_config),
        f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
        "hPs = " + hsi + ".hPhotostim;",
        f"trialTailSingle = {sequence_expr};",
        "assert(~isempty(trialTailSingle), 'Trigger sequence must contain at least one prepared stimulus group.');",
        "assert(all(trialTailSingle >= 1) && all(trialTailSingle <= numel(hPs.stimRoiGroups)), 'Trigger sequence references an invalid stimulus group.');",
        "currentSequence = hPs.sequenceSelectedStimuli;",
        "if isempty(currentSequence); currentSequence = 2; end",
        "currentPosition = [];",
        "if ~isempty(hPs.sequencePosition); currentPosition = double(hPs.sequencePosition); end",
        "if isempty(currentPosition) || currentPosition < 1 || currentPosition > numel(currentSequence);",
        "    currentPosition = 1;",
        "end",
        "insertPosition = [];",
        "idlePosition = [];",
        "reusePreparedTail = false;",
        "triggerSequence = currentSequence;",
        "candidateStart = currentPosition;",
        "candidateStop = candidateStart + numel(trialTailSingle) - 1;",
        "if candidateStop <= numel(currentSequence);",
        "    existingTail = currentSequence(candidateStart:candidateStop);",
        "    if isequal(double(existingTail(:).'), double(trialTailSingle(:).'));",
        "        insertPosition = candidateStart;",
        "        idlePosition = candidateStop;",
        "        reusePreparedTail = true;",
        "    end",
        "end",
        "if ~reusePreparedTail;",
        "    candidateStart = currentPosition + 1;",
        "    candidateStop = candidateStart + numel(trialTailSingle) - 1;",
        "    if candidateStop <= numel(currentSequence);",
        "        existingTail = currentSequence(candidateStart:candidateStop);",
        "        if isequal(double(existingTail(:).'), double(trialTailSingle(:).'));",
        "            insertPosition = candidateStart;",
        "            idlePosition = candidateStop;",
        "            reusePreparedTail = true;",
        "        end",
        "    end",
        "end",
        "assert(reusePreparedTail, 'Prepared active photostim sequence does not contain the requested next trial tail. Run prep_patterns again.');",
        "disp('TRIGGER_PHOTOSTIM_INSERT_POSITION');",
        "disp(double(insertPosition));",
        "disp('TRIGGER_PHOTOSTIM_IDLE_POSITION');",
        "disp(double(idlePosition));",
        "hPs.numSequences = 1;",
        "if isprop(hPs,'stimImmediately'); hPs.stimImmediately = false; end",
        "if ~hPs.active;",
        "    hPs.start();",
        "end",
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


def build_clear_photostim_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hPhotostim') && ~isempty({hsi}.hPhotostim), 'ScanImage photostim handle is not available.');",
            "hPs = " + hsi + ".hPhotostim;",
            "if hPs.active;",
            "    hPs.abort();",
            "end",
            "try; hPs.stimulusMode = 'sequence'; catch; end",
            "try; hPs.sequenceSelectedStimuli = []; catch; end",
            "try; hPs.stimRoiGroups = scanimage.mroi.RoiGroup.empty(1, 0); catch; end",
            "try; hPs.numSequences = 1; catch; end",
            "result = struct();",
            "result.status = 'ready';",
            "result.stimulus_group_count = double(numel(hPs.stimRoiGroups));",
            "result.sequence_length = double(numel(hPs.sequenceSelectedStimuli));",
            "disp('PHOTOSTIM_CLEAR_JSON');",
            "disp(jsonencode(result));",
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
    callback_body = (
        "if ~exist('optoPhotostimTrialDoTaskStarted','var') || ~optoPhotostimTrialDoTaskStarted, "
        "optoPhotostimTrialDoTaskStartedWallTime = posixtime(datetime('now','TimeZone','UTC')); "
        "hSI_cb = hSI; "
        "irm_cb = hSI_cb.hIntegrationRoiManager; "
        "roiNames_cb = cellfun(@char, {irm_cb.intParams.intRois.name}, 'UniformOutput', false); "
        "cursor_cb = double(irm_cb.integrationValueCursor); "
        "if isempty(cursor_cb), cursor_cb = zeros(1, numel(roiNames_cb)); end; "
        "if isscalar(cursor_cb) && numel(roiNames_cb) > 1, cursor_cb = repmat(cursor_cb, 1, numel(roiNames_cb)); end; "
        "frameHist_cb = double(irm_cb.integrationFrameNumberHistory); "
        "timeHist_cb = double(irm_cb.integrationTimestampHistory); "
        "frameVals_cb = zeros(1, numel(roiNames_cb)); "
        "timeVals_cb = zeros(1, numel(roiNames_cb)); "
        "for ii_cb = 1:numel(roiNames_cb), "
        "cursorIdx_cb = min(max(1, round(cursor_cb(min(ii_cb, numel(cursor_cb))))), max(1, size(frameHist_cb, 1))); "
        "if isvector(frameHist_cb), frameVals_cb(ii_cb) = frameHist_cb(cursorIdx_cb); "
        "else, frameVals_cb(ii_cb) = frameHist_cb(cursorIdx_cb, min(ii_cb, size(frameHist_cb, 2))); end; "
        "if isvector(timeHist_cb), timeVals_cb(ii_cb) = timeHist_cb(cursorIdx_cb); "
        "else, timeVals_cb(ii_cb) = timeHist_cb(cursorIdx_cb, min(ii_cb, size(timeHist_cb, 2))); end; "
        "end; "
        "optoPhotostimTrialIntegrationSnapshot = struct('roi_names', {roiNames_cb}, 'cursors', double(cursor_cb(:).'), 'frame_numbers', double(frameVals_cb(:).'), 'timestamps', double(timeVals_cb(:).')); "
        "optoPhotostimTrialDoTaskStarted = true; "
        "end;"
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
            "assignin('base', 'optoPhotostimTrialDoTaskStarted', false);",
            "assignin('base', 'optoPhotostimTrialDoTaskStartedWallTime', 0);",
            "assignin('base', 'optoPhotostimTrialIntegrationSnapshot', struct('roi_names', {{}}, 'cursors', [], 'frame_numbers', [], 'timestamps', []));",
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
            "do_task.sampleCallbackAutoRead = false;",
            "do_task.sampleCallbackN = 1;",
            f"do_task.sampleCallback = @(varargin) evalin('base', {matlab_string(callback_body)});",
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
            "disp('TRIAL_WAVEFORM_TASK_STARTED');",
            "if evalin('base', 'exist(''optoPhotostimTrialDoTaskStarted'',''var'')'); disp(double(evalin('base', 'optoPhotostimTrialDoTaskStarted'))); else; disp(0); end",
            "disp('TRIAL_WAVEFORM_TASK_STARTED_WALL_TIME');",
            "if evalin('base', 'exist(''optoPhotostimTrialDoTaskStartedWallTime'',''var'')'); disp(double(evalin('base', 'optoPhotostimTrialDoTaskStartedWallTime'))); else; disp(0); end",
            "disp('TRIAL_WAVEFORM_INTEGRATION_SNAPSHOT_JSON');",
            "if evalin('base', 'exist(''optoPhotostimTrialIntegrationSnapshot'',''var'')'); disp(jsonencode(evalin('base', 'optoPhotostimTrialIntegrationSnapshot'))); else; disp('{}'); end",
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
            "if evalin('base', 'exist(''optoPhotostimTrialDoTaskStarted'',''var'')'); evalin('base', 'clear optoPhotostimTrialDoTaskStarted'); end",
            "if evalin('base', 'exist(''optoPhotostimTrialDoTaskStartedWallTime'',''var'')'); evalin('base', 'clear optoPhotostimTrialDoTaskStartedWallTime'); end",
            "if evalin('base', 'exist(''optoPhotostimTrialIntegrationSnapshot'',''var'')'); evalin('base', 'clear optoPhotostimTrialIntegrationSnapshot'); end",
            "disp('TRIAL_WAVEFORM_STOPPED');",
        ]
    )


def build_configure_online_analysis_command(
    path_config: PathConfig,
    roi_specs: list[dict[str, object]],
    channel: int,
    roi_diameter_px: int,
    history_length: int,
) -> str:
    payload = {
        "rois": roi_specs,
        "channel": int(channel),
        "roi_diameter_px": int(roi_diameter_px),
        "history_length": int(history_length),
    }
    payload_expr = matlab_string(json.dumps(payload, separators=(",", ":")))
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"cfg = jsondecode({payload_expr});",
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hIntegrationRoiManager') && ~isempty({hsi}.hIntegrationRoiManager), 'ScanImage integration ROI manager is not available.');",
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hRoiManager') && ~isempty({hsi}.hRoiManager), 'ScanImage ROI manager is not available.');",
            f"assert(~isempty({hsi}.objectiveResolution), 'objectiveResolution is not set in ScanImage.');",
            "hInt = " + hsi + ".hIntegrationRoiManager;",
            "imagingRoiGroup = " + hsi + ".hRoiManager.currentRoiGroup;",
            "assert(~isempty(imagingRoiGroup) && most.idioms.isValidObj(imagingRoiGroup), 'Current imaging ROI group is not available.');",
            "res = " + hsi + ".objectiveResolution;",
            "if isscalar(res); resXY = [double(res) double(res)]; else; assert(numel(res) >= 2, 'objectiveResolution must contain at least two values.'); resXY = double(res(1:2)); end",
            "backup = struct();",
            "backup.valid = true;",
            "backup.enable = logical(hInt.enable);",
            "backup.enableDisplay = logical(hInt.enableDisplay);",
            "backup.integrationHistoryLength = double(hInt.integrationHistoryLength);",
            "backup.roiGroup = [];",
            "try; if ~isempty(hInt.roiGroup) && most.idioms.isValidObj(hInt.roiGroup); backup.roiGroup = hInt.roiGroup.copy(); end; catch; backup.roiGroup = []; end",
            "assignin('base', 'optoOnlineAnalysisBackup', backup);",
            "rg = scanimage.mroi.RoiGroup('OptoSchema Online Activity');",
            "added = strings(0,1);",
            "skipped = strings(0,1);",
            "channelNumber = max(1, round(double(cfg.channel)));",
            "diameterPx = max(1, round(double(cfg.roi_diameter_px)));",
            "maskSize = max(5, diameterPx);",
            "if mod(maskSize, 2) == 0; maskSize = maskSize + 1; end",
            "[xx, yy] = meshgrid(linspace(-1, 1, maskSize), linspace(-1, 1, maskSize));",
            "roiMask = double((xx.^2 + yy.^2) <= 1);",
            "for roiIdx = 1:numel(cfg.rois)",
            "    roiSpec = cfg.rois(roiIdx);",
            "    centerUm = [double(roiSpec.x_um) double(roiSpec.y_um)];",
            "    centerRef = [centerUm(1) ./ resXY(1), centerUm(2) ./ resXY(2)];",
            "    zValue = double(roiSpec.z_um);",
            "    roiName = sprintf('OA_%03d', roiIdx);",
            "    if isfield(roiSpec, 'roi_name') && strlength(string(roiSpec.roi_name)) > 0; roiName = char(string(roiSpec.roi_name)); end",
            "    probeField = scanimage.mroi.scanfield.fields.IntegrationField();",
            "    probeField.centerXY = centerRef;",
            "    probeField.sizeXY = [1 1];",
            "    probeField.rotationDegrees = 0;",
            "    probeField.channel = channelNumber;",
            "    probeField.processor = 'cpu';",
            "    probeField.mask = 1;",
            "    [owningSf, ~] = probeField.owningImagingScanField(imagingRoiGroup, zValue, 'centeronly');",
            "    if isempty(owningSf)",
            "        skipped(end+1,1) = string(roiName) + ':no_owning_scanfield'; %#ok<AGROW>",
            "        continue;",
            "    end",
            "    pixRes = double(owningSf.pixelResolutionXY);",
            "    sfSize = double(owningSf.sizeXY);",
            "    if numel(pixRes) < 2 || numel(sfSize) < 2 || any(pixRes <= 0)",
            "        skipped(end+1,1) = string(roiName) + ':invalid_scanfield_geometry'; %#ok<AGROW>",
            "        continue;",
            "    end",
            "    roiSizeRef = [diameterPx .* sfSize(1) ./ pixRes(1), diameterPx .* sfSize(2) ./ pixRes(2)];",
            "    roi = scanimage.mroi.Roi();",
            "    roi.name = roiName;",
            "    sf = scanimage.mroi.scanfield.fields.IntegrationField();",
            "    sf.centerXY = centerRef;",
            "    sf.sizeXY = roiSizeRef;",
            "    sf.rotationDegrees = 0;",
            "    sf.channel = channelNumber;",
            "    sf.processor = 'cpu';",
            "    sf.mask = roiMask;",
            "    roi.add(zValue, sf);",
            "    rg.add(roi);",
            "    added(end+1,1) = string(roiName); %#ok<AGROW>",
            "end",
            "try; hInt.enable = false; catch; end",
            "try; hInt.enableDisplay = false; catch; end",
            "try; hInt.integrationHistoryLength = max(32, round(double(cfg.history_length))); catch; end",
            "hInt.roiGroup = rg;",
            "hInt.enable = numel(rg.rois) > 0;",
            "result = struct();",
            "result.status = 'ready';",
            "result.added = cellstr(added);",
            "result.skipped = cellstr(skipped);",
            "result.roi_count = double(numel(rg.rois));",
            "result.history_length = double(hInt.integrationHistoryLength);",
            "disp('ONLINE_ANALYSIS_CONFIG_JSON');",
            "disp(jsonencode(result));",
        ]
    )


def build_restore_online_analysis_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"assert(~isempty({hsi}) && isprop({hsi}, 'hIntegrationRoiManager') && ~isempty({hsi}.hIntegrationRoiManager), 'ScanImage integration ROI manager is not available.');",
            "hInt = " + hsi + ".hIntegrationRoiManager;",
            "restored = false;",
            "if evalin('base', 'exist(''optoOnlineAnalysisBackup'',''var'')')",
            "    backup = evalin('base', 'optoOnlineAnalysisBackup');",
            "    try; hInt.enable = false; catch; end",
            "    try; if isstruct(backup) && isfield(backup,'roiGroup'); hInt.roiGroup = backup.roiGroup; else; hInt.roiGroup = scanimage.mroi.RoiGroup(); end; catch; end",
            "    try; if isstruct(backup) && isfield(backup,'integrationHistoryLength'); hInt.integrationHistoryLength = double(backup.integrationHistoryLength); end; catch; end",
            "    try; if isstruct(backup) && isfield(backup,'enableDisplay'); hInt.enableDisplay = logical(backup.enableDisplay); end; catch; end",
            "    try; if isstruct(backup) && isfield(backup,'enable'); hInt.enable = logical(backup.enable); end; catch; end",
            "    evalin('base', 'clear optoOnlineAnalysisBackup');",
            "    restored = true;",
            "end",
            "result = struct();",
            "result.status = 'ready';",
            "result.restored = logical(restored);",
            "disp('ONLINE_ANALYSIS_RESTORE_JSON');",
            "disp(jsonencode(result));",
        ]
    )


def build_clear_integration_rois_command(path_config: PathConfig) -> str:
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            "result = struct();",
            "result.status = 'ready';",
            "result.available = false;",
            "result.roi_count = 0;",
            f"if ~isempty({hsi}) && isprop({hsi}, 'hIntegrationRoiManager') && ~isempty({hsi}.hIntegrationRoiManager)",
            "    hInt = " + hsi + ".hIntegrationRoiManager;",
            "    result.available = true;",
            "    try; hInt.enable = false; catch; end",
            "    try; hInt.enableDisplay = false; catch; end",
            "    try; hInt.roiGroup = scanimage.mroi.RoiGroup(); catch; end",
            "    try; result.roi_count = double(numel(hInt.roiGroup.rois)); catch; result.roi_count = 0; end",
            "end",
            "evalin('base', 'clear optoOnlineAnalysisBackup');",
            "disp('INTEGRATION_ROIS_CLEAR_JSON');",
            "disp(jsonencode(result));",
        ]
    )


def build_online_analysis_delta_command(
    path_config: PathConfig,
    last_cursors: list[int] | None,
) -> str:
    cursor_expr = "[]" if not last_cursors else "[" + " ".join(str(int(v)) for v in last_cursors) + "]"
    hsi = path_config.hsi_variable
    return "\n".join(
        [
            build_global_preamble(path_config),
            f"lastCursors = double({cursor_expr});",
            "payload = struct();",
            "payload.status = 'ready';",
            "payload.enabled = false;",
            "payload.roi_names = {};",
            "payload.cursors = [];",
            "payload.values = {};",
            "payload.timestamps = {};",
            "payload.frame_numbers = {};",
            "payload.history_length = 0;",
            f"if ~isempty({hsi}) && isprop({hsi}, 'hIntegrationRoiManager') && ~isempty({hsi}.hIntegrationRoiManager)",
            "    hInt = " + hsi + ".hIntegrationRoiManager;",
            "    payload.enabled = logical(hInt.enable);",
            "    payload.history_length = double(size(hInt.integrationValueHistoryPostProcessed, 1));",
            "    if ~isempty(hInt.intParams) && isfield(hInt.intParams, 'intRois') && ~isempty(hInt.intParams.intRois)",
            "        roiNames = {hInt.intParams.intRois.name};",
            "        cursors = double(hInt.integrationValueCursor(:).');",
            "        payload.roi_names = roiNames;",
            "        payload.cursors = cursors;",
            "        if isempty(lastCursors) || numel(lastCursors) ~= numel(cursors)",
            "            lastCursors = cursors;",
            "        end",
            "        values = cell(1, numel(cursors));",
            "        timestamps = cell(1, numel(cursors));",
            "        frameNumbers = cell(1, numel(cursors));",
            "        histLen = size(hInt.integrationValueHistoryPostProcessed, 1);",
            "        for idx = 1:numel(cursors)",
            "            prev = double(lastCursors(idx));",
            "            curr = double(cursors(idx));",
            "            if histLen <= 0 || curr < 1 || curr > histLen || prev < 1 || prev > histLen",
            "                prev = curr;",
            "            end",
            "            sampleIdx = [];",
            "            if curr > prev",
            "                sampleIdx = (prev + 1):curr;",
            "            elseif curr < prev",
            "                sampleIdx = [(prev + 1):histLen, 1:curr];",
            "            end",
            "            values{idx} = reshape(double(hInt.integrationValueHistoryPostProcessed(sampleIdx, idx)), 1, []);",
            "            timestamps{idx} = reshape(double(hInt.integrationTimestampHistory(sampleIdx, idx)), 1, []);",
            "            frameNumbers{idx} = reshape(double(hInt.integrationFrameNumberHistory(sampleIdx, idx)), 1, []);",
            "        end",
            "        payload.values = values;",
            "        payload.timestamps = timestamps;",
            "        payload.frame_numbers = frameNumbers;",
            "    end",
            "end",
            "disp('ONLINE_ANALYSIS_DELTA_JSON');",
            "disp(jsonencode(payload));",
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
    first_continuation = command.find("...", idx)
    search_stop = first_continuation if first_continuation >= 0 else len(command)
    quoted_start = command.find("'", idx, search_stop)
    if quoted_start < 0:
        return None
    quoted_end = command.find("'", quoted_start + 1, search_stop)
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
import math
