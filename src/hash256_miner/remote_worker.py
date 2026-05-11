"""
GPU/CPU PoW worker HTTP service (runs on cloud, e.g. Alibaba Cloud with NVIDIA).

No private keys: only ``challenge`` + ``difficulty`` + nonce range.
Protect with ``REMOTE_MINER_API_KEY`` (client sends ``X-API-Key``).
"""

from __future__ import annotations

import os
from typing import Optional

import typer
from pydantic import BaseModel, Field

from hash256_miner.challenge import digest_less_than_difficulty, pow_hash
from hash256_miner.workers import gpu_worker

app = typer.Typer(no_args_is_help=True, add_completion=False)


class PowSearchRequest(BaseModel):
    challenge_hex: str = Field(..., description="0x-prefixed or raw hex, 64 hex chars = 32 bytes")
    difficulty: int
    batch_size: int = 65_536
    base_nonce: int = Field(0, ge=0, description="Lower 64-bit nonce base for GPU path")
    use_gpu: bool = True
    max_batches: int = Field(10_000, description="Safety cap per HTTP request")


def _parse_challenge(h: str) -> bytes:
    s = h.strip().lower()
    if s.startswith("0x"):
        s = s[2:]
    b = bytes.fromhex(s)
    if len(b) != 32:
        raise ValueError("challenge must be 32 bytes (64 hex chars)")
    return b


def _search_impl(req: PowSearchRequest, api_key: str) -> dict:
    expected = os.environ.get("REMOTE_MINER_API_KEY", "").strip()
    if expected and api_key.strip() != expected:
        return {"ok": False, "error": "unauthorized"}

    ch = _parse_challenge(req.challenge_hex)
    diff = int(req.difficulty)
    bs = int(req.batch_size)
    base = int(req.base_nonce)

    batches = 0
    while batches < req.max_batches:
        if req.use_gpu and gpu_worker.cupy_available():
            try:
                hit = gpu_worker.mine_batch_gpu(ch, diff, base, bs)
            except Exception as e:  # noqa: BLE001
                return {"ok": False, "error": f"gpu: {e!s}"}
        else:
            hit = None
            end = min(base + bs, (1 << 64))
            for n in range(base, end):
                if digest_less_than_difficulty(pow_hash(ch, n), diff):
                    hit = n
                    break
        batches += 1
        if hit is not None:
            return {"ok": True, "nonce": str(int(hit)), "batches": batches}
        base += bs
        if base >= 1 << 64:
            base = 0

    return {"ok": False, "error": "exhausted_max_batches", "batches": batches}


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("0.0.0.0", "--host"),
    port: int = typer.Option(8787, "--port"),
) -> None:
    """Run FastAPI worker (install optional deps: pip install -e \".[remote]\")."""
    try:
        from fastapi import FastAPI, Header, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        import uvicorn
    except ImportError as e:
        raise typer.Exit(
            "缺少依赖：请执行 pip install -e \".[remote]\"（需要 fastapi、uvicorn）"
        ) from e

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
