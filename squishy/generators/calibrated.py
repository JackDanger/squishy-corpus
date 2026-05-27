"""Generate a factorial (H × M) calibrated synthetic binary corpus.

Design axes:

  Main H×M grid:
    H (marginal entropy):  1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0 bits/byte
    M (LZ match density):  0.00, 0.25, 0.50, 0.75
    sizes:                 256K, 4M  (64M skipped for disk budget)
    seeds:                 s0, s1, s2
    filenames:  {size}-{H_str}-{M_str}-{rep}.bin

  L-sweep (supplementary, fixed M=0.50):
    H (marginal entropy):  same 8 values
    mean_L (match length): 3, 8, 32, 128
    sizes:                 256K, 4M  (64M skipped)
    seeds:                 s0, s1, s2
    filenames:  {size}-{H_str}-M0p50-L{L}-{rep}.bin

Plus one zeros file per size.

Purpose — a measurement instrument, not a theoretical bound:
  This corpus is a dial-a-file machine with two independently controlled knobs:
  H (how random the bytes are) and M (how much of the file is copy-paste from
  earlier in the file). Running codecs across the full (H, M) grid reveals
  codec-family differences that are invisible in natural-corpus benchmarks
  (Silesia, enwik8, Calgary), because natural files cluster in a narrow region
  of (H, M) space where codec rankings are similar.

  The reference rate R_ref is a measuring stick — the cost of encoding the file
  using the exact construction parse under ideal arithmetic coding. It is NOT a
  lower bound on achievable compression; strong codecs can and do beat it by
  finding better parses than the construction parse. Rate_ratio = compressed /
  reference_bytes; values < 1 mean the codec found structure the construction
  parse missed, which is informative, not a violation.

Ground truth per file (written to ground-truth.json):
  H_marginal:   Shannon entropy of the PMF (exact, bits/byte)
  R_ref:        reference-coder rate — bits/byte if you compressed using the
                exact construction parse with ideal entropy coding.
                NOT the Shannon entropy rate; NOT a lower bound on compression.
  M_fraction:   nominal target copy-byte fraction
  realized_M:   actual measured copy-byte fraction (may differ slightly from
                M_fraction due to finite-file effects)
  reference_bytes: ceil(R_ref × size / 8)
  perm_seed, data_seed: for full reproducibility

PMF parameterisation — tilted exponential:
  p_i ∝ exp(-β·i)  for i ∈ 0..255.
  β solved by bisection so Σ p_i·log2(1/p_i) = H_target exactly.
  β=0 → uniform (H=8); larger β → more skewed (lower H).
  After computing the PMF, the 256 byte values are randomly permuted once
  per seed so that the high-probability symbol is not always byte 0x00.
  This prevents codecs with position-dependent context models (e.g., LZMA's
  pb/lp parameters) from getting a free alignment signal.

Post-hoc LZ duplication:
  An IID stream from the PMF is post-processed: fraction M of bytes are
  replaced by LZ back-references (distance log-uniform [1, 32768]; length
  geometric(p=1/8), mean=8). The marginal byte distribution is preserved.

  Known limitation — second-order copying artifact:
  apply_lz_duplication copies from the already-modified buffer, so copied
  regions may themselves contain previously-copied bytes.  This introduces
  higher-order statistical correlations (run-length structure, periodic
  sub-sequences) beyond what the (H, M, L) axes capture.  Context-mixing
  and BWT-based codecs can exploit this latent structure and compress well
  below R_ref, particularly for high-M, long-L cells (M≥0.75, mean_L=128
  produce the most second-order structure).  Rate_ratio < 1.0 on such cells
  does NOT indicate a measurement error; it indicates the codec found
  structure the construction parse did not account for.
  A v5 generator could copy from a pristine pre-duplication buffer to
  eliminate second-order correlations.

Reference-coder rate derivation (R_ref):
  A reference coder that knows the construction parse encodes each copy event
  at cost (H_D + H_L) bits per event, amortized over mean_L copy bytes; fresh
  bytes cost H_marginal bits each. Thus:
    R_ref = (1-M)·H_marginal  +  M·(H_D + H_L)/mean_L
  where H_D = H(log-uniform 1..32768) ≈ 10.55 bits
        H_L = H(geometric p=1/8)      ≈  4.35 bits
        mean_L = 8
  Clamped at H_marginal: copying is more expensive than literals when
  H_marginal ≤ _COPY_BITS_PER_BYTE ≈ 1.86 bpb.

  R_ref is the cost of ONE specific valid code for these files. Better parses
  exist; strong codecs find them. Rate_ratio = compressed / reference_bytes
  measures how a codec compares to this reference, not to any theoretical limit.

Seed computation:
  sha256(f"cal2:{size}:{H}:{M}:{rep}")[:8]       → data_seed (uint64)
  sha256(f"cal2:perm:{size}:{H}:{M}:{rep}")[:8]  → perm_seed (uint64)
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import random
from pathlib import Path

from squishy.core.config import BuildConfig
from squishy.core.fs import write_bytes_atomic

# ── Corpus axes ────────────────────────────────────────────────────────────────

SIZES: list[tuple[str, int]] = [
    ("256K",  262144),
    ("4M",   4194304),
    ("64M", 67108864),
]

H_VALUES: list[float] = [1.0, 1.7, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
M_VALUES: list[float] = [0.00, 0.10, 0.25, 0.50, 0.75]
REPLICATES: list[str] = ["s0", "s1", "s2"]

# L-sweep: vary mean match length at fixed M (supplementary grid)
L_VALUES: list[float] = [3.0, 8.0, 32.0, 128.0]
L_SWEEP_M: float = 0.50

# LZ copy kernel parameters (pinned for reproducibility across corpus versions)
COPY_WINDOW: int   = 32768
COPY_MEAN_L: float = 8.0   # default for the main H×M grid


def _h_d() -> float:
    """H(log-uniform over {1, ..., COPY_WINDOW}). Window is fixed; compute once."""
    W = COPY_WINDOW
    H_W = sum(1.0 / d for d in range(1, W + 1))
    return sum((1.0 / d / H_W) * math.log2(H_W * d) for d in range(1, W + 1))


def _h_l(mean_L: float) -> float:
    """H(geometric with given mean). p = 1/mean_L."""
    p = 1.0 / mean_L
    return (-p * math.log2(p) - (1 - p) * math.log2(1 - p)) / p


def _copy_bits_per_byte(mean_L: float) -> float:
    """Per-byte cost of a copy token: (H_D + H_L) / mean_L."""
    return (_H_D + _h_l(mean_L)) / mean_L


_H_D: float = _h_d()
_COPY_BITS_PER_BYTE: float = _copy_bits_per_byte(COPY_MEAN_L)  # default L=8


def _build_log_uniform_cdf(W: int) -> list[float]:
    """CDF table for P(D=d) = (1/d)/H_W over d ∈ [1, W].

    _LOG_UNIFORM_CDF[d] = P(D ≤ d) for d in 0..W.
    Precomputed so sampling is O(log W) via bisect.
    """
    H_W = sum(1.0 / d for d in range(1, W + 1))
    cdf = [0.0] * (W + 1)
    for d in range(1, W + 1):
        cdf[d] = cdf[d - 1] + (1.0 / d) / H_W
    return cdf


_LOG_UNIFORM_CDF: list[float] = _build_log_uniform_cdf(COPY_WINDOW)


# ── Seed helpers ───────────────────────────────────────────────────────────────

def _make_seed(tag: str) -> int:
    return int.from_bytes(hashlib.sha256(tag.encode()).digest()[:8], "big")


def _data_seed(size: int, H: float, M: float, rep: str) -> int:
    return _make_seed(f"cal2:{size}:{H}:{M}:{rep}")


def _perm_seed(size: int, H: float, M: float, rep: str) -> int:
    return _make_seed(f"cal2:perm:{size}:{H}:{M}:{rep}")


def _data_seed_L(size: int, H: float, M: float, mean_L: float, rep: str) -> int:
    return _make_seed(f"cal2L:{size}:{H}:{M}:{mean_L}:{rep}")


def _perm_seed_L(size: int, H: float, M: float, mean_L: float, rep: str) -> int:
    return _make_seed(f"cal2L:perm:{size}:{H}:{M}:{mean_L}:{rep}")


# ── PMF construction ───────────────────────────────────────────────────────────

def tilted_pmf(H_target: float) -> list[float]:
    """Compute PMF p_i ∝ exp(-β·i) achieving Shannon entropy H_target (bits/byte).

    β=0 gives uniform (H=8); larger β gives lower entropy.
    Solved by bisection over β ∈ [0, 100] to precision < 1e-10 bits.
    """
    if H_target >= math.log2(256) - 1e-9:
        return [1.0 / 256] * 256

    def _H(beta: float) -> float:
        weights = [math.exp(-beta * i) for i in range(256)]
        Z = sum(weights)
        return -sum((w / Z) * math.log2(w / Z) for w in weights if w > 0)

    lo, hi = 0.0, 100.0
    for _ in range(128):
        mid = (lo + hi) / 2
        if _H(mid) > H_target:
            lo = mid
        else:
            hi = mid

    beta = (lo + hi) / 2
    weights = [math.exp(-beta * i) for i in range(256)]
    Z = sum(weights)
    return [w / Z for w in weights]


def pmf_entropy(pmf: list[float]) -> float:
    """Shannon entropy of a PMF in bits/symbol."""
    return -sum(p * math.log2(p) for p in pmf if p > 0)


# ── Reference-coder rate ───────────────────────────────────────────────────────

def reference_rate(H_marginal: float, M_frac: float,
                   mean_L: float = COPY_MEAN_L) -> float:
    """R_ref: bits/byte for a coder that knows the construction parse.

    Formula: R_ref = (1-M)·H_marginal + M·(H_D + H_L(mean_L))/mean_L

    Clamped at H_marginal: if H_marginal < copy cost, the reference coder
    uses literals only (skipping LZ is cheaper for nearly-uniform sources).

    This is NOT the Shannon entropy rate of the source process; it is the
    per-byte cost of the specific construction parse under arithmetic coding.
    A real LZ codec that finds the same parse adds entropy-coder overhead
    (~5-15%), so R_ref is an optimistic lower bound for real codecs.
    """
    cpb = _copy_bits_per_byte(mean_L)
    raw = (1.0 - M_frac) * H_marginal + M_frac * cpb
    return min(H_marginal, raw)


# ── Byte stream generation ─────────────────────────────────────────────────────

def _sample_log_uniform(rng: random.Random, lo: int, hi: int) -> int:
    """Draw exactly from P(D=d) ∝ 1/d over [lo, hi].

    For the full window [1, COPY_WINDOW] uses a precomputed CDF (O(log W)).
    For truncated ranges (near the start of file, where copy history < COPY_WINDOW)
    computes the CDF on-the-fly (O(hi)), which only applies to the first 32 KB.
    """
    if lo == 1 and hi == COPY_WINDOW:
        d = bisect.bisect_right(_LOG_UNIFORM_CDF, rng.random())
        return max(1, min(COPY_WINDOW, d))
    # Truncated range: exact CDF on-the-fly.
    H = sum(1.0 / d for d in range(lo, hi + 1))
    u = rng.random() * H
    acc = 0.0
    for d in range(lo, hi + 1):
        acc += 1.0 / d
        if acc >= u:
            return d
    return hi


def _p_start(M_frac: float, mean_L: float = COPY_MEAN_L) -> float:
    """Probability of starting a copy run so steady-state copy fraction = M_frac.

    In a Markov chain alternating between fresh bytes (geometric run of length 1)
    and copy runs (geometric length mean_L), the copy fraction satisfies:
      F = p_start·mean_L / (p_start·mean_L + (1-p_start))
    Solving for p_start given F = M_frac:
      p_start = M_frac / (mean_L - M_frac·(mean_L - 1))
    """
    if M_frac <= 0.0:
        return 0.0
    return M_frac / (mean_L - M_frac * (mean_L - 1))


def gen_iid(size: int, pmf: list[float], alphabet: list[int],
            rng: random.Random) -> bytearray:
    """Sample 'size' bytes i.i.d. from the PMF over the given alphabet."""
    return bytearray(rng.choices(alphabet, weights=pmf, k=size))


def apply_lz_duplication(buf: bytearray, M_frac: float,
                          rng: random.Random,
                          mean_L: float = COPY_MEAN_L) -> int:
    """Overwrite fraction ~M_frac of bytes in-place with LZ back-references.

    Algorithm: Markov chain — at each fresh byte, start a copy run with
    probability p_start (calibrated so steady-state copy fraction = M_frac).
    Each copy run: distance log-uniform [1, COPY_WINDOW], length geometric
    (p=1/mean_L, mean=mean_L). Overlapping copies are intentional (LZ standard).

    Uses geometric waiting times to skip fresh bytes efficiently — O(n·M)
    Python iterations rather than O(n), which is critical for large files.

    Returns the number of bytes replaced by copies (for realized_M tracking).
    """
    if M_frac <= 0.0:
        return 0
    ps = _p_start(M_frac, mean_L)
    if ps <= 0.0:
        return 0

    n = len(buf)
    pos = 1  # byte 0 is always fresh (no copy history)
    log_q = math.log(1.0 - ps)  # ln(1 - p_start); used for geometric sampling
    log_len_q = math.log(1.0 - 1.0 / mean_L)  # for geometric length sampling
    copy_bytes = 0

    while pos < n:
        # Geometric waiting time until next copy event
        # P(wait = k) = (1-ps)^k × ps  →  wait = floor(log(u) / log(1-ps))
        u = rng.random()
        if u <= 0.0:
            u = 1e-300
        wait = int(math.log(u) / log_q)
        pos += wait
        if pos >= n:
            break

        # Sample and apply copy run
        # Length: geometric(p=1/COPY_MEAN_L), support {1,2,...}, mean=COPY_MEAN_L
        D = _sample_log_uniform(rng, 1, min(pos, COPY_WINDOW))
        u_len = rng.random()
        if u_len <= 0.0:
            u_len = 1e-300
        L = math.ceil(math.log(u_len) / log_len_q)
        run_len = min(L, n - pos)
        src = pos - D
        copy_bytes += run_len

        if D >= run_len:
            # Non-overlapping: fast slice assignment (C-level)
            buf[pos:pos + run_len] = buf[src:src + run_len]
        else:
            # Overlapping: sequential copy preserves the repeating pattern
            for i in range(run_len):
                buf[pos + i] = buf[src + i]

        pos += run_len

    return copy_bytes


def generate_file(size: int, H: float, M: float, rep: str) -> bytes:
    """Generate one calibrated file deterministically. Returns raw bytes."""
    if H == 0.0:
        return b"\x00" * size

    pmf = tilted_pmf(H)
    data_rng = random.Random(_data_seed(size, H, M, rep))
    perm_rng = random.Random(_perm_seed(size, H, M, rep))

    alphabet = list(range(256))
    perm_rng.shuffle(alphabet)

    buf = gen_iid(size, pmf, alphabet, data_rng)
    apply_lz_duplication(
        buf, M,
        random.Random(_data_seed(size, H, M, rep) ^ 0xC0FFEE_BADF00D),
    )
    return bytes(buf)


# ── Ground-truth record ────────────────────────────────────────────────────────

def ground_truth_record(size_label: str, size: int, H: float, M: float,
                         rep: str, fname: str,
                         realized_M: float | None = None,
                         mean_L: float = COPY_MEAN_L) -> dict:
    pmf = tilted_pmf(H) if H > 0.0 else [1.0] + [0.0] * 255
    H_marginal = pmf_entropy(pmf)
    R_ref = reference_rate(H_marginal, M, mean_L)
    if mean_L == COPY_MEAN_L:
        d_seed = _data_seed(size, H, M, rep)
        p_seed = _perm_seed(size, H, M, rep)
    else:
        d_seed = _data_seed_L(size, H, M, mean_L, rep)
        p_seed = _perm_seed_L(size, H, M, mean_L, rep)
    r_ref_clamped = H_marginal < _COPY_BITS_PER_BYTE and M > 0.0
    rec = {
        "filename":        fname,
        "size_bytes":      size,
        "H_marginal":      round(H_marginal, 6),
        "R_ref":           round(R_ref, 6),
        "R_ref_clamped":   r_ref_clamped,
        "M_fraction":      M,
        "reference_bytes": math.ceil(R_ref * size / 8),
        "generator":       "calibrated_v3",
        "copy_window":     COPY_WINDOW,
        "copy_mean_length": mean_L,
        "data_seed":       d_seed,
        "perm_seed":       p_seed,
    }
    if realized_M is not None:
        rec["realized_M"] = round(realized_M, 5)
    return rec


# ── Main entry point ───────────────────────────────────────────────────────────

def run(cfg: BuildConfig) -> int:
    """Generate the full calibrated H×M grid. Returns 0 on success, 1 on failure."""
    try:
        out = cfg.raw_dir / "calibrated"
        out.mkdir(parents=True, exist_ok=True)

        records: list[dict] = []

        for size_label, size in SIZES:
            if size_label == "64M":
                continue  # disk budget: 64M corpus not generated here
            # zeros baseline
            fname_zeros = f"{size_label}-zeros-s0.bin"
            path_zeros = out / fname_zeros
            if not path_zeros.exists():
                write_bytes_atomic(path_zeros, b"\x00" * size)
                print(f"  {fname_zeros} ({size:,} bytes)")
            else:
                print(f"  skip {fname_zeros} (exists)")
            records.append({
                "filename": fname_zeros, "size_bytes": size,
                "H_marginal": 0.0, "R_ref": 0.0, "M_fraction": 0.0,
                "reference_bytes": 0, "generator": "zeros",
            })

            for H in H_VALUES:
                for M in M_VALUES:
                    for rep in REPLICATES:
                        H_str = f"H{H:.1f}".replace(".", "p")
                        M_str = f"M{M:.2f}".replace(".", "p")
                        fname = f"{size_label}-{H_str}-{M_str}-{rep}.bin"
                        path = out / fname
                        if path.exists():
                            print(f"  skip {fname} (exists)")
                            rec = ground_truth_record(size_label, size, H, M, rep, fname)
                        else:
                            pmf = tilted_pmf(H)
                            lz_rng = random.Random(
                                _data_seed(size, H, M, rep) ^ 0xC0FFEE_BADF00D
                            )
                            perm_rng = random.Random(_perm_seed(size, H, M, rep))
                            alphabet = list(range(256))
                            perm_rng.shuffle(alphabet)
                            buf = gen_iid(
                                size, pmf, alphabet,
                                random.Random(_data_seed(size, H, M, rep))
                            )
                            copy_count = apply_lz_duplication(buf, M, lz_rng)
                            data = bytes(buf)
                            realized_M = copy_count / size if size > 0 else 0.0
                            write_bytes_atomic(path, data)
                            print(f"  {fname} ({len(data):,} bytes, realized_M={realized_M:.3f})")
                            rec = ground_truth_record(
                                size_label, size, H, M, rep, fname, realized_M
                            )
                        records.append(rec)

        # ── L-sweep: H × mean_L grid at fixed M = L_SWEEP_M ──────────────────
        # Supplementary grid varying match length distribution.
        # Files named: {size}-{H_str}-M0p50-L{L}-{rep}.bin
        for size_label, size in SIZES:
            if size_label == "64M":
                continue  # skip 64M for L-sweep (disk budget)
            for H in H_VALUES:
                for mean_L in L_VALUES:
                    for rep in REPLICATES:
                        H_str = f"H{H:.1f}".replace(".", "p")
                        M_str = f"M{L_SWEEP_M:.2f}".replace(".", "p")
                        L_str = f"L{int(mean_L)}"
                        fname = f"{size_label}-{H_str}-{M_str}-{L_str}-{rep}.bin"
                        path = out / fname
                        if path.exists():
                            print(f"  skip {fname} (exists)")
                            rec = ground_truth_record(
                                size_label, size, H, L_SWEEP_M, rep, fname,
                                mean_L=mean_L,
                            )
                        else:
                            pmf = tilted_pmf(H)
                            lz_rng = random.Random(
                                _data_seed_L(size, H, L_SWEEP_M, mean_L, rep)
                                ^ 0xC0FFEE_BADF00D
                            )
                            perm_rng = random.Random(
                                _perm_seed_L(size, H, L_SWEEP_M, mean_L, rep)
                            )
                            alphabet = list(range(256))
                            perm_rng.shuffle(alphabet)
                            buf = gen_iid(
                                size, pmf, alphabet,
                                random.Random(
                                    _data_seed_L(size, H, L_SWEEP_M, mean_L, rep)
                                ),
                            )
                            copy_count = apply_lz_duplication(
                                buf, L_SWEEP_M, lz_rng, mean_L=mean_L
                            )
                            data = bytes(buf)
                            realized_M = copy_count / size if size > 0 else 0.0
                            write_bytes_atomic(path, data)
                            print(
                                f"  {fname} ({len(data):,} bytes,"
                                f" realized_M={realized_M:.3f})"
                            )
                            rec = ground_truth_record(
                                size_label, size, H, L_SWEEP_M, rep, fname,
                                realized_M, mean_L=mean_L,
                            )
                        records.append(rec)

        gt_path = out / "ground-truth.json"
        write_bytes_atomic(gt_path, json.dumps(records, indent=2).encode())
        print(f"  calibrated: {len(records)} records written to {out}")
        return 0

    except Exception as exc:
        import traceback
        print(f"  ERROR in calibrated: {exc}")
        traceback.print_exc()
        return 1
