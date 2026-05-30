#!/usr/bin/env python3
"""Scale-tier acquisition: NYC-TLC Yellow-Taxi FULL-YEAR 2024 as one uncompressed,
multi-row-group Parquet (~GB-scale, same source as the core `parquet`). Streams
each month into a single ParquetWriter (one row group per month → exercises
row-group / page boundaries), measures intrinsic byte properties, uploads to
s3://squishy-corpus/draft/scale/columnar/, records provenance + properties, and
deletes the local copy. Memory stays bounded to one month at a time.

  uv run --with pyarrow python scripts/scale-acquire-parquet.py
"""
from __future__ import annotations
import csv, hashlib, importlib.util, json, os, subprocess, tempfile, urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = "squishy-corpus"
NAME = "nyc-taxi-2024-fullyear.parquet"
KEY = f"draft/scale/columnar/{NAME}"
BASE = "https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-{:02d}.parquet"
OUT = REPO / "build" / "raw" / "scale" / "columnar"


def main() -> int:
    import pyarrow as pa, pyarrow.parquet as pq
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / NAME
    writer = None
    rows = 0
    with tempfile.TemporaryDirectory() as td:
        for mth in range(1, 13):
            url = BASE.format(mth)
            mp = os.path.join(td, f"m{mth}.parquet")
            print(f"  ↓ 2024-{mth:02d} ...", flush=True)
            try:
                urllib.request.urlretrieve(url, mp)
            except Exception as e:
                print(f"    skip {mth}: {e}"); continue
            t = pq.read_table(mp)
            if writer is None:
                writer = pq.ParquetWriter(dst, t.schema, compression="NONE")
            else:
                t = t.cast(writer.schema_arrow) if hasattr(writer, "schema_arrow") else t
            writer.write_table(t, row_group_size=len(t))   # one row group per month
            rows += t.num_rows
            os.remove(mp)
    if writer:
        writer.close()
    size = dst.stat().st_size
    print(f"  built {NAME}: {size/1e9:.2f} GB, {rows:,} rows", flush=True)

    # measure intrinsic properties (scale-safe)
    s = importlib.util.spec_from_file_location("fp", REPO / "scripts" / "file-properties.py")
    fp = importlib.util.module_from_spec(s); s.loader.exec_module(fp)
    props = fp.measure(dst); props["category"] = "Scale tier"
    sha = hashlib.sha256(dst.read_bytes() if size < (512 << 20) else b"").hexdigest() if size < (512 << 20) else None
    if sha is None:  # large file: stream the hash
        h = hashlib.sha256()
        with open(dst, "rb") as f:
            for c in iter(lambda: f.read(1 << 22), b""):
                h.update(c)
        sha = h.hexdigest()
    print(f"  props: {props}", flush=True)

    # upload + provenance
    subprocess.run(["aws", "s3", "cp", str(dst), f"s3://{BUCKET}/{KEY}",
                    "--content-type", "application/x-parquet", "--metadata", f"sha256={sha}",
                    "--no-progress"], check=True)
    man = REPO / "build/meta/LICENSE-MANIFEST.csv"
    with man.open("a", newline="") as f:
        csv.writer(f).writerow([NAME, "scale-columnar",
            "https://www.nyc.gov/site/tlc/about/data-and-research.page", sha, size,
            "NYC-TLC-public", "https://www.nyc.gov/site/tlc/about/data-and-research.page",
            f"NYC TLC Yellow Taxi 2024 full year ({rows} rows), uncompressed multi-row-group Parquet"])
    sp = REPO / "build/meta/scale-properties.json"
    data = json.loads(sp.read_text()) if sp.exists() else {"files": {}}
    data["files"][NAME] = {**props, "key": KEY, "sha256": sha}
    sp.write_text(json.dumps(data, indent=2) + "\n")
    dst.unlink()                                  # reclaim local space (streamed)
    print(f"  ✓ uploaded {KEY}, manifest + scale-properties updated, local deleted", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
