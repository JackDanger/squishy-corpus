#!/usr/bin/env python3
"""Stream the Squishy corpus into S3, idempotently, with a two-class provenance model.

Every member is one of two ORIGINS:

  • upstream — third-party and **independently retrievable**: a pinned, immutable
    upstream URL plus a deterministic decode (gunzip / xz / unzip / head-slice). Anyone
    can re-fetch these and reproduce the exact bytes, so we don't have to be their
    keeper — `publish`/`release` re-fetch them and verify the sha256.

  • minted — bytes that **might change (or vanish) if re-fetched**, or that WE built
    (slices, concatenations, snapshots, format conversions). These cannot be trusted to
    re-derive, so we mint them ONCE and keep our own canonical copy in the
    source-of-record prefix (s3://<bucket>/source/<key>). That copy is the authority;
    a release copies it byte-for-byte into the frozen edition.

Three prefixes in one bucket:
  source/   — write-once source-of-record for every MINTED member (our authority)
  draft/    — the working/served corpus (what the live site fetches)
  <edition>/— a frozen release (the edition year, e.g. 2026), copied from source/ (minted) + upstream (re-fetched)

Modes:
  --plan              offline: print each member's origin + acquisition method (no AWS)
  --check             AWS read-only: presence/drift report for the working prefix
  --mint              ensure every MINTED member exists in source/ (generate-if-reproducible,
                      else promote an existing copy); upstream members are skipped
  (default)           publish to draft/: upstream → fetch+verify; minted → copy from source/
  --release EDITION   freeze into EDITION/: minted ← server-side copy from source/;
                      upstream ← re-fetch+verify (proving independent retrievability)
  --force             redo even if the destination already matches
  --only KEY          restrict to one key (repeatable)

Needs creds for everything but --plan:  aws-vault exec personal -- make publish
"""
from __future__ import annotations
import argparse, gzip, hashlib, importlib.util, json, lzma, os, subprocess, sys, tarfile, tempfile, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = os.environ.get("S3_BUCKET", "squishy-corpus")
SOURCE_PREFIX = os.environ.get("S3_SOURCE_PREFIX", "source")   # our source-of-record (minted authority)
WORK_PREFIX = os.environ.get("S3_PREFIX", "draft")             # working/served corpus
# The CloudFront distribution that serves the working prefix at squishy.jackdanger.com.
# Its origin path is /draft, so a published key `corpus/x` is public at `/corpus/x` —
# the invalidation paths below are the PUBLIC paths (leading slash, no draft/ prefix).
CF_DISTRIBUTION_ID = os.environ.get("CF_DISTRIBUTION_ID", "E2UVD5LCNEUNSU")
UA = {"User-Agent": "squishy-corpus/1.0 (+https://github.com/JackDanger/squishy-corpus)"}
CHUNK = 1 << 22  # 4 MiB streaming reads

# Per-member recipe, keyed by edition `key`.
#   origin : "upstream" (re-fetchable, reproduced here) | "minted" (we keep the canonical copy)
#   how    : stream | unzip | recipe       (how the bytes are produced when we CAN)
#   dec    : gzip | xz                      (decompress each source stream)
#   limit  : bytes                          (deterministic head-slice of a decompressed stream)
#   gen    : clang-archive | bts-parquet    (a deterministic generator we own → minted but mintable)
#   note   : why a minted member can't be regenerated inline (relies on the kept copy)
RECIPES: dict[str, dict] = {
    # ── upstream: immutable source + deterministic decode → independently retrievable ──
    "corpus/minjs.min.js":               {"origin": "upstream", "how": "stream"},
    "corpus/monorepo.tar":               {"origin": "upstream", "how": "stream", "dec": "xz"},
    "corpus/access.log":   {"origin": "upstream", "how": "stream", "dec": "gzip", "limit": 26214398},
    "corpus/ecoli.fastq":  {"origin": "upstream", "how": "stream", "dec": "gzip", "limit": 26214271},
    "corpus/tool.bin":                   {"origin": "upstream", "how": "stream"},
    "corpus/movie.mp4":                  {"origin": "upstream", "how": "stream"},
    # executable-format members: prebuilt release archives → re-fetch + extract the member.
    "corpus/engine.wasm": {"origin": "upstream", "how": "unzip",
                           "member": "sqlite-wasm-3530200/jswasm/sqlite3.wasm"},
    "corpus/winexe.exe":  {"origin": "upstream", "how": "unzip",
                           "member": "fd-v10.4.2-x86_64-pc-windows-msvc/fd.exe"},
    "corpus/armexe.elf":  {"origin": "upstream", "how": "untar",
                           "member": "hyperfine-v1.20.0-aarch64-unknown-linux-gnu/hyperfine"},
    "scale/monorepo/llvm-project-19.1.0.src.tar":  {"origin": "upstream", "how": "stream", "dec": "xz"},
    "scale/media/big-buck-bunny-1080p.mov":        {"origin": "upstream", "how": "stream"},
    "scale/text/enwik9.txt":             {"origin": "upstream", "how": "unzip", "member": "enwik9"},
    "scale/genome/ecoli-DRR002013-full.fastq":  {"origin": "upstream", "how": "stream", "dec": "gzip"},
    "scale/log/nasa-http-jul-aug-1995.log":     {"origin": "upstream", "how": "stream", "dec": "gzip"},
    # HuggingFace weights, pinned to an immutable commit revision (resolve/<sha>/…) so a
    # re-fetch reproduces the exact bytes → independently retrievable, not minted.
    "corpus/weights.safetensors":                      {"origin": "upstream", "how": "stream"},
    "scale/weights/weights-qwen2.5-0.5b.safetensors":  {"origin": "upstream", "how": "stream"},
    "scale/weights/weights-qwen2.5-1.5b.safetensors":  {"origin": "upstream", "how": "stream"},
    # ── minted, but reproducible by OUR deterministic generator (mintable from scratch) ──
    "scale/archive/clang-releases-16-17-18-19.tar": {"origin": "minted", "how": "recipe", "gen": "clang-archive"},
    "corpus/data.parquet":                          {"origin": "minted", "how": "recipe", "gen": "bts-parquet", "months": "2024-1"},
    # ── minted, NOT regenerable inline → the kept source/ copy is the authority ──
    "corpus/dickens":     {"origin": "minted", "note": "Gutenberg body-slice (editions drift)"},
    "corpus/aozora.txt":  {"origin": "minted", "note": "Aozora author concat (index drifts)"},
    "corpus/markup.xml":  {"origin": "minted", "note": "our concat of shaks200.zip members"},
    "corpus/data.json":   {"origin": "minted", "note": "USGS earthquake query snapshot (live, not re-fetchable)"},
    "corpus/data.csv":    {"origin": "minted", "note": "NOAA GHCN head-slice (by_year files are revised)"},
    "corpus/data.sqlite": {"origin": "minted", "note": "our sqlite build from USDA FoodData Central"},
    "corpus/photo.jpg":   {"origin": "minted", "note": "Wikimedia Commons file (overwritable upstream)"},
    "corpus/symbols.dwarf": {"origin": "minted", "note": "Lua 5.4.8 -g build, DWARF companion "
                             "(toolchain-specific bytes — not byte-reproducible; the kept copy is the authority; "
                             "scripts/acquire-binaries.py rebuilds a fresh one)"},
    "scale/csv/noaa-ghcn-daily-2024-full.csv":  {"origin": "minted", "note": "NOAA GHCN by_year (revised over time)"},
    "scale/csv/noaa-ghcn-daily-2021-2023.csv":  {"origin": "minted", "note": "NOAA GHCN by_year (revised over time)"},
    "scale/columnar/bts-ontime-2022-2024.parquet": {"origin": "minted", "note": "our pyarrow parquet build"},
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


def rec_of(key: str) -> dict:
    return RECIPES.get(key, {"origin": "minted", "note": "UNCLASSIFIED"})


def members() -> list[dict]:
    return json.loads((REPO / "build/meta/edition.json").read_text())["files"]


def have_creds() -> bool:
    return subprocess.run(["aws", "sts", "get-caller-identity"], capture_output=True).returncode == 0


def s3_sha(bucket: str, key: str) -> str | None:
    r = subprocess.run(["aws", "s3api", "head-object", "--bucket", bucket, "--key", key,
                        "--query", "Metadata.sha256", "--output", "text"], capture_output=True, text=True)
    out = r.stdout.strip()
    return out if r.returncode == 0 and out and out != "None" else None


def s3_cp(args: list[str], where: str) -> bool:
    r = subprocess.run(["aws", "s3", "cp", *args, "--no-progress"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"    {where} FAILED: {r.stderr.strip()[:200]}", file=sys.stderr)
    return r.returncode == 0


def put_local(local: Path, bucket: str, key: str, sha: str, ctype: str, cache: bool) -> bool:
    cc = ["--cache-control", "public, max-age=31536000, immutable"] if cache else []
    ok = s3_cp([str(local), f"s3://{bucket}/{key}", "--metadata", f"sha256={sha}",
                "--content-type", ctype, "--checksum-algorithm", "SHA256", *cc], "UPLOAD")
    return ok and s3_sha(bucket, key) == sha


def invalidate_cdn(public_paths: list[str], dist_id: str = CF_DISTRIBUTION_ID) -> bool:
    """Invalidate the just-published paths on CloudFront so the new bytes are served
    immediately instead of after the edge TTL. `public_paths` are the public-facing
    paths (e.g. /corpus/dickens) — the distribution's /draft origin path is added by
    CloudFront, so we must NOT include the draft/ prefix here. Past a handful of paths
    a single /* wildcard is cheaper and simpler than enumerating them."""
    if not public_paths:
        return True
    args = ["/*"] if len(public_paths) > 12 else sorted(set(public_paths))
    r = subprocess.run(["aws", "cloudfront", "create-invalidation", "--distribution-id", dist_id,
                        "--paths", *args, "--query", "Invalidation.Id", "--output", "text"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  CDN invalidation FAILED ({dist_id}): {r.stderr.strip()[:200]}", file=sys.stderr)
        return False
    print(f"  CDN invalidation {r.stdout.strip()} created for "
          f"{'/* (all paths)' if args == ['/*'] else f'{len(args)} path(s)'}")
    return True


def copy_s3(bucket: str, src_key: str, dst_key: str, sha: str, ctype: str) -> bool:
    """Server-side copy src→dst, re-stamping sha256 metadata; verify by HEAD."""
    ok = s3_cp([f"s3://{bucket}/{src_key}", f"s3://{bucket}/{dst_key}",
                "--metadata", f"sha256={sha}", "--metadata-directive", "REPLACE",
                "--content-type", ctype, "--cache-control", "public, max-age=31536000, immutable",
                "--checksum-algorithm", "SHA256"], "COPY")
    return ok and s3_sha(bucket, dst_key) == sha


def _wrap(stream, dec):
    return gzip.GzipFile(fileobj=stream) if dec == "gzip" else lzma.LZMAFile(stream) if dec == "xz" else stream


def acquire(f: dict, rec: dict, dst: Path) -> tuple[str, int]:
    """Reproduce a member into `dst` from its source(s); return (sha256, nbytes).
    Works for upstream members and for minted members that have a deterministic `gen`."""
    urls = [u.strip() for u in (f.get("source_url") or "").split(";") if u.strip()]
    h = hashlib.sha256(); n = 0; limit = rec.get("limit")
    gen = rec.get("gen")

    if gen == "clang-archive":
        with dst.open("wb") as out:
            for v in ("16.0.0", "17.0.1", "18.1.8", "19.1.0"):
                u = f"https://github.com/llvm/llvm-project/releases/download/llvmorg-{v}/clang-{v}.src.tar.xz"
                with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=600) as r:
                    src = lzma.LZMAFile(r)
                    for c in iter(lambda: src.read(CHUNK), b""):
                        out.write(c); h.update(c); n += len(c)
        return h.hexdigest(), n
    if gen == "bts-parquet":
        s = importlib.util.spec_from_file_location("bts", REPO / "scripts" / "scale-acquire-bts-parquet.py")
        bts = importlib.util.module_from_spec(s); s.loader.exec_module(bts)
        bts.build(dst, bts.parse_months(rec["months"]))
        data = dst.read_bytes(); return hashlib.sha256(data).hexdigest(), len(data)
    if rec.get("how") == "unzip":
        with tempfile.NamedTemporaryFile() as tmp:
            with urllib.request.urlopen(urllib.request.Request(urls[0], headers=UA), timeout=600) as r:
                for c in iter(lambda: r.read(CHUNK), b""):
                    tmp.write(c)
            tmp.flush()
            with zipfile.ZipFile(tmp.name) as z, z.open(rec["member"]) as m, dst.open("wb") as out:
                for c in iter(lambda: m.read(CHUNK), b""):
                    out.write(c); h.update(c); n += len(c)
        return h.hexdigest(), n
    if rec.get("how") == "untar":                            # .tar / .tar.gz / .tar.xz → one member
        with tempfile.NamedTemporaryFile() as tmp:
            with urllib.request.urlopen(urllib.request.Request(urls[0], headers=UA), timeout=600) as r:
                for c in iter(lambda: r.read(CHUNK), b""):
                    tmp.write(c)
            tmp.flush()
            with tarfile.open(tmp.name, "r:*") as t:
                m = t.extractfile(rec["member"])
                if m is None:
                    raise RuntimeError(f"tar member not found: {rec['member']}")
                with dst.open("wb") as out:
                    for c in iter(lambda: m.read(CHUNK), b""):
                        out.write(c); h.update(c); n += len(c)
        return h.hexdigest(), n
    if rec.get("how") == "stream":
        with dst.open("wb") as out:
            for url in urls:
                if limit is not None and n >= limit:
                    break
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=600) as r:
                    src = _wrap(r, rec.get("dec"))
                    for c in iter(lambda: src.read(CHUNK), b""):
                        if limit is not None and n + len(c) > limit:
                            c = c[: limit - n]
                        out.write(c); h.update(c); n += len(c)
                        if limit is not None and n >= limit:
                            break
        return h.hexdigest(), n
    raise RuntimeError(f"not reproducible inline ({rec.get('note', '?')})")


# ── modes ────────────────────────────────────────────────────────────────────

def do_plan(files):
    peak = max((f.get("size_bytes") or 0) for f in files) / 1e9
    up = sum(1 for f in files if rec_of(f["key"])["origin"] == "upstream")
    print(f"PLAN · {len(files)} members ({up} upstream, {len(files)-up} minted) · peak local ≈ {peak:.2f} GB\n")
    for f in files:
        rec = rec_of(f["key"]); o = rec["origin"]
        if o == "upstream":
            tag = "stream" + (f"+{rec['dec']}" if rec.get("dec") else "") + \
                  (f" head {rec['limit']/1e6:.0f}MB" if rec.get("limit") else "") + \
                  (f" unzip[{rec['member']}]" if rec.get("how") == "unzip" else "")
            auth = "re-fetch upstream"
        else:
            tag = rec.get("gen") or f"KEEP ({rec.get('note', '?')})"
            auth = f"source/{f['key']}"
        print(f"  {o:<8} {f['key']:<46} {(f.get('size_bytes') or 0)/1e6:>8.1f}MB  {tag}")
        print(f"           authority: {auth}")
    print("\n(minted bytes live in source/ — our authority; upstream are re-fetched + sha-verified.)")
    return 0


def do_check(files, bucket, prefix):
    for f in files:
        cur = s3_sha(bucket, f"{prefix}/{f['key']}")
        st = "ok" if cur == f["sha256"] else ("DRIFTED" if cur else "MISSING")
        print(f"  {st:<8} {rec_of(f['key'])['origin']:<8} {f['key']}")
    return 0


def do_mint(files, bucket, force):
    """Ensure every MINTED member exists in source/ with the right sha (our authority)."""
    nskip = nmint = nfail = nmissing = 0
    for f in files:
        key, want, rec = f["key"], f["sha256"], rec_of(f["key"])
        if rec["origin"] != "minted":
            continue
        src_key = f"{SOURCE_PREFIX}/{key}"
        if s3_sha(bucket, src_key) == want and not force:
            print(f"  have   {key}  (source ✓)"); nskip += 1; continue
        ct = content_type(Path(key).name)
        # Prefer promoting the already-verified working copy (a cheap server-side copy)
        # over re-downloading/rebuilding — the draft bytes already match the manifest.
        work_key = f"{WORK_PREFIX}/{key}"
        if s3_sha(bucket, work_key) == want:
            if copy_s3(bucket, work_key, src_key, want, ct):
                print(f"  mint   {key}  (promoted {WORK_PREFIX}/ → source/)"); nmint += 1
            else:
                nfail += 1
            continue
        if rec.get("gen"):                                  # no working copy → regenerate from our recipe
            with tempfile.TemporaryDirectory() as d:
                dst = Path(d) / Path(key).name
                try:
                    got, nbytes = acquire(f, rec, dst)
                except Exception as e:
                    print(f"  FAIL   {key}: {e}"); nfail += 1; continue
                if got != want:
                    print(f"  ✗ SHA {key}: {got[:12]} != {want[:12]}", file=sys.stderr); nfail += 1; continue
                if put_local(dst, bucket, src_key, want, ct, cache=False):
                    print(f"  mint   {key}  ({nbytes/1e6:.1f} MB regenerated → source/)"); nmint += 1
                else:
                    nfail += 1
            continue
        # Bootstrap: seed source/ from the verified LOCAL canonical copy (build/raw/<key>).
        # This is how a minted member whose bytes aren't byte-reproducible (e.g. a compiled
        # artifact) first enters the source-of-record — sha-gated, so we only ever upload
        # bytes that match the manifest.
        local = REPO / "build" / "raw" / key
        if local.exists() and hashlib.sha256(local.read_bytes()).hexdigest() == want:
            if put_local(local, bucket, src_key, want, ct, cache=False):
                print(f"  mint   {key}  (seeded source/ from local {local.relative_to(REPO)})"); nmint += 1
            else:
                nfail += 1
            continue
        print(f"  ✗ NO SOURCE for minted {key}: not in source/ or {WORK_PREFIX}/, no local "
              f"build/raw copy, and not regenerable — provide the original bytes ({rec.get('note', '?')})",
              file=sys.stderr)
        nmissing += 1
    print(f"\n— mint: have={nskip} minted={nmint} missing={nmissing} failed={nfail}")
    return 1 if (nfail or nmissing) else 0


def do_publish(files, bucket, prefix, force):
    """Populate the working prefix: upstream → fetch+verify; minted → copy from source/.
    Anything actually (re)written to the SERVED prefix is then invalidated on CloudFront,
    so the new bytes go live immediately rather than after the edge TTL expires."""
    nskip = nput = nfail = nneed = 0
    written: list[str] = []                                  # keys we (re)wrote this run
    for f in files:
        key, want, rec = f["key"], f["sha256"], rec_of(f["key"])
        dst_key = f"{prefix}/{key}"
        if s3_sha(bucket, dst_key) == want and not force:
            print(f"  skip   {key}  (present ✓)"); nskip += 1; continue
        ct = content_type(Path(key).name)
        if rec["origin"] == "minted":
            src_key = f"{SOURCE_PREFIX}/{key}"
            if s3_sha(bucket, src_key) != want:
                print(f"  NEEDS-MINT  {key}  (no authoritative copy in source/ — run `make mint`)",
                      file=sys.stderr); nneed += 1; continue
            ok = copy_s3(bucket, src_key, dst_key, want, ct)
            print(f"  ✓ copy {key}  (source/ → {prefix}/)" if ok else f"  FAIL copy {key}")
            nput += ok; nfail += (not ok)
            if ok: written.append(key)
        else:                                                # upstream → fetch + verify
            with tempfile.TemporaryDirectory() as d:
                dst = Path(d) / Path(key).name
                try:
                    got, nbytes = acquire(f, rec, dst)
                except Exception as e:
                    print(f"  FAIL   {key}: {e}"); nfail += 1; continue
                if got != want:
                    print(f"  ✗ SHA {key}: {got[:12]} != {want[:12]} — NOT uploading", file=sys.stderr)
                    nfail += 1; continue
                if put_local(dst, bucket, dst_key, want, ct, cache=True):
                    print(f"  ✓ up   {key}  ({nbytes/1e6:.1f} MB, sha ✓)"); nput += 1
                    written.append(key)
                else:
                    nfail += 1
    print(f"\n— publish[{prefix}]: skipped={nskip} written={nput} needs-mint={nneed} failed={nfail}")
    # Only the served working prefix sits behind the CDN; a release/other prefix doesn't.
    if written and prefix == WORK_PREFIX:
        invalidate_cdn([f"/{k}" for k in written])
    elif written:
        print(f"  (no CDN invalidation: prefix {prefix!r} is not the served {WORK_PREFIX!r} prefix)")
    return 1 if (nfail or nneed) else 0


def do_release(files, bucket, edition, force):
    """Freeze into EDITION/: minted ← source/ (server-side copy); upstream ← re-fetch+verify."""
    nskip = nput = nfail = nneed = 0
    for f in files:
        key, want, rec = f["key"], f["sha256"], rec_of(f["key"])
        rel_key = f"{edition}/{key}"
        if s3_sha(bucket, rel_key) == want and not force:
            print(f"  frozen {key}  (already in {edition}/)"); nskip += 1; continue
        ct = content_type(Path(key).name)
        if rec["origin"] == "minted":
            src_key = f"{SOURCE_PREFIX}/{key}"
            if s3_sha(bucket, src_key) != want:
                print(f"  NEEDS-MINT  {key}  (mint it before releasing)", file=sys.stderr); nneed += 1; continue
            ok = copy_s3(bucket, src_key, rel_key, want, ct)
            print(f"  ✓ release {key}  (source/ → {edition}/)" if ok else f"  FAIL {key}")
            nput += ok; nfail += (not ok)
        else:                                                # upstream → re-fetch, proving retrievability
            with tempfile.TemporaryDirectory() as d:
                dst = Path(d) / Path(key).name
                try:
                    got, _ = acquire(f, rec, dst)
                except Exception as e:
                    print(f"  FAIL   {key}: {e}"); nfail += 1; continue
                if got != want:
                    print(f"  ✗ SHA {key}: upstream drifted ({got[:12]} != {want[:12]})", file=sys.stderr)
                    nfail += 1; continue
                if put_local(dst, bucket, rel_key, want, ct, cache=True):
                    print(f"  ✓ release {key}  (re-fetched + verified → {edition}/)"); nput += 1
                else:
                    nfail += 1
    print(f"\n— release[{edition}]: frozen={nskip} written={nput} needs-mint={nneed} failed={nfail}")
    return 1 if (nfail or nneed) else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="publish-corpus")
    ap.add_argument("--bucket", default=BUCKET)
    ap.add_argument("--prefix", default=WORK_PREFIX, help="working prefix (default: draft)")
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--mint", action="store_true")
    ap.add_argument("--release", metavar="EDITION", help="freeze into this prefix, the edition year, e.g. 2026")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", action="append", default=[])
    a = ap.parse_args()

    files = [f for f in members() if not a.only or f["key"] in a.only]
    if a.plan:
        return do_plan(files)
    if not have_creds():
        print("AWS creds missing/expired — run via 'aws-vault exec personal -- …' "
              "(or 'aws sso login'). Use --plan for an offline preview.", file=sys.stderr)
        return 2
    if a.check:
        return do_check(files, a.bucket, a.prefix)
    if a.mint:
        return do_mint(files, a.bucket, a.force)
    if a.release:
        return do_release(files, a.bucket, a.release, a.force)
    return do_publish(files, a.bucket, a.prefix, a.force)


if __name__ == "__main__":
    raise SystemExit(main())
