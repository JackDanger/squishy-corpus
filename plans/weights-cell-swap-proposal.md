# Proposal: swap the scored `weights` cell from all-MiniLM-L6-v2 → SmolLM2-135M

**Status:** DRAFT for review (constitution-level roster change — owner-gated, advisor-gated).
**Date:** 2026-06-22. **Branch target:** new branch off `edition-2026-namespace`.

## TL;DR

Repoint the single scored **`weights`** cell (Binary & Media / role `incompressible`)
from `sentence-transformers/all-MiniLM-L6-v2` (90 MB fp32 **sentence-embedding** model)
to **`HuggingFaceTB/SmolLM2-135M-Instruct`** (269 MB, an **instruction LLM**, *already
distributed in this edition as a diagnostic*). all-MiniLM is retired. SmolLM2 stops being
a diagnostic and becomes the scored cell under the canonical filename `weights.safetensors`.

This is a **coherence / believability** change, **not** a score change. It is score-neutral
by construction (see "Score impact"). If we don't have a positive reason, the default is
*leave MiniLM in place* — this doc exists so reviewers can decide whether the coherence win
is worth a constitution edit.

## Why (the case for)

1. **Genre fit.** The cell's job is to exemplify "neural-net weights bytes." The cultural
   referent of "model weights" in 2026 is an LLM, not a sentence-embedding encoder. The
   site copy already says *"the trained weights of a small neural network"*
   (`scripts/build-site.py:36`). SmolLM2-135M is squarely that; MiniLM reads as "an
   embedding model," a sibling genre.
2. **Internal coherence of the weights ladder.** Today the scored cell (MiniLM, embeddings)
   and the throughput ladder (Qwen2.5-0.5B, Qwen2.5-1.5B — instruction LLMs) are *different
   genres*. After the swap the anchor (SmolLM2-135M) and the ladder (Qwen 0.5B/1.5B) are all
   real instruction LLMs — one clean size ladder: **135M → 0.5B → 1.5B**.
3. **Human-scale is preserved.** 269 MB is still a human-scale exemplar (the schema's `kind`/
   `incompressible` constraint; README: *"a 3 GB JPEG tells you nothing a 90 MB one didn't"*).
   We are NOT promoting a multi-GB model — that would break the principle.
4. **No new provenance/license risk.** SmolLM2-135M is Apache-2.0, already pinned and
   distributed in this edition; sha256 already in the manifest. Zero new external dependency.
5. **Reviewer consensus (Composer 2.5 + GPT-5.5):** size is the wrong lever; believability
   lives in named provenance + genre fit; the only believability-positive move is *sideways*
   (MiniLM → a real small LLM already in the bundle), not smaller and not larger.

## Why not (the case against — for honest review)

- It's a **constitution edit** (schema.json `cells`), which the schema says should happen
  "≈once per decade, reviewed hard." A coherence nicety may not clear that bar.
- MiniLM is the **#1-downloaded model on HF** — by raw recognizability it's already strong.
- It costs a **re-measure + re-freeze** of the edition (measured fields, fingerprints, board).
- "Embedding vs LLM" is a connoisseur's distinction; many users won't care.

## Design decision: keep the canonical filename (content-swap)

Kind/incompressible cells use **generic names** (`photo.jpg`, `movie.mp4`, `weights.safetensors`);
descriptive names (`weights-smollm2-135m.safetensors`, `nasa-http-…`) are for length/diagnostic
rungs. To stay consistent, the scored cell **keeps** the filename `weights.safetensors`; its
*bytes/lineage* become SmolLM2-135M. The real identity lives where it already does — in
`lineage` / `source_url` / the site `display`. The now-redundant `weights-smollm2-135m.safetensors`
diagnostic is **removed** (its bytes are now the scored cell under the canonical key; keeping
both = same bytes twice).

> **OPEN QUESTION for reviewers:** is content-swap right, or should the scored cell adopt the
> descriptive name `weights-smollm2-135m.safetensors` (breaking the generic-name convention but
> making the real model legible in the filename)? I recommend content-swap for consistency +
> minimal churn, but flagging it.

## Score impact — rank-stable, but NOT "zero" (corrected after review)

> Earlier drafts overclaimed "score-neutral." Three independent reviews (Composer 2.5,
> GPT-5.5, GPT-5.3-Codex) corrected this. The accurate statement:

- **`squishy_score` (flat geomean, one vote of 26):** replacing the cell's ratio `r → r'`
  scales each codec's headline by `(r'/r)^(1/26)` — zero *only* if `r' = r`. MiniLM's
  per-codec `weights` ratio already varies (~1.07× xz-9 … ~1.12× zstd-22, per
  `squishy-board-complete.json`); SmolLM2 is also fp32 safetensors so `r' ≈ 1.0–1.15×`.
  The headline moves **slightly and codec-dependently**, almost certainly too little to
  reorder the panel (adjacent codecs differ by ~4%), but "cannot change the ranking" is
  **not provable a priori** — it must be confirmed by re-scoring.
- **`corpus_bpb` (byte-weighted, reported beside the score):** **NOT neutral.** Growing the
  scored incompressible cell 90 MB → 269 MB adds ~178 MB of ~8-bpb bytes to an ~11 GB / ~1.12-bpb
  pool: estimated **corpus_bpb ≈ 1.12 → ~1.22 (~9% relative)**. This is a real, disclosable
  move, not noise.

**Honest framing:** rank-stable in practice; cosmetic for the *headline*; a visible ~9% bump
in the *byte-weighted* companion metric that we must disclose, not bury. That ~9% is itself an
argument for caution — see Decision.

## Exact changes

> **CRITICAL correction (all three code reviews):** `edition.json` and the board/score JSONs
> are **generated**, not hand-authored. `scripts/build-edition-manifest.py` reads
> `LICENSE-MANIFEST.csv` (size/license/source_url), `CHECKSUMS.sha256` (sha256), and
> `file-properties.json` (entropy/coverage). The **source-of-record** edits below (group A)
> are the ones a human makes; everything in group C is then **regenerated** by the pipeline.
> Hand-editing `edition.json` alone would be silently overwritten.

### Group A — source-of-record (edit by hand)

1. **`build/meta/schema.json`**
```diff
   cells[]:
-  {"id":"weights", ... "lineage":"transformer-safetensors", "file":"weights.safetensors"}
+  {"id":"weights", ... "lineage":"smollm2-135m-instruct",   "file":"weights.safetensors"}
   diagnostics[]:
-  {"file":"weights-smollm2-135m.safetensors","kind":"weights","reason":"Model-weight throughput/RAM ladder …"}
   (qwen2.5-0.5b and qwen2.5-1.5b diagnostics unchanged)
```

2. **`build/meta/LICENSE-MANIFEST.csv`** ← *the size/license/source_url set-of-record*
   - Edit the `weights.safetensors,weights,…` row: MiniLM URL/sha/`90868376` → SmolLM2
     URL/`5af571…`/`269060552`, attribution `…all-MiniLM-L6-v2` → `…SmolLM2-135M-Instruct`.
   - **Delete** the `weights-smollm2-135m.safetensors,scale-weights,…` row.

3. **`build/meta/CHECKSUMS.sha256`** ← *guarded by `test_roster_consistency.py:test_core_matches_checksums`*
   - `…53aa51…  corpus/weights.safetensors` → `…5af571…  corpus/weights.safetensors`.

4. **`build/meta/NOTICE`** — the `weights —` attribution line names MiniLM → SmolLM2-135M;
   remove SmolLM2 from the diagnostic-ladder list (it's now the scored cell).

5. **`build/meta/file-properties.json`** — the `weights` entry's measured props
   (size/entropy/…) are re-derived by `file-properties.py` from the new bytes (don't hand-fake).

6. **`scripts/squishy.py:80`** (comment only; CORE tuple/filename unchanged)
```diff
-  ("weights", "corpus", "weights.safetensors"),  # model-weight shard (Apache) [incompressible]
+  ("weights", "corpus", "weights.safetensors"),  # SmolLM2-135M LLM weights (Apache) [incompressible]
```

7. **`scripts/build-site.py`** — `SHORT["weights"]` `"MiniLM"` → `"SmolLM2-135M"`; **drop** the
   `"135m"` branch in `scale_what()`; **reword** the Qwen ladder copy (0.5B is no longer the
   "second rung", 1.5B no longer the "top rung" once 135M leaves the scale tier).

8. **`scripts/publish-corpus.py`** — delete the `scale/weights/weights-smollm2-135m.safetensors`
   map row. `corpus/weights.safetensors` stays `{origin:upstream, how:stream}`; URL authority is
   the manifest row (no hardcoded MiniLM URL here — **confirmed** by review). **Pin `source_url`
   to an immutable `resolve/<commit-sha>/…` revision** (advisor: the real durability bug; see
   Riders), not `resolve/main/`.

### Group C — regenerated by the pipeline (DO NOT hand-edit)

`build/meta/edition.json` (incl. `weights` entry, `n_files` 31→**30**, `n_scored` **26**,
`scored_bytes`→**11,171,290,211**, `total_bytes`→**17,303,401,061**, `files_sha256`),
`build/meta/baseline.json` (`scored_set_fingerprint`, `files_sha256` — `check-baseline.py`
diffs this), `squishy-scores.json`, `squishy-score-complete.json`, `squishy-board-complete.json`,
`verification-pass4.json`, `size-convergence.json`, `coverage-map.svg`, and the `build/site/*`
copies — all emitted by `run-all.sh` after Group A/B land and the real bytes are in place.

### Tests — re-derive, don't hand-edit numbers
`tests/test_edition_manifest.py`, `tests/test_roster_consistency.py`, `tests/test_schema.py`
must pass with `n_files=30` and the new fingerprint. Update any hardcoded `31`/`26`/digests.

## Migration / apply steps (owner-gated; needs the bytes)

1. Fetch SmolLM2-135M `model.safetensors` (sha must equal `5af571…`, 269,060,552 bytes);
   place as the corpus `weights.safetensors` source-of-record.
2. Apply Group A + B edits (schema, LICENSE-MANIFEST.csv, CHECKSUMS.sha256, NOTICE,
   squishy.py comment, build-site.py, publish-corpus.py).
3. Re-run the pipeline to regenerate Group C: `scripts/run-all.sh` (or targeted
   `file-properties.py` → `build-edition-manifest.py` → `calculate-all.py` →
   `build-baseline.py` → `coverage-map.py` → `build-site.py`).
4. `pytest` green; **explicitly record** the new `squishy_score` (confirm panel order
   unchanged) and the new `corpus_bpb` (expect ~1.12 → ~1.22). If the score order changes
   or bpb moves far from ~+0.10, STOP — that's the tripwire.
5. Owner review → fold into the edition's **initial** freeze → publish.

## Riders (required if swapping — from the Opus advisor)

- **Pre-freeze only.** Status checked 2026-06-22: the edition is **freeze-*ready* but NOT
  frozen** (HEAD = "…freeze-ready"; no frozen marker/publish commit). ✅ condition met — this
  rides the *initial* freeze, so there is no thaw/re-freeze cost. **If Squishy-2026 is ever
  frozen before this lands, DEFER to Squishy-2030** — the whole case rests on it being cheap.
- **Pin the upstream URL** to `resolve/<commit-sha>/…` (not `resolve/main/…`) in the same
  change. An unpinned HF `main` URL is a louder 20-year reproducibility bug than embedding-vs-LLM;
  worth sweeping across the other upstream-streamed cells too.

## 20-year note (orthogonal, do NOT fix here)

The real long-horizon refresh question for this cell is **format, not model**: fp32
`.safetensors` (entropy 7.36 — exploitable mantissa structure) is already half-archaic vs how
weights actually live on disk now (GGUF / int8 / fp8 / bf16, which are *more* incompressible
and more representative). Flag for **Squishy-2030**: revisit the weights cell as fp32-safetensors
→ quantized-GGUF. Note the generic filename is mildly format-locked at `.safetensors`. This swap
neither fixes nor worsens it.

## Decision requested

(a) **Swap or leave MiniLM?** Given pre-freeze status, the cost objection mostly dissolves; the
real question is owner taste: genre-coherent LLM ladder (135M→0.5B→1.5B) vs MiniLM's #1-on-HF
recognizability. (b) If swap: content-swap (recommended) vs descriptive rename? (c) Accept the
disclosed ~9% `corpus_bpb` move as the price of coherence?

## Reviewer verdicts (4 independent)

- **Opus advisor:** SWAP-WITH-CHANGES — content-swap, pre-freeze only, pin the URL; reframes
  this as *not* a constitution edit (cell shape unchanged; refilling bytes is the edition's job).
- **Composer 2.5:** LEAVE MiniLM unless owner explicitly wants the narrative re-freeze; byte math
  + content-swap correct; flagged the source-of-record + `corpus_bpb` ~9% misses.
- **GPT-5.5:** LEAVE MiniLM unless pre-freeze and owner wants LLM identity; `corpus_bpb` not
  neutral; same source-of-record misses.
- **GPT-5.3-Codex (mechanics):** as originally drafted, incomplete/inconsistent — now fixed by
  Group A/C restructure; byte arithmetic verified correct; no hardcoded MiniLM URL in publish.
