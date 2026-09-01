"""清洗与净化层：把模型输出收敛成可发布的文章。

这一段是整条流水线里唯一「与模型的坏习惯作战」的地方，按污染类型分为四类：

1. 思维链泄漏  —— ReAct 的 Thought:/Action: 独白、工具调用串混进 frontmatter 或正文
2. 计划独白    —— 「让我先读取源码…」这类任务规划口吻出现在开头/结尾
3. 结构退化    —— 章节重复、标题层级不对、与 title 重复的 H2
4. 事实污染    —— 夸大词汇、源码里不存在的虚构符号

全部为确定性文本变换，不调 LLM。
"""

import re

from pipeline.config import (
    EXAGGERATED_TERMS,
    FIVE_SECTIONS,
    FM_VALUE_LEAK_RE,
    PLAN_MARKERS,
    REASONING_LEAK_LINE_RE,
    REASONING_LEAK_MARKERS,
)
from pipeline.frontmatter import strip_outer_fence


def has_reasoning_leak(text: str) -> bool:
    """检测成品（含 front matter）是否混入了内部推理独白（Thought:/Answer:/内容大纲/关键发现 等）。"""
    if not text:
        return False
    if REASONING_LEAK_LINE_RE.search(text):
        return True
    # front matter 的 title/description 可能整段就是 Thought:...（无换行），再兜底查一次标记
    return any(mk in text for mk in REASONING_LEAK_MARKERS)


def strip_reasoning_leaks(text: str) -> str:
    """删除正文里任意位置的思维链泄漏行（Thought:/Answer:/内容大纲/关键发现 等）。"""
    return REASONING_LEAK_LINE_RE.sub("", text)


def title_fallback(article: str, desc_fallback: str = "") -> str:
    """title 被判定为污染时的兜底：项目 desc → 正文首个二级标题 → 空串。

    返回空串是有意的：宁可让 validate.py 报「缺少 title」这种响亮错误，
    也不要把 `Action: read_file` 这类工具调用串发布成文章标题。
    """
    if desc_fallback:
        return desc_fallback
    m = re.search(r"^##\s+(.+?)\s*$", article, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return ""


def sanitize_frontmatter(article: str, desc_fallback: str = "") -> str:
    """净化 front matter 的 title/description：若其值混入了推理独白标记，则回退。

    - title 污染 → 回退 desc_fallback（通常是 p['desc']）
    - description 污染 → 整行删除（后续 auto_description 会干净地重新注入）
    不改变文章其它内容与结构。
    """
    m = re.match(r"^---\n(.*?)\n---\n", article, re.DOTALL)
    if not m:
        return article
    fm = m.group(1)
    cleaned = []
    for ln in fm.split("\n"):
        low = ln.strip()
        if low.startswith("title:"):
            val = ln.split(":", 1)[1].strip().strip('"').strip("'")
            # 1) ReAct 工具调用串（Action: read_file / read_file(...) 等）
            # 2) 常规思维链标记（Thought:/I need to/内容大纲…）
            if FM_VALUE_LEAK_RE.match(val) or any(mk in val for mk in REASONING_LEAK_MARKERS):
                cleaned.append(f"title: {title_fallback(article, desc_fallback)}")
                continue
            # 3) 第一人称 + 意图动词（I will read / We should check…）：典型思考过程泄漏。
            #    旧规则「含第一人称且 len > 20」会误伤 "My RAG Stack for 2026" 这类
            #    正常标题，故改为**必须同时出现意图动词**才判泄漏。
            if re.search(
                r"\b(?:I|we|my|let's)\b[^.!?\n]*?"
                r"\b(?:need|needed|will|'ll|should|must|am|'m|gonna|have to)\b",
                val, re.IGNORECASE,
            ):
                cleaned.append(f"title: {title_fallback(article, desc_fallback)}")
                continue
            # 4) 兜底：含第一人称、长于 20 字符、且以句号/问号/叹号结尾（像完整句子）
            if (re.search(r"\b(I|me|my|mine)\b", val, re.IGNORECASE)
                    and len(val) > 20
                    and val.rstrip().endswith((".", "?", "!"))):
                cleaned.append(f"title: {title_fallback(article, desc_fallback)}")
                continue
        if low.startswith("description:"):
            val = ln.split(":", 1)[1].strip().strip('"').strip("'")
            if FM_VALUE_LEAK_RE.match(val) or any(mk in val for mk in REASONING_LEAK_MARKERS):
                continue  # 删除整行，后续自动注入干净 description
        cleaned.append(ln)
    new_fm = "\n".join(cleaned)
    return f"---\n{new_fm}\n---\n" + article[m.end():]


def strip_planning_remnants(text: str) -> str:
    """剥离模型偶尔泄漏到成品里的内部规划独白
    （如「让我先读取源码和提纲，验证后再撰写文章」「现在让我读取核心源码文件」）。

    新管线中 Writer Agent 不带文件工具，本不应出现读取类独白；但作为保险，
    仍从开头与尾部双向剥离含标记的连续行（标记短语极难出现在真实散文里）。
    同时清除散落在正文任意位置的 ReAct 思维链泄漏行（Thought:/Answer:/内容大纲 等）。
    """
    # 1) 先清除散落在任意位置的思维链泄漏行
    text = strip_reasoning_leaks(text)
    lines = text.split("\n")
    # 2) 剥开头独白：跳过连续含标记的行（及空行），直到首行不含标记
    start = 0
    n = len(lines)
    while start < n:
        ln = lines[start].strip()
        if not ln:
            start += 1
            continue
        if any(mk in ln for mk in PLAN_MARKERS):
            start += 1
            continue
        break
    lines = lines[start:]
    # 3) 剥尾部独白：从末尾向前删除含标记的行
    while lines and any(mk in lines[-1].strip() for mk in PLAN_MARKERS):
        lines.pop()
    # 4) 去掉因截断残留的孤立分隔线 / 空行
    while lines and lines[-1].strip() in ("---", ""):
        lines.pop()
    while lines and lines[0].strip() in ("---", ""):
        lines.pop(0)
    return "\n".join(lines).strip()


def clean_section(text: str, expected_header: str) -> str:
    """清理单节 Writer 输出：剥围栏/Front Matter/模型自作主张的 H2，只留正文。

    F 风格逐节生成时，章节 H2 由程序控制，模型输出的任何 `##` 二级标题都丢弃
    （保留 `###` 子标题），避免模型自选章节标题破坏五段式结构。
    """
    t = strip_outer_fence(text)
    # 去掉 Front Matter
    m = re.match(r"^---\n.*?\n---\n", t + "\n", re.DOTALL)
    if m:
        t = t[m.end():].strip()
    # 丢弃模型自作主张的 H1/H2（章节标题由程序加），保留 ### 子标题
    lines = t.split("\n")
    kept = []
    for ln in lines:
        if re.match(r"^#{1,2}\s+\S", ln):
            continue
        kept.append(ln)
    t = "\n".join(kept).strip()
    # 清孤立强调符号
    t = re.sub(r"^(?:\s*\*{1,3}\s*)+", "", t)
    t = re.sub(r"(?:\s*\*{1,3}\s*)+$", "", t).strip()
    return t


def normalize_five_paragraph_headings(article: str) -> str:
    """F 风格确定性标题归一化（仅改标题层级，绝不改正文/代码）：

    1) 五段式章节（出发点/踩坑/调整/验证/结果 及英文对应词）无论模型写成 `###`/`####`，
       统一提升为 `##`，确保严格五段式 H2 结构。
    2) 删掉与 Front Matter `title` 重复的 `## <标题>` 行（模型常在正文开头重复写一遍标题）。
    3) 其它 `###` 子标题原样保留。
    """
    fm = re.search(r"^---\n.*?\n---\n", article, re.DOTALL)
    fm_title = ""
    if fm:
        mt = re.search(r"^title:\s*(.+)$", fm.group(0), re.MULTILINE)
        if mt:
            fm_title = mt.group(1).strip()
    out = []
    for ln in article.split("\n"):
        m = re.match(r"^(#{2,6})\s+(.+?)\s*$", ln)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            keyword = text.split("：")[0].split(":")[0].strip()
            if keyword in FIVE_SECTIONS:
                out.append(f"## {text}")  # 统一为二级标题
                continue
            if level == 2 and fm_title and text == fm_title:
                continue  # 删除与 Front Matter 重复的标题 H2
        out.append(ln)
    return "\n".join(out)


def extract_title(text: str) -> str:
    """从提纲或正文里提取文章标题。优先「### 标题」行，否则取首个 H1/H2。"""
    if not text:
        return ""
    m = re.search(
        r"(?:^|\n)#{1,3}\s*标题\s*\n+\s*\**\s*(.+?)\s*\**\s*(?:\n|$)",
        text,
    )
    if m:
        cand = m.group(1).strip().strip("*").strip()
        if not any(mk in cand for mk in REASONING_LEAK_MARKERS):
            return cand
    # 首个 H1/H2，但跳过混入思维链独白的行（如「## Thought: ...」）
    for m in re.finditer(r"^#{1,2}\s+(.+?)\s*$", text, re.MULTILINE):
        cand = m.group(1).strip().strip("*").strip()
        if cand and not any(mk in cand for mk in REASONING_LEAK_MARKERS):
            return cand
    return ""


def strip_exaggerated(text: str) -> str:
    """程序化删除包含夸大词汇的句子（按句号/换行分割）。"""
    for keyword in EXAGGERATED_TERMS:
        # 按句子边界（中文句号、换行、分号）逐句清理
        lines = text.split("\n")
        cleaned = []
        for line in lines:
            if keyword in line:
                # 尝试只删除包含关键词的子句（按中文标点分割）
                parts = re.split(r'([。；;])', line)
                filtered = []
                for i in range(0, len(parts) - 1, 2):
                    sentence = parts[i]
                    punct = parts[i + 1] if i + 1 < len(parts) else ""
                    if keyword not in sentence:
                        filtered.append(sentence + punct)
                # 处理最后一段（无标点结尾）
                if len(parts) % 2 == 1 and parts[-1]:
                    if keyword not in parts[-1]:
                        filtered.append(parts[-1])
                cleaned_line = "".join(filtered).strip()
                if cleaned_line:
                    cleaned.append(cleaned_line)
                # 如果整行都是关于该夸大词的，直接跳过
            else:
                cleaned.append(line)
        text = "\n".join(cleaned)
    return text


def strip_fictional_refs(text: str, refs: list[str]) -> str:
    """程序化删除仍存在的虚构代码引用，保证通过 verify_article。

    代码块：删除含引用的整行（含 def/class 行与调用行）。
    正文：按中英文句/子句边界删除含引用的子句，尽量保留其余内容。
    这是 LLM 自动修正失败后的确定性兜底，避免丢弃已生成的整篇文章。
    """
    if not refs:
        return text
    ref_set = set(refs)

    def _clean_code_block(block: str) -> str:
        lines = block.split("\n")
        kept = [ln for ln in lines if not any(r in ln for r in ref_set)]
        return "\n".join(kept)

    segments = re.split(r"(```[\s\S]*?```)", text)
    out = []
    for seg in segments:
        if seg.startswith("```") and seg.endswith("```"):
            cleaned = _clean_code_block(seg)
            # 若代码块内容被清空（只剩围栏），丢弃整块
            inner = cleaned.strip().strip("`").strip()
            if not inner:
                continue
            out.append(cleaned)
        else:
            lines = seg.split("\n")
            cleaned_lines = []
            for line in lines:
                if not any(r in line for r in ref_set):
                    cleaned_lines.append(line)
                    continue
                # 含引用：按子句拆分，仅删含引用的子句
                parts = re.split(r"([。；;！？!?])", line)
                filtered = []
                for i in range(0, len(parts) - 1, 2):
                    clause, punct = parts[i], parts[i + 1]
                    if not any(r in clause for r in ref_set):
                        filtered.append(clause + punct)
                if len(parts) % 2 == 1 and parts[-1] and not any(r in parts[-1] for r in ref_set):
                    filtered.append(parts[-1])
                kept = "".join(filtered).strip()
                if kept:
                    cleaned_lines.append(kept)
            out.append("\n".join(cleaned_lines))
    return "".join(out)


def dedup_repeated_blocks(text: str) -> str:
    """按 ## / ### 标题切块，相同标题的块只保留首次出现，消除整篇重复。

    正确跳过 ``` 代码围栏内的 `#` 注释行（否则会误判为标题）。
    """
    lines = text.split("\n")
    blocks: list[tuple[str, list[str]]] = []
    cur_key = "__preamble__"
    cur: list[str] = []
    in_fence = False
    for ln in lines:
        stripped = ln.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            cur.append(ln)
            continue
        is_heading = (not in_fence) and re.match(r"^#{2,3}\s+\S", ln)
        if is_heading:
            blocks.append((cur_key, cur))
            cur_key = ln.strip()
            cur = [ln]
        else:
            cur.append(ln)
    blocks.append((cur_key, cur))

    seen: set[str] = set()
    out: list[str] = []
    for key, block in blocks:
        if key != "__preamble__" and key in seen:
            continue  # 重复章节，跳过
        seen.add(key)
        out.extend(block)
    return "\n".join(out).strip()


def is_valid_article(text: str) -> bool:
    """判断合并 Agent 的输出是否为一篇真实文章，而非计划口吻/工具调用残片。"""
    if not text or len(text.strip()) < 600:
        return False
    # 计划口吻特征：开场即 Step 1 / 读取提纲 / 好的我 / 工具调用 json
    head = text.strip()[:150]
    if re.search(r"(step\s*\d|好的，我|我现在需要|读取提纲|合并草稿为|```json)", head, re.IGNORECASE):
        return False
    # 真实文章应有多个 markdown 标题
    if len(re.findall(r"^#{1,3}\s", text, re.MULTILINE)) < 2:
        return False
    return True


def merge_drafts(drafts: list[str]) -> str:
    """合并 Agent 失败时的兜底：直接拼接 Specialist 已落盘的真实草稿。"""
    cleaned = []
    for d in drafts:
        # 去掉每个草稿自带的 frontmatter（避免重复）
        m = re.match(r"^---\n.*?\n---\n", d.strip() + "\n", re.DOTALL)
        body = d[m.end():] if m else d
        cleaned.append(body.strip())
    return "\n\n".join(cleaned)
