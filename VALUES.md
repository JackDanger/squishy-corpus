# What Squishy believes

Squishy is meant to be a shared yardstick for decades. Every rule in
[`RULES.md`](RULES.md) and every guardrail in the code descends from a small set
of values. They're worth stating plainly, because they explain *why* the project
is careful where it's careful — and why it ties every number to verified bytes and a
dated, DOI-backed edition rather than quoting an impressive figure it can't stand behind.

### 1. Honesty over hype

A Squishy Score is real and reproducible: the runner verifies every file against its
published SHA-256 and **refuses to score bytes it can't verify** (always-on,
fail-closed), so a number can never quietly drift from the corpus it claims. The
headline is the **complete-edition** geomean over *every* scored file — the
near-incompressible members (photo / movie / weights) folded in, not cherry-picked away
to flatter the ratio. And a score is never a floating figure: it is pinned to a dated,
DOI-backed edition (`Squishy-2026`), so what you cite can't shift under you.

> *See it:* the complete board in
> [`build/meta/squishy-board-complete.json`](build/meta/squishy-board-complete.json)
> (`round_trip_verified` on the reference codec); the per-file hashes in
> [`build/meta/edition.json`](build/meta/edition.json).

### 2. Reproducible by anyone, forever

You never have to trust us — you re-run us. Every file carries a published
SHA-256 and the runner **refuses to score bytes it can't verify** (always-on,
fail-closed — not an opt-in flag); a Zenodo DOI defeats link-rot. A score is a
property of *(codec, setting, version, argv, edition)* and is recorded with the
exact tool that produced it. (Add `--verify --decompress "<cmd>"` to also prove
the round-trip is lossless.)

> *See it:* run `squishy-calculate` and watch it reject a tampered byte; the
> per-file hashes in [`build/meta/edition.json`](build/meta/edition.json) and
> [`build/meta/LICENSE-MANIFEST.csv`](build/meta/LICENSE-MANIFEST.csv).

### 3. Real data only

No synthetic or hand-built files in the scored corpus. Real inputs compress the
way real inputs do; a benchmark made of generated data measures the generator.
Pathological and synthetic inputs exist only in a clearly separated Bounds panel,
never in the headline.

> *See it:* the entry gates in [`GOVERNANCE.md`](GOVERNANCE.md) ("What a file
> must be to enter the core").

### 4. Representative, not just big

Files are placed to **span** the space of how data compresses — random vs.
repetitive, near vs. far-range repeats, tens of MB to multi-GB, real formats
(e.g. five executable/compiled forms spanning ELF, PE, ARM64, Wasm, and DWARF)
— not piled into one easy corner. That spread is the whole basis for "I tested on Squishy" meaning
something, and it's something you can *see*, not just take on faith.

> *See it:* the [coverage map](build/meta/coverage-map.svg) (and the live 3D
> explorer at [squishy.jackdanger.com](https://squishy.jackdanger.com), *soon*).

### 5. Un-gameable by design

The score is a plain geometric mean — **one vote per file**, no category/size
weights, no tuning knobs. You must not ship corpus bytes (or a dictionary trained
on them) inside your codec, tune per file, or branch on filename — rules the
runner enforces where it can and that anyone re-running you can check. And the
math itself is the backstop: a codec that overfits one giant file or one kind of
data pays for it on every other file.

> *See it:* "The canonical run" rules #1–#4 in [`RULES.md`](RULES.md).

### 6. Built to outlive its maintainer

The corpus, the runner, and these documents live in public Git; every edition is
additionally pinned in Zenodo. The artifact survives independent of any one host,
account, or person — including whoever currently maintains it.

> *See it:* "Maintainership & succession" in [`GOVERNANCE.md`](GOVERNANCE.md).

---

These six aren't aspirations bolted on after the fact — they're already enforced
by the code and the rules. This page just makes the *why* findable, so the rigor
elsewhere reads as principle rather than red tape.
