"""从仓库根目录加载 ``.env`` 到进程环境（需安装 optional ``python-dotenv``）。"""

from __future__ import annotations

from pathlib import Path


def load_repo_env_file() -> None:
    """读取 ``<repo>/.env``；未安装 python-dotenv 时静默跳过。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    root = Path(__file__).resolve().parents[2]
    load_dotenv(root / ".env")
