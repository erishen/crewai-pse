"""工具函数 — 文件读取和 bash 执行。"""

import os
import subprocess
from pathlib import Path

from crewai.tools import tool

# 项目根目录，限制文件访问范围
_PROJECT_ROOT = Path(os.getenv("PSE_ROOT", Path.cwd())).resolve()


@tool("read_file")
def read_file(path: str) -> str:
    """读取文件内容。参数 path 为文件路径（限定在项目目录内）。"""
    p = Path(path).resolve()
    if not str(p).startswith(str(_PROJECT_ROOT)):
        return f"[错误] 路径超出项目范围: {path}"
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    if not p.is_file():
        return f"[错误] 不是文件: {path}"
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
