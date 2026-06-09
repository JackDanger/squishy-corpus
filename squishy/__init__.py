"""Squishy corpus builder — deterministic compression test fixture pipeline."""
from importlib.metadata import version as _pkg_version, PackageNotFoundError

try:                                    # installed (wheel or editable): metadata is authoritative
    __version__ = _pkg_version("squishy")
except PackageNotFoundError:            # running from a source checkout that isn't installed
    import pathlib, tomllib
    _pyproject = pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    __version__ = tomllib.loads(_pyproject.read_text())["project"]["version"]

# No hard-coded version literal here on purpose: pyproject.toml is the one source of
# truth, and tests/test_roster_consistency.py asserts squishy.__version__ matches it.
