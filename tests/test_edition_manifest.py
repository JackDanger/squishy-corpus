"""Guards build/meta/edition.json against drift: it must list exactly the CORE
files (plus scale tier), every file URL-addressable + sha256-pinned, base-relative
keys composing with base_url, and never a retired-corpus path."""
import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
sq = importlib.util.module_from_spec(_SPEC); _SPEC.loader.exec_module(sq)
_MAN = REPO / "build" / "meta" / "edition.json"


def _manifest():
    if not _MAN.exists():
        pytest.skip("no edition.json (run scripts/build-edition-manifest.py)")
    return json.loads(_MAN.read_text())


def test_core_files_all_present_exactly():
    m = _manifest()
    core_displays = {d for files in sq.CORE.values() for (d, _s, _n) in files}
    manifest_core = {f["display"] for f in m["files"] if f["tier"] == "core"}
    assert manifest_core == core_displays, f"manifest core ≠ CORE: {manifest_core ^ core_displays}"


def test_every_file_is_addressable_and_pinned():
    m = _manifest()
    for f in m["files"]:
        assert f["sha256"] and len(f["sha256"]) == 64, f"{f['name']}: bad sha256"
        assert f["url"] == f"{m['base_url']}/{f['key']}", f"{f['name']}: url≠base/key"
        assert f["size_bytes"] and f["size_bytes"] > 0, f"{f['name']}: no size"
        assert f["tier"] in ("core", "scale")


def test_no_retired_corpus_paths():
    m = _manifest()
    retired = ("individual/", "bundle/", "bundles/", "dict/", "negative/", "raw/", "modern/")
    for f in m["files"]:
        assert not any(r in f["key"] for r in retired), f"retired path leaked: {f['key']}"
