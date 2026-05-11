"""Layout and length checks for abi.encodePacked-style preimages."""

from hash256_miner.challenge import (
    compute_challenge,
    encode_challenge_preimage,
    encode_pow_preimage,
)


def test_challenge_preimage_length():
    pre = encode_challenge_preimage(
        chain_id=1,
        contract_address="0x" + "11" * 20,
        miner_address="0x" + "22" * 20,
        epoch=3,
    )
    assert len(pre) == 104
    assert pre[:32] == (1).to_bytes(32, "big")
    assert pre[32:52] == bytes.fromhex("11" * 20)
    assert pre[52:72] == bytes.fromhex("22" * 20)
    assert pre[72:] == (3).to_bytes(32, "big")


def test_pow_preimage_length():
    ch = bytes(range(32))
    pre = encode_pow_preimage(ch, (1 << 255) + 123)
    assert len(pre) == 64
    assert pre[:32] == ch
    assert pre[32:] == ((1 << 255) + 123).to_bytes(32, "big")


def test_compute_challenge_deterministic():
    c1 = compute_challenge(5, "0x" + "aa" * 20, "0x" + "bb" * 20, 9)
    c2 = compute_challenge(5, "0x" + "aa" * 20, "0x" + "bb" * 20, 9)
    assert c1 == c2
    assert len(c1) == 32
