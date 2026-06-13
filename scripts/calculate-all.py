#!/usr/bin/env python3
"""calculate-all — the complete-edition Squishy Score for the WHOLE reference panel.

Runs `squishy-calculate --verify` once per reference codec (the canonical
PANEL_ARGV/PANEL_DECOMP pairs in squishy.py — single source of truth), in
parallel, and writes the complete-edition board to
build/meta/squishy-board-complete.json. Per-codec runs are cached and resumable
(squishy-calculate's own state), so re-running is cheap.

Safety properties:
  • fail closed   — any codec failure (crash, lossless failure, partial run)
                    aborts the board write; no silent partial board.
  • no downgrade  — an existing board covering MORE codecs is never overwritten
                    by one covering fewer (e.g. a machine missing brotli).
  • deterministic — scores are byte-deterministic per (codec version, argv);
                    concurrency can't change any number. Timing is per-codec
                    informational only and never canonical.

Losslessness follows run-all.sh's model: every panel codec is first certified
lossless on this host by verify-codecs-sane.py (fail closed), and the reference
codec carries the full-edition round-trip (squishy-score-complete.json). The
board runs themselves are plain streamed compressions — no temp files, so the
panel needs no scratch disk beyond the byte cache.

  uv run python scripts/calculate-all.py            # the whole installed panel
  uv run python scripts/calculate-all.py --jobs 2   # limit concurrency
"""
from __future__ import annotations
import argparse, importlib.util, json, os, shutil, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BOARD = REPO / "build" / "meta" / "squishy-board-complete.json"


def load_squishy():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


EDITION = load_squishy().EDITION  # single source of truth: scripts/squishy.py:EDITION


def run_one(label: str, argv: str, log: Path) -> dict:
    """One complete-edition run; returns the parsed --json result.
    Raises RuntimeError on any failure (non-zero exit, partial run, no JSON)."""
    t0 = time.time()
    with log.open("wb") as f:
        rc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "squishy-calculate.py"),
             "--cmd", argv, "--json"],
            stdout=f, stderr=subprocess.STDOUT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}).returncode
    txt = log.read_text(errors="replace")
    if rc != 0:
        raise RuntimeError(f"{label}: squishy-calculate exited {rc} — see {log}")
    # the JSON object is the last thing printed, after the progress lines
    i = txt.rfind("\n{")
    if i < 0:
        raise RuntimeError(f"{label}: no JSON in output — see {log}")
    d = json.loads(txt[i + 1:])
    if not d.get("complete"):
        raise RuntimeError(f"{label}: run is not complete — see {log}")
    d["wall_seconds_informational"] = round(time.time() - t0, 1)
    return d


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)    # progress lines appear live under make/tee
    ap = argparse.ArgumentParser(prog="calculate-all",
                                 description="complete-edition Squishy Score for the whole reference panel")
    ap.add_argument("--jobs", type=int, default=None,
                    help="max concurrent codecs (default: min(panel, cpu count))")
    args = ap.parse_args()

    sq = load_squishy()
    jobs: list[tuple[str, str]] = []
    for label, argv in sq.PANEL_ARGV.items():
        tool = sq.PANEL_TOOL[label]
        if shutil.which(tool) is None:
            print(f"  ⚠ skipping {label}: `{tool}` not installed on this machine")
            continue
        jobs.append((label, argv))
    if not jobs:
        sys.exit("FATAL: no panel codecs installed.")

    # no downgrade: never replace a board that covers codecs this run can't
    if BOARD.exists():
        have = set(json.loads(BOARD.read_text()).get("codecs", {}))
        miss = have - {label for label, _ in jobs}
        if miss:
            sys.exit(f"FATAL: existing {BOARD.name} covers {sorted(miss)} which this "
                     f"machine can't run — refusing to write a smaller board.")

    # certify every panel codec binary lossless on this host before scoring (fail closed)
    if subprocess.run([sys.executable, str(REPO / "scripts" / "verify-codecs-sane.py")]).returncode != 0:
        sys.exit("FATAL: a panel codec is not lossless on this host; board NOT run.")

    workers = args.jobs or min(len(jobs), os.cpu_count() or 1)

    logs = Path.home() / ".cache" / "squishy" / EDITION / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    print(f"{EDITION} · complete edition × {len(jobs)} panel codecs · {workers} concurrent")
    results: dict[str, dict] = {}
    failures: list[str] = []

    def work(job):
        label, argv = job
        log = logs / (argv.replace(" ", "_").replace("/", "-") + ".log")
        print(f"  ▶ {label:<11} ({argv})  log: {log}")
        try:
            d = run_one(label, argv, log)
            results[label] = d
            print(f"  ✔ {label:<11} {d['squishy_score']:.2f}×  bpb {d['corpus_bpb']:.3f}  "
                  f"({d['wall_seconds_informational']:.0f}s)")
        except Exception as e:
            failures.append(str(e))
            print(f"  ✘ {e}")

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, jobs))

    if failures:                                   # fail closed — no partial board
        sys.exit(f"FATAL: {len(failures)} codec(s) failed; board NOT written:\n  "
                 + "\n  ".join(failures))

    lock = REPO / "build" / "tools.lock"
    board = {
        "edition": json.loads((REPO / "build/meta/edition.json").read_text()).get("edition"),
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": ("Complete-edition reference board — every panel codec over every scored "
                 "file (core + large rungs). Squishy Score = plain geomean of per-file "
                 "ratio, one vote per file; corpus_bpb = 8·out/in, byte-weighted. "
                 "Reproducible only for the pinned codec builds recorded per row."),
        "losslessness": ("every panel codec certified lossless on this host by "
                         "verify-codecs-sane.py before scoring; the reference codec "
                         "additionally carries a full-edition round-trip "
                         "(squishy-score-complete.json)."),
        "host_provenance": sq.host_provenance(),
        "tools_lock_sha256": (__import__("hashlib").sha256(lock.read_bytes()).hexdigest()
                              if lock.exists() else None),
        "codecs": {label: {
            "codec_command": sq.PANEL_ARGV[label],
            "codec_version": d["codec_version"],
            "tool_provenance": d["tool_provenance"],
            "squishy_score": d["squishy_score"],
            "corpus_bpb": d["corpus_bpb"],
            "total_in_bytes": d["total_in_bytes"],
            "total_out_bytes": d["total_out_bytes"],
            "round_trip_verified": d["round_trip_verified"],
            "categories": d["categories"],
            "kinds": d["kinds"],
            "per_file": {k: round(v["ratio"], 4) for k, v in d["per_file"].items()},
        } for label, d in results.items()},
    }
    tmp = BOARD.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(board, indent=2) + "\n")
    os.replace(tmp, BOARD)

    print(f"\n{'codec':<13} {'SQUISHY×':>8} {'corpus bpb':>11}")
    print("-" * 34)
    for label, d in sorted(results.items(), key=lambda kv: -kv[1]["squishy_score"]):
        print(f"{label:<13} {d['squishy_score']:>7.2f}× {d['corpus_bpb']:>11.3f}")
    print(f"\nwrote {BOARD}  ({len(results)} codecs, complete edition)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
