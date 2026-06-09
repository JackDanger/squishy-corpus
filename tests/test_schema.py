"""Guards build/meta/schema.json — the corpus CONSTITUTION — and its agreement with
the edition manifest.

With a flat geomean (one vote per file) the roster IS the formula, so the roster has
its own small formula in schema.json: cells (scored votes) with roles {kind, length,
incompressible}, plus declared budgets. These tests are the backstop that makes the
score's balance/independence enforced by code instead of by curation discipline. They
read only committed metadata (never build/raw/*)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
META = REPO / "build" / "meta"


def _read(name: str):
    p = META / name
    if not p.exists():
        pytest.skip(f"no {name}")
    return json.loads(p.read_text())


def _schema():
    return _read("schema.json")


def _edition():
    return _read("edition.json")


# ── schema internal consistency ────────────────────────────────────────────────

def test_cell_ids_and_files_unique():
    s = _schema()
    ids = [c["id"] for c in s["cells"]]
    files = [c["file"] for c in s["cells"]]
    assert len(ids) == len(set(ids)), f"duplicate cell id: {[i for i in ids if ids.count(i) > 1]}"
    assert len(files) == len(set(files)), "two cells claim the same file"
    diag_files = [d["file"] for d in s["diagnostics"]]
    assert not (set(files) & set(diag_files)), "a file is both a cell and a diagnostic"


def test_categories_are_declared():
    s = _schema()
    declared = set(s["categories"])
    for c in s["cells"]:
        assert c["category"] in declared, f"cell {c['id']}: undeclared category {c['category']!r}"


def test_roles_are_valid():
    s = _schema()
    for c in s["cells"]:
        assert c["role"] in ("kind", "length", "incompressible"), f"{c['id']}: bad role {c['role']!r}"


def test_kind_cells_are_lineage_unique():
    """The anti-gaming independence rule, applied where it belongs: no two KIND cells
    share a lineage. (Length cells deliberately re-sample their kind's lineage and are
    exempt — that is the point of the size axis.)"""
    s = _schema()
    lineages = [c["lineage"] for c in s["cells"] if c["role"] == "kind"]
    dupes = [l for l, n in Counter(lineages).items() if n > 1]
    assert not dupes, f"kind cells share a lineage (independence violation): {dupes}"


def test_length_cells_bind_to_a_kind_cell():
    """Every length cell names the kind cell it scales (`scales`), that cell exists,
    is role=kind, and shares its `kind`. Lineage need NOT match (a same-kind
    different-source rung like markup-L=enwik9 is permitted)."""
    s = _schema()
    by_id = {c["id"]: c for c in s["cells"]}
    for c in s["cells"]:
        if c["role"] != "length":
            continue
        assert "scales" in c, f"length cell {c['id']} has no `scales`"
        parent = by_id.get(c["scales"])
        assert parent is not None, f"{c['id']}: scales unknown cell {c['scales']!r}"
        assert parent["role"] == "kind", f"{c['id']}: scales a non-kind cell {parent['id']}"
        assert parent["kind"] == c["kind"], (
            f"{c['id']}: kind {c['kind']!r} ≠ scaled cell kind {parent['kind']!r}")


def test_at_most_one_length_cell_per_kind():
    s = _schema()
    length_kinds = [c["kind"] for c in s["cells"] if c["role"] == "length"]
    dupes = [k for k, n in Counter(length_kinds).items() if n > 1]
    assert not dupes, f"kind has >1 length cell (caps votes/kind at 2): {dupes}"


def test_votes_per_kind_within_budget():
    s = _schema()
    cap = s["budgets"]["votes_per_kind_max"]
    per_kind = Counter(c["kind"] for c in s["cells"])
    over = {k: n for k, n in per_kind.items() if n > cap}
    assert not over, f"kinds over the {cap}-vote budget: {over}"


def test_budget_caps_hold():
    s = _schema()
    b = s["budgets"]
    n_length = sum(1 for c in s["cells"] if c["role"] == "length")
    n_incomp = sum(1 for c in s["cells"] if c["role"] == "incompressible")
    assert n_length <= b["length_cells_max"], f"{n_length} length cells > cap {b['length_cells_max']}"
    assert n_incomp <= b["incompressible_cells_max"], (
        f"{n_incomp} incompressible cells > cap {b['incompressible_cells_max']}")


def test_category_vote_envelope_matches_declared():
    """The declared per-category vote count is a tested invariant — the roster cannot
    silently drift (the bug that let Binary & Media balloon to 9 votes unnoticed)."""
    s = _schema()
    declared = {k: v for k, v in s["budgets"]["category_votes"].items() if not k.startswith("$")}
    actual = Counter(c["category"] for c in s["cells"])
    assert dict(actual) == declared, (
        f"category vote drift — declared {dict(declared)} ≠ actual {dict(actual)}; "
        f"update schema.json budgets deliberately if this is intended")


# ── schema ⇄ edition agreement (the scorer reads the edition) ───────────────────

def test_edition_scored_set_equals_schema_cells():
    s, ed = _schema(), _edition()
    cell_files = {c["file"] for c in s["cells"]}
    scored_files = {f["name"] for f in ed["files"] if f["scored"]}
    assert scored_files == cell_files, (
        f"edition scored set ≠ schema cells: {scored_files ^ cell_files}")


def test_edition_diagnostics_never_scored():
    s, ed = _schema(), _edition()
    diag_files = {d["file"] for d in s["diagnostics"]}
    for f in ed["files"]:
        if f["name"] in diag_files:
            assert not f["scored"], f"diagnostic {f['name']} is marked scored"


def test_edition_roles_and_lineage_match_schema():
    s, ed = _schema(), _edition()
    by_file = {c["file"]: c for c in s["cells"]}
    for f in ed["files"]:
        if not f["scored"]:
            continue
        c = by_file[f["name"]]
        assert f["role"] == c["role"], f"{f['name']}: edition role {f['role']!r} ≠ schema {c['role']!r}"
        assert f["lineage"] == c["lineage"], f"{f['name']}: lineage drift"
        assert f["cell"] == c["id"], f"{f['name']}: cell id drift"


def test_scorer_sees_exactly_the_cells():
    """The scoring entry point (scored_corpus) must enumerate exactly the schema cells —
    closes the old bug where the scored set was inferred from a stray `entropy` key."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("sq_schema", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(spec); spec.loader.exec_module(sq)
    sc = sq.scored_corpus()
    scored_displays = {f["display"] for kinds in sc.values() for pts in kinds.values() for f in pts}
    cell_ids = {c["id"] for c in _schema()["cells"]}
    assert scored_displays == cell_ids, f"scorer roster ≠ schema cells: {scored_displays ^ cell_ids}"
