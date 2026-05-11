"""
Flashbots bundle relay (eth_sendBundle) with X-Flashbots-Signature.

Auth: EIP-191 personal_sign over keccak256(UTF-8 JSON body), header
``X-Flashbots-Signature: <signer_address>:<signature_hex>``.

See: https://docs.flashbots.net/flashbots-auction/advanced/rpc-endpoint
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

import requests
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

log = logging.getLogger(__name__)

FLASHBOTS_RELAY_MAINNET = "https://relay.flashbots.net"
FLASHBOTS_RELAY_SEPOLIA = "https://relay-sepolia.flashbots.net"


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    """UTF-8 JSON with compact separators; must match POST body byte-for-byte."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign_flashbots_header(body_bytes: bytes, flashbots_auth_private_key_hex: str) -> str:
    """
    Build ``X-Flashbots-Signature`` value: ``0x<addr>:0x<sig>`` (checksum address + 65-byte sig hex).
    """
    key = flashbots_auth_private_key_hex.strip()
    if key.startswith("0x"):
        key = key[2:]
    body_hash = keccak(body_bytes)
    message = encode_defunct(primitive=body_hash)
    signer = Account.from_key(key)
    signed = signer.sign_message(message)
    addr = signer.address
    sig = signed.signature.hex()
    if not sig.startswith("0x"):
        sig = "0x" + sig
    return f"{addr}:{sig}"


def post_signed_flashbots(
    relay_url: str,
    payload: dict[str, Any],
    flashbots_auth_private_key_hex: str,
    timeout_sec: float = 60.0,
) -> dict[str, Any]:
    body_bytes = canonical_json_bytes(payload)
    header = sign_flashbots_header(body_bytes, flashbots_auth_private_key_hex)
    log.info("POST %s method=%s", relay_url, payload.get("method"))
    resp = requests.post(
        relay_url,
        data=body_bytes,
        headers={
            "Content-Type": "application/json",
            "X-Flashbots-Signature": header,
        },
        timeout=timeout_sec,
    )
    resp.raise_for_status()
    out = resp.json()
    if out.get("error"):
        raise RuntimeError(f"Flashbots relay error: {out['error']}")
    return out


def build_eth_send_bundle_payload(
    signed_raw_tx_hex: str,
    target_block_number: int,
    *,
    min_timestamp: int = 0,
    max_timestamp: int = 0,
    rpc_id: int = 1,
) -> dict[str, Any]:
    if not signed_raw_tx_hex.startswith("0x"):
        signed_raw_tx_hex = "0x" + signed_raw_tx_hex
    return {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "eth_sendBundle",
        "params": [
            {
                "txs": [signed_raw_tx_hex],
                "blockNumber": hex(target_block_number),
                "minTimestamp": min_timestamp,
                "maxTimestamp": max_timestamp,
            }
        ],
    }


def send_bundle_single_tx(
    relay_url: str,
    flashbots_auth_private_key_hex: str,
    signed_raw_tx: bytes,
    target_block_number: int,
) -> dict[str, Any]:
    """Submit one signed raw tx as a single-tx bundle; returns JSON-RPC result dict."""
    hx = signed_raw_tx.hex()
    if not hx.startswith("0x"):
        hx = "0x" + hx
    payload = build_eth_send_bundle_payload(hx, target_block_number)
    jr = post_signed_flashbots(relay_url, payload, flashbots_auth_private_key_hex)
    return jr.get("result") or {}


def default_relay_for_chain(chain_id: int) -> Optional[str]:
    if chain_id == 1:
        return FLASHBOTS_RELAY_MAINNET
    if chain_id == 11155111:
        return FLASHBOTS_RELAY_SEPOLIA
    return None
