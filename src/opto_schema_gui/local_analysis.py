from __future__ import annotations

import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QProcess
from PyQt6.QtGui import QTextCursor
from PyQt6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


EXP_ID_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{2}_[A-Za-z0-9]+$")


def _default_python() -> str:
    if sys.platform.startswith("win"):
        return r"C:\Users\ScanImage\miniconda3\python.exe"
    return sys.executable


def _default_code_root(name: str) -> str:
    if sys.platform.startswith("win"):
        return rf"C:\Code\repos\{name}"
    return str(Path.home() / "code" / name)


def _exp_id_sort_key(path: Path) -> tuple[float, str]:
    try:
        return (path.stat().st_mtime, path.name)
    except OSError:
        return (0.0, path.name)


class LocalAnalysisWidget(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._temp_files: list[Path] = []
        self._build_ui()
        self.refresh_discovery()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)

        paths_box = QGroupBox("Local paths")
        paths_grid = QGridLayout(paths_box)
        self.python_edit = QLineEdit(_default_python())
        self.split_script_edit = QLineEdit(str(Path(_default_code_root("meso_tif_split-main")) / "split_meso_rois.py"))
        self.pipeline_root_edit = QLineEdit(_default_code_root("lab_pipeline"))
        self.local_raw_root_edit = QLineEdit(r"F:\Local_Repository")
        self.local_processed_root_edit = QLineEdit(r"F:\Local_Repository_Processed")
        self.local_nas_root_edit = QLineEdit(r"\\ar-lab-nas1\DataServer\Remote_Repository")
        self.suite2p_config_root_edit = QLineEdit(r"F:\s2p_ops")

        rows = [
            ("Python", self.python_edit),
            ("Split script", self.split_script_edit),
            ("Pipeline repo", self.pipeline_root_edit),
            ("Local data root", self.local_raw_root_edit),
            ("Processed root", self.local_processed_root_edit),
            ("NAS root", self.local_nas_root_edit),
            ("Suite2p config root", self.suite2p_config_root_edit),
        ]
        for row, (label, widget) in enumerate(rows):
            paths_grid.addWidget(QLabel(label), row, 0)
            paths_grid.addWidget(widget, row, 1)
        layout.addWidget(paths_box)

        step1_box = QGroupBox("1) Split to paths and ROIs")
        step1_layout = QVBoxLayout(step1_box)
        self.split_command_label = QLabel()
        self.split_command_label.setWordWrap(True)
        split_row = QHBoxLayout()
        self.split_btn = QPushButton("Split to paths and ROIs")
        split_row.addWidget(self.split_btn)
        split_row.addStretch(1)
        step1_layout.addWidget(self.split_command_label)
        step1_layout.addLayout(split_row)
        layout.addWidget(step1_box)

        run_box = QGroupBox("2) Run Step 1 / 3) Run Step 2")
        run_grid = QGridLayout(run_box)
        self.user_combo = QComboBox()
        self.user_combo.setEditable(True)
        self.exp_combo = QComboBox()
        self.exp_combo.setEditable(True)
        self.suite2p_config_combo = QComboBox()
        self.suite2p_config_combo.setEditable(True)
        self.functional_chan_spin = QSpinBox()
        self.functional_chan_spin.setRange(1, 8)
        self.functional_chan_spin.setValue(1)
        self.suite2p_env_edit = QLineEdit("suite2p_1.1.0")
        self.pre_s_spin = QSpinBox()
        self.pre_s_spin.setRange(0, 3600)
        self.pre_s_spin.setValue(5)
        self.post_s_spin = QSpinBox()
        self.post_s_spin.setRange(0, 3600)
        self.post_s_spin.setValue(5)

        run_grid.addWidget(QLabel("Username"), 0, 0)
        run_grid.addWidget(self.user_combo, 0, 1)
        run_grid.addWidget(QLabel("expID"), 1, 0)
        run_grid.addWidget(self.exp_combo, 1, 1)
        run_grid.addWidget(QLabel("Suite2p config"), 2, 0)
        run_grid.addWidget(self.suite2p_config_combo, 2, 1)
        run_grid.addWidget(QLabel("Functional channel"), 3, 0)
        run_grid.addWidget(self.functional_chan_spin, 3, 1)
        run_grid.addWidget(QLabel("Suite2p env"), 4, 0)
        run_grid.addWidget(self.suite2p_env_edit, 4, 1)
        run_grid.addWidget(QLabel("Step 2 pre s"), 5, 0)
        run_grid.addWidget(self.pre_s_spin, 5, 1)
        run_grid.addWidget(QLabel("Step 2 post s"), 6, 0)
        run_grid.addWidget(self.post_s_spin, 6, 1)

        button_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh lists")
        self.run_step1_btn = QPushButton("Run Step 1")
        self.run_step2_btn = QPushButton("Run Step 2")
        self.stop_btn = QPushButton("Stop current command")
        self.stop_btn.setEnabled(False)
        button_row.addWidget(self.refresh_btn)
        button_row.addWidget(self.run_step1_btn)
        button_row.addWidget(self.run_step2_btn)
        button_row.addWidget(self.stop_btn)
        button_row.addStretch(1)
        run_grid.addLayout(button_row, 7, 0, 1, 2)
        layout.addWidget(run_box)

        output_box = QGroupBox("Command output")
        output_layout = QVBoxLayout(output_box)
        self.output_edit = QTextEdit()
        self.output_edit.setReadOnly(True)
        self.output_edit.setMinimumHeight(220)
        self.output_edit.setStyleSheet("font-family: Consolas, 'Courier New', monospace;")
        output_layout.addWidget(self.output_edit)
        layout.addWidget(output_box, 1)

        self.split_btn.clicked.connect(self.run_split)
        self.refresh_btn.clicked.connect(self.refresh_discovery)
        self.run_step1_btn.clicked.connect(self.run_step1)
        self.run_step2_btn.clicked.connect(self.run_step2)
        self.stop_btn.clicked.connect(self.stop_current_command)
        self.user_combo.currentTextChanged.connect(self._populate_suite2p_configs)
        self.python_edit.textChanged.connect(self._update_split_command_label)
        self.split_script_edit.textChanged.connect(self._update_split_command_label)
        self.local_raw_root_edit.textChanged.connect(self._update_split_command_label)
        self._update_split_command_label()

    def refresh_discovery(self) -> None:
        current_user = self.user_combo.currentText().strip()
        users = self._discover_users()
        self.user_combo.blockSignals(True)
        self.user_combo.clear()
        self.user_combo.addItems(users)
        if current_user:
            index = self.user_combo.findText(current_user)
            if index < 0:
                self.user_combo.addItem(current_user)
                index = self.user_combo.findText(current_user)
            self.user_combo.setCurrentIndex(index)
        elif users:
            self.user_combo.setCurrentIndex(0)
        self.user_combo.blockSignals(False)

        self._populate_exp_ids()
        self._populate_suite2p_configs()
        self._update_split_command_label()

    def _discover_users(self) -> list[str]:
        root = Path(self.suite2p_config_root_edit.text().strip())
        if not root.exists():
            return []
        return sorted(path.name for path in root.iterdir() if path.is_dir())

    def _populate_exp_ids(self) -> None:
        current = self.exp_combo.currentText().strip()
        root = Path(self.local_raw_root_edit.text().strip())
        exp_dirs: list[Path] = []
        if root.exists():
            try:
                exp_dirs = [
                    path
                    for path in root.glob("*/*")
                    if path.is_dir() and EXP_ID_PATTERN.match(path.name)
                ]
            except OSError:
                exp_dirs = []
        exp_dirs = sorted(exp_dirs, key=_exp_id_sort_key, reverse=True)

        self.exp_combo.blockSignals(True)
        self.exp_combo.clear()
        self.exp_combo.addItems([path.name for path in exp_dirs])
        if current:
            index = self.exp_combo.findText(current)
            if index < 0:
                self.exp_combo.addItem(current)
                index = self.exp_combo.findText(current)
            self.exp_combo.setCurrentIndex(index)
        elif exp_dirs:
            self.exp_combo.setCurrentIndex(0)
        self.exp_combo.blockSignals(False)

    def _populate_suite2p_configs(self) -> None:
        current = self.suite2p_config_combo.currentText().strip()
        user = self.user_combo.currentText().strip()
        config_dir = Path(self.suite2p_config_root_edit.text().strip()) / user if user else Path()
        configs: list[str] = []
        if config_dir.exists():
            try:
                configs = sorted(path.name for path in config_dir.glob("*.npy") if path.is_file())
            except OSError:
                configs = []
        self.suite2p_config_combo.blockSignals(True)
        self.suite2p_config_combo.clear()
        self.suite2p_config_combo.addItems(configs)
        if current:
            index = self.suite2p_config_combo.findText(current)
            if index < 0:
                self.suite2p_config_combo.addItem(current)
                index = self.suite2p_config_combo.findText(current)
            self.suite2p_config_combo.setCurrentIndex(index)
        elif configs:
            self.suite2p_config_combo.setCurrentIndex(0)
        self.suite2p_config_combo.blockSignals(False)

    def _update_split_command_label(self) -> None:
        self.split_command_label.setText(" ".join(self._split_command()))

    def _split_command(self) -> list[str]:
        return [
            self.python_edit.text().strip(),
            self.split_script_edit.text().strip(),
            self.local_raw_root_edit.text().strip(),
        ]

    def _validate_common(self) -> bool:
        if not self.python_edit.text().strip():
            QMessageBox.warning(self, "Missing Python", "Set the Python executable.")
            return False
        return True

    def _selected_values(self) -> tuple[str, str, str]:
        return (
            self.user_combo.currentText().strip(),
            self.exp_combo.currentText().strip(),
            self.suite2p_config_combo.currentText().strip(),
        )

    def run_split(self) -> None:
        if not self._validate_common():
            return
        command = self._split_command()
        self._start_process(command[0], command[1:], "Split to paths and ROIs")

    def run_step1(self) -> None:
        if not self._validate_common():
            return
        user_id, exp_id, suite2p_config = self._selected_values()
        if not user_id or not exp_id or not suite2p_config:
            QMessageBox.warning(self, "Missing Step 1 fields", "Set username, expID, and Suite2p config.")
            return
        config_path = self._write_step1_config(user_id, exp_id, suite2p_config)
        self._start_process(self.python_edit.text().strip(), [str(config_path)], "Run Step 1")

    def run_step2(self) -> None:
        if not self._validate_common():
            return
        user_id, exp_id, _suite2p_config = self._selected_values()
        if not user_id or not exp_id:
            QMessageBox.warning(self, "Missing Step 2 fields", "Set username and expID.")
            return
        config_path = self._write_step2_config(user_id, exp_id)
        self._start_process(self.python_edit.text().strip(), [str(config_path)], "Run Step 2")

    def _write_step1_config(self, user_id: str, exp_id: str, suite2p_config: str) -> Path:
        config = f"""
from pathlib import Path
import sys

pipeline_root = Path({self.pipeline_root_edit.text().strip()!r})
sys.path.insert(0, str(pipeline_root / "src"))

from preprocess_pipeline.step1.run_batch import run_step1_batch_universal

step1_config = {{}}
step1_config["userID"] = {user_id!r}
step1_config["expIDs"] = [{exp_id!r}]
step1_config["local_raw_repository_root"] = {self.local_raw_root_edit.text().strip()!r}
step1_config["local_processed_repository_root"] = {self.local_processed_root_edit.text().strip()!r}
step1_config["local_nas_repository_root"] = {self.local_nas_root_edit.text().strip()!r}
step1_config["suite2p_config_root"] = {self.suite2p_config_root_edit.text().strip()!r}
step1_config["suite2p_env"] = {self.suite2p_env_edit.text().strip()!r}
step1_config["suite2p_config"] = {{
    "default": {{"config": {suite2p_config!r}, "functional_chan": {self.functional_chan_spin.value()}}},
}}
step1_config["runs2p"] = True
step1_config["rundlc"] = False
step1_config["runfitpupil"] = False

run_step1_batch_universal(step1_config)
"""
        return self._write_temp_config("opto_local_step1", config)

    def _write_step2_config(self, user_id: str, exp_id: str) -> Path:
        config = f"""
from pathlib import Path
import sys

pipeline_root = Path({self.pipeline_root_edit.text().strip()!r})
sys.path.insert(0, str(pipeline_root / "src"))

from preprocess_pipeline.step2.run_batch import run_step2_batch

step2_config = {{}}
step2_config["userID"] = {user_id!r}
step2_config["expIDs"] = [{exp_id!r}]
step2_config["local_raw_repository_root"] = {self.local_raw_root_edit.text().strip()!r}
step2_config["local_processed_repository_root"] = {self.local_processed_root_edit.text().strip()!r}
step2_config["local_nas_repository_root"] = {self.local_nas_root_edit.text().strip()!r}
step2_config["pre_secs"] = {self.pre_s_spin.value()}
step2_config["post_secs"] = {self.post_s_spin.value()}
step2_config["run_bonvision"] = False
step2_config["run_s2p_timestamp"] = True
step2_config["run_ephys"] = False
step2_config["run_dlc_timestamp"] = False
step2_config["run_cuttraces"] = True
step2_config["settings"] = {{
    "neuropil_coeff": [0.7, 0.7],
    "subtract_overall_frame": False,
}}

run_step2_batch(step2_config)
"""
        return self._write_temp_config("opto_local_step2", config)

    def _write_temp_config(self, prefix: str, text: str) -> Path:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = Path(tempfile.gettempdir()) / f"{prefix}_{timestamp}.py"
        path.write_text(text.strip() + "\n", encoding="utf-8")
        self._temp_files.append(path)
        self._append_output(f"[config] wrote {path}")
        return path

    def _start_process(self, program: str, args: list[str], title: str) -> None:
        if self._process is not None:
            QMessageBox.warning(self, "Command running", "Wait for the current command to finish or stop it.")
            return
        self._append_output("")
        self._append_output(f"=== {title} ===")
        self._append_output("$ " + " ".join([program] + args))
        process = QProcess(self)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_process_output)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self.stop_btn.setEnabled(True)
        process.start(program, args)
        if not process.waitForStarted(3000):
            self._append_output(f"[error] failed to start: {program}")
            self._process = None
            self.stop_btn.setEnabled(False)

    def _read_process_output(self) -> None:
        if self._process is None:
            return
        data = bytes(self._process.readAllStandardOutput()).decode(errors="replace")
        if data:
            self._append_output(data.rstrip())

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        status = "crashed" if exit_status == QProcess.ExitStatus.CrashExit else "finished"
        self._append_output(f"[{status}] exit_code={exit_code}")
        self._process = None
        self.stop_btn.setEnabled(False)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        self._append_output(f"[process error] {error.name}")

    def stop_current_command(self) -> None:
        if self._process is None:
            return
        self._append_output("[stop] terminating current command")
        self._process.terminate()
        if not self._process.waitForFinished(3000):
            self._append_output("[stop] killing current command")
            self._process.kill()

    def _append_output(self, text: str) -> None:
        self.output_edit.append(text)
        self.output_edit.moveCursor(QTextCursor.MoveOperation.End)
