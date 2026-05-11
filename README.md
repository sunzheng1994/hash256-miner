# HASH256 本地挖矿工具（Python）

面向 [hash256.org/mine](https://hash256.org/mine) 类 **Keccak-256 PoW** 的本地矿工：从链上读取 `epoch` / `difficulty` 等参数，按 `abi.encodePacked` 规则构造 challenge，搜索 `nonce` 使 `keccak256(challenge || nonce)`（64 字节 packed）作为 uint256 小于 `difficulty`，并可选调用 `mine(uint256)` 上链。

## 快速开始

```bash
cd /Users/zhengsun/hash256-miner
python3.11 -m venv .venv && source .venv/bin/activate
pip install -U pip
pip install -e ".[dev]"
```

运行 CLI：`python -m hash256_miner run --help`（请确认 venv 的 `python` 与安装包所用版本一致，例如 3.11）。

设置 RPC 与密钥后：

```bash
hash256-mine run \
  --rpc "https://YOUR_RPC" \
  --contract "0xYourMineContract" \
  --address "0xYourMinerAddress" \
  --key-file ./key.hex
```

更多参数见 `hash256-mine run --help`。

## 远程 GPU（阿里云）+ 本机私钥 + Flashbots Bundle

架构要点：

1. **云端**只跑 PoW（`challenge` + `difficulty`），**不接收、不存储**钱包私钥。用 `REMOTE_MINER_API_KEY` + `X-API-Key` 做简单鉴权，生产环境务必 **HTTPS / 内网**。
2. **本机**通过 `hash256-local-ui` 读链、把任务 POST 给云端 Worker、拿到 `nonce` 后用 **`HASH256_PRIVATE_KEY`** 签 `mine(nonce)`，再用 **`FLASHBOTS_AUTH_KEY`**（仅用于中继身份签名，可另生成一把空钱包私钥）向 **`eth_sendBundle`** 提交 bundle。
3. 浏览器打开本仓库 [`web-ui/index.html`](web-ui/index.html)（由本地网关在根路径提供），表单里**不要**填私钥；私钥只放在本机 shell 环境变量中。

```bash
# --- 阿里云 GPU 机 ---
pip install -e ".[remote,gpu12]"   # 按需选 GPU extra
export REMOTE_MINER_API_KEY='长随机串'
hash256-remote-worker --host 0.0.0.0 --port 8787

# --- 本机（含私钥）---
export HASH256_PRIVATE_KEY='...'
export FLASHBOTS_AUTH_KEY='...'    # 另一把 key，仅 Flashbots 信誉 / 签名
export REMOTE_MINER_API_KEY='同上'  # 可选：与云端一致则前端可不填 key
pip install -e ".[remote]"
hash256-local-ui --host 127.0.0.1 --port 8790
# 浏览器访问 http://127.0.0.1:8790/
```

CLI 单机挖矿走 bundle：`hash256-mine run ... --flashbots-bundle --flashbots-auth-key-file ./fb.key`（与 `--flashbots` Protect RPC、`--submit-rpc` 三选一）。

## 隐私提交（Flashbots Protect 等）

读链（`eth_call`、`estimateGas`、`getTransactionCount` 等）仍走 **`--rpc`**。上链时的 **`eth_sendRawTransaction`** 可单独走保护/隐私 RPC，避免直接进入公共 mempool：

- **主网快捷方式**：`--flashbots`（仅 `chainId=1`），提交到 [Flashbots Protect](https://docs.flashbots.net/flashbots-protect/overview) 默认端点 `https://rpc.flashbots.net`。
- **任意隐私/自建中继**：`--submit-rpc "https://..."`，或通过环境变量 **`HASH256_SUBMIT_RPC`** 指定。
- 不要同时使用 `--flashbots` 与 `--submit-rpc`。

示例（主网 + Alchemy 读链 + Flashbots 提交）：

```bash
hash256-mine run \
  --rpc "https://eth-mainnet.g.alchemy.com/v2/KEY" \
  --flashbots \
  --contract "0x..." \
  --address "0x..." \
  --key-file ./key.hex
```

## 规则摘要（与计划一致）

1. `challenge = keccak256( abi.encodePacked(chainId, contract, miner, epoch) )` — 原像长度 **104** 字节。
2. `digest = keccak256( abi.encodePacked(challenge, nonce) )` — 原像长度 **64** 字节。
3. 有效当 `int(digest, big-endian) < difficulty`。

## 调优建议

- **`--batch-size`**：GPU 从 `65536` 量级起步，按显存与占用率调整；过大可能导致单次 kernel 过长、交互性变差。
- **`--threads`**：CPU 进程数以物理核为参考；过高可能因 GIL/调度反而下降（本实现 CPU 为 **多进程**）。
- **nonce 空间**：协调器按 worker 分区扫描；多机同时跑请自行错开 `--nonce-seed` 或使用不同矿工地址。
- **epoch 轮询**：默认周期性 RPC 拉取 `epoch`，变化后自动切换 challenge，无需每个 hash 都打 RPC。

详见 [INSTALL.md](INSTALL.md)。

## 免责声明

挖矿与链上交互可能产生 Gas 费用与资金风险。请自行审计合约 ABI 与地址，并在测试网验证后再用于主网。
