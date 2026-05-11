"""Pre-flight checks using eth_call."""

from __future__ import annotations

from typing import Optional

from web3 import Web3
from web3.contract import Contract

from hash256_miner.challenge import digest_less_than_difficulty, pow_hash


def local_pow_valid(challenge: bytes, nonce: int, difficulty: int) -> bool:
    d = pow_hash(challenge, nonce)
    return digest_less_than_difficulty(d, difficulty)


def eth_call_mine(
    w3: Web3,
    contract: Contract,
    *,
    nonce: int,
    from_address: str,
    gas: int = 2_000_000,
) -> None:
    """
    Simulates mine(nonce) via eth_call. Raises ContractLogicError on revert.
    """
    contract.functions.mine(int(nonce)).call(
        {
            "from": Web3.to_checksum_address(from_address),
            "gas": gas,
        }
    )


def verify_nonce_or_raise(
    w3: Web3,
    contract: Contract,
    *,
    miner_address: str,
    challenge: bytes,
    nonce: int,
    difficulty: int,
    skip_eth_call: bool = False,
) -> None:
    if not local_pow_valid(challenge, nonce, difficulty):
        raise ValueError("local PoW check failed for nonce")
    if skip_eth_call:
        return
    eth_call_mine(w3, contract, nonce=nonce, from_address=miner_address)


def try_eth_call_mine(
    w3: Web3,
    contract: Contract,
    *,
    nonce: int,
    from_address: str,
) -> Optional[str]:
    """
    Returns None on success, or an error string if eth_call fails
    (still might be valid locally if contract has extra checks).
    """
    try:
        eth_call_mine(w3, contract, nonce=nonce, from_address=from_address)
        return None
    except Exception as e:  # noqa: BLE001 - surface message to operator
        return f"{type(e).__name__}: {e}"
