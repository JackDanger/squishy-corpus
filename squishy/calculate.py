"""Console entry for `squishy-calculate`.

The implementation lives in scripts/squishy-calculate.py (loaded by path so the
single source of truth is shared with `uv run python scripts/squishy-calculate.py`).
"""
from __future__ import annotations
import importlib.util
from pathlib import Path


def main() -> int:
    impl = Path(__file__).resolve().parent.parent / "scripts" / "squishy-calculate.py"
    spec = importlib.util.spec_from_file_location("squishy_calculate_impl", impl)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.main()


if __name__ == "__main__":
    raise SystemExit(main())
