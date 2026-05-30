#!/usr/bin/env python3
"""Build an uncompressed, multi-row-group Parquet from US DOT BTS "Reporting
Carrier On-Time Performance" monthly data — a real, large analytics table that is
unambiguously **public domain** (a U.S. Government work, 17 U.S.C. §105). This is
the PD replacement for the NYC-TLC taxi parquet (NYC-TLC is a license, not PD, and
carried an EU database-right flag).

One row group per month → exercises row-group / data-page boundaries. The same
builder produces both the small scored core member (one month) and the large
columnar rung (many months), so the `parquet` kind is uniformly BTS / PD and
kind-continuous across sizes.

  # large columnar rung (multi-year):
  uv run --with pyarrow python scripts/scale-acquire-bts-parquet.py \
      --name bts-ontime-2021-2024.parquet --slot scale-columnar \
      --months 2021-1..2024-12

  # core member (one month), local build only (no upload), for the scored set:
  uv run --with pyarrow python scripts/scale-acquire-bts-parquet.py \
      --name data.parquet --core --months 2024-1
"""
from __future__ import annotations
import argparse, csv, hashlib, importlib.util, io, json, os, subprocess, tempfile, urllib.request, zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BUCKET = "squishy-corpus"
UA = {"User-Agent": "squishy-corpus/1.0 (+https://github.com/JackDanger/squishy-corpus)"}
# BTS prezipped monthly On-Time Reporting Carrier On-Time Performance (US DOT, public domain)
PREZIP = ("https://transtats.bts.gov/PREZIP/"
          "On_Time_Reporting_Carrier_On_Time_Performance_1987_present_{y}_{m}.zip")
SOURCE = "https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoyr_VQ=FGJ&QO_fu146_anzr=b0-gvzr"


def parse_months(spec: str) -> list[tuple[int, int]]:
    """'2021-1..2024-12' or '2024-1,2024-2' -> [(y,m), ...]"""
    out: list[tuple[int, int]] = []
    for part in spec.split(","):
        part = part.strip()
        if ".." in part:
            a, b = part.split("..")
            ay, am = map(int, a.split("-")); by, bm = map(int, b.split("-"))
            y, m = ay, am
            while (y, m) <= (by, bm):
                out.append((y, m))
                m += 1
                if m > 12:
                    m = 1; y += 1
        else:
            y, m = map(int, part.split("-")); out.append((y, m))
    return out


def build(dst: Path, months: list[tuple[int, int]]) -> int:
    import pyarrow as pa, pyarrow.csv as pacsv, pyarrow.parquet as pq
    dst.parent.mkdir(parents=True, exist_ok=True)
    writer = None
    keep: list[str] | None = None
    rows = 0
    for (y, m) in months:
        url = PREZIP.format(y=y, m=m)
        print(f"  ↓ {y}-{m:02d} ...", flush=True)
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=300) as r:
                blob = r.read()
        except Exception as e:
            print(f"    skip {y}-{m}: {e}"); continue
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
            data = z.read(csv_name)
        # Read EVERY column as string so the schema is identical across all months and
        # years (BTS inferred types drift on blanks across the decade). Still a real,
        # columnar Parquet (per-column dictionary/RLE), structurally distinct from CSV.
        ropts = pacsv.ReadOptions(block_size=1 << 26)
        copts = pacsv.ConvertOptions(strings_can_be_null=True)
        t = pacsv.read_csv(io.BytesIO(data), read_options=ropts,
                           convert_options=copts)
        t = t.cast(pa.schema([(f.name, pa.string()) for f in t.schema]))
        drop = [c for c in t.column_names if c == "" or c.startswith("Unnamed")]
        if drop:
            t = t.drop(drop)
        if writer is None:
            keep = t.column_names
            writer = pq.ParquetWriter(dst, pa.schema([(c, pa.string()) for c in keep]),
                                      compression="NONE")
        else:                                   # conform to the fixed keep columns
            cols = {c: t[c] for c in t.column_names}
            arrays = [cols.get(c, pa.nulls(len(t), pa.string())) for c in keep]
            t = pa.table(arrays, names=keep)
        writer.write_table(t, row_group_size=len(t))   # one row group per month
        rows += len(t)
    if writer:
        writer.close()
    print(f"  built {dst.name}: {dst.stat().st_size/1e9:.2f} GB, {rows:,} rows", flush=True)
    return rows


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for c in iter(lambda: f.read(1 << 22), b""):
            h.update(c)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", required=True)
    ap.add_argument("--slot", default="scale-columnar")
    ap.add_argument("--months", required=True, help="'2021-1..2024-12' or '2024-1,2024-2'")
    ap.add_argument("--core", action="store_true", help="build into build/raw/corpus (no upload); caller wires CHECKSUMS")
    a = ap.parse_args()
    months = parse_months(a.months)

    if a.core:
        dst = REPO / "build" / "raw" / "corpus" / a.name
        rows = build(dst, months)
        print(f"  core build complete: {dst} ({sha256_of(dst)})  rows={rows}", flush=True)
        return 0

    kind = a.slot.split("-", 1)[1]
    dst = REPO / "build" / "raw" / "scale" / kind / a.name
    rows = build(dst, months)
    size = dst.stat().st_size
    s = importlib.util.spec_from_file_location("fp", REPO / "scripts" / "file-properties.py")
    fp = importlib.util.module_from_spec(s); s.loader.exec_module(fp)
    props = fp.measure(dst); props["category"] = "Scale tier"
    sha = sha256_of(dst)
    print(f"  props: {props}", flush=True)

    key = f"draft/scale/{kind}/{a.name}"
    span = f"{months[0][0]}-{months[0][1]:02d}..{months[-1][0]}-{months[-1][1]:02d}"
    attr = (f"US DOT BTS Reporting Carrier On-Time Performance {span} ({rows} rows), "
            f"uncompressed multi-row-group Parquet (one row group/month); U.S. Government public domain")
    subprocess.run(["aws", "s3", "cp", str(dst), f"s3://{BUCKET}/{key}",
                    "--content-type", "application/x-parquet", "--metadata", f"sha256={sha}",
                    "--no-progress"], check=True)
    with (REPO / "build/meta/LICENSE-MANIFEST.csv").open("a", newline="") as f:
        csv.writer(f).writerow([a.name, a.slot, SOURCE, sha, size,
                                "Public-Domain-USGov", SOURCE, attr])
    sp = REPO / "build/meta/scale-properties.json"
    data = json.loads(sp.read_text()) if sp.exists() else {"files": {}}
    data["files"][a.name] = {**props, "key": key, "sha256": sha}
    sp.write_text(json.dumps(data, indent=2) + "\n")
    dst.unlink()
    print(f"  ✓ uploaded {key}, manifest + scale-properties updated, local deleted", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
