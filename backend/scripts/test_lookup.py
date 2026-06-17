"""Quick local smoke test for the processing pipeline (no HTTP server needed).

Usage (from backend/):
    python scripts/test_lookup.py
    python scripts/test_lookup.py path/to/payload.json
"""
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402


def main_cli(argv) -> int:
    if argv:
        payload = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    else:
        payload = json.loads((BACKEND_DIR / "sample_payload.json").read_text(encoding="utf-8"))
    print(json.dumps(main._process(payload), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli(sys.argv[1:]))
