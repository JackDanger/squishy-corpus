"""Build manifest.json, manifest.safe.json, index.txt, CHECKSUMS.sha256,
decode-expectations.json, expected-ratio.json, publish.tsv, and a versioned
snapshot from the contents of the build directory.

Public interface: ``run(cfg: BuildConfig) -> int``
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import sha256_file, write_bytes_atomic, write_text_atomic

# ── constants ────────────────────────────────────────────────────────────────

CC_IMMUTABLE = "public, max-age=31536000, immutable"
CC_INDEX = "public, max-age=300, must-revalidate"

CONTENT_TYPES: dict[str, str] = {
    ".txt":      "text/plain; charset=utf-8",
    ".json":     "application/json",
    ".html":     "text/html; charset=utf-8",
    ".css":      "text/css; charset=utf-8",
    ".js":       "application/javascript",
    ".wasm":     "application/wasm",
    ".gz":       "application/gzip",
    ".bz2":      "application/x-bzip2",
    ".xz":       "application/x-xz",
    ".zst":      "application/zstd",
    ".lz4":      "application/x-lz4",
    ".br":       "application/x-brotli",
    ".lzma":     "application/x-lzma",
    ".lz":       "application/x-lzip",
    ".lzo":      "application/x-lzop",
    ".zpaq":     "application/x-zpaq",
    ".7z":       "application/x-7z-compressed",
    ".zip":      "application/zip",
    ".tar":      "application/x-tar",
    ".cpio":     "application/x-cpio",
    ".sha256":   "text/plain; charset=utf-8",
    ".zdict":    "application/octet-stream",
    ".squashfs": "application/x-squashfs",
    ".sqlite":   "application/vnd.sqlite3",
    ".parquet":  "application/vnd.apache.parquet",
    ".protobuf": "application/octet-stream",
    ".ndjson":   "application/x-ndjson",
    ".log":      "text/plain; charset=utf-8",
}

DESCRIPTIONS: dict[str, str] = {
    "raw/silesia/dickens":  "Silesia: collected English novels (Charles Dickens)",
    "raw/silesia/mozilla":  "Silesia: Mozilla executables (Unix tar)",
    "raw/silesia/mr":       "Silesia: 3D MRI medical image",
    "raw/silesia/nci":      "Silesia: NCI chemical database (text)",
    "raw/silesia/ooffice":  "Silesia: OpenOffice DLL (Windows binary)",
    "raw/silesia/osdb":     "Silesia: OSDB synthetic database (binary)",
    "raw/silesia/reymont":  "Silesia: Reymont 'Chłopi' PDF (Polish text, uncompressed)",
    "raw/silesia/samba":    "Silesia: Samba source code (tar)",
    "raw/silesia/sao":      "Silesia: SAO star catalog (binary)",
    "raw/silesia/webster":  "Silesia: Webster's dictionary (HTML)",
    "raw/silesia/x-ray":    "Silesia: 16-bit grayscale DICOM x-ray",
    "raw/silesia/xml":      "Silesia: XML documents (tar)",
}

HAZARD_BY_DIR: dict[str, dict] = {
    "negative/truncated/":           {"class": "malformed",    "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/bitflip/":             {"class": "malformed",    "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/concat-mixed/":        {"class": "malformed",    "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/declared-length/":     {"class": "malformed",    "severity": "medium", "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/cve-class/":           {"class": "malformed",    "severity": "high",   "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/concat/":              {"class": "concat-multi", "severity": "none",   "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept_all_members_or_reject"},
    "negative/valid-empty/":         {"class": "valid-edge",   "severity": "none",   "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept"},
    "negative/zstd-skipframe-only/": {"class": "valid-edge",   "severity": "none",   "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept"},
    "negative/bomb/":                {"class": "bomb",         "severity": "high",   "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject_or_cap"},
}

SAFE_HAZARD: dict = {
    "class": "none",
    "severity": "none",
    "safe_to_decode_unbounded": True,
    "expected_decoder_outcome": "accept",
}

HAZARD_CLASSES: dict[str, dict] = {
    "none":         {"safe": True,  "decoder_should": "accept"},
    "bomb":         {"safe": False, "decoder_should": "reject_or_cap", "recommended_max_output_bytes": 1 << 30},
    "malformed":    {"safe": False, "decoder_should": "reject"},
    "concat-multi": {"safe": True,  "decoder_should": "accept_all_members_or_reject"},
    "valid-edge":   {"safe": True,  "decoder_should": "accept"},
}

CODEC_NAMES: dict[str, str] = {
    "gz": "gzip", "bz2": "bzip2", "xz": "xz", "zst": "zstd",
    "lz4": "lz4", "br": "brotli", "lzma": "lzma", "lz": "lzip",
    "lzo": "lzop", "zpaq": "zpaq", "7z": "7zip", "zip": "zip",
}

_BUNDLE_CONTAINERS = ["tar", "7z", "squashfs", "zip", "cpio", "pax", "ar"]
_BUNDLE_COMPRESSIONS = ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma", "lz", "lzo", "zpaq"]

META_FILES_CC_INDEX = [
    "index.txt", "manifest.json", "CHECKSUMS.sha256",
    "versions.txt", "expected-ratio.json", "README.txt",
    "index.html", "listing.html",
    "manifest.safe.json", "manifest.safe.txt",
    "decode-expectations.json",
]

OPTIONAL_META = ["AGENTS.md", "agent.json", "robots.txt", "llms.txt", "smoke.zip"]

# ── helpers ──────────────────────────────────────────────────────────────────


def _content_type_for(name: str) -> str:
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        ext = "." + parts[i]
        if ext in CONTENT_TYPES:
            return CONTENT_TYPES[ext]
    return "application/octet-stream"


def derive_tier(path: str) -> str:
    """Assign a tier label based on artifact path."""
    if "negative/" in path:
        return "pr"
    if "pathological/" in path:
        if any(s in path for s in ("empty-0B", "one-1B", "tiny-13B", "small-256B", "page-4095B", "short-65535B")):
            return "pr"
        if any(s in path for s in ("-1M", "1MiB")):
            return "nightly"
        return "full"
    if "bundles/combined/" in path:
        return "full"
    if "bundles/" in path:
        return "nightly"
    if "individual/silesia/" in path:
        return "nightly"
    if "individual/modern/" in path:
        return "nightly"
    if "individual/pathological/" in path:
        return "full"
    if "raw/silesia/" in path:
        return "nightly"
    if "raw/modern/" in path:
        return "pr"
    if "raw/pathological/" in path:
        return "nightly"
    if "dict/" in path:
        return "nightly"
    if path.startswith(("index.txt", "manifest.json", "CHECKSUMS.sha256", "README.txt", "versions.txt", "expected-ratio.json")):
        return "pr"
    return "full"


def assign_hazard(path: str, hazard_catalog: dict) -> dict:
    """Assign hazard metadata to a record by path."""
    if path.startswith("negative/") and hazard_catalog:
        rel_neg = path[len("negative/"):]
        by_path = hazard_catalog.get("by_path", {})
        if rel_neg in by_path:
            return by_path[rel_neg]
    for prefix, profile in HAZARD_BY_DIR.items():
        if path.startswith(prefix):
            return dict(profile)
    return dict(SAFE_HAZARD)


def parse_individual_codec(path: str) -> dict:
    """Parse codec/level/container/origin fields from an individual/ path."""
    if not path.startswith("individual/"):
        return {}
    rel = path[len("individual/"):]
    slash = rel.find("/")
    if slash < 0:
        return {}
    origin_set = rel[:slash]
    filename = rel[slash + 1:]

    container = None
    codec = None
    codec_level = None

    level_match = re.search(r'\.l(\d+)$', filename)
    if level_match:
        codec_level = int(level_match.group(1))
        filename_no_level = filename[:level_match.start()]
    else:
        filename_no_level = filename

    for suffix, cname, ccontainer in [
        (".zip.bzip2",   "bzip2",   "zip"),
        (".zip.lzma",    "lzma",    "zip"),
        (".zip.deflate", "deflate", "zip"),
        (".zip.store",   "store",   "zip"),
        (".zip",         "deflate", "zip"),
    ]:
        if filename_no_level.endswith(suffix):
            codec = cname
            container = ccontainer
            filename_no_level = filename_no_level[: -len(suffix)]
            break
    else:
        parts = filename_no_level.split(".")
        if len(parts) >= 2:
            ext = parts[-1]
            codec = CODEC_NAMES.get(ext, ext)
            filename_no_level = ".".join(parts[:-1])

    return {
        "origin_set":   origin_set,
        "origin_name":  filename_no_level,
        "codec":        codec,
        "codec_level":  codec_level,
        "container":    container,
    }


def parse_bundle_codec(path: str) -> dict:
    """Parse container/codec from a bundles/ path."""
    if not path.startswith("bundles/"):
        return {}
    rel = path[len("bundles/"):]
    fname = Path(rel).name
    parts = fname.split(".")
    if len(parts) < 2:
        return {}

    container = None
    codec = None

    tail = ".".join(parts[1:]).lower()

    if "concat-" in tail:
        container = "concat"
        rest = tail.split("concat-", 1)[1]
        rest = rest.replace("-skipframes", "").replace("-skipframe", "")
        codec = CODEC_NAMES.get(rest, rest) if rest else None
    elif len(parts) >= 3 and parts[-2] in _BUNDLE_CONTAINERS:
        container = parts[-2]
        codec = CODEC_NAMES.get(parts[-1], parts[-1])
    elif parts[-1] in _BUNDLE_CONTAINERS:
        container = parts[-1]
        codec = None
    elif len(parts) >= 2 and parts[-2] in _BUNDLE_COMPRESSIONS:
        container = "tar"
        codec = CODEC_NAMES.get(parts[-1], parts[-1])
    else:
        codec = CODEC_NAMES.get(parts[-1], parts[-1])

    return {
        "origin_set":  None,
        "origin_name": None,
        "codec":       codec,
        "codec_level": None,
        "container":   container,
    }


def parse_codec_fields(path: str) -> dict | None:
    if path.startswith("individual/"):
        return parse_individual_codec(path)
    if path.startswith("bundles/"):
        return parse_bundle_codec(path)
    return None


def _is_uncompressed(p: str) -> bool:
    return (
        p.startswith("raw/")
        or p.endswith(".tar")
        or p.endswith((".cpio", ".pax", ".ar"))
        or p.endswith(".zip.store")
    )


# ── profile merge helpers ─────────────────────────────────────────────────────

_PROFILE_SOURCE_KEYS = (
    "size_uncompressed",
    "source_sha256",
    "entropy_bits_per_byte",
    "compression_class",
    "representative_ratios",
)


def _merge_profile_into_record(record: dict, profile_sources: dict) -> None:
    """Merge per-source profile data into an artifact record where the source is known."""
    os_set = record.get("origin_set")
    oname = record.get("origin_name")
    if not os_set or not oname:
        return
    source_key = f"{os_set}/{oname}"
    src = profile_sources.get(source_key)
    if not src:
        return
    for k in _PROFILE_SOURCE_KEYS:
        if k in src:
            record[k] = src[k]


def _merge_profile_into_sources(sources: dict, profile_sources: dict) -> None:
    """Merge per-source profile data into the sources block of the manifest."""
    for key, entry in sources.items():
        src = profile_sources.get(key)
        if not src:
            continue
        for k in _PROFILE_SOURCE_KEYS:
            if k in src:
                entry[k] = src[k]


# ── main entry points ─────────────────────────────────────────────────────────


def run(cfg: BuildConfig) -> int:
    """Build all manifest and index files from the current build directory."""
    build = cfg.build_dir
    meta = cfg.meta_dir
    meta.mkdir(parents=True, exist_ok=True)

    hazard_catalog: dict = {}
    hazard_catalog_path = build / "negative" / "hazard-catalog.json"
    if hazard_catalog_path.exists():
        try:
            with hazard_catalog_path.open() as f:
                hazard_catalog = json.load(f)
        except Exception as e:
            print(f"warning: could not load hazard-catalog.json: {e}", file=sys.stderr)

    profile_sources: dict = {}
    profile_path = meta / "profile.json"
    if profile_path.exists():
        try:
            with profile_path.open() as f:
                profile_data = json.load(f)
                profile_sources = profile_data.get("sources", {})
        except Exception as e:
            print(f"warning: could not load profile.json: {e}", file=sys.stderr)

    skip_dirs = {build / "sources", meta}
    records: list[dict] = []
    raw_hashes: dict[str, str] = {}

    for root, dirs, files in os.walk(build):
        root_p = Path(root)
        dirs[:] = [d for d in dirs if root_p / d not in skip_dirs]
        for fname in files:
            p = root_p / fname
            if any(p.is_relative_to(s) for s in skip_dirs):
                continue
            if fname.endswith((".tmp", ".sha256", ".list")):
                continue
            if fname.startswith(".") or fname in {"tools.lock", "publish.tsv"}:
                continue
            rel = p.relative_to(build).as_posix()
            digest = sha256_file(p)
            size = p.stat().st_size
            ct = _content_type_for(fname)
            desc = DESCRIPTIONS.get(rel, "")
            tier = derive_tier(rel)

            if rel.startswith("raw/"):
                raw_hashes[rel[len("raw/"):]] = digest

            hazard = assign_hazard(rel, hazard_catalog)

            rec: dict = {
                "path":         rel,
                "size":         size,
                "sha256":       digest,
                "content_type": ct,
                "description":  desc,
                "tier":         tier,
                "hazard":       hazard,
            }

            codec_fields = parse_codec_fields(rel)
            if codec_fields is not None:
                rec.update(codec_fields)

            if profile_sources:
                _merge_profile_into_record(rec, profile_sources)

            records.append(rec)

    records.sort(key=lambda r: r["path"])

    raw_sizes = {r["path"][len("raw/"):]: r["size"] for r in records if r["path"].startswith("raw/")}

    # ── index.txt ─────────────────────────────────────────────────────────────
    lines = ["# sha256\tsize\tcontent_type\ttier\tpath\tdescription\thazard_class\n"]
    for r in records:
        hclass = r["hazard"]["class"]
        lines.append(f"{r['sha256']}\t{r['size']}\t{r['content_type']}\t{r['tier']}\t{r['path']}\t{r['description']}\t{hclass}\n")
    write_text_atomic(meta / "index.txt", "".join(lines))

    # ── expected-ratio.json ───────────────────────────────────────────────────
    ratios: dict[str, dict] = {}
    for r in records:
        p = r["path"]
        if not p.startswith("individual/"):
            continue
        rel = p[len("individual/"):]
        parts = rel.split(".")
        for i in range(1, len(parts)):
            if "." + parts[i] in CONTENT_TYPES or parts[i].startswith("l"):
                origin = ".".join(parts[:i])
                suffix = ".".join(parts[i:])
                break
        else:
            origin, suffix = rel, ""
        ratios.setdefault(origin, {})[suffix] = r["size"]
    write_bytes_atomic(
        meta / "expected-ratio.json",
        (json.dumps({"raw_sizes": raw_sizes, "compressed_sizes": ratios}, indent=2) + "\n").encode(),
    )

    # ── sources block ─────────────────────────────────────────────────────────
    sources: dict = {}
    for r in records:
        p = r["path"]
        if not p.startswith("individual/"):
            continue
        os_set = r.get("origin_set")
        oname = r.get("origin_name")
        if not os_set or not oname:
            continue
        key = f"{os_set}/{oname}"
        if key not in sources:
            canonical = f"individual/{os_set}/{oname}.gz"
            entry: dict = {
                "canonical_delivery": canonical,
                "note": "Uncompressed bytes are not published. Fetch canonical_delivery and decompress.",
            }
            raw_key = f"{os_set}/{oname}"
            if raw_key in raw_sizes:
                entry["uncompressed_size"] = raw_sizes[raw_key]
            sources[key] = entry

    if profile_sources:
        _merge_profile_into_sources(sources, profile_sources)

    # ── manifest.json ─────────────────────────────────────────────────────────
    manifest = {
        "version":                       2,
        "bucket":                        cfg.bucket,
        "prefix":                        cfg.prefix,
        "uncompressed_sources_published": False,
        "hazard_classes":                HAZARD_CLASSES,
        "sources":                       sources,
        "artifacts":                     records,
    }
    manifest_json = (json.dumps(manifest, indent=2) + "\n").encode()
    write_bytes_atomic(meta / "manifest.json", manifest_json)

    # ── versioned snapshot ────────────────────────────────────────────────────
    import hashlib
    manifest_digest = hashlib.sha256(manifest_json).hexdigest()
    sha7 = manifest_digest[:7]
    today = date.today().isoformat()
    snapshots_dir = meta / "manifests"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_name = f"manifest-{today}-{sha7}.json"
    snapshot_path = snapshots_dir / snapshot_name
    shutil.copy2(meta / "manifest.json", snapshot_path)

    # ── CHECKSUMS.sha256 ──────────────────────────────────────────────────────
    cksum_lines = "".join(f"{r['sha256']}  {r['path']}\n" for r in records)
    write_text_atomic(meta / "CHECKSUMS.sha256", cksum_lines)

    # ── manifest.safe.json ────────────────────────────────────────────────────
    safe_records = [r for r in records if r["hazard"].get("safe_to_decode_unbounded") is True]
    manifest_safe = {
        "version":    2,
        "bucket":     cfg.bucket,
        "prefix":     cfg.prefix,
        "note":       "Filtered to safe_to_decode_unbounded=true. All negative fixtures with class=bomb or class=malformed are excluded.",
        "uncompressed_sources_published": False,
        "hazard_classes": HAZARD_CLASSES,
        "sources":    sources,
        "artifacts":  safe_records,
    }
    write_bytes_atomic(
        meta / "manifest.safe.json",
        (json.dumps(manifest_safe, indent=2) + "\n").encode(),
    )

    # ── manifest.safe.txt ─────────────────────────────────────────────────────
    safe_lines = ["# sha256\tsize\tcontent_type\ttier\tpath\tdescription\thazard_class\n"]
    for r in safe_records:
        hclass = r["hazard"]["class"]
        safe_lines.append(f"{r['sha256']}\t{r['size']}\t{r['content_type']}\t{r['tier']}\t{r['path']}\t{r['description']}\t{hclass}\n")
    write_text_atomic(meta / "manifest.safe.txt", "".join(safe_lines))

    # ── decode-expectations.json ──────────────────────────────────────────────
    expectations: dict = {}
    for r in records:
        p = r["path"]
        hazard = r["hazard"]
        hclass = hazard["class"]

        if p.startswith("individual/"):
            os_set = r.get("origin_set")
            oname = r.get("origin_name")
            codec = r.get("codec")
            entry: dict = {"should_succeed": True, "hazard_class": "none"}
            if codec:
                entry["codec"] = codec
            if os_set and oname:
                entry["origin"] = f"{os_set}/{oname}"
                raw_key = f"{os_set}/{oname}"
                if raw_key in raw_hashes:
                    entry["decoded_sha256"] = raw_hashes[raw_key]
                if raw_key in raw_sizes:
                    entry["decoded_size"] = raw_sizes[raw_key]
            expectations[p] = entry

        elif p.startswith("negative/"):
            codec = r.get("codec")
            entry = {"hazard_class": hclass}
            if codec:
                entry["codec"] = codec

            if hclass == "bomb":
                entry["should_succeed"] = "either"
                entry["must_not_oom"] = True
                entry["must_complete_within_seconds"] = 60
                if hazard_catalog:
                    neg_rel = p[len("negative/"):]
                    bp = hazard_catalog.get("by_path", {}).get(neg_rel, {})
                    if "expansion_bytes_max" in bp:
                        entry["if_succeed_max_output_bytes"] = bp["expansion_bytes_max"]
            elif hclass == "malformed":
                entry["should_succeed"] = False
                if hazard_catalog:
                    neg_rel = p[len("negative/"):]
                    bp = hazard_catalog.get("by_path", {}).get(neg_rel, {})
                    if "expected_error_class" in bp:
                        entry["expected_error_class"] = bp["expected_error_class"]
            elif hclass in ("valid-edge", "concat-multi", "none"):
                entry["should_succeed"] = True

            expectations[p] = entry

    decode_exp = {
        "version": 1,
        "note":    "Per-artifact decoder expectations. decoded_sha256 is the sha256 of the uncompressed original where known.",
        "expectations": expectations,
    }
    write_bytes_atomic(
        meta / "decode-expectations.json",
        (json.dumps(decode_exp, indent=2) + "\n").encode(),
    )

    # ── publish.tsv ───────────────────────────────────────────────────────────
    records_sorted = sorted(
        (r for r in records if not _is_uncompressed(r["path"])),
        key=lambda r: -r["size"],
    )
    tsv_lines: list[str] = []
    for r in records_sorted:
        local = build / r["path"]
        s3key = f"{cfg.prefix}/{r['path']}"
        tsv_lines.append(f"{local}\t{s3key}\t{r['content_type']}\t{CC_IMMUTABLE}\n")
    for name in META_FILES_CC_INDEX:
        local = meta / name
        if not local.exists():
            continue
        s3key = f"{cfg.prefix}/{name}"
        ct = _content_type_for(name)
        tsv_lines.append(f"{local}\t{s3key}\t{ct}\t{CC_INDEX}\n")
    for name in OPTIONAL_META:
        local = meta / name
        if not local.exists():
            continue
        s3key = f"{cfg.prefix}/{name}"
        ct = _content_type_for(name)
        tsv_lines.append(f"{local}\t{s3key}\t{ct}\t{CC_INDEX}\n")
    s3key_snap = f"{cfg.prefix}/manifests/{snapshot_name}"
    tsv_lines.append(f"{snapshot_path}\t{s3key_snap}\tapplication/json\t{CC_IMMUTABLE}\n")
    write_text_atomic(meta / "publish.tsv", "".join(tsv_lines))

    print(f"manifest: {len(records)} artifacts, {len(safe_records)} safe, snapshot={snapshot_name}", file=sys.stderr)
    return 0


def run_verify(cfg: BuildConfig) -> int:
    """Verify all checksums listed in CHECKSUMS.sha256."""
    checksums_path = cfg.meta_dir / "CHECKSUMS.sha256"
    if not checksums_path.exists():
        print(f"error: {checksums_path} not found — run 'build manifest' first", file=sys.stderr)
        return 1

    errors = 0
    checked = 0
    with checksums_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split(None, 2)
            if len(parts) < 2:
                continue
            expected_digest, rel_path = parts[0], parts[1]
            p = cfg.build_dir / rel_path
            if not p.exists():
                print(f"MISSING: {rel_path}", file=sys.stderr)
                errors += 1
                continue
            actual = sha256_file(p)
            if actual != expected_digest:
                print(f"FAIL: {rel_path}  expected={expected_digest}  got={actual}", file=sys.stderr)
                errors += 1
            else:
                checked += 1

    if errors:
        print(f"verify: {errors} failures, {checked} ok", file=sys.stderr)
        return 1
    print(f"verify: {checked} ok", file=sys.stderr)
    return 0
