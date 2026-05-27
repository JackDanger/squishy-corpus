"""Generate index.html and listing.html from manifest.json.

Both files are self-contained with inlined CSS — no external dependencies.

Public interface: run(cfg: BuildConfig) -> int
"""
from __future__ import annotations

import datetime
import html
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_text_atomic

# ── palette ──────────────────────────────────────────────────────────────────

PALETTE: dict[str, tuple[str, str, str]] = {
    "silesia":      ("#3b82f6", "#dbeafe", "Silesia"),
    "modern":       ("#10b981", "#d1fae5", "Modern"),
    "pathological": ("#f59e0b", "#fef3c7", "Pathological"),
    "squash":       ("#ec4899", "#fce7f3", "Squash"),
    "negative":     ("#ef4444", "#fee2e2", "Negative"),
    "dict":         ("#8b5cf6", "#ede9fe", "Dictionary"),
    "bundles":      ("#14b8a6", "#ccfbf1", "Bundles"),
    "combined":     ("#06b6d4", "#cffafe", "Combined"),
    "meta":         ("#64748b", "#f1f5f9", "Index files"),
}

CODEC_INFO: dict[str, tuple[str, str, list[str], str]] = {
    "gz":   ("gzip",   "DEFLATE (RFC 1951) inside a gzip wrapper (RFC 1952).",
             ["-1", "-6", "-9"], "https://www.gnu.org/software/gzip/"),
    "bz2":  ("bzip2",  "Burrows–Wheeler transform + Huffman.",
             ["-9"], "https://sourceware.org/bzip2/"),
    "xz":   ("xz",     "LZMA2 stream with xz framing. Single-thread (-T1) for determinism.",
             ["-0", "-6", "-9e"], "https://tukaani.org/xz/"),
    "zst":  ("zstd",   "Zstandard (RFC 8878). Single-thread (-T1) for determinism.",
             ["-1", "-3", "-9", "-19", "-22"], "https://github.com/facebook/zstd"),
    "lz4":  ("lz4",    "LZ77 family; built for raw speed.",
             ["-9"], "https://github.com/lz4/lz4"),
    "br":   ("brotli", "Brotli (RFC 7932): LZ77 + Huffman + 2nd-order context.",
             ["-q 1", "-q 6", "-q 11"], "https://github.com/google/brotli"),
    "lzma": ("lzma",   "Raw LZMA stream (no .xz framing).",
             ["-9"], "https://en.wikipedia.org/wiki/LZMA"),
    "lz":   ("lzip",   "LZMA in the lzip container.",
             ["-9"], "https://www.nongnu.org/lzip/"),
    "lzo":  ("lzop",   "LZO real-time codec.",
             ["-9"], "https://www.lzop.org/"),
    "zpaq": ("zpaq",   "Journaling archiver (Matt Mahoney).",
             ["-m5"], "http://mattmahoney.net/dc/zpaq.html"),
    "7z":   ("7-Zip",  "LZMA2 default; PPMd, bzip2, Deflate also tested.",
             ["-mx=9"], "https://www.7-zip.org/"),
    "zip":  ("zip",    "DEFLATE in the ZIP container.",
             ["-9"], "https://en.wikipedia.org/wiki/ZIP_(file_format)"),
}

ZIP_INTERNAL_INFO: dict[str, tuple[str, str]] = {
    "deflate": ("DEFLATE (the zip default).", "https://www.rfc-editor.org/rfc/rfc1951"),
    "bzip2":   ("bzip2 inside a zip container (via 7-Zip; BSD zip lacks support).", "https://sourceware.org/bzip2/"),
    "lzma":    ("LZMA inside a zip container (via 7-Zip).", "https://en.wikipedia.org/wiki/LZMA"),
}

BUNDLE_INFO: dict[str, tuple[str, str]] = {
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

TIER_INFO: dict[str, tuple[str, str]] = {
    "pr":      ("PR tier",      "Small and critical, around 50 MiB. Right for per-commit CI."),
    "nightly": ("Nightly tier", "Mid-size, around 500 MiB. Right for daily runs."),
    "full":    ("Full tier",    "Everything. Several GiB. Right for release validation."),
}

CVE_LINKS: dict[str, str] = {
    "CVE-2022-4899":  "https://nvd.nist.gov/vuln/detail/CVE-2022-4899",
    "CVE-2018-25032": "https://nvd.nist.gov/vuln/detail/CVE-2018-25032",
    "CVE-2020-8927":  "https://nvd.nist.gov/vuln/detail/CVE-2020-8927",
    "CVE-2019-12900": "https://nvd.nist.gov/vuln/detail/CVE-2019-12900",
    "CVE-2022-37434": "https://nvd.nist.gov/vuln/detail/CVE-2022-37434",
}

SOURCE_LINKS: dict[str, str] = {
    "silesia":       "https://sun.aei.polsl.pl/~sdeor/index.php?page=silesia",
    "wanos":         "https://wanos.co/assets/silesia.tar",
    "jquery":        "https://jquery.com/",
    "bootstrap":     "https://getbootstrap.com/",
    "eff":           "https://www.eff.org/",
    "cc0":           "https://creativecommons.org/publicdomain/zero/1.0/",
    "mit":           "https://opensource.org/license/mit/",
    "zstd_spec":     "https://datatracker.ietf.org/doc/html/rfc8878",
    "gzip_spec":     "https://datatracker.ietf.org/doc/html/rfc1952",
    "brotli_spec":   "https://datatracker.ietf.org/doc/html/rfc7932",
    "deflate_spec":  "https://datatracker.ietf.org/doc/html/rfc1951",
    "jack":          "https://jackdanger.com",
    "squash":        "https://github.com/nemequ/squash-corpus",
    "squash_corpus": "https://github.com/nemequ/squash-corpus",
    "inter":         "https://rsms.me/inter/",
    "react":         "https://react.dev/",
    "msgpack":       "https://msgpack.org/",
    "arrow":         "https://arrow.apache.org/",
}

# ── CSS ───────────────────────────────────────────────────────────────────────

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
table.files th { color: #475569; font-weight: 600; background: #f8fafc; position: sticky; top: 0; }
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

# ── helpers ───────────────────────────────────────────────────────────────────


def _human_size(n: int) -> str:
    for unit in ["B", "KiB", "MiB", "GiB"]:
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} TiB"


def _category(path: str) -> str:
    if path.startswith("raw/"):
        return path.split("/")[1]
    if path.startswith("individual/"):
        return "individual"
    if path.startswith("bundles/combined/"):
        return "combined"
    if path.startswith("bundles/"):
        return "bundles"
    if path.startswith("dict/"):
        return "dict"
    if path.startswith("negative/"):
        return "negative"
    return "meta"


def _artifact_url(bucket: str, prefix: str, path: str) -> str:
    return f"https://{bucket}/{prefix}/{path}"


def _render_codec_card(codec: str) -> str:
    name, desc, levels, url = CODEC_INFO[codec]
    levels_html = "".join(f'<span class="level">{html.escape(l)}</span>' for l in levels)
    return (
        f'<div class="card">'
        f'<h4><a href="{html.escape(url)}">{html.escape(name)}</a>'
        f' <span class="ext">.{codec}</span></h4>'
        f'<div class="meta">{html.escape(desc)}</div>'
        f'<div class="levels">{levels_html}</div>'
        f'</div>'
    )


def _render_files_table(records: list[dict], bucket: str, prefix: str) -> str:
    rows = "\n".join(
        f'<tr>'
        f'<td class="path"><a href="{_artifact_url(bucket, prefix, r["path"])}">{html.escape(r["path"])}</a></td>'
        f'<td class="size">{_human_size(r["size"])}</td>'
        f'<td class="tier"><span class="tier-pill tier-{r["tier"]}">{r["tier"]}</span></td>'
        f'<td class="sha">{r["sha256"][:16]}…</td>'
        f'</tr>'
        for r in records
    )
    return (
        '<table class="files">'
        '<thead><tr><th>Path</th><th>Size</th><th>Tier</th><th>SHA-256</th></tr></thead>'
        f'<tbody>{rows}</tbody>'
        '</table>'
    )


def _render_collapsible_group(
    title: str,
    records: list[dict],
    bucket: str,
    prefix: str,
    color: str,
) -> str:
    if not records:
        return ""
    total_size = sum(r["size"] for r in records)
    summary = (
        f'<span class="dot" style="display:inline-block;width:10px;height:10px;'
        f'border-radius:50%;background:{color}"></span> '
        f'<strong>{html.escape(title)}</strong> '
        f'<span style="color:#94a3b8">— {len(records)} files, {_human_size(total_size)}</span>'
    )
    return (
        f'<details>'
        f'<summary>{summary}</summary>'
        f'<div>{_render_files_table(records, bucket, prefix)}</div>'
        f'</details>'
    )


# ── page renderers ────────────────────────────────────────────────────────────


def render_index(manifest: dict, versions_text: str) -> str:
    bucket = manifest["bucket"]
    prefix = manifest["prefix"]
    artifacts = manifest["artifacts"]

    by_cat: dict[str, list[dict]] = defaultdict(list)
    for r in artifacts:
        by_cat[_category(r["path"])].append(r)

    total_size = sum(r["size"] for r in artifacts)
    total_count = len(artifacts)
    built_at = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")

    indiv_groups: dict[str, list[dict]] = defaultdict(list)
    for r in by_cat["individual"]:
        rel = r["path"][len("individual/"):]
        m = re.match(r"([^/]+)/([^.]+)", rel)
        key = f"{m.group(1)}/{m.group(2)}" if m else rel
        indiv_groups[key].append(r)

    bundle_groups: dict[str, list[dict]] = defaultdict(list)
    for r in by_cat["bundles"] + by_cat["combined"]:
        parts = r["path"].split("/")
        bundle_groups[parts[1] if len(parts) > 2 else "other"].append(r)

    neg_groups: dict[str, list[dict]] = defaultdict(list)
    for r in by_cat["negative"]:
        parts = r["path"].split("/")
        neg_groups[parts[1] if len(parts) > 2 else "other"].append(r)

    codec_cards = "".join(_render_codec_card(c) for c in CODEC_INFO)
    zip_cards = "".join(
        f'<div class="card"><h4><a href="{html.escape(url)}">zip / {html.escape(k)}</a>'
        f' <span class="ext">.zip.{k}</span></h4>'
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
    for cat in ("silesia", "squash", "modern", "pathological"):
        if not by_cat[cat]:
            continue
        color, soft, label = PALETTE[cat]
        raw_tables += (
            f'<h3 style="color:{color}">{html.escape(label)} — raw inputs</h3>'
            + _render_files_table(by_cat[cat], bucket, prefix)
        )

    indiv_collapsibles = "".join(
        _render_collapsible_group(
            key, sorted(files, key=lambda r: r["path"]), bucket, prefix, "#475569"
        )
        for key, files in sorted(indiv_groups.items())
    )
    bundle_collapsibles = "".join(
        _render_collapsible_group(
            key, sorted(files, key=lambda r: r["path"]), bucket, prefix,
            PALETTE.get(key, PALETTE["bundles"])[0],
        )
        for key, files in sorted(bundle_groups.items())
    )
    neg_collapsibles = "".join(
        _render_collapsible_group(
            key, sorted(files, key=lambda r: r["path"]), bucket, prefix, PALETTE["negative"][0],
        )
        for key, files in sorted(neg_groups.items())
    )

    base = f"https://{bucket}/{prefix}"
    quick_start = (
        f'<pre class="snippet">'
        f'<span class="comment"># Browse the whole manifest (TSV; sha256, size, content-type, tier, path, description)</span>\n'
        f"curl -s {base}/index.txt\n\n"
        f'<span class="comment"># Or as JSON</span>\n'
        f"curl -s {base}/manifest.json\n\n"
        f'<span class="comment"># Verify a download</span>\n'
        f"curl -O {base}/individual/silesia/dickens.gz\n"
        f"curl -s {base}/CHECKSUMS.sha256 | grep individual/silesia/dickens.gz | sha256sum -c\n\n"
        f'<span class="comment"># Pull just the PR tier (small + critical, ~50 MiB)</span>\n'
        f"curl -s {base}/manifest.json \\\\\n"
        f"  | jq -r '.artifacts[] | select(.tier==\"pr\") | .path' \\\\\n"
        f"  | xargs -I{{}} curl -sO {base}/" + "{}\n\n"
        f'<span class="comment"># Or grab the plain HTML listing and pipe through your tooling</span>\n'
        f'curl -s {base}/<a href="{base}/listing.html">listing.html</a>\n'
        f'</pre>'
    )

    cve_items = "".join(
        f'<li><a href="{url}">{html.escape(cve)}</a> — {html.escape(_cve_desc(cve))}</li>'
        for cve, url in CVE_LINKS.items()
    )
    cve_items += '<li><a href="https://snyk.io/research/zip-slip-vulnerability">Zip Slip</a> — path traversal via crafted zip entry names</li>'

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>The Squishy Corpus</title>
  <meta name="description" content="Test files for compression and decompression libraries: Silesia, modern web files, decoder edge cases, and intentionally malformed fixtures. Pre-compressed in every common format, served from a CDN with stable URLs.">
  <link rel="canonical" href="{base}/">
  <style>{CSS}</style>
</head>
<body>

<header class="hero">
  <div class="inner">
    <h1>The Squishy Corpus</h1>
    <p class="lede">
      Test fixtures for compression and decompression libraries.
      <a href="https://jackdanger.com/squishy">Squishy</a> combines
      <a href="{SOURCE_LINKS['silesia']}">Silesia</a> (2003),
      <a href="{SOURCE_LINKS['squash']}">Squash</a> (~2015), modern web formats,
      and pathological edge cases — pre-compressed in every common format and
      served from a CDN with stable, immutable URLs.
    </p>
    <p class="lede">Usable from CI without vendoring anything.</p>
    <div class="chips">
      <span class="chip">{total_count} artifacts</span>
      <span class="chip">{_human_size(total_size)} total</span>
      <span class="chip">{len(CODEC_INFO)} codecs</span>
      <span class="chip">3 tiers (pr · nightly · full)</span>
      <span class="chip">Reproducible</span>
    </div>
  </div>
</header>

<nav class="sticky">
  <div class="inner">
    <a href="#quick-start">Quick start</a>
    <a href="#raw">What's in it</a>
    <a href="#provenance">Provenance</a>
    <a href="#algorithms">Algorithms</a>
    <a href="#bundles-info">Bundle formats</a>
    <a href="#tiers">Tiers</a>
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
  <p>Every artifact lives at <code>{base}/&lt;path&gt;</code>. Paths are stable.
  Bytes are byte-immutable for a given snapshot of tool versions (recorded in
  <a href="{base}/versions.txt">versions.txt</a>). Served via CloudFront with
  <code>Cache-Control: immutable</code>; downloads should be fast.</p>
  {quick_start}
</section>

<section id="raw">
  <h2><span class="dot" style="background:#3b82f6"></span> Raw inputs</h2>
  <p>The unmodified source files. The uncompressed bytes are <em>not</em> published to S3 — the
  canonical &ldquo;raw&rdquo; delivery is the <code>.gz</code> version at
  <code>individual/&lt;set&gt;/&lt;file&gt;.gz</code> (gzip <code>-9</code>, deterministic).</p>
  {raw_tables}
</section>

<section id="provenance">
  <h2><span class="dot" style="background:#10b981"></span> Provenance &amp; licensing</h2>
  <p>I built this when I needed real test fixtures for <a href="https://github.com/JackDanger/gzippy">gzippy</a>.
  Sources and licenses for each dataset are below.</p>

  <h3>Silesia</h3>
  <p>The twelve original Silesia files come from
  <a href="{SOURCE_LINKS['silesia']}">Sebastian Deorowicz's 2003 corpus</a>, extracted from the
  canonical mirror at <a href="{SOURCE_LINKS['wanos']}">wanos.co/assets/silesia.tar</a>. Use under
  the same terms as the upstream distribution.</p>

  <h3>Squash</h3>
  <p>Six files from <a href="{SOURCE_LINKS['squash_corpus']}">Evan Nemerson's Squash corpus</a>
  (2015–2016): a PDF document, Bootstrap 3.3.6 CSS, the EFF homepage snapshot, jQuery 2.1.4,
  a 1 MiB random-bytes file, and <code>zlib.wasm</code>.</p>

  <h3>Modern</h3>
  <p>Generated locally from a fixed PRNG seed, plus fetched files:
  <a href="{SOURCE_LINKS['react']}">React 18.3.1</a> production bundle
  (<a href="{SOURCE_LINKS['mit']}">MIT</a>),
  and the <a href="{SOURCE_LINKS['inter']}">Inter typeface</a> Regular
  (<a href="https://scripts.sil.org/OFL">SIL OFL</a>). Synthetic content is
  <a href="{SOURCE_LINKS['cc0']}">CC0</a>.</p>

  <h3>Pathological</h3>
  <p>Generated locally from a fixed seed. Edge cases targeting codec internals,
  window boundaries, entropy extremes, and near-duplicates.
  All <a href="{SOURCE_LINKS['cc0']}">CC0</a>.</p>

  <h3>Negative</h3>
  <p>Derived from good fixtures by deterministic mutation. <strong>Intentionally malformed</strong> —
  see the <a href="#negative">negative section</a> below.</p>
</section>

<section id="algorithms">
  <h2><span class="dot" style="background:#8b5cf6"></span> Algorithms &amp; levels</h2>
  <p>Each raw input is compressed by every algorithm at several levels per codec.</p>
  <div class="grid-3">{codec_cards}</div>

  <h3>ZIP containers with internal-codec variants</h3>
  <div class="grid-3">{zip_cards}</div>
</section>

<section id="bundles-info">
  <h2><span class="dot" style="background:#14b8a6"></span> Bundle formats</h2>
  <div class="grid-3">{bundle_cards}</div>
</section>

<section id="tiers">
  <h2><span class="dot" style="background:#f59e0b"></span> Tiers</h2>
  <p>Every artifact carries a <code>tier</code> field in the manifest.</p>
  <div class="grid-3">{tier_cards}</div>
</section>

<section id="individual">
  <h2><span class="dot" style="background:#64748b"></span> Individual compressions</h2>
  <p>Each raw input compressed by each codec at multiple levels.</p>
  {indiv_collapsibles}
</section>

<section id="bundles">
  <h2><span class="dot" style="background:#14b8a6"></span> Bundles</h2>
  <p>Combined archives per set and across all sets (<em>combined</em>).</p>
  {bundle_collapsibles}
</section>

<section id="dict">
  <h2><span class="dot" style="background:#8b5cf6"></span> Zstd dictionaries</h2>
  <p>A trained zstd dictionary derived from NDJSON samples.</p>
  {_render_files_table(by_cat['dict'], bucket, prefix)}
</section>

<section id="negative">
  <h2><span class="dot" style="background:#ef4444"></span> Negative fixtures</h2>
  <div class="warn-banner">
    <strong>Warning.</strong> Files under <code>negative/</code> are intentionally malformed.
    Apply expansion-size and time caps before feeding them to a decoder.
  </div>
  <ul>{cve_items}</ul>
  {neg_collapsibles}
</section>

<section id="index">
  <h2><span class="dot" style="background:#64748b"></span> Index files</h2>
  <ul>
    <li><a href="{base}/index.txt"><code>index.txt</code></a> — TSV</li>
    <li><a href="{base}/manifest.json"><code>manifest.json</code></a> — JSON v2</li>
    <li><a href="{base}/CHECKSUMS.sha256"><code>CHECKSUMS.sha256</code></a> — GNU sha256sum format</li>
    <li><a href="{base}/expected-ratio.json"><code>expected-ratio.json</code></a> — compressed sizes per (input, codec, level)</li>
    <li><a href="{base}/listing.html"><code>listing.html</code></a> — plain HTML directory listing</li>
  </ul>
</section>

</main>

<footer>
  Built {html.escape(built_at)} • {total_count} artifacts, {_human_size(total_size)} total
  • <a href="{base}/versions.txt">tool versions</a>
  • <a href="{SOURCE_LINKS['jack']}">jackdanger.com</a>
</footer>

</body>
</html>
"""


def _cve_desc(cve: str) -> str:
    descs = {
        "CVE-2022-4899":  "zstd out-of-bounds read on crafted dictionary",
        "CVE-2018-25032": "zlib memory corruption on specific deflate input",
        "CVE-2020-8927":  "brotli buffer overflow on crafted ring-buffer size",
        "CVE-2019-12900": "bzip2 OOB write on crafted N_SELECTORS",
        "CVE-2022-37434": "zlib heap buffer overflow on gzip header with FHCRC flag",
    }
    return descs.get(cve, "")


def render_listing(manifest: dict) -> str:
    """Plain HTML: one anchor per line, easy to pipe through wget/grep."""
    bucket = manifest["bucket"]
    prefix = manifest["prefix"]
    lines = [
        f'<a href="{html.escape(_artifact_url(bucket, prefix, r["path"]))}">{html.escape(r["path"])}</a>'
        for r in sorted(manifest["artifacts"], key=lambda r: r["path"])
    ]
    return (
        "<!doctype html>\n"
        '<html><head><meta charset="utf-8"><title>Squishy — listing</title></head>\n'
        '<body><pre>\n'
        + "\n".join(lines)
        + "\n</pre></body></html>\n"
    )


# ── main entry point ──────────────────────────────────────────────────────────


def run(cfg: BuildConfig) -> int:
    """Generate index.html and listing.html."""
    meta = cfg.meta_dir
    manifest_path = meta / "manifest.json"

    if not manifest_path.exists():
        print(f"html: {manifest_path} not found — run 'build manifest' first", file=sys.stderr)
        return 1

    with manifest_path.open() as f:
        manifest = json.load(f)

    versions = ""
    versions_path = meta / "versions.txt"
    if versions_path.exists():
        versions = versions_path.read_text()

    write_text_atomic(meta / "index.html", render_index(manifest, versions))
    write_text_atomic(meta / "listing.html", render_listing(manifest))

    print(f"html: wrote index.html and listing.html to {meta}", file=sys.stderr)
    return 0
