"""Tests for squishy.meta modules.

Covers:
  - shannon_entropy with known inputs
  - entropy_bigram with known inputs
  - lz_match_density with known inputs
  - compression class assignment
  - assign_hazard from manifest
  - baselines.json structure
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from squishy.meta.profile import (
    shannon_entropy,
    entropy_bigram,
    lz_match_density,
    assign_compression_class,
)
from squishy.meta.manifest import assign_hazard, run as run_manifest
from squishy.core.config import BuildConfig


# ── shannon_entropy ───────────────────────────────────────────────────────────


class TestShannonEntropy:
    def test_all_same_byte(self):
        assert shannon_entropy(b'\x00' * 1000) == pytest.approx(0.0)

    def test_uniform_all_256(self):
        data = bytes(range(256)) * 4  # 1024 bytes, each byte appears 4 times
        result = shannon_entropy(data)
        assert result == pytest.approx(8.0, abs=1e-9)

    def test_two_byte_alternating(self):
        data = b'\x00\xff' * 500  # exactly half 0x00, half 0xff
        result = shannon_entropy(data)
        assert result == pytest.approx(1.0, abs=1e-9)

    def test_empty(self):
        assert shannon_entropy(b'') == 0.0

    def test_single_byte(self):
        assert shannon_entropy(b'\x42') == pytest.approx(0.0)


# ── entropy_bigram ────────────────────────────────────────────────────────────


class TestEntropyBigram:
    def test_perfectly_predictable(self):
        # b'\x00\x01' * 500 → every 0x00 is always followed by 0x01
        # and every 0x01 is always followed by 0x00 → conditional entropy = 0
        data = b'\x00\x01' * 500
        result = entropy_bigram(data)
        assert result == pytest.approx(0.0, abs=1e-9)

    def test_random_data_has_higher_entropy(self):
        # Hash-derived pseudo-random bytes have high conditional entropy (> 2.0)
        import hashlib
        chunks = [hashlib.sha256(i.to_bytes(4, "little")).digest() for i in range(128)]
        data = b"".join(chunks)
        result = entropy_bigram(data)
        assert result > 2.0

    def test_single_byte_repeated(self):
        # All same byte: every transition is self-to-self, conditional entropy = 0
        data = b'\xAB' * 1000
        assert entropy_bigram(data) == pytest.approx(0.0, abs=1e-9)

    def test_too_short(self):
        assert entropy_bigram(b'\x00') == 0.0
        assert entropy_bigram(b'') == 0.0


# ── lz_match_density ─────────────────────────────────────────────────────────


class TestLzMatchDensity:
    def test_all_zeros_high_density(self):
        data = b'\x00' * 4096
        result = lz_match_density(data)
        assert result > 0.99

    def test_random_low_density(self):
        # Use a deterministic pseudo-random sequence that mimics urandom
        import hashlib
        chunks = []
        seed = b"test-seed"
        for i in range(64):
            h = hashlib.sha256(seed + i.to_bytes(4, "little"))
            chunks.append(h.digest())
        data = b"".join(chunks)  # 2048 bytes of hash output
        result = lz_match_density(data)
        assert result < 0.1

    def test_repeating_pattern(self):
        # Highly repetitive: every 4-gram after position 4 should match
        data = b'\xAB\xCD\xEF\x01' * 1000
        result = lz_match_density(data)
        assert result > 0.95

    def test_short_data(self):
        # Too short for any grams
        assert lz_match_density(b'\x00\x01') == 0.0


# ── compression class assignment ──────────────────────────────────────────────


class TestAssignCompressionClass:
    """Test the compression_class taxonomy."""

    def _path(self, name: str) -> Path:
        return Path(f"/fake/raw/set/{name}")

    def test_silesia_manual_overrides(self):
        cases = [
            ("silesia", "dickens",  "natural-text"),
            ("silesia", "mozilla",  "binary-executable"),
            ("silesia", "mr",       "binary-media"),
            ("silesia", "nci",      "structured-text"),
            ("silesia", "ooffice",  "binary-executable"),
            ("silesia", "osdb",     "structured-binary"),
            ("silesia", "reymont",  "natural-text"),
            ("silesia", "samba",    "source-code"),
            ("silesia", "sao",      "binary-media"),
            ("silesia", "webster",  "natural-text"),
            ("silesia", "x-ray",    "binary-media"),
            ("silesia", "xml",      "structured-text"),
        ]
        for set_name, name, expected in cases:
            result = assign_compression_class(self._path(name), set_name, name, 4.5)
            assert result == expected, f"{set_name}/{name}: expected {expected}, got {result}"

    def test_squash_manual_overrides(self):
        cases = [
            ("squash", "bootstrap-3.3.6.min.css", "source-code"),
            ("squash", "eff.html",                 "natural-text"),
            ("squash", "jquery-2.1.4.min.js",      "source-code"),
            ("squash", "MG44-MathGuide.tar",        "binary-media"),
            ("squash", "random",                    "near-random"),
            ("squash", "zlib.wasm",                 "binary-executable"),
        ]
        for set_name, name, expected in cases:
            result = assign_compression_class(self._path(name), set_name, name, 4.5)
            assert result == expected, f"{set_name}/{name}: expected {expected}, got {result}"

    def test_extension_based(self):
        cases = [
            ("modern", "sample.json",     "structured-text"),
            ("modern", "sample.ndjson",   "structured-text"),
            ("modern", "sample.csv",      "structured-text"),
            ("modern", "sample.py",       "source-code"),
            ("modern", "sample.js",       "source-code"),
            ("modern", "sample.wasm",     "binary-executable"),
            ("modern", "inter.woff2",     "binary-media"),
            ("modern", "sample.parquet",  "structured-binary"),
            ("modern", "sample.arrow",    "structured-binary"),
            ("modern", "sample.sqlite",   "structured-binary"),
        ]
        for set_name, name, expected in cases:
            p = Path(f"/fake/{name}")
            result = assign_compression_class(p, set_name, name, 4.5)
            assert result == expected, f"{name}: expected {expected}, got {result}"

    def test_pathological_near_random(self):
        for name in ("urandom-1M", "urandom-10M", "random-1M"):
            result = assign_compression_class(self._path(name), "pathological", name, 7.9)
            assert result == "near-random", f"{name}: expected near-random, got {result}"

    def test_pathological_adversarial(self):
        for name in ("thue-morse-10M", "debruijn-order3", "window-zstd-128M", "mixed-entropy-blocks-2M"):
            result = assign_compression_class(self._path(name), "pathological", name, 4.5)
            assert result == "synthetic-adversarial", f"{name}: expected synthetic-adversarial, got {result}"

    def test_pathological_calibrated(self):
        for name in ("zeros-1M", "empty-0B", "tiny-13B", "page-4095B", "phrase-repeated-10M"):
            result = assign_compression_class(self._path(name), "pathological", name, 1.0)
            assert result == "synthetic-calibrated", f"{name}: expected synthetic-calibrated, got {result}"

    def test_entropy_fallback_near_random(self):
        # unknown extension, high entropy → near-random
        p = Path("/fake/raw/modern/mystery-file.bin")
        result = assign_compression_class(p, "modern", "mystery-file.bin", 7.95)
        assert result == "near-random"

    def test_entropy_fallback_natural_text(self):
        p = Path("/fake/raw/modern/doc.pdf")
        result = assign_compression_class(p, "modern", "doc.pdf", 4.5)
        assert result == "natural-text"

    def test_calibrated_set(self):
        p = Path("/fake/raw/calibrated/anything")
        result = assign_compression_class(p, "calibrated", "anything", 3.0)
        assert result == "synthetic-calibrated"


# ── assign_hazard ─────────────────────────────────────────────────────────────


class TestAssignHazard:
    def test_bomb_subdir(self):
        hazard = assign_hazard("negative/bomb/nested-zip-4levels.zip", {})
        assert hazard["class"] == "bomb"
        assert hazard["safe_to_decode_unbounded"] is False

    def test_individual_is_safe(self):
        hazard = assign_hazard("individual/silesia/dickens.gz", {})
        assert hazard["class"] == "none"
        assert hazard["safe_to_decode_unbounded"] is True

    def test_truncated_is_malformed(self):
        hazard = assign_hazard("negative/truncated/gzip-header.gz", {})
        assert hazard["class"] == "malformed"
        assert hazard["safe_to_decode_unbounded"] is False

    def test_valid_empty_is_safe(self):
        hazard = assign_hazard("negative/valid-empty/empty.gz", {})
        assert hazard["class"] == "valid-edge"
        assert hazard["safe_to_decode_unbounded"] is True

    def test_concat_is_safe(self):
        hazard = assign_hazard("negative/concat/gzip-two-members.gz", {})
        assert hazard["class"] == "concat-multi"
        assert hazard["safe_to_decode_unbounded"] is True

    def test_catalog_lookup_overrides_dir(self):
        catalog = {
            "by_path": {
                "bomb/custom.gz": {
                    "class": "bomb",
                    "severity": "high",
                    "safe_to_decode_unbounded": False,
                    "expected_decoder_outcome": "reject_or_cap",
                    "expansion_bytes_max": 1073741824,
                }
            }
        }
        hazard = assign_hazard("negative/bomb/custom.gz", catalog)
        assert hazard["expansion_bytes_max"] == 1073741824

    def test_dict_is_safe(self):
        hazard = assign_hazard("dict/json-samples.zdict", {})
        assert hazard["class"] == "none"

    def test_cve_class_is_malformed(self):
        hazard = assign_hazard("negative/cve-class/zstd-dict-oob.zst", {})
        assert hazard["class"] == "malformed"
        assert hazard["severity"] == "high"


# ── baselines.json structure ──────────────────────────────────────────────────


class TestBaselinesStructure:
    """Integration test: generate a minimal profile + manifest, run stats, check baselines."""

    def _make_cfg(self, tmpdir: str) -> BuildConfig:
        return BuildConfig(build_dir=Path(tmpdir))

    def test_baselines_has_required_keys(self, tmp_path: Path):
        cfg = BuildConfig(build_dir=tmp_path)
        meta = cfg.meta_dir
        meta.mkdir(parents=True, exist_ok=True)

        # Write a minimal manifest
        manifest = {
            "version": 2,
            "bucket": "test-bucket",
            "prefix": "squishy",
            "uncompressed_sources_published": False,
            "hazard_classes": {},
            "sources": {},
            "artifacts": [],
        }
        (meta / "manifest.json").write_text(json.dumps(manifest))

        # Write a minimal profile with all four codec ratios
        profile = {
            "version": 1,
            "generated_at": "2025-01-01T00:00:00+00:00",
            "tools": {},
            "sources": {
                "silesia/dickens": {
                    "size_uncompressed": 10192446,
                    "source_sha256": "a" * 64,
                    "entropy_bits_per_byte": 4.58,
                    "entropy_bigram": 3.21,
                    "lz_match_density_32k": 0.71,
                    "compression_class": "natural-text",
                    "representative_ratios": {
                        "gzip-9": 0.381,
                        "zstd-3": 0.347,
                        "zstd-19": 0.312,
                        "lz4": 0.423,
                    },
                },
                "silesia/mr": {
                    "size_uncompressed": 9736897,
                    "source_sha256": "b" * 64,
                    "entropy_bits_per_byte": 7.12,
                    "entropy_bigram": 6.90,
                    "lz_match_density_32k": 0.05,
                    "compression_class": "binary-media",
                    "representative_ratios": {
                        "gzip-9": 0.921,
                        "zstd-3": 0.915,
                        "zstd-19": 0.910,
                        "lz4": 0.930,
                    },
                },
            },
        }
        (meta / "profile.json").write_text(json.dumps(profile))

        from squishy.meta.stats import run as run_stats
        rc = run_stats(cfg)
        assert rc == 0

        baselines_path = meta / "baselines.json"
        assert baselines_path.exists()
        baselines = json.loads(baselines_path.read_text())

        assert baselines["version"] == 1
        assert "codecs" in baselines
        assert "sources" in baselines

        required_codecs = ["gzip-9", "zstd-3", "zstd-19", "lz4"]
        assert baselines["codecs"] == required_codecs

        for src_key, src_entry in baselines["sources"].items():
            assert "size_uncompressed" in src_entry, f"{src_key} missing size_uncompressed"
            assert "compression_class" in src_entry, f"{src_key} missing compression_class"
            for codec in required_codecs:
                assert codec in src_entry, f"{src_key} missing codec {codec}"
                codec_entry = src_entry[codec]
                assert "compressed_bytes" in codec_entry, f"{src_key}/{codec} missing compressed_bytes"
                assert "ratio" in codec_entry, f"{src_key}/{codec} missing ratio"
                assert 0.0 < codec_entry["ratio"] < 2.0, f"{src_key}/{codec} ratio out of range"

    def test_baselines_excludes_partial_sources(self, tmp_path: Path):
        """Sources missing any codec ratio must not appear in baselines."""
        cfg = BuildConfig(build_dir=tmp_path)
        meta = cfg.meta_dir
        meta.mkdir(parents=True, exist_ok=True)

        (meta / "manifest.json").write_text(json.dumps({
            "version": 2, "bucket": "b", "prefix": "p",
            "uncompressed_sources_published": False,
            "hazard_classes": {}, "sources": {}, "artifacts": [],
        }))

        profile = {
            "version": 1,
            "generated_at": "2025-01-01T00:00:00+00:00",
            "tools": {},
            "sources": {
                "modern/complete": {
                    "size_uncompressed": 1000,
                    "source_sha256": "c" * 64,
                    "entropy_bits_per_byte": 5.0,
                    "entropy_bigram": 4.0,
                    "lz_match_density_32k": 0.5,
                    "compression_class": "structured-text",
                    "representative_ratios": {
                        "gzip-9": 0.4, "zstd-3": 0.35, "zstd-19": 0.30, "lz4": 0.45,
                    },
                },
                "modern/incomplete": {
                    "size_uncompressed": 2000,
                    "source_sha256": "d" * 64,
                    "entropy_bits_per_byte": 5.0,
                    "entropy_bigram": 4.0,
                    "lz_match_density_32k": 0.5,
                    "compression_class": "structured-text",
                    "representative_ratios": {
                        "gzip-9": 0.4,
                        # missing zstd-3, zstd-19, lz4
                    },
                },
            },
        }
        (meta / "profile.json").write_text(json.dumps(profile))

        from squishy.meta.stats import run as run_stats
        run_stats(cfg)

        baselines = json.loads((meta / "baselines.json").read_text())
        assert "modern/complete" in baselines["sources"]
        assert "modern/incomplete" not in baselines["sources"]


# ── stats.json structure ──────────────────────────────────────────────────────


class TestStatsStructure:
    def test_stats_keys_present(self, tmp_path: Path):
        cfg = BuildConfig(build_dir=tmp_path)
        meta = cfg.meta_dir
        meta.mkdir(parents=True, exist_ok=True)

        artifacts = [
            {
                "path": "individual/silesia/dickens.gz",
                "size": 3976892,
                "sha256": "a" * 64,
                "content_type": "application/gzip",
                "tier": "nightly",
                "hazard": {"class": "none", "safe_to_decode_unbounded": True},
                "origin_set": "silesia",
                "origin_name": "dickens",
            },
            {
                "path": "negative/bomb/bomb.gz",
                "size": 100,
                "sha256": "b" * 64,
                "content_type": "application/gzip",
                "tier": "pr",
                "hazard": {"class": "bomb", "safe_to_decode_unbounded": False},
            },
        ]
        (meta / "manifest.json").write_text(json.dumps({
            "version": 2, "bucket": "b", "prefix": "p",
            "uncompressed_sources_published": False,
            "hazard_classes": {}, "sources": {}, "artifacts": artifacts,
        }))

        from squishy.meta.stats import run as run_stats
        rc = run_stats(cfg)
        assert rc == 0

        stats = json.loads((meta / "stats.json").read_text())
        required_keys = [
            "version", "total_sources", "total_artifacts",
            "total_uncompressed_bytes", "total_compressed_bytes",
            "by_compression_class", "by_set", "by_tier",
            "entropy_distribution", "size_distribution",
            "top_most_compressible", "top_least_compressible",
        ]
        for k in required_keys:
            assert k in stats, f"stats.json missing key: {k}"

        assert stats["total_artifacts"] == 2
        assert stats["by_tier"].get("nightly", 0) == 1
        assert stats["by_tier"].get("pr", 0) == 1
