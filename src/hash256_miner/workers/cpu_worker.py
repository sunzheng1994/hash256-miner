"""CPU PoW search (multiprocessing-friendly entrypoint)."""

from __future__ import annotations

from hash256_miner.challenge import digest_less_than_difficulty, pow_hash


def stride_search(
    challenge: bytes,
    difficulty: int,
    start: int,
    end: int,
    stride: int,
    offset: int,
) -> tuple[int | None, int]:
    """
    Visit nonces start+offset, start+offset+stride, ... while < end.
    Returns (found_nonce or None, hashes_tried).
    """
    if stride <= 0:
        raise ValueError("stride must be positive")
    tried = 0
    n = start + offset
    while n < end:
        tried += 1
        if digest_less_than_difficulty(pow_hash(challenge, n), difficulty):
            return n, tried
        n += stride
    return None, tried


def contiguous_search(
    challenge: bytes,
    difficulty: int,
    start: int,
    count: int,
) -> tuple[int | None, int]:
    """Linear range [start, start+count)."""
    end = start + count
    n = start
    tried = 0
    while n < end:
        tried += 1
        if digest_less_than_difficulty(pow_hash(challenge, n), difficulty):
            return n, tried
        n += 1
    return None, tried
