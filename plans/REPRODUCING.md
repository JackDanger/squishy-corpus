# Reproducing the Squishy corpus & score

Every distributed file is either fetched verbatim from a pinned source or **derived
by a pinned recipe that reproduces byte-for-byte**. `scripts/verify-derived-reproducible.py`
rebuilds the constructed files and asserts their sha256 against the recorded values;
`scripts/run-all.sh` runs the whole clean-room reproduction and diffs against
`build/meta/baseline.json`.

## Toolchain pins (derived-file determinism depends on these)
- **Python** 3.14.x · **pyarrow** 24.0.0 (the BTS parquet's `created_by` is
  `parquet-cpp-arrow version 24.0.0`; a different pyarrow major may change container
  bytes — re-pin and re-baseline if upgraded).
- **xz/lzma** (stdlib) for decompressing `.tar.xz` release assets — deterministic.
- Codec builds for scores are pinned in `build/tools.lock` and recorded per-score in
  `squishy-scores.json` / `squishy-score-complete.json` (version + arch + host).

## Derived files and their recipes
| file | recipe | reproduces |
|------|--------|-----------|
| `corpus/data.parquet` (parquet core) | BTS On-Time 2024-01 CSV → `scale-acquire-bts-parquet.py --core --months 2024-1` (all columns string, uncompressed, one row group) | ✓ byte-identical |
| `scale/.../bts-ontime-2022-2024.parquet` | same builder, `--months 2022-1..2024-12` | ✓ (per-month determinism) |
| `scale/.../clang-releases-16-17-18-19.tar` | 4 clang release `.tar.xz` (16.0.0,17.0.1,18.1.8,19.1.0) → lzma-decompress each → concatenate | ✓ byte-identical |
| `scale/.../noaa-ghcn-daily-2021-2023.csv` | NOAA `by_year/{2021,2022,2023}.csv.gz` → gunzip → concatenate | ✓ (gunzip deterministic) |
| `corpus/monorepo.tar`, `scale/.../llvm-project-19.1.0.src.tar` | LLVM release `.tar.xz` → lzma-decompress | ✓ |
| `corpus/dickens`, `corpus/aozora.txt` | Gutenberg/Aozora source → boilerplate/ruby strip (see build scripts) | source-anchored |

Fetched-verbatim files (enwik9, genome FASTQ, NOAA single-year, USGS json, USDA
sqlite, photo, movie, weights, minjs, markup) reproduce trivially from their pinned
source URL + sha256 in `LICENSE-MANIFEST.csv` / `edition.json`.

## The one-shot tarball
Built at freeze with `scripts/build-tarball.sh <dir> squishy-2026.tar` (GNU tar,
`--sort=name --owner=0 --group=0 --numeric-owner --mtime=@0`) so the citable archive
has a fixed sha256 independent of the machine that built it.

## End-to-end
```
bash scripts/run-all.sh        # reproduce + verify against build/meta/baseline.json
```
Regenerate the baseline only on a deliberate edition change:
`uv run python scripts/build-baseline.py` (review the diff before committing).
