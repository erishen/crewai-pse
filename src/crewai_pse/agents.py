"""Agent 创建 — 三个角色 + Crew 装配。"""

from crewai import Agent, Crew, Process, LLM

from .config import settings
from .prompts import load_prompt
from .tools import read_file, run_bash


def _create_llm() -> LLM:
    return LLM(
        model=settings.OPENAI_MODEL.replace("openai/", ""),
        api_key=settings.OPENAI_API_KEY,
        base_url=settings.OPENAI_BASE_URL,
    )


def create_planner(task: str | None = None) -> Agent:
    return Agent(
        role="Planner",
        goal="规划任务结构、委托执行、根据验证结果决策交付",
        backstory=load_prompt("planner", task),
        llm=_create_llm(),
        tools=[read_file, run_bash],
        verbose=True,
    )


def create_specialist(task: str | None = None) -> Agent:
    return Agent(
        role="Specialist",
        goal="先读取源码验证，再撰写文章。所有代码示例必须来自实际源码",
        backstory=load_prompt("specialist", task),
        llm=_create_llm(),
        tools=[read_file],
        verbose=True,
        allow_delegation=False,
    )


def create_evaluator(task: str | None = None) -> Agent:
    return Agent(
        role="Evaluator",
        goal="验证 Specialist 输出是否符合标准，给出判决",
        backstory=load_prompt("evaluator", task),
        llm=_create_llm(),
        tools=[read_file, run_bash],
        verbose=True,
    )


def create_crew(task: str | None = None) -> Crew:
    """创建 PSE 三角色 Crew（Sequential 流程）。"""
    return Crew(
        agents=[create_planner(task), create_specialist(task), create_evaluator(task)],
        process=Process.sequential,
        verbose=True,
    )
