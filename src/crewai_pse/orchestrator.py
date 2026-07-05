"""编排层 — 创建 Task 列表，运行 Crew。"""

from dataclasses import dataclass

from crewai import Crew, Task

from .config import settings


@dataclass
class RunResult:
    outcome: str
    output: str
    reason: str = ""


def run_crew(
    crew: Crew,
    planner_task: Task,
    specialist_task: Task,
    evaluator_task: Task,
) -> RunResult:
    """运行 Crew，返回结果。

    CrewAI Sequential 流程按 task 顺序执行：
    1. Planner 执行 planner_task
    2. Specialist 执行 specialist_task（上下文包含 Planner 的输出）
    3. Evaluator 执行 evaluator_task（上下文包含前两个的输出）
    """
    crew.tasks = [planner_task, specialist_task, evaluator_task]

    max_retries = settings.PSE_MAX_RETRIES
    for attempt in range(1, max_retries + 1):
        crew.kickoff(inputs={"attempt": attempt, "max_retries": max_retries})

        article = str(specialist_task.output.raw) if specialist_task.output else ""
        verdict = str(evaluator_task.output.raw) if evaluator_task.output else ""

        if "交付完成" in verdict or "PASS" in verdict:
            return RunResult(outcome="PASS", output=article)
        if "BLOCKED" in verdict or "FAIL" in verdict:
            return RunResult(outcome="BLOCKED", output=article, reason=verdict)

    return RunResult(outcome="TIMEOUT", output="", reason=f"超过最大重试 {max_retries} 次")
