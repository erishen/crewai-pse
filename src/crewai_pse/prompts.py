"""提示词加载 — 从任务目录的 prompts/*.md 加载。"""

from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent.parent / "tasks"


def load_prompt(name: str, task: str | None = None) -> str:
    """加载指定角色的系统提示词。"""
    if task:
        prompt_path = PROMPTS_DIR / task / "prompts" / f"{name}.md"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
    return ""
