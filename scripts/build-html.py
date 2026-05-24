#!/usr/bin/env python3
"""Generate index.html (the website) and listing.html (the script-friendly
recursive link dump) from manifest.json.

Both are inlined CSS, no external dependencies — CloudFront serves them
standalone.
"""
from __future__ import annotations
import argparse, datetime, html, json, re
from collections import defaultdict
from pathlib import Path

# ─── palette ──────────────────────────────────────────────────────────────
PALETTE = {
    "silesia":      ("#3b82f6", "#dbeafe", "Silesia"),
    "modern":       ("#10b981", "#d1fae5", "Modern"),
    "pathological": ("#f59e0b", "#fef3c7", "Pathological"),
    "negative":     ("#ef4444", "#fee2e2", "Negative"),
    "dict":         ("#8b5cf6", "#ede9fe", "Dictionary"),
    "bundles":      ("#14b8a6", "#ccfbf1", "Bundles"),
    "combined":     ("#06b6d4", "#cffafe", "Combined"),
    "meta":         ("#64748b", "#f1f5f9", "Index files"),
}

# Each codec: (name, description, levels-used, homepage/spec URL)
CODEC_INFO = {
    "gz":    ("gzip",   "DEFLATE (RFC 1951) inside a gzip wrapper (RFC 1952).",
              ["-1", "-6", "-9"], "https://www.gnu.org/software/gzip/"),
    "bz2":   ("bzip2",  "Burrows–Wheeler transform + Huffman.",
              ["-9"], "https://sourceware.org/bzip2/"),
    "xz":    ("xz",     "LZMA2 stream with xz framing. Single-thread (-T1) for determinism.",
              ["-0", "-6", "-9e"], "https://tukaani.org/xz/"),
    "zst":   ("zstd",   "Zstandard (RFC 8878). Single-thread (-T1) for determinism.",
              ["-1", "-3", "-9", "-19", "-22"], "https://github.com/facebook/zstd"),
    "lz4":   ("lz4",    "LZ77 family; built for raw speed.",
              ["-9"], "https://github.com/lz4/lz4"),
    "br":    ("brotli", "Brotli (RFC 7932): LZ77 + Huffman + 2nd-order context.",
              ["-q 1", "-q 6", "-q 11"], "https://github.com/google/brotli"),
    "lzma":  ("lzma",   "Raw LZMA stream (no .xz framing).",
              ["-9"], "https://en.wikipedia.org/wiki/LZMA"),
    "lz":    ("lzip",   "LZMA in the lzip container.",
              ["-9"], "https://www.nongnu.org/lzip/"),
    "lzo":   ("lzop",   "LZO real-time codec.",
              ["-9"], "https://www.lzop.org/"),
    "zpaq":  ("zpaq",   "Journaling archiver (Matt Mahoney).",
              ["-m5"], "http://mattmahoney.net/dc/zpaq.html"),
    "7z":    ("7-Zip",  "LZMA2 default; PPMd, bzip2, Deflate also tested.",
              ["-mx=9"], "https://www.7-zip.org/"),
    "zip":   ("zip",    "DEFLATE in the ZIP container.",
              ["-9"], "https://en.wikipedia.org/wiki/ZIP_(file_format)"),
}

ZIP_INTERNAL_INFO = {
    "deflate": ("DEFLATE (the zip default).", "https://www.rfc-editor.org/rfc/rfc1951"),
    "bzip2":   ("bzip2 inside a zip container (via 7-Zip; BSD zip lacks support).", "https://sourceware.org/bzip2/"),
    "lzma":    ("LZMA inside a zip container (via 7-Zip).", "https://en.wikipedia.org/wiki/LZMA"),
}

BUNDLE_INFO = {
    "tar":      ("POSIX ustar tar. Files sorted, mtime=@0, owner=0:0 for reproducibility.",
                 "https://www.gnu.org/software/tar/"),
    "7z":       ("7-Zip native archive. Solid by default; LZMA2 / PPMd / bzip2 / Deflate.",
                 "https://www.7-zip.org/7z.html"),
    "squashfs": ("Linux squashfs filesystem image. Solid, indexable; gzip / xz / lz4 / zstd.",
                 "https://github.com/plougher/squashfs-tools"),
    "zip":      ("ZIP container with selectable internal codec.",
                 "https://en.wikipedia.org/wiki/ZIP_(file_format)"),
    "concat":   ("Multi-member stream: each file compressed independently, frames concatenated, no tar. Tests decoder restart-state at frame boundaries.",
                 "https://datatracker.ietf.org/doc/html/rfc8878#name-concatenated-frames"),
    "skipframes": ("zstd frames concatenated with skippable metadata frames interleaved between them.",
                   "https://datatracker.ietf.org/doc/html/rfc8878#name-skippable-frames"),
}

TIER_INFO = {
    "pr":      ("PR tier",      "Small and critical, around 50 MiB. Right for per-commit CI."),
    "nightly": ("Nightly tier", "Mid-size, around 500 MiB. Right for daily runs."),
    "full":    ("Full tier",    "Everything. Several GiB. Right for release validation."),
}

# CVEs referenced in the negative section. Linked inline.
CVE_LINKS = {
    "CVE-2022-4899":  "https://nvd.nist.gov/vuln/detail/CVE-2022-4899",
    "CVE-2018-25032": "https://nvd.nist.gov/vuln/detail/CVE-2018-25032",
    "CVE-2020-8927":  "https://nvd.nist.gov/vuln/detail/CVE-2020-8927",
    "CVE-2019-12900": "https://nvd.nist.gov/vuln/detail/CVE-2019-12900",
}

# Source attributions (used in provenance section)
SOURCE_LINKS = {
    "silesia":   "https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia",
    "wanos":     "https://wanos.co/assets/silesia.tar",
    "jquery":    "https://jquery.com/",
    "bootstrap": "https://getbootstrap.com/",
    "eff":       "https://www.eff.org/",
    "cc0":       "https://creativecommons.org/publicdomain/zero/1.0/",
    "mit":       "https://opensource.org/license/mit/",
    "zstd_spec": "https://datatracker.ietf.org/doc/html/rfc8878",
    "gzip_spec": "https://datatracker.ietf.org/doc/html/rfc1952",
    "brotli_spec": "https://datatracker.ietf.org/doc/html/rfc7932",
    "deflate_spec": "https://datatracker.ietf.org/doc/html/rfc1951",
    "jack":      "https://jackdanger.com",
}

# ─── CSS ──────────────────────────────────────────────────────────────────
CSS = """
*,*::before,*::after { box-sizing: border-box; }
html { scroll-behavior: smooth; }
body {
  margin: 0; padding: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'Segoe UI', sans-serif;
  color: #0f172a; background: #ffffff;
  line-height: 1.6;
}
code, pre, .mono { font-family: 'SF Mono', 'JetBrains Mono', 'Menlo', monospace; }
a { color: #2563eb; text-decoration: none; border-bottom: 1px solid rgba(37,99,235,0.25); }
a:hover { border-bottom-color: #2563eb; }
.card a, .chip a, nav.sticky a, footer a { border-bottom: none; }
.card a:hover, nav.sticky a:hover, footer a:hover { text-decoration: underline; }
header.hero {
  padding: 88px 24px 64px;
  background:
    radial-gradient(circle at 10% 20%, #dbeafe 0%, transparent 35%),
    radial-gradient(circle at 90% 10%, #d1fae5 0%, transparent 35%),
    radial-gradient(circle at 50% 100%, #fef3c7 0%, transparent 40%),
    #ffffff;
  border-bottom: 1px solid #e2e8f0;
}
header.hero .inner { max-width: 1140px; margin: 0 auto; }
header.hero h1 {
  margin: 0 0 16px; font-size: 56px; font-weight: 800; letter-spacing: -0.02em;
  background: linear-gradient(90deg, #3b82f6, #8b5cf6, #ec4899);
  -webkit-background-clip: text; background-clip: text; color: transparent;
}
header.hero p.lede { margin: 0 0 14px; font-size: 19px; color: #334155; max-width: 760px; }
header.hero p.lede a { color: #1e40af; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 18px; }
.chip {
  display: inline-flex; align-items: center; gap: 6px;
  padding: 6px 12px; border-radius: 999px;
  font-size: 13px; font-weight: 500;
  background: #f1f5f9; color: #334155;
  border: 1px solid #e2e8f0;
}
nav.sticky {
  position: sticky; top: 0; z-index: 10;
  background: rgba(255,255,255,0.94);
  backdrop-filter: saturate(180%) blur(10px);
  border-bottom: 1px solid #e2e8f0;
}
nav.sticky .inner {
  max-width: 1140px; margin: 0 auto;
  display: flex; gap: 22px; padding: 12px 24px;
  overflow-x: auto; white-space: nowrap;
}
nav.sticky a { color: #475569; font-size: 14px; font-weight: 500; }
nav.sticky a:hover { color: #0f172a; }
main { max-width: 1140px; margin: 0 auto; padding: 36px 24px 96px; }
section { padding: 32px 0; border-bottom: 1px solid #f1f5f9; }
section:last-child { border-bottom: none; }
section h2 {
  margin: 0 0 14px; font-size: 28px; font-weight: 700; letter-spacing: -0.01em;
  display: flex; align-items: center; gap: 12px;
}
section h2 .dot { width: 14px; height: 14px; border-radius: 50%; }
section h3 { margin: 28px 0 10px; font-size: 18px; font-weight: 600; color: #1e293b; }
section p { margin: 0 0 14px; color: #334155; }
section p + p { margin-top: 12px; }
pre.snippet {
  background: #0f172a; color: #e2e8f0;
  padding: 16px 20px; border-radius: 10px;
  overflow-x: auto; font-size: 13px; line-height: 1.55;
}
pre.snippet .comment { color: #94a3b8; }
pre.snippet a { color: #93c5fd; border: none; }
.grid-2 { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 14px; }
.grid-3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 14px; }
.card {
  border: 1px solid #e2e8f0; border-radius: 10px;
  padding: 16px 18px; background: #ffffff;
  transition: transform .12s ease, box-shadow .12s ease;
}
.card:hover { transform: translateY(-2px); box-shadow: 0 8px 24px -10px rgba(15,23,42,0.16); }
.card h4 { margin: 0 0 6px; font-size: 16px; font-weight: 600; display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
.card .meta { font-size: 13px; color: #475569; line-height: 1.5; }
.card .levels { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 10px; }
.card .level {
  font-family: 'SF Mono','JetBrains Mono','Menlo',monospace;
  font-size: 11px; padding: 2px 7px; background: #f1f5f9; border-radius: 4px; color: #475569;
}
.card .ext {
  font-family: 'SF Mono','JetBrains Mono','Menlo',monospace;
  font-size: 12px; color: #94a3b8;
}
.card a { font-weight: 500; }
.card .docs-link {
  display: inline-block; margin-top: 8px; font-size: 12px; color: #2563eb;
}
table.files {
  width: 100%; border-collapse: collapse; font-size: 13px;
  background: #ffffff;
}
table.files th, table.files td {
  text-align: left; padding: 8px 10px; border-bottom: 1px solid #f1f5f9;
}
table.files th { color: #475569; font-weight: 600; background: #f8fafc; position: sticky; top: 56px; }
table.files td.size, table.files td.tier { color: #64748b; font-variant-numeric: tabular-nums; white-space: nowrap; }
table.files td.sha { font-family: 'SF Mono','JetBrains Mono','Menlo',monospace; font-size: 11px; color: #94a3b8; }
table.files td.path a { font-family: 'SF Mono','JetBrains Mono','Menlo',monospace; font-size: 12px; border: none; }
table.files td.path a:hover { text-decoration: underline; }
details {
  border: 1px solid #e2e8f0; border-radius: 8px;
  margin: 8px 0; background: #ffffff;
}
details summary {
  cursor: pointer; padding: 10px 14px;
  font-weight: 500; color: #1e293b; user-select: none;
  display: flex; align-items: center; gap: 8px;
}
details[open] summary { border-bottom: 1px solid #e2e8f0; }
details > div { padding: 4px 0 8px; max-height: 480px; overflow-y: auto; }
.tier-pill {
  display: inline-block; padding: 2px 8px; border-radius: 999px;
  font-size: 11px; font-weight: 600; text-transform: uppercase;
}
.tier-pr      { background: #dbeafe; color: #1e40af; }
.tier-nightly { background: #fef3c7; color: #92400e; }
.tier-full    { background: #ede9fe; color: #5b21b6; }
.warn-banner {
  background: #fee2e2; border: 1px solid #fca5a5; color: #991b1b;
  padding: 14px 18px; border-radius: 8px; margin: 14px 0;
  font-size: 14px; line-height: 1.55;
}
.warn-banner strong { color: #7f1d1d; }
.warn-banner a { color: #7f1d1d; }
footer {
  max-width: 1140px; margin: 0 auto; padding: 36px 24px;
  border-top: 1px solid #e2e8f0; color: #64748b; font-size: 13px;
}
footer a { color: #475569; border-bottom-color: rgba(71,85,105,0.3); }
"""

# ─── helpers ──────────────────────────────────────────────────────────────
def human_size(n: int) -> str:
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"

def category(path: str) -> str:
    if path.startswith("raw/"):        return path.split("/")[1]
    if path.startswith("individual/"): return "individual"
    if path.startswith("bundles/combined/"): return "combined"
    if path.startswith("bundles/"):    return "bundles"
    if path.startswith("dict/"):       return "dict"
    if path.startswith("negative/"):   return "negative"
    return "meta"

def base_url(bucket: str, prefix: str, path: str) -> str:
    return f"https://{bucket}/{prefix}/{path}"

def render_codec_card(codec: str) -> str:
    name, desc, levels, url = CODEC_INFO[codec]
    levels_html = "".join(f'<span class="level">{html.escape(l)}</span>' for l in levels)
    return f"""
    <div class="card">
      <h4><a href="{html.escape(url)}">{html.escape(name)}</a> <span class="ext">.{codec}</span></h4>
      <div class="meta">{html.escape(desc)}</div>
      <div class="levels">{levels_html}</div>
    </div>
    """

def render_files_table(records: list[dict], bucket: str, prefix: str, limit: int | None = None) -> str:
    rows = records[:limit] if limit else records
    body = "\n".join(
        f"""<tr>
          <td class="path"><a href="{base_url(bucket, prefix, r['path'])}">{html.escape(r['path'])}</a></td>
          <td class="size">{human_size(r['size'])}</td>
          <td class="tier"><span class="tier-pill tier-{r['tier']}">{r['tier']}</span></td>
          <td class="sha">{r['sha256'][:16]}…</td>
        </tr>"""
        for r in rows
    )
    return f"""
    <table class="files">
      <thead><tr><th>Path</th><th>Size</th><th>Tier</th><th>SHA-256</th></tr></thead>
      <tbody>{body}</tbody>
    </table>
    """

def render_collapsible_group(title: str, records: list[dict], bucket: str, prefix: str, color: str) -> str:
    if not records:
        return ""
    total_size = sum(r["size"] for r in records)
    summary = (f'<span class="dot" style="display:inline-block;width:10px;height:10px;'
               f'border-radius:50%;background:{color}"></span> '
               f'<strong>{html.escape(title)}</strong> '
               f'<span style="color:#94a3b8">— {len(records)} files, {human_size(total_size)}</span>')
    return f"""
    <details>
      <summary>{summary}</summary>
      <div>{render_files_table(records, bucket, prefix)}</div>
    </details>
    """

# ─── main render ──────────────────────────────────────────────────────────
def render_index(manifest: dict, versions_text: str) -> str:
    bucket = manifest["bucket"]; prefix = manifest["prefix"]
    artifacts = manifest["artifacts"]
    by_cat = defaultdict(list)
    for r in artifacts:
        by_cat[category(r["path"])].append(r)

    total_size = sum(r["size"] for r in artifacts)
    total_count = len(artifacts)
    built_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    indiv_groups = defaultdict(list)
    for r in by_cat["individual"]:
        rel = r["path"][len("individual/"):]
        m = re.match(r"([^/]+)/([^.]+)", rel)
        key = f"{m.group(1)}/{m.group(2)}" if m else rel
        indiv_groups[key].append(r)

    bundle_groups = defaultdict(list)
    for r in by_cat["bundles"] + by_cat["combined"]:
        parts = r["path"].split("/")
        bundle_groups[parts[1] if len(parts) > 2 else "other"].append(r)

    neg_groups = defaultdict(list)
    for r in by_cat["negative"]:
        parts = r["path"].split("/")
        neg_groups[parts[1] if len(parts) > 2 else "other"].append(r)

    codec_cards = "".join(render_codec_card(c) for c in CODEC_INFO)
    zip_cards = "".join(
        f'<div class="card"><h4><a href="{html.escape(url)}">zip / {html.escape(k)}</a> <span class="ext">.zip.{k}</span></h4>'
        f'<div class="meta">{html.escape(desc)}</div></div>'
        for k, (desc, url) in ZIP_INTERNAL_INFO.items()
    )
    bundle_cards = "".join(
        f'<div class="card"><h4><a href="{html.escape(url)}">{html.escape(k)}</a></h4>'
        f'<div class="meta">{html.escape(desc)}</div></div>'
        for k, (desc, url) in BUNDLE_INFO.items()
    )
    tier_cards = "".join(
        f'<div class="card"><h4><span class="tier-pill tier-{k}">{k}</span> '
        f'<span style="color:#64748b;font-weight:400">{html.escape(label)}</span></h4>'
        f'<div class="meta">{html.escape(desc)}</div></div>'
        for k, (label, desc) in TIER_INFO.items()
    )

    raw_tables = ""
    for cat in ("silesia", "modern", "pathological"):
        if not by_cat[cat]:
            continue
        color, soft, label = PALETTE[cat]
        raw_tables += f"""
        <h3 style="color:{color}">{html.escape(label)} — raw inputs</h3>
        {render_files_table(by_cat[cat], bucket, prefix)}
        """

    indiv_collapsibles = "".join(
        render_collapsible_group(key, sorted(files, key=lambda r: r["path"]), bucket, prefix, "#475569")
        for key, files in sorted(indiv_groups.items())
    )
    bundle_collapsibles = "".join(
        render_collapsible_group(key, sorted(files, key=lambda r: r["path"]), bucket, prefix,
                                  PALETTE.get(key, PALETTE["bundles"])[0])
        for key, files in sorted(bundle_groups.items())
    )
    neg_collapsibles = "".join(
        render_collapsible_group(key, sorted(files, key=lambda r: r["path"]), bucket, prefix, PALETTE["negative"][0])
        for key, files in sorted(neg_groups.items())
    )

    quick_start = f"""
<pre class="snippet"><span class="comment"># Browse the whole manifest (TSV; sha256, size, content-type, tier, path, description)</span>
curl -s https://{bucket}/{prefix}/index.txt

<span class="comment"># Or as JSON</span>
curl -s https://{bucket}/{prefix}/manifest.json

<span class="comment"># Verify a download</span>
curl -O https://{bucket}/{prefix}/individual/silesia/dickens.gz
curl -s https://{bucket}/{prefix}/CHECKSUMS.sha256 | grep individual/silesia/dickens.gz | sha256sum -c

<span class="comment"># Pull just the PR tier (small + critical, ~50 MiB)</span>
curl -s https://{bucket}/{prefix}/manifest.json \\
  | jq -r '.artifacts[] | select(.tier=="pr") | .path' \\
  | xargs -I{{}} curl -sO https://{bucket}/{prefix}/{{}}

<span class="comment"># Or grab the plain HTML listing and pipe through your tooling</span>
curl -s https://{bucket}/{prefix}/<a href="{base_url(bucket, prefix, 'listing.html')}">listing.html</a>
</pre>
"""

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>The Squishy Corpus</title>
  <meta name="description" content="Squishy — a corpus of real, weird, and intentionally broken files for testing compression and decompression libraries. Silesia, modern web data, pathological inputs, and CVE-shaped fixtures.">
  <link rel="canonical" href="https://{bucket}/{prefix}/">
  <style>{CSS}</style>
</head>
<body>

<header class="hero">
  <div class="inner">
    <h1>The Squishy Corpus</h1>
    <p class="lede">A corpus of heterogonous compressed content, optimized for authors of compression/decompression algorithms.</p>
    <p class="lede">This began as a mirror of [the Silesia corpus](https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia) combined with the [Squash Corpus](https://github.com/nemequ/squash-corpus), I expanded it to include pathological files (entropy extremes, files aligned to compression windows, already-compressed input, etc.) as well as malformed content, then encoded every file in every popular compression format. It's served through a CDN so you can use it in CI and scripts for testing your edge cases.
    </p>
    <p class="lede">Squishy pulls together the canonical <a href="{SOURCE_LINKS['silesia']}">Silesia corpus</a> (Sebastian Deorowicz, 2003),
    a small set of modern web and data files, deterministically-generated pathological inputs
    on every interesting decoder boundary, and a museum of malformed fixtures shaped to look
    like real-world decoder CVE classes. Maintained by <a href="{SOURCE_LINKS['jack']}">Jack Danger</a>.</p>
    <div class="chips">
      <span class="chip">{total_count} artifacts</span>
      <span class="chip">{human_size(total_size)} total</span>
      <span class="chip">{len(CODEC_INFO)} codecs</span>
      <span class="chip">3 tiers (pr · nightly · full)</span>
      <span class="chip">Reproducible</span>
    </div>
  </div>
</header>

<nav class="sticky">
  <div class="inner">
    <a href="#quick-start">Quick start</a>
    <a href="#provenance">Provenance</a>
    <a href="#algorithms">Algorithms</a>
    <a href="#bundles-info">Bundle formats</a>
    <a href="#tiers">Tiers</a>
    <a href="#raw">Raw inputs</a>
    <a href="#individual">Individual</a>
    <a href="#bundles">Bundles</a>
    <a href="#dict">Dictionary</a>
    <a href="#negative">Negative</a>
    <a href="#index">Index files</a>
  </div>
</nav>

<main>

<section id="quick-start">
  <h2><span class="dot" style="background:#3b82f6"></span> Quick start</h2>
  <p>Every artifact lives at <code>https://{bucket}/{prefix}/&lt;path&gt;</code>. Paths are stable.
  Bytes are byte-immutable for a given snapshot of tool versions (recorded in
  <a href="{base_url(bucket, prefix, 'versions.txt')}">versions.txt</a>). Served via CloudFront with
  <code>Cache-Control: immutable</code>; downloads should be fast.</p>
  {quick_start}
</section>

<section id="provenance">
  <h2><span class="dot" style="background:#10b981"></span> Provenance &amp; licensing</h2>

  <h3>Silesia</h3>
  <p>The twelve original Silesia files (Charles Dickens text, a Mozilla executable tar, a 3D MRI image,
  an NCI chemical database, an OpenOffice DLL, a synthetic database, an uncompressed PDF of Reymont's
  <em>Chłopi</em>, a Samba source tarball, the SAO star catalog, Webster's dictionary in HTML, a 16-bit
  grayscale x-ray, and an XML bundle) come from
  <a href="{SOURCE_LINKS['silesia']}">Sebastian Deorowicz's 2003 corpus</a>, extracted from the
  canonical mirror at <a href="{SOURCE_LINKS['wanos']}">wanos.co/assets/silesia.tar</a>. Use under
  the same terms as the upstream distribution.</p>

  <h3>Modern</h3>
  <p>License-clean fetches from the open web — <a href="{SOURCE_LINKS['jquery']}">jQuery 2.1.4</a>
  (<a href="{SOURCE_LINKS['mit']}">MIT</a>), <a href="{SOURCE_LINKS['bootstrap']}">Bootstrap 3.3.6</a>
  (<a href="{SOURCE_LINKS['mit']}">MIT</a>), and a snapshot of the <a href="{SOURCE_LINKS['eff']}">EFF</a>
  homepage as a representative HTML sample. Alongside, a small set of synthetic but representative
  modern files generated locally from a fixed PRNG seed: a JSON record collection, an NDJSON log dump,
  a SQLite database, a parquet-shaped fixture, protobuf wire bytes, syslog-style lines, and
  deterministic random bytes. Synthetic content is in the
  <a href="{SOURCE_LINKS['cc0']}">public domain (CC0)</a>.</p>

  <h3>Pathological</h3>
  <p>Generated locally from a fixed seed. Sub-window-size inputs (0, 1, 13, 256, 4095, 65535 bytes —
  the sizes where stored-block fallback paths live). Window-boundary triples for the major codecs:
  zstd at 128 MiB ± 1, brotli at 16 MiB ± 1, deflate at 32 KiB ± 1. Entropy extremes: zeros, urandom,
  single-byte-repeating, alternating bits, ASCII rotating, one-byte-per-page, a phrase repeated to
  10 MiB, pi digits as ASCII, sparse-geometric, and an already-gzipped blob. All
  <a href="{SOURCE_LINKS['cc0']}">CC0</a>.</p>

  <h3>Negative</h3>
  <p>Derived from good fixtures by deterministic mutation. <strong>Intentionally malformed</strong> —
  see the <a href="#negative">negative section</a> below for the categories and the warning.</p>
</section>

<section id="algorithms">
  <h2><span class="dot" style="background:#8b5cf6"></span> Algorithms &amp; levels</h2>
  <p>Each raw input is compressed by every algorithm I could reasonably hold to deterministic flags,
  at several levels per codec. Bytes are stable across runs; pinned tool versions live in
  <a href="{base_url(bucket, prefix, 'versions.txt')}">versions.txt</a>. The flags suppress embedded
  timestamps and force single-threaded operation where parallelism injects nondeterminism
  (xz <code>-T1</code>, zstd <code>-T1</code>).</p>
  <div class="grid-3">{codec_cards}</div>

  <h3>ZIP containers with internal-codec variants</h3>
  <p>Several internal codecs in the same ZIP container, so a decoder can be exercised on the same
  data shape with different inner formats. (We omit <code>zip / store</code> per the
  &ldquo;no uncompressed publish&rdquo; policy — it's just a container.)</p>
  <div class="grid-3">{zip_cards}</div>
</section>

<section id="bundles-info">
  <h2><span class="dot" style="background:#14b8a6"></span> Bundle formats</h2>
  <p>Each set is also packaged as combined archives across every container × codec combination.
  Solid-archive ordering matters for ratio (the encoder sees a continuous stream), so for
  <a href="https://www.7-zip.org/7z.html">7z</a> and <a href="https://github.com/plougher/squashfs-tools">squashfs</a>
  bundles we ship both alphabetical and size-descending orderings. The
  <code>concat-*</code> variants concatenate independent compressed frames with no tar wrapper,
  which is how <a href="{SOURCE_LINKS['gzip_spec']}">gzip</a>, <a href="{SOURCE_LINKS['zstd_spec']}">zstd</a>,
  and <a href="https://tukaani.org/xz/format.html">xz</a> streams behave in practice.</p>
  <div class="grid-3">{bundle_cards}</div>
</section>

<section id="tiers">
  <h2><span class="dot" style="background:#f59e0b"></span> Tiers</h2>
  <p>Every artifact carries a <code>tier</code> field in the manifest so you can grab the
  subset that matches your scenario.</p>
  <div class="grid-3">{tier_cards}</div>
</section>

<section id="raw">
  <h2><span class="dot" style="background:#3b82f6"></span> Raw inputs</h2>
  <p>The unmodified source files. Note: the uncompressed bytes are <em>not</em> published to S3 — the
  canonical &ldquo;raw&rdquo; delivery on the wire is the <code>.gz</code> version at
  <code>individual/&lt;set&gt;/&lt;file&gt;.gz</code> (gzip <code>-9</code>, deterministic). Decompress
  client-side if you need the raw bytes. The tables below describe the files; click a path to
  go to its gzipped form.</p>
  {raw_tables}
</section>

<section id="individual">
  <h2><span class="dot" style="background:#64748b"></span> Individual compressions</h2>
  <p>Each raw input compressed by each codec at multiple levels. Click a file to expand its variants.</p>
  {indiv_collapsibles}
</section>

<section id="bundles">
  <h2><span class="dot" style="background:#14b8a6"></span> Bundles</h2>
  <p>Combined archives per set (Silesia, modern, pathological) and across all three (<em>combined</em>).</p>
  {bundle_collapsibles}
</section>

<section id="dict">
  <h2><span class="dot" style="background:#8b5cf6"></span> Zstd dictionaries</h2>
  <p>A trained <a href="https://github.com/facebook/zstd#the-case-for-small-data-compression">zstd dictionary</a>
  derived from NDJSON samples, alongside the same content compressed with and without the dict, and
  the dict applied to non-matching content (the worst-case scenario, where the dict actively hurts ratio).</p>
  {render_files_table(by_cat['dict'], bucket, prefix)}
</section>

<section id="negative">
  <h2><span class="dot" style="background:#ef4444"></span> Negative fixtures</h2>
  <div class="warn-banner">
    <strong>Warning.</strong> Files under <code>negative/</code> are intentionally malformed.
    They will crash, OOM, or produce wrong output in libraries that lack robustness checks.
    Apply expansion-size and time caps before feeding them to a decoder. The <code>bomb/</code>
    subdirectory contains decompression bombs that expand to multi-gigabyte outputs from small
    inputs — set a decompressed-size cap.
  </div>
  <p>These fixtures are here because if your decoder doesn't crash on a truncated frame, doesn't
  OOM on a deceitful header, and doesn't silently produce wrong output on a checksum mismatch,
  those are properties worth measuring. Shapes include truncation at decoder-sensitive offsets,
  single-byte flips at magic/header/body/checksum, declared-length attacks (header lies about
  uncompressed size), valid-empty streams (positive sanity), good-then-corrupt concatenations,
  zstd-skippable-only streams, decompression bombs, and shapes modeled on known CVE classes:</p>
  <ul>
    <li><a href="{CVE_LINKS['CVE-2022-4899']}">CVE-2022-4899</a> — zstd out-of-bounds read on crafted dictionary</li>
    <li><a href="{CVE_LINKS['CVE-2018-25032']}">CVE-2018-25032</a> — zlib memory corruption on specific deflate input</li>
    <li><a href="{CVE_LINKS['CVE-2020-8927']}">CVE-2020-8927</a> — brotli buffer overflow on crafted ring-buffer size</li>
    <li><a href="{CVE_LINKS['CVE-2019-12900']}">CVE-2019-12900</a> — bzip2 OOB write on crafted N_SELECTORS</li>
    <li><a href="https://snyk.io/research/zip-slip-vulnerability">Zip Slip</a> — path traversal via crafted zip entry names</li>
  </ul>
  {neg_collapsibles}
</section>

<section id="index">
  <h2><span class="dot" style="background:#64748b"></span> Index files</h2>
  <p>For machines and scripts. Stable URLs, short cache TTL so updates propagate.</p>
  <ul>
    <li><a href="{base_url(bucket, prefix, 'index.txt')}"><code>index.txt</code></a> — tab-separated: <code>sha256 \\t size \\t content-type \\t tier \\t path \\t description</code></li>
    <li><a href="{base_url(bucket, prefix, 'manifest.json')}"><code>manifest.json</code></a> — same data as JSON, with bucket and prefix</li>
    <li><a href="{base_url(bucket, prefix, 'CHECKSUMS.sha256')}"><code>CHECKSUMS.sha256</code></a> — GNU sha256sum format (<code>sha256sum -c</code> compatible)</li>
    <li><a href="{base_url(bucket, prefix, 'expected-ratio.json')}"><code>expected-ratio.json</code></a> — known compressed sizes per (input, codec, level) for benchmark regression detection</li>
    <li><a href="{base_url(bucket, prefix, 'versions.txt')}"><code>versions.txt</code></a> — pinned versions of every tool used to build this snapshot</li>
    <li><a href="{base_url(bucket, prefix, 'README.txt')}"><code>README.txt</code></a> — human-readable overview &amp; methodology</li>
    <li><a href="{base_url(bucket, prefix, 'listing.html')}"><code>listing.html</code></a> — <strong>plain HTML directory listing</strong>: one <code>&lt;a href&gt;</code> per artifact, easy to pipe through <code>wget</code>, <code>grep</code>, or any URL-extracting script</li>
  </ul>
</section>

</main>

<footer>
  Built {html.escape(built_at)} • {total_count} artifacts, {human_size(total_size)} total
  • <a href="{base_url(bucket, prefix, 'versions.txt')}">tool versions</a>
  • <a href="{SOURCE_LINKS['jack']}">jackdanger.com</a>
  <br>
  Storage: S3 ONEZONE_IA (single-AZ; the whole corpus is rederivable from the Makefile).
  Served by CloudFront with <code>Cache-Control: public, max-age=31536000, immutable</code>.
</footer>

</body>
</html>
"""

def render_listing(manifest: dict) -> str:
    """Plain HTML: one anchor per line, in a <pre>. Easy to:
       - wget -i (extract URLs first: grep -oE 'href="[^"]+"' | cut -d'"' -f2)
       - browse manually
    """
    bucket = manifest["bucket"]; prefix = manifest["prefix"]
    lines = []
    for r in sorted(manifest["artifacts"], key=lambda r: r["path"]):
        url = base_url(bucket, prefix, r["path"])
        lines.append(f'<a href="{html.escape(url)}">{html.escape(r["path"])}</a>')
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>Squishy — listing</title></head>\n'
        '<body><pre>\n'
        + "\n".join(lines)
        + "\n</pre></body></html>\n"
    )

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True, help="path to build/meta")
    args = ap.parse_args()
    meta = Path(args.meta)

    manifest = json.loads((meta / "manifest.json").read_text())
    versions = (meta / "versions.txt").read_text() if (meta / "versions.txt").exists() else ""

    (meta / "index.html").write_text(render_index(manifest, versions))
    (meta / "listing.html").write_text(render_listing(manifest))
    print(f"wrote {meta / 'index.html'} and {meta / 'listing.html'}")

if __name__ == "__main__":
    main()
