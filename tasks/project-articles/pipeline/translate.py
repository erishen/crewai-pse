"""英文翻译阶段：翻译 → 对齐 FAQ/TL;DR 数量 → SEO 注入 → 落盘 → token 统计 → 可选发布。

翻译放在中文定稿之后：中文若未通过核查闸门就不会走到这里，避免把污染文本
送进翻译 Agent 二次放大。
"""

import os
import re
import subprocess
import sys

from pipeline import prompts
from pipeline.blocks import (
    count_faq_blocks,
    count_tldr_bullets,
    normalize_faq_blocks,
    normalize_tldr,
)
from pipeline.polish import polish_article, report_polish
from pipeline.config import ARTICLES_DIR, BASE, STANDARD_TAGS_EN
from pipeline.frontmatter import (
    clean_code_block_whitespace,
    en_taxonomy,
    fix_frontmatter_slug,
    inject_frontmatter_description,
    inject_frontmatter_excerpt,
    project_categories,
    project_tags,
    set_frontmatter_categories,
    set_frontmatter_tags,
    strip_outer_fence,
)
from pipeline.sanitize import normalize_five_paragraph_headings
from pipeline.seo import auto_description, generate_excerpt, inject_series_links

# 分节翻译：单批中文字符预算（约 4k 英文 tokens 输出，留足余量防截断）
CHUNK_CHAR_BUDGET = 6000
CHUNK_MAX_TOKENS = 8192
# 围栏代码块（判“正文是否被截短”时剔除）
FENCE_RE = re.compile(r"```[\s\S]*?```")



def looks_truncated(zh: str, en: str) -> str | None:
    """判断英文稿是否被输出上限截断；返回原因（None = 完整）。

    2026-11-14 nsbp-lume 实跑：中文 38KB 译成英文时烧完 8192 输出 tokens，
    正文停在半句话（"…as long as it can fool the original `exists"），
    ## 复盘 / 源码导航 / FAQ 整段丢失，却照样落盘。
    """
    if not en.strip():
        return "英文稿为空"
    ratio = len(en) / max(len(zh), 1)
    if ratio < 0.55:
        return f"英文长度只有中文的 {ratio:.0%}"
    zh_sections = zh.count("\n## ")
    en_sections = en.count("\n## ")
    if en_sections < zh_sections:
        return f"章节数 {en_sections} < 中文 {zh_sections}"
    last = ""
    for ln in reversed(en.strip().splitlines()):
        if ln.strip():
            last = ln.strip()
            break
    if not last.endswith(("。", ".", "!", "?", "！", "？", "[/faq]", "```", "---", "|")):
        return f"结尾疑被截断：…{last[-30:]}"
    return None


def split_sections(article: str) -> list[str]:
    """按二级标题切分为若干片段（前置块 + 每个 `##` 小节），代码围栏内的 `##` 不算标题。

    切分是分节翻译的基础：整篇单发必然撞输出上限，而按标题切能保证每批都
    “自带完整小节”，便于逐批判截断、失败只重试一小段。
    """
    sections: list[str] = []
    cur: list[str] = []
    in_fence = False
    in_frontmatter = False
    frontmatter_done = False
    for idx, line in enumerate(article.splitlines(keepends=True)):
        stripped = line.strip()
        if idx == 0 and stripped == "---":
            in_frontmatter = True
        elif in_frontmatter and stripped == "---":
            in_frontmatter = False
            frontmatter_done = True
        if not in_frontmatter and stripped.startswith("```"):
            in_fence = not in_fence
        is_heading = (
            not in_fence
            and not in_frontmatter
            and frontmatter_done
            and line.startswith("## ")
        )
        if is_heading and cur:
            sections.append("".join(cur))
            cur = []
        cur.append(line)
    if cur:
        sections.append("".join(cur))
    return sections


def pack_batches(sections: list[str], budget: int = CHUNK_CHAR_BUDGET) -> list[str]:
    """把片段按预算打包成批，保证每条 `##` 小节不被劈开。"""
    batches: list[str] = []
    cur: list[str] = []
    size = 0
    for sec in sections:
        if cur and size + len(sec) > budget:
            batches.append("".join(cur))
            cur, size = [], 0
        cur.append(sec)
        size += len(sec)
    if cur:
        batches.append("".join(cur))
    return batches


def _prose_len(text: str) -> int:
    """去掉围栏代码块后的字符数：代码原样保留，不参与“译文是否太短”的判定。"""
    return len(FENCE_RE.sub("", text))


def batch_problem(src: str, out: str) -> str | None:
    """单批译文体检：空稿 / 标题缺失 / 正文长度异常 / 结尾断裂。

    只比“正文”长度：技术稿里代码占比可达 35%，整篇字数比会误判成截断。
    """
    if not out.strip():
        return "译文为空"
    src_heads, out_heads = src.count("\n## "), out.count("\n## ")
    if out_heads < src_heads:
        return f"标题数 {out_heads} < 原文 {src_heads}"
    src_prose = _prose_len(src)
    ratio = _prose_len(out) / max(src_prose, 1)
    if src_prose > 200 and ratio < 0.5:
        return f"正文字数只有原文的 {ratio:.0%}"
    last = ""
    for ln in reversed(out.strip().splitlines()):
        if ln.strip():
            last = ln.strip()
            break
    if not last.endswith(("。", ".", "!", "?", "！", "？", "[/faq]", "```", "---", "|", ":", "`")):
        return f"结尾疑被截断：…{last[-30:]}"
    return None


def translate_in_batches(
    article: str, client, model: str, fallbacks: list[str] | None = None
) -> tuple[str, str | None, int, int]:
    """分节翻译整稿，返回 (英文全文, 截断原因, 输入 tokens, 输出 tokens)。

    每批先试主模型、再试回退模型（网关侧别名可跨供应商 failover，但单次
    502/半截仍需客户端换别名重试）；任何一批最终不合格就整篇判失败，
    不落盘半成品。
    """
    batches = pack_batches(split_sections(article))
    total = len(batches)
    models = [m for m in [model, *(fallbacks or [])] if m]
    print(f"✂️  分批翻译：{total} 批（每批 ≤ {CHUNK_CHAR_BUDGET} 字），候选模型 {models}")
    parts: list[str] = []
    pt_tokens = ct_tokens = 0
    for i, chunk in enumerate(batches, 1):
        budget = min(CHUNK_MAX_TOKENS, max(1024, int(len(chunk) * 2.2)))
        reason, text = "未执行", ""
        for candidate_model in models:
            for attempt in range(2):
                try:
                    resp = client.chat.completions.create(
                        model=candidate_model,
                        messages=[{
                            "role": "user",
                            "content": prompts.translate_batch_prompt(
                                chunk, i, total, is_first=(i == 1)
                            ),
                        }],
                        max_tokens=budget,
                        temperature=0.2,
                    )
                    text = resp.choices[0].message.content or ""
                    usage = getattr(resp, "usage", None)
                    if usage:
                        pt_tokens += usage.prompt_tokens
                        ct_tokens += usage.completion_tokens
                except Exception as e:
                    print(f"⚠️ 第 {i}/{total} 批 {candidate_model} 调用失败"
                          f"（第 {attempt + 1} 次）: {str(e)[:120]}")
                    text, reason = "", str(e)[:120]
                    continue
                reason = batch_problem(chunk, text)
                if reason is None:
                    break
                print(f"⚠️ 第 {i}/{total} 批 {candidate_model} 不合格"
                      f"（{reason}），重试")
            if reason is None:
                break
            print(f"↩️  第 {i}/{total} 批换下一个模型")
        if reason is not None:
            return "", f"第 {i}/{total} 批：{reason}", pt_tokens, ct_tokens
        parts.append(text.strip())
        print(f"   ✅ 第 {i}/{total} 批：{len(chunk)} 字 → {len(text)} 字符")
    return "\n\n".join(parts) + "\n", None, pt_tokens, ct_tokens


def translate(
    article: str,
    slug: str,
    client,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    do_publish: bool,
    project_key: str = "",
    p: dict = None,
):
    """翻译英文 + 统计 token + 可选发布。"""
    # 翻译英文
    print("🌐 翻译英文版...")
    fallbacks = [
        m.strip()
        for m in os.getenv("TRANSLATE_FALLBACK_MODELS", "deepseek-v4-flash,tr-zh").split(",")
        if m.strip() and m.strip() != model
    ]
    raw_en, truncation, pt2, ct2 = translate_in_batches(
        article, client, model, fallbacks
    )
    prompt_tokens += pt2
    completion_tokens += ct2
    print(f"📊 翻译: {pt2} 输入 + {ct2} 输出")
    if truncation is None:
        truncation = looks_truncated(article, raw_en)
    if truncation:
        # 截断必须在后处理之前判掉：否则空稿/残稿会先被 polish、SEO、excerpt 跑一遍
        print(f"❌ 英文稿截断未修复（{truncation}），不覆盖 en 文件，请重跑翻译")
        return


    try:
        en_content = normalize_tldr(
            normalize_faq_blocks(
                normalize_five_paragraph_headings(
                    set_frontmatter_tags(
                        fix_frontmatter_slug(
                            strip_outer_fence(clean_code_block_whitespace(raw_en)), "-en"
                        ),
                        en_taxonomy(project_tags(p, "en")) if p else STANDARD_TAGS_EN,
                    )
                ),
                "en",
            ),
            "en",
        )
        # 英文稿分类强制用英文分类名（projects.json 里是中文，需翻译；
        # 不能依赖 LLM 翻译，否则会把「架构」原样带过）。
        if p:
            en_content = set_frontmatter_categories(
                en_content, en_taxonomy(project_categories(p))
            )
        zh_faq, en_faq = count_faq_blocks(article), count_faq_blocks(en_content)
        if zh_faq != en_faq:
            print(f"⚠️ FAQ 区块数不一致：中文 {zh_faq} 条 / 英文 {en_faq} 条，请复核英文稿")
        elif en_faq:
            print(f"❓ FAQ: 中英文各 {en_faq} 条")
        zh_tldr, en_tldr = count_tldr_bullets(article), count_tldr_bullets(en_content)
        if zh_tldr != en_tldr:
            print(f"⚠️ TL;DR 条数不一致：中文 {zh_tldr} 条 / 英文 {en_tldr} 条，请复核英文稿")
        elif en_tldr:
            print(f"📝 TL;DR: 中英文各 {en_tldr} 条")
        # ── SEO 程序化层：英文也注入 description + 同系列内链 ──
        en_content = inject_series_links(en_content, project_key, "en")
        en_desc = auto_description(en_content, "en")
        if en_desc:
            en_content = inject_frontmatter_description(en_content, en_desc)
        # ── 列表页专属摘要：英文也生成 1-2 句真实归纳 ──
        en_excerpt, ep2, ec2 = generate_excerpt(en_content, client, model, "en")
        prompt_tokens += ep2
        completion_tokens += ec2
        if en_excerpt:
            en_content = inject_frontmatter_excerpt(en_content, en_excerpt)
        else:
            print("⚠️ 无法生成英文 excerpt（将回退 description）")
        en_content, en_polish = polish_article(en_content, "en")
        report_polish(en_polish)
        if truncation:
            print(f"❌ 英文稿截断未修复（{truncation}），不覆盖 en 文件，请重跑翻译")
            return
        en_path = ARTICLES_DIR / "en" / f"{slug}.md"
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(en_content, encoding="utf-8")
        print(f"✅ 英文已保存 → {en_path}")
    except Exception as e:
        print(f"⚠️ 翻译失败（跳过）: {e}")
