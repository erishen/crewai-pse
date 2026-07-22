"""工具函数 — 文件读取和 bash 执行。"""

import os
import subprocess
from pathlib import Path

from crewai.tools import tool

# 项目根目录，限制文件访问范围（run_bash 的工作目录与范围判定用此值）
_PROJECT_ROOT = Path(os.getenv("PSE_ROOT", Path.cwd())).resolve()

# read_file 允许的读取根目录集合。默认仅项目根；运行具体项目时由 run.py
# 调用 set_read_roots() 收紧为「镜像后的项目源码目录 + 提纲临时目录」，
# 使 Specialist 物理上读不到 crewai-pse 框架自身代码（避免文章跑题写成框架方法论）。
_READ_ROOTS = [_PROJECT_ROOT]


def set_read_roots(roots) -> None:
    """运行时收紧/放宽 read_file 的沙箱范围。roots 为路径或可解析为路径的对象列表。"""
    global _READ_ROOTS
    _READ_ROOTS = [Path(r).resolve() for r in roots]


@tool("read_file")
def read_file(path: str) -> str:
    """读取文件内容。参数 path 为文件路径（限定在 set_read_roots() 设定的允许目录内）。"""
    p = Path(path).resolve()
    if not any(str(p).startswith(str(r)) for r in _READ_ROOTS):
        return f"[错误] 路径超出项目范围: {path}"
    if not p.exists():
        return f"[错误] 文件不存在: {path}"
    if not p.is_file():
        return f"[错误] 不是文件: {path}"
    return p.read_text(encoding="utf-8")


# 危险命令片段黑名单（命中即拒绝，降低被诱导执行破坏命令的风险）
_DANGEROUS_PATTERNS = [
    r"\brm\s+-rf\b", r"\brm\s+-fr\b", r"\brm\s+-r\b", r"\brm\s+-R\b",
    r"\bmkfs\b", r"\bdd\b\s+if=", r":\(\)\s*\{", r"\bshutdown\b",
    r"\breboot\b", r"\bhalt\b", r"\bpoweroff\b", r">\s*/dev/sd",
    r"\bchmod\b\s+-R\s+777\s+/", r"\bchown\b\s+-R\s+.*\s+/",
    r"curl\b[^\n]*\|\s*(sh|bash)", r"wget\b[^\n]*\|\s*(sh|bash)",
    r"\bnc\b[^\n]*-e\b",
]


@tool("run_bash")
def run_bash(command: str) -> str:
    """执行 bash 命令并返回输出（受限沙箱：禁止破坏性命令，工作目录限定在项目根内）。"""
    import re

    for pat in _DANGEROUS_PATTERNS:
        if re.search(pat, command):
            return (
                f"[拒绝] 命令命中危险模式（{pat}），已被沙箱拦截。"
                "如需执行破坏性操作请人工进行。"
            )
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=30, cwd=str(_PROJECT_ROOT),
        )
        return result.stdout + "\n" + result.stderr
    except Exception as e:
        return f"[错误] {e}"
