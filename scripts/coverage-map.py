#!/usr/bin/env python3
"""Render the static coverage map: build/meta/coverage-map.svg.

A first-time reader should *see* the corpus span the space of how data
compresses, not just read that it does. This is the flat, citable companion to
the live 3D explorer (scripts/assets/squishy-cube.js): one dot per file,
positioned by two intrinsic, codec-free byte properties —

  x = entropy        (bits/byte, 0..8   — how random the bytes look)
  y = repeat coverage(0..1            — how much of the file exactly recurs)

coloured by category (Okabe–Ito, colour-blind-safe), with dot AREA ∝ log file
size so the size axis (40 MB core → 4 GB scale rungs) reads honestly. The third
shape axis, match distance, is what the live map adds in 3D.

Zero dependencies, deterministic output (stable point order, no timestamps): the
SVG re-derives bit-for-bit from the same property JSON, exactly like every other
build artifact.

  uv run python scripts/coverage-map.py
"""
from __future__ import annotations
import json
import math
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "build/meta/coverage-map.svg"

# Okabe–Ito by category — identical to the live cube (scripts/build-provenance.py).
CAT_COLOR = {
    "Prose": "#E69F00",
    "Code & Web": "#56B4E9",
    "Structured": "#009E73",
    "Tabular / DB": "#0072B2",
    "Binary & Media": "#CC79A7",
}
# Scale-tier files record their kind in the S3 key (draft/scale/<kind>/…); map
# each kind back to its category so a large rung shares its small sibling's colour.
KIND_CATEGORY = {
    "csv": "Tabular / DB", "columnar": "Tabular / DB",
    "monorepo": "Code & Web", "archive": "Code & Web",
    "log": "Structured", "genome": "Structured",
    "text": "Prose",
    "media": "Binary & Media",
}
# Friendly, distinct tooltip labels for the large rungs (the raw filenames collide:
# two NOAA CSVs would both shorten to "noaa"). Falls back to a derived short name.
SCALE_LABEL = {
    "noaa-ghcn-daily-2024-full.csv": "csv 1yr",
    "noaa-ghcn-daily-2021-2023.csv": "csv 3yr",
    "big-buck-bunny-1080p.mov": "movie 1080p",
    "ecoli-DRR002013-full.fastq": "genome 1GB",
    "enwik9.txt": "enwik9",
    "llvm-project-19.1.0.src.tar": "monorepo 1.8GB",
    "nasa-http-jul-aug-1995.log": "log 0.4GB",
    "clang-releases-16-17-18-19.tar": "archive 1.5GB",
    "bts-ontime-2022-2024.parquet": "parquet 0.8GB",
}

W, H = 960, 600
ML, MR, MT, MB = 72, 210, 78, 74          # margins (right margin holds the legend)
X0, X1 = ML, W - MR                         # plot box, entropy axis
Y0, Y1 = MT, H - MB                         # plot box, coverage axis (Y1 = bottom)
R_MIN, R_MAX = 4.5, 16.0
INK = "#1c2530"
MUTED = "#6b7682"
GRID = "#e3e7ec"


def load_points() -> list[dict]:
    pts: list[dict] = []
    core = json.loads((REPO / "build/meta/file-properties.json").read_text())["files"]
    for name, m in core.items():
        pts.append({"name": name, "tier": "core", "cat": m["category"], **m})
    sp = REPO / "build/meta/scale-properties.json"
    if sp.exists():
        for fname, m in json.loads(sp.read_text())["files"].items():
            kind = m.get("key", "//").split("/")[2] if "key" in m else ""
            cat = KIND_CATEGORY.get(kind, "Binary & Media")
            label = SCALE_LABEL.get(fname, fname.split("-")[0].split(".")[0])
            pts.append({"name": label, "tier": "scale", "cat": cat, **m})
    # Deterministic order: by entropy, then name (stable, no run-to-run drift).
    return sorted(pts, key=lambda p: (p["entropy"], p["name"]))


def sx(entropy: float) -> float:
    return X0 + (entropy / 8.0) * (X1 - X0)


def sy(coverage: float) -> float:
    return Y1 - max(0.0, min(1.0, coverage)) * (Y1 - Y0)


def radius(size: int, lo: float, hi: float) -> float:
    # area ∝ log(size)  →  r ∝ sqrt(log-normalised size)
    t = (math.log10(size) - lo) / (hi - lo) if hi > lo else 0.0
    return R_MIN + (R_MAX - R_MIN) * math.sqrt(max(0.0, min(1.0, t)))


def esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def main() -> int:
    pts = load_points()
    logs = [math.log10(p["size"]) for p in pts]
    lo, hi = min(logs), max(logs)
    out: list[str] = []
    a = out.append

    a(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" '
      f'font-family="-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif" '
      f'role="img" aria-label="Squishy coverage map: corpus files spread across '
      f'byte entropy and repeat coverage">')
    # Document-level <title> as the accessible name (more reliably honoured than
    # aria-label on an SVG); the per-circle <title>s below are hover tooltips.
    a('<title>Squishy coverage map: corpus files spread across byte entropy and '
      'repeat coverage</title>')
    a(f'<rect width="{W}" height="{H}" fill="#fafafa"/>')

    # Title + subtitle.
    a(f'<text x="{ML}" y="34" font-size="22" font-weight="700" fill="{INK}">'
      f'The coverage map</text>')
    a(f'<text x="{ML}" y="56" font-size="13" fill="{MUTED}">Every Squishy file, '
      f'placed by how its bytes behave — orderly/compressible (left) to random '
      f'(right), highly repetitive (top) to not (bottom).</text>')

    # Plot frame.
    a(f'<rect x="{X0}" y="{Y0}" width="{X1 - X0}" height="{Y1 - Y0}" '
      f'fill="#ffffff" stroke="{GRID}"/>')

    # X gridlines / ticks: entropy 0..8.
    for h in range(0, 9):
        x = sx(h)
        a(f'<line x1="{x:.1f}" y1="{Y0}" x2="{x:.1f}" y2="{Y1}" stroke="{GRID}"/>')
        a(f'<text x="{x:.1f}" y="{Y1 + 18}" font-size="11" fill="{MUTED}" '
          f'text-anchor="middle">{h}</text>')
    # Y gridlines / ticks: coverage 0..1.
    for i in range(0, 6):
        c = i / 5.0
        y = sy(c)
        a(f'<line x1="{X0}" y1="{y:.1f}" x2="{X1}" y2="{y:.1f}" stroke="{GRID}"/>')
        a(f'<text x="{X0 - 10}" y="{y + 4:.1f}" font-size="11" fill="{MUTED}" '
          f'text-anchor="end">{c:.1f}</text>')

    # Axis labels.
    a(f'<text x="{(X0 + X1) / 2:.0f}" y="{Y1 + 42}" font-size="13" '
      f'font-weight="600" fill="{INK}" text-anchor="middle">how random  →  '
      f'entropy (bits / byte)</text>')
    a(f'<text x="22" y="{(Y0 + Y1) / 2:.0f}" font-size="13" font-weight="600" '
      f'fill="{INK}" text-anchor="middle" transform="rotate(-90 22 '
      f'{(Y0 + Y1) / 2:.0f})">how repetitive  →  repeat coverage</text>')

    # Points (small files first so the big scale rungs read on top).
    for p in sorted(pts, key=lambda q: q["size"]):
        x, y = sx(p["entropy"]), sy(p["coverage"])
        r = radius(p["size"], lo, hi)
        fill = CAT_COLOR.get(p["cat"], "#9aa0a6")
        stroke = "#0d1418" if p["tier"] == "scale" else "#ffffff"
        sw = 1.6 if p["tier"] == "scale" else 1.2
        title = f'{esc(p["name"])} — {p["size"] / 1e6:.0f} MB, H={p["entropy"]:.2f}, cover={p["coverage"]:.2f}'
        a(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{fill}" '
          f'fill-opacity="0.82" stroke="{stroke}" stroke-width="{sw}">'
          f'<title>{title}</title></circle>')

    # Category legend.
    lx, ly = X1 + 28, Y0 + 8
    a(f'<text x="{lx}" y="{ly}" font-size="12" font-weight="700" fill="{INK}">'
      f'category</text>')
    for i, (cat, col) in enumerate(CAT_COLOR.items()):
        yy = ly + 22 + i * 24
        a(f'<circle cx="{lx + 7}" cy="{yy - 4:.0f}" r="7" fill="{col}" '
          f'fill-opacity="0.82" stroke="#fff" stroke-width="1.2"/>')
        a(f'<text x="{lx + 22}" y="{yy}" font-size="12" fill="{INK}">{esc(cat)}</text>')

    # Size legend (dot area ∝ log size).
    sy0 = ly + 22 + len(CAT_COLOR) * 24 + 22
    a(f'<text x="{lx}" y="{sy0}" font-size="12" font-weight="700" fill="{INK}">'
      f'dot area ∝ log file size</text>')
    for i, (lbl, rr) in enumerate([("~4 MB", R_MIN), ("~50 MB", 9.5), ("~4 GB", R_MAX)]):
        yy = sy0 + 26 + i * 30
        a(f'<circle cx="{lx + 12}" cy="{yy - 4:.0f}" r="{rr}" fill="{MUTED}" '
          f'fill-opacity="0.35" stroke="{MUTED}" stroke-width="1"/>')
        a(f'<text x="{lx + 34}" y="{yy}" font-size="11" fill="{MUTED}">{lbl}</text>')
    a(f'<text x="{lx}" y="{sy0 + 26 + 3 * 30 + 8}" font-size="10.5" fill="{MUTED}">'
      f'ringed = large scale rung.</text>')

    # Footnote.
    a(f'<text x="{ML}" y="{H - 16}" font-size="10.5" fill="{MUTED}">'
      f'Intrinsic, codec-free properties (measured from bytes alone). A third axis '
      f'— repeat distance — and live rotation are at squishy.jackdanger.com.</text>')

    a('</svg>')
    OUT.write_text("\n".join(out) + "\n")
    print(f"wrote {OUT.relative_to(REPO)} ({len(pts)} files, "
          f"{sum(p['tier'] == 'core' for p in pts)} core + "
          f"{sum(p['tier'] == 'scale' for p in pts)} scale)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
