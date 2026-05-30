"""Empirical validation of fixed C/W/K definitions before implementing.

Tests the reviewer's two fatal/major findings:
  K: does an EXACT-context (no hash) prequential measure produce a real
     gradient across source orders, and what depth is learnable at 4 MB?
  W: does length-weighting + a C-floor stop random data landing at high W?
"""
import collections, math, hashlib, os, sys

W_LOCAL = 64*1024
MIN_MATCH = 4

def lz_parse(data, window):
    """Greedy single-entry LZ. Returns (matched_bytes, list of (length, offset))."""
    n = len(data); table = {}; matched = 0; i = 0; matches = []
    while i < n - MIN_MATCH:
        h = data[i] | (data[i+1]<<8) | (data[i+2]<<16) | (data[i+3]<<24)
        prev = table.get(h); table[h] = i
        if prev is not None:
            off = i - prev
            if 0 < off <= window:
                j = MIN_MATCH; limit = min(n-i, n-prev, 65536)
                while j < limit and data[prev+j] == data[i+j]:
                    j += 1
                matched += j; matches.append((j, off)); i += j; continue
        i += 1
    return matched, matches

def measure_C(data):
    matched, _ = lz_parse(data, len(data))
    return matched / len(data)

def measure_W(data, min_match_len_for_W=8, c_floor=0.05):
    """Length-weighted mean log2(offset), only counting matches >= threshold.
    Returns (W, C). W=None if C below floor."""
    matched, matches = lz_parse(data, len(data))
    C = matched / len(data)
    if C < c_floor:
        return None, C
    num = 0.0; den = 0.0
    for (L, off) in matches:
        if L >= min_match_len_for_W:
            num += L * math.log2(off); den += L
    if den == 0:
        return None, C
    return (num/den)/math.log2(len(data)), C

def prequential_H(data, order):
    """Exact-context prequential log-loss (bpb) at given order. No hashing."""
    n = len(data); alpha = 0.5
    counts = collections.defaultdict(lambda: [0]*256)
    totals = collections.defaultdict(int)
    loss = 0.0
    for t in range(n):
        ctx = data[max(0,t-order):t] if order > 0 else b''
        cnt = counts[ctx]; tot = totals[ctx]
        x = data[t]
        p = (cnt[x] + alpha) / (tot + 256*alpha)
        loss += -math.log2(p)
        cnt[x] += 1; totals[ctx] = tot + 1
    return loss / n

def measure_K(data):
    H = {h: prequential_H(data, h) for h in (0,1,2,3)}
    K_vs1 = max(0.0, (H[1]-H[3])/H[1]) if H[1] > 0 else 0.0
    K_vs0 = max(0.0, (H[0]-H[3])/H[0]) if H[0] > 0 else 0.0
    return H, K_vs1, K_vs0

# ---- synthetic sources ----
def rng_bytes(seed, n):
    out = bytearray()
    s = hashlib.shake_256(seed).digest(n)
    return bytes(s)

def small_alpha_markov(seed, n, order, alphabet=6, peak=0.9):
    """Order-k Markov over a small alphabet with peaked transitions.
    Deterministic-ish high-order structure that recurs densely at 4 MB."""
    import random
    rng = random.Random(int.from_bytes(hashlib.sha256(seed).digest()[:8],'big'))
    # build a fixed peaked PMF per context via hashing
    syms = list(range(alphabet))
    state = bytes([0]*order)
    out = bytearray()
    for _ in range(n):
        h = hashlib.shake_256(seed + b'|' + state).digest(alphabet)
        ranked = sorted(range(alphabet), key=lambda i: h[i], reverse=True)
        # peaked: top symbol gets `peak`, rest share remainder
        weights = [0.0]*alphabet
        weights[ranked[0]] = peak
        for r in ranked[1:]:
            weights[r] = (1-peak)/(alphabet-1)
        b = rng.choices(syms, weights=weights)[0]
        out.append(b)
        if order:
            state = state[1:] + bytes([b])
    return bytes(out)

def order1_256(seed, n, peak=0.5):
    import random
    rng = random.Random(int.from_bytes(hashlib.sha256(seed).digest()[:8],'big'))
    state = 0; out = bytearray()
    for _ in range(n):
        h = hashlib.shake_256(seed + bytes([state])).digest(256)
        ranked = sorted(range(256), key=lambda i: h[i], reverse=True)
        weights = [0.0]*256
        weights[ranked[0]] = peak
        for r in ranked[1:]:
            weights[r] = (1-peak)/255
        b = rng.choices(range(256), weights=weights)[0]
        out.append(b); state = b
    return bytes(out)

N = 1_000_000  # 1MB for speed in experiment

def report(name, data):
    C = measure_C(data)
    W, _ = measure_W(data)
    H, K1, K0 = measure_K(data)
    print(f"{name:32s} C={C:.4f} W={'  None' if W is None else f'{W:.3f}'} "
          f"H0={H[0]:.2f} H1={H[1]:.2f} H2={H[2]:.2f} H3={H[3]:.2f} "
          f"K(vs1)={K1:.3f} K(vs0)={K0:.3f}")

print(f"=== synthetic sources ({N//1000}KB each) ===")
report("uniform random", rng_bytes(b'rand', N))
report("order1 256alpha peak0.5", order1_256(b'o1', N, 0.5))
report("order2 6alpha peak0.85", small_alpha_markov(b'o2', N, 2, 6, 0.85))
report("order3 6alpha peak0.85", small_alpha_markov(b'o3', N, 3, 6, 0.85))
report("order3 16alpha peak0.7", small_alpha_markov(b'o3b', N, 3, 16, 0.7))
report("order0 6alpha (no ctx)", small_alpha_markov(b'o0', N, 0, 6, 0.85))

print(f"\n=== natural text (first {N//1000}KB) ===")
for fn in ('dickens','webster','reymont','mozilla'):
    p = f"/Users/jackdanger/www/squishy-corpus/build/raw/silesia/{fn}"
    if os.path.exists(p):
        with open(p,'rb') as f: d = f.read(N)
        report(fn, d)
