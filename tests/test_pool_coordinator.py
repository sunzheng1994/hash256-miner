"""Tests for PoW pool coordinator fan-out (httpx MockTransport)."""

from __future__ import annotations

import asyncio
import json

import httpx

from hash256_miner.pool_coordinator import PowPoolSearchBody, pool_search_core

_CH = "0x" + "00" * 32


def test_pool_first_hit_cancels_other_worker() -> None:
    seen: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        seen.append((request.url.host, body))
        host = request.url.host
        if host == "w1":
            return httpx.Response(200, json={"ok": True, "nonce": "42", "batches": 3})
        return httpx.Response(200, json={"ok": False, "error": "exhausted_max_batches", "batches": 10})

    async def run() -> dict[str, object]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            body = PowPoolSearchBody(
                challenge_hex=_CH,
                difficulty=2**256 - 1,
                batch_size=1000,
                base_nonce=0,
                max_batches=50_000,
                lease_max_batches=100,
                use_gpu=False,
            )
            return await pool_search_core(
                client=client,
                worker_urls=["http://w1/v1/pow-search", "http://w2/v1/pow-search"],
                body=body,
                x_api_key="secret",
            )

    out = asyncio.run(run())
    assert out["ok"] is True
    assert out["nonce"] == "42"
    assert out["pool_coordinator"]["workers"] == 2
    bases = {s[1]["base_nonce"] for s in seen}
    assert 0 in bases
    # 另一台 lease 的 base；若取消足够快可能尚未发起请求
    assert bases <= {0, 100_000}


def test_pool_exhausts_global_budget() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        b = json.loads(request.content.decode())
        return httpx.Response(200, json={"ok": False, "error": "exhausted_max_batches", "batches": b["max_batches"]})

    async def run() -> dict[str, object]:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            body = PowPoolSearchBody(
                challenge_hex=_CH,
                difficulty=2**256 - 1,
                batch_size=500,
                base_nonce=0,
                max_batches=2000,
                lease_max_batches=200,
                use_gpu=False,
            )
            return await pool_search_core(
                client=client,
                worker_urls=["http://a/v1/pow-search", "http://b/v1/pow-search"],
                body=body,
                x_api_key="",
            )

    out = asyncio.run(run())
    assert out["ok"] is False
    assert out["error"] == "exhausted_max_batches"
    assert out["batches"] == 2000


def test_pool_forwards_api_key_header() -> None:
    got: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        got.append(request.headers.get("x-api-key", ""))
        return httpx.Response(200, json={"ok": True, "nonce": "1", "batches": 1})

    async def run() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            body = PowPoolSearchBody(
                challenge_hex=_CH,
                difficulty=2**256 - 1,
                batch_size=100,
                max_batches=5000,
                lease_max_batches=50,
                use_gpu=False,
            )
            await pool_search_core(
                client=client,
                worker_urls=["http://solo/v1/pow-search"],
                body=body,
                x_api_key="k2",
            )

    asyncio.run(run())
    assert got == ["k2"]
