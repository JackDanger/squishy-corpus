#!/usr/bin/env python3
"""make publish — stream the whole Squishy corpus into S3, idempotently.

Driven by build/meta/edition.json (the single source of truth). For each member:

  1. HEAD s3://<bucket>/<prefix>/<key>; if its x-amz-meta-sha256 already matches the
     manifest, SKIP it. This is how publish "regenerates only as necessary" — a member
     already safely in S3 is never re-fetched or re-uploaded.
  2. Otherwise ACQUIRE it by its recipe, STREAMING the source(s) — decompressing on the
     fly (gzip / xz), concatenating multi-part sources, truncating to the pinned size —
     into a single temp file while hashing every byte.
  3. VERIFY sha256 == the manifest value. Bytes that don't match are NEVER uploaded
     (a recipe drift or a moved upstream can't silently corrupt the corpus).
  4. UPLOAD with `aws s3 cp` (transparent multipart; native SHA256 checksum + a
     sha256 user-metadata tag), re-HEAD to confirm, then delete the temp file.

Peak local footprint = one member at a time — a streaming download into a streaming
multipart upload, so the 17 GB corpus never lands on disk all at once.

Modes:
  --plan          offline: print the per-member acquisition plan + peak footprint (no AWS)
  --check         AWS read-only: HEAD every member; report present / missing / drifted
  (default)       publish: acquire + upload every missing or drifted member
  --force         re-acquire + re-upload even members already present
  --only KEY      restrict to one key (repeatable), e.g. --only corpus/minjs.min.js

Needs your AWS creds for everything except --plan:
  aws-vault exec personal -- make publish
  aws-vault exec personal -- make publish ARGS=--check
"""
from __future__ import annotations
import argparse, gzip, hashlib, importlib.util, json, lzma, os, subprocess, sys, tempfile, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = os.environ.get("S3_BUCKET", "squishy-corpus")
PREFIX = os.environ.get("S3_PREFIX", "draft")
UA = {"User-Agent": "squishy-corpus/1.0 (+https://github.com/JackDanger/squishy-corpus)"}
CHUNK = 1 << 22  # 4 MiB streaming reads

# Per-member acquisition recipe, keyed by edition `key`. Source URLs come from
# edition.json; this only records HOW to turn the source bytes into the published
# member. `dec` decompresses each source stream; `limit` truncates to the pinned size
# (the small cores are deterministic head-slices of a larger stream).
#   stream  : fetch the source url(s), decompress per `dec`, concat in order, head `limit`
#   unzip   : the source is a .zip; extract `member`
#   recipe  : built by a dedicated generator (clang-archive / bts-parquet), or a slice
#             script not reproduced inline — relies on the S3 copy (skip-if-present)
RECIPES: dict[str, dict] = {
    # ── direct downloads (bytes as-is) ──
    "corpus/minjs.min.js":               {"how": "stream"},
    "corpus/photo.jpg":                  {"how": "stream"},
    "corpus/movie.mp4":                  {"how": "stream"},
    "corpus/weights.safetensors":        {"how": "stream"},
    "corpus/tool.bin":                   {"how": "stream"},
    "scale/weights/weights-smollm2-135m.safetensors":  {"how": "stream"},
    "scale/weights/weights-qwen2.5-0.5b.safetensors":  {"how": "stream"},
    "scale/weights/weights-qwen2.5-1.5b.safetensors":  {"how": "stream"},
    "scale/media/big-buck-bunny-1080p.mov":            {"how": "stream"},
    # ── decompress on the fly ──
    "corpus/monorepo.tar":               {"how": "stream", "dec": "xz"},
    "scale/monorepo/llvm-project-19.1.0.src.tar":      {"how": "stream", "dec": "xz"},
    "scale/csv/noaa-ghcn-daily-2024-full.csv":         {"how": "stream", "dec": "gzip"},
    "scale/text/enwik9.txt":             {"how": "unzip", "member": "enwik9"},
    # ── deterministic head-slices of a decompressed stream (the small cores) ──
    "corpus/access.log":  {"how": "stream", "dec": "gzip", "limit": 26214398},
    "corpus/ecoli.fastq": {"how": "stream", "dec": "gzip", "limit": 26214271},
    "corpus/data.csv":    {"how": "stream", "dec": "gzip", "limit": 26500039},
    # ── multi-source gunzip + concat ──
    "scale/genome/ecoli-DRR002013-full.fastq":  {"how": "stream", "dec": "gzip"},
    "scale/log/nasa-http-jul-aug-1995.log":     {"how": "stream", "dec": "gzip"},
    "scale/csv/noaa-ghcn-daily-2021-2023.csv":  {"how": "stream", "dec": "gzip"},
    # ── dedicated deterministic generators ──
    "scale/archive/clang-releases-16-17-18-19.tar": {"how": "recipe", "gen": "clang-archive"},
    "corpus/data.parquet":                          {"how": "recipe", "gen": "bts-parquet", "months": "2024-1"},
    # ── recipe-built, not reproduced inline → rely on the S3 copy (skip-if-present) ──
    "corpus/data.json":   {"how": "recipe", "note": "USGS earthquake query snapshot (point-in-time, not re-fetchable)"},
    "corpus/dickens":     {"how": "recipe", "note": "Gutenberg body-slice (refs removed)"},
    "corpus/aozora.txt":  {"how": "recipe", "note": "Aozora author concat"},
    "corpus/markup.xml":  {"how": "recipe", "note": "shaks200.zip → concat XML members"},
    "corpus/data.sqlite": {"how": "recipe", "note": "USDA FoodData Central zip → sqlite build"},
    "scale/columnar/bts-ontime-2022-2024.parquet": {"how": "recipe", "note": "BTS all-string parquet, 2022–2024"},
}

CTYPE = {".json": "application/json", ".csv": "text/csv; charset=utf-8", ".xml": "application/xml",
         ".js": "application/javascript", ".jpg": "image/jpeg", ".mp4": "video/mp4",
         ".mov": "video/quicktime", ".tar": "application/x-tar", ".log": "text/plain; charset=utf-8",
         ".fastq": "text/plain; charset=utf-8", ".txt": "text/plain; charset=utf-8",
         ".parquet": "application/vnd.apache.parquet", ".safetensors": "application/octet-stream",
         ".sqlite": "application/vnd.sqlite3", ".bin": "application/octet-stream"}


def content_type(name: str) -> str:
    for ext, ct in CTYPE.items():
        if name.endswith(ext):
            return ct
    return "application/octet-stream"


def members() -> list[dict]:
    d = json.loads((REPO / "build/meta/edition.json").read_text())
    return d["files"]


def s3_sha(bucket: str, key: str) -> str | None:
    """The x-amz-meta-sha256 of s3://bucket/key, or None if absent/unreadable."""
    r = subprocess.run(["aws", "s3api", "head-object", "--bucket", bucket, "--key", key,
                        "--query", "Metadata.sha256", "--output", "text"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return out if out and out != "None" else None


def _wrap(stream, dec: str | None):
    if dec == "gzip":
        return gzip.GzipFile(fileobj=stream)
    if dec == "xz":
        return lzma.LZMAFile(stream)
    return stream


def acquire(f: dict, rec: dict, dst: Path) -> tuple[str, int]:
    """Stream the member into `dst`, return (sha256, nbytes). Raises on a recipe we
    don't reproduce inline."""
    how = rec["how"]
    urls = [u.strip() for u in (f.get("source_url") or "").split(";") if u.strip()]
    h = hashlib.sha256(); n = 0
    limit = rec.get("limit")

    if how == "stream":
        with dst.open("wb") as out:
            for url in urls:
                if limit is not None and n >= limit:
                    break
                req = urllib.request.Request(url, headers=UA)
                with urllib.request.urlopen(req, timeout=600) as r:
                    src = _wrap(r, rec.get("dec"))
                    for chunk in iter(lambda: src.read(CHUNK), b""):
                        if limit is not None and n + len(chunk) > limit:
                            chunk = chunk[: limit - n]
                        out.write(chunk); h.update(chunk); n += len(chunk)
                        if limit is not None and n >= limit:
                            break
        return h.hexdigest(), n

    if how == "unzip":
        with tempfile.NamedTemporaryFile() as tmp:
            req = urllib.request.Request(urls[0], headers=UA)
            with urllib.request.urlopen(req, timeout=600) as r:
                for chunk in iter(lambda: r.read(CHUNK), b""):
                    tmp.write(chunk)
            tmp.flush()
            with zipfile.ZipFile(tmp.name) as z, z.open(rec["member"]) as m, dst.open("wb") as out:
                for chunk in iter(lambda: m.read(CHUNK), b""):
                    out.write(chunk); h.update(chunk); n += len(chunk)
        return h.hexdigest(), n

    if how == "recipe" and rec.get("gen") == "clang-archive":
        with dst.open("wb") as out:
            for v in ("16.0.0", "17.0.1", "18.1.8", "19.1.0"):
                u = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{v}/clang-{v}.src.tar.xz"
                req = urllib.request.Request(u, headers=UA)
                with urllib.request.urlopen(req, timeout=600) as r:
                    src = lzma.LZMAFile(r)
                    for chunk in iter(lambda: src.read(CHUNK), b""):
                        out.write(chunk); h.update(chunk); n += len(chunk)
        return h.hexdigest(), n

    if how == "recipe" and rec.get("gen") == "bts-parquet":
        s = importlib.util.spec_from_file_location("bts", REPO / "scripts" / "scale-acquire-bts-parquet.py")
        bts = importlib.util.module_from_spec(s); s.loader.exec_module(bts)
        bts.build(dst, bts.parse_months(rec["months"]))
        data = dst.read_bytes()
        return hashlib.sha256(data).hexdigest(), len(data)

    raise RuntimeError(f"recipe not reproduced inline ({rec.get('note', '?')}) — "
                       f"pre-generate it, or rely on the S3 copy")


def upload(local: Path, bucket: str, key: str, sha: str, ctype: str) -> bool:
    r = subprocess.run(["aws", "s3", "cp", str(local), f"s3://{bucket}/{key}",
                        "--metadata", f"sha256={sha}", "--content-type", ctype,
                        "--cache-control", "public, max-age=31536000, immutable",
                        "--checksum-algorithm", "SHA256", "--no-progress"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    UPLOAD FAILED: {r.stderr.strip()[:200]}", file=sys.stderr)
        return False
    return s3_sha(bucket, key) == sha


def have_creds() -> bool:
    return subprocess.run(["aws", "sts", "get-caller-identity"],
                          capture_output=True).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="publish-corpus")
    ap.add_argument("--bucket", default=BUCKET)
    ap.add_argument("--prefix", default=PREFIX)
    ap.add_argument("--plan", action="store_true", help="offline plan, no AWS")
    ap.add_argument("--check", action="store_true", help="AWS read-only presence report")
    ap.add_argument("--force", action="store_true", help="re-upload even if present")
    ap.add_argument("--only", action="append", default=[], help="restrict to key(s)")
    a = ap.parse_args()

    files = [f for f in members() if not a.only or f["key"] in a.only]
    peak = max((f.get("size_bytes") or 0) for f in files) / 1e9

    if a.plan:
        print(f"PLAN · {len(files)} members · s3://{a.bucket}/{a.prefix}/ · peak local ≈ {peak:.2f} GB\n")
        for f in files:
            rec = RECIPES.get(f["key"], {"how": "recipe", "note": "UNKNOWN"})
            how = rec["how"]
            tag = {"stream": "stream" + (f"+{rec['dec']}" if rec.get("dec") else "")
                            + (f" head {rec['limit']/1e6:.0f}MB" if rec.get("limit") else ""),
                   "unzip": f"unzip[{rec.get('member')}]",
                   "recipe": rec.get("gen") or f"RECIPE-ONLY ({rec.get('note','?')})"}[how]
            n = len((f.get("source_url") or "").split(";"))
            multi = f" ·{n}×src" if n > 1 else ""
            print(f"  {f['key']:<46} {(f.get('size_bytes') or 0)/1e6:>8.1f}MB  {tag}{multi}")
        print("\n(run without --plan, with AWS creds, to publish. recipe-only members are "
              "skipped when already in S3.)")
        return 0

    if not have_creds():
        print("AWS credentials missing/expired — run via 'aws-vault exec personal -- make publish' "
              "(or 'aws sso login'). Use --plan for an offline preview.", file=sys.stderr)
        return 2

    n_skip = n_up = n_fail = n_recipe = 0
    for f in files:
        key, want = f["key"], f["sha256"]
        s3key = f"{a.prefix}/{key}"
        rec = RECIPES.get(key, {"how": "recipe", "note": "UNKNOWN"})
        cur = s3_sha(a.bucket, s3key)
        if cur == want and not a.force:
            print(f"  skip   {key}  (present, sha ✓)"); n_skip += 1; continue
        if a.check:
            state = "DRIFTED" if cur else "MISSING"
            print(f"  {state:<8} {key}" + (f"  (s3={cur[:12]} want={want[:12]})" if cur else ""))
            n_up += 1
            continue
        # acquire → verify → upload, peak one file
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / Path(key).name
            try:
                print(f"  fetch  {key}  ({rec['how']}) …", flush=True)
                got, nbytes = acquire(f, rec, dst)
            except Exception as e:
                print(f"  RECIPE {key}  — {e}"); n_recipe += 1; continue
            if got != want:
                print(f"  ✗ SHA MISMATCH {key}: got {got[:12]} want {want[:12]} — NOT uploading",
                      file=sys.stderr); n_fail += 1; continue
            if upload(dst, a.bucket, s3key, want, content_type(Path(key).name)):
                print(f"  ✓ up   {key}  ({nbytes/1e6:.1f} MB, sha ✓)"); n_up += 1
            else:
                n_fail += 1
    print(f"\n— skipped={n_skip} uploaded/missing={n_up} recipe-only={n_recipe} failed={n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
