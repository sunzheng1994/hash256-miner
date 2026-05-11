#!/usr/bin/env bash
# 将本机 CUDA Toolkit 的 include 目录同步到远端（供 NVRTC / 编译找 cuda_runtime.h 等）。
# 使用前请安装与本机 GPU 驱动匹配的 CUDA Toolkit；路径按实际修改。
#
# 用法:
#   export REMOTE='ubuntu@你的ECS公网IP'
#   export REMOTE_DIR='/opt/cuda-local/include'   # 远端目录，需可写
#   export LOCAL_CUDA_INCLUDE='/usr/local/cuda/include'   # 本机头文件根（macOS/Linux 按实际改）
#   ./scripts/upload_cuda_includes_to_remote.sh
#
# 同步完成后，在远端 worker 的 systemd 或 shell 中增加（路径与 REMOTE_DIR 一致）:
#   export CUDA_INCLUDE_PATH=/opt/cuda-local/include
# 若 CuPy 仍走 NVRTC 且不读该变量，可配合仓库内 keccak256_batch.cu 的 __CUDACC_RTC__ 条件包含使用。

set -euo pipefail

REMOTE="${REMOTE:?请设置 REMOTE，例如 ubuntu@1.2.3.4}"
REMOTE_DIR="${REMOTE_DIR:?请设置 REMOTE_DIR，例如 /opt/cuda-local/include}"
LOCAL_CUDA_INCLUDE="${LOCAL_CUDA_INCLUDE:-/usr/local/cuda/include}"

if [[ ! -d "$LOCAL_CUDA_INCLUDE" ]]; then
  echo "错误: 本机不存在目录: $LOCAL_CUDA_INCLUDE" >&2
  echo "常见路径: Linux /usr/local/cuda/include ；macOS 可能为 /Developer/NVIDIA/CUDA-12.x/include" >&2
  exit 1
fi

echo "本机: $LOCAL_CUDA_INCLUDE"
echo "远端: $REMOTE:$REMOTE_DIR"
ssh -o BatchMode=yes "$REMOTE" "mkdir -p '$REMOTE_DIR'"
rsync -avz --delete-after \
  --include='*.h' --include='*.cuh' --include='*/' --exclude='*' \
  "$LOCAL_CUDA_INCLUDE/" "$REMOTE:$REMOTE_DIR/"

echo "完成。远端头文件数量示例:"
ssh "$REMOTE" "find '$REMOTE_DIR' -maxdepth 2 -name 'cuda_runtime.h' -print; du -sh '$REMOTE_DIR'"
