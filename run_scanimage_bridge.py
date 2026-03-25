from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    try:
        from opto_schema_gui.scanimage_bridge import main as bridge_main
    except ModuleNotFoundError as exc:
        if exc.name == "yaml":
            raise SystemExit(
                "PyYAML is not installed. Install dependencies with:\n"
                "  python -m pip install PyYAML\n"
                "or create a virtual environment and install the project there."
            ) from exc
        raise

    raise SystemExit(bridge_main(sys.argv[1:]))


if __name__ == "__main__":
    main()
