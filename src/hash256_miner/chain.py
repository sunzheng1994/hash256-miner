"""Read chain + contract state via web3.py."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Union

from web3 import Web3
from web3.contract import Contract

from hash256_miner.challenge import compute_challenge


def _load_abi(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _pick_abi_functions(full_abi: list[dict[str, Any]], names: set[str]) -> list[dict[str, Any]]:
    return [x for x in full_abi if isinstance(x, dict) and x.get("name") in names and x.get("type") == "function"]


@dataclass(frozen=True)
class MiningState:
    chain_id: int
    block_number: int
    epoch: int
    difficulty: int
    challenge: bytes
    challenge_source: str  # "onchain" | "local"


def connect(rpc_url: str) -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 60}))
    if not w3.is_connected():
        raise RuntimeError(f"failed to connect to RPC: {rpc_url!r}")
    return w3


def load_contract(w3: Web3, address: Union[str, bytes], abi_path: Path) -> Contract:
    full = _load_abi(abi_path)
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=full)


def read_mining_state(
    w3: Web3,
    contract: Contract,
    miner_address: str,
    *,
    abi_path: Path,
    prefer_onchain_challenge: bool = True,
) -> MiningState:
    """
    Reads epoch/difficulty from the contract and computes or fetches challenge.

    If `getChallenge(address)` exists and `prefer_onchain_challenge` is True,
    compares with locally computed challenge (must match if both succeed).
    """
    chain_id = int(w3.eth.chain_id)
    block_number = int(w3.eth.block_number)
    miner_cs = Web3.to_checksum_address(miner_address)

    epoch = int(contract.functions.epoch().call())
    diff_raw = contract.functions.difficulty().call()
    difficulty = int(diff_raw)

    local_challenge = compute_challenge(
        chain_id,
        contract.address,
        miner_cs,
        epoch,
    )

    challenge = local_challenge
    source = "local"

    if prefer_onchain_challenge:
        full_abi = _load_abi(abi_path)
        minimal = _pick_abi_functions(
            full_abi,
            {"getChallenge"},
        )
        if minimal:
            try:
                c2 = w3.eth.contract(address=contract.address, abi=minimal)
                onchain = c2.functions.getChallenge(miner_cs).call()
            except Exception:
                onchain = None
            if isinstance(onchain, (bytes, bytearray)) and len(onchain) == 32:
                if bytes(onchain) != local_challenge:
                    raise RuntimeError(
                        "on-chain getChallenge != locally computed challenge; "
                        "check ABI, address packing, or mining rules."
                    )
                challenge = bytes(onchain)
                source = "onchain"

    return MiningState(
        chain_id=chain_id,
        block_number=block_number,
        epoch=epoch,
        difficulty=difficulty,
        challenge=challenge,
        challenge_source=source,
    )
