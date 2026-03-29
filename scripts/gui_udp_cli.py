#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
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

    subparsers = parser.add_subparsers(dest="subcommand", required=True)

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
    payload, port = build_payload(args)
    client = UdpJsonClient(args.host, port, args.timeout)
    reply = client.request(payload)
    print(json.dumps(reply, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
