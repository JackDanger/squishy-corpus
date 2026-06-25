"""Cross-file roster-consistency guards.

The Squishy corpus roster (the set of core members and their per-member facts) is
duplicated across many files. Historically a change updated some copies and silently
missed others, producing real bugs:

  • adding members updated scripts/squishy.py CORE but missed build-site.py WHATIS,
    then missed publish-corpus.py RECIPES (so `make mint` couldn't mint them);
  • scripts/validate-core.py SNIFF kept a removed `mail` member and lacked the four
    new Binary & Media members (a latent KeyError when run);
  • build/meta/LICENSE-MANIFEST.csv rows were appended with unquoted commas, so a
    row had 9 fields instead of 8 and nobody noticed.

These tests fail if any of those copies drift apart. They are the single guard that
the duplicated facts agree. They must stay robust to the known limitation that the
raw corpus bytes are not present locally (so they only read the committed metadata,
never build/raw/*).
"""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
META = REPO / "build" / "meta"


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sq = _load("sq_roster", "scripts/squishy.py")
pc = _load("pc_roster", "scripts/publish-corpus.py")
bs = _load("bs_roster", "scripts/build-site.py")
cm = _load("cm_roster", "scripts/coverage-map.py")


def _core_keys() -> set[str]:
    return {f"{s}/{n}" for files in sq.CORE.values() for (_d, s, n) in files}


def _core_displays() -> set[str]:
    return {d for files in sq.CORE.values() for (d, _s, _n) in files}


def _core_filenames() -> set[str]:
    return {n for files in sq.CORE.values() for (_d, _s, n) in files}


def _read_json(name: str):
    p = META / name
    if not p.exists():
        pytest.skip(f"no {name}")
    return json.loads(p.read_text())


def _manifest_rows() -> tuple[list[str], list[list[str]]]:
    p = META / "LICENSE-MANIFEST.csv"
    if not p.exists():
        pytest.skip("no LICENSE-MANIFEST.csv")
    rows = list(csv.reader(p.open(encoding="utf-8")))
    return rows[0], rows[1:]


# ── the spine: CORE is the source of truth for every other roster ──────────────

def test_core_matches_recipes():
    """Every CORE member has a publish-corpus RECIPES entry (so `make mint`/publish
    knows how to produce it) and vice-versa — no orphan recipes for the corpus/ tier."""
    core = _core_keys()
    recipes_corpus = {k for k in pc.RECIPES if k.startswith("corpus/")}
    assert core == recipes_corpus, f"CORE ⇄ RECIPES drift: {core ^ recipes_corpus}"


def test_every_recipe_has_valid_origin():
    for key, rec in pc.RECIPES.items():
        assert rec.get("origin") in ("upstream", "minted"), f"{key}: bad origin {rec.get('origin')!r}"
        if rec["origin"] == "upstream":
            assert rec.get("how"), f"{key}: upstream member needs a 'how' recipe"
        else:  # minted: either a generator recipe or a 'note' explaining the kept copy
            assert rec.get("gen") or rec.get("note"), f"{key}: minted member needs 'gen' or 'note'"


def test_checksums_cover_every_distributed_file():
    """CHECKSUMS.sha256 is the published trust-root: it must list EVERY distributed
    file — the named core AND the scale tier (all of edition.json), so the one-line
    downloader can verify the whole edition, not just the core."""
    p = META / "CHECKSUMS.sha256"
    if not p.exists():
        pytest.skip("no CHECKSUMS.sha256")
    keys = set()
    for line in p.read_text().splitlines():
        parts = line.split()
        if len(parts) == 2:
            assert len(parts[0]) == 64, f"bad sha length: {line!r}"
            keys.add(parts[1])
    ed = _read_json("edition.json")
    want = {f["key"] for f in ed["files"]}
    assert keys == want, f"CHECKSUMS ⇄ edition drift: {keys ^ want}"


def test_core_matches_file_properties():
    fp = _read_json("file-properties.json")["files"]
    assert set(fp) == _core_displays(), f"file-properties ⇄ CORE drift: {set(fp) ^ _core_displays()}"


def test_core_matches_edition():
    """The small named members (under corpus/) are exactly CORE, and all are scored."""
    ed = _read_json("edition.json")
    keys = {f["key"] for f in ed["files"] if f["key"].startswith("corpus/")}
    disp = {f["display"] for f in ed["files"] if f["key"].startswith("corpus/")}
    assert keys == _core_keys(), f"edition core keys ⇄ CORE drift: {keys ^ _core_keys()}"
    assert disp == _core_displays(), f"edition core displays ⇄ CORE drift: {disp ^ _core_displays()}"


def test_edition_category_matches_core():
    ed = _read_json("edition.json")
    core_cat = {d: cat for cat, files in sq.CORE.items() for (d, _s, _n) in files}
    for f in ed["files"]:
        if f["key"].startswith("corpus/"):
            assert f["category"] == core_cat[f["display"]], (
                f"{f['display']}: edition category {f['category']!r} ≠ CORE {core_cat[f['display']]!r}")


def test_edition_origin_matches_recipes():
    """The provenance class in the manifest must agree with publish-corpus RECIPES
    for every key — they are the two halves of the mint/publish contract."""
    ed = _read_json("edition.json")
    for f in ed["files"]:
        rec = pc.RECIPES.get(f["key"])
        if rec is None:
            continue  # not all scale fixtures carry a recipe
        if "origin" in f:
            assert f["origin"] == rec["origin"], (
                f"{f['key']}: edition origin {f['origin']!r} ≠ recipe {rec['origin']!r}")


# ── build-site.py presentation rosters ─────────────────────────────────────────

def test_core_matches_whatis():
    assert set(bs.WHATIS) == _core_displays(), f"WHATIS ⇄ CORE drift: {set(bs.WHATIS) ^ _core_displays()}"


def test_core_displays_have_short_labels():
    missing = _core_displays() - set(bs.SHORT)
    assert not missing, f"SHORT missing core labels: {missing}"


# ── validate-core.py SNIFF roster (caught the stale `mail` / missing-member bug) ─

def test_core_matches_sniff():
    vc = _load("vc_roster", "scripts/validate-core.py")
    assert set(vc.SNIFF) == _core_displays(), (
        f"validate-core SNIFF ⇄ CORE drift (would KeyError or skip a member): "
        f"{set(vc.SNIFF) ^ _core_displays()}")


# ── LICENSE-MANIFEST.csv well-formedness (caught the unquoted-comma bug) ────────

def test_license_manifest_is_well_formed():
    header, rows = _manifest_rows()
    for i, row in enumerate(rows, start=2):
        assert len(row) == len(header), (
            f"LICENSE-MANIFEST.csv line {i}: {len(row)} fields, expected {len(header)} "
            f"(an unquoted comma in a field would do this): {row!r}")


def test_license_manifest_covers_every_core_member():
    header, rows = _manifest_rows()
    name_i = header.index("name")
    slot_i = header.index("core_slot")
    by_name = {r[name_i]: r for r in rows}
    # every core file has a manifest row, and its slot equals its display (kind)
    core_disp_by_name = {n: d for files in sq.CORE.values() for (d, _s, n) in files}
    for name, disp in core_disp_by_name.items():
        assert name in by_name, f"LICENSE-MANIFEST.csv missing core file: {name}"
        assert by_name[name][slot_i] == disp, (
            f"{name}: manifest core_slot {by_name[name][slot_i]!r} ≠ CORE display {disp!r}")
    # no core_slot collisions for the scored core (would double-map a member)
    core_slots = [r[slot_i] for r in rows if not r[slot_i].startswith("scale-")]
    assert len(core_slots) == len(set(core_slots)), "duplicate core_slot in LICENSE-MANIFEST.csv"


# ── colour palette: build-site CUBE_COLORS vs coverage-map CAT_COLOR ────────────

def test_category_palette_agrees():
    assert bs.CUBE_COLORS == cm.CAT_COLOR, (
        f"category palette drift between build-site.py and coverage-map.py: "
        f"{set(bs.CUBE_COLORS.items()) ^ set(cm.CAT_COLOR.items())}")


def test_category_order_matches_palette_and_core():
    assert set(sq.CATEGORY_ORDER) == set(bs.CUBE_COLORS), "CATEGORY_ORDER ⇄ palette drift"
    assert set(sq.CATEGORY_ORDER) == set(sq.CORE), "CATEGORY_ORDER ⇄ CORE categories drift"


# ── scale-kind → category map: coverage-map vs build-edition-manifest ───────────

def test_scale_category_maps_agree():
    """coverage-map's kind→category colouring must agree with schema.json (the roster's
    category source of truth) for every kind they share."""
    schema = _read_json("schema.json")
    kind_cat = {c["kind"]: c["category"] for c in schema["cells"]}
    shared = set(cm.KIND_CATEGORY) & set(kind_cat)
    for k in shared:
        assert cm.KIND_CATEGORY[k] == kind_cat[k], (
            f"kind {k!r}: coverage-map {cm.KIND_CATEGORY[k]!r} ≠ schema {kind_cat[k]!r}")


def test_coverage_map_colours_every_scale_kind():
    p = META / "scale-properties.json"
    if not p.exists():
        pytest.skip("no scale-properties.json")
    files = json.loads(p.read_text()).get("files", {})
    for fname, m in files.items():
        key = m.get("key", "")
        # edition-relative key: scale/<kind>/<name> (never the draft/ working prefix)
        parts = key.split("/")
        if len(parts) < 3 or parts[0] != "scale":
            continue
        kind = parts[1]
        assert kind in cm.KIND_CATEGORY, (
            f"scale kind {kind!r} (from {fname}) has no colour in coverage-map.KIND_CATEGORY "
            f"→ would render in the default grey")


# ── reference panel: PANEL / PANEL_ARGV / PANEL_TOOL vs tools.lock ──────────────

def test_panel_dicts_have_identical_keys():
    assert set(sq.PANEL) == set(sq.PANEL_ARGV) == set(sq.PANEL_TOOL), (
        "PANEL / PANEL_ARGV / PANEL_TOOL key drift: "
        f"{set(sq.PANEL) ^ set(sq.PANEL_ARGV) ^ set(sq.PANEL_TOOL)}")


def test_panel_tools_are_recorded_in_tools_lock():
    lock = REPO / "build" / "tools.lock"
    if not lock.exists():
        pytest.skip("no tools.lock")
    tools = set()
    for line in lock.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        tools.add(line.split()[0])
    missing = set(sq.PANEL_TOOL.values()) - tools
    assert not missing, f"panel codecs missing from tools.lock: {missing}"


# ── package version: one source of truth (pyproject), no drift ──────────────────

def test_version_is_single_source_of_truth():
    """squishy.__version__ derives from pyproject.toml; there must be no second,
    hand-maintained version literal that can drift (e.g. __init__ once said 2.0.0
    while pyproject said 0.1.0)."""
    import tomllib

    sqpkg = _load("sq_pkg_version", "squishy/__init__.py")
    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    assert sqpkg.__version__ == pyproject, (
        f"squishy.__version__ ({sqpkg.__version__}) != pyproject version ({pyproject}) — "
        f"run `uv sync`/reinstall, or remove a stray hard-coded __version__"
    )
    # sane semver-ish (N.N.N), so a typo can't slip through
    parts = pyproject.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), f"not a clean MAJOR.MINOR.PATCH: {pyproject}"
