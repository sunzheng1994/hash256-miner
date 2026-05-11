"""
Local control plane + web UI (binds to 127.0.0.1).

- Reads **wallet** + **Flashbots auth** keys only from environment (never from browser):
  ``HASH256_PRIVATE_KEY``, ``FLASHBOTS_AUTH_KEY``
- Optional ``REMOTE_MINER_API_KEY`` forwarded to cloud worker as ``X-API-Key``.
- Serves static files from ``web-ui/`` at repo root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import BaseModel, Field

from hash256_miner.chain import connect, load_contract, read_mining_state
from hash256_miner.flashbots_bundle import default_relay_for_chain, send_bundle_single_tx
from hash256_miner.tx import build_signed_mine_raw, load_private_key

cli = typer.Typer(invoke_without_command=True, no_args_is_help=False, add_completion=False)


def _repo_web_ui_dir() -> Path:
    # local_gateway.py -> hash256_miner -> src -> repo root
    return Path(__file__).resolve().parent.parent.parent / "web-ui"


class MineSubmitBody(BaseModel):
    rpc: str
    contract: str
    address: str
    remote_worker: str = Field(..., description="https://your-gpu-host/v1/pow-search")
    remote_api_key: str | None = Field(None, description="Or set REMOTE_MINER_API_KEY on gateway")
    abi: str | None = None
    batch_size: int = 65_536
    flashbots_relay: str | None = None
    target_block_offset: int = Field(1, ge=1, le=64)


def _abi_path(p: str | None) -> Path:
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "abi" / "miner.json"


@cli.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind only localhost for safety"),
    port: int = typer.Option(8790, "--port"),
) -> None:
    """本机网关 + 静态 web-ui；请先 ``pip install -e .`` 并安装 optional-dependencies 里的 remote 组。"""
    if ctx.invoked_subcommand is not None:
        return
    try:
        from fastapi import FastAPI, HTTPException
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import FileResponse
        from fastapi.staticfiles import StaticFiles
        import uvicorn
    except ImportError as e:
        raise typer.Exit('请安装: pip install -e ".[remote]"') from e

    import httpx

    web_ui = _repo_web_ui_dir()
    if not web_ui.is_dir():
        raise typer.Exit(f"未找到前端目录: {web_ui}（请保留仓库内 web-ui/）")

    api = FastAPI(title="HASH256 Local Gateway", version="0.1.0")
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @api.get("/")
    def index() -> FileResponse:
        return FileResponse(web_ui / "index.html")

    @api.post("/api/mine-flashbots-bundle")
    def mine_flashbots_bundle(body: MineSubmitBody) -> dict[str, Any]:
        pk = os.environ.get("HASH256_PRIVATE_KEY", "").strip()
        fb = os.environ.get("FLASHBOTS_AUTH_KEY", "").strip()
        if not pk or not fb:
            raise HTTPException(
                status_code=500,
                detail="本机需设置环境变量 HASH256_PRIVATE_KEY 与 FLASHBOTS_AUTH_KEY（浏览器不传私钥）",
            )
        w3 = connect(body.rpc)
        abi_path = _abi_path(body.abi)
        if not abi_path.is_file():
            raise HTTPException(status_code=400, detail=f"ABI 文件不存在: {abi_path}")
        c = load_contract(w3, body.contract, abi_path)
        st = read_mining_state(w3, c, body.address, abi_path=abi_path)

        rkey = body.remote_api_key or os.environ.get("REMOTE_MINER_API_KEY", "").strip()
        payload = {
            "challenge_hex": "0x" + st.challenge.hex(),
            "difficulty": st.difficulty,
            "batch_size": body.batch_size,
            "base_nonce": 0,
            "use_gpu": True,
            "max_batches": 50_000,
        }
        headers = {}
        if rkey:
            headers["X-API-Key"] = rkey

        with httpx.Client(timeout=600.0) as client:
            r = client.post(body.remote_worker, json=payload, headers=headers)
            r.raise_for_status()
            jr = r.json()
        if not jr.get("ok"):
            raise HTTPException(status_code=400, detail=jr)

        nonce = int(jr["nonce"])
        relay = body.flashbots_relay or default_relay_for_chain(st.chain_id)
        if not relay:
            raise HTTPException(status_code=400, detail="请指定 flashbots_relay（非主网/Sepolia 无默认中继）")

        target_block = int(w3.eth.block_number) + int(body.target_block_offset)
        raw = build_signed_mine_raw(
            w3,
            c,
            private_key_hex=load_private_key(private_key=pk),
            miner_address=body.address,
            pow_nonce=nonce,
            chain_id=st.chain_id,
            gas_limit=None,
            max_fee_per_gas=None,
            priority_fee=None,
            tx_nonce=None,
        )
        result = send_bundle_single_tx(relay, fb, raw, target_block)
        return {"ok": True, "nonce": str(nonce), "relay_result": result, "target_block": target_block}

    api.mount("/static", StaticFiles(directory=str(web_ui), html=False), name="static")

    uvicorn.run(api, host=host, port=port)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
