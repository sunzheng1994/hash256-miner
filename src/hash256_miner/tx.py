"""Build, sign, and broadcast mine() transactions."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

from eth_account import Account
from web3 import Web3
from web3.contract import Contract

from hash256_miner.flashbots_bundle import send_bundle_single_tx

log = logging.getLogger(__name__)

# Ethereum mainnet Flashbots Protect (private tx submission path; standard JSON-RPC).
FLASHBOTS_PROTECT_MAINNET_RPC = "https://rpc.flashbots.net"


def load_private_key(
    *,
    private_key: Optional[str] = None,
    key_file: Optional[Path] = None,
    env_var: str = "HASH256_PRIVATE_KEY",
) -> str:
    if private_key:
        k = private_key.strip()
        if k.startswith("0x"):
            k = k[2:]
        return k
    if key_file is not None:
        raw = key_file.read_text(encoding="utf-8").strip()
        if raw.startswith("0x"):
            raw = raw[2:]
        return raw
    env = os.environ.get(env_var)
    if env:
        e = env.strip()
        if e.startswith("0x"):
            e = e[2:]
        return e
    raise ValueError(
        "missing signing material: pass --private-key, --key-file, or set "
        f"{env_var} in the environment"
    )


def _legacy_fees(w3: Web3) -> dict[str, int]:
    return {"gasPrice": int(w3.eth.gas_price)}


def _eip1559_fees(
    w3: Web3,
    max_fee_per_gas: Optional[int],
    priority_fee: Optional[int],
) -> dict[str, int]:
    block = w3.eth.get_block("latest")
    base = block.get("baseFeePerGas")
    if base is None:
        return _legacy_fees(w3)
    base = int(base)
    prio = int(priority_fee) if priority_fee is not None else int(w3.eth.max_priority_fee)
    cap = int(max_fee_per_gas) if max_fee_per_gas is not None else base * 2 + prio
    return {"maxPriorityFeePerGas": prio, "maxFeePerGas": cap}


def build_signed_mine_raw(
    w3: Web3,
    contract: Contract,
    *,
    private_key_hex: str,
    miner_address: str,
    pow_nonce: int,
    chain_id: int,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    priority_fee: Optional[int] = None,
    tx_nonce: Optional[int] = None,
) -> bytes:
    """Build and sign ``mine(pow_nonce)``; returns raw signed tx bytes."""
    acct = Account.from_key(private_key_hex)
    from_addr = Web3.to_checksum_address(miner_address)
    if Web3.to_checksum_address(acct.address) != from_addr:
        raise ValueError("private key does not match --address / miner address")

    fn = contract.functions.mine(int(pow_nonce))
    if gas_limit is None:
        try:
            gas_limit = int(fn.estimate_gas({"from": from_addr}))
        except Exception:
            gas_limit = 400_000
        gas_limit = min(int(gas_limit * 1.25) + 50_000, 15_000_000)

    fees: dict[str, Any] = _eip1559_fees(w3, max_fee_per_gas, priority_fee)
    if "gasPrice" in fees:
        tx_fields: dict[str, Any] = {
            "from": from_addr,
            "nonce": int(tx_nonce) if tx_nonce is not None else int(w3.eth.get_transaction_count(from_addr, "pending")),
            "chainId": int(chain_id),
            "gas": int(gas_limit),
            "gasPrice": int(fees["gasPrice"]),
        }
    else:
        tx_fields = {
            "from": from_addr,
            "nonce": int(tx_nonce) if tx_nonce is not None else int(w3.eth.get_transaction_count(from_addr, "pending")),
            "chainId": int(chain_id),
            "gas": int(gas_limit),
            "maxFeePerGas": int(fees["maxFeePerGas"]),
            "maxPriorityFeePerGas": int(fees["maxPriorityFeePerGas"]),
        }

    built = fn.build_transaction(tx_fields)
    signed = acct.sign_transaction(built)
    raw = getattr(signed, "raw_transaction", None) or getattr(signed, "rawTransaction", None)
    if raw is None:
        raise RuntimeError("signed transaction missing raw bytes")
    return raw


def send_mine_flashbots_bundle(
    w3: Web3,
    contract: Contract,
    *,
    private_key_hex: str,
    flashbots_auth_private_key_hex: str,
    miner_address: str,
    pow_nonce: int,
    chain_id: int,
    relay_url: str,
    target_block_number: Optional[int] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    priority_fee: Optional[int] = None,
    tx_nonce: Optional[int] = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> Optional[str]:
    """
    Submit ``mine`` via Flashbots ``eth_sendBundle`` (signed relay auth header).
    Returns bundleHash hex string (not the same as tx hash).
    """
    raw = build_signed_mine_raw(
        w3,
        contract,
        private_key_hex=private_key_hex,
        miner_address=miner_address,
        pow_nonce=pow_nonce,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        priority_fee=priority_fee,
        tx_nonce=tx_nonce,
    )
    if confirm:
        print({"relay": relay_url, "raw_len": len(raw)})
        if input("Submit Flashbots bundle? [y/N]: ").strip().lower() != "y":
            return None
    if dry_run:
        return None
    if target_block_number is None:
        target_block_number = int(w3.eth.block_number) + 1
    result = send_bundle_single_tx(relay_url, flashbots_auth_private_key_hex, raw, int(target_block_number))
    bh = result.get("bundleHash")
    if isinstance(bh, str):
        return bh
    return str(result)


def send_mine_transaction(
    w3: Web3,
    contract: Contract,
    *,
    private_key_hex: str,
    miner_address: str,
    pow_nonce: int,
    chain_id: int,
    submit_web3: Optional[Web3] = None,
    gas_limit: Optional[int] = None,
    max_fee_per_gas: Optional[int] = None,
    priority_fee: Optional[int] = None,
    tx_nonce: Optional[int] = None,
    dry_run: bool = False,
    confirm: bool = False,
) -> Optional[str]:
    raw = build_signed_mine_raw(
        w3,
        contract,
        private_key_hex=private_key_hex,
        miner_address=miner_address,
        pow_nonce=pow_nonce,
        chain_id=chain_id,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        priority_fee=priority_fee,
        tx_nonce=tx_nonce,
    )
    if confirm:
        print(f"mine() raw tx length={len(raw)} bytes")
        if input("Broadcast transaction? [y/N]: ").strip().lower() != "y":
            return None
    if dry_run:
        return None
    submitter = submit_web3 or w3
    if submitter is not w3:
        log.info("submitting raw tx via private / alternate RPC endpoint (not --rpc)")
    tx_hash = submitter.eth.send_raw_transaction(raw)
    return tx_hash.hex()
