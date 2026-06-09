# Critique of the Gemini review, and a counter-proposal for score weighting

*Context: `plans/gemini-3-flash-review.md` proposes an "Algorithmic Redundancy
Taxonomy" — re-bin the scored kinds into four equal 25% categories and move
photo/movie/weights into an unscored diagnostic tier. This doc grades that review
against the locked design (`plans/squishy-score.md`, `scripts/squishy.py`) and
proposes a cleaner fix for the one real problem it surfaces.*

---

## TL;DR

Gemini found a real wart and prescribed the wrong medicine.

- **The wart is real:** with the compressibility plane evacuating Binary & Media
  down to a single scored file (`exe`), the nested geomean hands that lone 62 MB
  binary a **full 1/5 category vote**. Measured: dropping `exe` moves the headline
  from **5.81× → 6.48×**. One idiosyncratic file swings the citable number by
  **−11.5%**. That violates the project's own "no single file dominates" rule.
- **Gemini's cure is worse than the wart.** It re-categorizes by *codec technique*
  (BCJ filters, PPM/PAQ modeling), which the spec explicitly forbids; it
  **misclassifies `genome`** (the single most compressible file in the corpus) as a
  "dense non-text bitstream hostile to text compressors"; it re-derives the existing
  compressibility plane as if it were novel; and it replaces structural weighting
  with **hand-assigned 25% buckets** that are brittle over a 20-year lifespan.
- **My proposal:** demote *category* from a weighting tier to a pure diagnostic
  re-slice, and weight the headline by **kind** (a two-level `size → kind` geomean).
  This deletes the dictator (exe leverage 11.5% → 4.7%), deletes a whole layer of
  hand-drawn weights, and is *simpler* than what we have. It touches a LOCKED
  decision (nested `size→kind→category`), so it must go to the Opus advisor before
  adoption — see the standing directive.

---

## 1. What Gemini got right

**The "category of one" is a genuine balance flaw.** Today's headline is the nested
geomean over five categories. The per-file **compressibility plane**
(`K = coverage + (8 − entropy)/8 ≥ 0.11`) correctly drops photo/movie/weights as
entropy-coded, but it leaves **`exe` as the *only* scored member of Binary & Media**.
A category vote that was meant to represent a whole compression discipline now
represents one file:

| | headline |
|---|---|
| current nested (5 categories) | **5.81×** |
| same corpus, drop the `exe` category | **6.48×** |

So `exe` alone is worth **−0.67× / −11.5%** of the headline — the same leverage as
*all of Prose* or *all of Tabular/DB*. Gemini is right that this is disproportionate,
and right that the fix lives at the category layer.

It also independently converges on two things the project already does — moving
incompressible media out of the score, and equal-weighting disciplines so "best at
text" can't win alone. Convergence from a cold read is mild evidence those instincts
are sound.

## 2. Where the Gemini review is wrong or weaker than it claims

1. **It re-introduces codec-technique taxonomy — the thing we retired.** Gemini's
   category prose is literally "what it rewards: BCJ/Branch-Call-Jump preprocessing,
   PPM/PAQ-style bitwise modeling, delta-of-delta, transpose stride modeling." The
   spec is explicit: the axes are *descriptive, not predictive*; categorizing by how
   a 2026 compressor attacks a file is **circular for a compression benchmark** and
   **dates the corpus to today's codecs.** This is the same over-fitting that killed
   the R×D×M / C×W×K "cube." Adopting it walks us back into a retired mistake.

2. **It misclassifies `genome`.** Gemini moves `genome` into "Dense Non-Text
   Bitstreams … notoriously hostile to standard text-optimized compressors,"
   alongside `exe`. But `genome` has the **lowest order-0 entropy in the entire
   corpus (3.28 bpb)** and the **highest compressibility proxy (K ≈ 1.08)** — it is
   the *easiest*, most structured file we score, not a hostile one. The only thing it
   shares with `exe` is "not human-readable," which is surface modality, not
   compression structure. Grouping by modality is exactly the confusion the intrinsic
   axes (entropy / repetition / distance / size) exist to dissolve.

3. **It re-derives the compressibility plane without knowing it exists.** The
   proposed "Performance & Throughput Diagnostic Tier" for photo/movie/weights is
   *already shipped* — `is_scored()` drops them by intrinsic K, and they live on as a
   diagnostic panel. Presenting this as a novel redesign signals the review was done
   without the current scoring code in view, which undercuts confidence in the rest.

4. **Its headline math assumes a flat geomean we don't use.** "exe drops from 20% to
   12.5%" reasons as if every file were 1/5 of a five-way split. Our score is already
   nested. The 20% figure happens to be *coincidentally near* the true value (≈11.5%
   in ratio terms) but for the wrong reason — exe is a singleton *category*, not a
   1/5 *file*. A fix should target the singleton-category mechanism, which Gemini
   never names.

5. **Hand-assigned 25% buckets are the least durable choice available.** Fixed
   percentage weights are a maintenance liability: every future kind forces a
   re-derivation of the split, and "balance exe by pairing it with genome" is
   balancing-by-arbitrary-pairing — it only works until the next edition adds a kind.
   For a 20-year artifact we want weights that *emerge from structure*, not a table
   someone has to keep rebalancing.

## 3. The real problem, stated precisely

The plane decides *which files are compressible enough to score* (a per-file,
codec-free judgment — good). The nested geomean decides *how much each survivor
counts* by giving every **category** an equal vote. These two mechanisms interact
badly: **the plane can empty a category unevenly, and the geomean still pays the
survivors a full category-sized vote.** Binary & Media is the acute case (5 members →
1 survivor → 1 full vote), but the disease is general: category votes are only fair
when categories stay comparably populated, and the plane makes no such promise.

Note also that the *choice of weighting tier is itself worth ~12% of the headline*:
the same per-file ratios give **5.81×** weighted by category and **6.51×** weighted
by kind. A citable scientific number should not swing that much on how many boxes we
drew around the kinds.

## 4. Proposal

### Recommended — Option A: category becomes a diagnostic, weight by kind

Make the headline a **two-level `size → kind` geomean** (equal weight per size-point
within a kind; equal weight per kind in the headline). Keep the five categories
exactly as they are *for the corpus coverage map and the by-category diagnostic
table* — the spec already calls that table "a diagnostic re-slice, never a second
formula." We simply stop using categories as a load-bearing weighting tier.

```
score = geomean over the scored KINDS of
          ( geomean over that kind's size-points of  uncompressed/compressed )
```

What it buys:

- **Kills the dictator.** `exe` leverage drops from **−11.5% → −4.7%** (one kind
  among thirteen, not one category among five). No file, however quirky, gets a
  discipline-sized vote.
- **Deletes hand-drawn weights entirely.** No 25% table to maintain, no
  pair-to-balance hacks. Weight emerges from the kind list, which curation already
  governs.
- **Strictly simpler:** two tiers instead of three. Matches the standing directive's
  bias toward minimal, unified formulations.
- **Headline under A:** 5.81× → **6.51×** for `zstd -19` (illustrative; recompute per
  codec at freeze).

**The honest tradeoff:** category-nesting existed to stop a discipline that's split
into many kinds from dominating. Under kind-weighting, if a future edition adds five
code kinds, "code" out-votes "prose." The defense is **governance we already have**:
the *memorability guard* caps the corpus at ~8–12 kinds and curation is explicitly
charged with spanning the coverage map, not piling kinds into one corner. If we trust
curation to keep kinds balanced across disciplines — which the corpus philosophy
already requires — kind-weighting is safe, and it's the more timeless choice because
it removes machinery rather than adding it.

### Conservative fallback — Option B: keep nesting, forbid singleton scored categories

If we don't want to disturb the locked `size→kind→category` rule, add one runner
invariant: **a category earns a category-level vote only if ≥ 2 of its kinds clear
the plane; otherwise its lone survivor is weighted as a kind, not a category.** This
patches the acute case (`exe`) with minimal change and keeps category-balance for
populated categories. It's less elegant (it's a special-case rule rather than a
removed layer) but it preserves the locked decision and is a one-function change in
`nested_score()`.

### Rejected — Option C: re-house exe by modality (Gemini's path)

Folding `exe` into a "Dense Non-Text" bucket with `genome` is rejected for the
reasons in §2: it categorizes by codec technique, misclassifies `genome`, and
hand-tunes weights. It treats the symptom (exe is alone) by manufacturing a
companion, rather than fixing the mechanism that over-weights singletons.

## 5. What does NOT change

- **The compressibility plane** (`K ≥ 0.11`) stays — it's the codec-free,
  per-file scoring gate and it's working. photo/movie/weights remain unscored
  diagnostics. (Gemini's "diagnostic tier" is already this.)
- **The corpus and its five categories** stay — they're the memorable coverage map.
  Only their role *in the headline weighting* is on the table.
- **Anti-gaming** (versioned DOI editions, no-corpus-bytes rule, single-file leverage
  cap) is unaffected; Option A *tightens* the leverage cap from 1/(kinds×5) per
  category-survivor to a clean 1/kinds.
- **`corpus_bpb`** (byte-weighted rate) is untouched and still the size-weighted
  complement to the equal-weight headline.

## DECISION (owner, 2026-06-07): plain geomean, no magic numbers

The owner cut through the whole weighting debate: **the weighting tier was never
where the dimensionality belonged.** The dimensions that earn their keep are the
*intrinsic* axes that make the file selection representative — entropy, repetition,
repeat-distance (the three structural axes; the maximal set measurable codec-free
without circularity) plus size (operational). The *score* over those files should be
the simplest defensible thing.

**Adopted:** `Squishy Score = geomean of per-file compression ratio over the whole
corpus, one vote per file.` No category/kind/size weighting, no compressibility
threshold, no tuning constants. Every measured file counts once — including the
near-incompressible media (they score ~1.0× and lower every codec equally, so they
never change the ranking). The only files excluded are the unmeasured model-weight
**throughput ladder** (a speed/RAM fixture, not a ratio corpus member) — scoped by a
principled, non-magic line: *a file is scored iff we've placed it on the intrinsic
map (i.e. measured it).*

This retires **both** things this doc argued about: the nested `size→kind→category`
geomean *and* the `K ≥ 0.11` compressibility plane (a magic number by the owner's
"no magic numbers" rule). Categories survive only as the coverage-map / by-category
**diagnostic** grouping.

**Landed:** `scripts/squishy.py` (`corpus_score`, flat `_collect`, plane removed),
`squishy-calculate.py`, the board/`_capture`/`baseline`/`edition-manifest` scripts,
the index page (`build-site.py` + `squishy-cube.js`: compressibility wall and
"not scored" UI removed), `RULES.md`, `README.md`, `plans/squishy-score.md`, and the
tests (now assert the plain geomean over all files). Published board JSON
re-aggregated from its real per-file ratios; the complete-edition number is marked
**provisional** pending a fresh full streaming run that folds in the incompressible
core members.

## 6. Superseded — earlier recommended next step

Per the standing advisor directive (every design judgment call → Opus advisor,
lens = *intuitive / elegant / simple / useful / 20-year durable*): take **Option A vs
Option B** to the advisor with this doc and the leverage numbers. The specific
question to put: *"Does the memorability guard adequately protect kind-weighting from
kind-proliferation, or do we need category-balance (Option B) as a structural
backstop?"* That single question decides between the two. Do not adopt Gemini's
Option C.
