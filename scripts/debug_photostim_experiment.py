#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import socket
import sys
import time
from dataclasses import dataclass


@dataclass
class UdpJsonClient:
    host: str
    port: int
    timeout_s: float = 5.0

    def request(self, payload: dict, timeout_s: float | None = None) -> dict:
        timeout = self.timeout_s if timeout_s is None else timeout_s
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        sock.settimeout(timeout)
        try:
            sock.sendto(json.dumps(payload).encode("utf-8"), (self.host, self.port))
            data, _ = sock.recvfrom(65535)
            return json.loads(data.decode("utf-8"))
        finally:
            sock.close()


def _print_json(label: str, payload: dict) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(payload, indent=2, sort_keys=True))


def wait_for_control(host: str, port: int, timeout_s: float = 60.0) -> UdpJsonClient:
    deadline = time.monotonic() + timeout_s
    client = UdpJsonClient(host, port, timeout_s=2.0)
    attempt = 0
    while time.monotonic() < deadline:
        attempt += 1
        try:
            reply = client.request({"request_id": f"ping-{attempt}", "action": "ping"}, timeout_s=2.0)
            if reply.get("status") == "ready":
                return client
        except Exception:
            pass
        time.sleep(1.0)
    raise TimeoutError(f"Timed out waiting for GUI control on {host}:{port}")


def ensure_reconnect_prompt_handled(control: UdpJsonClient) -> dict:
    state = control.request({"request_id": "state-initial", "action": "get_state"})
    prompt = state.get("data", {}).get("scanimage", {}).get("pending_prompt")
    if prompt and prompt.get("prompt_id") == "startup_reconnect":
        reply = control.request(
            {
                "request_id": "prompt-reconnect",
                "action": "respond_prompt",
                "prompt_id": "startup_reconnect",
                "choice": "yes",
            }
        )
        _print_json("respond_prompt", reply)
        time.sleep(1.0)
        state = control.request({"request_id": "state-post-prompt", "action": "get_state"})
    return state


def ensure_gui_state(control: UdpJsonClient, trigger_mode: str) -> dict:
    reply = control.request(
        {
            "request_id": "set-state",
            "action": "set_state",
            "values": {
                "main_tab": "ScanImage Control",
                "machine": "ar-lab-si2",
                "config": "PS",
                "trigger_mode": trigger_mode,
            },
        }
    )
    _print_json("set_state", reply)
    return control.request({"request_id": "state-after-set", "action": "get_state"})


def ensure_photostim_path_running(control: UdpJsonClient, timeout_s: float = 30.0) -> dict:
    state = control.request({"request_id": "state-before-launch", "action": "get_state"})
    scanimage = state.get("data", {}).get("scanimage", {})
    paths = scanimage.get("paths", {})
    ps_state = paths.get("PS", {})
    if not ps_state.get("launched"):
        reply = control.request(
            {"request_id": "launch-ps", "action": "invoke", "command": "launch_path", "path_name": "PS"},
            timeout_s=10.0,
        )
        _print_json("launch_path", reply)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        state = control.request({"request_id": "state-wait-ps", "action": "get_state"})
        paths = state.get("data", {}).get("scanimage", {}).get("paths", {})
        ps_state = paths.get("PS", {})
        if ps_state.get("launched") and ps_state.get("listener_on"):
            return state
        time.sleep(1.0)
    raise TimeoutError("Photostim path PS did not reach launched+listener_on state")


def dump_recent_logs(control: UdpJsonClient, last_n: int = 80) -> None:
    reply = control.request(
        {"request_id": f"logs-{last_n}", "action": "get_debug_log", "scope": "global", "last_n": last_n}
    )
    lines = reply.get("data", {}).get("lines", [])
    print("\n=== recent logs ===")
    for line in lines:
        print(line)


def path_request(host: str, port: int, payload: dict, timeout_s: float = 90.0) -> dict:
    client = UdpJsonClient(host, port, timeout_s=timeout_s)
    return client.request(payload, timeout_s=timeout_s)


def run_debug_experiment(
    host: str,
    control_port: int,
    path_port: int,
    schema_name: str,
    exp_id: str,
    seq_nums: list[int],
    trigger_mode: str,
    restart_first: bool,
) -> int:
    control = wait_for_control(host, control_port)
    if restart_first:
        try:
            control.request({"request_id": "update-restart", "action": "invoke", "command": "update_and_restart"}, timeout_s=3.0)
        except Exception:
            pass
        control = wait_for_control(host, control_port, timeout_s=90.0)

    state = ensure_reconnect_prompt_handled(control)
    _print_json("initial_state", state)
    state = ensure_gui_state(control, trigger_mode)
    _print_json("configured_state", state)
    state = ensure_photostim_path_running(control)
    _print_json("path_state", state)

    prep_reply = path_request(
        host,
        path_port,
        {
            "action": "prep_patterns",
            "schema_name": schema_name,
            "expID": exp_id,
            "seq_nums": seq_nums,
        },
        timeout_s=180.0,
    )
    _print_json("prep_patterns", prep_reply)
    dump_recent_logs(control, last_n=120)

    exit_code = 0
    for trial_index, seq_num in enumerate(seq_nums, start=1):
        trigger_reply = path_request(
            host,
            path_port,
            {
                "action": "trigger_photo_stim",
                "schema_name": schema_name,
                "expID": exp_id,
                "seq_num": seq_num,
            },
            timeout_s=90.0,
        )
        _print_json(f"trigger_photo_stim trial {trial_index}", trigger_reply)
        dump_recent_logs(control, last_n=120)
        if trigger_reply.get("status") != "ready":
            exit_code = 1
            break
        time.sleep(1.0)

    return exit_code


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Drive the Opto Schema GUI like a real photostim experiment.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--control-port", type=int, default=1816)
    parser.add_argument("--path-port", type=int, default=1813)
    parser.add_argument("--schema-name", default="default")
    parser.add_argument("--exp-id", required=True)
    parser.add_argument("--seq-nums", default="0,1")
    parser.add_argument("--trigger-mode", choices=["software", "hardware"], default="hardware")
    parser.add_argument("--restart-first", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    seq_nums = [int(part.strip()) for part in args.seq_nums.split(",") if part.strip()]
    if not seq_nums:
        raise SystemExit("No seq_nums specified")
    return run_debug_experiment(
        host=args.host,
        control_port=args.control_port,
        path_port=args.path_port,
        schema_name=args.schema_name,
        exp_id=args.exp_id,
        seq_nums=seq_nums,
        trigger_mode=args.trigger_mode,
        restart_first=args.restart_first,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
