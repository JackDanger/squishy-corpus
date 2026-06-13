# Squishy Score — the rules

A Squishy Score is only comparable if everyone computes it the same way. To
report a Squishy Score for a codec, you MUST follow these rules. The runner
(`squishy-calculate`, implemented in `scripts/squishy.py`) enforces what it can;
the rest is on your honor and is checkable by anyone who re-runs you.

## The canonical run

1. **One codec, one setting, all files.** Run the *same* codec at the *same*
   single setting over *every* file in the named edition's corpus. No per-file
   tuning, no per-file level/flag selection, no switching algorithms by file.
2. **No corpus bytes in the codec.** You may not ship any byte of the Squishy
   corpus (or a hash-equivalent, or a dictionary trained on it) inside the codec.
   Pre-training a dictionary *on the corpus* is cheating; a general-purpose
   built-in dictionary is fine.
3. **No filename / extension / magic-byte model selection.** The codec must not
   branch on which corpus file it is. It sees bytes, not names.
4. **Score = the geometric mean of per-file (uncompressed ÷ compressed) ratio over
   the whole corpus — one vote per file.** No category weights, no kind weights, no
   size weights, no tuning constants, and no threshold deciding which files count.
   Every file is averaged once. The Squishy Score is reported as **"×"** and is a
   **dimensionless quality index, NOT a bit rate — do not derive bpb from it.**
   Always shown beside it is the **corpus bpb** = `8 · total_out_bytes /
   total_in_bytes` (byte-weighted, the operational bits-per-byte the literature
   uses). The two are deliberate complements: the Score weights every file equally
   regardless of size (anti-gaming); corpus bpb is the size-weighted physical number.
   Bare "bpb" never appears as a standalone label. Plus a **by-category** diagnostic
   sub-table (the geomean of each category's files) and a **by-size-bucket** sub-table
   (`≤100 MB` vs `≥1 GB`, landing once the large rungs are acquired) — both are
   re-slices of the same per-file ratios, never a weight in the headline.
   - **One corpus, one number.** The Squishy Score is computed over the *complete*
     edition and is the only citable number. There is no subset and no second
     score; a run over fewer than all files prints per-file ratios for your own
     regression use, never a headline (see "Partial runs" below).
   - **Size is part of the score on purpose:** a codec can win at 40 MB and lose at
     1 GB (window size, long-range matching, parallel decode), and the score must
     show that. The no-corpus-bytes rule (#2) applies *especially* to the large
     files — they're the lucrative dictionary target. A general-purpose
     `--long`/large-window mode is fine.
   - The geometric mean **caps any one file's leverage to a single vote**, so no
     single giant file can be overfit to move the headline, and a codec that overfits
     one kind of data pays for it on every other file.
   - **The headline is the ratio over a realistic mix, including the
     near-incompressible files** (`photo`, `movie`, `weights`). It reports what a
     codec achieves on real data as it comes — not the best ratio reachable on
     only-compressible data. Those files score ~1.0×, lowering the headline by nearly
     the same factor for every codec, so they barely move the ranking — a codec can
     earn a sliver by genuinely squeezing them, but can't win on them.
   - **One vote per file means file *count* is the only weight** (a kind sampled at
     two sizes votes twice). The formula has no knob; balance is enforced by
     *curation*, and curation is itself constituted and tested: the scored roster is
     declared cell-by-cell in [`build/meta/schema.json`](build/meta/schema.json), where
     each cell has a **role** — `kind` (the human-scale backbone), `length` (a
     deliberate larger re-sample of a kind, probing long-range matching), or
     `incompressible`. **Independence rule:** no two `kind` cells may share a source or
     lineage, so correlated near-duplicates can't stack votes. A `length` cell is
     *exempt* — it re-samples its kind's data at a larger size on purpose — but is
     capped at **one per kind** (≤2 votes per kind) and bound to the cell it scales.
     The near-incompressible budget (`photo`, `movie`, `weights`) and the per-category
     vote count are likewise declared budgets in the schema, and `tests/test_schema.py`
     fails the build if the live roster drifts from them.
5. **The number is versioned, and the tool is provenanced like the data.** A Squishy
   Score is a property of *(codec, setting, codec-version, codec-argv, corpus-edition)*.
   Every published score records the **exact tool that produced it** — its release
   **version** (or a short **git sha** for a non-release build), the command line, and
   the **target architecture** — plus the **host machine** (OS, CPU architecture). (The
   install path and the binary's own sha256 are deliberately NOT recorded: both are
   host-specific noise that says nothing about which code ran.) Ratios are byte-deterministic for a given
   (version, argv), so scores *should* match across machines; this provenance is what
   lets any future discrepancy be traced precisely, exactly as each dataset carries its
   own sha256. (Recorded automatically in `build/meta/squishy-scores.json` and
   `build/meta/squishy-score-complete.json`.)
6. **Lossless round-trip is required.** A Squishy Score only counts if the codec
   decompresses every file back to its exact original bytes. A codec that
   loses or corrupts data has no valid score. `squishy-calculate --verify
   --decompress "<cmd>"` checks this per file and refuses a score on any mismatch.

## Precision (so two honest runs agree)

- Compute each per-file ratio from the **exact byte counts** (`uncompressed_bytes ÷
  compressed_bytes`) at full precision — never from a pre-rounded ratio.
- Take the geomean over the full-precision per-file ratios (one vote per file),
  then round **once** for reporting: the **Squishy Score to 2 decimals**
  (e.g. `4.44×`). Compute **corpus bpb** from the exact byte totals
  (`8 · total_out_bytes / total_in_bytes`) and report to **3 decimals** (e.g.
  `3.090`). Category and size sub-scores to 2 decimals.
- Two codecs that tie at that printed precision are a **tie** — do not claim a win
  on hidden digits.

## What is NOT in the score

- **Speed is not in the canonical number** (it isn't reproducible across
  machines). Report speed separately on a leaderboard as
  *(ratio, compress MB/s, decompress MB/s, peak RAM)* on a named reference
  machine, optionally with a "best score above 500 MB/s" tier.
- **Synthetic / incompressible edge files** are not in the headline. They are
  reported in a separate Bounds panel as empirical edge cases — never claimed as
  theoretical bounds.

## Editions & honesty

- The corpus is **versioned and dated** (`Squishy-2026`, then a ~4-year refresh).
  Cite the edition. A codec overfit to one edition will visibly stop winning on
  the next.
- The edition manifest pins the **exact set of `(kind, size)` members**, not just
  filenames — the size ladder grows between editions, so two people citing
  "Squishy-2026" must be running the identical leaf set. Stamp every full Squishy
  Score with the edition, the per-file SHA-256 set, and total bytes processed.
- Every file is **real and provenanced** (see `LICENSE-MANIFEST.csv`). There is no
  synthetic or hand-built data in the scored corpus; synthetic/pathological inputs
  live only in the separate Bounds panel.

## Provenance: two classes, one frozen copy

Every member carries an `origin` in the manifest, and that decides who keeps the
authoritative bytes:

- **`upstream`** — third-party and **independently retrievable**: a pinned, immutable
  source URL plus a deterministic decode (gunzip / xz / unzip / head-slice). Anyone
  can re-fetch and reproduce the exact bytes, so we don't have to be their keeper.
- **`minted`** — bytes that **might change (or vanish) if re-fetched**, or that we
  built ourselves (slices, concatenations, format conversions, point-in-time
  snapshots). These cannot be trusted to re-derive, so we **mint them once and keep
  our own canonical copy** in the source-of-record (`s3://<bucket>/source/<key>`).
  That copy — not the volatile upstream — is the authority.

The pipeline (`scripts/publish-corpus.py`, `make mint|publish|release`) enforces it:
`mint` seeds `source/` for every minted member (generating the ones we can reproduce,
promoting the rest from an existing copy); `publish` populates the working corpus
(upstream re-fetched + sha-verified, minted server-side-copied from `source/`); and a
**release copies the frozen edition into `<edition>/` (the edition year, e.g. `2026/`) — minted straight
from `source/`, upstream re-fetched to prove it still reproduces.** Nothing is ever
uploaded whose sha256 doesn't match the manifest. A minted member with no copy in
`source/` blocks the release (we never freeze bytes we can't stand behind).

## Verifying a score

Anyone can reproduce a reported score: download the edition's files by their
published sha256 (the edition manifest / `CHECKSUMS.sha256`), run the same codec
build at the same setting, and recompute the geometric mean. `squishy-calculate --cmd "<your
codec>"` streams the verified corpus and does exactly this — it refuses to score
bytes whose published hash is missing or wrong (fail closed). `squishy bench
--cmd "<your codec>"` scores a local copy with the same rules.

## Submitting a score to the public leaderboard

The board is meant to stay trustworthy for 20 years, so a submission must be
**independently reproducible**, not just asserted:

1. Submit the full tuple: **codec name, version, exact argv, corpus edition**, the
   resulting **Squishy Score + 5 category sub-scores**, and (optional) speed on a
   named machine.
2. Provide a **one-line reproduction command** — ideally the exact
   `squishy-calculate --cmd "…"` (plus `--verify --decompress "…"`) invocation —
   and, for a non-public codec, a way to obtain the pinned build.
3. The maintainer (or any third party) **re-runs it** and only then is the row
   published. A number nobody else can reproduce does not go on the board.
4. Rows are stamped with the edition. When a new edition ships, scores are
   recomputed; a codec that overfit the old edition visibly drops.

Because a full re-run is hours of compute + a multi-GB download, the board is
**tiered**: a row is **self-reported** until reproduced, and **verified** once a
third party re-runs the *full* corpus and matches. A cheap smoke check — round-trip
+ sane per-file ratios on any files you choose — is fine for your own confidence,
but it is not a score and is never ranked.

## Partial runs

A run over the complete edition prints `Squishy Score: X.XX×`. A run over fewer
than all files prints the per-file ratio table plus an explicit line — *"partial
run (N/total files) — per-file ratios for your own regression use; NOT a Squishy
Score."* Per-file addressability is for picking the files that stress your code;
the headline is only ever the whole corpus.
