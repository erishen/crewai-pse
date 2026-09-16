"""环境引导：sys.path、.env 与 crewai-pse 依赖。

必须在任何 pipeline 子模块之前导入——`load_dotenv` 会填充
`crewai_pse.config.settings` 所需的环境变量（OPENAI_API_KEY 等），
而 `sys.path` 决定 `crewai_pse` 包能否被找到。

只做引导，不定义任何业务常量（那些在 config.py）。
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 禁用 CrewAI 后台遥测线程：该线程在主流程结束后仍存活，会在解释器退出
# (finalization) 阶段与 stdin 缓冲锁竞争，触发 CPython 3.13 的致命错误
# (_enter_buffered_busy, SIGABRT/134)。配合 run.py 的 os._exit 退出兜底。
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "1")

# BASE = tasks/project-articles，CREWAI_PSE_ROOT = frameworks/crewai-pse
BASE = Path(__file__).resolve().parent.parent
CREWAI_PSE_ROOT = BASE.parent.parent

sys.path.insert(0, str(BASE))
sys.path.insert(0, str(CREWAI_PSE_ROOT / "src"))
load_dotenv(CREWAI_PSE_ROOT / ".env")

from crewai import Crew, Process, Task  # noqa: E402
from crewai_pse import create_crew, create_writer  # noqa: E402
from crewai_pse.config import settings  # noqa: E402
from crewai_pse.tools import set_read_roots  # noqa: E402

__all__ = [
    "BASE",
    "CREWAI_PSE_ROOT",
    "Crew",
    "Process",
    "Task",
    "create_crew",
    "create_writer",
    "set_read_roots",
    "settings",
]
