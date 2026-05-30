#!/usr/bin/env python3
"""Recompute the reference board LIVE over build/raw/corpus (the actual bytes),
running each pinned panel codec on every core file. Replaces the old
individual/-matrix path. Writes build/meta/squishy-scores.json in the published
schema (nested score + byte-weighted corpus_bpb + per-file + per-category +
codec_version/codec_command), labelled DRAFT/partial (small members only; the
large rungs are the periodic whole-corpus computation).

  uv run python scripts/board-live.py [--json build/meta/squishy-scores.json]
"""
from __future__ import annotations
import argparse, importlib.util, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=REPO / "build/meta/squishy-scores.json")
    a = ap.parse_args()
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)

    altered = sq.verify_core_checksums()
    if altered:
        print(f"⚠ CORE ALTERED vs CHECKSUMS: {altered} — refusing to score."); return 2

    versions = sq.tool_versions()
    panel = {}
    n_core = sum(len(v) for v in sq.CORE.values())
    for codec, argv in sq.PANEL_ARGV.items():
        print(f"  {codec:<12} ({argv}) ...", flush=True)
        r = sq.score_cmd(argv)
        r.pop("compress_MBps", None); r.pop("note_speed", None)
        r["codec_version"] = versions.get(sq.PANEL_TOOL.get(codec, ""), "UNKNOWN")
        r["codec_command"] = argv
        r["tool_provenance"] = sq.tool_provenance(argv)   # release version (or git sha) + argv + arch
        panel[codec] = r
        print(f"     Squishy {r['squishy_score']}×  corpus bpb {r['corpus_bpb']}", flush=True)

    missing = sorted(set(next(iter(panel.values()))["missing"]))
    out = {
        "score_definition": ("Squishy Score = category-balanced geomean of per-file ratios "
                             "(nested size→kind→category; dimensionless, NOT a bit rate). "
                             "corpus_bpb = byte-weighted 8·total_out/total_in (operational rate)."),
        "edition": "Squishy-2026-DRAFT",
        "core_files": n_core,
        "host_provenance": sq.host_provenance(),       # machine/arch the scores ran on
        "missing": missing,
        "status": ("DRAFT — partial board over the small core members only. The large size "
                   "rungs are scored in the periodic whole-corpus computation; until then this "
                   "is NOT a citable Squishy Score. Each row is a property of "
                   "(corpus, codec, codec_version, codec_command), reproducible for the pinned build."),
        "panel": panel,
    }
    a.json.parent.mkdir(parents=True, exist_ok=True)
    a.json.write_text(json.dumps(out, indent=2) + "\n")
    print(f"\nwrote {a.json}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
