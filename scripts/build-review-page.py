#!/usr/bin/env python3
"""Generate the #17 review web page: a visual representativeness + license + PII
review of the named core. One self-contained HTML file with, per core file:
kind, size, license + legal verdict, source link, a content preview, and a
checkbox. Lets the owner do the sign-off by clicking instead of reading terminal.

  uv run python scripts/build-review-page.py   # -> build/site/review.html
"""
from __future__ import annotations
import csv, html, importlib.util, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Verdicts from plans/LEGAL-REVIEW.md (what a human must confirm).
VERDICT = {
    "dickens": ("clear", "PD (Dickens d.1870); collected novels, body sliced between PG START/END markers — pure prose, all Project Gutenberg boilerplate/trademark removed."),
    "aozora": ("clear", "PD: Natsume Sōseki (夏目漱石) 1867–1916 → public domain (Japan life+70; US pre-1929). Verify via the author page linked below. Aozora Bunko; ruby/markup stripped."),
    "monorepo": ("clear", "Apache-2.0 w/ LLVM exception; LICENSE.TXT bundled in the tar."),
    "minjs": ("clear", "MIT (Plotly.js); in-file banner + LICENSES/plotly-2.27.0.LICENSE.txt."),
    "markup": ("clear", "Jon Bosak's Shakespeare XML — freely distributable."),
    "json": ("clear", "USGS earthquake catalog — US-Gov public domain, no PII."),
    "log": ("review", "NASA-HTTP 1995 web log — 'may be freely redistributed'. Contains 1995 client hostnames/IPs (30-yr-old, public). Confirm acceptable."),
    "genome": ("clear", "E. coli FASTQ (ENA DRR002013) — INSDC free; bacterium, no human-subject PII."),
    "csv": ("clear", "NOAA GHCN-Daily 2024 weather observations — U.S. Government public domain (NOAA/NCEI). No personal data."),
    "parquet": ("counsel", "NYC TLC taxi data — soft license (NYC ToS, no explicit grant). Zone-only schema, no driver/medallion/GPS PII. Counsel to bless redistribution."),
    "sqlite": ("clear", "USDA FoodData Central SR Legacy — U.S. Government public domain (USDA). No personal data."),
    "exe": ("counsel", "Hugo binary, Apache-2.0; bundles 106 modules incl. an MPL-2.0 component (hashicorp/golang-lru). Counsel to confirm MPL §3 satisfied by upstream source."),
    "photo": ("clear", "NASA Apollo-17 'Blue Marble' — confirmed PD-USGov-NASA original."),
    "movie": ("clear", "Big Buck Bunny — CC-BY 3.0 (attribution carried in NOTICE)."),
    "weights": ("clear", "all-MiniLM-L6-v2 — Apache-2.0."),
}
BADGE = {"clear": "✓ clear", "clear*": "✓ clear (glance)", "review": "● review", "counsel": "§ counsel"}


def load():
    s = importlib.util.spec_from_file_location("sq", REPO / "scripts" / "squishy.py")
    sq = importlib.util.module_from_spec(s); s.loader.exec_module(sq)
    man = {r["core_slot"]: r for r in csv.DictReader((REPO / "build/meta/LICENSE-MANIFEST.csv").open())}
    return sq, man


def preview(p: Path) -> str:
    b = p.read_bytes()[:3000]
    ftype = subprocess.run(["file", "-b", str(p)], capture_output=True, text=True).stdout.strip()
    printable = sum(32 <= c < 127 or c in (9, 10, 13) for c in b)
    if printable > len(b) * 0.85:
        txt = b.decode("utf-8", "replace")
        lines = [l for l in txt.splitlines() if l.strip()][:8]
        return ftype, "\n".join(l[:140] for l in lines)
    if p.suffix == ".tar" or (p.suffix == ".xml"):
        names = subprocess.run(["tar", "-tf", str(p)], capture_output=True, text=True).stdout.split()[:10]
        return ftype, "members: " + ", ".join(names) + " …"
    return ftype, "(binary — see file type above)"


def main() -> int:
    sq, man = load()
    cards = ""
    n = 0
    for cat, files in sq.CORE.items():
        cards += f'<h2>{html.escape(cat)}</h2>\n'
        for display, st, name in files:
            n += 1
            p = REPO / "build" / "raw" / st / name
            m = man.get(display, {})
            sz = p.stat().st_size if p.exists() else 0
            ftype, body = preview(p) if p.exists() else ("MISSING", "")
            v, note = VERDICT.get(display, ("?", ""))
            src = html.escape(m.get("source_url", ""))
            cards += f'''<section class="card">
  <div class="hd"><span class="nm">{n}. <code>{display}</code></span>
    <span class="badge b-{v.rstrip('*')}">{BADGE.get(v, v)}</span>
    <span class="sz">{sz/1e6:.1f} MB · {html.escape(m.get('license','?'))}</span></div>
  <div class="note">{html.escape(note)}</div>
  <div class="meta">type: {html.escape(ftype)} · source: <a href="{src}">{src[:70] or '—'}</a></div>
  <pre>{html.escape(body)}</pre>
  <label><input type="checkbox"> representative of <code>{display}</code>, real data, nothing private — OK</label>
</section>\n'''
    n_counsel = sum(1 for v, _ in VERDICT.values() if v == "counsel")
    n_review = sum(1 for v, _ in VERDICT.values() if v == "review")
    page = TEMPLATE.format(cards=cards, n=n, n_counsel=n_counsel, n_review=n_review)
    out = REPO / "build" / "site" / "review.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page)
    print(f"wrote {out} ({len(page)} bytes, {n} files)")
    return 0


TEMPLATE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Squishy-2026 — core review (task #17)</title>
<style>
 body{{font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;color:#111;max-width:880px;margin:0 auto;padding:1.5rem;background:#fff}}
 h1{{font-size:1.9rem;margin:.2em 0}} h2{{margin:1.8rem 0 .4rem;border-bottom:2px solid #111;padding-bottom:.15em}}
 .card{{border:1px solid #ccc;border-radius:8px;padding:.8rem 1rem;margin:.7rem 0}}
 .hd{{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap}}
 .nm{{font-size:1.1rem}} code{{font-family:ui-monospace,Menlo,monospace}}
 .sz{{color:#666;margin-left:auto;font-variant-numeric:tabular-nums}}
 .badge{{font-size:.78rem;border:1px solid #111;border-radius:12px;padding:.05rem .5rem}}
 .b-clear{{background:#111;color:#fff}} .b-review{{background:#fff}} .b-counsel{{background:#fff;border-style:dashed}}
 .note{{margin:.4rem 0;color:#333}} .meta{{font-size:.82rem;color:#666}}
 pre{{background:#f5f5f5;border:1px solid #ddd;border-radius:5px;padding:.6rem;overflow:auto;font-size:.8rem;max-height:200px}}
 label{{display:block;margin-top:.4rem;font-weight:600}} a{{color:#0a5fa5}}
 .legend{{background:#fffbe6;border:1px solid #e6d98a;padding:.7rem 1rem;border-radius:6px}}
</style></head><body>
<h1>Squishy-2026 core — review (task #17)</h1>
<p class="legend"><strong>{n} files.</strong> Badges (shape + text, not color-only):
 <b>✓ clear</b> = license/PII settled · <b>✓ clear (glance)</b> = confirm the preview ·
 <b>● review</b> = your judgment ({n_review}) · <b>§ counsel</b> = needs a lawyer ({n_counsel}).
 Tick each box once you're satisfied; the <b>§</b> items go to counsel with
 <code>plans/LEGAL-REVIEW.md</code>. Verification pass-4 (stdlib-codec rescore) is already done.</p>
{cards}
<p>When every box is ticked and counsel has cleared the <b>§</b> items, task #17 is
done → run the freeze (task #18).</p>
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
