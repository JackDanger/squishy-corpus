# Squishy-2026 — pre-freeze verification report

The agent-runnable verification is complete and green. This is the evidence for
the owner sign-offs (#17) and the freeze trigger (#18). Re-run any command to
reproduce. (Regenerated 2026-05-29 after the NYC-taxi decoupling: `csv`→NOAA,
`sqlite`→USDA.)

## Agent checks — all green

| Check | Command | Result |
|-------|---------|--------|
| Test suite | `uv run pytest -q` | **335 passed**, 0 failed (2 slow deselected) |
| Score-runner tests | `uv run pytest tests/test_squishy_score.py` | 11 passed (golden vector, fail-on-missing, empty-file regression, locked-count=15) |
| PRNG reference vectors | `uv run pytest tests/test_prng.py` | 7 passed (version-stable byte stream pinned) |
| Core validation | `uv run python scripts/validate-core.py` | **15/15, 0 failures** (format + non-degeneracy + bands) |
| PII / secret scan | `uv run python scripts/pii-scan.py` | no credential findings; `csv` clean, `sqlite` only numeric false-positives |
| Distribution audit | `uv run python scripts/audit-distribution.py` | **17/17 objects** size + sha256 (x-amz-meta) + public ✓ |
| Provenance | `build/meta/LICENSE-MANIFEST.csv` | 15/15 core rows (source, license, sha256) |
| Canonical bytes (F1) | `draft/squishy-2026.tar` (419 MB, 15/15) + `CHECKSUMS.sha256` | published, audited |
| Round-trip (losslessness) | `squishy bench --cmd "<c>" --verify --decompress "<d>"` / `squishy-calculate --verify` | available per codec; refuses a score on mismatch |
| Verification pass-4 (cross-impl) | `uv run python scripts/verify-pass4.py` | **PASS** — stdlib zlib/bz2/lzma reproduce the gzip/bzip2/xz board within **0.05%** (tol 2%) |
| Size convergence | `uv run python scripts/size-convergence.py` | byte-stream files converge (large ≤3.5%); structured n/a (need row-boundary subset) |

## The 15 core files (kind, license, source)

| name | kind | license | source |
|------|------|---------|--------|
| dickens | English prose | Public-Domain | Project Gutenberg (Dickens d.1870), PG boilerplate stripped |
| aozora | Japanese prose | Public-Domain | Aozora Bunko (Natsume Sōseki, 1867–1916) |
| monorepo | C++ source (tar) | Apache-2.0 w/ LLVM exc. | LLVM clang 19.1.0 `lib/` |
| minjs | minified JS | MIT | Plotly.js v2.27.0 |
| markup | XML (tar) | Freely-distributable | Jon Bosak's Shakespeare (shaks200) |
| json | GeoJSON | Public-Domain (USGS) | USGS earthquakes M4.5+, 2010–2024 |
| log | server log | Public-Domain | NASA-HTTP, Jul 1995 (LBL ITA) |
| genome | FASTQ reads | INSDC free | ENA DRR002013 (E. coli) |
| csv | tabular weather | **Public-Domain (NOAA/USGov)** | **NOAA GHCN-Daily 2024** |
| parquet | columnar (uncompressed) | NYC-TLC public (soft) | NYC TLC Yellow-Taxi 2024-01, re-encoded |
| sqlite | relational DB (17 tables) | **Public-Domain (USDA/USGov)** | **USDA FoodData Central SR Legacy** |
| exe | ELF binary | Apache-2.0 (+MPL module) | Hugo v0.162.1 |
| photo | JPEG | Public-Domain | NASA "Blue Marble" (Apollo 17) |
| movie | H.264 MP4 | CC-BY-3.0 | Big Buck Bunny (Blender) |
| weights | safetensors | Apache-2.0 | all-MiniLM-L6-v2 |

Near-incompressible budget = 3 (photo, movie, weights). Category balance 2/3/3/3/4.

## Owner sign-offs required before freeze (#17)

These need human judgment — the agent cannot do them:

1. ~~**Representativeness**~~ — **ACCEPTED (owner, 2026-05-29):** "I reviewed it
   and I accept it all." The explorer (now the **primary page**, `draft/index.html`,
   with a 3D cube + per-dataset previews) covers the full product — 15 core + the
   3-rung scale weights ladder.
2. ~~**Legal counsel — 2 items**~~ — **DECIDED 2026-05-29** (owner delegated the
   call; Opus-advisor-backed). **KEEP both, documented:**
   - `parquet` (NYC-TLC) — **KEEP.** Trip records are uncopyrightable facts
     (*Feist v. Rural Telephone*); NYC asserts no copyright; browsewrap ToS covers
     access, not republication of public facts; PII-clean. Discharged by a
     no-rights-asserted provenance note in `NOTICE`.
   - `exe` (Hugo, Apache-2.0 + MPL-2.0 `golang-lru`) — **KEEP.** MPL §3 satisfied
     by shipping `LICENSES/tool.bin.THIRD-PARTY.txt` (MPL text + pinned source
     pointer). A discharged compliance checkbox, not a blocker.
   (`csv`/`sqlite` are unambiguous US-Gov public domain — never needed counsel.)
3. ~~**PII review of `log`**~~ — **SIGNED OFF (owner, 2026-05-29).** It's the
   already-public NASA-HTTP dataset: client *host identifiers* only (17,237 hosts;
   no usernames, 0 auth/password tokens). Acceptable for permanent release.
4. ~~**Verification pass-4**~~ — **DONE (agent).** `scripts/verify-pass4.py`
   re-scored with independent stdlib implementations (zlib/bz2/lzma) and matched
   the pinned-CLI gzip/bzip2/xz board within 0.05%. Artifact:
   `build/meta/verification-pass4.json`. (No human action needed.)

## Freeze (#18) — owner, irreversible

1. Copy `s3://squishy-corpus/draft/` → the pristine `s3://squishy-corpus/v1.0/`.
2. Tag `v1.0.0`; optionally S3 Object-Lock (governance) the released objects.
3. Mint the **Zenodo DOI** (rotated token, env var only) over the core tarball +
   manifest + checksums + LICENSE-MANIFEST + NOTICE; record the git tag/commit so
   the regeneratable tier is covered. Update CITATION.cff (real date + DOI).
4. Cross-region / Glacier backup copy.
5. Recompute + publish the final board; regenerate the explorer; announce.

## Notes for the record

- Reference-board numbers are **draft, partial** (small members only; large rungs
  pending — not yet a Squishy Score), pinned `tools.lock` builds. **The figures once
  quoted here predate the 2026-06-07 switch to the plain one-vote-per-file geomean
  (and the older nested weighting) — superseded; see `build/meta/squishy-scores.json`
  for the current 6-codec reproducible board** (xz 4.37× · brotli 4.34× · zstd-22
  4.20× · zstd-19 4.15× · bzip2 3.98× · gzip 3.23×; zpaq removed — see below).
- The NYC-taxi decoupling (2026-05-29) removed a hidden correlation: `csv`,
  `parquet`, `sqlite` previously shared the *same taxi rows*; they are now three
  independent datasets (weather / taxi / nutrition) with distinct compressibility.
- `parquet` ships uncompressed-encoding (a prior catch: it had shipped internally
  ZSTD-compressed at 1.02× external; uncompressed → a genuine 1.7–2.2× columnar
  regime).
- `zpaq` (2016 v7.15 binary) was **removed** from the reproducible reference panel
  (2026-06-08) — nobody could reinstall the exact build. The board is now the six
  mainstream, version-pinned, installable codecs. High-ratio context-mixing codecs are
  submitter-reported; a packaged `lrzip` is the candidate reproducible re-add.
