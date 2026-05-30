"""Package entry point for the Squishy Score runner.

The runner implementation lives in scripts/squishy.py (loaded by the corpus
build scripts and tests by path). This shim makes it reachable from the
installed package so `squishy bench` / `squishy board` work as a real command
(wired in squishy/cli.py).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_RUNNER = Path(__file__).resolve().parent.parent / "scripts" / "squishy.py"


def _load():
    spec = importlib.util.spec_from_file_location("squishy_score_runner", _RUNNER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main(argv: list[str] | None = None) -> int:
    """Run `bench` / `board` via the score runner. argv like ['bench','--cmd','...']."""
    if argv is not None:
        sys.argv = ["squishy-score", *argv]
    return _load().main()
