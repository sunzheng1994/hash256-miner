"""Vectors against pycryptodome Keccak-256 and difficulty comparison."""

from Crypto.Hash import keccak

from hash256_miner.challenge import (
    compute_challenge,
    digest_less_than_difficulty,
    encode_pow_preimage,
    pow_hash,
    pow_hash_prefix_counter,
    uint256_from_prefix_counter,
)


def test_keccak_empty_matches_reference():
    k = keccak.new(digest_bits=256)
    k.update(b"")
    assert k.hexdigest() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"


def test_pow_hash_matches_direct_keccak():
    challenge = compute_challenge(
        chain_id=31337,
        contract_address="0x" + "01" * 20,
        miner_address="0x" + "02" * 20,
        epoch=42,
    )
    nonce = 999888777666
    pre = encode_pow_preimage(challenge, nonce)
    k = keccak.new(digest_bits=256)
    k.update(pre)
    assert pow_hash(challenge, nonce) == k.digest()


def test_difficulty_boundary():
    digest = (12345).to_bytes(32, "big")
    assert digest_less_than_difficulty(digest, 12346) is True
    assert digest_less_than_difficulty(digest, 12345) is False
    assert digest_less_than_difficulty(digest, 12344) is False


def test_max_difficulty_always_valid():
    digest = ((1 << 256) - 1).to_bytes(32, "big")
    assert digest_less_than_difficulty(digest, 1 << 256) is True


def test_prefix_counter_matches_uint256_pow_hash():
    challenge = compute_challenge(
        chain_id=31337,
        contract_address="0x" + "01" * 20,
        miner_address="0x" + "02" * 20,
        epoch=42,
    )
    prefix = bytes(range(24))
    for counter in (0, 1, 12345, (1 << 64) - 1):
        n = uint256_from_prefix_counter(prefix, counter)
        assert pow_hash(challenge, n) == pow_hash_prefix_counter(challenge, prefix, counter)
