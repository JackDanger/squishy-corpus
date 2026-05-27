"""Central build configuration.

All path derivations live here so nothing hard-codes ``build/`` anywhere else.
Pass a BuildConfig instance through every function that touches the filesystem.
"""
from __future__ import annotations

import dataclasses
import platform
from pathlib import Path


@dataclasses.dataclass(frozen=True)
class BuildConfig:
    build_dir: Path = dataclasses.field(default_factory=lambda: Path("build"))
    bucket: str = "jackdanger.com"
    prefix: str = "squishy"
    workers: int = 4

    # ── derived paths ────────────────────────────────────────────────────────

    @property
    def sources_dir(self) -> Path:
        return self.build_dir / "sources"

    @property
    def raw_dir(self) -> Path:
        return self.build_dir / "raw"

    @property
    def individual_dir(self) -> Path:
        return self.build_dir / "individual"

    @property
    def bundles_dir(self) -> Path:
        return self.build_dir / "bundles"

    @property
    def dict_dir(self) -> Path:
        return self.build_dir / "dict"

    @property
    def negative_dir(self) -> Path:
        return self.build_dir / "negative"

    @property
    def meta_dir(self) -> Path:
        return self.build_dir / "meta"

    @property
    def cache_db(self) -> Path:
        return self.build_dir / ".build-cache.db"

    @property
    def base_url(self) -> str:
        return f"https://{self.bucket}/{self.prefix}"

    @property
    def machine(self) -> str:
        return platform.machine()

    # ── stamp files (written when a pipeline stage completes fully) ──────────

    def stamp(self, stage: str) -> Path:
        return self.build_dir / f".{stage}.stamp"

    # ── factory ──────────────────────────────────────────────────────────────

    @classmethod
    def from_args(cls, args: object) -> "BuildConfig":
        """Construct from a parsed argparse Namespace."""
        return cls(
            build_dir=Path(getattr(args, "build", "build")),
            bucket=getattr(args, "bucket", "jackdanger.com"),
            prefix=getattr(args, "prefix", "squishy"),
            workers=getattr(args, "workers", 4),
        )
