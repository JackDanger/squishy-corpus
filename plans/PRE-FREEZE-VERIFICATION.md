# Squishy-2026 — pre-freeze verification report

> **Regenerated 2026-06-24** against the current edition: the **19-member named
> core + scale tier = 30 files** (26 scored cells + 4 diagnostics), with the
> scored `weights` cell now **SmolLM2-135M-Instruct** (the MiniLM→SmolLM2 swap of
> 2026-06-22) and `parquet` now **US-DOT BTS On-Time** (not NYC-TLC). Every number
> below was re-run today against `build/meta/*` and the live distribution. Re-run
> any command to reproduce.

The agent-runnable verification is **complete and green**. This is the evidence
for the owner sign-offs (#17) and the freeze trigger (#18). The corpus payload and
the site/metadata are **published to the live `draft/` distribution and audited**;
the only steps left are the OWNER-gated, irreversible freeze + DOI (§ Freeze).

## Agent checks — all green (2026-06-24)

| Check | Command | Result |
|-------|---------|--------|
| Clean-room reproduction | `make all` (`scripts/run-all.sh`) | **PASS** — derived files rebuild byte-identical; scored-set fingerprint matches `baseline.json`; 6 panel codecs lossless |
| Test suite | `uv run pytest -q -m "not slow"` | **51 passed**, 0 failed (1 slow deselected) |
| Score-runner tests | `tests/test_squishy_score.py` | 16 passed (golden vector, fail-on-missing, empty-file regression, locked roster) |
| Core validation | `scripts/validate-core.py` | **19/19, 0 failures** (format + non-degeneracy + bands) |
| PII / secret scan | `scripts/pii-scan.py` | **no credential findings**; `sqlite` only numeric false-positives; `weights` clean |
| Distribution audit | `scripts/audit-distribution.py` | **31/31 objects** at `https://squishy.jackdanger.com` — public + Content-Length + `x-amz-meta-sha256` ✓; CHECKSUMS matches local |
| Verification pass-4 (cross-impl) | `scripts/verify-pass4.py` | **PASS** — stdlib zlib/bz2/lzma reproduce the gzip/bzip2/xz board within **0.63%** (tol 2%) |
| Provenance | `build/meta/LICENSE-MANIFEST.csv` | 30/30 rows (source_url, license, sha256, attribution) |
| Canonical bytes | `edition.json` manifest + `CHECKSUMS.sha256` (per-file; no combined tarball) | published, audited |
| Round-trip (losslessness) | `squishy-calculate --verify` | refuses a score on mismatch; reference run verified every file |

## The headline (citable) Squishy Score

Complete-edition, round-trip-verified, plain one-vote-per-file geomean over the
whole corpus (`build/meta/squishy-score-complete.json`):

| codec | Squishy Score × | corpus bpb |
|-------|----------------:|-----------:|
| xz -9        | **5.10** | 1.086 |
| brotli -11   | **5.03** | 1.107 |
| zstd -22     | **4.94** | 1.108 |
| zstd -19     | **4.74** | 1.189 |
| bzip2 -9     | **4.45** | 1.345 |
| gzip -9      | **3.63** | 1.594 |

Scored set: **26 cells**, 11.17 GB scored (17.30 GB distributed incl. diagnostics).
Scored-set fingerprint `6ed7f40361f4…` (the golden anchor in `baseline.json`).
zstd -19 is the round-trip-verified reference pass (`complete=True, verified=True`).

## The 19 core files (kind, license, source)

| name | kind | license | source |
|------|------|---------|--------|
| dickens | English prose | Public-Domain | Project Gutenberg (Dickens, d.1870), PG boilerplate stripped |
| aozora | Japanese prose | Public-Domain | Aozora Bunko (Natsume Sōseki, 1867–1916) |
| monorepo | C++ source (tar) | Apache-2.0 w/ LLVM exc. | LLVM clang 19.1.0 `lib/` |
| minjs | minified JS | MIT | Plotly.js v2.27.0 |
| markup | XML (tar) | Freely-distributable | Jon Bosak's Shakespeare (shaks200) |
| json | GeoJSON (ndjson) | Public-Domain (USGS) | USGS earthquakes M4.5+ |
| log | server access log | Public-Domain | NASA-HTTP, Jul 1995 (LBL ITA) |
| genome | FASTQ reads | INSDC free | ENA DRR002013 (E. coli), head-slice |
| csv | tabular weather | **Public-Domain (NOAA/USGov)** | NOAA GHCN-Daily 2024 |
| parquet | columnar (uncompressed) | **Public-Domain (US-DOT/USGov)** | **US-DOT BTS On-Time Reporting, 2024-01** |
| sqlite | relational DB | **Public-Domain (USDA/USGov)** | USDA FoodData Central SR Legacy |
| exe (tool.bin) | ELF binary | Apache-2.0 (+MPL module) | Hugo v0.162.1 |
| symbols | DWARF debug companion | MIT | Lua 5.4.8, `-g` build |
| engine | WebAssembly | Public-Domain | SQLite WASM (sqlite-wasm-3530200) |
| winexe | PE (Windows exe) | MIT OR Apache-2.0 | fd v10.4.2 (x86_64-pc-windows-msvc) |
| armexe | ELF (ARM64) | MIT | hyperfine v1.20.0 (aarch64-unknown-linux-gnu) |
| photo | JPEG | Public-Domain | NASA "Blue Marble" (Apollo 17), via Wikimedia |
| movie | H.264 MP4 | CC-BY-3.0 | Big Buck Bunny (Blender) |
| weights | safetensors | Apache-2.0 | **HuggingFaceTB/SmolLM2-135M-Instruct** (pinned commit `12fd25f`) |

Near-incompressible budget in the core = 3 (photo, movie, weights). The scale tier
adds 11 large members (incl. the Qwen2.5-0.5B / 1.5B weights ladder, `big-buck-bunny`
1080p, `enwik9`, the NOAA/clang/llvm/BTS multi-GB rungs).

## Owner sign-offs required before freeze (#17)

> ⚠️ **Re-confirm against the CURRENT 30-file edition.** The sign-offs below were
> recorded 2026-05-29 over the **then-15-member** core. The roster has since
> changed: **+4 Binary & Media** members (`symbols`, `engine/wasm`, `winexe`,
> `armexe`); **`parquet` re-based NYC-TLC → US-DOT BTS On-Time**; **`weights`
> swapped MiniLM → SmolLM2-135M**. The agent cannot re-issue these human
> judgments — the owner must re-affirm 1–3 over the current edition before the
> freeze. (The technical sign-off, #4, has been re-run today and is green.)

1. **Representativeness** — *prior:* **ACCEPTED (owner, 2026-05-29):** "I reviewed
   it and I accept it all." *Now:* re-confirm over the 19-core + scale-ladder
   edition (the live `draft/index.html` explorer renders all 28 cube points).
2. **License review** — *prior (2026-05-29, owner, Opus-advisor-backed):*
   - `parquet` (then NYC-TLC) — KEEP; trip records uncopyrightable (*Feist*). **The
     dataset is now US-DOT BTS On-Time** — also uncopyrightable US-Gov public-domain
     facts; the rationale strengthens, but re-affirm against BTS specifically.
   - `exe` (Hugo, Apache-2.0 + MPL-2.0 `golang-lru`) — KEEP; MPL §3 satisfied by
     shipping `LICENSES/tool.bin.THIRD-PARTY.txt`. (Unchanged.)
   - New binaries to confirm: `winexe` (fd, MIT/Apache-2.0), `armexe` (hyperfine,
     MIT), `engine` (SQLite WASM, public-domain), `symbols` (Lua, MIT), `weights`
     (SmolLM2-135M, Apache-2.0) — all permissive; LICENSE texts shipped under
     `LICENSES/`.
   (`csv`/`sqlite` are unambiguous US-Gov public domain — never in question.)
3. **PII review of `log`** — **SIGNED OFF (owner, 2026-05-29).** Already-public
   NASA-HTTP: client host identifiers only; no usernames, 0 auth tokens. Unchanged
   member; the 2026-06-24 PII scan re-confirms no credential findings corpus-wide.
4. **Verification pass-4** — **DONE (agent, re-run 2026-06-24).** Independent stdlib
   zlib/bz2/lzma reproduce the pinned-CLI gzip/bzip2/xz board within **0.63%**
   (tol 2%). Artifact: `build/meta/verification-pass4.json` (`all_agree: true`).

## Freeze — owner, irreversible (OWNER/CRED-GATED) — NOT YET RUN

The live `draft/` distribution is current and audited (31/31, 0 failures); the
permanent `s3://squishy-corpus/2026/` prefix is **empty (pristine)** — the freeze
has not been triggered. To freeze:

1. `make freeze` (AWS credentials + `ZENODO_TOKEN` in the environment; bails if either
   is missing) runs the whole sequence: preflight → `scripts/freeze.sh` (re-audits
   `draft/`, asserts `2026/` empty, then server-side-copies the curated allowlist into
   the immutable `2026/` prefix, `cache-control: …immutable`; interactive `--confirm` +
   a `y/N` on the dry-run set) → `capture-frozen-versions.py` (pins exact object
   versions) → `zenodo-deposit.py --publish` (mints the DOI from the frozen `2026/`
   bytes).
2. Git tag `Squishy-2026`; push the tag.
3. Paste the minted DOI into the two DOI lines in `CITATION.cff` and the website's
   "How to cite" section; set `date-released` to the freeze date; redeploy the live site.
4. Cross-region / Glacier backup copy of `2026/`.
5. Announce.

## Notes for the record

- The board is the six mainstream, version-pinned, installable codecs (gzip, bzip2,
  zstd-19, zstd-22, xz, brotli). `zpaq` was removed 2026-06-08 (unreproducible 2016
  build). High-ratio context-mixing codecs are submitter-reported.
- The score is the **plain one-vote-per-file geomean** over the whole corpus
  (locked 2026-06-07; the older nested/byte-weighted figures are superseded), with a
  byte-weighted `corpus_bpb` companion.
- `parquet` ships uncompressed-encoding (a prior catch: it had shipped internally
  ZSTD-compressed at ~1.02× external; uncompressed → a genuine columnar regime, here
  1.48× gzip / ~1.7× on the scale rung).
- The MiniLM→SmolLM2 swap (2026-06-22) is rank-stable (zstd-19 4.71×→4.74×);
  `corpus_bpb` moved 1.119→1.189 (SmolLM2 entropy 6.24 < MiniLM 7.36). The weights
  URL is pinned to an immutable HF commit (`resolve/12fd25f…`) for re-fetch
  reproducibility. See `plans/weights-cell-swap-proposal.md`.
</content>
</invoke>
