"""CPU stride worker finds a PoW solution under an easy target."""

from hash256_miner.challenge import compute_challenge, digest_less_than_difficulty, pow_hash
from hash256_miner.workers.cpu_worker import stride_search


def test_stride_search_finds_known_easy_target():
    ch = compute_challenge(1, "0x" + "11" * 20, "0x" + "22" * 20, 1)
    # Largest uint256: every digest except exactly 0xff..ff satisfies digest < D.
    diff = (1 << 256) - 1
    expected = None
    for cand in range(20):
        if digest_less_than_difficulty(pow_hash(ch, cand), diff):
            expected = cand
            break
    assert expected is not None
    n, tried = stride_search(ch, diff, 0, expected + 50_000, 7, 0)
    assert n == expected
    assert tried > 0
