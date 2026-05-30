#!/usr/bin/env python3
"""Build index.txt, manifest.json, CHECKSUMS.sha256, expected-ratio.json,
and publish.tsv from the contents of build/.

index.txt format (tab-separated, one record per line):
  <sha256>\t<size>\t<content_type>\t<tier>\t<path>\t<description>\t<hazard_class>

publish.tsv format (consumed by publish.sh):
  <local_path>\t<s3_key>\t<content_type>\t<cache_control>
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, sys
from datetime import date
from pathlib import Path

CC_IMMUTABLE = "public, max-age=31536000, immutable"
CC_INDEX     = "public, max-age=300, must-revalidate"

CONTENT_TYPES = {
    ".txt":  "text/plain; charset=utf-8",
    ".json": "application/json",
    ".html": "text/html; charset=utf-8",
    ".css":  "text/css; charset=utf-8",
    ".js":   "application/javascript",
    ".wasm": "application/wasm",
    ".gz":   "application/gzip",
    ".bz2":  "application/x-bzip2",
    ".xz":   "application/x-xz",
    ".zst":  "application/zstd",
    ".lz4":  "application/x-lz4",
    ".br":   "application/x-brotli",
    ".lzma": "application/x-lzma",
    ".lz":   "application/x-lzip",
    ".lzo":  "application/x-lzop",
    ".zpaq": "application/x-zpaq",
    ".7z":   "application/x-7z-compressed",
    ".zip":  "application/zip",
    ".tar":  "application/x-tar",
    ".cpio": "application/x-cpio",
    ".sha256":  "text/plain; charset=utf-8",
    ".zdict":   "application/octet-stream",
    ".squashfs":"application/x-squashfs",
    ".sqlite":  "application/vnd.sqlite3",
    ".parquet": "application/vnd.apache.parquet",
    ".protobuf":"application/octet-stream",
    ".ndjson":  "application/x-ndjson",
    ".log":     "text/plain; charset=utf-8",
}

DESCRIPTIONS = {
    "raw/silesia/dickens":    "Silesia: collected English novels (Charles Dickens)",
    "raw/silesia/mozilla":    "Silesia: Mozilla executables (Unix tar)",
    "raw/silesia/mr":         "Silesia: 3D MRI medical image",
    "raw/silesia/nci":        "Silesia: NCI chemical database (text)",
    "raw/silesia/ooffice":    "Silesia: OpenOffice DLL (Windows binary)",
    "raw/silesia/osdb":       "Silesia: OSDB synthetic database (binary)",
    "raw/silesia/reymont":    "Silesia: Reymont 'Chłopi' PDF (Polish text, uncompressed)",
    "raw/silesia/samba":      "Silesia: Samba source code (tar)",
    "raw/silesia/sao":        "Silesia: SAO star catalog (binary)",
    "raw/silesia/webster":    "Silesia: Webster's dictionary (HTML)",
    "raw/silesia/x-ray":      "Silesia: 16-bit grayscale DICOM x-ray",
    "raw/silesia/xml":        "Silesia: XML documents (tar)",
}

# ─── P2: hazard metadata ─────────────────────────────────────────────────────

HAZARD_BY_DIR = {
    "negative/truncated/":       {"class": "malformed", "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/bitflip/":         {"class": "malformed", "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/concat-mixed/":    {"class": "malformed", "severity": "low",    "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/declared-length/": {"class": "malformed", "severity": "medium", "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/cve-class/":       {"class": "malformed", "severity": "high",   "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject"},
    "negative/concat/":          {"class": "concat-multi", "severity": "none","safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept_all_members_or_reject"},
    "negative/valid-empty/":     {"class": "valid-edge", "severity": "none",  "safe_to_decode_unbounded": True,  "expected_decoder_outcome": "accept"},
    "negative/zstd-skipframe-only/": {"class": "valid-edge", "severity": "none", "safe_to_decode_unbounded": True, "expected_decoder_outcome": "accept"},
    "negative/bomb/":               {"class": "bomb",       "severity": "high",  "safe_to_decode_unbounded": False, "expected_decoder_outcome": "reject_or_cap"},
}

SAFE_HAZARD = {"class": "none", "severity": "none", "safe_to_decode_unbounded": True, "expected_decoder_outcome": "accept"}

HAZARD_CLASSES = {
    "none":        {"safe": True,  "decoder_should": "accept"},
    "bomb":        {"safe": False, "decoder_should": "reject_or_cap", "recommended_max_output_bytes": 1 << 30},
    "malformed":   {"safe": False, "decoder_should": "reject"},
    "concat-multi":{"safe": True,  "decoder_should": "accept_all_members_or_reject"},
    "valid-edge":  {"safe": True,  "decoder_should": "accept"},
}

# ─── P4: codec parsing ───────────────────────────────────────────────────────

CODEC_NAMES = {
    "gz": "gzip", "bz2": "bzip2", "xz": "xz", "zst": "zstd",
    "lz4": "lz4", "br": "brotli", "lzma": "lzma", "lz": "lzip",
    "lzo": "lzop", "zpaq": "zpaq", "7z": "7zip", "zip": "zip",
}

def derive_tier(path: str) -> str:
    """A path's tier controls whether 'minimal' / 'nightly' / 'full' test
    suites pull it. Keep small inputs in 'pr', mid in 'nightly', huge in
    'full'. Negative fixtures are 'pr' (small + critical)."""
    if "negative/" in path:               return "pr"
    if "pathological/" in path:
        # only the tiny + small set in pr
        if any(s in path for s in ("empty-0B","one-1B","tiny-13B","small-256B","page-4095B","short-65535B")):
            return "pr"
        if any(s in path for s in ("-1M", "1MiB")):  return "nightly"
        return "full"
    if "bundles/combined/" in path:        return "full"
    if "bundles/" in path:                 return "nightly"
    if "individual/silesia/" in path:      return "nightly"
    if "individual/modern/" in path:       return "nightly"
    if "individual/pathological/" in path: return "full"
    if "raw/silesia/" in path:             return "nightly"
    if "raw/modern/" in path:              return "pr"
    if "raw/pathological/" in path:        return "nightly"
    if "dict/" in path:                    return "nightly"
    if path.startswith(("index.txt","manifest.json","CHECKSUMS.sha256","README.txt","versions.txt","expected-ratio.json")):
        return "pr"
    return "full"

def sha256_file(p: Path) -> tuple[str, int]:
    h = hashlib.sha256(); n = 0
    with p.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk); n += len(chunk)
    return h.hexdigest(), n

def content_type_for(name: str) -> str:
    # multi-suffix: take the rightmost known extension
    parts = name.split(".")
    for i in range(len(parts) - 1, 0, -1):
        ext = "." + parts[i]
        if ext in CONTENT_TYPES:
            return CONTENT_TYPES[ext]
    return "application/octet-stream"

def assign_hazard(path: str, hazard_catalog: dict) -> dict:
    """Assign hazard metadata to a record by path."""
    # 1. Check hazard catalog by_path (keyed relative to negative/ dir)
    if path.startswith("negative/") and hazard_catalog:
        rel_neg = path[len("negative/"):]
        by_path = hazard_catalog.get("by_path", {})
        if rel_neg in by_path:
            return by_path[rel_neg]
    # 2. Dir-prefix lookup
    for prefix, profile in HAZARD_BY_DIR.items():
        if path.startswith(prefix):
            return dict(profile)
    # 3. Default safe
    return dict(SAFE_HAZARD)

def parse_individual_codec(path: str) -> dict:
    """Parse codec/level/container/origin fields from an individual/ path.

    Returns a dict with keys: origin_set, origin_name, codec, codec_level, container.
    Returns empty dict if path is not under individual/.
    """
    if not path.startswith("individual/"):
        return {}
    # individual/<set>/<filename>
    rel = path[len("individual/"):]
    slash = rel.find("/")
    if slash < 0:
        return {}
    origin_set = rel[:slash]
    filename = rel[slash + 1:]

    # Determine container and codec from the filename extension chain.
    # Strategy: scan from the right for known extension tokens.
    # Special compound cases first:
    #   .zip.bzip2  → codec=bzip2, container=zip
    #   .zip.lzma   → codec=lzma,  container=zip
    #   .zip.deflate → codec=deflate, container=zip
    #   .zip.store  → codec=store,  container=zip
    #   .zip        → codec=deflate, container=zip  (default deflate)
    #   .l<N> suffix for level

    container = None
    codec = None
    codec_level = None

    # Strip level suffix first: .l<N> at the very end
    level_match = re.search(r'\.l(\d+)$', filename)
    if level_match:
        codec_level = int(level_match.group(1))
        filename_no_level = filename[:level_match.start()]
    else:
        filename_no_level = filename

    # Now detect container/codec from the trailing extensions
    for suffix, cname, ccontainer in [
        (".zip.bzip2",  "bzip2",   "zip"),
        (".zip.lzma",   "lzma",    "zip"),
        (".zip.deflate","deflate",  "zip"),
        (".zip.store",  "store",    "zip"),
        (".zip",        "deflate",  "zip"),
    ]:
        if filename_no_level.endswith(suffix):
            codec = cname
            container = ccontainer
            base = filename_no_level[: -len(suffix)]
            break
    else:
        # Regular extension mapping
        parts = filename_no_level.split(".")
        if len(parts) >= 2:
            ext = parts[-1]
            codec = CODEC_NAMES.get(ext, ext)
            base = ".".join(parts[:-1])
        else:
            base = filename_no_level

    # origin_name is the base filename (before codec extension)
    # Remove origin_set prefix if present: e.g. "silesia/dickens" → "dickens"
    origin_name = base

    return {
        "origin_set": origin_set,
        "origin_name": origin_name,
        "codec": codec,
        "codec_level": codec_level,
        "container": container,
    }

# Bundle filename format: <set>.<ordering>.<format>
# format can be compound: tar.gz, 7z.lzma2, squashfs.xz, concat-zst, concat-zst-skipframes
_BUNDLE_CONTAINERS = ["tar", "7z", "squashfs", "zip", "cpio", "pax", "ar"]
_BUNDLE_COMPRESSIONS = ["gz", "bz2", "xz", "zst", "lz4", "br", "lzma", "lz", "lzo", "zpaq"]

def parse_bundle_codec(path: str) -> dict:
    """Parse container/codec from a bundles/ path."""
    if not path.startswith("bundles/"):
        return {}
    rel = path[len("bundles/"):]
    # Strip subdirs if any (e.g. bundles/combined/...)
    fname = Path(rel).name
    # Remove common non-extension prefix patterns like set.ordering.
    # The format is the extension chain after the base name
    # We parse the extension(s) from the end of the filename
    parts = fname.split(".")
    if len(parts) < 2:
        return {}

    container = None
    codec = None

    # Try to match known bundle formats from the extension tail
    # Formats like: tar.gz, tar.bz2, 7z.lzma2, squashfs.xz, zip, concat-zst, concat-zst-skipframes
    # The last one or two parts form the format descriptor
    tail = ".".join(parts[1:]).lower()  # everything after first dot

    # concat- prefix formats
    if "concat-" in tail:
        container = "concat"
        rest = tail.split("concat-", 1)[1]
        # strip -skipframes suffix
        rest = rest.replace("-skipframes", "").replace("-skipframe", "")
        codec = CODEC_NAMES.get(rest, rest) if rest else None
    elif len(parts) >= 3 and parts[-2] in _BUNDLE_CONTAINERS:
        container = parts[-2]
        codec_ext = parts[-1]
        codec = CODEC_NAMES.get(codec_ext, codec_ext)
    elif parts[-1] in _BUNDLE_CONTAINERS:
        container = parts[-1]
        codec = None
    elif len(parts) >= 2 and parts[-2] in _BUNDLE_COMPRESSIONS:
        # e.g. tar.gz → container=tar, codec=gzip
        container = "tar"
        codec = CODEC_NAMES.get(parts[-1], parts[-1])
    else:
        # fallback: last part is codec, second-to-last is container if known
        ext = parts[-1]
        codec = CODEC_NAMES.get(ext, ext)

    return {
        "origin_set": None,
        "origin_name": None,
        "codec": codec,
        "codec_level": None,
        "container": container,
    }

def parse_codec_fields(path: str) -> dict | None:
    """Return codec/level/container/origin fields for individual/ and bundles/ paths."""
    if path.startswith("individual/"):
        return parse_individual_codec(path)
    if path.startswith("bundles/"):
        return parse_bundle_codec(path)
    return None

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build",  required=True)
    ap.add_argument("--meta",   required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", required=True)
    args = ap.parse_args()

    build = Path(args.build); meta = Path(args.meta)
    meta.mkdir(parents=True, exist_ok=True)

    # Load hazard catalog if available (written by gen-negative.py)
    hazard_catalog: dict = {}
    hazard_catalog_path = build / "negative" / "hazard-catalog.json"
    if hazard_catalog_path.exists():
        try:
            with hazard_catalog_path.open() as f:
                hazard_catalog = json.load(f)
        except Exception as e:
            print(f"warning: could not load hazard-catalog.json: {e}", file=sys.stderr)

    # Walk build/ collecting everything we should publish, excluding sources/ and meta/.
    skip_dirs = {build / "sources", meta}
    records = []
    raw_hashes: dict[str, str] = {}  # keyed by e.g. "silesia/dickens" (strip "raw/")

    for root, dirs, files in os.walk(build):
        root_p = Path(root)
        # prune skipped subtrees
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
            digest, size = sha256_file(p)
            ct = content_type_for(fname)
            desc = DESCRIPTIONS.get(rel, "")
            tier = derive_tier(rel)

            # Collect raw hashes for P8 (decode-expectations)
            if rel.startswith("raw/"):
                raw_key = rel[len("raw/"):]
                raw_hashes[raw_key] = digest

            # P2: assign hazard
            hazard = assign_hazard(rel, hazard_catalog)

            record: dict = {
                "path": rel,
                "size": size,
                "sha256": digest,
                "content_type": ct,
                "description": desc,
                "tier": tier,
                "hazard": hazard,
            }

            # P4: codec/level/container/origin fields
            codec_fields = parse_codec_fields(rel)
            if codec_fields is not None:
                record.update(codec_fields)

            records.append(record)

    records.sort(key=lambda r: r["path"])

    # ── index.txt — TSV, comment header (P2: add hazard_class column) ────────
    with (meta / "index.txt").open("w") as f:
        f.write("# sha256\tsize\tcontent_type\ttier\tpath\tdescription\thazard_class\n")
        for r in records:
            hclass = r["hazard"]["class"]
            f.write(f"{r['sha256']}\t{r['size']}\t{r['content_type']}\t{r['tier']}\t{r['path']}\t{r['description']}\t{hclass}\n")

    # ── expected-ratio.json (unchanged logic) ────────────────────────────────
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
    raw_sizes = {r["path"][len("raw/"):]: r["size"] for r in records if r["path"].startswith("raw/")}
    with (meta / "expected-ratio.json").open("w") as f:
        json.dump({"raw_sizes": raw_sizes, "compressed_sizes": ratios}, f, indent=2)

    # ── P5: sources block ────────────────────────────────────────────────────
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
            # canonical_delivery = the .gz path for this origin
            canonical = f"individual/{os_set}/{oname}.gz"
            entry: dict = {
                "canonical_delivery": canonical,
                "note": "Uncompressed bytes are not published. Fetch canonical_delivery and decompress.",
            }
            raw_key = f"{os_set}/{oname}"
            if raw_key in raw_sizes:
                entry["uncompressed_size"] = raw_sizes[raw_key]
            sources[key] = entry

    # ── manifest.json (version 2, with hazard_classes + sources) ────────────
    manifest = {
        "version": 2,
        "bucket": args.bucket,
        "prefix": args.prefix,
        "uncompressed_sources_published": False,
        "hazard_classes": HAZARD_CLASSES,
        "sources": sources,
        "artifacts": records,
    }
    manifest_path = meta / "manifest.json"
    with manifest_path.open("w") as f:
        json.dump(manifest, f, indent=2)

    # ── P9: versioned manifest snapshot ──────────────────────────────────────
    manifest_digest, _ = sha256_file(manifest_path)
    sha7 = manifest_digest[:7]
    today = date.today().isoformat()
    snapshots_dir = meta / "manifests"
    snapshots_dir.mkdir(parents=True, exist_ok=True)
    snapshot_name = f"manifest-{today}-{sha7}.json"
    snapshot_path = snapshots_dir / snapshot_name
    import shutil
    shutil.copy2(manifest_path, snapshot_path)

    # ── CHECKSUMS.sha256 — gnu sha256sum format ───────────────────────────────
    with (meta / "CHECKSUMS.sha256").open("w") as f:
        for r in records:
            f.write(f"{r['sha256']}  {r['path']}\n")

    # ── P3: manifest.safe.json ────────────────────────────────────────────────
    safe_records = [r for r in records if r["hazard"].get("safe_to_decode_unbounded") is True]
    manifest_safe = {
        "version": 2,
        "bucket": args.bucket,
        "prefix": args.prefix,
        "note": "Filtered to safe_to_decode_unbounded=true. All negative fixtures with class=bomb or class=malformed are excluded.",
        "uncompressed_sources_published": False,
        "hazard_classes": HAZARD_CLASSES,
        "sources": sources,
        "artifacts": safe_records,
    }
    with (meta / "manifest.safe.json").open("w") as f:
        json.dump(manifest_safe, f, indent=2)

    # ── P3: manifest.safe.txt ────────────────────────────────────────────────
    with (meta / "manifest.safe.txt").open("w") as f:
        f.write("# sha256\tsize\tcontent_type\ttier\tpath\tdescription\thazard_class\n")
        for r in safe_records:
            hclass = r["hazard"]["class"]
            f.write(f"{r['sha256']}\t{r['size']}\t{r['content_type']}\t{r['tier']}\t{r['path']}\t{r['description']}\t{hclass}\n")

    # ── P8: decode-expectations.json ─────────────────────────────────────────
    expectations: dict = {}
    for r in records:
        p = r["path"]
        hazard = r["hazard"]
        hclass = hazard["class"]

        if p.startswith("individual/"):
            os_set = r.get("origin_set")
            oname = r.get("origin_name")
            codec = r.get("codec")
            entry: dict = {
                "should_succeed": True,
                "hazard_class": "none",
            }
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
                # Include expansion cap if available from catalog
                if hazard_catalog:
                    neg_rel = p[len("negative/"):]
                    bp = hazard_catalog.get("by_path", {}).get(neg_rel, {})
                    if "expansion_bytes_max" in bp:
                        entry["if_succeed_max_output_bytes"] = bp["expansion_bytes_max"]
            elif hclass == "malformed":
                entry["should_succeed"] = False
                # Try to get expected_error_class from catalog
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
        "note": "Per-artifact decoder expectations. decoded_sha256 is the sha256 of the uncompressed original where known.",
        "expectations": expectations,
    }
    with (meta / "decode-expectations.json").open("w") as f:
        json.dump(decode_exp, f, indent=2)

    # ── publish.tsv ───────────────────────────────────────────────────────────
    # Records sorted by size DESCENDING so each successful upload frees
    # the maximum local bytes. Meta files (tiny) go last.
    meta_files_cc_index = [
        "index.txt", "manifest.json", "CHECKSUMS.sha256",
        "versions.txt", "expected-ratio.json", "README.txt",
        "index.html", "listing.html",
        # P3 new files
        "manifest.safe.json", "manifest.safe.txt",
        # P8 new file
        "decode-expectations.json",
        # Squishy Score board (draft; datasets not yet locked for 1.0.0)
        "squishy-scores.json",
    ]
    # Optional meta files (publish only if they exist)
    optional_meta = ["AGENTS.md", "agent.json", "robots.txt", "llms.txt", "smoke.zip"]

    # Policy: NEVER publish uncompressed bytes. Drop raw/, bare .tar bundles,
    # uncompressed containers (.cpio/.pax/.ar), and zip.store. Compressed
    # equivalents are always available (.tar.gz, deflate-zip, etc.).
    def is_uncompressed(p: str) -> bool:
        return (p.startswith("raw/")
                or p.endswith(".tar")
                or p.endswith((".cpio", ".pax", ".ar"))
                or p.endswith(".zip.store"))

    records_sorted = sorted(
        (r for r in records if not is_uncompressed(r["path"])),
        key=lambda r: -r["size"],
    )
    with (meta / "publish.tsv").open("w") as f:
        for r in records_sorted:
            local = build / r["path"]
            s3key = f"{args.prefix}/{r['path']}"
            f.write(f"{local}\t{s3key}\t{r['content_type']}\t{CC_IMMUTABLE}\n")
        for name in meta_files_cc_index:
            local = meta / name
            if not local.exists():
                continue
            s3key = f"{args.prefix}/{name}"
            ct = content_type_for(name)
            f.write(f"{local}\t{s3key}\t{ct}\t{CC_INDEX}\n")
        for name in optional_meta:
            local = meta / name
            if not local.exists():
                continue
            s3key = f"{args.prefix}/{name}"
            ct = content_type_for(name)
            f.write(f"{local}\t{s3key}\t{ct}\t{CC_INDEX}\n")
        # P9: versioned snapshot — immutable
        s3key_snap = f"{args.prefix}/manifests/{snapshot_name}"
        f.write(f"{snapshot_path}\t{s3key_snap}\tapplication/json\t{CC_IMMUTABLE}\n")

    print(f"manifest written: {len(records)} artifacts", file=sys.stderr)
    print(f"  safe records: {len(safe_records)}", file=sys.stderr)
    print(f"  versioned snapshot: {snapshot_name}", file=sys.stderr)

if __name__ == "__main__":
    main()
