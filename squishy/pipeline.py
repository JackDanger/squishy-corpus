"""Build pipeline orchestration.

Each stage is a function that takes a BuildConfig, does its work, and returns
an exit code (0 = success).  Stages are skipped if their stamp file exists,
unless ``force=True``.  ``clean=True`` wipes the content-addressed cache
before running (not the output files — those are left in place until
explicitly rebuilt).

Dependency order:
    sources → raw → individual → bundles → dict → negative
            → profile → manifest → stats
"""
from __future__ import annotations

import time
from pathlib import Path

from squishy.core.cache import wipe as cache_wipe
from squishy.core.config import BuildConfig

# Ordered list of stage names; "all" runs them in this order.
STAGES = [
    "sources",
    "raw",
    "individual",
    "bundles",
    "dict",
    "negative",
    "profile",
    "manifest",
    "stats",
]


def run_build(
    target: str,
    cfg: BuildConfig,
    *,
    force: bool = False,
    clean: bool = False,
) -> int:
    if clean:
        cache_wipe(cfg.cache_db)
        print("build cache wiped")

    if target == "all":
        for stage in STAGES:
            rc = _run_stage(stage, cfg, force=force)
            if rc != 0:
                return rc
        return 0

    return _run_stage(target, cfg, force=force)


def _run_stage(stage: str, cfg: BuildConfig, *, force: bool) -> int:
    stamp = cfg.stamp(stage)
    if not force and stamp.exists():
        print(f"  {stage}: up to date")
        return 0

    print(f"  {stage}: running…")
    t0 = time.monotonic()
    rc = _dispatch(stage, cfg)
    elapsed = time.monotonic() - t0

    if rc == 0:
        stamp.touch()
        print(f"  {stage}: done ({elapsed:.1f}s)")
    else:
        print(f"  {stage}: FAILED (rc={rc})")
    return rc


def _dispatch(stage: str, cfg: BuildConfig) -> int:
    if stage == "sources":
        from squishy.generators.sources import run
        return run(cfg)

    if stage == "raw":
        from squishy.generators.pathological import run as run_patho
        from squishy.generators.modern import run as run_modern
        from squishy.generators.calibrated import run as run_cal
        from squishy.generators.logs import run as run_logs
        from squishy.generators.markov import run as run_markov
        from squishy.generators.lz77_synth import run as run_lz77
        from squishy.generators.periodic import run as run_periodic
        for fn in (run_patho, run_modern, run_cal, run_logs,
                   run_markov, run_lz77, run_periodic):
            rc = fn(cfg)
            if rc != 0:
                return rc
        return 0

    if stage == "individual":
        from squishy.compress.individual import run
        return run(cfg)

    if stage == "bundles":
        from squishy.compress.bundles import run
        return run(cfg)

    if stage == "dict":
        from squishy.compress.dict_ import run
        return run(cfg)

    if stage == "negative":
        from squishy.generators.negative import run
        return run(cfg)

    if stage == "profile":
        from squishy.meta.profile import run
        return run(cfg)

    if stage == "manifest":
        from squishy.meta.manifest import run
        return run(cfg)

    if stage == "stats":
        from squishy.meta.stats import run
        return run(cfg)

    print(f"unknown stage: {stage}")
    return 1
