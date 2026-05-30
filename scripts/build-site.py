#!/usr/bin/env python3
"""Generate the Squishy website: a single, self-contained, accessible index.html.

Reads the score board (build/meta/squishy-scores.json), provenance
(build/meta/LICENSE-MANIFEST.csv), and the locked core (scripts/squishy.py CORE).
Emits build/site/index.html with the data embedded as JSON.

Design rules:
  - Clear & accessible FIRST: semantic HTML, readable without JavaScript, ARIA
    labels, high contrast.
  - Colorblind-safe: information is carried by POSITION (bar length), SHAPE
    (category glyphs), and TEXT — never by hue alone.
  - Fun to explore SECOND (progressive enhancement): sort the board, filter the
    core by category, inspect a per-file × per-codec ratio grid.

Usage: uv run python scripts/build-site.py [--base-url <https prefix for downloads>]
"""
from __future__ import annotations
import argparse, csv, importlib.util, json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
# shape glyphs (not color) distinguish categories — colorblind-safe
CAT_GLYPH = {
    "Prose": "●", "Code & Web": "◆", "Structured": "▲",
    "Tabular / DB": "■", "Binary & Media": "★",
}


def load_core():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    core = {}
    for cat, files in sq.CORE.items():
        core[cat] = [{"name": d, "set": st, "file": n} for (d, st, n) in files]
    return core


def load_manifest():
    p = REPO / "build" / "meta" / "LICENSE-MANIFEST.csv"
    rows = {}
    if p.exists():
        for r in csv.DictReader(p.open()):
            rows[r["core_slot"]] = r
    return rows


def load_board():
    p = REPO / "build" / "meta" / "squishy-scores.json"
    return json.loads(p.read_text()) if p.exists() else {"panel": {}}


def human(n: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return f"{n:.0f} {u}" if u == "B" else f"{n/1:.0f} {u}".replace(f"{n:.0f}", f"{n:.1f}") if False else f"{n/ (1024**(['B','KB','MB','GB'].index(u))):.1f} {u}"
        n_next = n
    return f"{n} B"


def hsize(n: int) -> str:
    for i, u in enumerate(("B", "KB", "MB", "GB")):
        v = n / (1024 ** i)
        if v < 1024 or u == "GB":
            return f"{v:.0f} {u}" if u == "B" else f"{v:.1f} {u}"
    return f"{n} B"


def build(base_url: str) -> str:
    core, man, board = load_core(), load_manifest(), load_board()
    panel = board.get("panel", {})
    # assemble per-file rows with provenance + download url
    cats = []
    for cat, files in core.items():
        items = []
        for f in files:
            slot = f["name"] if f["name"] not in ("markup",) else "markup"
            m = man.get(f["name"], {})
            items.append({
                "name": f["name"], "file": f["file"],
                "size": int(m.get("size_bytes", 0)),
                "license": m.get("license", "—"),
                "source": m.get("source_url", ""),
                "url": f"{base_url}/{f['file']}",
            })
        cats.append({"cat": cat, "glyph": CAT_GLYPH.get(cat, "•"), "items": items})

    # board rows sorted by score desc (skip codecs with no score yet)
    rows = []
    for codec, r in panel.items():
        sc = r.get("squishy_score")
        if isinstance(sc, (int, float)) and sc == sc:
            rows.append({"codec": codec, "score": sc, "bpb": r.get("bpb"),
                         "cats": r.get("categories", {}), "per_file": r.get("per_file", {}),
                         "version": r.get("codec_version", ""), "cmd": r.get("codec_command", "")})
    rows.sort(key=lambda x: -x["score"])
    maxscore = max((x["score"] for x in rows), default=10)

    data = {"cats": cats, "rows": rows, "maxscore": maxscore,
            "edition": board.get("edition", "Squishy-2026-DRAFT"),
            "status": board.get("status", ""), "cat_glyph": CAT_GLYPH}

    # ---- HTML (semantic, accessible, no color-only encoding) ----
    board_table = ""
    for x in rows:
        pct = 100 * x["score"] / maxscore
        board_table += (
            f'<tr><th scope="row">{x["codec"]}</th>'
            f'<td class="num">{x["score"]:.2f}×</td>'
            f'<td class="num">{x["bpb"]:.2f}</td>'
            f'<td class="bar"><span class="barfill" style="width:{pct:.1f}%" '
            f'aria-hidden="true"></span><span class="vis">{x["score"]:.2f}×</span></td></tr>\n')

    cat_sections = ""
    for c in cats:
        rowsf = ""
        for it in c["items"]:
            dl = f'<a href="{it["url"]}">download</a>' if it["url"] else "—"
            rowsf += (f'<tr><th scope="row"><code>{it["name"]}</code></th>'
                      f'<td>{it["file"]}</td><td class="num">{hsize(it["size"]) if it["size"] else "—"}</td>'
                      f'<td>{it["license"]}</td><td>{dl}</td></tr>\n')
        cat_sections += (
            f'<section class="cat" data-cat="{c["cat"]}"><h3>'
            f'<span class="glyph" aria-hidden="true">{c["glyph"]}</span> {c["cat"]} '
            f'<span class="count">({len(c["items"])})</span></h3>'
            f'<table><thead><tr><th scope="col">name</th><th scope="col">file</th>'
            f'<th scope="col">size</th><th scope="col">license</th><th scope="col"></th>'
            f'</tr></thead><tbody>{rowsf}</tbody></table></section>\n')

    return TEMPLATE.format(
        board_table=board_table, cat_sections=cat_sections,
        edition=data["edition"], status=data["status"],
        data_json=json.dumps(data), base_url=base_url,
        n_files=sum(len(c["items"]) for c in cats))


TEMPLATE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Squishy — the 2026 compression benchmark</title>
<style>
  :root {{ --ink:#111; --mut:#666; --line:#ccc; --bg:#fff; --accent:#1a1a1a; --fill:#444; }}
  * {{ box-sizing:border-box; }}
  body {{ font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
         color:var(--ink); background:var(--bg); margin:0; }}
  main {{ max-width:920px; margin:0 auto; padding:1.5rem; }}
  h1 {{ font-size:2.4rem; margin:.2em 0; letter-spacing:-.02em; }}
  h2 {{ margin-top:2.2rem; border-bottom:2px solid var(--ink); padding-bottom:.2em; }}
  .tag {{ color:var(--mut); font-size:1.05rem; }}
  code,pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }}
  pre {{ background:#f4f4f4; padding:.9rem 1rem; border-radius:6px; overflow:auto; border:1px solid var(--line); }}
  table {{ border-collapse:collapse; width:100%; margin:.6rem 0; }}
  th,td {{ text-align:left; padding:.4rem .6rem; border-bottom:1px solid var(--line); }}
  td.num,th.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  .bar {{ width:40%; position:relative; }}
  .barfill {{ display:inline-block; height:1em; background:var(--fill); vertical-align:middle;
             border-radius:2px; min-width:2px; }}
  .vis {{ position:absolute; right:.5rem; font-size:.8rem; color:#fff; mix-blend-mode:difference; }}
  .glyph {{ display:inline-block; width:1.4em; text-align:center; }}
  .count {{ color:var(--mut); font-weight:normal; font-size:.9rem; }}
  .controls button {{ font:inherit; padding:.35rem .7rem; margin:.15rem; border:1px solid var(--line);
                      background:#fafafa; border-radius:20px; cursor:pointer; }}
  .controls button[aria-pressed=true] {{ background:var(--ink); color:#fff; border-color:var(--ink); }}
  .note {{ background:#fffbe6; border:1px solid #e6d98a; padding:.6rem .9rem; border-radius:6px; }}
  .hero-num {{ font-size:1.3rem; }}
  footer {{ color:var(--mut); margin:3rem 0 1rem; font-size:.9rem; }}
  a {{ color:#0b5; }}  /* links underlined too (not color-only) */
  a {{ color:#0a5fa5; text-decoration:underline; }}
</style></head>
<body><main>
  <h1>Squishy</h1>
  <p class="tag"><strong>The 2026 compression benchmark.</strong> Real data, one citable number.</p>
  <pre>squishy bench --cmd "zstd -19 -c"
&rarr; Squishy Score: 4.3&times;  ({n_files}/{n_files} files)</pre>
  <p class="note">Edition: <strong>{edition}</strong>. {status}</p>

  <h2>Reference board</h2>
  <p>Squishy Score = geometric mean of per-file compression ratio over the {n_files}-file
     real core. Higher is squishier. Bar length encodes the score (no color needed).</p>
  <div class="controls" role="group" aria-label="sort board">
    <button data-sort="score" aria-pressed="true">sort by score</button>
    <button data-sort="bpb">sort by bits/byte</button>
  </div>
  <table id="board"><thead><tr><th scope="col">codec</th><th scope="col" class="num">Squishy</th>
    <th scope="col" class="num">bpb</th><th scope="col">score</th></tr></thead>
    <tbody>{board_table}</tbody></table>

  <h2>The core — {n_files} real files</h2>
  <p>Every file is real and provenanced (source + license + SHA-256). Shapes mark
     categories so they're distinguishable without color.</p>
  <div class="controls" id="catfilter" role="group" aria-label="filter by category"></div>
  <div id="cats">{cat_sections}</div>

  <h2>Explore: per-file &times; per-codec</h2>
  <p>How each codec compresses each file (ratio, &times;). Click a column header to sort.
     Numbers, not colors.</p>
  <div id="grid" aria-live="polite"></div>

  <h2>Run it</h2>
  <pre>squishy bench --cmd "&lt;your codec, reads stdin writes compressed stdout&gt;"
# file-arg codecs: use {{in}} / {{out}} placeholders</pre>
  <p>The runner fetches the core by SHA-256, verifies every file, runs your codec
     once per file under the <a href="RULES.md">rules</a>, and exits non-zero if
     anything is missing or altered.</p>

  <footer>Squishy is the successor to the Silesia corpus. Versioned, dated editions;
     frozen and DOI-minted at release. Speed is reported separately, never in the
     canonical score.</footer>
</main>
<script id="data" type="application/json">{data_json}</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
// --- board sort ---
const board = document.querySelector('#board tbody');
const baseRows = [...board.rows];
document.querySelectorAll('.controls [data-sort]').forEach(b=>b.onclick=()=>{{
  document.querySelectorAll('.controls [data-sort]').forEach(x=>x.setAttribute('aria-pressed','false'));
  b.setAttribute('aria-pressed','true');
  const key=b.dataset.sort, idx = key==='score'?1:2;
  const asc = key==='bpb';
  [...board.rows].sort((a,c)=>{{
    const av=parseFloat(a.cells[idx].textContent), cv=parseFloat(c.cells[idx].textContent);
    return asc? av-cv : cv-av;
  }}).forEach(r=>board.appendChild(r));
}});
// --- category filter ---
const cf=document.getElementById('catfilter');
const allBtn=document.createElement('button'); allBtn.textContent='all'; allBtn.setAttribute('aria-pressed','true');
cf.appendChild(allBtn);
D.cats.forEach(c=>{{const b=document.createElement('button');
  b.textContent=(D.cat_glyph[c.cat]||'•')+' '+c.cat; b.dataset.cat=c.cat; b.setAttribute('aria-pressed','false'); cf.appendChild(b);}});
cf.querySelectorAll('button').forEach(b=>b.onclick=()=>{{
  cf.querySelectorAll('button').forEach(x=>x.setAttribute('aria-pressed','false'));
  b.setAttribute('aria-pressed','true');
  const want=b.dataset.cat;
  document.querySelectorAll('#cats .cat').forEach(s=>{{s.style.display=(!want||s.dataset.cat===want)?'':'none';}});
}});
// --- per-file x per-codec grid ---
const files=[]; D.cats.forEach(c=>c.items.forEach(it=>files.push(it.name)));
const codecs=D.rows.map(r=>r.codec);
let html='<table><thead><tr><th scope="col">file</th>'+codecs.map(c=>`<th scope="col" class="num">${{c}}</th>`).join('')+'</tr></thead><tbody>';
files.forEach(f=>{{
  html+=`<tr><th scope="row"><code>${{f}}</code></th>`;
  D.rows.forEach(r=>{{const v=r.per_file[f]; html+=`<td class="num">${{v?v.toFixed(2)+'×':'—'}}</td>`;}});
  html+='</tr>';
}});
html+='</tbody></table>';
document.getElementById('grid').innerHTML=html;
</script>
</body></html>
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url",
                    default="https://squishy-corpus.s3.us-west-2.amazonaws.com/draft/corpus")
    ap.add_argument("--out", default=str(REPO / "build" / "site" / "index.html"))
    args = ap.parse_args()
    html = build(args.base_url)
    out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html)
    print(f"wrote {out} ({len(html)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
