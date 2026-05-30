#!/usr/bin/env python3
"""Read `squishy-calculate --json` output on stdin and write the complete-edition
result to build/meta/squishy-score-complete.json (flattening per_file to ratios,
keeping tool + host provenance). The runner prints progress before the JSON object,
so we slice from the first brace."""
from __future__ import annotations
import json, sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
txt = sys.stdin.read()
if "{" not in txt:
    sys.exit("no JSON object in input (run failed?)")
d = json.loads(txt[txt.index("{"):])
out = {
    "edition": d["edition"],
    "note": ("Complete-edition Squishy Score — nested size→kind→category geomean over the "
             "compressibility-scored size-points (core + large rungs), round-trip verified."),
    "complete": d["complete"], "round_trip_verified": d.get("round_trip_verified"),
    "codec": d["cmd"], "codec_version": d["codec_version"],
    "tool_provenance": d.get("tool_provenance"), "host_provenance": d.get("host_provenance"),
    "squishy_score": d["squishy_score"], "corpus_bpb": d["corpus_bpb"],
    "total_in_bytes": d["total_in_bytes"], "total_out_bytes": d["total_out_bytes"],
    "categories": d["categories"], "kinds": d.get("kinds", {}),
    "per_file": {k: v["ratio"] for k, v in d["per_file"].items()},
}
dst = REPO / "build/meta/squishy-score-complete.json"
dst.write_text(json.dumps(out, indent=2) + "\n")
print(f"wrote {dst}: {out['codec']} = {out['squishy_score']}× "
      f"(complete={out['complete']}, verified={out['round_trip_verified']})")
