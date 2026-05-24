#!/usr/bin/env python3
"""Build index.txt, manifest.json, CHECKSUMS.sha256, expected-ratio.json,
and publish.tsv from the contents of build/.

index.txt format (tab-separated, one record per line):
  <sha256>\t<size>\t<content_type>\t<path>\t<description>

publish.tsv format (consumed by publish.sh):
  <local_path>\t<s3_key>\t<content_type>\t<cache_control>
"""
from __future__ import annotations
import argparse, hashlib, json, mimetypes, os, sys
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

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--build",  required=True)
    ap.add_argument("--meta",   required=True)
    ap.add_argument("--bucket", required=True)
    ap.add_argument("--prefix", required=True)
    args = ap.parse_args()

    build = Path(args.build); meta = Path(args.meta)
    meta.mkdir(parents=True, exist_ok=True)

    # Walk build/ collecting everything we should publish, excluding sources/ and meta/.
    skip_dirs = {build / "sources", meta}
    records = []
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
            records.append({
                "path": rel,
                "size": size,
                "sha256": digest,
                "content_type": ct,
                "description": desc,
                "tier": tier,
            })

    records.sort(key=lambda r: r["path"])

    # index.txt — TSV, comment header
    with (meta / "index.txt").open("w") as f:
        f.write("# sha256\tsize\tcontent_type\ttier\tpath\tdescription\n")
        for r in records:
            f.write(f"{r['sha256']}\t{r['size']}\t{r['content_type']}\t{r['tier']}\t{r['path']}\t{r['description']}\n")

    # manifest.json — same data, json
    with (meta / "manifest.json").open("w") as f:
        json.dump({"version": 1, "bucket": args.bucket, "prefix": args.prefix,
                   "artifacts": records}, f, indent=2)

    # CHECKSUMS.sha256 — gnu sha256sum format, relative to build/
    with (meta / "CHECKSUMS.sha256").open("w") as f:
        for r in records:
            f.write(f"{r['sha256']}  {r['path']}\n")

    # expected-ratio.json — derived from individual/<set>/<file>.<codec>[.l<n>]
    ratios: dict[str, dict] = {}
    for r in records:
        p = r["path"]
        if not p.startswith("individual/"):
            continue
        # individual/<set>/<file>.<codec>[.l<n>]  → key: <set>/<file>
        rel = p[len("individual/"):]
        # split off codec/level suffix
        parts = rel.split(".")
        # find the start of the codec/level chain by looking at known extensions
        for i in range(1, len(parts)):
            if "." + parts[i] in CONTENT_TYPES or parts[i].startswith("l"):
                origin = ".".join(parts[:i])
                suffix = ".".join(parts[i:])
                break
        else:
            origin, suffix = rel, ""
        ratios.setdefault(origin, {})[suffix] = r["size"]
    # also include raw sizes as baseline
    raw_sizes = {r["path"][len("raw/"):]: r["size"] for r in records if r["path"].startswith("raw/")}
    with (meta / "expected-ratio.json").open("w") as f:
        json.dump({"raw_sizes": raw_sizes, "compressed_sizes": ratios}, f, indent=2)

    # publish.tsv — what publish.sh consumes.
    # Records are sorted by size DESCENDING so each successful upload frees
    # the maximum local bytes. On tight disk, this is the difference between
    # the build completing or stalling out. Meta files (tiny) go last.
    meta_files = ["index.txt", "manifest.json", "CHECKSUMS.sha256",
                  "versions.txt", "expected-ratio.json", "README.txt",
                  "index.html", "listing.html"]
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
        for name in meta_files:
            local = meta / name
            if not local.exists():
                continue
            s3key = f"{args.prefix}/{name}"
            ct = content_type_for(name)
            f.write(f"{local}\t{s3key}\t{ct}\t{CC_INDEX}\n")

    print(f"manifest written: {len(records)} artifacts", file=sys.stderr)

if __name__ == "__main__":
    main()
