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

# 论断—源码一致性（2026-11-06）。旧版只要求「引用真实符号」，模型于是把
# README 的描述性事实包装成虚构物理约束：每个名词都真实，因果却是编的
# （例：「镜像里没有 nginx」→「SSE 长连接导致不能用 nginx」）。
# 规则必须与 pipeline/evidence.py 的确定性闸门一一对应，否则模型写得再好
# 也会被隔离，或被隔离原因误导成「换个风格重跑」。
GROUNDING_RULES = """
## ⚠️ 论断—源码一致性（硬性，违反会被程序隔离，换风格重跑无效）
1. 正文中每一处「不能 / 无法 / 必须 / 唯一 / 只能 / 决定了 / 之所以 / 直接原因 /
   根本原因 / 物理约束 / 淘汰 / 别无选择」这类**因果或排他性论断**，句末必须紧跟一个
   可核查锚点，格式严格为 `相对路径:行号`（例如 `frontend/src/api.ts:44`），
   只能从下面「源码证据锚点」清单里挑，严禁自行编造路径或行号。
2. 严禁把 README / 架构图里的**描述性事实**升级成排他性因果结论。
   反例：「因为 SSE 是长连接，所以容器里不能用 nginx」—— README 只说「镜像内没有
   nginx」，而项目里 nginx.conf 仍保留着 1800s 配置，这个因果不成立。
   写「某技术做不到 / 必须用某技术」之前，必须在源码实现或注释里找到依据并给出锚点。
3. 至少 1 个代码块**逐字摘录真实源码**（≥2 行，来自「真实源码片段」），
   块前用 `path:line` 标注出处；架构图、伪代码、README 文本块都不算。
4. 至少 1 个可复现命令，放进代码块，优先用「可复现命令」清单里的 `make <target>`；
   不得编造项目里不存在的 target。
5. 至少 2 个「非显而易见的亮点」必须来自**源码实现**（函数、常量、注释、配置行），
   不能只是 README 技术栈表 / 端口表 / 架构图的复述；全文至少出现 2 个不同实现文件的行号锚点。
"""

# 逐节流（F / G-K 骨架）每节都注入完整规则太浪费 token，用同语义的精简版。
GROUNDING_RULES_SECTION = """
## ⚠️ 论断—源码一致性（本节硬性要求）
1. 本节每处「不能/无法/必须/唯一/只能/决定了/之所以」类论断句末必须紧跟
   `相对路径:行号`，只能从「源码证据锚点」清单里挑，不得自编。
2. 严禁把 README 描述性事实升级成排他性因果（如「没有 nginx」→「不能用 nginx」）。
3. 本节至少引用 1 个真实源码实现行；全文还须有逐字摘录源码的代码块与
   Makefile 里真实存在的可复现命令（见「源码证据锚点 / 可复现命令」清单）。
4. **不会写代码块就别写**：没有真实源码片段支撑时，宁可整段用文字讲，也不要输出
   只有 ``` 和语言标签的**空围栏**——空代码块是假证据，会被程序删掉并判定证据残缺。
"""


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
                        source_map_block: str, style_instruction: str,
                        style_extra_rule: str) -> str:
    """Phase 1 —— Planner 生成提纲的任务描述。

    源码不再由模型自己用 read_file 读：程序预读后以「源码地图」注入（含真实符号行号）。
    这样既不可能出现「工具调用被当正文吐回、调用未执行」的空证据池，
    也不再有自编文件路径 / 自编行号的空间。
    """
    return f"""撰写 {desc}（{project_key}）的中文技术文章。

## 项目信息
- GitHub: {repo}
- 核心卖点: {highlights}

## 你的任务
1. 阅读下方【源码地图】——它是程序直接读取该项目的真实文件生成的，含目录结构、
   关键文件的开头片段与**真实符号行号**。你**没有任何文件工具**：不得调用
   read_file / run_bash，也不得在输出里出现任何工具调用标记（如 `Action:`、`<｜｜ … ｜｜>`）。
   需要的信息全在源码地图里；地图里没有的文件，就不要在提纲里引用。
2. 分析项目特点，从 6 种叙事风格中选择最合适的一种（问题驱动/设计决策/实战场景/架构漫游/对比分析/工程实践）
3. 基于源码地图提炼 2-3 个非显而易见的亮点，按选定风格组织提纲
4. 将推荐文件按主题相关性分成若干批次（每批不超过 5 个），标注每批对应的章节；
   批次里的文件路径必须**逐字取自源码地图的 `#### 路径` 标题**，使用相对仓库根的路径
   （如 `src/langgraph_pse/graph.py`），不带任何绝对路径前缀，也不要带 frameworks/ 前缀
5. 提纲末尾附上"交付完成"
6. 你规划的文章主题必须严格是本项目（{project_key}：{desc}，GitHub: {repo}）。绝对禁止规划任何关于 AI 写作框架、多 Agent 协作、验证机制、防幻觉、Planner/Specialist/Evaluator 角色等内容——那些不是本项目，不要把它们写进提纲。
7. 提纲中每个因果 / 排他性论断（「不能 / 必须 / 唯一 / 决定了 / 之所以」）都要标注对应的
   真实源码文件路径（如 `frontend/src/api.ts`），**不要写行号**——行号由程序按真实文件
   计算后注入写作环节，自编行号会与真实锚点冲突。另须规划至少 1 处「逐字摘录
   真实源码」的代码块与 1 个 Makefile 里真实存在的可复现命令。严禁只按 README 的
   技术栈表 / 端口表 / 架构图组织全文，至少 2 个亮点必须来自源码实现而非文档。
{style_instruction}{style_extra_rule}
{source_map_block}"""


def section_description(header: str, directive: str, decision_line: str, author: str,
                        excerpts_block: str, symbol_hint: str, variant_note: str,
                        prev_text: str, project_key: str, desc: str, repo: str,
                        anchor_block: str = "") -> str:
    """Phase 2（逐节流）—— 单节 Writer 的任务描述。"""
    return f"""你是一名技术文章作者。基于【真实源码片段】和【核心工程决策线】，写文章「{header}」这一节的正文。

## 核心工程决策线（全文主线，用第一人称展开）
{decision_line or desc}
{author}

## 真实源码片段（你只能引用这里出现的代码/符号，严禁编造任何函数/类/文件名）
{excerpts_block}

## 真实符号候选（优先引用，**非穷举**）
{symbol_hint}

{anchor_block}

## 本节要写的内容
{directive}{variant_note}

## 已写好的前面几节（保持连贯，本节承接它们）
{prev_text}

## 硬性要求
1. 直接输出本节正文，**不要写标题**（程序会加 H2），**不要写 Front Matter**，不保存到文件。
2. 可用 `###` 子标题和代码块，但**不得出现 `##` 二级标题**（章节结构由程序控制）。
3. 所有代码必须来自「真实源码片段」，不得编造；符号候选不足以支撑论述时，直接引用
   源码片段里确实出现的函数/常量/类名，同样合规。**严禁自行造词或改写符号名**。
4. 不提及本地绝对路径、缓存目录、AI 写作框架、多 Agent、验证机制等内部信息。
5. 只写本项目（{project_key}：{desc}，GitHub: {repo}）。本项目的技术栈、文件结构与
   核心实现必须能在正文里被具体指认；**严禁写成放之任何项目皆准的通用架构议论文**。
{GROUNDING_RULES_SECTION}
输出仅本节正文。"""


def full_article_description(structure_rule: str, decision_line: str, author: str,
                             excerpts_block: str, project_key: str, desc: str, repo: str,
                             style_instruction: str, style_extra_rule: str,
                             anchor_block: str = "") -> str:
    """Phase 2（单 Writer 流）—— 全文一次性写作的任务描述（A-E 风格）。"""
    return f"""你是一名技术文章作者。根据下面提供的【真实源码片段】和【文章结构】，写一篇完整的中文技术文章。

## 文章结构（必须严格遵守）
{structure_rule}

## 核心工程决策线（围绕这条主线展开，用第一人称）
{decision_line or desc}
{author}

## 真实源码片段（你只能引用这里出现的代码/符号，严禁编造任何函数/类/文件名）
{excerpts_block}

{anchor_block}

## 写作要求
1. 直接从第一个标题开始输出，**不要写 Front Matter，不要保存到文件**，把整篇文章作为你的回答直接返回。
2. 正文不以 H1（`#`）开头，用 `##` / `###` 组织章节。
3. 所有代码必须来自上面「真实源码片段」，不得编造；引用真实符号即可，无需复述整段代码。
4. 在文章末尾加「源码导航」小节，用相对路径列出关键文件（如 `backend/app/crud.py`）。
5. 在「源码导航」之后追加一个 FAQ 区块：用 `[faq]` 与 `[/faq]` 包裹 4-6 条问答，每条 `问：...` / `答：...` 各占一行（正文式短代码），内容须基于文章、严禁编造。
6. 不提及本地绝对路径、缓存目录等内部信息。
7. 你只写本项目（{project_key}：{desc}，GitHub: {repo}）。绝对禁止写任何关于 AI 写作框架、多 Agent、验证机制、防幻觉、或本写作管线本身的内容。
8. 正文必须出现至少 2 个不同实现文件的 `path:line` 锚点，且因果/排他论断就近带锚点；否则程序会直接隔离，不会进入发布。
{GROUNDING_RULES}
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
5. **严禁删除** `相对路径:行号` 锚点、逐字摘录真实源码的代码块、以及可复现命令；
   这些是「论断—源码一致性」闸门的证据链，删掉会让整篇被隔离

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


def grounding_repair_description(claims: list[str], anchor_block: str) -> str:
    """定向证据回填 —— 只处理「无就近源码依据」的论断，输出**补丁**而非全文。

    为什么用补丁而不是「重写全文」：实测（2026-11-14 nsbp-lume）模型会把全文缩成
    只回被改的几段（761 tokens / 7k 字原文），全文校验直接判退化、修复拿不到手。
    补丁格式让模型只回「原句 → 改句」，由程序精确替换——其余内容一个字都不会被模型碰。
    """
    bullets = "\n".join(f"论断{i}：{c}" for i, c in enumerate(claims, start=1))
    return f"""你是技术文章作者。下面 {len(claims)} 条论断在「论断—源码一致性」检查中没有
就近的源码依据，请逐条给出修补方案。

## 待修论断
{bullets}

## 可用的真实源码锚点（只能从这里挑，严禁自编路径或行号）
{anchor_block}

## 每条论断三选一
1. **补锚点**（首选）：若这条论断确实能由清单里某个锚点支撑，就在该句句末紧邻处补
   `（见 `相对路径:行号`）`。
2. **降级表述**：补不了就把排他/因果措辞换成非排他说法——必须→需要、唯一→主要、
   只能→通常、「之所以 A 是因为 B」→「A 的原因是 B」（B 作为观察而非断言），
   并删掉没有依据的因果连接词。
3. **删句**：两者都做不到就整句删掉（改为写 DELETE）。

## 硬约束
- 不得新增任何事实、数字、文件路径、符号名、`make` target；不得把降级句写成新论断。
- 只改这几句，不要动其他任何内容。
- **「改为」必须是原句的最小改动版**：只允许做两种编辑——句末补锚点，或把个别排他/因果
  词换成非排他词。**不得更换句子主题、不得删掉原句的信息再塞进无关内容**；主题一旦变，
  这条修补会被程序丢弃（按字符相似度校验）。

## 输出格式（严格，**不要输出文章全文**）
每条论断一个块，块之间空一行：

[REPAIR 1]
原文：<从文章里**逐字**复制的原句，含标点，一个字都不改>
改为：<替换后的完整句子；整句删除就写 DELETE>

[REPAIR 2]
原文：…
改为：…

原文必须能在文章里逐字找到（程序按字符串匹配替换），否则该条修补会被丢弃。"""


def grounding_repair_full_article_description(claims: list[str], anchor_block: str,
                                             article: str) -> str:
    """回填的兜底形态：要求输出**完整文章**（补丁式解析不到时才用）。"""
    bullets = "\n".join(f"{i}. {c}" for i, c in enumerate(claims, start=1))
    return f"""你是技术文章作者。下面这篇文章有 {len(claims)} 条论断没有就近的源码依据。
请**只改这些论断所在的句子/段落**，然后把**完整文章全文**原样输出（其余段落一字不动）。

## 待修论断
{bullets}

## 可用的真实源码锚点（只能从清单里挑，严禁自编路径或行号）
{anchor_block}

## 每条论断三选一
1. 补锚点：句末紧邻处补 `（见 `相对路径:行号`）`；
2. 降级表述：必须→需要、唯一→主要、只能→通常、「之所以 A 是因为 B」→「A 的原因是 B」；
3. 删句：整句删掉并衔接通顺。

不得新增任何事实、数字、路径、符号。输出必须含 front matter 与全部既有代码块，
不要解释、不要用 ``` 包住全文。

## 文章原文
{article}"""
