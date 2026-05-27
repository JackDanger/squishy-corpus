#!/usr/bin/env python3
"""Generate LLM-agent-oriented discovery files for the Squishy Corpus.

Writes:
  build/meta/AGENTS.md
  build/meta/agent.json
  build/meta/robots.txt
  build/meta/llms.txt
  build/meta/smoke.zip
  build/meta/<dirname>-index.json  (one per top-level subdir in manifest)

Usage:
  python3 scripts/gen-agent-docs.py \
    --meta build/meta \
    --build build \
    --bucket jackdanger.com \
    --prefix squishy
"""
from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

# ─── helpers ─────────────────────────────────────────────────────────────────

def human_size(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if n < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TiB"  # unreachable but satisfies type checker


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    tmp.rename(path)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2) + "\n", encoding="utf-8")
    tmp.rename(path)


# ─── AGENTS.md ────────────────────────────────────────────────────────────────

AGENTS_MD_TEMPLATE = """\
# Squishy Corpus — Agent Guide

Base URL: {base_url}/

## Overview

Test fixtures for compression and decompression libraries. Pre-compressed in every common format. Served from CloudFront with stable, immutable URLs.

Sets: silesia (2003 text/binary/medical), squash (2015 web files), modern (generated JSON/Parquet/Arrow/WASM/protobuf/UTF-8), pathological (edge cases).

Total artifacts: {total_count}. Total size: {total_size_human}.

## Hazards — READ FIRST

`negative/` contains intentionally malformed and hazardous files. Do NOT pass to a decoder without:
- Output size cap: 1 GiB recommended
- Wall-clock timeout: 30 seconds recommended
- Nesting depth limit: 2 recommended

Specific hazards:
- `negative/bomb/nested-zip-4levels.zip` — expands to ~10 GiB (4-level nested zip bomb)
- `negative/bomb/bomb-gz-10MiB-to-zeros.gz` — expands to 10 MiB zeros
- `negative/bomb/bomb-gz-1MiB-to-zeros.gz` — expands to 1 MiB zeros
- `negative/declared-length/zstd-fcs-10gib-empty.zst` — declares 10 GiB FCS, empty body
- `negative/cve-class/` — fixtures shaped like real-world CVEs
- `negative/bitflip/` — single-byte corruptions (decoders should reject)
- `negative/truncated/` — truncated at sensitive offsets (decoders should reject)

Safe negative fixtures (decoders should accept):
- `negative/valid-empty/` — minimal valid empty streams
- `negative/concat/` — valid multi-member concatenated streams
- `negative/zstd-skipframe-only/` — valid skippable-frame-only stream

Each negative file has a sidecar `<path>.hazard.json` with machine-readable metadata.

## What is NOT published

Uncompressed source bytes are not on S3. There is no `individual/silesia/dickens` — only compressed variants.

**Canonical uncompressed delivery**: `individual/<set>/<file>.gz` (gzip -9, deterministic). Decompress client-side for raw bytes. Example:
```
curl -s {base_url}/individual/silesia/dickens.gz | gzip -d > dickens
```

## Canonical URL patterns

```
{base_url}/individual/<set>/<file>.<codec>          # per-file per-codec
{base_url}/individual/<set>/<file>.<codec>.l<N>     # leveled variant
{base_url}/bundles/<set>/<set>.<ordering>.<format>  # bundle archives
{base_url}/negative/<category>/<file>               # hazardous fixtures
{base_url}/negative/<category>/<file>.hazard.json   # per-file hazard metadata
{base_url}/dict/json-samples.zdict                  # trained zstd dictionary
```

Sets: `silesia`, `squash`, `modern`, `pathological`
Orderings: `alpha`, `random`, `size-desc`
Formats: `tar.gz`, `tar.xz`, `tar.zst`, `tar.bz2`, `tar.lz4`, `tar.br`, `tar.lzma`, `7z.lzma2`, `7z.ppmd`, `7z.bzip2`, `7z.deflate`, `squashfs.gzip`, `squashfs.xz`, `squashfs.lz4`, `squashfs.zstd`, `zip.deflate`, `zip.bzip2`, `zip.lzma`, `concat-gz`, `concat-xz`, `concat-zst`, `concat-zst-skipframes`

## Tiers

Every artifact has a `tier` field in manifest.json:
- `pr` — ~50 MiB total. Right for per-commit CI.
- `nightly` — ~500 MiB. Right for daily runs.
- `full` — several GiB. Right for release validation.

## Recipe: safe smoke test (one URL)

```bash
curl -sO {base_url}/smoke.zip && unzip -q smoke.zip -d smoke/
# smoke/ contains one file per major codec, AGENTS.md, and manifest.smoke.json
```

## Recipe: pr tier, safe only

```bash
curl -s {base_url}/manifest.safe.json \\
  | python3 -c "
import json,sys
m=json.load(sys.stdin)
for a in m['artifacts']:
    if a['tier']=='pr':
        print(a['path'])
" | xargs -I{{}} curl -sO {base_url}/{{}}
```

## Recipe: check a download

```bash
curl -sO {base_url}/individual/silesia/dickens.gz
curl -s {base_url}/CHECKSUMS.sha256 | grep individual/silesia/dickens.gz | sha256sum -c
```

## Recipe: round-trip test

```bash
# decode-expectations.json tells you what sha256 the decoder should produce
curl -s {base_url}/decode-expectations.json \\
  | python3 -c "
import json,sys
m=json.load(sys.stdin)
for path,exp in m['expectations'].items():
    if exp.get('should_succeed') is True and 'decoded_sha256' in exp:
        print(path, exp['decoded_sha256'], exp['decoded_size'])
" | head -20
```

## Schema: manifest.json

```json
{{
  "version": 2,
  "bucket": "jackdanger.com",
  "prefix": "squishy",
  "uncompressed_sources_published": false,
  "hazard_classes": {{ ... }},
  "sources": {{ "silesia/dickens": {{ "canonical_delivery": "individual/silesia/dickens.gz", ... }} }},
  "artifacts": [
    {{
      "path": "individual/silesia/dickens.gz",
      "size": 3976892,
      "sha256": "...",
      "content_type": "application/gzip",
      "tier": "nightly",
      "description": "Silesia: collected English novels (Charles Dickens)",
      "hazard": {{"class": "none", "safe_to_decode_unbounded": true}},
      "codec": "gzip",
      "codec_level": null,
      "container": null,
      "origin_set": "silesia",
      "origin_name": "dickens"
    }}
  ]
}}
```

## Index files

| File | Format | Cache | Purpose |
|------|--------|-------|---------|
| manifest.json | JSON v2 | 5 min | Full metadata, agent-queryable |
| manifest.safe.json | JSON v2 | 5 min | Safe-only subset (no bombs/malformed) |
| index.txt | TSV | 5 min | sha256+size+type+tier+path+description+hazard_class |
| decode-expectations.json | JSON | 5 min | Per-artifact decoder oracle |
| CHECKSUMS.sha256 | sha256sum | 5 min | GNU-compatible checksums |
| expected-ratio.json | JSON | 5 min | Known compressed sizes for encoder regression |
| _INDEX.json | JSON | 5 min | Per-directory listing (available in each subdir) |
| smoke.zip | ZIP | immutable | ~5 MiB onboarding bundle with AGENTS.md embedded |

## Glossary

- **codec level**: compression level (e.g. `.zst.l22` = zstd at level 22, `.gz.l9` = gzip at level 9)
- **concat-***: files compressed independently per-member, frames concatenated, no tar wrapper
- **solid archive**: all files compressed as a single stream (7z, squashfs) — ordering affects ratio
- **hazard class**: `none` | `bomb` | `malformed` | `concat-multi` | `valid-edge`
- **tier**: `pr` | `nightly` | `full` — controls which CI jobs pull this artifact
"""


def gen_agents_md(base_url: str, total_count: int, total_size_bytes: int) -> str:
    return AGENTS_MD_TEMPLATE.format(
        base_url=base_url,
        total_count=total_count,
        total_size_human=human_size(total_size_bytes),
    )


# ─── agent.json ──────────────────────────────────────────────────────────────

def gen_agent_json(
    base_url: str,
    artifacts: list[dict],
) -> dict:
    total_artifacts = len(artifacts)
    total_size_bytes = sum(a.get("size", 0) for a in artifacts)

    tier_counts: dict[str, int] = {}
    for a in artifacts:
        t = a.get("tier", "full")
        tier_counts[t] = tier_counts.get(t, 0) + 1

    sets_seen: list[str] = []
    seen_set: set[str] = set()
    for a in artifacts:
        s = a.get("origin_set") or ""
        if s and s not in seen_set:
            sets_seen.append(s)
            seen_set.add(s)

    # Ensure canonical order for sets
    canonical_sets = ["silesia", "squash", "modern", "pathological"]
    sets_out = [s for s in canonical_sets if s in seen_set]
    for s in sets_seen:
        if s not in seen_set or s not in canonical_sets:
            sets_out.append(s)

    return {
        "version": 1,
        "corpus": "squishy",
        "base_url": base_url,
        "human_doc": f"{base_url}/README.txt",
        "agent_doc": f"{base_url}/AGENTS.md",
        "manifest": f"{base_url}/manifest.json",
        "manifest_safe": f"{base_url}/manifest.safe.json",
        "decode_expectations": f"{base_url}/decode-expectations.json",
        "smoke_bundle": f"{base_url}/smoke.zip",
        "index_txt": f"{base_url}/index.txt",
        "checksums": f"{base_url}/CHECKSUMS.sha256",
        "uncompressed_sources_published": False,
        "recommended_default_tier": "pr",
        "total_artifacts": total_artifacts,
        "total_size_bytes": total_size_bytes,
        "hazardous_prefixes": [
            "negative/bomb/",
            "negative/bitflip/",
            "negative/truncated/",
            "negative/cve-class/",
            "negative/declared-length/",
            "negative/concat-mixed/",
        ],
        "safe_prefixes": [
            "individual/",
            "bundles/",
            "dict/",
            "negative/valid-empty/",
            "negative/concat/",
            "negative/zstd-skipframe-only/",
        ],
        "max_expansion_ratio_in_corpus": 22000000,
        "decoder_caps_recommended": {
            "max_output_bytes": 1073741824,
            "max_wall_seconds": 30,
            "max_nesting_depth": 2,
        },
        "artifact_count_by_tier": {
            "pr": tier_counts.get("pr", 0),
            "nightly": tier_counts.get("nightly", 0),
            "full": tier_counts.get("full", 0),
        },
        "sets": sets_out if sets_out else canonical_sets,
    }


# ─── robots.txt ──────────────────────────────────────────────────────────────

ROBOTS_TXT_TEMPLATE = """\
User-agent: *
Allow: /

# Agent discovery
# Full agent guide: {base_url}/AGENTS.md
# Machine-readable: {base_url}/agent.json
# Manifest: {base_url}/manifest.json
# Safe subset: {base_url}/manifest.safe.json
# WARNING: negative/ contains decompression bombs and malformed files
"""


def gen_robots_txt(base_url: str) -> str:
    return ROBOTS_TXT_TEMPLATE.format(base_url=base_url)


# ─── llms.txt ─────────────────────────────────────────────────────────────────

LLMS_TXT_TEMPLATE = """\
# Squishy Corpus

> Compression test fixtures for decompression libraries. Pre-compressed in every common format, CDN-served with stable URLs.

## Agent entry points

- [Agent guide](AGENTS.md): structured guide for LLM agents (start here)
- [agent.json](agent.json): machine-readable corpus descriptor
- [manifest.json](manifest.json): full artifact catalog (version 2, includes hazard metadata)
- [manifest.safe.json](manifest.safe.json): safe-only subset (excludes bombs and malformed fixtures)
- [smoke.zip](smoke.zip): ~5 MiB onboarding bundle (one file per major codec + AGENTS.md)
- [decode-expectations.json](decode-expectations.json): per-artifact decoder oracle

## WARNING

`negative/` contains decompression bombs (up to ~10 GiB expansion) and intentionally malformed streams. Do not decode without output size caps and timeouts.

## Sets

- silesia/: 12 classic benchmark files (text, binary, medical, 2003)
- squash/: 6 mid-2010s web files (HTML, CSS, JS, WASM, PDF, random)
- modern/: generated modern formats (JSON, Parquet, Arrow, protobuf, WASM, CSV, UTF-8)
- pathological/: edge cases (window boundaries, entropy extremes, near-duplicates)

## Tiers

- pr (~50 MiB): per-commit CI
- nightly (~500 MiB): daily runs
- full (several GiB): release validation
"""


# ─── per-directory _INDEX.json ────────────────────────────────────────────────

def gen_dir_index(
    prefix: str,
    artifacts: list[dict],
    base_url: str,
) -> dict:
    matching = [a for a in artifacts if a["path"].startswith(prefix)]
    total_size = sum(a.get("size", 0) for a in matching)

    # Collect second-level subdirectory names (e.g. "silesia" from "individual/silesia/...")
    subsets_seen: list[str] = []
    seen: set[str] = set()
    for a in matching:
        rest = a["path"][len(prefix):]
        parts = rest.split("/")
        if len(parts) >= 2:
            sub = parts[0]
            if sub not in seen:
                subsets_seen.append(sub)
                seen.add(sub)

    # Sample paths: up to 5 from different subsets
    sample_paths: list[str] = []
    used_subsets: set[str] = set()
    for a in matching:
        if len(sample_paths) >= 5:
            break
        rest = a["path"][len(prefix):]
        parts = rest.split("/")
        sub = parts[0] if len(parts) >= 2 else ""
        if sub not in used_subsets:
            sample_paths.append(a["path"])
            used_subsets.add(sub)
    # If fewer than 5 from different subsets, fill with any
    if len(sample_paths) < 5:
        for a in matching:
            if a["path"] not in sample_paths:
                sample_paths.append(a["path"])
            if len(sample_paths) >= 5:
                break

    return {
        "version": 1,
        "prefix": prefix,
        "agent_guide": f"{base_url}/AGENTS.md",
        "artifact_count": len(matching),
        "total_size_bytes": total_size,
        "subsets": subsets_seen,
        "sample_paths": sample_paths,
        "manifest": f"{base_url}/manifest.json",
    }


# ─── smoke.zip ────────────────────────────────────────────────────────────────

SMOKE_CODECS = ["gz", "bz2", "xz", "zst", "br", "lz4"]
SMOKE_MODERN = [f"individual/modern/sample.json.{codec}" for codec in SMOKE_CODECS]
SMOKE_PATHO = [
    "individual/pathological/small-256B.gz",
    "individual/pathological/small-256B.zst",
]
SMOKE_PATHS = SMOKE_MODERN + SMOKE_PATHO


def gen_smoke_zip(
    meta_dir: Path,
    build_dir: Path,
    artifacts_by_path: dict[str, dict],
    agents_md_content: str,
    base_url: str,
) -> None:
    smoke_out = meta_dir / "smoke.zip"
    tmp = smoke_out.with_suffix(".zip.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)

    included_paths: list[str] = []
    included_artifacts: list[dict] = []

    # Collect which smoke files actually exist
    for rel_path in SMOKE_PATHS:
        local = build_dir / rel_path
        if not local.exists():
            print(f"  smoke: skipping {rel_path} (not found)", file=sys.stderr)
            continue
        included_paths.append(rel_path)
        if rel_path in artifacts_by_path:
            included_artifacts.append(artifacts_by_path[rel_path])

    # Build manifest.smoke.json
    smoke_manifest = {
        "version": 2,
        "base_url": base_url,
        "agent_guide": f"{base_url}/AGENTS.md",
        "note": "Minimal smoke-test bundle. See AGENTS.md for full corpus guide.",
        "artifacts": included_artifacts,
    }

    # Deterministic ZIP timestamp (SOURCE_DATE_EPOCH = 0 → 1980-01-01 00:00:00)
    zip_time = (1980, 1, 1, 0, 0, 0)

    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        # AGENTS.md
        zi = zipfile.ZipInfo("AGENTS.md", date_time=zip_time)
        zf.writestr(zi, agents_md_content.encode("utf-8"))

        # manifest.smoke.json
        zi = zipfile.ZipInfo("manifest.smoke.json", date_time=zip_time)
        zf.writestr(zi, (json.dumps(smoke_manifest, indent=2) + "\n").encode("utf-8"))

        # Artifact files
        for rel_path in included_paths:
            local = build_dir / rel_path
            zi = zipfile.ZipInfo(rel_path, date_time=zip_time)
            zi.compress_type = zipfile.ZIP_STORED  # already compressed
            with local.open("rb") as fh:
                data = fh.read()
            zf.writestr(zi, data)

    tmp.rename(smoke_out)
    size_human = human_size(smoke_out.stat().st_size)
    print(f"  smoke.zip: {len(included_paths)} files, {size_human}", file=sys.stderr)


# ─── main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Generate agent-oriented discovery files for the Squishy Corpus."
    )
    ap.add_argument("--meta",   required=True, help="output directory (e.g. build/meta)")
    ap.add_argument("--build",  required=True, help="build root directory (e.g. build)")
    ap.add_argument("--bucket", required=True, help="S3 bucket (e.g. jackdanger.com)")
    ap.add_argument("--prefix", required=True, help="S3 prefix (e.g. squishy)")
    args = ap.parse_args()

    meta_dir  = Path(args.meta)
    build_dir = Path(args.build)
    base_url  = f"https://{args.bucket}/{args.prefix}"

    manifest_path = meta_dir / "manifest.json"
    if not manifest_path.exists():
        print(f"ERROR: {manifest_path} not found — run 'make manifest' first",
              file=sys.stderr)
        sys.exit(1)

    with manifest_path.open() as f:
        manifest = json.load(f)

    artifacts: list[dict] = manifest.get("artifacts", [])
    artifacts_by_path: dict[str, dict] = {a["path"]: a for a in artifacts}
    total_count = len(artifacts)
    total_size_bytes = sum(a.get("size", 0) for a in artifacts)

    meta_dir.mkdir(parents=True, exist_ok=True)

    # ── AGENTS.md ─────────────────────────────────────────────────────────────
    agents_md = gen_agents_md(base_url, total_count, total_size_bytes)
    write_text(meta_dir / "AGENTS.md", agents_md)
    print(f"  AGENTS.md ({total_count} artifacts, {human_size(total_size_bytes)})",
          file=sys.stderr)

    # ── agent.json ────────────────────────────────────────────────────────────
    agent_obj = gen_agent_json(base_url, artifacts)
    write_json(meta_dir / "agent.json", agent_obj)
    print("  agent.json", file=sys.stderr)

    # ── robots.txt ────────────────────────────────────────────────────────────
    write_text(meta_dir / "robots.txt", gen_robots_txt(base_url))
    print("  robots.txt", file=sys.stderr)

    # ── llms.txt ──────────────────────────────────────────────────────────────
    write_text(meta_dir / "llms.txt", LLMS_TXT_TEMPLATE)
    print("  llms.txt", file=sys.stderr)

    # ── per-directory _INDEX.json files ───────────────────────────────────────
    # Discover top-level prefixes from manifest paths
    top_prefixes: list[str] = []
    seen_prefixes: set[str] = set()
    for a in artifacts:
        parts = a["path"].split("/")
        if len(parts) >= 2:
            prefix = parts[0] + "/"
            if prefix not in seen_prefixes:
                top_prefixes.append(prefix)
                seen_prefixes.add(prefix)

    for prefix in top_prefixes:
        dirname = prefix.rstrip("/")
        out_path = meta_dir / f"{dirname}-index.json"
        idx = gen_dir_index(prefix, artifacts, base_url)
        write_json(out_path, idx)
        print(f"  {out_path.name} ({idx['artifact_count']} artifacts)", file=sys.stderr)

    # ── smoke.zip ─────────────────────────────────────────────────────────────
    gen_smoke_zip(meta_dir, build_dir, artifacts_by_path, agents_md, base_url)

    print(f"gen-agent-docs: wrote {5 + len(top_prefixes)} files to {meta_dir}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
