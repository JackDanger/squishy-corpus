"""Reference vectors for SquishyPRNG. These pin the byte stream forever — if any
of these change, regenerate-from-seed reproducibility is broken (the whole point
of replacing random.Random for synthesize-on-demand corpus files)."""
from squishy.core.prng import SquishyPRNG


def test_reference_randbytes():
    assert SquishyPRNG("squishy-2026").randbytes(16).hex() == "168d8443a4f8d8d5891b6b0320e1891e"


def test_reference_random_float():
    assert SquishyPRNG("squishy-2026").random() == 0.08809687282061718


def test_reference_randints():
    p = SquishyPRNG("squishy-2026")
    assert [p.randint(0, 255) for _ in range(8)] == [22, 141, 132, 67, 164, 248, 216, 213]


def test_reference_weighted_choices():
    p = SquishyPRNG("squishy-2026")
    assert p.choices(["a", "b", "c"], [1, 1, 8], k=10) == \
        ["a", "c", "c", "c", "c", "c", "c", "a", "c", "a"]


def test_deterministic_across_instances():
    assert SquishyPRNG(42).randbytes(32) == SquishyPRNG(42).randbytes(32)


def test_seed_types_equivalent_for_int():
    # int seed uses decimal-ASCII encoding, documented in the spec
    assert SquishyPRNG(42).randbytes(8) == SquishyPRNG("42").randbytes(8)


def test_randint_range_bounds():
    p = SquishyPRNG("range")
    for _ in range(500):
        v = p.randint(3, 7)
        assert 3 <= v <= 7
