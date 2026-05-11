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


def _function_names(full_abi: list[dict[str, Any]]) -> set[str]:
    return {
        str(x["name"])
        for x in full_abi
        if isinstance(x, dict) and x.get("type") == "function" and x.get("name")
    }


def _try_call_uint(contract: Contract, name: str) -> Optional[int]:
    try:
        return int(getattr(contract.functions, name)().call())
    except Exception:
        return None


def _pick_difficulty(contract: Contract, names: set[str]) -> int:
    """``difficulty()`` 与 ``currentDifficulty()`` 语义相同，selector 不同，二者择一。"""
    if "difficulty" in names:
        v = _try_call_uint(contract, "difficulty")
        if v is not None:
            return v
    if "currentDifficulty" in names:
        v = _try_call_uint(contract, "currentDifficulty")
        if v is not None:
            return v
    raise RuntimeError("合约中无法读取难度：需要可成功的 difficulty() 或 currentDifficulty()")


def _epoch_candidates(contract: Contract, names: set[str]) -> list[tuple[str, int]]:
    """用于与 ``getChallenge`` 对齐的 epoch uint256 候选（encodePacked 第三项）。"""
    out: list[tuple[str, int]] = []
    if "epoch" in names:
        v = _try_call_uint(contract, "epoch")
        if v is not None:
            out.append(("epoch", v))
    if "epochBlocksLeft" in names:
        v = _try_call_uint(contract, "epochBlocksLeft")
        if v is not None:
            out.append(("epochBlocksLeft", v))
    if "EPOCH_BLOCKS" in names and "epochBlocksLeft" in names:
        eb = _try_call_uint(contract, "EPOCH_BLOCKS")
        ebl = _try_call_uint(contract, "epochBlocksLeft")
        if eb is not None and ebl is not None:
            out.append(("EPOCH_BLOCKS_minus_epochBlocksLeft", eb - ebl))
    return out


@dataclass(frozen=True)
class MiningState:
    chain_id: int
    block_number: int
    epoch: int
    difficulty: int
    challenge: bytes
    challenge_source: str  # "onchain" | "local"（onchain 含「与本地推导一致」或「仅信 getChallenge」）


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
    读取难度与 challenge。

    若存在 ``getChallenge(miner)``：优先用本地 ``compute_challenge`` 与若干 epoch 候选对齐；
    若无法对齐（合约 encode 与仓库假设不同），**仍采用链上返回的 bytes32** 作为 PoW challenge，
    ``MiningState.epoch`` 仅作展示（优先 ``epochBlocksLeft`` / ``epoch``）。
    """
    chain_id = int(w3.eth.chain_id)
    block_number = int(w3.eth.block_number)
    miner_cs = Web3.to_checksum_address(miner_address)
    full_abi = _load_abi(abi_path)
    names = _function_names(full_abi)

    difficulty = _pick_difficulty(contract, names)
    cands = _epoch_candidates(contract, names)

    onchain_challenge: Optional[bytes] = None
    if "getChallenge" in names and prefer_onchain_challenge:
        try:
            raw = contract.functions.getChallenge(miner_cs).call()
            if isinstance(raw, (bytes, bytearray)) and len(raw) == 32:
                onchain_challenge = bytes(raw)
        except Exception:
            onchain_challenge = None

    if onchain_challenge is not None and len(onchain_challenge) == 32:
        matched: Optional[int] = None
        for _, ev in cands:
            if compute_challenge(chain_id, contract.address, miner_cs, ev) == onchain_challenge:
                matched = ev
                break
        if matched is not None:
            return MiningState(
                chain_id=chain_id,
                block_number=block_number,
                epoch=matched,
                difficulty=difficulty,
                challenge=onchain_challenge,
                challenge_source="onchain",
            )
        # 与本地 encodePacked 不一致：以链上 challenge 为准继续 PoW
        epoch_fb = _try_call_uint(contract, "epochBlocksLeft")
        if epoch_fb is None:
            epoch_fb = _try_call_uint(contract, "epoch")
        epoch_val = epoch_fb if epoch_fb is not None else 0
        return MiningState(
            chain_id=chain_id,
            block_number=block_number,
            epoch=epoch_val,
            difficulty=difficulty,
            challenge=onchain_challenge,
            challenge_source="onchain",
        )

    if not cands:
        raise RuntimeError(
            "合约中无法推断 epoch：需要 epoch()，或 epochBlocksLeft()，或 EPOCH_BLOCKS()+epochBlocksLeft()"
        )
    epoch = cands[0][1]
    challenge = compute_challenge(chain_id, contract.address, miner_cs, epoch)
    return MiningState(
        chain_id=chain_id,
        block_number=block_number,
        epoch=epoch,
        difficulty=difficulty,
        challenge=challenge,
        challenge_source="local",
    )
