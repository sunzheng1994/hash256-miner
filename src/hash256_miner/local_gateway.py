"""
Local control plane + web UI (binds to 127.0.0.1).

- Reads **wallet** + **Flashbots auth** keys only from environment (never from browser):
  ``HASH256_PRIVATE_KEY``, ``FLASHBOTS_AUTH_KEY``
- Optional ``REMOTE_MINER_API_KEY`` forwarded to cloud worker as ``X-API-Key``.
- Optional form defaults (same root ``.env``): ``HASH256_READ_RPC``, ``HASH256_MINE_CONTRACT``,
  ``HASH256_MINER_ADDRESS`` (或从 ``HASH256_PRIVATE_KEY`` 推导地址)、``HASH256_REMOTE_WORKER``、``HASH256_FLASHBOTS_RELAY``.
- Serves static files from ``web-ui/`` at repo root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

import typer
from pydantic import BaseModel, Field
from web3 import Web3

from hash256_miner.chain import connect, load_contract, read_mining_state
from hash256_miner.env_bootstrap import load_repo_env_file
from eth_account import Account

from hash256_miner.flashbots_bundle import (
    FLASHBOTS_RELAY_MAINNET,
    FLASHBOTS_RELAY_SEPOLIA,
    default_relay_for_chain,
    send_bundle_single_tx,
)
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
    max_batches: int = Field(500_000, ge=1_000, le=10_000_000, description="转发给 Worker 的 max_batches 上限")
    flashbots_relay: str | None = None
    target_block_offset: int = Field(1, ge=1, le=64)


def _abi_path(p: str | None) -> Path:
    if p:
        return Path(p)
    return Path(__file__).resolve().parent / "abi" / "miner.json"


# 未设置 HASH256_READ_RPC / HASH256_MINE_CONTRACT 时，/api/form-defaults 返回的默认（可被 .env 覆盖）
DEFAULT_FORM_RPC = "https://ethereum-rpc.publicnode.com"
DEFAULT_FORM_MINT_CONTRACT = "0xAC7b5d06fa1e77D08aea40d46cB7C5923A87A0cc"


def _form_defaults_from_env() -> dict[str, Any]:
    """供网页预填：不含任何私钥内容；矿工地址可为显式配置或由钱包私钥推导。"""
    rpc = (os.environ.get("HASH256_READ_RPC") or os.environ.get("HASH256_RPC") or "").strip()
    contract = (os.environ.get("HASH256_MINE_CONTRACT") or os.environ.get("HASH256_CONTRACT") or "").strip()
    miner = (os.environ.get("HASH256_MINER_ADDRESS") or os.environ.get("HASH256_MINER") or "").strip()
    if not miner:
        pk = os.environ.get("HASH256_PRIVATE_KEY", "").strip()
        if pk:
            try:
                raw = load_private_key(private_key=pk)
                miner = Account.from_key("0x" + raw).address
            except Exception:
                miner = ""
    relay = (os.environ.get("HASH256_FLASHBOTS_RELAY") or "").strip() or FLASHBOTS_RELAY_MAINNET
    remote = (
        os.environ.get("HASH256_REMOTE_WORKER")
        or os.environ.get("HASH256_REMOTE_WORKER_URL")
        or ""
    ).strip()
    rpc_out = rpc or DEFAULT_FORM_RPC
    contract_raw = contract or DEFAULT_FORM_MINT_CONTRACT
    try:
        contract_out = Web3.to_checksum_address(contract_raw) if contract_raw else None
    except Exception:
        contract_out = contract_raw or None
    return {
        "rpc": rpc_out,
        "contract": contract_out,
        "miner_address": miner or None,
        "remote_worker": remote or None,
        "flashbots_relay_suggested": relay,
        "flashbots_relay_mainnet": FLASHBOTS_RELAY_MAINNET,
        "flashbots_relay_sepolia": FLASHBOTS_RELAY_SEPOLIA,
    }


@cli.callback(invoke_without_command=True)
def _main(
    ctx: typer.Context,
    host: str = typer.Option("127.0.0.1", "--host", help="Bind only localhost for safety"),
    port: int = typer.Option(8790, "--port"),
) -> None:
    """本机网关 + 静态 web-ui；请先 ``pip install -e .`` 并安装 optional-dependencies 里的 remote 组。"""
    if ctx.invoked_subcommand is not None:
        return
    load_repo_env_file()
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

    @api.get("/api/gateway-status")
    def gateway_status() -> dict[str, Any]:
        """方案 A：仅返回是否已配置环境变量，不包含任何密钥内容。"""
        return {
            "wallet_key_configured": bool(os.environ.get("HASH256_PRIVATE_KEY", "").strip()),
            "flashbots_auth_configured": bool(os.environ.get("FLASHBOTS_AUTH_KEY", "").strip()),
            "remote_worker_api_key_env": bool(os.environ.get("REMOTE_MINER_API_KEY", "").strip()),
        }

    @api.get("/api/form-defaults")
    def form_defaults() -> dict[str, Any]:
        """从仓库根 ``.env`` 读取链/合约/矿工/Worker/Flashbots relay 预填项（无私钥）。"""
        return _form_defaults_from_env()

    @api.post("/api/mine-flashbots-bundle")
    def mine_flashbots_bundle(body: MineSubmitBody) -> dict[str, Any]:
        pk = os.environ.get("HASH256_PRIVATE_KEY", "").strip()
        fb = os.environ.get("FLASHBOTS_AUTH_KEY", "").strip()
        if not pk or not fb:
            raise HTTPException(
                status_code=500,
                detail="本机需设置环境变量 HASH256_PRIVATE_KEY 与 FLASHBOTS_AUTH_KEY（浏览器不传私钥）",
            )
        try:
            w3 = connect(body.rpc)
            abi_path = _abi_path(body.abi)
            if not abi_path.is_file():
                raise HTTPException(status_code=400, detail=f"ABI 文件不存在: {abi_path}")
            c = load_contract(w3, body.contract, abi_path)
            st = read_mining_state(w3, c, body.address, abi_path=abi_path)

            rkey = body.remote_api_key or os.environ.get("REMOTE_MINER_API_KEY", "").strip()
            gpu_flag = os.environ.get("HASH256_REMOTE_WORKER_USE_GPU", "true").strip().lower()
            use_gpu = gpu_flag in ("1", "true", "yes", "on")
            cap_mb = int(os.environ.get("HASH256_REMOTE_POW_MAX_BATCHES", "10000000"))
            max_batches = min(int(body.max_batches), max(1000, cap_mb))
            payload = {
                "challenge_hex": "0x" + st.challenge.hex(),
                "difficulty": st.difficulty,
                "batch_size": body.batch_size,
                "base_nonce": 0,
                "use_gpu": use_gpu,
                "max_batches": max_batches,
            }
            headers = {}
            if rkey:
                headers["X-API-Key"] = rkey

            timeout_s = float(os.environ.get("HASH256_REMOTE_POW_HTTP_TIMEOUT", "3600"))
            with httpx.Client(timeout=timeout_s) as client:
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
        except HTTPException:
            raise
        except httpx.HTTPStatusError as e:
            body_txt = e.response.text[:2000] if e.response is not None else ""
            raise HTTPException(
                status_code=502,
                detail=f"云端 Worker HTTP {e.response.status_code if e.response else '?'}: {body_txt or str(e)}",
            ) from e
        except httpx.RequestError as e:
            raise HTTPException(status_code=502, detail=f"云端 Worker 网络错误: {e}") from e
        except (RuntimeError, ValueError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"网关内部错误: {type(e).__name__}: {e}") from e

    api.mount("/static", StaticFiles(directory=str(web_ui), html=False), name="static")

    uvicorn.run(api, host=host, port=port)


def main() -> None:
    cli()


if __name__ == "__main__":
    main()
