"""CPU stride worker finds a PoW solution under an easy target (prefix ‖ counter layout)."""

from hash256_miner.challenge import (
    compute_challenge,
    digest_less_than_difficulty,
    pow_hash_prefix_counter,
    uint256_from_prefix_counter,
)
from hash256_miner.workers.cpu_worker import stride_search_prefix


def test_stride_search_prefix_finds_known_easy_target():
    ch = compute_challenge(1, "0x" + "11" * 20, "0x" + "22" * 20, 1)
    # Largest uint256: every digest except exactly 0xff..ff satisfies digest < D.
    diff = (1 << 256) - 1
    zero = b"\x00" * 24
    expected = None
    for cand in range(20):
        if digest_less_than_difficulty(pow_hash_prefix_counter(ch, zero, cand), diff):
            expected = cand
            break
    assert expected is not None
    n, tried = stride_search_prefix(ch, diff, zero, 0, expected + 50_000, 7, 0)
    assert n == uint256_from_prefix_counter(zero, expected)
    assert tried > 0
