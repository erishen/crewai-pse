"""SEO 程序化层（description / excerpt / 同系列内链）。

设计原则：能不给 LLM 做的就不给 LLM 做。description 直接复用 TL;DR 首条，
内链直接查已发布清单——两者都是零 token 的确定性操作，且不会像模型那样
编造不存在的链接。只有 excerpt（列表页卡片摘要）需要一次轻量归纳调用。
"""

import json
import re

from pipeline.blocks import FAQ_BLOCK_RE, TLDR_BLOCK_RE, TLDR_BULLET_RE
from pipeline.config import PUBLISHED_FILE
from pipeline.frontmatter import extract_frontmatter


def auto_description(text: str, lang: str = "zh") -> str:
    """从 TL;DR 首条要点自动生成 meta description（≤160 字）。

    TL;DR 已是 GEO 友好的「全局结论摘要」，复用其首条作搜索摘要零额外 token。
    无 TL;DR 时兜底取首个正文段落前 150 字。
    """
    m = TLDR_BLOCK_RE.search(text)
    if m:
        for line in m.group(1).splitlines():
            s = line.strip()
            bm = TLDR_BULLET_RE.match(s)
            if bm:
                return bm.group(1).strip()[:160]
    _fm, body = extract_frontmatter(text)
    paras = [p.strip() for p in body.split("\n\n") if p.strip() and not p.strip().startswith("#")]
    if paras:
        return paras[0][:150]
    return ""


def series_links(current_key: str, lang: str = "zh") -> list[tuple[str, str]]:
    """已发布且含 link 的同系列 PSE 兄弟文章 [(key, link)]，排除自己。

    仅链已发布兄弟，避免 404 内链；zh 取中文 link、en 取英文 link（缺则回退中文）。

    守卫：系列内链仅适用于 pse 系列文章。非 pse 项目（如 markdown-library /
    photo-library / video-library 这类本地优先 Rust 工具）并不属于该系列，
    不应被注入不相关的 pse 兄弟文章——它们的「相关项目」由人工在正文维护。
    否则（如 --translate 漏传 project_key 或当前为非 pse 项目）会把
    autogen-pse / crewai-pse / langgraph-pse / llamaindex-pse 等塞进一篇
    Markdown 工具的译文里，形成文不对题的串链。
    """
    out: list[tuple[str, str]] = []
    if "-pse" not in current_key:
        return out
    if not PUBLISHED_FILE.exists():
        return out
    try:
        published = json.loads(PUBLISHED_FILE.read_text(encoding="utf-8"))
    except Exception:
        return out
    for k, cfg in published.items():
        if k == current_key or "-pse" not in k:
            continue
        pub = cfg.get("published") or {}
        link = pub.get(lang, {}).get("link") or pub.get("zh", {}).get("link")
        if link:
            out.append((k, link))
    return out


def inject_series_links(text: str, current_key: str, lang: str = "zh") -> str:
    """在文末（源码导航之后）注入同系列文章内链。"""
    links = series_links(current_key, lang)
    if not links:
        return text
    heading = "## 同系列文章" if lang == "zh" else "## Related Articles in this Series"
    items = "\n".join(f"- [{k}]({link})" for k, link in links)
    return text.rstrip() + f"\n\n{heading}\n\n{items}\n"


def generate_excerpt(body: str, client, model: str, lang: str = "zh"):
    """调用 LLM 生成 1-2 句真实归纳，作为列表页专属摘要（区别于 SEO 的 description）。

    与 auto_description（复用 TL;DR 首条、零 token）不同，这里对正文做一次性轻量归纳，
    产出更贴近全文的列表卡片摘要。返回 (excerpt, prompt_tokens, completion_tokens)；
    失败（API 异常 / 正文过短）时回退 ("", 0, 0)，由调用方决定是否回退 description。
    """
    _fm, body_only = extract_frontmatter(body)
    # 去掉代码块与 TL;DR/速览，避免摘要抄袭代码或复述要点标题
    text = re.sub(r"```.*?```", "", body_only, flags=re.DOTALL)
    text = re.sub(TLDR_BLOCK_RE, "", text)
    text = re.sub(r"^##\s*速览.*$", "", text, flags=re.MULTILINE)
    # 去掉 FAQ 短代码，避免摘要被问答占满
    text = FAQ_BLOCK_RE.sub("", text)
    text = text.strip()[:4000]
    if not text:
        return "", 0, 0
    if lang == "en":
        sys_msg = (
            "You are a technical article summarization expert. Write a 1-2 sentence summary "
            "for a list-page card excerpt. Requirements: 1-2 sentences, under 220 characters; "
            "state plainly what project / engineering decision the article covers and what the "
            "reader takes away; no meta phrasing like 'this article' / 'we will'; do not copy "
            "the title; no code or symbol lists. Output only the summary, no quotes, no prefix."
        )
    else:
        sys_msg = (
            "你是技术文章的摘要专家。请基于下面的文章，写一段用于「列表页卡片摘要」的 1-2 句中文归纳。"
            "要求：1-2 句话、不超过 110 字；直接说清这篇文章讲了什么项目 / 什么核心工程决策 / 读者能带走什么；"
            "不要出现「本文」「这篇文章」「我们将」等元叙述；不要照抄标题；不要包含代码或符号名列表。"
            "只输出摘要本身，不要引号、不要前缀。"
        )
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_msg},
                {"role": "user", "content": text},
            ],
            max_tokens=200,
            temperature=0.3,
        )
        ex = (resp.choices[0].message.content or "").strip().strip('"').strip("'").strip()
        ex = ex.strip("`").strip()
        u = getattr(resp, "usage", None)
        p = u.prompt_tokens if u else 0
        c = u.completion_tokens if u else 0
        return ex, p, c
    except Exception as e:
        print(f"⚠️ 生成 excerpt 失败（列表摘要将回退 description）: {e}")
        return "", 0, 0
