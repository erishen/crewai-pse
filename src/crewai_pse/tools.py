"""工具函数 — 文件读取和 bash 执行。"""

import subprocess
from pathlib import Path

from crewai.tools import tool


@tool("read_file")
def read_file(path: str) -> str:
    """读取文件内容。参数 path 为文件路径。"""
    p = Path(path)
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    return p.read_text(encoding="utf-8")


@tool("run_bash")
def run_bash(command: str) -> str:
    """执行 bash 命令并返回输出。"""
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout + "\n" + result.stderr
    except Exception as e:
        return f"[错误] {e}"
