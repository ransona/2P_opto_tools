from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .matlab_bridge import (
    DEFAULT_CONFIG_PATH,
    MatlabSession,
    MatlabSessionError,
    build_import_command,
    parse_session_configs,
    session_names,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch MATLAB ScanImage sessions and import schema patterns as photostimulation stimulus groups."
    )
    parser.add_argument("schema", help="Path to schema YAML file.")
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to bridge INI configuration file.",
    )
    parser.add_argument(
        "--session",
        action="append",
        dest="sessions",
        default=[],
        help="Only run the named session. Repeat to select multiple sessions.",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help="Leave MATLAB sessions running after the import command succeeds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    repo_root = Path(__file__).resolve().parents[2]
    schema_path = Path(args.schema).resolve()
    config_path = Path(args.config).resolve()

    if not schema_path.is_file():
        parser.error(f"Schema file not found: {schema_path}")

    try:
        configs = parse_session_configs(config_path, repo_root)
    except Exception as exc:
        print(f"Failed to load bridge config: {exc}", file=sys.stderr)
        return 2

    if args.sessions:
        selected = set(args.sessions)
        configs = [config for config in configs if config.name in selected]
        missing = selected.difference(session_names(configs))
        if missing:
            print(
                f"Unknown session name(s): {', '.join(sorted(missing))}. "
                f"Configured sessions: {', '.join(session_names(parse_session_configs(config_path, repo_root)))}",
                file=sys.stderr,
            )
            return 2

    exit_code = 0
    sessions: list[MatlabSession] = []
    try:
        for config in configs:
            session = MatlabSession(config)
            sessions.append(session)
            print(f"[{config.name}] starting MATLAB session")
            session.start()
            print(f"[{config.name}] importing schema patterns from {schema_path}")
            output_lines = session.eval(build_import_command(schema_path, config), timeout_s=config.startup_timeout_s)
            for line in output_lines:
                if line.strip():
                    print(f"[{config.name}] {line}")
    except MatlabSessionError as exc:
        print(str(exc), file=sys.stderr)
        exit_code = 1
    finally:
        if not args.keep_open:
            for session in reversed(sessions):
                session.stop()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
