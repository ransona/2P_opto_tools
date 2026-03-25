from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: schema_to_json.py <schema.yaml>", file=sys.stderr)
        return 2

    path = Path(sys.argv[1])
    payload = yaml.safe_load(path.read_text()) or {}
    json.dump(payload, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
