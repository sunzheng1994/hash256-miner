"""Challenge construction and PoW hashing (Ethereum Keccak-256, not SHA3-256)."""

from __future__ import annotations

import re
from typing import Union

from Crypto.Hash import keccak


def _addr_to_bytes20(addr: Union[str, bytes]) -> bytes:
    if isinstance(addr, bytes):
        if len(addr) != 20:
            raise ValueError("address bytes must be length 20")
        return addr
    s = addr.strip()
    if not re.fullmatch(r"0x[a-fA-F0-9]{40}", s):
        raise ValueError(f"invalid EVM address: {addr!r}")
    return bytes.fromhex(s[2:])


def _u256_to_bytes32(value: int) -> bytes:
    if value < 0 or value >= 1 << 256:
        raise ValueError("uint256 out of range")
    return value.to_bytes(32, "big")


def encode_challenge_preimage(
    chain_id: int,
    contract_address: Union[str, bytes],
    miner_address: Union[str, bytes],
    epoch: int,
) -> bytes:
    """
    Solidity: keccak256(abi.encodePacked(
        uint256 chainId,
        address contractAddress,
        address minerAddress,
        uint256 epoch
    ))
    Packed layout: 32 + 20 + 20 + 32 = 104 bytes.
    """
    c = _addr_to_bytes20(contract_address)
    m = _addr_to_bytes20(miner_address)
    return _u256_to_bytes32(chain_id) + c + m + _u256_to_bytes32(epoch)


def compute_challenge(
    chain_id: int,
    contract_address: Union[str, bytes],
    miner_address: Union[str, bytes],
    epoch: int,
) -> bytes:
    """Returns 32-byte challenge (bytes32)."""
    pre = encode_challenge_preimage(chain_id, contract_address, miner_address, epoch)
    h = keccak.new(digest_bits=256)
    h.update(pre)
    return h.digest()


def encode_pow_preimage(challenge_bytes32: bytes, nonce: int) -> bytes:
    """
    Solidity: keccak256(abi.encodePacked(bytes32 challenge, uint256 nonce))
    Packed layout: 32 + 32 = 64 bytes.
    """
    if len(challenge_bytes32) != 32:
        raise ValueError("challenge must be 32 bytes")
    return challenge_bytes32 + _u256_to_bytes32(nonce)


def uint256_from_prefix_counter(prefix24: bytes, counter: int) -> int:
    """
    Build uint256 nonce the same way as hash-cli-miner CUDA: high 192 bits from
    ``prefix24``, low 64 bits from ``counter`` (big-endian in the last 8 bytes).
    """
    if len(prefix24) != 24:
        raise ValueError("prefix must be 24 bytes")
    if counter < 0 or counter > (1 << 64) - 1:
        raise ValueError("counter must fit uint64")
    return int.from_bytes(prefix24 + counter.to_bytes(8, "big"), "big")


def encode_pow_preimage_prefix_counter(challenge_bytes32: bytes, prefix24: bytes, counter: int) -> bytes:
    """64-byte preimage: challenge || prefix24 || counter_be_8 (same packed layout as uint256 nonce)."""
    if len(challenge_bytes32) != 32:
        raise ValueError("challenge must be 32 bytes")
    n = uint256_from_prefix_counter(prefix24, counter)
    return encode_pow_preimage(challenge_bytes32, n)


def pow_hash_prefix_counter(challenge_bytes32: bytes, prefix24: bytes, counter: int) -> bytes:
    """Keccak-256 digest for PoW using prefix+counter nonce layout."""
    pre = encode_pow_preimage_prefix_counter(challenge_bytes32, prefix24, counter)
    h = keccak.new(digest_bits=256)
    h.update(pre)
    return h.digest()


def pow_hash(challenge_bytes32: bytes, nonce: int) -> bytes:
    """Returns 32-byte digest for PoW comparison."""
    pre = encode_pow_preimage(challenge_bytes32, nonce)
    h = keccak.new(digest_bits=256)
    h.update(pre)
    return h.digest()


def digest_less_than_difficulty(digest: bytes, difficulty: int) -> bool:
    """Interpret digest as big-endian uint256; valid if digest < difficulty."""
    if len(digest) != 32:
        raise ValueError("digest must be 32 bytes")
    if difficulty < 0 or difficulty > (1 << 256):
        raise ValueError("difficulty out of uint256 range")
    d = int.from_bytes(digest, "big")
    return d < difficulty


def mine_batch(
    challenge_bytes32: bytes,
    start_nonce: int,
    count: int,
    difficulty: int,
) -> int | None:
    """
    Linear scan [start_nonce, start_nonce + count). Returns first valid nonce or None.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    if start_nonce < 0 or start_nonce + count > 1 << 256:
        raise ValueError("nonce range overflow uint256")
    end = start_nonce + count
    n = start_nonce
    while n < end:
        if digest_less_than_difficulty(pow_hash(challenge_bytes32, n), difficulty):
            return n
        n += 1
    return None


def mine_prefix_counter_range(
    challenge_bytes32: bytes,
    prefix24: bytes,
    start_counter: int,
    count: int,
    difficulty: int,
) -> int | None:
    """Linear scan counters [start_counter, start_counter + count); returns full uint256 nonce or None."""
    if len(prefix24) != 24:
        raise ValueError("prefix must be 24 bytes")
    if count < 0:
        raise ValueError("count must be non-negative")
    if start_counter < 0 or start_counter + count > 1 << 64:
        raise ValueError("counter range overflow uint64")
    c = start_counter
    end = start_counter + count
    while c < end:
        if digest_less_than_difficulty(pow_hash_prefix_counter(challenge_bytes32, prefix24, c), difficulty):
            return uint256_from_prefix_counter(prefix24, c)
        c += 1
    return None
