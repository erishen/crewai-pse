"""环境引导：sys.path、.env 与 crewai-pse 依赖。

必须在任何 pipeline 子模块之前导入——`load_dotenv` 会填充
`crewai_pse.config.settings` 所需的环境变量（OPENAI_API_KEY 等），
而 `sys.path` 决定 `crewai_pse` 包能否被找到。

只做引导，不定义任何业务常量（那些在 config.py）。
"""

import sys
from pathlib import Path

from dotenv import load_dotenv

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
