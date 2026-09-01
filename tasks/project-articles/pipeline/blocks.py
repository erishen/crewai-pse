"""FAQ / TL;DR 区块：归一化、计数与自动生成。

FAQ 用 `[faq]...[/faq]` 短代码交给 WordPress 端解析；TL;DR 是 GEO 友好的
「全局结论摘要」区块。两者都要求模型输出稳定结构，但模型经常写成属性式
或漏闭合标签，故在此做确定性归一化。
"""

import re

from pipeline.frontmatter import extract_frontmatter

FAQ_BLOCK_RE = re.compile(r"\[faq\b([^\]]*)\](.*?)\[/faq\]", re.S)
FAQ_QA_RE = re.compile(
    r"(?:问|Q(?:uestion)?)\s*[:：]\s*(.*?)\s*(?:答|A(?:nswer)?)\s*[:：]\s*(.*)", re.S
)
FAQ_ATTR_Q_RE = re.compile(
    r"""question\s*=\s*(?:"|&quot;|'|&#39;)?(.*?)(?:"|&quot;|'|&#39;)?\s*$""", re.S
)

TLDR_BLOCK_RE = re.compile(
    r"^#{1,4}\s*[^\n]*TL;?DR\b[^\n]*\n+(.*?)(?=\n#{1,4}\s|\Z)",
    re.MULTILINE | re.DOTALL,
)
TLDR_BULLET_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+(.*)$")


def normalize_faq_blocks(text: str, lang: str = "zh") -> str:
    """归一化 [faq] 区块，保证 WordPress 端短代码能被正确解析。

    1. 属性式 `[faq question="..."]答案[/faq]` → 正文式。Markdown 转换会把属性里的
       引号实体化成 &quot;，属性式在 WordPress 端解析不出来，必须转成正文式。
    2. `[faq]` / `[/faq]` 与问、答各自独占一行（多余空白、单行写法都会被拉平）。
    3. 英文版统一成 `Q: / A:`，中文版统一成 `问：/答：`。

    识别不出问答结构的区块原样保留，交由后续人工/评审处理，不静默丢内容。
    """
    q_label, a_label = ("Q: ", "A: ") if lang == "en" else ("问：", "答：")

    def repl(m):
        attrs, body = m.group(1) or "", (m.group(2) or "").strip()
        if "[faq" in body:
            # 少了闭合标签导致跨块吞并，宁可原样保留也不合并两条问答
            return m.group(0)
        qa = FAQ_QA_RE.search(body)
        if qa:
            q, a = qa.group(1), qa.group(2)
        else:
            attr_q = FAQ_ATTR_Q_RE.search(attrs.strip())
            if not attr_q:
                return m.group(0)
            q, a = attr_q.group(1), body
        q = re.sub(r"\s+", " ", q).strip()
        a = a.strip()
        if not q or not a:
            return m.group(0)
        return f"[faq]\n{q_label}{q}\n{a_label}{a}\n[/faq]"

    return FAQ_BLOCK_RE.sub(repl, text)


def count_faq_blocks(text: str) -> int:
    return len(FAQ_BLOCK_RE.findall(text))


def auto_generate_faq(body: str, project: dict, decision_line: str,
                      client, model: str, lang: str = "zh") -> str:
    """文章缺 [faq] 时，基于正文自动生成 4-6 条 FAQ（正文式短代码）。

    返回 `[faq]...[/faq]` 字符串；失败或正文过短返回 ''。生成的区块随后会被
    `normalize_faq_blocks` 归一为 `问：/答：` 正文式，并通过 `count_faq_blocks` 计数。
    """
    _fm, body_only = extract_frontmatter(body)
    text = re.sub(r"```.*?```", "", body_only, flags=re.DOTALL)
    text = FAQ_BLOCK_RE.sub("", text)
    text = text.strip()[:3500]
    # 正文剥离后可能为空（文章以代码块/标题为主、或本次生成偏薄），此时退化为
    # 仅用项目元信息生成，避免因为取不到正文就直接返回 '' 导致 FAQ 闸门整轮失败。
    body_available = bool(text)
    if lang == "en":
        sys_msg = (
            "You are a technical FAQ writer. Write a "
            "[faq]...[/faq] block with 4-6 reader questions and concise answers about the "
            "project's design decisions, usage, and safety. "
            + ("" if body_available else
               "No article body is provided, so base answers ONLY on the project description "
               "and do NOT invent file names, APIs, or facts. ")
            + "Output format (each Q/A on its own line, body-style shortcode):\n"
            "[faq]\nQ: ...\nA: ...\n[/faq]\n"
            "Do NOT invent file names, APIs, or facts not in the source. Output only the block."
        )
    else:
        sys_msg = (
            "你是一名技术 FAQ 写手。请写一个 `[faq]...[/faq]` 区块，"
            "包含 4-6 条读者关心的问答，聚焦本项目的设计取舍、使用方式、安全边界。"
            + ("" if body_available else
               "未提供文章正文，请仅依据项目描述作答，严禁编造文件名、API 或事实。")
            + "输出格式（每条问答各占一行，正文式短代码）：\n[faq]\n问：...\n答：...\n[/faq]\n"
            "严禁编造文章里没有的文件名、API 或事实。只输出该区块本身。"
        )
    user_content = (
        f"项目：{project.get('desc', '')}\n"
        f"GitHub: {project.get('repo', '')}\n"
        f"核心主线：{decision_line or ''}\n"
    )
    if body_available:
        user_content += f"\n文章正文：\n{text}"
    else:
        user_content += "\n（未提供文章正文，请仅依据上述项目描述生成通用且准确的 FAQ。）"
    last_err = ""
    for _attempt in range(2):  # 模型偶发空输出/漏闭合标签，给一次重试机会
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": sys_msg},
                    {"role": "user", "content": user_content},
                ],
                max_tokens=900,
                temperature=0.4,
            )
            out = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            last_err = f"调用失败: {e}"
            continue
        # 剥离可能的 Markdown 围栏与大小写标签变体
        out = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", out).strip()
        out = out.replace("[FAQ]", "[faq]").replace("[/FAQ]", "[/faq]")
        if "[faq]" not in out:
            if not out:
                last_err = "模型返回空内容"
                continue
            out = f"[faq]\n{out}\n[/faq]"
        # 模型偶发只写 [faq] 开头、漏掉 [/faq] 闭合 → 正则解析不出完整块，
        # 上层计数为 0 却显示「已自动补入」，必须在此补齐
        if out.count("[/faq]") < out.count("[faq]"):
            out = out.rstrip() + "\n[/faq]"
        # 硬校验：至少解析出 1 个含问答对的完整块，否则视为失败并重试
        if not any(
            FAQ_QA_RE.search(body) for _attrs, body in FAQ_BLOCK_RE.findall(out)
        ):
            last_err = f"输出缺少问答对：{out[:120]!r}"
            continue
        return out
    print(f"⚠️ 自动生成 FAQ 失败（重试后仍无效）：{last_err}")
    return ""


def normalize_tldr(text: str, lang: str = "zh") -> str:
    """归一化 TL;DR 区块，保证 GEO 友好：标题统一 `## 速览（TL;DR）`、3-5 条要点、置于引言之后。

    1. 识别各种写法（### TL;DR / TL;DR（本文要点） / TL;DR： / ## 速览（TL;DR） 等）→ 统一 `## 速览（TL;DR）`。
    2. 仅抽取 bullet 形式的要点（- / * / 数字序号），归一为 `- `，上限 5 条。
    3. 把 TL;DR 块移动到「引言 / Introduction」章节之后、第一个技术章节之前；若文章无显式引言章节，
       则放在正文第一个 `## ` 章节之后。列表页摘要现以 crewai-pse 生成的专属 excerpt（写入 post_excerpt）为准，
       本步主要影响正文阅读顺序与「description 缺失时」的兜底摘要质量，避免兜底摘要以“速览（TL;DR）”开头。
    4. 识别不出结构（无 TL;DR / 无可识别要点）时原样返回，不静默丢内容。
    """
    fm, body = extract_frontmatter(text)
    block_m = TLDR_BLOCK_RE.search(body)
    if not block_m:
        return text
    bullets = []
    for line in block_m.group(1).splitlines():
        s = line.strip()
        if not s:
            continue
        bm = TLDR_BULLET_RE.match(s)
        if bm:
            b = bm.group(1).strip()
            if b:
                bullets.append(b)
    if not bullets:
        return text
    bullets = bullets[:5]
    tldr = "## 速览（TL;DR）\n\n" + "\n".join(f"- {b}" for b in bullets) + "\n"
    before = body[: block_m.start()].rstrip()
    after = body[block_m.end():].lstrip()
    rest = (before + "\n\n" + after).strip() + "\n"
    # 定位插入点：优先「引言 / Introduction」，否则第一个 ## 章节之后
    intro_m = re.search(r"^##\s*(?:引言|Introduction)[^\n]*", rest, re.MULTILINE | re.IGNORECASE)
    anchor = intro_m if intro_m else re.search(r"^##\s", rest, re.MULTILINE)
    if anchor:
        tail = rest[anchor.end():]
        nxt = re.search(r"\n##\s", tail)
        if nxt:
            pos = anchor.end() + nxt.start()
            new_body = rest[:pos].rstrip() + "\n\n" + tldr + "\n" + rest[pos:].lstrip()
        else:
            new_body = rest.rstrip() + "\n\n" + tldr + "\n"
    else:
        new_body = tldr + rest
    if fm:
        return fm.rstrip() + "\n\n" + new_body
    return new_body


def count_tldr_bullets(text: str) -> int:
    block_m = TLDR_BLOCK_RE.search(text)
    if not block_m:
        return 0
    return len(
        [1 for ln in block_m.group(1).splitlines() if TLDR_BULLET_RE.match(ln.strip())]
    )
