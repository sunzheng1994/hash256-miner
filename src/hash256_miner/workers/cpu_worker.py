"""CPU PoW search (multiprocessing-friendly entrypoint)."""

from __future__ import annotations

from hash256_miner.challenge import (
    digest_less_than_difficulty,
    pow_hash,
    pow_hash_prefix_counter,
    uint256_from_prefix_counter,
)


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


def stride_search_prefix(
    challenge: bytes,
    difficulty: int,
    prefix24: bytes,
    start_counter: int,
    end_counter: int,
    stride: int,
    offset: int,
) -> tuple[int | None, int]:
    """
    Same as stride_search but nonces are uint256 from ``prefix24 || counter`` (uint64 counter, BE tail).
    """
    if len(prefix24) != 24:
        raise ValueError("prefix24 must be 24 bytes")
    if stride <= 0:
        raise ValueError("stride must be positive")
    tried = 0
    c = start_counter + offset
    while c < end_counter:
        tried += 1
        if digest_less_than_difficulty(pow_hash_prefix_counter(challenge, prefix24, c), difficulty):
            return uint256_from_prefix_counter(prefix24, c), tried
        c += stride
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
