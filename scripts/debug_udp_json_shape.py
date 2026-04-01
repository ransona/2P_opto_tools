#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
from pathlib import Path
from typing import Any


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.file:
        raw = Path(args.file).read_text(encoding="utf-8")
    elif args.json:
        raw = args.json
    else:
        raw = sys.stdin.read()
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise ValueError(f"Top-level JSON must be an object; received {type(parsed).__name__}")
    return parsed


def _describe_value(value: Any) -> str:
    if isinstance(value, dict):
        return f"object[{len(value)}]"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    return type(value).__name__


def _print_summary(payload: dict[str, Any]) -> None:
    print("Top level:")
    for key, value in payload.items():
        print(f"  {key}: {_describe_value(value)}")
    conditions = payload.get("stimulus_conditions")
    print("stimulus_conditions check:")
    if conditions is None:
        print("  missing")
        return
    print(f"  type: {type(conditions).__name__}")
    if isinstance(conditions, list):
        print(f"  length: {len(conditions)}")
        for idx, item in enumerate(conditions[:5]):
            print(f"  [{idx}] type: {type(item).__name__}")
            if isinstance(item, dict):
                for key, value in item.items():
                    print(f"    {key}: {_describe_value(value)}")
            else:
                print(f"    value: {item!r}")
        if len(conditions) > 5:
            print(f"  ... {len(conditions) - 5} more entries")


def _send_payload(payload: dict[str, Any], host: str, port: int, timeout_s: float) -> int:
    encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_s)
        sock.sendto(encoded, (host, port))
        data, _ = sock.recvfrom(65535)
    try:
        reply = json.loads(data.decode("utf-8"))
    except Exception:
        print("Raw reply:")
        print(data.decode("utf-8", errors="replace"))
        return 0
    print("Reply:")
    print(json.dumps(reply, indent=2, sort_keys=True))
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Inspect and optionally send UDP JSON payloads.")
    parser.add_argument("--file", help="Path to a JSON file to inspect")
    parser.add_argument("--json", help="Raw JSON string to inspect")
    parser.add_argument("--send", action="store_true", help="Send the parsed payload over UDP after inspection")
    parser.add_argument("--host", default="127.0.0.1", help="UDP host when using --send")
    parser.add_argument("--port", type=int, default=1813, help="UDP port when using --send")
    parser.add_argument("--timeout", type=float, default=5.0, help="UDP reply timeout in seconds")
    args = parser.parse_args(argv)

    try:
        payload = _load_payload(args)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    _print_summary(payload)
    if not args.send:
        return 0
    try:
        return _send_payload(payload, args.host, args.port, args.timeout)
    except Exception as exc:
        print(f"ERROR sending payload: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
