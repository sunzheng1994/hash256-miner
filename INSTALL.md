# 安装说明

## Python

- 需要 **Python 3.10+**（推荐 3.11）。
- 建议使用虚拟环境：

```bash
cd /Users/zhengsun/hash256-miner
python3.11 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

## 仅 CPU 模式

不安装 CuPy 即可；使用 `--no-gpu` 或在没有 NVIDIA/CuPy 的环境下会自动退回 CPU。

## GPU（CUDA）模式

### 驱动

安装与你的 GPU 匹配的 **NVIDIA 驱动**（尽量新）。CuPy 的预编译 wheel 会带上大部分 CUDA 运行时库，但仍需驱动支持对应 CUDA 主版本。

### CuPy 版本选择

根据 [CuPy 安装文档](https://docs.cupy.dev/en/stable/install.html) 选择 **cupy-cuda12x** 或 **cupy-cuda11x** 等：

```bash
pip install "cupy-cuda12x>=13.0.0"
# 或
pip install "cupy-cuda11x>=12.0.0"
```

或使用本项目的 extras：

```bash
pip install -e ".[dev,gpu12]"
```

### CUDA Toolkit（nvcc）

- 若使用 **CuPy RawKernel / NVRTC** 在运行时编译内核，通常 **不需要** 单独安装完整 CUDA Toolkit；需保证 CuPy 与驱动匹配。
- 若你自行修改 `.cu` 并想用 **nvcc 离线编译** 为 cubin/ptx，再被 Python 加载，则需要安装对应版本的 **CUDA Toolkit**，并确保 `nvcc` 在 `PATH` 中。

### 常见错误

1. **Keccak-256 vs SHA3-256**：以太坊使用的是 **Keccak-256**（`pycryptodome` 的 `Crypto.Hash.keccak`），不是 NIST SHA3-256。
2. **CuPy 与 CUDA 主版本不匹配**：按官方矩阵更换 `cupy-cuda11x` / `cupy-cuda12x` 等包名。
3. **macOS**：Apple Silicon / 无 NVIDIA GPU 时无法使用本项目的 CUDA 路径；请使用 CPU 或多进程模式。

## 私钥与安全

- 使用环境变量 `HASH256_PRIVATE_KEY` 或 `--key-file`（`chmod 600`），避免在 shell 历史记录中直接 `--private-key`。
- 永远不要将私钥提交到 git。

## 隐私提交 RPC

- 通过 **`--submit-rpc`** 或 **`HASH256_SUBMIT_RPC`** 只替换「发送已签名交易」所用的节点；**`--rpc`** 继续负责读链与模拟。
- 以太坊主网可使用 **`--flashbots`**，等价于向 Flashbots Protect 端点提交（见 README）。
- 其他链请使用对应的支持私有 `eth_sendRawTransaction` 的 RPC / 中继 URL（以服务商文档为准）。

## 远程 Worker + 本地 UI（可选）

```bash
pip install -e ".[remote]"          # fastapi、uvicorn、httpx
# 或云端再加: pip install -e ".[remote,gpu12]"
```

- **`FLASHBOTS_AUTH_KEY`**：用于 `X-Flashbots-Signature` 的 **Flashbots 身份私钥**（与挖矿钱包可不同，不用于转账）。
- 详见仓库根目录 [`README.md`](README.md) 中「远程 GPU + 本机私钥 + Flashbots Bundle」一节。
