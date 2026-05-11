"""Optional CUDA batch search via CuPy RawKernel."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from hash256_miner.challenge import digest_less_than_difficulty, pow_hash, uint256_from_prefix_counter


def cuda_kernel_path() -> Path:
    return Path(__file__).resolve().parent.parent / "cuda_kernels" / "keccak256_batch.cu"


def cupy_available() -> bool:
    try:
        import cupy  # noqa: F401

        return True
    except Exception:
        return False


def mine_batch_gpu(
    challenge: bytes,
    difficulty: int,
    prefix24: bytes,
    base_counter: int,
    batch_size: int,
    *,
    block_dim: int = 256,
) -> Optional[int]:
    """
    GPU search on uint64 counters in [base_counter, base_counter + batch_size).
    Full on-chain nonce is uint256: ``prefix24 || counter_be_8`` (same layout as hash-cli-miner).
    Returns full uint256 mine nonce or None. Re-validates on CPU before use.
    """
    import cupy as cp

    if len(challenge) != 32:
        raise ValueError("challenge must be 32 bytes")
    if len(prefix24) != 24:
        raise ValueError("prefix24 must be 24 bytes")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if base_counter < 0 or base_counter + batch_size > 1 << 64:
        raise ValueError("GPU path supports uint64 counter range only")

    cuda_src = cuda_kernel_path().read_text(encoding="utf-8")
    mod = cp.RawModule(code=cuda_src, options=("--std=c++11",))
    kern = mod.get_function("hash256_mine_batch")

    ch = cp.frombuffer(challenge, dtype=cp.uint8)
    diff_b = difficulty.to_bytes(32, "big")
    diff = cp.frombuffer(diff_b, dtype=cp.uint8)
    px = cp.frombuffer(prefix24, dtype=cp.uint8)

    found = cp.zeros(1, dtype=cp.int32)
    found_counter = cp.zeros(1, dtype=cp.uint64)

    grid = (batch_size + block_dim - 1) // block_dim
    import numpy as np

    base_u = np.uint64(base_counter & ((1 << 64) - 1))
    batch_u = np.uint32(batch_size & 0xFFFFFFFF)

    found.fill(0)
    found_counter.fill(0)

    kern((grid,), (block_dim,), (ch, diff, px, base_u, batch_u, found_counter, found))
    cp.cuda.Stream.null.synchronize()

    if int(found[0]) != 1:
        return None
    ctr = int(found_counter[0])
    nonce_u256 = uint256_from_prefix_counter(prefix24, ctr)
    if not digest_less_than_difficulty(pow_hash(challenge, nonce_u256), difficulty):
        raise RuntimeError("GPU digest mismatch vs CPU reference implementation")
    return nonce_u256
