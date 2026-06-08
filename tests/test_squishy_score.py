"""Tests for the Squishy Score runner (scripts/squishy.py).

Guards the score math (geometric mean), the edge cases, and a golden vector so
the headline number can't silently drift.
"""
import importlib.util
import math
from pathlib import Path

import pytest

# Load scripts/squishy.py as a module (scripts/ isn't a package).
_SPEC = importlib.util.spec_from_file_location(
    "squishy_runner", Path(__file__).resolve().parent.parent / "scripts" / "squishy.py")
sq = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(sq)


# ── geomean ──────────────────────────────────────────────────────────────────

def test_geomean_basic():
    assert sq.geomean([2.0, 8.0]) == pytest.approx(4.0)        # sqrt(16)
    assert sq.geomean([1.0, 1.0, 1.0]) == pytest.approx(1.0)
    assert sq.geomean([4.0]) == pytest.approx(4.0)


def test_geomean_not_dominated_by_largest():
    # arithmetic mean would be 17.0; geomean is far lower — the whole point.
    g = sq.geomean([2.0, 2.0, 2.0, 2.0, 50.0])
    assert g < 4.0


# ── core shape ───────────────────────────────────────────────────────────────

def test_core_is_locked_count():
    # 15 after dropping `mail` (PII/license); a clean 16th may be re-added later.
    n = sum(len(v) for v in sq.CORE.values())
    assert n == 15, f"named core must be 15 files, got {n}"


def test_core_has_no_duplicates():
    sq._validate_core()  # raises on dup display name or dup (set,filename)


def test_near_incompressible_budget_is_three():
    # photo, movie, weights are the only intended incompressibles.
    names = {d for files in sq.CORE.values() for (d, _s, _n) in files}
    assert {"photo", "movie", "weights"} <= names


# ── _collect: golden vector + edge cases ─────────────────────────────────────

def _all_files():
    return [(s, n) for files in sq.CORE.values() for (_d, s, n) in files]


def _core_count():
    """Every core file counts in the score now — one vote per file, no threshold."""
    return sum(len(v) for v in sq.CORE.values())


def test_golden_vector_all_equal():
    """Every core file at ratio 4.0 → score 4.0. EVERY file counts (one vote each) —
    including the entropy-coded media; there is no compressibility gate. (The Score is a
    dimensionless geomean; there is no `bpb` field on it — corpus bpb lives elsewhere.)"""
    res = sq._collect(lambda s, n: 4.0)
    assert res["n_files"] == _core_count()             # ALL files, no exclusions
    assert res["squishy_score"] == pytest.approx(4.0)
    assert "bpb" not in res                       # the misleading 8/geomean field is gone
    assert "diagnostic_non_scored" not in res     # nothing is excluded any more
    assert res["missing"] == []
    assert res["expansions"] == []
    for cat_score in res["categories"].values():
        assert cat_score == pytest.approx(4.0)


def test_headline_is_plain_geomean_over_all_files():
    """Unequal ratios incl. a near-incompressible: the headline is the flat geomean of
    EVERY per-file ratio (one vote each), not a category-nested or gated geomean."""
    files = _all_files()
    ratios = {fn: 2.0 + i for i, fn in enumerate(files)}        # all distinct
    ratios[("corpus", "photo.jpg")] = 1.01                      # an incompressible still votes
    res = sq._collect(lambda s, n: ratios.get((s, n)))          # BOUNDS files → None (not core)
    assert res["n_files"] == len(files)                         # nothing dropped
    expected = sq.geomean(list(ratios.values()))
    assert res["squishy_score"] == pytest.approx(round(expected, 3), abs=0.001)


def test_corpus_bpb_is_byte_weighted_not_geomean_inverse():
    """corpus bpb = 8·total_out/total_in (byte-weighted), and on UNEQUAL sizes it
    must differ from 8/squishy_score — the regression guard for the old mislabel."""
    res = {}
    sq._add_byte_weighted(res, tot_in=1000, tot_out=250.0)   # equal-ratio degenerate case
    assert res["corpus_bpb"] == pytest.approx(2.0)
    assert res["total_in_bytes"] == 1000 and res["total_out_bytes"] == 250
    # unequal: a tiny highly-compressible file + a big incompressible one.
    # geomean of ratios = sqrt(100*1) = 10 → 8/geomean = 0.8; but byte-weighted bpb
    # is dominated by the big file → ~7.99, nowhere near 0.8.
    res2 = {}
    tot_in = 1_000 + 100_000_000
    tot_out = 1_000 / 100 + 100_000_000 / 1.0
    sq._add_byte_weighted(res2, tot_in, tot_out)
    assert res2["corpus_bpb"] > 7.9               # byte-weighted, honest
    assert abs(res2["corpus_bpb"] - 8.0 / 10.0) > 5  # nowhere near the geomean-inverse


def test_missing_files_are_reported_not_silently_dropped():
    files = _all_files()
    drop = {files[0], files[1]}                    # dickens, aozora
    res = sq._collect(lambda s, n: None if (s, n) in drop else 4.0)
    # two files dropped, so the counted-file total falls by exactly 2
    assert res["n_files"] == _core_count() - 2
    assert len(res["missing"]) == 2


def test_expansion_is_flagged():
    files = _all_files()
    one = files[0]
    res = sq._collect(lambda s, n: 0.97 if (s, n) == one else 4.0)
    assert len(res["expansions"]) == 1


def test_live_ratio_empty_and_missing_are_none(tmp_path):
    """Drives the real bench path: empty OR missing file → None (never ratio 0),
    keeping `bench` consistent with `board`. Regression guard for the cycle-2 bug."""
    empty = tmp_path / "empty"; empty.write_bytes(b"")
    assert sq._live_ratio("gzip -9 -c", empty) is None        # was: scored 0.0
    assert sq._live_ratio("gzip -9 -c", tmp_path / "nope") is None
    full = tmp_path / "full"; full.write_bytes(b"hello world " * 2000)
    out = sq._live_ratio("gzip -9 -c", full)
    assert out is not None and out[0] > 1.0                    # (ratio, nbytes, secs)


def test_live_ratio_file_arg_convention(tmp_path):
    """{in} placeholder routes through a temp file for codecs that need a path.
    (Not asserting equality with stdin mode: gzip stores the filename in the
    header in file mode, so the bytes differ slightly — both must just work.)"""
    full = tmp_path / "f"; full.write_bytes(b"abcabcabc" * 3000)
    via_stdin = sq._live_ratio("gzip -9 -c", full)[0]
    via_filearg = sq._live_ratio("gzip -9 -c {in}", full)[0]
    assert via_stdin > 1.0 and via_filearg > 1.0
    assert via_filearg == pytest.approx(via_stdin, rel=0.5)  # same ballpark


# ── reproducibility metadata ─────────────────────────────────────────────────

def test_panel_has_pinned_argv_and_tool():
    for codec in sq.PANEL:
        assert codec in sq.PANEL_ARGV, f"{codec} missing pinned argv"
        assert codec in sq.PANEL_TOOL, f"{codec} missing tool mapping"


# ── round-trip (losslessness) ────────────────────────────────────────────────

def test_round_trip_ok_detects_lossless_and_lossy():
    data = b"the quick brown fox " * 500
    assert sq.round_trip_ok("gzip -9 -c", "gzip -dc", data) is True
    assert sq.round_trip_ok("gzip -9 -c", "cat", data) is False   # cat ≠ decompress


# ── golden board: the published JSON matches the score definition ────────────

import json  # noqa: E402

_SCORES = Path(__file__).resolve().parent.parent / "build" / "meta" / "squishy-scores.json"


_COMPLETE = Path(__file__).resolve().parent.parent / "build" / "meta" / "squishy-score-complete.json"


def _published_board():
    if not _SCORES.exists():
        pytest.skip("no published squishy-scores.json")
    return json.loads(_SCORES.read_text())


def _assert_tool_provenance(t):
    """Every scored tool must carry a portable identity — release version (or a git
    sha for a non-release build) + architecture — so any cross-system score variance
    has provenance. The install path is deliberately NOT recorded (host-specific)."""
    for k in ("tool", "argv", "version", "git_sha", "release", "arch"):
        assert k in t, f"tool_provenance missing {k}"
    assert "path" not in t and "sha256" not in t, "host-specific path/binary-sha must not be recorded"
    assert t["version"], "tool version must be recorded"
    assert t["release"] or t["git_sha"], "a non-release build must record its git sha"
    assert t["arch"], "tool architecture must be recorded"


def test_scored_artifacts_record_tool_and_host_provenance():
    """Board rows AND the complete-edition score must pin the exact tool (version,
    binary sha256, arch) and the host machine that produced them."""
    board = _published_board()
    host = board.get("host_provenance", {})
    assert host.get("machine") and host.get("platform"), "board must record host machine/platform"
    for codec, row in board["panel"].items():
        assert "tool_provenance" in row, f"{codec}: no tool_provenance"
        _assert_tool_provenance(row["tool_provenance"])
    if _COMPLETE.exists():
        d = json.loads(_COMPLETE.read_text())
        _assert_tool_provenance(d["tool_provenance"])
        assert d["host_provenance"].get("machine"), "complete-edition score must record host arch"


def test_published_board_is_internally_consistent():
    """Each codec's headline must equal the PLAIN geomean of every per-file ratio (one
    vote per file — no category nesting, no compressibility gate), and bpb is byte-weighted."""
    board = _published_board()
    core_displays = {d for c, files in sq.CORE.items() for (d, _s, _n) in files}
    for codec, row in board["panel"].items():
        pf = row.get("per_file", {})
        assert set(pf) == core_displays, f"{codec}: per_file keys ≠ the core files"
        # headline = flat geomean over EVERY file, incompressibles included.
        flat = sq.geomean(list(pf.values()))
        assert flat == pytest.approx(row["squishy_score"], abs=0.01), \
            f"{codec}: stored {row['squishy_score']} ≠ plain geomean {flat:.3f}"
        # no vestigial geomean-inverse "bpb"; corpus_bpb is byte-weighted + self-consistent
        assert "bpb" not in row, f"{codec}: stale 8/geomean 'bpb' field must be gone"
        assert row["corpus_bpb"] == pytest.approx(
            8.0 * row["total_out_bytes"] / row["total_in_bytes"], abs=0.01), \
            f"{codec}: corpus_bpb ≠ byte-weighted 8·out/in"


@pytest.mark.slow
def test_published_gzip_row_matches_live_bytes():
    """Real-bytes regression guard: recompute gzip -9 over the actual core and
    confirm it reproduces the published gzip row (per-file + headline)."""
    board = _published_board()
    if "gzip -9" not in board["panel"]:
        pytest.skip("no gzip row")
    if sq.verify_core_checksums():
        pytest.skip("core bytes not present/verified")
    want = board["panel"]["gzip -9"]["per_file"]
    for display, s, name in [(d, s, n) for files in sq.CORE.values() for (d, s, n) in files]:
        p = sq.raw_path(s, name)
        if not p.exists():
            pytest.skip(f"{display} bytes absent")
        live = sq._live_ratio("gzip -9 -c", p)[0]
        assert live == pytest.approx(want[display], rel=0.001), \
            f"{display}: live {live:.3f} ≠ published {want[display]}"
