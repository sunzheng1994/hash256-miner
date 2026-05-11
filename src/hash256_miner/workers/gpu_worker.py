"""Optional CUDA batch search via CuPy RawKernel."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from hash256_miner.challenge import digest_less_than_difficulty, pow_hash


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
    base_nonce: int,
    batch_size: int,
    *,
    block_dim: int = 256,
) -> Optional[int]:
    """
    GPU search on nonces in [base_nonce, base_nonce + batch_size) (64-bit lower space).
    Returns first hit or None. Always re-validates on CPU before use.
    """
    import cupy as cp

    if len(challenge) != 32:
        raise ValueError("challenge must be 32 bytes")
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if base_nonce < 0 or base_nonce + batch_size > 1 << 64:
        raise ValueError("GPU path currently supports 64-bit nonce range only")

    cuda_src = cuda_kernel_path().read_text(encoding="utf-8")
    mod = cp.RawModule(code=cuda_src, options=("--std=c++11",))
    kern = mod.get_function("hash256_mine_batch")

    ch = cp.frombuffer(challenge, dtype=cp.uint8)
    diff_b = difficulty.to_bytes(32, "big")
    diff = cp.frombuffer(diff_b, dtype=cp.uint8)

    found = cp.zeros(1, dtype=cp.int32)
    found_nonce = cp.zeros(1, dtype=cp.uint64)

    grid = (batch_size + block_dim - 1) // block_dim
    import numpy as np

    base_u = np.uint64(base_nonce & ((1 << 64) - 1))
    batch_u = np.uint32(batch_size & 0xFFFFFFFF)

    found.fill(0)
    found_nonce.fill(0)

    kern((grid,), (block_dim,), (ch, diff, base_u, batch_u, found_nonce, found))
    cp.cuda.Stream.null.synchronize()

    if int(found[0]) != 1:
        return None
    n = int(found_nonce[0])
    if not digest_less_than_difficulty(pow_hash(challenge, n), difficulty):
        raise RuntimeError("GPU digest mismatch vs CPU reference implementation")
    return n
