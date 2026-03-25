from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .matlab_bridge import (
    MatlabSession,
    MatlabSessionError,
    autodetect_machine_name,
    build_import_command,
    build_run_script_command,
    list_config_names,
    list_machine_names,
    load_machine_config,
    matlab_string,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch configured ScanImage paths and import schema patterns into the configured photostim path."
    )
    parser.add_argument("schema", help="Path to schema YAML file.")
    parser.add_argument("--machine", help="Machine name under configs/. Defaults to autodetected machine when available.")
    parser.add_argument("--config", help="Config name under configs/<machine>/.")
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        default=[],
        help="Only operate on the named path. Repeat to select multiple paths.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave MATLAB sessions running after the import completes.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    schema_path = Path(args.schema).resolve()
    if not schema_path.is_file():
        parser.error(f"Schema file not found: {schema_path}")

    machine_name = _resolve_machine_name(parser, repo_root, args.machine)
    config_name = _resolve_config_name(parser, repo_root, machine_name, args.config)

    try:
        machine_config = load_machine_config(repo_root, machine_name, config_name)
    except Exception as exc:
        print(f"Failed to load ScanImage config: {exc}", file=sys.stderr)
        return 2

    if args.paths:
        selected_paths = args.paths
    elif machine_config.photostim_path:
        selected_paths = [machine_config.photostim_path]
    else:
        selected_paths = machine_config.launch_order

    missing = [path_name for path_name in selected_paths if path_name not in machine_config.paths]
    if missing:
        parser.error(
            f"Unknown path name(s): {', '.join(missing)}. "
            f"Configured paths: {', '.join(machine_config.launch_order)}"
        )

    exit_code = 0
    sessions: list[MatlabSession] = []
    try:
        for path_name in selected_paths:
            path_config = machine_config.paths[path_name]
            session = MatlabSession(path_config)
            sessions.append(session)
            print(f"[{path_name}] starting MATLAB")
            session.start(startup_command=_build_launch_startup_command(path_config))
            if session.simulated:
                print(f"[{path_name}] running launch.m")
                for line in session.eval(
                    build_run_script_command(path_config, "launch.m"),
                    timeout_s=path_config.startup_timeout_s,
                ):
                    if line.strip():
                        print(f"[{path_name}] {line}")
            print(f"[{path_name}] importing schema patterns from {schema_path}")
            for line in session.eval(
                build_import_command(schema_path, path_config),
                timeout_s=path_config.command_timeout_s,
            ):
                if line.strip():
                    print(f"[{path_name}] {line}")
    except MatlabSessionError as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    finally:
        if not args.keep_open:
            for session in reversed(sessions):
                session.stop()
    return exit_code


def _resolve_machine_name(parser: argparse.ArgumentParser, repo_root: Path, requested: str | None) -> str:
    if requested:
        return requested
    autodetected = autodetect_machine_name(repo_root)
    if autodetected:
        return autodetected
    machines = list_machine_names(repo_root)
    if len(machines) == 1:
        return machines[0]
    parser.error(f"Specify --machine. Available machines: {', '.join(machines) if machines else 'none'}")
    raise AssertionError


def _build_launch_startup_command(path_config) -> str:
    return "; ".join(
        [
            f"addpath(genpath({matlab_string(str(path_config.repo_matlab_path))}))",
            f"cd({matlab_string(str(path_config.directory))})",
            "run('launch.m')",
            f"opto.scanimage.startCommandServer({matlab_string(str(path_config.directory))})",
        ]
    )


def _resolve_config_name(
    parser: argparse.ArgumentParser,
    repo_root: Path,
    machine_name: str,
    requested: str | None,
) -> str:
    if requested:
        return requested
    configs = list_config_names(repo_root, machine_name)
    if len(configs) == 1:
        return configs[0]
    parser.error(
        f"Specify --config for machine '{machine_name}'. Available configs: {', '.join(configs) if configs else 'none'}"
    )
    raise AssertionError


if __name__ == "__main__":
    raise SystemExit(main())
