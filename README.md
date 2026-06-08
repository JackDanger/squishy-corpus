# Squishy

**The 2026 compression corpus — one curated, representative set of real data:
cite its Squishy Score to compare compression ratios, or use it as a regression
battery when you're tuning a codec's speed and memory without changing its output.**

```
git clone https://github.com/JackDanger/squishy-corpus && cd squishy-corpus
uv run squishy-calculate --cmd "zstd -19 -c"     # streams the FULL corpus, scores your codec
→ Squishy Score: 6.88×   (plain geomean over every file — provisional; full freeze run pending)
# streams + verifies every file; resumable; re-runs are instant from cache
```

## What is Squishy

Squishy is a successor to the Silesia corpus for 2026: a fixed set of **real,
redistributable files**, each measured along a few intrinsic properties — *how
random the bytes are, how repetitive, how far back the repeats sit, and how big
the file is* — and deliberately chosen to **span that range** rather than pile up
in one corner of it.

One corpus, two jobs:

- **Measure ratio.** Run your compressor over the whole corpus and you get a
  **Squishy Score** — one number, a geometric-mean compression ratio, citable and
  pinned to a frozen edition (SHA-pinned, Zenodo DOI). You recompute it when you
  change your *algorithm*, not on every commit. If you're researching better
  ratios, this is your scoreboard.

- **Test behavior.** Most compression work *isn't* about ratio. If you're making
  an existing codec faster, leaner, or more parallel, you need a representative
  battery of real inputs to catch **time / CPU / memory regressions** — and
  confidence the battery actually covers the cases that behave differently.
  Because Squishy's artifacts are placed to span byte structure *and* size (from
  tens of MB to multi-GB), "I tested on Squishy" means "I tested across the real
  diversity of inputs a compressor sees," not "I tested on a random pile."

Both jobs rest on the *same* property: the corpus is a **diverse, representative**
set, and the [coverage map](#the-coverage-map) is the evidence of that diversity.
Whether you're picking one artifact to stress-test or scoring your whole codec,
you're relying on the same thing — that these files cover the space.

## Who it's for

- **The ratio researcher** — innovating on how small files get. Cites the Squishy
  Score as a stable benchmark across editions.
- **The implementer hardening a codec** — e.g. a faster gzip-compatible encoder
  that outputs the *same bytes*. Its Squishy Score barely moves from stock gzip;
  the value is a diverse, principled test set to guard against speed/CPU/memory
  regressions.
- **The archivist / citation user** — needs a fixed, provenanced, DOI-frozen
  dataset they can name in a paper and others can reproduce exactly.

## The coverage map

Compressors behave differently depending on the *shape* of the input — random
media vs. repetitive logs, nearby matches vs. long-range ones — and differently
again at *scale*, where window size, long-range matching, and parallel block
decode start to matter. So each artifact is measured on four intrinsic,
**codec-free** properties (computed from the bytes alone, never from how some
compressor performs):

- **how random** — order-0 entropy (bits/byte)
- **how repetitive** — fraction of the file that is exact repetition
- **repeat distance** — how far back those repeats sit (local vs. long-range)
- **how big** — file size

The files are **sparse in this space — not a dense grid — but representative of
the whole.** We claim *coverage of the range*, not completeness; and these
properties are the dimensions along which compressors are *known* to behave
differently — they describe why each file is here, they don't *predict* a ratio.
These four properties are why each file is here; they make the *file selection*
representative. They are **not** a weight in the score. Explore it: the live map
plots every artifact in 3D (size as the dot size) at
[squishy.jackdanger.com](https://squishy.jackdanger.com) *(soon)*.

## The Squishy Score

> **Squishy Score (of a codec) = the geometric mean of the per-file compression
> ratio (uncompressed ÷ compressed) over the whole corpus — one vote per file.**

- **Plain geomean, one vote per file.** No category weights, no kind weights, no
  size weights, no tuning constants, and no threshold deciding which files count.
  Every file is averaged once; the geometric mean is what stops any single huge or
  tiny file from running away with the number. (Design rationale:
  [`plans/score-weighting-critique-and-proposal.md`](plans/score-weighting-critique-and-proposal.md).)
- **Every real file counts — including the near-incompressible media.** `photo`,
  `movie`, and `weights` score ~1.0×, which lowers the headline by the same factor
  for every codec, so they never change the ranking — and a corpus of real data
  honestly contains some incompressible files. The only thing left out is the
  unmeasured model-weight **throughput ladder** (a speed/RAM fixture, not a ratio
  corpus member).
- Reported as **"×"** (2 decimals) — a dimensionless quality index, **not** a bit
  rate. Always shown beside the **corpus bpb (byte-weighted)** = 8 · total
  compressed ÷ total input (3 decimals), the operational rate; the two are
  deliberate complements (equal-per-file vs size-weighted).
- **Whole-corpus and periodic.** It's expensive and meant to be *stable* — you run
  it when you change your algorithm, not per commit. The corpus may be tens of GB.
- **Lossless round-trip required**; **speed is not in the score** (it isn't
  cross-machine reproducible — it lives on a leaderboard).
- A number is a property of *(codec, setting, codec-version, codec-argv,
  corpus-edition)*. See [`RULES.md`](RULES.md).

There is **one corpus and one number** — no subset and no second score. A run over
the *complete* edition is the only thing that prints a Squishy Score; running over
a handful of files (for CI/dev) prints per-file ratios for your own regression
diffs, not a headline.

## The corpus

Memorable like Silesia — you can name the **kinds**. Every file is **real and
provenanced** (sources, licenses, SHA-256 in
[`build/meta/LICENSE-MANIFEST.csv`](build/meta/LICENSE-MANIFEST.csv)).

| Category | Kinds |
|----------|-------|
| **Prose** | `dickens` (Dickens, PD) · `aozora` (Natsume Sōseki, PD Japanese) |
| **Code & Web** | `monorepo` (LLVM/clang source) · `minjs` (Plotly.js, minified) · `markup` (Bosak Shakespeare XML) |
| **Structured** | `json` (USGS earthquakes) · `log` (NASA-HTTP server log) · `genome` (E. coli FASTQ) |
| **Tabular / DB** | `csv` (NOAA daily weather) · `parquet` (US DOT airline on-time) · `sqlite` (USDA nutrition DB) |
| **Binary & Media** | `exe` (Hugo binary) · `photo` (NASA "Blue Marble" JPEG) · `movie` (Big Buck Bunny) · `weights` (a Transformer's safetensors) |

The size axis is real: people compress 40 MB files *and* multi-GB ones, so the
corpus carries **large members (0.3–4 GB+) that extend well past today's codec
window sizes** — that's where long-range matching and large-window modelling start
to matter, and where a future large-file codec earns its keep. Large members
acquired (all PD / permissive, whole-file measured):

- **`csv`** — NOAA GHCN-Daily, 1.33 GB (one year) **and 4.07 GB** (three years)
- **`monorepo`** — full LLVM source tree, 1.77 GB
- **`archive`** — four clang release source trees concatenated, 1.50 GB: a real
  release-mirror/backup artifact (high repetition with duplicates hundreds of MB
  apart — the long-range corner nothing else covers)
- **`genome`** — full E. coli run, 1.07 GB · **`text`** — enwik9, 1.0 GB ·
  **`log`** — full NASA-HTTP Jul+Aug, 0.37 GB · **`parquet`** — US DOT airline
  on-time, multi-year columnar

`json` and `sqlite` stay single-size: their byte-property region is already
represented (by `log` and `csv` respectively) and the size axis is carried by the
rungs above. The **near-incompressible** kinds (`photo`, `movie`, `weights`) also
stay single-size — a 3 GB JPEG tells you nothing a 90 MB one didn't (the 1080p
movie and the model-weights ladder ship as throughput/behaviour diagnostics, not
scored rungs).

## Run it

**Zero setup — stream the corpus and score in one command:**

```bash
squishy-calculate --cmd "zstd -19 -c"                       # score a stdin→stdout codec
squishy-calculate --cmd "xz -9 -c" --verify --decompress "xz -dc"   # + prove losslessness
squishy-calculate --cmd "mycodec -o {out} {in}"             # file-arg codecs
```

`squishy-calculate` streams each file from the published mirror, **verifies it
against the published SHA-256 and refuses to score unverifiable bytes** (fail
closed), caches verified bytes + per-file results, and is **resumable** and
**idempotent** (same codec + bytes ⇒ the same number, instantly). Point it at any
mirror with `--base`.

**For CI / dev,** pull whatever files stress your code — each is individually
addressable by name in the edition manifest
([`build/meta/edition.json`](build/meta/edition.json): per-file HTTPS URL + SHA-256
+ size + kind) — and check the [coverage map](#the-coverage-map) for
representativeness. A run over fewer than all files prints per-file ratios for your
own regression diffing, **not a Squishy Score**.
**Already have the bytes?** `squishy bench --cmd "…"` is the same runner's
local-bytes path (both `squishy-calculate` and `squishy bench` are implemented in
`scripts/squishy.py`).

## Reference board

The first **complete-edition** Squishy Score has been computed end-to-end —
**`zstd -19` → 5.81×** over all 20 scored size-points (core + large rungs, 12.2 GB →
1.5 GB), in [`build/meta/squishy-score-complete.json`](build/meta/squishy-score-complete.json).
The large rungs compress *better* than their small siblings (LLVM 12.6×, csv-4 GB
12.6×, clang-archive 17.5×) — long-range matching at scale, now in the number.

The table below is the **fast panel over the small members only** (draft) — handy
for ranking codecs quickly; the full panel over the complete edition is the
expensive periodic computation. Every codec build is pinned in
[`build/tools.lock`](build/tools.lock).

| codec | Squishy Score (×) | corpus bpb (byte-weighted) |
|-------|------------------:|---------------------------:|
| zpaq | 5.81× | 2.620 |
| xz -9 | 4.37× | 2.977 |
| brotli -11 | 4.34× | 3.021 |
| zstd -22 | 4.20× | 3.092 |
| zstd -19 | 4.15× | 3.106 |
| bzip2 -9 | 3.98× | 3.278 |
| gzip -9 | 3.23× | 3.495 |

The **Squishy Score** (×) is the geometric mean of the per-file ratio over every
file — one vote per file, including the near-incompressible media (which score ~1.0×
and lower every codec's number equally) — a dimensionless quality index, *not* a bit
rate (don't derive bpb from it). The **corpus bpb** is the byte-weighted operational rate over the whole corpus
(8 · total compressed ÷ total input). They're deliberate complements: the score
weights every file equally (anti-gaming); corpus bpb is the size-weighted physical
number. `zpaq` is the legacy 2016 v7.15 build — an honest data point, but the
mainstream-codec rows reproduce most portably.

## Editions & permanence

- **Versioned, dated editions.** The edition manifest pins the exact set of
  `(kind, size)` members, so two people citing "Squishy-2026" can't silently
  disagree. A codec overfit to one edition visibly stops winning on the next.
- **Hosted footprint stays small.** Large files are **hosted where their size is
  load-bearing** (real long-range structure — csv, genome, repo); incompressible
  and pathological large inputs ship as **seeded generators + published SHA-256**
  that regenerate bit-for-bit. A tens-of-GB benchmark inside a ~10–20 GB mirror.
- **Permanent home:** a Zenodo DOI (defeats link-rot); the public S3 bucket is a
  mirror accessed via the edition manifest of HTTPS URIs
  ([`edition.json`](build/meta/edition.json)). Every file's SHA-256 is
  published; anyone can recompute the score with a pinned codec build.

## Licensing

Every file is redistributable (Public-Domain / CC-BY / Apache-2.0 / MIT), with
source URL, license, and SHA-256 in
[`build/meta/LICENSE-MANIFEST.csv`](build/meta/LICENSE-MANIFEST.csv). `movie` (Big
Buck Bunny) is © Blender Foundation, CC-BY 3.0. The build system is [MIT](LICENSE).

---

*Status: pre-1.0. The small core is assembled, verified, and scored; the
size-spanning corpus and its score are being finalized; the edition **will be**
frozen and DOI-minted at `v1.0.0`. Roadmap:
[`plans/squishy-1.0-readiness.md`](plans/squishy-1.0-readiness.md).*
