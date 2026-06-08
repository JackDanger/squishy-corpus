# Squishy 1.0 — Readiness Plan

What must be true before `Squishy-2026` v1.0.0 is **bulletproof**: the right
data, verified four independent ways, distributed correctly, and permanent
enough to cite for 20 years.

> v1.0.0 is **frozen forever**. Once tagged + DOI'd, the bytes never change.
> Everything below is the gate to earning that freeze. Until then, anything
> published (currently under `s3://squishy-corpus/v1.0/`) is **DRAFT** and must
> not be advertised as citable.

Status legend: `[ ]` todo · `[~]` partial/exists-verify · `[x]` done.
Companion: `plans/squishy-score.md` (the score definition).

---

## Multi-angle review → build-up plan (2026-05-29, DECISION: path B)

Five Opus reviewers (compression researcher · perf/implementer · preservation
archivist · IP lawyer · newcomer) reviewed the repo. Consensus: strong foundation,
**not freeze-ready**; the through-line is *the spec/docs have outrun what's built*.
**Decision: build up the full size-spanning corpus and fix all findings** (not the
"freeze the 15 now" path). Do it right and thoroughly. Tracked checklist:

**P0 — freeze blockers**
- [x] **bpb mislabel — FIXED.** Deleted the `8÷geomean` field everywhere; the Score
  is a dimensionless `×` shown adjacent to a true byte-weighted `corpus_bpb`
  (8·out/in) + byte totals, across runner/JSON/README/RULES/cube; regression test
  added (corpus_bpb ≠ 8/score on unequal sizes). *(researcher)*
- [x] **Real edition manifest — DONE.** `scripts/build-edition-manifest.py` →
  `build/meta/edition.json` (per-file name/key/HTTPS-URL/sha256/size/kind/category/
  tier, derived from CORE+CHECKSUMS+LICENSE-MANIFEST+scale-properties). Published;
  `tests/test_edition_manifest.py` CI-asserts it matches CORE, all addressable +
  pinned, no retired path. README/RULES/spec now point to it. *(implementer, archivist)*
- [~] **Assemble + checksum the size-spanning corpus** — largely DONE (2026-05-29).
  Large rungs acquired, whole-file measured, hashed, in `LICENSE-MANIFEST` +
  `scale-properties`: `csv` (1.33 GB + 4.07 GB multi-year), `monorepo` (LLVM full
  1.77 GB), `archive` (NEW kind — clang 16/17/18/19 trees concatenated, 1.50 GB,
  fills the high-coverage/long-distance gap an advisor flagged), `genome` (1.07 GB),
  `text`/enwik9 (1.0 GB), `log` (NASA Jul+Aug 0.37 GB), `parquet` (US BTS airline,
  building). **`parquet` swapped NYC-TLC → US DOT BTS airline on-time** (PD US-gov;
  NYC-TLC was license-encumbered open data + EU database-right flag) — BOTH the
  scored core member and the large rung. `json`/`sqlite` stay single-size (regions
  covered by `log`/`csv`; size axis carried by the rungs above) — advisor-endorsed.
  Coverage verified spanning the volume via `scripts/coverage-summary.py`.
  Incompressible kinds (photo/movie/weights) stay single-size. *(archivist, researcher)*
- [ ] **Zenodo deposit reconcile** — add a `license` field; include `LICENSES/`
  (verbatim **MPL-2.0.txt**, Hugo component named + source pinned); decide scale-tier
  inclusion; enumerate **every** shipped license in NOTICE + README (currently omit
  CC-BY-SA, MPL, NYC-TLC). *(lawyer, archivist)*

**P1**
- [ ] **Deterministic tarball** (`--sort=name --owner=0 --group=0 --numeric-owner
  --mtime=@0`) — current tar bakes uid/gid/mtime → SHA not reproducible. *(archivist)*
- [ ] **Pin derived-file recipes as code** (`scripts/build-corpus/<name>.sh`, tool
  versions pinned, CI-assert SHA==CHECKSUMS) + pin mutable sources (NOAA `by_year`
  overwritten yearly; HF `resolve/main` moving ref) to immutable snapshots. *(archivist)*
- [ ] **Re-measure scale files whole-file** (replace the 384 MB-head numbers in
  `scale-properties.json`). *(implementer, archivist)*
- [ ] **Per-file byte-weighted bpb table + enwik9 anchor** for Silesia/literature
  comparability. *(researcher)*
- [ ] **`squishy perf` harness** — machine-readable per-file comp/decomp time +
  peak RSS; off the citable score. *(implementer)*
- [x] **zpaq headline portability** — DONE 2026-06-08: removed from the reproducible
  panel; `lrzip` is the candidate reproducible high-ratio re-add (see P1 below).
- [ ] **Held-out shadow file per category** for "verified" board status (catches
  in-edition overfit). *(researcher)*
- [ ] **Kind-continuity:** large prose rung = same-source PD (more Gutenberg), not
  enwik9; enwik8 ships as a labeled CC-BY-SA benchmark point; weigh the NYC-TLC
  full-year parquet (EU database-right) vs the 18 MB month. *(researcher, lawyer)*

**P2 — hygiene**
- [ ] delete `scripts/score.py` (untangle from Makefile) + the duplicate `missing =`
  line in `squishy.py`; README install/clone instructions (hero isn't on PATH);
  `×`/`x` + 4.4/4.44 consistency; soften "authoritative" pre-1.0; `bench`-vs-`calculate`
  table; replace the coverage-map "(soon)" dead link; NYC-TLC database-right note.

---

## ⚠ Score redefined 2026-05-29 — this REOPENS the freeze

The owner clarified the model (and two Opus advisors specced it): the Squishy
Score is a **whole-corpus, periodically-recomputed** number — NOT a CI gate — so
**size is now part of the score** and the corpus spans kinds × sizes (tens of GB
is fine). The old "scale tier is out of the score" assumption is overridden.
`squishy-score.md`, `RULES.md`, and `README.md` are rewritten to this. New work
the freeze now depends on:

- [x] **Plain geomean over all files** (one vote per file) implemented in
  `scripts/squishy.py` + `squishy-calculate` — no category/kind/size weighting and no
  compressibility threshold (the nested geomean and the K-plane were retired
  2026-06-07; see `plans/score-weighting-critique-and-proposal.md`). Board re-aggregated.
- [ ] **Partial run → no headline:** a run over fewer than all files prints
  per-file ratios + an explicit "NOT a Squishy Score" line; only the complete
  edition prints the headline. (One corpus, one number — no named subset.)
- [ ] **Acquire the load-bearing large rungs** for the kinds whose redundancy
  grows with length (csv ✓, genome ✓, parquet ✓, plus **log** and **prose** to do).
  Incompressible kinds (`photo`, `movie`, `weights`) stay single-size.
- [ ] **P0 — CSV independence (decided 2026-06-08; needs acquire+measure+SHA).** The
  three current CSVs are all NOAA GHCN weather from the same source — the small `csv`
  is a prefix of the 1.3 GB rung — which violates the no-shared-lineage rule AND
  over-weights one schema (csv = 3 of 24 votes). **Keep one** NOAA GHCN CSV (the large
  rung: numeric/narrow/integer weather). **Replace the other two** with structurally
  different real CSVs: (1) a **wide text/categorical** export (NYC 311 or Chicago
  crimes open data, PD under their open-data terms — high-cardinality strings); (2) a
  **floating-point time-series** (exchange OHLCV or scientific-sensor series under a
  permissive/PD license — dense decimal floats). Acquire, verify license, measure
  intrinsic axes, SHA-pin, add to edition.json. Until then edition.json still lists the
  3 NOAA files; the scored set should drop to keep the lineage rule true before freeze.
- [ ] **P0 — Large PROSE rung (decided 2026-06-08; needs acquire+measure+SHA).**
  `enwik9` is XML-wrapped Wikipedia and stays filed under `markup` (Code & Web), so
  Prose currently has **no** large rung despite the corpus table promising one. Add a
  large **public-domain English-prose** rung — a Gutenberg aggregate (e.g. the
  Standardized Project Gutenberg Corpus, or a deterministic concatenation of many PD
  PG books, body-sliced like `dickens`) — giving genuine long-range vocabulary reuse
  at ~1 GB. PD, so redistributable + DOI-freezable. Acquire/measure/SHA/manifest.
- [ ] **Compute the reference board over the complete size-spanning corpus** (panel
  codecs over every scored file) → the Squishy Score + size buckets.
  Today's published board is a **partial run over the small members** only.
- [ ] **Edition manifest pins the exact `(kind, size)` leaf set** (not just
  filenames); stamp full scores with edition + per-file sha + total bytes.
- [ ] **Whole-file measurement for the coverage map** — the current
  `file-properties.py` recurrence scan caps at the first 384 MB; for GB files
  several real p90 match-distances exceed that, so the cube's scale points are
  measured on a non-representative head. Fix with a bounded-memory whole-file scan
  (or label the axis honestly) before those points are presented as canonical.

Reconciliation: the cube/coverage map is **not** gold-plating (an earlier advisor
called it that under the old "website decoration" framing) — it is the corpus's
**diversity rationale**, load-bearing for the "test behavior" job. But the
**frozen DOI should carry the citable corpus + meta, with the website/map as a
living mirror**, not frozen surface area.

---

## Advisor review outcomes (read first)

An Opus advisor reviewed this plan. Headlines:

**FATALS (must fix before freeze):**
- **F1 — canonical input bytes aren't in the release.** **RESOLVED (decision):**
  ship the core bytes **uncompressed**, with `sha256(raw)` per file + a one-shot
  uncompressed core tarball; those exact bytes are the denominator-of-record.
  Rejected the "store gzip + `Content-Encoding: gzip` transparent-decompress"
  option: S3 sends that header un-negotiated, so plain curl/wget/most HTTP
  clients receive gzipped bytes verbatim while browsers decompress → delivery is
  client-dependent and `sha256(raw)` only verifies for some clients. Storage
  saving on the small core is negligible. (If transfer cost ever matters, use
  CloudFront *negotiated* auto-compression over raw objects — not stored
  Content-Encoding.) Still blocks freeze until the raw core is published.
- **F2 — reference-board numbers depend on codec version.** `brotli 1.2.0` vs a
  future `brotli 2.x` gives a different number for the same corpus. **Fixed in
  code:** `squishy-scores.json` now stamps `codec_version` per row and is labeled
  DRAFT/not-citable; the spec must say "reproducible for a *pinned* build."
- **F3 — validate before public.** PII/license/format checks must pass before
  bytes are public. *(Initial pass run: no credential patterns; ratios sane.
  Full per-file + the 4 new files still pending.)*
- **F4 — `v1.0/` must be pristine until the freeze.** **DONE:** in-flight draft
  moved to `s3://squishy-corpus/draft/`; all `v1.0/` versions purged. First write
  to `v1.0/` will be the release.

**Cut as over-engineering:** sealed holdout (implies a 20-yr leaderboard
*service* — keep dated editions instead); S3 Object Lock in compliance mode (a
foot-gun before verification — governance mode at most); CDN/custom-domain as a
*freeze gate* (nice-to-have, not blocking — and not needed given manifest-of-URIs
delivery); per-OS CI matrix (collapse to one golden-vector test).

**Delivery model (owner):** access is via a **published text manifest of HTTPS
URIs**, not a world-listable bucket or CDN. Bucket is public-read on GetObject
only (no listing). This removes the public-listing/CDN concerns; bomb-fixture
isolation stays as cheap hygiene only.

**Restored (was wrongly cut): a Scale tier of large files.** The owner wants
large files (100 MB–1 GB+) spread across the data kinds — real for gzippy
throughput + parallel single-member decode, and a legitimate modern-corpus need.
This is first-class, distinct from the human-scale citable core (which stays
small + downloadable). See Phase 1a.

**Corrected critical path (do in this order):**
1. Lock the 12 → acquire the 4 → recompute the board on *exactly the 12*
   (today's numbers are for the wrong, 30-file set — draft only).
2. Decide & ship the canonical raw bytes-of-record (F1).
3. PII + license + format-band validation on the draft *before* any public byte (F3).
4. Upload to `draft/`; run the 4 verification passes; only on all-green does the
   first write to `v1.0/` happen — immediately followed by the **Zenodo deposit**
   (which IS the permanence + disaster-recovery, not an afterthought).

**Single most important thing:** freeze the canonical *input* bytes and
version-stamp every published number (F1+F2) — without both, no citation
reproduces and the 20-year premise voids.

---

## Phase 0 — Locked decisions (ratified; zero open questions)

These are decided. Listed for the record, not for re-litigation.

- [x] **Prefix:** draft lives at `draft/`; `v1.0/` reserved & pristine for the
  freeze (done — v1.0/ purged of all versions).
- [x] **Named core = 16 files** (advisor-locked) + Squishy-Extended. See
  `squishy-score.md` for the table.
- [x] **Edition & cadence:** `Squishy-2026`, ~4-year refresh.
- [x] **Permanent home:** Zenodo DOI is the canonical citation target (defeats
  link-rot); S3 is a mirror.
- [x] **Holdout: CUT** (a sealed holdout implies running a perpetual service;
  dated editions handle overfitting instead).
- [x] **Object Lock:** governance mode at most, applied AT freeze — not a
  pre-freeze gate (compliance mode would block fixing a discovered problem).
- [x] **CDN/custom domain:** POST-1.0 (not needed given manifest-of-URIs
  delivery; add only if egress bills bite).

---

## Phase 1 — Dataset curation (is this the RIGHT data?)

The core question. Each item has an acceptance criterion, not just a task.

- [ ] **Acquire the 4 missing named-core files**, each a distinct compression
  regime, redistributable, ~10–50 MB:
  - [ ] `monorepo` — modern source slice (LLVM/Apache or Linux/GPL); replaces aging `samba`.
  - [ ] `media` — real pre-compressed JPEG/MP4/PNG (Wikimedia CC0 / Blender CC-BY).
  - [ ] `fastq` — genomics reads (1000 Genomes / SRA, public domain).
  - [ ] `weights` — safetensors/GGUF shard from a permissively-licensed model.
- [ ] **Representativeness review.** Each core file resembles real-world data of
  its kind (not degenerate, not synthetic-looking). Acceptance: a domain-literate
  human signs off per file with a one-line justification.
- [ ] **Distinctness.** No two core files are near-duplicates and each exercises a
  different codec behavior. Acceptance: pairwise cross-compression test (compress
  A with B as dictionary) shows low cross-redundancy; ratios spread across the 5
  categories.
- [ ] **No single file dominates the score** (measure in log/bpb space, not
  absolute "x"). Acceptance: no file's log-ratio contributes more than ~1.5× the
  mean per-file contribution, and no per-file ratio is a >2σ outlier within its
  category. (Absolute "0.15x leave-one-out" was rejected — codec-dependent and
  too loose.)
- [ ] **Size stability.** Ratio has converged at the chosen size. Acceptance:
  ratio at full size vs half size differs < 2% per file.
- [ ] **Format validity AND non-degeneracy.** Every file is genuinely its claimed
  format *and* representative content (not all-nulls, not one repeated record,
  not accidentally re-compressed). Acceptance: (a) opens with the format's own
  tool (parquet reader, FASTQ linter, image decoder, sqlite integrity, json
  parse); (b) **per-file ratio-band assertion** in the manifest — each file
  compresses within a declared band per reference codec (regression test), and
  `media` files assert ratio < 1.15x with xz (proving genuinely pre-compressed,
  not raw mislabeled). *(Initial band check built: only woff2 is correctly
  near-incompressible.)*
- [ ] **Stable identity.** Canonical filename + memorable short name per file;
  unambiguous (no double-extension surprises).

---

## Phase 1a — Large members of the one corpus

There is **one size-spanning corpus, scored whole.** The large files (100 MB–
1 GB+) are members of it — the large rung of the kinds whose redundancy grows with
length — not a separate unscored tier. They count toward the Squishy Score like
every other file (size is part of the score on purpose: window size, long-range
matching, and parallel decode are the point). The parallel-single-member-decode
and pathological fixtures below are the exception: they live in the **Bounds**
panel for behavior testing, never the headline.

- [ ] **Large realistic representatives** per category (big text, big structured/
  log, big columnar, big binary/media) — real or honestly-scaled, licensed.
- [ ] **Parallel single-member decode set** (gzippy's live need): `homogeneous-1GB`
  (few block boundaries — hard to split), `patchwork-1GB` (many boundaries),
  `two-identical-halves-1GB` (giant back-ref across the midpoint split),
  `cross-cut-backref` (matches straddling power-of-two split offsets),
  `max-distance-match-1GB`. Ship raw + single-member `.gz` + a sidecar with block
  count / boundary offsets / max back-ref distance.
- [ ] **Large edge files:** big incompressible, big highly-repetitive.
- [ ] Acceptance: each carries size, sha256, kind, and (for the parallel set) the
  structural sidecar; none enter the headline Squishy Score.

### Tabular/DB scale siblings (advisor, 2026-05-29)

The three citable tabular files (~18/26/48 MB) all fit inside strong codecs'
windows, so they test the *encoding model* but say nothing about window-bounded,
block-split, or page-boundary behavior. Concrete adds (prioritized):

- [ ] **P0 — Large CSV (~4 GB):** full multi-year NOAA GHCN-Daily (same schema as
  the core `csv`, just more years; PD). Crosses every codec's dictionary ceiling →
  the long-range-match / parallel-block job. Single most valuable tabular add.
- [ ] **P1 — Large parquet (~1–2 GB):** all 12 months of NYC-TLC 2024 in one
  multi-**row-group** file (same provenance as core `parquet`). Only thing that
  exercises row-group / data-page boundary behavior; nothing in Squishy does today.
- [ ] **P1 — Tiny CSV fixture (~1–4 KB):** a few hundred NOAA rows — the
  header/framing-overhead regime. Scale-tier fixture only, **not** the Score
  (a 2 KB ratio is container-overhead noise in a geomean).
- [ ] **P2 — Large indexed SQLite (~1–4 GB):** USDA FoodData Central *Branded*
  (actively maintained; SR-Legacy is frozen) → page-size × B-tree locality.
  Partly overlaps the large-CSV long-range test; lowest priority.
- **Reject:** medium (100–300 MB) tabular siblings (dead middle), a second large
  CSV, synthetic tabular, tiny parquet/sqlite fixtures.

Durability notes the swap must honor (apply to all three citable tabular files):
checksummed **mirrored snapshot** (live gov endpoints will move); pin the
**generation recipe** (parquet writer + row-group/page settings + uncompressed
encoding; sqlite `VACUUM` + `page_size`; CSV `\n` + UTF-8), not just bytes.

## Phase 2 — Score methodology (is the MATH right?)

- [~] **Score definition frozen** in `squishy-score.md`: geomean of per-file
  uncompressed/compressed ratio over the named core; headline "x", bpb beside it;
  synthetics excluded → Bounds panel; 5 category sub-scores. Acceptance: spec
  states every edge case (empty file, ratio < 1, missing file → run fails, not
  silently skipped).
- [ ] **Reference implementation tested.** Unit tests for geomean vs hand-computed
  values; a golden-vector test (fixed inputs → fixed score) that CI guards.
- [ ] **Determinism across platforms.** Same score on macOS + Linux (ratios are
  byte-deterministic). Acceptance: CI runs the board on two OSes, identical to 3
  decimals.
- [ ] **Canonical codec panel pinned** (`tools.lock`): exact argv + tool versions
  for every reference codec; the board records versions inline.
- [ ] **Sensitivity documented:** score vs codec level, vs file set, vs size —
  published so reviewers can't ambush it.
- [ ] **Anti-gaming rules file**: one codec / one setting / all files; no
  corpus-as-dictionary; no filename-based model selection. Written and linked
  from the runner output.

---

## Phase 3 — Build reproducibility

- [ ] **Synthetic files regenerate bit-for-bit.** Pin a version-stable PRNG
  (SHAKE-256 counter stream, not bare `random.Random`); publish reference vectors.
  Acceptance: regenerate from seed → identical sha256.
- [~] **Generators pinned & documented** (`lz77_synth`, `markov`, `periodic`,
  `pathological`, `modern`, `logs`). Acceptance: `make` rebuilds every synthetic
  artifact to its published sha256.
- [ ] **Build is hermetic enough.** Record tool versions for every generated
  artifact; a fresh checkout + documented toolchain reproduces the build.

---

## Phase 4 — Upload / distribution (are we uploading it the RIGHT way?)

- [x] Bucket `s3://squishy-corpus` (us-west-2): versioning ON, public-read via
  **policy** (GetObject only, no ACLs), CORS for browser fetches, multipart
  lifecycle, STANDARD storage (multi-AZ durability).
- [~] Full corpus uploading with native SHA256 checksums + `x-amz-meta-sha256` +
  correct content-type + cache-control (immutable for artifacts, short for meta).
- [ ] **Manifest ↔ bucket audit** (automated): every manifest artifact exists in
  the bucket; no orphan objects; counts, sizes, and sha256 metadata all match.
- [ ] **Public-read audit:** sampled (or full) HTTPS GET of objects returns 200 +
  correct content-type + cache-control.
- [ ] **Downloader can verify:** published `CHECKSUMS.sha256` matches; a
  documented one-liner verifies a download.
- [ ] **Hazard handling:** bomb/malformed fixtures carry hazard metadata; the safe
  manifest excludes them; downloaders are warned.
- [ ] **Security audit:** only `GetObject` is public (no list, no put, no config);
  no credentials anywhere in artifacts; consider MFA-delete on versioning.
- [ ] **Disaster recovery:** the frozen 1.0.0 is backed up beyond one bucket —
  Zenodo deposit + a cross-region or Glacier copy. A deleted bucket must not
  destroy the corpus.
- [ ] **Cost:** storage + projected egress estimate; a billing alarm. CDN if egress
  is a concern.

---

## Phase 5 — Permanence & citation (cited for 20 years)

- [ ] **Zenodo deposit + DOI** for the frozen named core (one-shot tarball +
  manifest + checksums + license manifest). The canonical citation target.
- [ ] **`CITATION.cff`** and a "How to cite" snippet.
- [ ] **One-shot core tarball** (`squishy-2026.tar`, like `silesia.tar`) so
  the cited set downloads in one command.
- [ ] **README** (corpus overview, the score, categories, file table, licenses,
  how to run, version policy).
- [ ] **LICENSE-MANIFEST.csv**: per file — source URL, sha256, license, license
  URL, archive.org snapshot, attribution text.

---

## Phase 6 — Tooling & adoption

- [~] **One-command runner** (`scripts/squishy.py`): `board` + live `bench --cmd`.
  Harden for 1.0: fetch the DOI-pinned corpus if absent, hash-verify every file
  before scoring, handle codecs that need file args (not just stdin/stdout),
  print the rules + tool versions, exit non-zero on any missing/altered file.
- [ ] **Package the runner** (pipx/uv tool or a single static script) so
  `squishy bench ./mycodec` works with zero setup.
- [ ] **Leaderboard** seeded with the reference panel (ratio + a speed tier on a
  named reference machine; speed is NOT in the canonical number).
- [ ] **CI** recomputes the board on any change and diffs against golden scores.

---

## Phase 7 — Legal / privacy / ethics

- [ ] **License compliance** per file: GPL attribution/notices (kernel/LLVM),
  CC-BY attribution, CC0 acknowledged. Acceptance: a lawyer-or-equivalent review
  of LICENSE-MANIFEST.
- [ ] **PII / sensitive-data scan**, especially logs/email/structured data — must
  be synthetic or fully anonymized. Acceptance: automated secret/PII scan + human
  review, zero findings.
- [ ] **Responsible distribution** of bomb/malformed fixtures (documented as test
  fixtures; safe manifest is the default download).

---

## Phase 8 — The four independent verification passes (triple/quadruple check)

Run all four, by *different methods*, before the freeze. Each must pass clean.

> These must fail *independently*. Checksum-matching only proves consistency, not
> correctness — a file that was already wrong when its sha256 was taken passes
> every checksum-anchored pass. So at least one pass must be **source-anchored**.

1. **Automated manifest↔bucket audit** — counts, sizes, sha256, content-type,
   public-readability, for every object (Phase 4).
2. **Clean-room re-download + verify** — from a fresh environment with no local
   cache, download the published core, verify every sha256 against
   `CHECKSUMS.sha256`, and recompute the Squishy board from the downloaded bytes;
   must match the published `squishy-scores.json` to 3 decimals (with the pinned
   codec builds).
3. **Source-anchored re-derivation** — for each core file, re-fetch from its
   upstream source URL in `LICENSE-MANIFEST.csv` and confirm the bytes match.
   This is the ONLY pass that catches a file wrong *before* its checksum was
   taken. Plus format re-open + PII scan with *different* tooling than pass 1.
4. **Human eyeball + third-party re-measurement** — a person inspects each named
   core file and its category; the score is reproduced with a **different codec
   build** than `tools.lock` (e.g. lzbench's vendored zstd) so it doesn't share
   F2's version blind spot.

Acceptance to leave this phase: all four green, signed and dated in the release
notes.

---

## Phase 9 — Release

- [ ] Freeze the named core; tag `v1.0.0`; publish to `v1.0/` (immutable,
  Object-Locked); Zenodo DOI minted.
- [ ] CHANGELOG / release notes including the Phase-8 sign-offs.
- [ ] Announce only after Phases 1–8 are green.

---

## Full-setup review — additional work (Opus advisor, 2026-05-29)

NEW items found reviewing the whole setup (README/RULES/CITATION, the runner,
CLI, site/audit/freeze/zenodo scripts, manifest, tests), deduped against Phases
0–9. Single biggest risk once called out — *"one honest, reproducible number" yet the
runner never verifies decompression and fails **open** on missing checksums, while the
flagship board headline (`zpaq`) depends on a 2016 binary nobody can reinstall* — is
now **resolved** (2026-06-08): streaming `--verify` round-trips; checksums fail closed
against `edition.json`; zpaq removed from the reproducible panel.

### P0 — found during the owner's representativeness review (2026-05-29)
- [x] **`freeze.sh` would have frozen 61 GB of retired junk.** It did
  `cp draft/ → v1.0/ --recursive` (1,984 objects); ~98% is retired byte-property-cube
  build output (`individual/`, `bundle/`, `bundles/`, `negative/`, `bench/`). Fixed:
  the freeze now copies an **allowlist of only the v1.0 product** (32 objects: 15
  core + 3 scale weights + LICENSES + provenance + meta) with a dry-run + confirm.
- [x] **Scale weight shipped with no provenance.** The bucket had three weights
  (`135m`, `0.5b`, `1.5b`) but `LICENSE-MANIFEST.csv` listed only two — the
  `qwen2.5-0.5b` file had no manifest row. Added (Qwen2.5-0.5B, Apache-2.0, sha+size);
  the explorer now inventories the full 135M→0.5B→1.5B ladder. Manifest = 18 rows.

### P0 — must fix before freeze
- [x] **`build/meta/NOTICE` mis-attributed `csv`/`sqlite` to NYC TLC** after the
  swap → fixed: split into NOAA (csv, PD-USGov), NYC-TLC (parquet only), USDA
  (sqlite, PD-USGov). (Closed 2026-05-29.)
- [x] **`PRE-FREEZE-VERIFICATION.md` regenerated** against the real 15-file core
  (NOAA/USDA tabular, 2 counsel items, current green checks). (Closed 2026-05-29.)
- [x] **Runner fails *open* on missing checksums** — FIXED 2026-06-08.
  `verify_core_checksums()` now verifies every present core file against the sha256 in
  **`edition.json`** (the authoritative manifest, always committed) plus `CHECKSUMS.sha256`
  if present, and fails **closed**: a present-but-unverifiable file (no published sha)
  is reported, and the CLI/board refuse to score on any failure. The no-op path is gone.
- [x] **The two COUNSEL swap-outs — resolved WITHOUT counsel (2026-06-08):**
  - `parquet` NYC-TLC → already swapped to **US-DOT BTS airline on-time (PD-USGov)**;
    edition.json `parquet` + `bts-ontime` both carry `Public-Domain-USGov`. Closed.
  - `exe` (Hugo) MPL-2.0 component → **cleared by reading the license, not a lawyer.**
    Hugo's own license is Apache-2.0; it bundles some MPL-2.0 Go deps. MPL-2.0 §3.2
    explicitly permits distributing the work **in executable form** under terms of your
    choice provided the MPL-covered *source* remains available (it is, upstream). So
    redistributing the compiled Hugo binary as the `exe` member is compliant. The
    edition.json license is now `"Apache-2.0 (binary bundles MPL-2.0 components; MPL
    §3.2 permits executable redistribution)"`. Keep Hugo.
- [x] **Define rounding/precision in `RULES.md`** — headline `x` to 2 decimals,
  `bpb` to 3, computed from full-precision per-file sizes; ties at that precision
  are ties. (Closed 2026-05-29; also fixed a stale 2/3/3/3/3 → 2/3/3/3/4 balance.)

### P1 — should fix before 1.0
- [~] **Verify decompression (round-trip).** Done for the streaming path:
  `squishy-calculate --verify --decompress "<cmd>"` round-trips every file and
  refuses a score on any mismatch; `RULES.md` now requires lossless round-trip.
  Remaining: add the same `--verify` to `squishy bench` (local path).
- [x] **Pin every panel codec / zpaq portability** — RESOLVED 2026-06-08. `zpaq`
  (hand-carried 2016 v7.15 binary, unreproducible by a third party) **removed from the
  reproducible reference panel** (`PANEL`/`PANEL_ARGV`/`PANEL_TOOL` in `squishy.py`)
  and from the published board JSON. The reference board is now the 6 mainstream,
  installable, version-pinned codecs (gzip/bzip2/zstd×2/xz/brotli). High-ratio
  context-mixing codecs (zpaq/cmix/paq) are submitter-reported on the leaderboard.
  **TODO (re-add a reproducible high-ratio anchor):** evaluate **`lrzip`** (packaged on
  apt/brew, version-pinnable, rzip long-range + lzma/zpaq backend — a good high-ratio
  point especially on the large rungs); acquire/measure it into the board at freeze.
- [ ] **Golden end-to-end board test** — a CI test that recomputes the board from
  `build/raw/corpus/` and diffs `squishy-scores.json` to the locked precision (the
  real-bytes regression guard Phase 2 calls for but no test implements).
- [ ] **Delete dead duplicate implementations** — `scripts/squishy-score.py` and
  `scripts/score.py` carry separate `PANEL`/`CORE` dicts; only `scripts/squishy.py`
  is canonical. Likewise name ONE site generator and remove the others.
- [x] **Leaderboard governance ("Submitting a score")** in `RULES.md`: required
  tuple (codec, version, exact argv, edition) + a published reproduction command +
  maintainer re-run. (Closed 2026-05-29.)
- [x] **Zenodo deposit covers the regeneratable tier** — `zenodo-deposit.py` now
  pins the exact git tag/commit as a `related_identifiers` (isSupplementedBy) +
  notes it in the description, so the DOI references the generators+PRNG that
  reproduce the large/pathological files. (Closed 2026-05-29.)
- [~] **Size-convergence evidence** — `scripts/size-convergence.py` →
  `build/meta/size-convergence.json`. Finding: byte-halving is only valid for
  byte-stream formats; the large ones converge tightly (log 0.3%, exe 1.3%, csv
  2.0%, json 2.6%, genome 2.7%, dickens 3.0%, markup 3.5%), the small 3.6 MB
  `minjs` drifts most (6.7%, expected small-file variance, geomean-diluted).
  STRUCTURED files (parquet/sqlite/monorepo) are reported **n/a** — byte-halving
  cuts mid-structure and is noise. *Follow-up:* a faithful convergence check for
  the structured trio needs a **row/member-boundary subset** (half the rows/
  members), best produced via their pinned generation recipe.

### P2 — nice to have
- [x] **Structured PII pass** — `pii-scan.py` now decodes `sqlite` string cells and
  `parquet` string columns and scans those (skipping numeric columns). Confirms
  `sqlite`'s 715 raw "credit-card-like" hits are numeric false positives (string
  cells clean) and `parquet` is clean. Closes verification pass 3's "different
  tooling". (Closed 2026-05-29.)
- [—] **Integrity UX on the explorer** — DECLINED: conflicts with the owner's
  explicit directive that the explorer "just show the data, not prove our work."
  Integrity instead lives where it belongs — `squishy-calculate` verifies every
  byte against the published SHA-256 automatically, and the manifest/CHECKSUMS
  carry the hashes.
- [~] **`CITATION.cff`** — DOI/identifiers structure + freeze-date TODO wired in
  (concrete values are filled by `zenodo-deposit.py` / the owner at v1.0.0 freeze).
- [ ] **`GOVERNANCE.md`** — who curates `Squishy-2030`, how cross-edition
  overfitting is measured (publish per-codec ratio delta), deprecation policy.
- [ ] **Make derived-file transforms reproducible** — pin the exact recipe for
  `dickens`/`monorepo`/`markup` so the derived bytes are themselves re-derivable
  (supports the "real data" claim under scrutiny).

---

## Retired approaches (do not revisit without new evidence)

- **"Squishy-Core" / any named subset — RETIRED 2026-05-29.** *Decision: No named
  subset. One corpus, one number.* Squishy is a single corpus; the Squishy Score is
  computed over the complete edition and is the only citable number — a periodic,
  algorithm-change-triggered measurement, never a CI gate. There is no
  "Squishy-Core," no "quick set," no `--set core`, and no second number. CI/dev use
  is ad hoc per-file use: pull whatever files stress your code (per-file
  addressability via the published manifest of URIs) and consult the coverage map to
  judge representativeness for your codec; a run over fewer than all files prints
  per-file ratios for regression diffing and does NOT print a Squishy Score headline.
  Large files are simply members of the one corpus, not a separate unscored tier.
  *Rationale:* the score is periodic so a fast subset has no CI job; a small-file
  subset produces a rankable number that can disagree with the full ranking exactly
  where the full corpus is most valuable (size-dependent behavior — cf.
  enwik8/enwik9); representativeness is codec-specific, so one blessed subset implies
  a universality it can't have (the coverage map serves that better); memorability
  lives in the kind list, not a subset.

- **Byte-property coordinate "cube"** (R×D×M, then C×W×K): an attempt to give each
  file a 3D structural coordinate. Retired — over-engineered, axes coupled
  (D ≤ 1−R; C–K correlation 0.71 on naturals), K not scale-invariant, and no
  persona needed it. Superseded by the curated-realistic-set + Squishy Score.
  History in git + memory notes. The `parse_cwk.py` measurement is not wired into
  the product and should not drive 1.0.
