"""
PoW pool coordinator: one HTTP entry fans out parallel leases to many ``hash256-remote-worker`` URLs.

Each worker already accepts ``base_nonce`` + ``max_batches`` (lease size). The coordinator assigns
disjoint nonce ranges and returns the first ``ok: true`` response; other in-flight leases are cancelled.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from typing import Any

import httpx
import typer
from pydantic import BaseModel, Field

from hash256_miner.env_bootstrap import load_repo_env_file

_log = logging.getLogger("hash256_miner.pool_coordinator")

app = typer.Typer(invoke_without_command=True, no_args_is_help=False, add_completion=False)

_NONCE_CAP = 1 << 64


class PowPoolSearchBody(BaseModel):
    """Same JSON shape as a single Worker ``/v1/pow-search`` plus pool tuning."""

    challenge_hex: str = Field(..., description="0x-prefixed or raw hex, 64 hex chars = 32 bytes")
    difficulty: int
    batch_size: int = 65_536
    base_nonce: int = Field(0, ge=0, description="Global starting nonce for this job")
    use_gpu: bool = True
    max_batches: int = Field(
        2_000_000,
        ge=1_000,
        le=100_000_000,
        description="Global cap: total lease batches scheduled (incl. cancelled in-flight leases)",
    )
    lease_max_batches: int = Field(
        5_000,
        ge=50,
        le=1_000_000,
        description="Per-worker HTTP call max_batches (smaller = more frequent fan-out / lower tail latency)",
    )
    nonce_prefix_hex: str | None = Field(
        default=None,
        description="Optional shared 24-byte prefix (0x+48 hex) for all pool workers; omit = each worker random",
    )


def _parse_worker_urls() -> list[str]:
    raw = (os.environ.get("HASH256_POOL_WORKER_URLS") or "").strip()
    if not raw:
        return []
    parts: list[str] = []
    for chunk in raw.replace("\n", ",").split(","):
        u = chunk.strip()
        if u:
            parts.append(u)
    return parts


async def pool_search_core(
    *,
    client: httpx.AsyncClient,
    worker_urls: list[str],
    body: PowPoolSearchBody,
    x_api_key: str,
) -> dict[str, Any]:
    if not worker_urls:
        return {"ok": False, "error": "pool_no_workers", "detail": "set HASH256_POOL_WORKER_URLS"}

    pool_prefix_hex: str | None = body.nonce_prefix_hex
    if pool_prefix_hex is None and len(worker_urls) > 1:
        pool_prefix_hex = "0x" + secrets.token_hex(24)

    budget = int(body.max_batches)
    lease_cap = int(body.lease_max_batches)
    bs = int(body.batch_size)
    cursor = int(body.base_nonce)
    headers: dict[str, str] = {}
    if x_api_key.strip():
        headers["X-API-Key"] = x_api_key.strip()

    pool_burned = 0
    t_round = 0

    while budget > 0:
        wave: list[tuple[str, int, int]] = []
        for url in worker_urls:
            if budget <= 0:
                break
            if cursor >= _NONCE_CAP:
                return {
                    "ok": False,
                    "error": "exhausted_nonce_space",
                    "batches": pool_burned,
                    "pool_rounds": t_round,
                }
            remain = _NONCE_CAP - cursor
            if remain < bs:
                return {
                    "ok": False,
                    "error": "exhausted_nonce_space",
                    "batches": pool_burned,
                    "pool_rounds": t_round,
                }
            lb = min(lease_cap, budget)
            max_nonces = lb * bs
            if max_nonces > remain:
                lb = remain // bs
            if lb < 1:
                break
            wave.append((url, cursor, lb))
            cursor += lb * bs
            budget -= lb
            pool_burned += lb

        if not wave:
            break

        t_round += 1
        _log.info(
            "pool wave=%s leases=%s budget_left=%s next_base=%s",
            t_round,
            len(wave),
            budget,
            wave[0][1] if wave else None,
        )

        async def one_post(url: str, base: int, lb: int) -> dict[str, Any]:
            payload = {
                "challenge_hex": body.challenge_hex,
                "difficulty": int(body.difficulty),
                "batch_size": bs,
                "base_nonce": base,
                "use_gpu": bool(body.use_gpu),
                "max_batches": lb,
            }
            if pool_prefix_hex is not None:
                payload["nonce_prefix_hex"] = pool_prefix_hex
            try:
                r = await client.post(url, json=payload, headers=headers)
                r.raise_for_status()
                data = r.json()
                if not isinstance(data, dict):
                    return {"ok": False, "error": "bad_json:not_object", "batches": 0}
                return data
            except httpx.HTTPStatusError as e:
                txt = e.response.text[:500] if e.response is not None else ""
                return {"ok": False, "error": f"http_{e.response.status_code if e.response else '?'}:{txt}", "batches": 0}
            except httpx.RequestError as e:
                return {"ok": False, "error": f"http:{e!s}", "batches": 0}
            except (json.JSONDecodeError, ValueError) as e:
                return {"ok": False, "error": f"bad_json:{e!s}", "batches": 0}

        wave_tasks = [asyncio.create_task(one_post(u, b, lb)) for u, b, lb in wave]
        remaining: set[asyncio.Task] = set(wave_tasks)

        try:
            while remaining:
                done, pending = await asyncio.wait(remaining, return_when=asyncio.FIRST_COMPLETED)
                remaining = set(pending)
                for t in done:
                    try:
                        jr = t.result()
                    except asyncio.CancelledError:
                        continue
                    except Exception as e:  # noqa: BLE001
                        jr = {"ok": False, "error": f"task:{e!s}", "batches": 0}
                    if jr.get("ok"):
                        for p in remaining:
                            p.cancel()
                        if remaining:
                            await asyncio.gather(*remaining, return_exceptions=True)
                        remaining.clear()
                        out = dict(jr)
                        out["pool_coordinator"] = {
                            "workers": len(worker_urls),
                            "lease_batches_scheduled": pool_burned,
                            "waves": t_round,
                        }
                        return out
        finally:
            for p in remaining:
                p.cancel()
            if remaining:
                await asyncio.gather(*remaining, return_exceptions=True)

    return {
        "ok": False,
        "error": "exhausted_max_batches",
        "batches": pool_burned,
        "pool_coordinator": {"workers": len(worker_urls), "lease_batches_scheduled": pool_burned, "waves": t_round},
    }


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8788, "--port"),
) -> None:
    if ctx.invoked_subcommand is not None:
        return
    load_repo_env_file()
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        import uvicorn
    except ImportError as e:
        raise typer.Exit('请安装: pip install -e ".[remote]"') from e

    logging.basicConfig(
        level=os.environ.get("HASH256_POOL_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s %(message)s",
    )

    worker_urls = _parse_worker_urls()
    timeout_s = float(os.environ.get("HASH256_POOL_HTTP_TIMEOUT", os.environ.get("HASH256_REMOTE_POW_HTTP_TIMEOUT", "3600")))

    api = FastAPI(title="HASH256 PoW Pool Coordinator", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.post("/v1/pow-pool/search")
    async def pow_pool_search(req: PowPoolSearchBody, x_api_key: str = Header(default="")) -> dict[str, Any]:
        urls = _parse_worker_urls()
        if not urls:
            raise HTTPException(status_code=503, detail="HASH256_POOL_WORKER_URLS empty")
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            return await pool_search_core(client=client, worker_urls=urls, body=req, x_api_key=x_api_key)

    @api.get("/healthz")
    def healthz() -> dict[str, Any]:
        urls = _parse_worker_urls()
        return {"status": "ok", "worker_urls": len(urls), "timeout_s": timeout_s}

    _log.info("pool coordinator worker_urls=%s timeout_s=%s", len(worker_urls), timeout_s)
    uvicorn.run(api, host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
