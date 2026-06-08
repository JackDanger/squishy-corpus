#!/usr/bin/env python3
"""squishy-calculate — stream the Squishy corpus and compute a Squishy Score.

One command. Streams every core file from the published mirror, verifies each
against the published SHA-256 (fails closed if a hash is missing or wrong),
caches verified bytes locally, runs your codec once per file under the canonical
rules, and prints the Squishy Score + category table.

  squishy-calculate --cmd "zstd -19 -c"
  squishy-calculate --cmd "xz -9 -c" --verify --decompress "xz -dc"
  squishy-calculate --cmd "mycodec -o {out} {in}"        # file-arg codecs

Properties:
  • streaming   — pulls files over HTTPS from --base (S3/CloudFront); no tarball.
  • verified    — every file checked against <base>/CHECKSUMS.sha256; a
                  missing or mismatched hash is a hard error (fail closed).
  • resumable   — interrupt any time; re-run continues. Verified bytes and
                  per-file results are cached and reused.
  • idempotent  — same codec + same bytes ⇒ cached result reused, same number.
  • obvious     — clear progress and one headline number.

The score math, the named core, and the category map all come from squishy.py —
this tool only adds streaming + caching + resume around the canonical runner.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
EDITION = "Squishy-2026"
DEFAULT_BASE = "https://squishy.jackdanger.com"


def load_squishy():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
    return m


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch_checksums(base: str) -> dict[str, str]:
    """Required. Maps 'core/<name>' -> sha256. Fail closed if unavailable."""
    url = f"{base}/CHECKSUMS.sha256"
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            text = r.read().decode()
    except Exception as e:
        sys.exit(f"FATAL: cannot fetch checksums {url}: {e}\n"
                 f"       refusing to score unverified bytes (fail closed).")
    want = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            want[parts[1]] = parts[0].lower()
    if not want:
        sys.exit(f"FATAL: {url} has no checksums; fail closed.")
    return want


def ensure_file(base: str, key: str, want_sha: str, cache: Path) -> Path:
    """Return a local path to the verified file `key` (e.g. 'core/data.csv').
    Reuse cache or an in-repo copy if the hash matches; else stream + verify.
    Fail closed on any hash mismatch."""
    dst = cache / key
    dst.parent.mkdir(parents=True, exist_ok=True)
    # already-verified cache hit
    if dst.exists() and sha256_file(dst) == want_sha:
        return dst
    # seed from an in-repo raw copy if present and matching (saves a download)
    seed = REPO / "build" / "raw" / key
    if seed.exists() and sha256_file(seed) == want_sha:
        shutil.copyfile(seed, dst)
        return dst
    # stream to a .part, hashing as we go, then atomically rename
    url = f"{base}/{key}"
    tmp = dst.with_suffix(dst.suffix + ".part")
    h = hashlib.sha256()
    try:
        with urllib.request.urlopen(url, timeout=120) as r, tmp.open("wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                h.update(chunk); f.write(chunk)
    except Exception as e:
        tmp.unlink(missing_ok=True)
        sys.exit(f"FATAL: download failed for {url}: {e}")
    got = h.hexdigest()
    if got != want_sha:
        tmp.unlink(missing_ok=True)
        sys.exit(f"FATAL: checksum mismatch for {key}\n  want {want_sha}\n  got  {got}\n"
                 f"       refusing to score altered bytes (fail closed).")
    os.replace(tmp, dst)
    return dst


def codec_version(cmd: str) -> str:
    tok = cmd.replace("{in}", "").replace("{out}", "").split()
    tool = tok[0] if tok else cmd
    for flag in ("--version", "-V", "version"):
        try:
            out = subprocess.run([tool, flag], capture_output=True, text=True, timeout=10)
            line = (out.stdout or out.stderr).splitlines()
            if line:
                return line[0].strip()[:80]
        except Exception:
            continue
    return "unknown"


def slug(cmd: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", cmd).strip("-")[:48] or "codec"


def compress_size(cmd: str, path: Path) -> tuple[int, float]:
    """Compressed size of `path` under `cmd`, streaming from disk so multi-GB files
    never load fully into RAM. Supports stdin→stdout filters and {in}/{out} file-arg
    codecs. Returns (compressed_bytes, seconds)."""
    t0 = time.perf_counter()
    if "{in}" in cmd:
        with tempfile.TemporaryDirectory() as d:
            op = os.path.join(d, "out")
            run = cmd.replace("{in}", str(path)).replace("{out}", op)
            proc = subprocess.run(run, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            if "{out}" in cmd:
                sizes = [os.path.getsize(os.path.join(d, f)) for f in os.listdir(d)]
                size = max(sizes) if sizes else len(proc.stdout)
            else:
                size = len(proc.stdout)
        return size, time.perf_counter() - t0
    with open(path, "rb") as f:
        proc = subprocess.Popen(cmd, shell=True, stdin=f, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        n = 0
        for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
            n += len(chunk)
        proc.stdout.close(); rc = proc.wait()
    if rc != 0:
        return 0, time.perf_counter() - t0
    return n, time.perf_counter() - t0


def verify_roundtrip_file(comp_cmd: str, decomp_cmd: str, path: Path) -> tuple[bool, int, float]:
    """Lossless round-trip that STREAMS through temp files (memory-safe for multi-GB
    rungs): compress path→.c, decompress .c→.d, compare sha256(.d)==sha256(path).
    Returns (lossless, compressed_size, seconds) — the compressed size is reused as the
    score's measurement so verify compresses each file ONCE, not twice. Supports
    stdin→stdout filters and {in}/{out} file-arg codecs."""
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory() as d:
        comp = os.path.join(d, "c"); dec = os.path.join(d, "d")
        if "{in}" in comp_cmd:
            run = comp_cmd.replace("{in}", str(path)).replace("{out}", comp)
            r = subprocess.run(run, shell=True, stdout=(subprocess.DEVNULL if "{out}" in comp_cmd
                               else open(comp, "wb")), stderr=subprocess.DEVNULL)
        else:
            with open(path, "rb") as fi, open(comp, "wb") as fo:
                r = subprocess.run(comp_cmd, shell=True, stdin=fi, stdout=fo, stderr=subprocess.DEVNULL)
        if r.returncode != 0 or not os.path.exists(comp):
            return False, 0, time.perf_counter() - t0
        csize = os.path.getsize(comp)
        if "{in}" in decomp_cmd:
            run = decomp_cmd.replace("{in}", comp).replace("{out}", dec)
            r = subprocess.run(run, shell=True, stdout=(subprocess.DEVNULL if "{out}" in decomp_cmd
                               else open(dec, "wb")), stderr=subprocess.DEVNULL)
        else:
            with open(comp, "rb") as fi, open(dec, "wb") as fo:
                r = subprocess.run(decomp_cmd, shell=True, stdin=fi, stdout=fo, stderr=subprocess.DEVNULL)
        if r.returncode != 0 or not os.path.exists(dec):
            return False, csize, time.perf_counter() - t0
        ok = sha256_file(Path(dec)) == sha256_file(path)
        return ok, csize, time.perf_counter() - t0


def main() -> int:
    ap = argparse.ArgumentParser(prog="squishy-calculate", description="Stream the Squishy corpus and compute a Squishy Score.")
    ap.add_argument("--cmd", required=True, help='compressor command; stdin→stdout, or with {in}/{out}')
    ap.add_argument("--base", default=os.environ.get("SQUISHY_BASE", DEFAULT_BASE), help="corpus base URL")
    ap.add_argument("--cache", default=os.environ.get("SQUISHY_CACHE", str(Path.home() / ".cache" / "squishy" / EDITION)))
    ap.add_argument("--verify", action="store_true", help="round-trip each file (requires --decompress)")
    ap.add_argument("--decompress", help="decompressor command for --verify; stdin→stdout or {in}/{out}")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON")
    ap.add_argument("--fresh", action="store_true", help="ignore cached per-file results (still reuse verified bytes)")
    args = ap.parse_args()
    if args.verify and not args.decompress:
        ap.error("--verify requires --decompress \"<cmd>\"")

    sq = load_squishy()
    cache = Path(args.cache); cache.mkdir(parents=True, exist_ok=True)
    ver = codec_version(args.cmd)
    state_path = cache / "results" / f"{slug(args.cmd)}.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state = {}
    if state_path.exists() and not args.fresh:
        try:
            state = json.loads(state_path.read_text())
            if state.get("codec_version") != ver or state.get("cmd") != args.cmd:
                state = {}                       # codec changed → recompute
        except Exception:
            state = {}
    pf = state.get("per_file", {})

    # The whole edition (core + large rungs) — every file counts, one vote each. The
    # single source of truth is build/meta/edition.json, which pins every file's key +
    # sha256. We verify each streamed file against that sha (fail closed); edition.json
    # is the published, pinned manifest.
    sc = sq.scored_corpus()
    points = [pt for ks in sc.values() for pts in ks.values() for pt in pts]
    n_total = len(points)
    print(f"Squishy-2026 · {n_total} files (core + large rungs) · codec: {args.cmd}  [{ver}]")
    print(f"  base:  {args.base}\n  cache: {cache}")

    for i, pt in enumerate(points, 1):
        name, key, sha = pt["name"], pt["key"], pt.get("sha256")
        gb = (pt.get("size_bytes") or 0) / 1e9
        if not sha:
            sys.exit(f"FATAL: no published sha256 for {name} in edition.json (fail closed).")
        cached = pf.get(name)
        if cached and cached.get("in_sha") == sha and not args.fresh and (cached.get("verified") or not args.verify):
            print(f"  [{i:>2}/{n_total}] {name:<34} cached  {cached['ratio']:.2f}×")
            continue
        p = ensure_file(args.base, key, sha, cache)            # streams + verifies vs sha (fail closed)
        size_in = p.stat().st_size
        verified = None
        if args.verify:                                        # one compression: round-trip + size
            verified, size_c, secs = verify_roundtrip_file(args.cmd, args.decompress, p)
            if not verified:
                sys.exit(f"FATAL: round-trip FAILED for {name} — codec is not lossless on this file.")
        else:
            size_c, secs = compress_size(args.cmd, p)          # streamed from disk (memory-safe)
        if size_c <= 0:
            sys.exit(f"FATAL: codec produced {size_c} bytes for {name}.")
        ratio = size_in / size_c
        pf[name] = {"in_sha": sha, "size_in": size_in, "size_comp": size_c,
                    "ratio": round(ratio, 6), "kind": pt.get("kind"), "category": pt.get("category"),
                    "verified": bool(verified) if verified is not None else None}
        state.update({"edition": EDITION, "cmd": args.cmd, "codec_version": ver,
                      "base": args.base, "per_file": pf, "updated": time.time()})
        state_path.write_text(json.dumps(state, indent=2))     # persist → interruptible / resumable
        vflag = " ✓rt" if verified else ""
        print(f"  [{i:>2}/{n_total}] {name:<34} {ratio:6.2f}×  "
              f"({size_in/1e6:.0f}→{size_c/1e6:.1f}MB, {secs:.0f}s){vflag}")

    # headline = the plain geomean of per-file ratio over the whole edition (one vote
    # per file), computed by the canonical scorer in squishy.py from the cached ratios.
    res = sq.corpus_score(lambda pt: pf[pt["name"]]["ratio"] if pt["name"] in pf else None)
    score = res["squishy_score"]
    cat_scores = res["categories"]
    complete = res["complete"]
    # byte-weighted corpus bpb (the operational rate) — the Squishy Score is a
    # dimensionless quality index, NOT a bit rate; never derive bpb from it.
    tot_in = sum(pf[n]["size_in"] for n in pf)
    tot_out = sum(pf[n]["size_comp"] for n in pf)
    corpus_bpb = round(8.0 * tot_out / tot_in, 3) if tot_in else None
    # One corpus, one number: the Squishy Score prints ONLY on a complete edition run
    # (every scored size-point — core AND the large rungs).
    if not complete:
        done, total = res["n_done"], res["n_scored"]
        print(f"\n  partial run ({done}/{total} files) — per-file ratios for your "
              f"own regression use; NOT a Squishy Score.")
        for n in pf:
            print(f"    {n:<34} {pf[n]['ratio']:5.2f}×")
    else:
        print(f"\n  Squishy Score: {score:.2f}×   [{res['n_scored']} files, core+scale]  "
              f"(plain geomean of per-file ratios — one vote per file)")
        print(f"  corpus bpb (byte-weighted, total out÷in): {corpus_bpb:.3f}  "
              f"[{tot_in/1e9:.1f}→{tot_out/1e9:.1f} GB]")
        print("  ── category ───────────────")
        for c, v in cat_scores.items():
            print(f"    {c:<16} {v:5.2f}×")
    if args.verify:
        print("  round-trip: ✓ lossless on all files")
    print(f"\n  cached at {state_path}  (re-run is instant + identical)")

    if args.json:
        print(json.dumps({"edition": EDITION, "cmd": args.cmd, "codec_version": ver,
                          "tool_provenance": sq.tool_provenance(args.cmd),
                          "host_provenance": sq.host_provenance(),
                          "complete": complete,
                          "squishy_score": (round(score, 2) if complete else None),
                          "corpus_bpb": corpus_bpb,
                          "total_in_bytes": tot_in, "total_out_bytes": tot_out,
                          "categories": cat_scores, "kinds": res["kinds"],
                          "per_file": pf, "round_trip_verified": bool(args.verify)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
