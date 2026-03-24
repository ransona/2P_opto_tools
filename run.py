from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent
    src = root / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))

    try:
        from opto_schema_gui.app import main as app_main
    except ModuleNotFoundError as exc:
        if exc.name == "PyQt6":
            raise SystemExit(
                "PyQt6 is not installed. Install dependencies with:\n"
                "  python -m pip install PyQt6 PyYAML\n"
                "or create a virtual environment and install the project there."
            ) from exc
        raise

    app_main()


if __name__ == "__main__":
    main()
