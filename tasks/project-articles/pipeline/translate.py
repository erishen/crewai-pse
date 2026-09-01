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
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompts.translate_prompt(article)}],
            max_tokens=8192,
            temperature=0.3,
        )
        raw_en = resp.choices[0].message.content or ""
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
        t_usage = resp.usage
        if t_usage:
            prompt_tokens += t_usage.prompt_tokens
            completion_tokens += t_usage.completion_tokens
            print(f"📊 翻译: {t_usage.prompt_tokens} 输入 + {t_usage.completion_tokens} 输出")
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
        en_path = ARTICLES_DIR / "en" / f"{slug}.md"
        en_path.parent.mkdir(parents=True, exist_ok=True)
        en_path.write_text(en_content, encoding="utf-8")
        print(f"✅ 英文已保存 → {en_path}")
    except Exception as e:
        print(f"⚠️ 翻译失败（跳过）: {e}")

    # Token 统计
    total = prompt_tokens + completion_tokens
    print(f"\n📊 Token 总消耗: {prompt_tokens} 输入 + {completion_tokens} 输出 = {total} 合计")
    if total:
        print(f"💰 预估费用: ¥{total * 0.0000014:.4f}")

    # 发布到 WordPress
    if do_publish and project_key:
        print(f"\n{'='*60}")
        print("🚀 发布文章到 WordPress...")
        pub_result = subprocess.run(
            [sys.executable, str(BASE / "publish.py"), project_key],
            capture_output=True,
            text=True,
        )
        print(pub_result.stdout)
        if pub_result.returncode != 0:
            print(pub_result.stderr)
            sys.exit(pub_result.returncode)
