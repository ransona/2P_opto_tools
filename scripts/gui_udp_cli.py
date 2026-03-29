#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


class UdpJsonClient:
    def __init__(self, host: str, port: int, timeout_s: float) -> None:
        self.host = host
        self.port = port
        self.timeout_s = timeout_s

    def request(self, payload: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(self.timeout_s if timeout_s is None else timeout_s)
        try:
            sock.sendto(json.dumps(payload).encode("utf-8"), (self.host, self.port))
            data, _ = sock.recvfrom(65535)
        finally:
            sock.close()
        return json.loads(data.decode("utf-8"))


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _default_gui_entrypoint() -> Path:
    return _repo_root() / "run_pattern_builder_gui.py"


def launch_gui_process(
    python_executable: str,
    gui_entrypoint: str | Path,
    workdir: str | Path | None = None,
    detach: bool = True,
) -> int:
    entrypoint = Path(gui_entrypoint)
    cwd = str(Path(workdir) if workdir is not None else entrypoint.resolve().parent)
    cmd = [python_executable, str(entrypoint)]

    kwargs: dict[str, Any] = {
        "cwd": cwd,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if detach:
        if os.name == "nt":
            kwargs["creationflags"] = (
                getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            )
        else:
            kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **kwargs)
    return int(proc.pid)


def wait_for_udp_ready(host: str, port: int, timeout_s: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return UdpJsonClient(host, port, timeout_s=min(2.0, timeout_s)).request({"action": "ping"})
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)
    raise TimeoutError(f"Timed out waiting for UDP control on {host}:{port}: {last_error}")


def _json_arg(text: str) -> Any:
    return json.loads(text)


def _parse_key_value_pairs(pairs: list[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Expected KEY=JSON_VALUE, got: {pair}")
        key, raw_value = pair.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError(f"Invalid empty key in pair: {pair}")
        values[key] = _json_arg(raw_value)
    return values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CLI for the Opto Schema GUI UDP control interface.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=1816, help="GUI control port by default")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--request-id", default=None)
    parser.add_argument("--auto-launch", action="store_true", help="Launch GUI first, then issue the command")
    parser.add_argument("--wait-after-launch", type=float, default=60.0)
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--gui-entrypoint", default=str(_default_gui_entrypoint()))
    parser.add_argument("--workdir", default=str(_repo_root()))

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    launch = subparsers.add_parser("launch-gui")
    launch.add_argument("--no-detach", action="store_true")
    launch.add_argument("--wait", action="store_true", help="Wait for UDP control ping after launch")

    subparsers.add_parser("ping")
    subparsers.add_parser("get-state")

    set_state = subparsers.add_parser("set-state")
    set_state.add_argument("values", nargs="+", help="KEY=JSON_VALUE pairs")

    invoke = subparsers.add_parser("invoke")
    invoke.add_argument("command")
    invoke.add_argument("--path-name")
    invoke.add_argument("--exp-id")

    matlab_eval = subparsers.add_parser("matlab-eval")
    matlab_eval.add_argument("path_name")
    matlab_eval.add_argument("command")
    matlab_eval.add_argument("--no-preamble", action="store_true")
    matlab_eval.add_argument("--eval-timeout", type=float, default=None)

    logs = subparsers.add_parser("get-log")
    logs.add_argument("--scope", choices=["global", "path_udp"], default="global")
    logs.add_argument("--path-name")
    logs.add_argument("--last-n", type=int, default=200)

    respond = subparsers.add_parser("respond-prompt")
    respond.add_argument("prompt_id")
    respond.add_argument("choice")

    raw = subparsers.add_parser("raw")
    raw.add_argument("payload", help="Raw JSON object payload")

    path_json = subparsers.add_parser("path-json", help="Send raw JSON to a path UDP port")
    path_json.add_argument("payload", help="Raw JSON object payload")

    return parser


def build_payload(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    request_id = args.request_id or f"cli-{args.subcommand}"
    if args.subcommand == "launch-gui":
        raise ValueError("launch-gui does not use a UDP payload")
    if args.subcommand == "ping":
        return {"request_id": request_id, "action": "ping"}, args.port
    if args.subcommand == "get-state":
        return {"request_id": request_id, "action": "get_state"}, args.port
    if args.subcommand == "set-state":
        return {
            "request_id": request_id,
            "action": "set_state",
            "values": _parse_key_value_pairs(args.values),
        }, args.port
    if args.subcommand == "invoke":
        payload: dict[str, Any] = {
            "request_id": request_id,
            "action": "invoke",
            "command": args.command,
        }
        if args.path_name:
            payload["path_name"] = args.path_name
        if args.exp_id:
            payload["exp_id"] = args.exp_id
        return payload, args.port
    if args.subcommand == "matlab-eval":
        payload = {
            "request_id": request_id,
            "action": "matlab_eval",
            "path_name": args.path_name,
            "command": args.command,
            "prepend_preamble": not args.no_preamble,
        }
        if args.eval_timeout is not None:
            payload["timeout_s"] = args.eval_timeout
        return payload, args.port
    if args.subcommand == "get-log":
        payload = {
            "request_id": request_id,
            "action": "get_debug_log",
            "scope": args.scope,
            "last_n": args.last_n,
        }
        if args.path_name:
            payload["path_name"] = args.path_name
        return payload, args.port
    if args.subcommand == "respond-prompt":
        return {
            "request_id": request_id,
            "action": "respond_prompt",
            "prompt_id": args.prompt_id,
            "choice": args.choice,
        }, args.port
    if args.subcommand in {"raw", "path-json"}:
        payload = _json_arg(args.payload)
        if not isinstance(payload, dict):
            raise ValueError("Raw payload must be a JSON object")
        payload.setdefault("request_id", request_id)
        return payload, args.port
    raise ValueError(f"Unsupported subcommand: {args.subcommand}")


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.subcommand == "launch-gui":
            pid = launch_gui_process(
                python_executable=args.python_executable,
                gui_entrypoint=args.gui_entrypoint,
                workdir=args.workdir,
                detach=not args.no_detach,
            )
            result: dict[str, Any] = {"status": "ready", "data": {"launched": True, "pid": pid}}
            if args.wait:
                result["data"]["ping"] = wait_for_udp_ready(args.host, args.port, args.wait_after_launch)
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0

        if args.auto_launch:
            launch_gui_process(
                python_executable=args.python_executable,
                gui_entrypoint=args.gui_entrypoint,
                workdir=args.workdir,
                detach=True,
            )
            wait_for_udp_ready(args.host, args.port, args.wait_after_launch)

        payload, port = build_payload(args)
        client = UdpJsonClient(args.host, port, args.timeout)
        reply = client.request(payload)
        print(json.dumps(reply, indent=2, sort_keys=True))
        return 0
    except ConnectionResetError:
        print(
            f"ERROR: UDP control on {args.host}:{args.port} is not accepting requests. "
            "The GUI is likely not running, the control listener is not bound, or the remote host rejected the port.",
            file=sys.stderr,
        )
        return 2
    except TimeoutError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"ERROR: UDP request failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
