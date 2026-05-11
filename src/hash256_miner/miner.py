"""Typer CLI: coordinate chain reads, CPU/GPU workers, and optional broadcast."""

from __future__ import annotations

import logging
import os
import random
import signal
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.live import Live
from rich.table import Table
from tqdm import tqdm
from hash256_miner.chain import connect, load_contract
from hash256_miner.challenge import digest_less_than_difficulty, pow_hash
from hash256_miner.logging_conf import setup_logging
from hash256_miner.telemetry import Telemetry
from hash256_miner.flashbots_bundle import default_relay_for_chain
from hash256_miner.tx import (
    FLASHBOTS_PROTECT_MAINNET_RPC,
    load_private_key,
    send_mine_flashbots_bundle,
    send_mine_transaction,
)
from hash256_miner.verify import try_eth_call_mine, verify_nonce_or_raise
from hash256_miner.workers import coordinator as coordmod
from hash256_miner.workers import cpu_worker, gpu_worker

app = typer.Typer(no_args_is_help=True, add_completion=False)
console = Console()
log = logging.getLogger(__name__)


def _default_abi_path() -> Path:
    return Path(__file__).resolve().parent / "abi" / "miner.json"


@app.command("run")
def run_cmd(
    rpc: str = typer.Option(..., "--rpc", help="HTTP(S) RPC endpoint"),
    contract: str = typer.Option(..., "--contract", help="Mine contract address"),
    address: str = typer.Option(..., "--address", help="Your miner EVM address (checksum ok)"),
    abi: Path = typer.Option(None, "--abi", help="Contract ABI JSON path"),
    gpu: bool = typer.Option(True, "--gpu/--no-gpu", help="Try CUDA batch mining via CuPy"),
    threads: int = typer.Option(max(1, (os.cpu_count() or 4) - 1), "--threads", help="CPU worker processes"),
    batch_size: int = typer.Option(65_536, "--batch-size", help="GPU batch size (also scales CPU chunk)"),
    poll_interval: float = typer.Option(2.0, "--poll-interval", help="Epoch / state poll seconds"),
    nonce_seed: Optional[int] = typer.Option(
        None,
        "--nonce-seed",
        help="Fixed 64-bit nonce space offset (default: random)",
    ),
    private_key: Optional[str] = typer.Option(
        None,
        "--private-key",
        help="Hex private key (prefer HASH256_PRIVATE_KEY or --key-file)",
    ),
    key_file: Optional[Path] = typer.Option(None, "--key-file", help="File containing hex private key"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Find nonce but do not broadcast"),
    confirm: bool = typer.Option(False, "--confirm", help="Prompt before broadcasting"),
    skip_preverify: bool = typer.Option(
        False,
        "--skip-preverify",
        help="Skip eth_call simulation before broadcast (not recommended)",
    ),
    gas_limit: Optional[int] = typer.Option(None, "--gas-limit"),
    max_fee_per_gas: Optional[int] = typer.Option(None, "--max-fee-per-gas", help="Wei"),
    priority_fee: Optional[int] = typer.Option(None, "--priority-fee", help="Wei max priority fee"),
    tx_nonce: Optional[int] = typer.Option(None, "--tx-nonce", help="Override account tx nonce"),
    submit_rpc: Optional[str] = typer.Option(
        None,
        "--submit-rpc",
        envvar="HASH256_SUBMIT_RPC",
        help="Separate HTTP RPC only for eth_sendRawTransaction (e.g. Flashbots Protect); reads still use --rpc",
    ),
    flashbots: bool = typer.Option(
        False,
        "--flashbots/--no-flashbots",
        help="Shortcut: submit via Flashbots Protect (https://rpc.flashbots.net), mainnet (chainId=1) only",
    ),
    flashbots_bundle: bool = typer.Option(
        False,
        "--flashbots-bundle/--no-flashbots-bundle",
        help="通过 Flashbots relay 的 eth_sendBundle 提交（需 FLASHBOTS_AUTH_KEY 身份私钥，可与钱包私钥不同）",
    ),
    flashbots_relay: Optional[str] = typer.Option(
        None,
        "--flashbots-relay",
        help="Bundle 中继 URL（默认：主网 relay.flashbots.net，Sepolia relay-sepolia.flashbots.net）",
    ),
    flashbots_auth_key: Optional[str] = typer.Option(
        None,
        "--flashbots-auth-key",
        envvar="FLASHBOTS_AUTH_KEY",
        help="Flashbots 身份私钥 hex（仅用于 X-Flashbots-Signature，不托管资金）",
    ),
    flashbots_auth_key_file: Optional[Path] = typer.Option(
        None,
        "--flashbots-auth-key-file",
        help="同上，从文件读取（chmod 600）",
    ),
    bundle_target_block_offset: int = typer.Option(
        1,
        "--bundle-target-offset",
        help="bundle 的 blockNumber = latest + offset（eth_sendBundle）",
    ),
    log_level: str = typer.Option("INFO", "--log-level"),
) -> None:
    """Start HASH256-style Keccak PoW mining."""
    setup_logging(getattr(logging, log_level.upper(), logging.INFO))
    abi_path = abi or _default_abi_path()
    if not abi_path.is_file():
        console.print(f"[red]ABI not found: {abi_path}[/red]")
        raise typer.Exit(code=1)

    w3 = connect(rpc)
    c = load_contract(w3, contract, abi_path)

    coord = coordmod.Coordinator(
        w3=w3,
        contract=c,
        miner_address=address,
        abi_path=abi_path,
        poll_interval=poll_interval,
    )
    coord.refresh()
    coordmod.start_epoch_thread(coord)

    stop = coord.stop

    def _handle_sigint(_sig, _frame) -> None:
        console.print("\n[yellow]Stopping…[/yellow]")
        stop.set()

    signal.signal(signal.SIGINT, _handle_sigint)

    use_gpu = gpu and gpu_worker.cupy_available()
    if gpu and not use_gpu:
        log.warning("CuPy / CUDA not available; falling back to CPU-only mode")

    telemetry = Telemetry()
    gpu_base = (nonce_seed if nonce_seed is not None else random.randrange(1 << 62)) & ((1 << 64) - 1)
    cpu_base = random.randrange(1 << 60)

    chunk = max(4096, min(batch_size, 2_000_000))

    result: dict[str, int | None] = {"nonce": None}

    def gpu_thread_fn() -> None:
        nonlocal gpu_base
        if not use_gpu:
            return
        while not stop.is_set() and result["nonce"] is None:
            try:
                st = coord.current()
            except Exception:
                time.sleep(0.2)
                continue
            try:
                hit = gpu_worker.mine_batch_gpu(
                    st.challenge,
                    st.difficulty,
                    int(gpu_base),
                    int(batch_size),
                )
            except Exception:
                log.exception("GPU batch failed; stopping GPU thread")
                return
            telemetry.record_gpu(batch_size)
            gpu_base = (gpu_base + batch_size) & ((1 << 64) - 1)
            if hit is not None:
                result["nonce"] = int(hit)
                stop.set()
                return

    gt = threading.Thread(target=gpu_thread_fn, name="gpu-miner", daemon=True)
    if use_gpu:
        gt.start()

    def _one_cpu_batch(pool: ProcessPoolExecutor, st_gen: int) -> Optional[int]:
        nonlocal cpu_base
        try:
            st = coord.current()
        except Exception:
            return None
        if coord.generation_id() != st_gen:
            return None
        ch, diff = st.challenge, st.difficulty
        tw = max(1, threads)
        span = chunk * tw
        end = cpu_base + span

        futures = [
            pool.submit(cpu_worker.stride_search, ch, diff, cpu_base, end, tw, off) for off in range(tw)
        ]
        total_tried = 0
        for fut in futures:
            n, tried = fut.result()
            total_tried += tried
            if n is not None:
                telemetry.record_cpu(total_tried)
                return int(n)
        telemetry.record_cpu(total_tried)
        cpu_base = (cpu_base + span) % (1 << 62)
        return None

    st_gen = coord.generation_id()
    with ProcessPoolExecutor(max_workers=max(1, threads)) as pool:
        with tqdm(desc="Mining", unit="h", dynamic_ncols=True) as bar:
            with Live(console=console, refresh_per_second=4) as live:
                while not stop.is_set() and result["nonce"] is None:
                    st_gen = coord.generation_id()
                    hit = _one_cpu_batch(pool, st_gen)
                    if hit is not None:
                        result["nonce"] = hit
                        break
                    bar.update(chunk * max(1, threads))
                    tbl = Table(title="HASH256 miner")
                    try:
                        st = coord.current()
                        tbl.add_row("epoch", str(st.epoch))
                        tbl.add_row("difficulty", hex(st.difficulty))
                        tbl.add_row("challenge[0:4]", st.challenge[:4].hex())
                        tbl.add_row("block", str(st.block_number))
                        tbl.add_row("GPU", f"{telemetry.gpu_meter.rate_hz()/1e6:.3f} MH/s")
                        tbl.add_row("CPU", f"{telemetry.cpu_meter.rate_hz()/1e6:.3f} MH/s")
                        tbl.add_row("Total", f"{telemetry.total_rate_mh_s():.3f} MH/s")
                        tbl.add_row("GPU hashes", str(telemetry.gpu_hashes))
                        tbl.add_row("CPU hashes", str(telemetry.cpu_hashes))
                    except Exception:
                        tbl.add_row("status", "waiting for chain state…")
                    live.update(tbl)
                    time.sleep(0.02)

    if use_gpu:
        gt.join(timeout=2.0)

    if result["nonce"] is None:
        console.print("[yellow]No nonce found (stopped).[/yellow]")
        raise typer.Exit(code=0)

    found = int(result["nonce"])
    console.print(f"[green]Candidate nonce:[/green] {found}")

    st = coord.refresh()
    if not digest_less_than_difficulty(pow_hash(st.challenge, found), st.difficulty):
        console.print("[red]PoW candidate invalid against latest epoch/difficulty; aborting.[/red]")
        raise typer.Exit(code=2)

    verify_nonce_or_raise(
        w3,
        c,
        miner_address=address,
        challenge=st.challenge,
        nonce=found,
        difficulty=st.difficulty,
        skip_eth_call=skip_preverify,
    )
    if not skip_preverify:
        console.print("[green]eth_call mine() simulation succeeded.[/green]")
    else:
        err = try_eth_call_mine(w3, c, nonce=found, from_address=address)
        if err:
            console.print(f"[yellow]eth_call warning:[/yellow] {err}")

    if dry_run:
        console.print("[cyan]dry-run:[/cyan] not broadcasting")
        raise typer.Exit(code=0)

    try:
        pk = load_private_key(private_key=private_key, key_file=key_file)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    submit_modes = int(bool(submit_rpc)) + int(bool(flashbots)) + int(bool(flashbots_bundle))
    if submit_modes > 1:
        console.print("[red]--submit-rpc、--flashbots、--flashbots-bundle 只能三选一。[/red]")
        raise typer.Exit(code=1)

    if flashbots_bundle:
        try:
            fb_pk = load_private_key(
                private_key=flashbots_auth_key,
                key_file=flashbots_auth_key_file,
                env_var="FLASHBOTS_AUTH_KEY",
            )
        except ValueError as e:
            console.print(f"[red]Flashbots 身份私钥: {e}[/red]")
            raise typer.Exit(code=1) from e
        relay = flashbots_relay or default_relay_for_chain(st.chain_id)
        if not relay:
            console.print("[red]请使用 --flashbots-relay 指定 bundle 中继 URL（当前链无内置默认）。[/red]")
            raise typer.Exit(code=1)
        target_bn = int(w3.eth.block_number) + int(bundle_target_block_offset)
        console.print(f"[cyan]Flashbots eth_sendBundle →[/cyan] {relay} [dim](target block {target_bn})[/dim]")
        bundle_id = send_mine_flashbots_bundle(
            w3,
            c,
            private_key_hex=pk,
            flashbots_auth_private_key_hex=fb_pk,
            miner_address=address,
            pow_nonce=found,
            chain_id=st.chain_id,
            relay_url=relay,
            target_block_number=target_bn,
            gas_limit=gas_limit,
            max_fee_per_gas=max_fee_per_gas,
            priority_fee=priority_fee,
            tx_nonce=tx_nonce,
            dry_run=False,
            confirm=confirm,
        )
        if bundle_id:
            console.print(f"[green]Bundle 已提交 bundleHash:[/green] {bundle_id}")
        else:
            console.print("[yellow]未提交 bundle（确认被拒绝或 dry-run）。[/yellow]")
        return

    submit_w3 = None
    if flashbots:
        if st.chain_id != 1:
            console.print("[red]--flashbots 仅支持 Ethereum 主网 (chainId=1)；其他链请用 --submit-rpc 指定隐私节点。[/red]")
            raise typer.Exit(code=1)
        submit_w3 = connect(FLASHBOTS_PROTECT_MAINNET_RPC)
        console.print(f"[cyan]提交走 Flashbots Protect:[/cyan] {FLASHBOTS_PROTECT_MAINNET_RPC}")
    elif submit_rpc:
        submit_w3 = connect(submit_rpc)
        console.print("[cyan]提交走独立 RPC（隐私/保护通道）[/cyan]")

    txh = send_mine_transaction(
        w3,
        c,
        private_key_hex=pk,
        miner_address=address,
        pow_nonce=found,
        chain_id=st.chain_id,
        submit_web3=submit_w3,
        gas_limit=gas_limit,
        max_fee_per_gas=max_fee_per_gas,
        priority_fee=priority_fee,
        tx_nonce=tx_nonce,
        dry_run=False,
        confirm=confirm,
    )
    if txh:
        label = "已提交 (隐私/独立 RPC)" if submit_w3 else "已提交 (公共 RPC)"
        console.print(f"[green]{label}:[/green] {txh}")
    else:
        console.print("[yellow]Transaction not sent (confirm declined or dry-run).[/yellow]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
