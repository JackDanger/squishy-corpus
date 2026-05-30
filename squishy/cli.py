"""`squishy` command — the Squishy Score runner.

The corpus is acquired and scored by the scripts in scripts/ (the canonical
implementation). This CLI exposes the runner as an installed command:

    squishy bench  --cmd "gzip -9 -c"      # score one codec over the local members
    squishy board                          # reference panel over the local members

The whole-edition canonical Squishy Score (streaming core + large rungs) is the
separate `squishy-calculate` command (squishy/calculate.py → scripts/squishy-calculate.py).
"""
from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    _argv = sys.argv[1:] if argv is None else argv
    if not _argv or _argv[0] in ("-h", "--help"):
        print("usage: squishy {bench|board} [...]   "
              "(whole-edition score: squishy-calculate --cmd \"...\")", file=sys.stderr)
        return 0 if _argv else 2
    if _argv[0] in ("bench", "board", "score"):
        from squishy import score
        sub = "board" if _argv[0] == "score" else _argv[0]
        return score.main([sub, *_argv[1:]])
    print(f"unknown command: {_argv[0]} (try: bench, board)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
