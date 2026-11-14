"""英文翻译阶段：翻译 → 对齐 FAQ/TL;DR 数量 → SEO 注入 → 落盘 → token 统计 → 可选发布。

翻译放在中文定稿之后：中文若未通过核查闸门就不会走到这里，避免把污染文本
送进翻译 Agent 二次放大。
"""

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
    raw_en, truncation = "", None
    for _attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompts.translate_prompt(article)}],
                # 8192 对 38KB 中文稿不够：实测译到正文中段就烧完上限，尾部章节整段丢失
                max_tokens=16384,
                temperature=0.3,
            )
            candidate = resp.choices[0].message.content or ""
        except Exception as e:
            print(f"⚠️ 翻译调用失败（第 {_attempt + 1} 次）: {e}")
            candidate = ""
        usage = getattr(resp, "usage", None) if candidate else None
        if usage:
            prompt_tokens += usage.prompt_tokens
            completion_tokens += usage.completion_tokens
            print(f"📊 翻译: {usage.prompt_tokens} 输入 + {usage.completion_tokens} 输出")
        raw_en, truncation = candidate, looks_truncated(article, candidate)
        if truncation is None:
            break
        print(f"⚠️ 英文稿疑似截断（{truncation}）"
              + ("" if _attempt else "，重试一次…"))

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
