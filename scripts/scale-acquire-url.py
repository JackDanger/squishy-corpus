#!/usr/bin/env python3
"""Generic streaming scale-tier acquirer for single-source large files.

Downloads a URL (optionally gunzip-on-the-fly), measures intrinsic byte
properties (scale-safe), uploads to s3://squishy-corpus/draft/scale/<kind>/<name>,
appends a LICENSE-MANIFEST row + scale-properties entry, and deletes the local
copy. Peak local footprint = one file.

  uv run python scripts/scale-acquire-url.py --name big-buck-bunny-1080p.mp4 \
      --slot scale-media --url https://.../bbb_1080p.mp4 \
      --license CC-BY-3.0 --attr "Big Buck Bunny 1080p ..." --content-type video/mp4
"""
from __future__ import annotations
import argparse, csv, gzip, hashlib, importlib.util, json, lzma, shutil, subprocess, tempfile, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = "squishy-corpus"
UA = {"User-Agent": "squishy-corpus/1.0 (+https://github.com/JackDanger/squishy-corpus)"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--slot", required=True)            # e.g. scale-csv / scale-media / scale-genome
    ap.add_argument("--url", action="append", required=True, help="repeatable; concatenated in order")
    ap.add_argument("--license", required=True)
    ap.add_argument("--attr", required=True)
    ap.add_argument("--content-type", default="application/octet-stream")
    ap.add_argument("--decompress", choices=["none", "gzip", "zip", "xz"], default="none")
    a = ap.parse_args()
    kind = a.slot.split("-", 1)[1]
    out_dir = REPO / "build" / "raw" / "scale" / kind
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / a.name

    with dst.open("wb") as out:
        for url in a.url:
            print(f"  ↓ {url}", flush=True)
            req = urllib.request.Request(url, headers=UA)
            if a.decompress == "zip":                    # zip needs a seekable file
                with tempfile.NamedTemporaryFile() as tmp:
                    with urllib.request.urlopen(req, timeout=180) as r:
                        shutil.copyfileobj(r, tmp, length=1 << 23)
                    tmp.flush()
                    with zipfile.ZipFile(tmp.name) as z:
                        for member in z.namelist():
                            with z.open(member) as m:
                                shutil.copyfileobj(m, out, length=1 << 23)
            else:
                with urllib.request.urlopen(req, timeout=180) as r:
                    if a.decompress == "gzip":
                        src = gzip.GzipFile(fileobj=r)
                    elif a.decompress == "xz":
                        src = lzma.LZMAFile(r)
                    else:
                        src = r
                    shutil.copyfileobj(src, out, length=1 << 23)
    size = dst.stat().st_size
    print(f"  built {a.name}: {size/1e9:.2f} GB", flush=True)

    s = importlib.util.spec_from_file_location("fp", REPO / "scripts" / "file-properties.py")
    fp = importlib.util.module_from_spec(s); s.loader.exec_module(fp)
    props = fp.measure(dst); props["category"] = "Scale tier"
    h = hashlib.sha256()
    with dst.open("rb") as f:
        for c in iter(lambda: f.read(1 << 22), b""):
            h.update(c)
    sha = h.hexdigest()
    print(f"  props: {props}", flush=True)

    key = f"draft/scale/{kind}/{a.name}"
    subprocess.run(["aws", "s3", "cp", str(dst), f"s3://{BUCKET}/{key}",
                    "--content-type", a.content_type, "--metadata", f"sha256={sha}",
                    "--no-progress"], check=True)
    src_urls = " ; ".join(a.url)
    with (REPO / "build/meta/LICENSE-MANIFEST.csv").open("a", newline="") as f:
        csv.writer(f).writerow([a.name, a.slot, src_urls, sha, size, a.license,
                                a.url[0], a.attr])
    sp = REPO / "build/meta/scale-properties.json"
    data = json.loads(sp.read_text()) if sp.exists() else {"files": {}}
    data["files"][a.name] = {**props, "key": key, "sha256": sha}
    sp.write_text(json.dumps(data, indent=2) + "\n")
    dst.unlink()
    print(f"  ✓ uploaded {key}, manifest + scale-properties updated, local deleted", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
