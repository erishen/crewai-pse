"""所有 LLM 提示词的构造入口。

提示词集中在一个文件里，原因很实际：它们是最常调整的部分，散落在流程代码中
时每次微调都要在几千行里翻找；集中后可以整体通读、对比各阶段的约束是否一致。

约束共同点（每条提示词都在防的几件事）：
1. 跑题      —— 写成「AI 写作框架 / 多 Agent」而非目标项目
2. 编造      —— 写出源码里不存在的函数/类/文件名
3. 结构退化  —— 从指定叙事风格退回通用模板
4. 泄密      —— 把本地绝对路径、缓存目录、管线内部信息写进正文
"""

from pipeline.config import AUTHOR_FILE, STYLE_EXTRA_F, STYLE_NAMES, STYLE_SPECS


def author_block() -> str:
    """读取 author.md 生成人设注入块（文件缺失则为空串）。

    作者人设仅塑造第一人称的语气/视角，不构成事实依据。
    """
    if not AUTHOR_FILE.exists():
        return ""
    try:
        text = AUTHOR_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not text:
        return ""
    return (
        "\n\n## 作者人设（仅用于第一人称的语气与视角，不构成事实依据）\n"
        f"{text}\n"
        "要求：仅据此塑造“我”的说话方式、取舍观与视角；"
        "严禁编造本段之外的经历或事实；不要在本段文字原样出现在正文里，也不要在文中做自我评价。"
    )


def build_variant_note(hook: str, person: str, timeline: str) -> str:
    """表述变体指令块（本次随机一次，Planner 与逐节 Writer 共用同一组）。"""
    if not any((hook, person, timeline)):
        return ""
    return (
        "\n\n## 表述变体（本次随机，要求遵循）\n"
        f"- 开场：{hook}\n"
        f"- 人称：{person}\n"
        f"- 时间轴：{timeline}\n"
        "以上变体只改变表达方式，不得破坏本风格的核心结构与禁止项。\n"
    )


def build_style_instruction(style_override: str, spec_block: str = "", variant_note: str = "") -> str:
    """风格强制指令块：`必须使用 X 风格，不要选择其他风格`。"""
    if not style_override:
        return ""
    return (
        f"\n\n## ⚠️ 风格强制\n"
        f"**必须使用 {style_override}. {STYLE_NAMES[style_override]} 风格**，不要选择其他风格。"
        f"所有写作严格按该风格的结构组织，不得退回通用模板。\n"
        f"{spec_block}{variant_note}"
    )


def build_spec_block(style_override: str) -> str:
    """程序化章节骨架提示（仅对 STYLE_SPECS 里的风格有内容）。"""
    if style_override in STYLE_SPECS:
        secs = " → ".join(s[0] for s in STYLE_SPECS[style_override])
        return f"\n该风格采用程序化章节骨架，必须围绕此骨架组织提纲：{secs}"
    return ""


def style_extra(style_override: str) -> str:
    """风格专属硬规则（目前只有 F 有）。"""
    return STYLE_EXTRA_F if style_override == "F" else ""


def planner_description(desc: str, project_key: str, repo: str, highlights,
                        sandbox_dir, style_instruction: str, style_extra_rule: str) -> str:
    """Phase 1 —— Planner 生成提纲的任务描述。"""
    return f"""撰写 {desc}（{project_key}）的中文技术文章。

## 项目信息
- GitHub: {repo}
- 核心卖点: {highlights}
- 源码目录: {sandbox_dir}（这是该项目的仓库根目录，仅供 read_file 读取；文章中引用文件路径请用相对此目录的路径，如 `src/langgraph_pse/graph.py`，不要暴露此目录本身，也不要带 frameworks/ 前缀）

## 你的任务
1. 用 read_file 读取源码目录下的关键文件（README.md + 核心 .py 文件）——**必须先读真实源码，再规划**
2. 分析项目特点，从 6 种叙事风格中选择最合适的一种（问题驱动/设计决策/实战场景/架构漫游/对比分析/工程实践）
3. 基于源码提炼 2-3 个非显而易见的亮点，按选定风格组织提纲
4. 将推荐文件按主题相关性分成若干批次（每批不超过 5 个），标注每批对应的章节
5. 提纲末尾附上"交付完成"
6. 你规划的文章主题必须严格是本项目（{project_key}：{desc}，GitHub: {repo}）。绝对禁止规划任何关于 AI 写作框架、多 Agent 协作、验证机制、防幻觉、Planner/Specialist/Evaluator 角色等内容——那些不是本项目，不要把它们写进提纲。
{style_instruction}{style_extra_rule}"""


def section_description(header: str, directive: str, decision_line: str, author: str,
                        excerpts_block: str, symbol_hint: str, variant_note: str,
                        prev_text: str, project_key: str, desc: str, repo: str) -> str:
    """Phase 2（逐节流）—— 单节 Writer 的任务描述。"""
    return f"""你是一名技术文章作者。基于【真实源码片段】和【核心工程决策线】，写文章「{header}」这一节的正文。

## 核心工程决策线（全文主线，用第一人称展开）
{decision_line or desc}
{author}

## 真实源码片段（你只能引用这里出现的代码/符号，严禁编造任何函数/类/文件名）
{excerpts_block}

## 真实符号白名单（引用代码时优先使用，严禁编造白名单外符号）
{symbol_hint}

## 本节要写的内容
{directive}{variant_note}

## 已写好的前面几节（保持连贯，本节承接它们）
{prev_text}

## 硬性要求
1. 直接输出本节正文，**不要写标题**（程序会加 H2），**不要写 Front Matter**，不保存到文件。
2. 可用 `###` 子标题和代码块，但**不得出现 `##` 二级标题**（章节结构由程序控制）。
3. 所有代码必须来自「真实源码片段」，不得编造；引用真实符号即可。
4. 不提及本地绝对路径、缓存目录、AI 写作框架、多 Agent、验证机制等内部信息。
5. 只写本项目（{project_key}：{desc}，GitHub: {repo}）。

输出仅本节正文。"""


def full_article_description(structure_rule: str, decision_line: str, author: str,
                             excerpts_block: str, project_key: str, desc: str, repo: str,
                             style_instruction: str, style_extra_rule: str) -> str:
    """Phase 2（单 Writer 流）—— 全文一次性写作的任务描述（A-E 风格）。"""
    return f"""你是一名技术文章作者。根据下面提供的【真实源码片段】和【文章结构】，写一篇完整的中文技术文章。

## 文章结构（必须严格遵守）
{structure_rule}

## 核心工程决策线（围绕这条主线展开，用第一人称）
{decision_line or desc}
{author}

## 真实源码片段（你只能引用这里出现的代码/符号，严禁编造任何函数/类/文件名）
{excerpts_block}

## 写作要求
1. 直接从第一个标题开始输出，**不要写 Front Matter，不要保存到文件**，把整篇文章作为你的回答直接返回。
2. 正文不以 H1（`#`）开头，用 `##` / `###` 组织章节。
3. 所有代码必须来自上面「真实源码片段」，不得编造；引用真实符号即可，无需复述整段代码。
4. 在文章末尾加「源码导航」小节，用相对路径列出关键文件（如 `backend/app/crud.py`）。
5. 在「源码导航」之后追加一个 FAQ 区块：用 `[faq]` 与 `[/faq]` 包裹 4-6 条问答，每条 `问：...` / `答：...` 各占一行（正文式短代码），内容须基于文章、严禁编造。
6. 不提及本地绝对路径、缓存目录等内部信息。
7. 你只写本项目（{project_key}：{desc}，GitHub: {repo}）。绝对禁止写任何关于 AI 写作框架、多 Agent、验证机制、防幻觉、或本写作管线本身的内容。

{style_instruction}{style_extra_rule}"""


def translate_prompt(article: str) -> str:
    """中文 → 英文翻译（保留代码、frontmatter 结构与 FAQ/TL;DR 区块）。"""
    return (
        "Translate the following Chinese technical article to English. "
        "Keep ALL code examples, file paths, class names, function names, and the "
        "YAML front matter structure unchanged. Translate the `title` field, but change "
        "the `slug` field to end with `-en` (e.g. `foo` -> `foo-en`), never `-zh`. "
        "Translate the `description` field too (it carries the meta description). "
        "IMPORTANT — FAQ blocks: keep every `[faq]` ... `[/faq]` shortcode exactly as a "
        "block (same count, same order, tags on their own lines). Inside each block, "
        "translate the question and answer text and rewrite the labels as `Q: ` and `A: ` "
        "(each on its own line). Never turn them into attribute form "
        '(`[faq question="..."]`) and never drop or merge blocks. '
        "IMPORTANT — TL;DR section: keep the `## 速览（TL;DR）` heading and translate each bullet "
        "point (same count, same order). Do not convert it to a paragraph or drop it. "
        f"Output ONLY the translated article:\n\n{article}"
    )


def fix_prompt(article: str, code_refs: list[str], exaggerations: list[str]) -> str:
    """核查未通过时的修正提示（删除虚构引用 / 夸大词，其余保持不变）。"""
    fix_parts = []
    if code_refs:
        fix_parts.append(f"**虚构代码引用（在源码中不存在，必须删除）**: {', '.join(code_refs)}")
    if exaggerations:
        exagg_words = [f.split("—")[0].replace("[夸大]", "").strip() for f in exaggerations]
        fix_parts.append(f"**禁止使用的夸大词汇（必须从文章中彻底删除这些词）**: {', '.join(exagg_words)}")
    fix_body = "\n".join(fix_parts)
    return f"""以下文章被核查发现问题，请修正。

{fix_body}

**规则**:
1. 虚构代码引用：删除包含该引用的句子或代码示例，不要创造性替换
2. 夸大词汇：删除包含该词汇的整句话，不要尝试改写
3. 保持文章其余部分不变
4. 输出修正后的完整文章（从 Front Matter 开始），不输出解释

## 当前文章
{article}"""


def topic_rewrite_prompt(article: str, leak_hits: list[str],
                         project_key: str, desc: str, repo: str) -> str:
    """跑题重写提示：文章写成了 crewai-pse 框架本身，需整篇重写。

    与 fix_prompt 的区别：跑题是骨架错了，删引用救不回来，必须整篇重写。
    """
    return f"""你写的文章完全跑题了。它讲的是 crewai-pse 这个写作框架本身（出现了 {'、'.join(leak_hits)} 等框架内部符号），但本项目是 {project_key}（{desc}，GitHub: {repo}）。

请完全重写整篇文章，只围绕 {project_key} 展开，基于你用 read_file 读取的该项目真实源码。绝对不要写任何关于 AI 写作框架、多 Agent、验证机制、防幻觉、或本写作管线本身的内容。
输出完整修正文章（从 Front Matter 开始），不输出解释。

## 当前文章
{article}"""
