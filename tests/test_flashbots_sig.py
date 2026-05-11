"""Flashbots X-Flashbots-Signature round-trip (local only)."""

from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak

from hash256_miner.flashbots_bundle import sign_flashbots_header


def test_flashbots_header_recoverable():
    signer = Account.create()
    key_hex = signer.key.hex()
    body = (
        '{"jsonrpc":"2.0","id":1,"method":"eth_sendBundle",'
        '"params":[{"txs":["0x00"],"blockNumber":"0x1","minTimestamp":0,"maxTimestamp":0}]}'
    ).encode("utf-8")
    header = sign_flashbots_header(body, key_hex)
    addr, sig = header.split(":", 1)
    assert addr.lower() == signer.address.lower()
    msg = encode_defunct(primitive=keccak(body))
    recovered = Account.recover_message(msg, signature=sig)
    assert recovered.lower() == signer.address.lower()
