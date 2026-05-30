#!/usr/bin/env python3
"""Compute the full reference-panel Squishy board by live-compressing the 16
real core files with each pinned codec, and write build/meta/squishy-scores.json.
Stamps codec version+argv per row. Marked DRAFT until verification passes."""
import importlib.util as u, json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
s = u.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
sq = u.module_from_spec(s); s.loader.exec_module(sq)

N_CORE = sum(len(v) for v in sq.CORE.values())
out = REPO / "build" / "meta" / "squishy-scores.json"
versions = sq.tool_versions()
panel = {}


def _write(pending):
    """Write the JSON after every codec so correct numbers publish incrementally
    (a slow codec like zpaq can't hold the whole board hostage)."""
    missing = sorted(set(next(iter(panel.values()))["missing"])) if panel else []
    out.write_text(json.dumps({
        "score_definition": "equal-weight geomean of per-category geomeans (nested size→kind→category)",
        "edition": "Squishy-2026-DRAFT",
        "status": "DRAFT — partial board: small members only, large rungs pending — NOT yet a Squishy Score.",
        "corpus_files": N_CORE,
        "missing": missing,
        "pending_codecs": pending,
        "panel": panel,
    }, indent=2) + "\n")


codecs = list(sq.PANEL_ARGV.items())
for i, (codec, argv) in enumerate(codecs):
    print(f"  {codec} ...", flush=True)
    try:
        res = sq.score_cmd(argv)
    except Exception as e:                       # one bad codec must not abort the board
        print(f"    SKIPPED ({type(e).__name__}: {e})", flush=True)
        continue
    res.pop("note_speed", None)
    res["codec_version"] = versions.get(sq.PANEL_TOOL.get(codec, ""), "UNKNOWN")
    res["codec_command"] = argv
    panel[codec] = res
    print(f"    {res['squishy_score']}x  ({res['n_files']}/{N_CORE})", flush=True)
    _write([c for c, _ in codecs[i + 1:]])       # publish after each codec
print(f"wrote {out}")
