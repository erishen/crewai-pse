"""Agent 创建 — 三个角色 + Crew 装配。"""

import os
import time

# 必须在 import crewai / litellm 之前关闭 OpenTelemetry 观测导出。
# litellm 在 opentelemetry 存在时会自动启用 tracing，默认往 localhost:4317
# 发 span；无 collector 时持续抛 "Connection reset by peer" 的 span batch 噪音，
# 还会徒增连接 churn。关闭后不影响任何功能。
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("LITELLM_LOG", "ERROR")

from crewai import LLM, Agent, Crew, Process

from .config import settings
from .prompts import load_prompt
from .tools import read_file, run_bash


class RetryLLM(LLM):
    """给 CrewAI LLM 调用包一层指数退避重试。

    第三方网关（Agnes / DeepSeek）偶发故障：404（FastAPI 的 {"detail":"Not Found"}）、
    503、以及连接层 ConnectionResetError（"Connection reset by peer"）。这类瞬时故障
    重试即可恢复。默认 6 次、退避 2/4/8/16/32s，覆盖所有 Agent 的 LLM 调用。
    """

    def __init__(self, max_retries: int | None = None, backoff_base: float = 2.0, **kwargs):
        # 单次请求超时，避免连接挂死无限等待
        kwargs.setdefault("timeout", 180)
        super().__init__(**kwargs)
        self._max_retries = max_retries or settings.PSE_MAX_RETRIES or 6
        self._backoff_base = backoff_base

    def call(self, messages, tools=None, callbacks=None, available_functions=None,
             from_task=None, from_agent=None):
        last_err: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                return super().call(
                    messages,
                    tools=tools,
                    callbacks=callbacks,
                    available_functions=available_functions,
                    from_task=from_task,
                    from_agent=from_agent,
                )
            except Exception as e:  # noqa: BLE001 — 覆盖所有瞬时网关错误
                last_err = e
                if attempt < self._max_retries:
                    wait = self._backoff_base * (2 ** (attempt - 1))
                    msg = str(e)[:140].replace("\n", " ")
                    print(
                        f"⚠️ LLM 调用失败（第 {attempt}/{self._max_retries} 次），"
                        f"{wait:.0f}s 后重试: {type(e).__name__}: {msg}"
                    )
                    time.sleep(wait)
                else:
                    print(f"❌ LLM 调用在 {self._max_retries} 次重试后仍失败")
        raise last_err


def _create_llm() -> LLM:
    model = settings.OPENAI_MODEL
    # CrewAI requires "openai/" prefix when using custom base_url
    if not model.startswith("openai/"):
        model = f"openai/{model}"
    return RetryLLM(
        model=model,
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


def create_writer(task: str | None = None) -> Agent:
    """纯写作 Agent：不带任何文件读取工具。

    用于消除『Specialist 读源码 + 写文章』一步法导致的思考链泄漏
    （模型把「让我先读取…」这类工具调用前的独白当成正文输出）。
    Writer 只能基于任务描述中由程序喂入的真实源码片段写作，物理上无法调用 read_file。
    """
    return Agent(
        role="Writer",
        goal="基于提供的真实源码片段，按指定结构撰写技术文章；所有代码引用必须来自素材，绝不编造或自行读取文件",
        backstory="你是一名严谨的技术写作者，只使用上下文中给出的真实代码片段，绝不编造或自行读取文件。",
        llm=_create_llm(),
        tools=[],
        verbose=True,
        allow_delegation=False,
    )


def create_crew(task: str | None = None) -> Crew:
    """创建 PSE 三角色 Crew（Sequential 流程）。

    当前流程：Planner(提纲) → Specialist(展开)。
    Evaluator 已创建但不在 Sequential 流程中分配任务 —
    文章验证由 run.py 中的 _verify_article() 程序化完成（grep 源码 + 夸大词检查），
    比 LLM 评估更可靠。保留 Evaluator 以便未来扩展（如切换到 Hierarchical 流程）。
    """
    return Crew(
        agents=[create_planner(task), create_specialist(task), create_evaluator(task)],
        process=Process.sequential,
        verbose=True,
    )
