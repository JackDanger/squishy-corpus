#!/usr/bin/env python3
"""Build the Squishy data-explorer: build/site/provenance/index.html plus rendered
preview assets (image thumbnail, video poster frame). Shows each dataset clearly
— a preview of every file, what it is, and how every tool+version compresses it,
in a layout that scales to many versions.

  uv run --with pyarrow --with pandas python scripts/build-provenance.py
"""
from __future__ import annotations
import csv, html, importlib.util, json, re, sqlite3, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "build" / "site"          # this is now the PRIMARY site page (index.html)
ASSETS = REPO / "scripts" / "assets"   # maintainable JS/CSS sources copied into the build

WHATIS = {
 "dickens": "Nine novels by Charles Dickens — English prose.",
 "aozora": "Collected works of Natsume Sōseki — Japanese literary prose.",
 "monorepo": "The <code>lib/</code> source tree of the LLVM Clang C++ compiler.",
 "minjs": "The minified Plotly.js charting library — one big line of JavaScript.",
 "markup": "Shakespeare's plays, marked up in XML.",
 "json": "20,000 magnitude-4.5+ earthquakes, 2010–2024 (USGS GeoJSON).",
 "log": "A NASA web server's access log from July 1995.",
 "genome": "Sequencing reads from an E. coli genome (FASTQ).",
 "csv": "Daily weather observations from NOAA's global climate network, 2024 (CSV).",
 "parquet": "U.S. airline on-time flight records (Bureau of Transportation Statistics) — stored column-wise as Apache Parquet.",
 "sqlite": "USDA's nutrition database — foods, nutrients, and portions across 17 related tables (SR Legacy).",
 "exe": "A compiled Linux executable — the Hugo static-site generator.",
 "photo": "NASA's “Blue Marble” — Earth photographed from Apollo 17.",
 "movie": "A clip from the open film Big Buck Bunny (H.264 video).",
 "weights": "The trained weights of a small neural network (safetensors).",
}


def sh(args, **kw):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=120, **kw).stdout
    except Exception:
        return ""


def render_assets(core):
    OUT.mkdir(parents=True, exist_ok=True)
    photo = REPO / "build/raw/corpus/photo.jpg"
    if photo.exists():
        if not sh(["sips", "-Z", "760", str(photo), "--out", str(OUT / "photo.jpg")]):
            subprocess.run(["ffmpeg", "-y", "-i", str(photo), "-vf", "scale=760:-1", str(OUT / "photo.jpg")],
                           capture_output=True)
    movie = REPO / "build/raw/corpus/movie.mp4"
    if movie.exists():
        subprocess.run(["ffmpeg", "-y", "-ss", "8", "-i", str(movie), "-frames:v", "1",
                        "-vf", "scale=760:-1", str(OUT / "movie.jpg")], capture_output=True)


def esc(s): return html.escape(str(s))


def table(rows, head=None):
    h = "<tr>" + "".join(f"<th>{esc(c)}</th>" for c in head) + "</tr>" if head else ""
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="data"><thead>{h}</thead><tbody>{body}</tbody></table>'


def txt_excerpt(p, n=14, w=150):
    lines = [l for l in p.read_text("utf-8", "replace").splitlines() if l.strip()][:n]
    return f'<pre class="txt">{esc(chr(10).join(l[:w] for l in lines))}</pre>'


def tar_excerpt(p, member_suffix, head=12):
    names = [x for x in sh(["tar", "-tf", str(p)]).split() if not x.endswith("/")]
    listing = "\n".join(names[:head]) + (f"\n… {len(names)} files total" if len(names) > head else "")
    pick = next((x for x in names if x.endswith(member_suffix)), names[0] if names else None)
    body = ""
    if pick:
        content = sh(["tar", "-xOf", str(p), pick])
        snip = "\n".join([l for l in content.splitlines()][:16])
        body = f'<div class="cap">— {esc(pick)} —</div><pre class="txt">{esc(snip[:1100])}</pre>'
    return f'<pre class="txt">{esc(listing)}</pre>{body}'


def csv_table(p, rows=8):
    rd = csv.reader(p.open("r", encoding="utf-8", errors="replace"))
    data = [next(rd) for _ in range(rows + 1)]
    return table(data[1:], head=data[0])


def parquet_table(p, rows=6):
    import pyarrow.parquet as pq
    t = pq.read_table(p)
    cols = t.column_names
    schema = ", ".join(f"{c}:{t.schema.field(c).type}" for c in cols[:8])
    d = t.slice(0, rows).to_pylist()
    body = table([[str(r.get(c, ""))[:18] for c in cols[:7]] for r in d], head=cols[:7])
    return f'<div class="cap">{t.num_rows:,} rows × {len(cols)} columns · schema: {esc(schema)} …</div>{body}'


def sqlite_table(p, rows=6):
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    tbls = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    t = "food" if "food" in tbls else tbls[0]          # show a meaningful table, not the first
    cols = [c[1] for c in con.execute(f"PRAGMA table_info('{t}')")]
    d = con.execute(f"SELECT * FROM '{t}' LIMIT {rows}").fetchall()
    con.close()
    return (f'<div class="cap">{len(tbls)} tables · showing <code>{esc(t)}</code> ({len(cols)} columns)</div>'
            + table([[str(x)[:18] for x in r[:7]] for r in d], head=cols[:7]))


def hexdump(p, n=160):
    return f'<pre class="hex">{esc(sh(["xxd", "-l", str(n), str(p)]))}</pre>'


CUBE_COLORS = {
    "Prose": "#E69F00", "Code & Web": "#56B4E9", "Structured": "#009E73",
    "Tabular / DB": "#0072B2", "Binary & Media": "#CC79A7", "Scale tier": "#9aa0a6",
}


def cube_data(sq, props, scale=None) -> dict:
    """Build the 3D scatter from INTRINSIC byte properties (file-properties.json
    for the core, scale-properties.json for the scale tier): x=entropy (bits/byte),
    y=repeat coverage, z=match distance (bytes, log). Size is the dot radius. No
    compressor is referenced — the axes are properties of the bytes themselves."""
    import math
    K = props["block_bytes"]
    entries = dict(props["files"])                      # core (measured)
    if scale:
        for nm, m in scale.get("files", {}).items():    # scale tier (measured, real)
            entries[nm.replace(".parquet", "").replace(".safetensors", "")] = m
    sizes = [m["size"] for m in entries.values()]
    dists = [max(m["match_distance"], K) for m in entries.values()]
    lo, hi = math.log10(min(sizes)), math.log10(max(sizes))
    pts = []
    for d, m in entries.items():
        pts.append({
            "name": d, "cat": m.get("category", "Scale tier"),
            "x": m["entropy"], "y": m["coverage"], "z": max(m["match_distance"], K),
            "r": round((math.log10(m["size"]) - lo) / (hi - lo), 3),
            "sizeMB": m["size"] / 1e6, "entropy": m["entropy"],
            "coverage": m["coverage"], "dist": m["match_distance"],
            "scored": bool(sq.is_scored(m)),
            "K": round(sq.compressibility(m["entropy"], m["coverage"]), 3),
        })
    # The compressibility plane that gates the Squishy Score: K = coverage + (8−entropy)/8
    # ≥ COMPRESSIBILITY_MIN. K ignores match-distance, so the boundary is a flat vertical
    # plane; in the (x=entropy, y=coverage) face it is the line y = entropy/8 − (1 − K_min),
    # extended across all z. Points below it (high entropy, ~no repetition) are the
    # entropy-coded media that are measured but NOT scored.
    kmin = sq.COMPRESSIBILITY_MIN
    return {
        "axes": {"x": {"label": "entropy (bits/byte)", "min": 0, "max": 8, "log": False},
                 "y": {"label": "repeat coverage", "min": 0, "max": 1, "log": False},
                 "z": {"label": "match distance (bytes)", "min": K, "max": max(dists), "log": True}},
        "categories": CUBE_COLORS,
        "plane": {"label": f"compressibility K = {kmin} (scored ↔ diagnostic)",
                  "kmin": kmin, "slope_x": 1.0 / 8.0, "intercept": -(1.0 - kmin)},
        "points": pts}


def coverage_table(cube: dict) -> str:
    """Accessible, non-3D fallback for the explorer: the same points as a sortable
    table. Mirrors the tooltip fields so screen-reader / no-WebGL users get everything."""
    head = ["category", "file", "entropy (bpb)", "coverage", "match distance",
            "size", "K", "status"]
    rows = []
    for p in sorted(cube["points"], key=lambda p: (p["cat"], -p["sizeMB"])):
        dist = p["dist"]
        ds = (f"{dist/1e6:.1f} MB" if dist >= 1e6 else f"{dist/1e3:.0f} KB"
              if dist >= 1e3 else f"{dist} B")
        sz = (f"{p['sizeMB']/1000:.1f} GB" if p["sizeMB"] >= 1000 else f"{p['sizeMB']:.1f} MB")
        rows.append([p["cat"], p["name"], f"{p['entropy']:.2f}",
                     f"{p['coverage']*100:.0f}%", ds, sz,
                     f"{p['K']:.2f}" if p.get("K") is not None else "—",
                     "scored" if p.get("scored") else "diagnostic"])
    h = "<tr>" + "".join(f"<th>{esc(c)}</th>" for c in head) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="coverage"><thead>{h}</thead><tbody>{body}</tbody></table>'


def scale_what(name: str) -> str:
    n = name.lower()
    if "135m" in n:
        return ("SmolLM2-135M — a small (135M-parameter) language model's weights "
                "(Apache-2.0). The middle rung of the weights size-ladder.")
    if "0.5b" in n or "0p5b" in n:
        return ("Qwen2.5-0.5B — a 0.5B-parameter language model's weights "
                "(Apache-2.0). The second rung of the weights size-ladder.")
    if "1.5b" in n or "1p5b" in n:
        return ("Qwen2.5-1.5B — a larger (1.5B-parameter) language model's weights "
                "(Apache-2.0). The top rung of the ladder; multi-GB, for large-window "
                "and throughput work.")
    return "Scale-tier file — for throughput / large-window testing (not scored)."


def preview_safetensors(p) -> str:
    """Render a safetensors tensor table from the HEADER ONLY (reads a few KB, not
    the multi-GB body)."""
    try:
        import struct
        with open(p, "rb") as f:
            hlen = struct.unpack("<Q", f.read(8))[0]
            hdr = json.loads(f.read(hlen))
        keys = [k for k in hdr if k != "__metadata__"]
        rows = [(k, hdr[k].get("dtype", ""), "×".join(map(str, hdr[k].get("shape", [])))) for k in keys[:8]]
        return (f'<div class="prev"><div class="cap">{len(keys)} tensors</div>'
                + table(rows, head=["tensor", "dtype", "shape"]) + "</div>")
    except Exception:
        return ""


def weights_table(p):
    import struct
    b = p.read_bytes()
    hlen = struct.unpack("<Q", b[:8])[0]
    hdr = json.loads(b[8:8+hlen])
    rows = [(k, v.get("dtype", ""), "×".join(map(str, v.get("shape", []))))
            for k, v in list(hdr.items()) if k != "__metadata__"][:8]
    return (f'<div class="cap">{len([k for k in hdr if k!="__metadata__"])} tensors</div>'
            + table(rows, head=["tensor", "dtype", "shape"]) + hexdump(p, 96))


def quake_map(p, cap=2500):
    pts = []
    for line in p.read_text("utf-8", "replace").splitlines():
        m = re.search(r'"coordinates":\[(-?\d+\.?\d*),(-?\d+\.?\d*)', line)
        if m:
            lng, lat = float(m.group(1)), float(m.group(2))
            x = (lng + 180) / 360 * 720; y = (90 - lat) / 180 * 360
            pts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="1.1"/>')
            if len(pts) >= cap: break
    return (f'<svg viewBox="0 0 720 360" class="map" role="img" aria-label="world map of earthquakes">'
            f'<rect width="720" height="360" fill="#eef3f7"/>{"".join(pts)}</svg>'
            f'<div class="cap">{len(pts):,} epicenters plotted (lng/lat)</div>')


def preview(display, st, name):
    p = REPO / "build" / "raw" / st / name
    if not p.exists():
        return "<p>(file not present locally)</p>"
    try:
        if display == "photo":
            return '<img class="shot" src="photo.jpg" alt="NASA Blue Marble">'
        if display == "movie":
            return '<img class="shot" src="movie.jpg" alt="Big Buck Bunny frame"><div class="cap">frame from the clip</div>'
        if display == "csv":     return csv_table(p)
        if display == "parquet": return parquet_table(p)
        if display == "sqlite":  return sqlite_table(p)
        if display == "weights": return weights_table(p)
        if display == "exe":     return hexdump(p)
        if display == "json":    return quake_map(p) + txt_excerpt(p, 2, 200)
        if display == "monorepo": return tar_excerpt(p, ".cpp")
        if display == "markup":   return tar_excerpt(p, ".xml")
        return txt_excerpt(p)
    except Exception as e:
        return f"<pre class='hex'>(preview error: {esc(e)})</pre>"


def tool_label(codec, ver, cmd):
    # label = the clean panel key (e.g. "zstd -19"); flag = parsed version (e.g. "v1.5.7")
    vm = re.search(r"(\d+\.\d+(?:\.\d+)?)", ver or "")
    return codec, (f"v{vm.group(1)}" if vm else "")


def main() -> int:
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    man = {r["core_slot"]: r for r in csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open())}
    sj = json.loads((REPO / "build/meta/squishy-scores.json").read_text()) if (REPO / "build/meta/squishy-scores.json").exists() else {"panel": {}}

    # tools: scalable list of (label, flag, squishy, bpb, categories, per_file)
    tools = []
    for codec, r in sj.get("panel", {}).items():
        nm, flag = tool_label(codec, r.get("codec_version", ""), r.get("codec_command", ""))
        tools.append({"label": nm, "flag": flag, "sq": r.get("squishy_score"), "bpb": r.get("corpus_bpb"),
                      "cats": r.get("categories", {}), "pf": r.get("per_file", {})})
    cats = list(sq.CORE.keys())

    core = [(d, st, n) for files in sq.CORE.values() for (d, st, n) in files]
    render_assets(core)

    # leaderboard (sortable, scales to many rows)
    lb = ""
    for t in sorted(tools, key=lambda x: -(x["sq"] or 0)):
        cells = "".join(f"<td>{t['cats'].get(c, float('nan')):.2f}×</td>" if c in t["cats"] else "<td>—</td>" for c in cats)
        bpb_cell = f"{t['bpb']:.3f}" if t["bpb"] else "—"
        lb += (f"<tr><td class='tool'>{esc(t['label'])} <span class='flag'>{esc(t['flag'])}</span></td>"
               f"<td class='big'>{t['sq']:.2f}×</td><td>{bpb_cell}</td>{cells}</tr>")
    lbhead = "<th>tool</th><th>Squishy Score (×)</th><th>corpus bpb</th>" + "".join(f"<th>{esc(c)}</th>" for c in cats)

    # dataset cards
    cards = ""; n = 0
    maxr = {}  # per-file max ratio for bar scaling
    for d, st, nm in core:
        maxr[d] = max((t["pf"].get(d, 0) for t in tools), default=1) or 1
    for cat, files in sq.CORE.items():
        cards += f'<h2>{esc(cat)}</h2>'
        for d, st, nm in files:
            n += 1; m = man.get(d, {})
            sz = (REPO / "build/raw" / st / nm).stat().st_size if (REPO / "build/raw" / st / nm).exists() else 0
            # compression bars per tool@version (sorted, scalable)
            bars = ""
            for t in sorted(tools, key=lambda x: -(x["pf"].get(d, 0))):
                r = t["pf"].get(d)
                if r is None: continue
                w = 100 * r / maxr[d]
                bars += (f"<div class='bar'><span class='bl'>{esc(t['label'])} {esc(t['flag'])}</span>"
                         f"<span class='bt'><span class='bf' style='width:{w:.0f}%'></span></span>"
                         f"<span class='bv'>{r:.2f}×</span></div>")
            cards += f'''<section class="card">
  <div class="dh"><span class="num">{n}</span><h3><code>{d}</code></h3><span class="sz">{sz/1e6:.1f} MB · {esc(m.get('license','?'))}</span></div>
  <p class="what">{WHATIS.get(d,'')}</p>
  <div class="prev">{preview(d, st, nm)}</div>
  <div class="src"><a href="{esc(m.get('source_url',''))}">source ↗</a></div>
  <details class="cmp"><summary>compression — {len(tools)} tools</summary>{bars}</details>
</section>'''
    # ── The scale tier: large members of the one corpus ────────────────────
    core_slots = {d for files in sq.CORE.values() for (d, _s, _n) in files}
    rows = list(csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open()))
    extra = [r for r in rows if r["core_slot"] not in core_slots]
    if extra:
        cards += ('<h2>Scale tier — the large members</h2>'
                  '<p class="what">Large files spanning the kinds and the size axis (~0.3–3 GB). '
                  'The GB rungs of compressible kinds (csv, columnar, genome, text) are scored members '
                  'of the corpus; the model-weights ladder (135M → 0.5B → 1.5B params) and large media '
                  'are near-incompressible <strong>throughput / behavior diagnostics</strong>, not scored. '
                  '<em>This tier is still being assembled — see the readiness plan.</em></p>')
        for r in sorted(extra, key=lambda r: int(r["size_bytes"])):
            nm = r["name"]
            local = next((p for p in REPO.glob(f"build/raw/*/{nm}") if p.exists()), None)
            prev = preview_safetensors(local) if (local and nm.endswith(".safetensors")) else ""
            what = scale_what(nm)
            cards += f'''<section class="card">
  <div class="dh"><span class="num">scale</span><h3><code>{esc(nm)}</code></h3>
    <span class="sz">{int(r['size_bytes'])/1e6:.0f} MB · {esc(r['license'])}</span></div>
  <p class="what">{what}</p>
  {prev}
  <div class="src">sha256 <code>{esc(r['sha256'][:16])}…</code> · <a href="{esc(r['source_url'])}">source ↗</a></div>
</section>'''

    # 3D cube: built from INTRINSIC byte properties (file-properties.json), inlined
    # + shipped as a data file; the maintainable renderer source is copied in.
    props = json.loads((REPO / "build/meta/file-properties.json").read_text())
    sp = REPO / "build/meta/scale-properties.json"
    scale = json.loads(sp.read_text()) if sp.exists() else None
    cube = cube_data(sq, props, scale)
    (OUT / "cube-data.json").write_text(json.dumps(cube, indent=2))
    import shutil
    shutil.copyfile(ASSETS / "squishy-cube.js", OUT / "squishy-cube.js")

    page = TEMPLATE.format(lbhead=lbhead, lb=lb, cards=cards, n=n, ntools=len(tools),
                           coverage_table=coverage_table(cube))
    page = page.replace("/*CUBE_DATA*/", json.dumps(cube, separators=(",", ":")))
    (OUT / "index.html").write_text(page)
    print(f"wrote {OUT/'index.html'} ({len(page):,} bytes); "
          f"cube points: {len(cube['points'])}; "
          f"assets: {sorted(f.name for f in OUT.iterdir() if f.suffix in ('.jpg', '.png', '.js', '.json'))}")
    return 0


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Squishy data</title>
<style>
 body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;max-width:920px;margin:0 auto;padding:1.5rem;background:#fafafa}}
 h1{{font-size:1.9rem;margin:.1em 0}} h2{{margin:2rem 0 .3rem;font-size:1.15rem;color:#555;text-transform:uppercase;letter-spacing:.05em}}
 code{{font-family:ui-monospace,Menlo,monospace}}
 .card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:1rem 1.1rem;margin:.7rem 0}}
 .dh{{display:flex;align-items:baseline;gap:.6rem}} .dh h3{{margin:0;font-size:1.2rem}}
 .num{{color:#aaa;font-variant-numeric:tabular-nums}} .sz{{margin-left:auto;color:#777;font-size:.85rem}}
 .what{{margin:.3rem 0 .6rem;font-size:1.02rem}}
 .prev{{margin:.4rem 0}} .src{{font-size:.82rem}} a{{color:#0a5fa5}}
 pre.txt,pre.hex{{background:#f6f8fa;border:1px solid #e2e2e2;border-radius:6px;padding:.6rem;overflow:auto;font-size:.78rem;max-height:230px;margin:.2rem 0}}
 pre.run{{background:#0d1117;color:#e6edf3;border-radius:8px;padding:.85rem 1rem;font-size:.95rem;overflow:auto;margin:.4rem 0 .3rem;border:0}}
 .sitehead{{position:sticky;top:0;z-index:50;display:flex;justify-content:space-between;align-items:center;gap:1rem;
   margin:-1.5rem -1.5rem 1.4rem;padding:.5rem .9rem;background:#0d1117;color:#e6edf3;
   font:500 .82rem ui-monospace,Menlo,Consolas,monospace}}
 .sitehead a{{color:#9cd2ff;text-decoration:none;white-space:nowrap}}
 .sitehead a:hover{{text-decoration:underline}}
 .headcmd{{display:inline-flex;align-items:center;gap:.55rem;min-width:0;overflow:hidden}}
 .headcmd code{{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;user-select:all;color:#e6edf3}}
 .headcmd .codec{{color:#7ee787;transition:opacity .22s ease}}
 .copyb{{flex:none;cursor:pointer;border:1px solid #30363d;background:#161b22;color:#9cd2ff;
   border-radius:5px;padding:.08rem .45rem;font:inherit}}
 .copyb:hover{{background:#21262d}}
 .deftip{{position:relative;border-bottom:1px dotted #999;cursor:help;outline:none}}
 .deftip .pop{{visibility:hidden;opacity:0;position:absolute;left:0;bottom:1.6em;z-index:60;
   width:24rem;max-width:84vw;background:#0d1117;color:#e6edf3;border-radius:8px;padding:.6rem .75rem;
   font:400 .82rem/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
   box-shadow:0 8px 28px rgba(0,0,0,.28);transition:opacity .15s;pointer-events:none}}
 .deftip .pop b{{color:#7ee787;font-weight:600}}
 .deftip:hover .pop,.deftip:focus .pop{{visibility:visible;opacity:1}}
 @media(max-width:560px){{ .headcmd .lead-uv{{display:none}} }}
 pre.hex{{font-size:.72rem;color:#555}}
 table.data{{border-collapse:collapse;font-size:.8rem;width:100%;overflow:auto;display:block}}
 table.data th,table.data td{{border:1px solid #e2e2e2;padding:.2rem .45rem;text-align:left;white-space:nowrap}}
 table.data th{{background:#f2f2f2}}
 .cap{{color:#777;font-size:.8rem;margin:.25rem 0}}
 img.shot{{max-width:100%;border-radius:6px;border:1px solid #ddd}}
 svg.map{{width:100%;height:auto;border:1px solid #ddd;border-radius:6px;fill:#c0392b;fill-opacity:.5}}
 details.cmp{{margin-top:.5rem}} summary{{cursor:pointer;color:#555;font-size:.85rem}}
 .bar{{display:flex;align-items:center;gap:.5rem;margin:.12rem 0;font-size:.8rem}}
 .bl{{width:11rem;text-align:right;color:#444}} .bt{{flex:1;background:#eee;border-radius:3px;height:.85rem}}
 .bf{{display:block;height:100%;background:#2c6e9b;border-radius:3px}} .bv{{width:3rem;font-variant-numeric:tabular-nums}}
 table.lead{{border-collapse:collapse;width:100%;font-size:.85rem}}
 table.lead th,table.lead td{{border-bottom:1px solid #e2e2e2;padding:.35rem .5rem;text-align:right;font-variant-numeric:tabular-nums}}
 table.lead th{{cursor:pointer;text-align:right;background:#f2f2f2;position:sticky;top:0}}
 table.lead td.tool,table.lead th:first-child{{text-align:left}} td.big{{font-weight:700}}
 .flag{{color:#999;font-size:.85em}} .lbwrap{{max-height:340px;overflow:auto;border:1px solid #ddd;border-radius:8px}}
 .lede{{font-size:1.12rem;line-height:1.6;color:#333;max-width:46rem}}
 .tag{{font-size:1.15rem;color:#555;margin:.1em 0 .8em}}
 .hero{{margin:.6rem 0 1.6rem}}
 .hero .cap{{max-width:46rem}}
 .cube-wrap{{position:relative;margin:.7rem 0 .5rem;border-radius:14px;overflow:hidden;
   box-shadow:0 10px 48px rgba(10,12,20,.4);background:#0a0c11;border:1px solid #1a1f2b}}
 canvas#cube{{display:block;width:100%;height:min(64vh,600px);min-height:380px;cursor:grab;
   touch-action:none;outline:none}}
 canvas#cube:active{{cursor:grabbing}}
 canvas#cube:focus-visible{{box-shadow:inset 0 0 0 2px #9cd2ff}}
 .cube-bar{{position:absolute;top:.55rem;right:.55rem;display:flex;gap:.35rem;z-index:6}}
 .cube-bar button{{cursor:pointer;border:1px solid #2a3140;background:rgba(18,22,30,.82);
   color:#cfe3f5;border-radius:7px;padding:.22rem .55rem;font:500 .78rem ui-monospace,Menlo,monospace;
   backdrop-filter:blur(4px)}}
 .cube-bar button:hover{{background:rgba(33,38,45,.92)}}
 .tip{{position:absolute;display:none;pointer-events:none;background:rgba(12,15,22,.97);color:#e8ecf2;
   border:1px solid #2a3140;border-radius:8px;padding:.5rem .65rem;font-size:.8rem;line-height:1.5;
   max-width:20rem;z-index:7;box-shadow:0 6px 22px rgba(0,0,0,.4)}}
 .tip b{{color:#fff;font-weight:600}}
 .legend{{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem;margin:.5rem 0 0;font-size:.85rem;color:#444}}
 .legend .lg{{display:inline-flex;align-items:center;gap:.4rem}}
 .legend i{{width:.72rem;height:.72rem;border-radius:50%;display:inline-block}}
 .legend i.ring{{background:transparent;border:1.5px dashed #cc79a7;box-sizing:border-box}}
 .hint{{color:#888;font-size:.82rem}}
 .sr-only{{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
   clip:rect(0,0,0,0);white-space:nowrap;border:0}}
 details.fallback{{margin:.6rem 0 0}} details.fallback>summary{{color:#666;font-size:.85rem;cursor:pointer}}
 table.coverage{{border-collapse:collapse;width:100%;font-size:.8rem;margin-top:.5rem}}
 table.coverage th,table.coverage td{{border-bottom:1px solid #e2e2e2;padding:.25rem .5rem;
   text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
 table.coverage th{{background:#f2f2f2;text-align:right}}
 table.coverage td:first-child,table.coverage th:first-child,
 table.coverage td:nth-child(2),table.coverage th:nth-child(2){{text-align:left}}
</style></head><body>
<div class="sitehead">
  <span class="headcmd"><code id="headcmd-text"><span class="lead-uv">uv run </span>squishy-calculate --cmd "<span class="codec" id="codec">zstd -19 -c</span>"</code><button class="copyb" id="copyb" title="copy command">copy</button></span>
  <a href="https://github.com/JackDanger/squishy-corpus">GitHub&nbsp;↗</a>
</div>
<h1>Squishy</h1>
<p class="tag">One number for how well a compressor does on real data.</p>
<p class="lede">Squishy is a fixed set of real, freely-shareable files — prose, code, logs,
genomes, tables, images, binaries — picked to cover the range of things people actually
compress, from a few megabytes to several gigabytes. Run your tool over it and you get a
single <b>Squishy Score</b> you can cite and compare. It's the 2026 <span class="deftip" tabindex="0">successor<span class="pop">Every file is real and <b>freely redistributable</b> — public-domain or permissively licensed — with its source URL and SHA-256 published. So you can download, ship, cite, and freeze Squishy without licensing worry. (Silesia's files were gathered decades ago without clear licenses, which makes it awkward to redistribute today.)</span></span> to the <a href="https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia">Silesia</a> corpus.</p>

<section class="hero" aria-labelledby="coverage-h">
<h2 id="coverage-h">The shape of the corpus</h2>
<div class="cube-wrap">
  <canvas id="cube" tabindex="0" role="img"
    aria-label="3D scatter plot of the Squishy corpus. Each file is placed by its byte properties: entropy, repeat coverage, and match distance. A full data table is below.">
  </canvas>
  <div class="cube-bar">
    <button id="cube-reset" type="button" title="reset the view">reset</button>
  </div>
  <div class="tip" id="cubetip" role="status"></div>
</div>
<div class="legend" id="cubelegend"></div>
<p class="cap"><b>Each dot is a real file, placed by the shape of its bytes — and the set
deliberately spans the space.</b> Position comes only from properties of the bytes
themselves, never from how any compressor performs: <b>how random</b> they look (entropy),
<b>how much exactly repeats</b> (coverage), and <b>how far back the repeats sit</b> (match
distance); <b>dot area = file size</b>. The translucent wall is the <b>compressibility gate</b>:
files on the near side are <b>scored</b>; the near-random media behind it (photo, movie, model
weights) are kept as <b>diagnostics</b>, drawn hollow. The files are sparse — not a dense grid —
but spread across the dimensions where compressors are known to differ.
<span class="hint">Drag to rotate · scroll to zoom · hover a dot for detail · keyboard: arrows rotate, +/- zoom, 0 resets, Enter cycles dots.</span></p>
<p id="cube-status" class="sr-only" role="status" aria-live="polite"></p>
<details class="fallback"><summary>View the data as a table (no 3D required)</summary>
{coverage_table}
</details>
</section>

<h2>Score your tool</h2>
<p>One command. Hand it your compressor as a plain <code>stdin&nbsp;→&nbsp;stdout</code> command —
it streams the corpus, runs your tool over every file, and prints your score:</p>
<pre class="run">uv run squishy-calculate --cmd "zstd -19 -c"</pre>
<p class="cap">Works with any codec the same way: <code>--cmd "xz -9 -c"</code>, <code>--cmd "brotli -q 11 -c"</code>,
or your own <code>--cmd "./mytool -c"</code>. Add <code>--verify --decompress "zstd -dc"</code> to
prove it's lossless; use <code>--cmd "mytool -o {{out}} {{in}}"</code> for tools that read/write files
instead of pipes. It caches as it goes, so re-runs are instant.</p>

<h2>Reference board — draft (partial)</h2>
<div class="lbwrap"><table class="lead" id="lead"><thead><tr>{lbhead}</tr></thead><tbody>{lb}</tbody></table></div>
<p class="cap"><b>Draft, partial:</b> these run only the small members of the corpus — the large
rungs are pending, so this is <b>not yet a Squishy Score</b>. Click a column to sort; scales to any number of tool versions.</p>

<h2>Every dataset</h2>
{cards}

<script>window.CUBE_DATA=/*CUBE_DATA*/;</script>
<script src="squishy-cube.js"></script>
<script>
// mount the 3D cube from the inlined live data
if (window.SquishyCube && window.CUBE_DATA) SquishyCube.mount(
  document.getElementById('cube'), window.CUBE_DATA,
  {{legendEl: document.getElementById('cubelegend'), tooltipEl: document.getElementById('cubetip'),
    resetEl: document.getElementById('cube-reset'), statusEl: document.getElementById('cube-status')}});
// click-to-sort leaderboard (scales to many rows/versions)
document.querySelectorAll('#lead th').forEach((th,i)=>th.onclick=()=>{{
  const tb=document.querySelector('#lead tbody'),rows=[...tb.rows];
  const num=v=>parseFloat(v.replace(/[^0-9.]/g,''))||0;
  rows.sort((a,b)=> i===0 ? a.cells[0].textContent.localeCompare(b.cells[0].textContent)
                          : num(b.cells[i].textContent)-num(a.cells[i].textContent));
  rows.forEach(r=>tb.appendChild(r));
}});
// header one-liner: slowly rotate the codec; always selectable/copyable; pause on
// hover or while the user has a selection inside it.
(function(){{
  const codecs=["zstd -19 -c","xz -9 -c","brotli -q 11 -c","zstd --ultra -22 -c",
                "gzip -9 -c","bzip2 -9 -c","lz4 -9 -c","./mytool -c"];
  const el=document.getElementById('codec'),
        cmd=document.getElementById('headcmd-text'),
        wrap=document.querySelector('.headcmd'),
        btn=document.getElementById('copyb');
  if(!el) return;
  let i=0, hover=false;
  function selInside(){{ const s=window.getSelection();
    return s && !s.isCollapsed && s.anchorNode && wrap.contains(s.anchorNode); }}
  function tick(){{
    if(hover || selInside()) return;            // frozen while hovered/selected
    i=(i+1)%codecs.length; el.style.opacity=0;
    setTimeout(()=>{{ el.textContent=codecs[i]; el.style.opacity=1; }}, 220);
  }}
  setInterval(tick, 3000);
  wrap.addEventListener('mouseenter',()=>hover=true);
  wrap.addEventListener('mouseleave',()=>hover=false);
  btn.addEventListener('click',()=>{{
    navigator.clipboard.writeText(cmd.textContent.trim()).then(()=>{{
      const o=btn.textContent; btn.textContent='copied ✓';
      setTimeout(()=>btn.textContent=o, 1200);
    }}).catch(()=>{{}});
  }});
}})();
</script>
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
