"""
GPU/CPU PoW worker HTTP service (runs on cloud, e.g. Alibaba Cloud with NVIDIA).

No private keys: only ``challenge`` + ``difficulty`` + nonce range.
Protect with ``REMOTE_MINER_API_KEY`` (client sends ``X-API-Key``).
"""

from __future__ import annotations

import logging
import os
import secrets
import time
from typing import Optional

import typer
from pydantic import BaseModel, ConfigDict, Field

from hash256_miner.challenge import (
    digest_less_than_difficulty,
    pow_hash_prefix_counter,
    uint256_from_prefix_counter,
)
from hash256_miner.env_bootstrap import load_repo_env_file
from hash256_miner.workers import gpu_worker

_log = logging.getLogger("hash256_miner.remote_worker")

# 单入口：与 ``hash256-remote-worker --help`` 根级 ``--host/--port`` 一致（无需 ``serve`` 子命令）
app = typer.Typer(invoke_without_command=True, no_args_is_help=False, add_completion=False)


class PowSearchRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    challenge_hex: str = Field(..., description="0x-prefixed or raw hex, 64 hex chars = 32 bytes")
    difficulty: int
    batch_size: int = 65_536
    base_nonce: int = Field(
        0,
        ge=0,
        description="uint64 counter base; on-chain nonce = prefix24 || counter (BE), same as hash-cli-miner",
    )
    nonce_prefix_hex: str | None = Field(
        default=None,
        description="Optional 0x + 48 hex (24 bytes). Omit to use a random prefix for this HTTP request.",
    )
    use_gpu: bool = True
    max_batches: int = Field(500_000, ge=1, le=10_000_000, description="Safety cap per HTTP request")


def _parse_challenge(h: str) -> bytes:
    s = h.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    b = bytes.fromhex(s)
    if len(b) != 32:
        raise ValueError("challenge must be 32 bytes (64 hex chars)")
    return b


def _parse_nonce_prefix_hex(raw: str | None) -> bytes:
    if raw is None or not str(raw).strip():
        return secrets.token_bytes(24)
    s = str(raw).strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    if len(s) != 48 or any(c not in "0123456789abcdef" for c in s):
        raise ValueError("nonce_prefix_hex must be 24 bytes as 0x + 48 hex chars")
    return bytes.fromhex(s)


def _mine_cpu_batch(ch: bytes, difficulty: int, prefix24: bytes, base: int, batch_size: int) -> Optional[int]:
    """Linear scan counters [base, base+batch_size); returns full uint256 mine nonce."""
    end = min(base + batch_size, (1 << 64))
    for c in range(base, end):
        if digest_less_than_difficulty(pow_hash_prefix_counter(ch, prefix24, c), difficulty):
            return uint256_from_prefix_counter(prefix24, c)
    return None


def _search_impl(req: PowSearchRequest, api_key: str) -> dict:
    expected = os.environ.get("REMOTE_MINER_API_KEY", "").strip()
    if expected and api_key.strip() != expected:
        return {"ok": False, "error": "unauthorized"}

    ch = _parse_challenge(req.challenge_hex)
    diff = int(req.difficulty)
    bs = int(req.batch_size)
    base = int(req.base_nonce)
    try:
        prefix24 = _parse_nonce_prefix_hex(req.nonce_prefix_hex)
    except ValueError as e:
        return {"ok": False, "error": f"bad_prefix:{e}"}

    prog_step = int(os.environ.get("HASH256_POW_PROGRESS_LOG_EVERY", "2000"))
    prog_step = max(100, min(50_000, prog_step))
    t0 = time.perf_counter()
    _log.info(
        "pow-search start batch_size=%s max_batches=%s use_gpu=%s prefix=%s",
        bs,
        req.max_batches,
        req.use_gpu and gpu_worker.cupy_available(),
        prefix24[:4].hex() + "…",
    )

    batches = 0
    while batches < req.max_batches:
        hit: Optional[int] = None
        if req.use_gpu and gpu_worker.cupy_available():
            try:
                hit = gpu_worker.mine_batch_gpu(ch, diff, prefix24, base, bs)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"gpu: {e!s}"}
        else:
            hit = _mine_cpu_batch(ch, diff, prefix24, base, bs)
        batches += 1
        if batches == 1 or batches % prog_step == 0:
            elapsed = time.perf_counter() - t0
            hashes = batches * bs
            hr = hashes / elapsed if elapsed > 0 else 0.0
            ghs = hr / 1e9
            mhs = hr / 1e6
            hr_s = f"{ghs:.2f} GH/s" if ghs >= 1.0 else f"{mhs:.2f} MH/s"
            _log.info(
                "pow-search progress batches=%s/%s counter_base=%s hashes=%s %s elapsed_s=%.1f",
                batches,
                req.max_batches,
                base,
                hashes,
                hr_s,
                elapsed,
            )
        if hit is not None:
            _log.info(
                "pow-search hit nonce=%s batches=%s elapsed_s=%.1f",
                int(hit),
                batches,
                time.perf_counter() - t0,
            )
            return {"ok": True, "nonce": str(int(hit)), "batches": batches}
        base += bs
        if base >= 1 << 64:
            base = 0

    elapsed = time.perf_counter() - t0
    hashes = batches * bs
    hr = hashes / elapsed if elapsed > 0 else 0.0
    ghs = hr / 1e9
    mhs = hr / 1e6
    hr_s = f"{ghs:.2f} GH/s" if ghs >= 1.0 else f"{mhs:.2f} MH/s"
    _log.info(
        "pow-search exhausted_max_batches batches=%s hashes=%s %s elapsed_s=%.1f",
        batches,
        hashes,
        hr_s,
        elapsed,
    )
    return {"ok": False, "error": "exhausted_max_batches", "batches": batches}


@app.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    """云端 PoW HTTP 服务；请先 ``pip install -e .`` 并安装 optional-dependencies 里的 remote 组（fastapi、uvicorn）。"""
    if ctx.invoked_subcommand is not None:
        return
    load_repo_env_file()
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        import uvicorn
    except ImportError as e:
        raise typer.Exit(
            "缺少依赖：请执行 pip install -e \".[remote]\"（需要 fastapi、uvicorn）"
        ) from e

    logging.basicConfig(
        level=os.environ.get("HASH256_WORKER_LOG_LEVEL", "INFO").upper(),
        format="%(levelname)s %(name)s %(message)s",
    )

    api = FastAPI(title="HASH256 Remote PoW Worker", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.post("/v1/pow-search")
    def pow_search(req: PowSearchRequest, x_api_key: str = Header(default="")) -> dict:
        out = _search_impl(req, x_api_key)
        if out.get("error") == "unauthorized":
            raise HTTPException(status_code=401, detail="invalid X-API-Key")
        return out

    @api.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok", "gpu": gpu_worker.cupy_available()}

    uvicorn.run(api, host=host, port=port)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
