#!/usr/bin/env python3
"""Build the Squishy website — the single explorer page at build/site/index.html,
plus its rendered preview assets (image thumbnail, video poster frame). Shows each
dataset clearly: a preview of every file, what it is, and how every tool+version
compresses it, in a layout that scales to many versions.

  uv run --with pyarrow --with pandas python scripts/build-site.py   # or: make site
"""
from __future__ import annotations
import csv, html, importlib.util, json, re, sqlite3, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "build" / "site"          # the site lives here: index.html + its assets
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
 "symbols": "DWARF debug symbols from a Lua 5.4.8 build compiled with -g (a debug-info file, not a runnable program).",
 "wasm": "The SQLite engine compiled to WebAssembly — stack-machine bytecode.",
 "winexe": "The fd file-finder as a Windows PE executable.",
 "armexe": "The hyperfine benchmarking tool as an ARM64 Linux executable.",
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


def cat_anchor(cat: str) -> str:
    """Stable in-page anchor id for a category heading (e.g. 'Tabular / DB' → cat-tabular-db)."""
    return "cat-" + re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-")


def human_bytes(n) -> str:
    n = float(n or 0)
    if n >= 1e9: return f"{n/1e9:.2f} GB"
    if n >= 1e6: return f"{n/1e6:.1f} MB"
    if n >= 1e3: return f"{n/1e3:.0f} KB"
    return f"{int(n)} B"


def metrics_strip(pm: dict | None, size: int) -> str:
    """The card's at-a-glance stat row: the three intrinsic byte axes the explorer
    plots — entropy, repetition, repeat distance — plus the file's size. The three
    axis swatches are colour-matched to the hero's axis key (m-e/m-r/m-d). `pm` is the
    file's file-properties / scale-properties entry (None ⇒ only size is shown)."""
    cells = []
    if pm:
        dist = pm.get("match_distance_p90", pm.get("match_distance", 0))
        cells += [
            ("m-e", "entropy", f"{pm['entropy']:.2f}", "bits per byte (0–8): how random the bytes look"),
            ("m-r", "repetition", f"{pm['coverage']*100:.1f}%", "share of 16-byte blocks with an earlier exact copy"),
            ("m-d", "repeat distance", human_bytes(dist), "how far back those repeats sit (90th percentile)"),
        ]
    cells.append(("m-s", "size", human_bytes(size), "uncompressed size of the file"))
    lis = "".join(
        f'<li class="{cls}" title="{esc(tip)}"><span class="mv">{esc(v)}</span>'
        f'<span class="ml">{esc(label)}</span></li>'
        for cls, label, v, tip in cells)
    return f'<ul class="metrics">{lis}</ul>'


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
    "Tabular / DB": "#0072B2", "Binary & Media": "#CC79A7",
}


# Short, friendly on-canvas labels (the full filename ellipsizes uselessly when the
# dots sit together). Keyed by the cube point name (core display, or stripped scale name).
SHORT = {
    "dickens": "Dickens", "aozora": "Aozora", "monorepo": "LLVM", "minjs": "Plotly.js",
    "markup": "Shakespeare", "json": "Earthquakes", "log": "NASA log", "genome": "E. coli",
    "csv": "Weather", "parquet": "Airline", "sqlite": "USDA foods", "exe": "Hugo",
    "symbols": "Lua DWARF", "wasm": "SQLite/Wasm", "winexe": "fd (PE)", "armexe": "hyperfine (ARM64)",
    "photo": "Blue Marble", "movie": "Big Buck Bunny", "weights": "SmolLM2-135M",
    "noaa-ghcn-daily-2024-full.csv": "Weather ’24", "noaa-ghcn-daily-2021-2023.csv": "Weather ’21–23",
    "big-buck-bunny-1080p.mov": "Big Buck Bunny HD", "ecoli-DRR002013-full.fastq": "E. coli (full)",
    "enwik9.txt": "enwik9", "llvm-project-19.1.0.src.tar": "LLVM (full)",
    "nasa-http-jul-aug-1995.log": "NASA log (full)", "clang-releases-16-17-18-19.tar": "clang ×4",
    "bts-ontime-2022-2024": "Airline (3 yr)",
}
# friendly descriptions for files whose kind-description would be wrong/absent
SCALE_DESC = {
    "enwik9.txt": "The first billion bytes of an English Wikipedia XML dump (the Hutter-Prize text).",
    "clang-releases-16-17-18-19.tar": "Four LLVM/Clang release source trees concatenated — a real software archive.",
    "big-buck-bunny-1080p.mov": "The full open film Big Buck Bunny in 1080p H.264 video.",
}


def cube_data(sq, props, scale=None) -> dict:
    """Build the 3D scatter from INTRINSIC byte properties (file-properties.json for the
    core, scale-properties.json for the scale tier): x=entropy, y=repeat coverage,
    z=repeat distance (bytes, log). Size is the dot area. No compressor is referenced —
    the axes are properties of the bytes themselves. Per-point provenance (short name,
    plain-English description, license, source + download links) is merged from
    edition.json so the hover card can be friendly and link out."""
    import math
    ed = {}
    edp = REPO / "build/meta/edition.json"
    if edp.exists():
        for f in json.loads(edp.read_text()).get("files", []):
            # Index by BOTH display and name: the cube's `entries` are keyed by display
            # for core members but by filename for the scale rungs, and a scored cell's
            # display is now its cell id (e.g. "monorepo-L"), not its filename — so match
            # on either to keep every point's category/license/links.
            for raw in (f.get("display"), f.get("name")):
                if raw:
                    ed[raw.replace(".parquet", "").replace(".safetensors", "")] = f
    K = props["block_bytes"]
    entries = dict(props["files"])                      # core (measured)
    if scale:
        for nm, m in scale.get("files", {}).items():    # scale tier (measured, real)
            entries[nm.replace(".parquet", "").replace(".safetensors", "")] = m
    sizes = [m["size"] for m in entries.values()]
    # z-axis = p90 repeat distance ("how far back the farthest repeats sit") — a truer
    # measure of long-range structure than the median, which is dominated by local repeats.
    def p90(m): return max(m.get("match_distance_p90", m["match_distance"]), K)
    dists = [p90(m) for m in entries.values()]
    lo, hi = math.log10(min(sizes)), math.log10(max(sizes))
    pts = []
    for d, m in entries.items():
        e = ed.get(d, {})
        kind = e.get("kind", d)
        desc = SCALE_DESC.get(d) or WHATIS.get(d) or WHATIS.get(kind) or ""
        pts.append({
            "name": d, "label": SHORT.get(d, d),
            "cat": e.get("category") or m.get("category", "Binary & Media"),
            "kind": kind, "desc": re.sub("<[^>]+>", "", desc),
            "license": e.get("license"), "source_url": e.get("source_url"), "url": e.get("url"),
            "x": m["entropy"], "y": m["coverage"], "z": p90(m),
            "r": round((math.log10(m["size"]) - lo) / (hi - lo), 3),
            "sizeMB": m["size"] / 1e6, "entropy": m["entropy"],
            "coverage": m["coverage"], "dist": m["match_distance"],
            "distp90": m.get("match_distance_p90", m["match_distance"]),
        })
    # Every file is scored (one vote per file) — there is no compressibility gate and no
    # plane to draw. The scatter is purely the three intrinsic byte axes.
    return {
        "axes": {"x": {"label": "entropy", "min": 0, "max": 8, "log": False},
                 "y": {"label": "repetition", "min": 0, "max": 1, "log": False},
                 "z": {"label": "repeat distance", "min": K, "max": max(dists), "log": True}},
        "categories": CUBE_COLORS,
        "points": pts}


def coverage_table(cube: dict) -> str:
    """Accessible, non-3D fallback for the explorer: the same points as a sortable
    table. Mirrors the tooltip fields so screen-reader / no-WebGL users get everything."""
    head = ["category", "file", "entropy (bpb)", "coverage", "match distance", "size"]
    rows = []
    for p in sorted(cube["points"], key=lambda p: (p["cat"], -p["sizeMB"])):
        dist = p["dist"]
        ds = (f"{dist/1e6:.1f} MB" if dist >= 1e6 else f"{dist/1e3:.0f} KB"
              if dist >= 1e3 else f"{dist} B")
        sz = (f"{p['sizeMB']/1000:.1f} GB" if p["sizeMB"] >= 1000 else f"{p['sizeMB']:.1f} MB")
        rows.append([p["cat"], p["name"], f"{p['entropy']:.2f}",
                     f"{p['coverage']*100:.0f}%", ds, sz])
    h = "<tr>" + "".join(f"<th>{esc(c)}</th>" for c in head) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="coverage"><thead>{h}</thead><tbody>{body}</tbody></table>'


def scale_what(name: str) -> str:
    n = name.lower()
    if "0.5b" in n or "0p5b" in n:
        return ("Qwen2.5-0.5B — a 0.5B-parameter language model's weights "
                "(Apache-2.0). The lower rung of the weights size-ladder above the "
                "scored SmolLM2-135M cell.")
    if "1.5b" in n or "1p5b" in n:
        return ("Qwen2.5-1.5B — a larger (1.5B-parameter) language model's weights "
                "(Apache-2.0). The top rung of the ladder; multi-GB, for large-window "
                "and throughput work.")
    return "Scale-tier file — a large rung for large-window and throughput testing."


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


def preview(display, st, name, url=""):
    p = REPO / "build" / "raw" / st / name
    if not p.exists():
        # The bytes are always served; this checkout just may not hold them. Link, don't apologise.
        return (f'<p class="cap">Peek-inside builds from a local copy. '
                f'<a href="{esc(url)}" download>Download <code>{esc(name)}</code> ↓</a></p>'
                if url else '<p class="cap">Preview unavailable in this checkout.</p>')
    try:
        if display == "photo":
            return '<img class="shot" src="photo.jpg" alt="NASA Blue Marble">'
        if display == "movie":
            return '<img class="shot" src="movie.jpg" alt="Big Buck Bunny frame"><div class="cap">frame from the clip</div>'
        if display == "csv":     return csv_table(p)
        if display == "parquet": return parquet_table(p)
        if display == "sqlite":  return sqlite_table(p)
        if display == "weights": return weights_table(p)
        if display in ("exe", "winexe", "armexe", "wasm", "symbols"):
            return hexdump(p)
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
    # The headline board = the complete whole-corpus computation (every panel codec over
    # every scored file, core + large rungs). This is THE Squishy Score the site displays.
    cbp = REPO / "build/meta/squishy-board-complete.json"
    cb = json.loads(cbp.read_text()) if cbp.exists() else {"codecs": {}}

    # Edition manifest = the source of truth for what we SERVE: every member's public
    # download URL and size. We key it by display (core) and by name (scale) so a card
    # always links to the live file and shows its real size, whether or not the bytes
    # happen to be present locally. file-properties / scale-properties give the per-file
    # byte-axis metrics shown on each card.
    edf = json.loads((REPO / "build/meta/edition.json").read_text()).get("files", [])
    ed_by_disp = {f["display"]: f for f in edf if f.get("display")}
    ed_by_name = {f["name"]: f for f in edf if f.get("name")}
    props = json.loads((REPO / "build/meta/file-properties.json").read_text())
    sp = REPO / "build/meta/scale-properties.json"
    scale = json.loads(sp.read_text()) if sp.exists() else {"files": {}}

    # tools: scalable list of (label, flag, squishy, bpb, categories, per_file).
    # The complete board keys per_file by filename; the dataset cards key by display
    # slug — map filename → display so the per-file bars line up. (Scale-tier filenames
    # have no display slug and pass through unchanged, harmless to the core cards.)
    name2disp = {n: d for files in sq.CORE.values() for (d, _s, n) in files}
    tools = []
    for codec, r in cb.get("codecs", {}).items():
        nm, flag = tool_label(codec, r.get("codec_version", ""), r.get("codec_command", ""))
        pf = {name2disp.get(k, k): v for k, v in r.get("per_file", {}).items()}
        tools.append({"label": nm, "flag": flag, "sq": r.get("squishy_score"), "bpb": r.get("corpus_bpb"),
                      "cats": r.get("categories", {}), "pf": pf})
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
        cards += f'<h2 id="{esc(cat_anchor(cat))}">{esc(cat)}</h2>'
        for d, st, nm in files:
            n += 1; m = man.get(d, {}); e = ed_by_disp.get(d, {}); pm = props["files"].get(d)
            size = (pm or {}).get("size") or int(e.get("size_bytes") or 0)
            url = e.get("url", ""); src_url = e.get("source_url") or m.get("source_url", "")
            license_ = e.get("license") or m.get("license", "?")
            # compression bars per tool@version (sorted, scalable)
            bars = ""
            for t in sorted(tools, key=lambda x: -(x["pf"].get(d, 0))):
                r = t["pf"].get(d)
                if r is None: continue
                w = 100 * r / maxr[d]
                bars += (f"<div class='bar'><span class='bl'>{esc(t['label'])} {esc(t['flag'])}</span>"
                         f"<span class='bt'><span class='bf' style='width:{w:.0f}%'></span></span>"
                         f"<span class='bv'>{r:.2f}×</span></div>")
            dl = f'<a class="dl" href="{esc(url)}" download>download <code>{esc(nm)}</code> ↓</a>' if url else ""
            src = f'<a href="{esc(src_url)}">source ↗</a>' if src_url else ""
            cards += f'''<section class="card" id="file-{esc(d)}">
  <div class="dh"><span class="num">{n}</span><h3><code>{esc(d)}</code></h3>
    <span class="sz">{esc(human_bytes(size))} · {esc(license_)}</span></div>
  <p class="what">{WHATIS.get(d,'')}</p>
  <div class="prev">{preview(d, st, nm, url)}</div>
  {metrics_strip(pm, size)}
  <div class="src">{dl}{' · ' if dl and src else ''}{src}</div>
  <details class="cmp"><summary>compression — {len(tools)} tools</summary>{bars}</details>
</section>'''
    # ── The scale tier: large members of the one corpus ────────────────────
    core_slots = {d for files in sq.CORE.values() for (d, _s, _n) in files}
    rows = list(csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open()))
    extra = [r for r in rows if r["core_slot"] not in core_slots]
    if extra:
        cards += ('<h2>The big ones</h2>'
                  '<p class="cap">The same kinds of data at gigabyte scale, where long-range '
                  'matching and big compression windows start to matter. Most are scored cells '
                  'in the board above; the largest few — the 1080p film, the full-year NOAA CSV, '
                  'and the two larger Qwen weights — are non-scored diagnostics (throughput and '
                  'behaviour at extreme sizes, never folded into the Squishy Score).</p>')
        for r in sorted(extra, key=lambda r: int(r["size_bytes"])):
            nm = r["name"]; e = ed_by_name.get(nm, {}); pm = scale["files"].get(nm)
            size = (pm or {}).get("size") or int(r["size_bytes"])
            url = e.get("url", "")
            is_scored = bool(e.get("scored", False))
            badge = "scored" if is_scored else "diagnostic"
            local = next((p for p in REPO.glob(f"build/raw/*/{nm}") if p.exists()), None)
            prev = preview_safetensors(local) if (local and nm.endswith(".safetensors")) else ""
            what = (SCALE_DESC.get(nm) or scale_what(nm))
            if not is_scored:
                what = "Non-scored diagnostic — distributed for throughput/behaviour at scale, never in the score. " + what
            dl = f'<a class="dl" href="{esc(url)}" download>download ↓</a> · ' if url else ""
            cards += f'''<section class="card" id="file-{esc(re.sub(r"[^a-z0-9]+","-",nm.lower()).strip("-"))}">
  <div class="dh"><span class="num">{esc(badge)}</span><h3><code>{esc(nm)}</code></h3>
    <span class="sz">{esc(human_bytes(size))} · {esc(r['license'])}</span></div>
  <p class="what">{esc(what)}</p>
  {prev}
  {metrics_strip(pm, size)}
  <div class="src">{dl}sha256 <code>{esc(r['sha256'][:16])}…</code> · <a href="{esc(r['source_url'])}">source ↗</a></div>
</section>'''

    # 3D cube: built from the INTRINSIC byte properties loaded above (file-properties.json
    # + scale-properties.json), inlined + shipped as a data file; the maintainable renderer
    # source is copied in.
    cube = cube_data(sq, props, scale if scale["files"] else None)
    (OUT / "cube-data.json").write_text(json.dumps(cube, indent=2))
    import shutil
    shutil.copyfile(ASSETS / "squishy-cube.js", OUT / "squishy-cube.js")

    # Stage the SERVED metadata into the site build so the single deploy path
    # (deploy-site.sh pushes build/site → the draft prefix) keeps the public mirror
    # current. Without this the CDN's CHECKSUMS.sha256 / edition.json silently go
    # stale (the bug that left the published manifest missing the 4 executables).
    # These are exactly the meta files the freeze allowlist copies into the frozen
    # edition, so the public mirror and the Zenodo deposit carry identical metadata.
    META = REPO / "build" / "meta"
    served_meta = [
        "edition.json", "schema.json", "baseline.json", "CHECKSUMS.sha256",
        "LICENSE-MANIFEST.csv", "NOTICE",
        "squishy-board-complete.json", "squishy-score-complete.json",
        "file-properties.json", "scale-properties.json", "size-convergence.json",
        "verification-pass4.json",
    ]
    for name in served_meta:
        src = META / name
        if src.exists():
            shutil.copyfile(src, OUT / name)
        else:
            print(f"  ⚠ served meta missing, not staged: {name}")
    if (META / "LICENSES").is_dir():
        shutil.copytree(META / "LICENSES", OUT / "LICENSES", dirs_exist_ok=True)

    page = TEMPLATE.format(lbhead=lbhead, lb=lb, cards=cards, n=n, ntools=len(tools),
                           coverage_table=coverage_table(cube))
    page = page.replace("/*CUBE_DATA*/", json.dumps(cube, separators=(",", ":")))
    (OUT / "index.html").write_text(page)
    print(f"wrote {OUT/'index.html'} ({len(page):,} bytes); "
          f"cube points: {len(cube['points'])}; "
          f"assets: {sorted(f.name for f in OUT.iterdir() if f.suffix in ('.jpg', '.png', '.js', '.json'))}")
    return 0


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Squishy</title>
<style>
 body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;max-width:920px;margin:0 auto;padding:1.5rem;background:#fafafa}}
 h1{{font-size:1.9rem;margin:.1em 0}} h2{{margin:2rem 0 .3rem;font-size:1.15rem;color:#555;text-transform:uppercase;letter-spacing:.05em}}
 code{{font-family:ui-monospace,Menlo,monospace}}
 .card{{background:#fff;border:1px solid #ddd;border-radius:10px;padding:1rem 1.1rem;margin:.7rem 0}}
 .dh{{display:flex;align-items:baseline;gap:.6rem}} .dh h3{{margin:0;font-size:1.2rem}}
 .num{{color:#aaa;font-variant-numeric:tabular-nums}} .sz{{margin-left:auto;color:#777;font-size:.85rem}}
 .what{{margin:.3rem 0 .6rem;font-size:1.02rem}}
 .prev{{margin:.4rem 0}} .src{{font-size:.82rem}} a{{color:#0a5fa5}}
 .src .dl{{font-weight:600}}
 .card{{scroll-margin-top:1rem}} h2{{scroll-margin-top:1rem}}
 :target.card{{box-shadow:0 0 0 2px #2c6e9b}}
 .lede a{{color:inherit;text-decoration:underline;text-decoration-color:#b9c6d2;
   text-underline-offset:2px}}
 .lede a:hover{{text-decoration-color:#0a5fa5;color:#0a5fa5}}
 ul.metrics{{display:flex;flex-wrap:wrap;gap:.4rem;margin:.55rem 0 .5rem;padding:0;list-style:none}}
 ul.metrics li{{flex:1 1 0;min-width:5rem;background:#f7f9fb;border:1px solid #e7eaee;
   border-radius:8px;padding:.4rem .3rem;text-align:center;cursor:help}}
 ul.metrics .mv{{display:block;font-size:1.02rem;font-weight:650;
   font-variant-numeric:tabular-nums;line-height:1.2}}
 ul.metrics .ml{{display:block;font-size:.66rem;color:#888;text-transform:uppercase;
   letter-spacing:.03em;margin-top:.15rem}}
 ul.metrics .m-e .mv{{color:#b23a6b}} ul.metrics .m-r .mv{{color:#1f8a5a}}
 ul.metrics .m-d .mv{{color:#2a6f9e}} ul.metrics .m-s .mv{{color:#333}}
 pre.txt,pre.hex{{background:#f6f8fa;border:1px solid #e2e2e2;border-radius:6px;padding:.6rem;overflow:auto;font-size:.78rem;max-height:230px;margin:.2rem 0}}
 pre.run{{background:#0d1117;color:#e6edf3;border-radius:8px;padding:.85rem 1rem;font-size:.95rem;overflow:auto;margin:.4rem 0 .3rem;border:0}}
 .head{{display:flex;justify-content:space-between;align-items:baseline;gap:1rem}}
 .head a{{font-size:.9rem;white-space:nowrap}}
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
   background:#fafafa;border:1px solid #e7e7e9}}
 canvas#cube{{display:block;width:100%;height:min(76vh,720px);min-height:440px;cursor:grab;
   touch-action:none;outline:none;background:#fafafa}}
 canvas#cube:active{{cursor:grabbing}}
 canvas#cube:focus-visible{{box-shadow:inset 0 0 0 2px #2c6e9b}}
 .cube-bar{{position:absolute;top:.55rem;right:.55rem;display:flex;gap:.35rem;z-index:6}}
 .cube-bar button{{cursor:pointer;border:1px solid #d4d7dc;background:rgba(255,255,255,.85);
   color:#333;border-radius:7px;padding:.22rem .55rem;font:500 .78rem ui-monospace,Menlo,monospace;
   backdrop-filter:blur(4px)}}
 .cube-bar button:hover{{background:#fff;border-color:#b9bdc4}}
 .tip{{position:absolute;display:none;pointer-events:auto;background:#fff;color:#1c2530;
   border:1px solid #d6d9de;border-radius:10px;padding:.6rem .75rem;font-size:.82rem;line-height:1.5;
   max-width:23rem;z-index:7;box-shadow:0 10px 30px rgba(20,24,33,.20)}}
 .tip b{{color:#111;font-weight:600}}
 .tip .tdesc{{color:#333;margin:.3rem 0 .35rem}}
 .tip .tnums{{color:#555;font-size:.78rem}}
 .tip a{{color:#0b6bcb;text-decoration:none;font-weight:500}} .tip a:hover{{text-decoration:underline}}
 .tip .tlinks{{margin-top:.45rem;display:flex;flex-wrap:wrap;gap:.1rem .8rem}}
 .legend{{display:flex;flex-wrap:wrap;gap:.4rem 1.1rem;margin:.5rem 0 0;font-size:.85rem;color:#444}}
 .legend .lg{{display:inline-flex;align-items:center;gap:.4rem}}
 .legend i{{width:.72rem;height:.72rem;border-radius:50%;display:inline-block}}
 .legend i.dotsm{{width:.4rem;height:.4rem;background:#9aa0a6}}
 .legend i.dotbig{{width:.85rem;height:.85rem;background:#9aa0a6;margin-right:-.15rem}}
 .hint{{color:#888;font-size:.82rem}}
 .axiskey{{list-style:none;padding:0;margin:.2rem 0 .15rem;display:flex;flex-wrap:wrap;gap:.3rem 1.6rem;font-size:.97rem;color:#2a2a2a}}
 ul.readkey{{list-style:none;padding:0;margin:.3rem 0 .6rem;display:grid;gap:.32rem;max-width:46rem}}
 ul.readkey li{{font-size:.95rem;color:#333;line-height:1.45}}
 ul.readkey b{{color:#111}}
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
<div class="head">
  <h1>Squishy</h1>
  <a href="https://github.com/JackDanger/squishy-corpus">GitHub ↗</a>
</div>
<p class="lede">A corpus of realistic stuff you might compress.
Squishy is a fixed set of real files: <a href="#file-dickens">novels</a>,
<a href="#file-monorepo">source code</a>, <a href="#file-log">server logs</a>,
<a href="#file-genome">genome reads</a>, <a href="#file-csv">weather tables</a>,
<a href="#file-sqlite">databases</a>, <a href="#file-exe">executables</a>,
<a href="#file-photo">a photo</a>, <a href="#file-movie">a film clip</a>, the
<a href="#file-weights">weights of a neural network</a>. They range from a few
megabytes to a few gigabytes, and every one
is freely redistributable, with its source and checksum published. Run any compressor
over the set and you get a single Squishy Score you can cite and compare. It's a
successor to the <a href="https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia">Silesia</a> corpus.</p>
<section class="hero" aria-labelledby="coverage-h">
<h2 id="coverage-h">The shape of the corpus</h2>
<p>Each dot is one file, placed by three measurements of its bytes. No compressor is
involved in the placement.</p>
<ul class="axiskey">
  <li><b style="color:#b23a6b">&rarr; entropy</b> — how random the bytes look (bits per byte, 0–8)</li>
  <li><b style="color:#1f8a5a">&uarr; repetition</b> — how much of the file repeats itself (16-byte blocks with an earlier exact copy)</li>
  <li><b style="color:#2a6f9e">&nearr; repeat distance</b> — how far back those copies sit (log scale)</li>
</ul>
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
<p class="cap">Color is the kind of data. The corpus is chosen so the dots spread across the whole space instead
of piling up in one corner.</p>
<p class="hint">Drag to rotate · scroll to zoom · hover a dot for details · keyboard: arrows rotate, +/− zoom, 0 resets, Enter steps through.</p>
<p id="cube-status" class="sr-only" role="status" aria-live="polite"></p>
<details class="fallback"><summary>View the data as a table (no 3D required)</summary>
{coverage_table}
</details>
</section>

<p class="tag">The <strong>Squishy Score</strong> is the geomean of compression across all files - a stable number reference number.</p>

<h2>Score your tool</h2>
<p>Give it any compressor that reads stdin and writes stdout, such as
<code>"xz -9 -c"</code> or your own <code>"./mytool -c"</code>. It streams the corpus,
runs your tool on every file, and prints the score:</p>
<pre class="run">uv run squishy-calculate --cmd "zstd -19 -c"</pre>
<p class="cap">The Squishy Score is the geometric mean of each file's compression ratio
(original size ÷ compressed size). Every file counts once; nothing is weighted,
excluded, or tuned. Beside it the runner prints corpus bpb, the plain bits-per-byte
over all bytes, where the big files dominate.</p>
<ul class="readkey">
  <li>Tools that need files instead of pipes: <code>"mytool -o {{out}} {{in}}"</code>.</li>
  <li>Add <code>--verify --decompress "zstd -dc"</code> to prove the round trip is lossless.</li>
  <li>Files and results are cached, so re-runs are instant.</li>
</ul>

<h2>The Squishy Score board</h2>
<p class="cap">Six familiar codecs over the whole corpus — every file counts once.
The headline column is the Squishy Score (geomean of per-file ratio); corpus bpb is the
byte-weighted companion. Click a column to sort.</p>
<div class="lbwrap"><table class="lead" id="lead"><thead><tr>{lbhead}</tr></thead><tbody>{lb}</tbody></table></div>

<h2>How to cite</h2>
<p class="cap">Cite the dated edition you ran against. The DOI resolves to the immutable
Zenodo deposit — corpus bytes, score, and metadata — so a citation can never drift from
what you measured.</p>
<pre class="run">Jack Danger. Squishy-2026: a citable compression benchmark corpus and score. 2026.
DOI: 10.5281/zenodo.XXXXXXX</pre>
<p class="cap">The machine-readable citation (and BibTeX) live in <code>CITATION.cff</code>.
The <code>XXXXXXX</code> is assigned when the edition is minted, then filled in once and
never changed.</p>

<h2>Every file</h2>
<p class="cap">What each file is, a peek inside, and how each tool compresses it.</p>
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
</script>
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
