"""将 ARTICLES_DIR 中生成的文章发布到 WordPress。

用法:
    python publish.py <项目名> [--local]

默认发布到线上（--prod），加 --local 发布到本地环境。
通过 subprocess 调用发布工具的 writeArticle.js 完成发布。
发布成功后自动将文章链接和 wp_id 回写到 projects.json。
发布工具路径通过 WP_TOOLS_DIR 环境变量配置。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

BASE = Path(__file__).parent
load_dotenv(BASE.parent.parent / ".env")
ARTICLES_DIR = Path(os.getenv("ARTICLES_DIR", ""))
WP_TOOLS_DIR = Path(os.getenv("WP_TOOLS_DIR", ""))
PROJECTS_FILE = BASE / "projects.json"
PUBLISHED_FILE = BASE / "projects-published.json"

# WordPress API 配置（用于更新链接页面）
WP_API_URL = os.getenv("WP_API_URL", "https://your-site.com/wp-json/wp/v2")
WP_USERNAME = os.getenv("WP_USERNAME", "your-username")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
LINKS_PAGE_ID = int(os.getenv("LINKS_PAGE_ID", "1"))

# 强制统一出网代理：shell/uv/make 链路可能把旧端口（如 7890）一路带进 node，
# 而 make 的 export 未必能压过它。这里在 spawn writeArticle.js 前覆盖代理环境变量，
# 确保 axios 走 .env 里 WP_PROXY 指定的正确端口。换端口只改 .env 一处即可。
_PROXY = (
    os.getenv("WP_PROXY")
    or os.getenv("HTTPS_PROXY")
    or os.getenv("HTTP_PROXY")
    or "http://127.0.0.1:7897"
)
for _pk in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ[_pk] = _PROXY

# ⚠️ 关键：Python 的 urllib 默认【不读取】环境变量代理。上面设的 HTTP_PROXY 只对
# subprocess 调起的 node/axios 生效；本文件内 urllib.request.urlopen() 仍会裸直连，
# 在国内访问海外 VPS 上的 erishen.cn 时直接失败。必须显式安装 ProxyHandler 让 urllib
# 走代理，否则 _set_lang_meta / _update_links_page 等写入 meta 的请求会静默失败。
import urllib.request as _urllib

_urllib.install_opener(
    _urllib.build_opener(_urllib.ProxyHandler({"http": _PROXY, "https": _PROXY}))
)


def _load_pending() -> dict:
    if not PROJECTS_FILE.exists():
        print(f"❌ 找不到待写项目配置文件: {PROJECTS_FILE}")
        sys.exit(1)
    with open(PROJECTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _load_published() -> dict:
    if not PUBLISHED_FILE.exists():
        return {}
    with open(PUBLISHED_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_pending(projects: dict) -> None:
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _save_published(projects: dict) -> None:
    with open(PUBLISHED_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _get_project_desc(project_key: str) -> str:
    """跨两个文件查找项目 desc（待写队列优先）。"""
    if PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            pending = json.load(f)
        if project_key in pending:
            return pending[project_key].get("desc", project_key)
    if PUBLISHED_FILE.exists():
        with open(PUBLISHED_FILE, encoding="utf-8") as f:
            published = json.load(f)
        if project_key in published:
            return published[project_key].get("desc", project_key)
    return project_key


def _parse_output(stdout: str) -> dict:
    """从 writeArticle.js 的 stdout 中提取链接和 wp_id。"""
    info = {}
    link_match = re.search(r"🔗 链接:\s*(https?://\S+)", stdout)
    if link_match:
        info["link"] = link_match.group(1)
    id_match = re.search(r"📄 文章ID:\s*(\d+)", stdout)
    if id_match:
        info["wp_id"] = int(id_match.group(1))
    return info


def _add_cross_links(articles_dir: Path, slug: str, slug_zh: str, slug_en: str, pub_info: dict) -> None:
    """在文章 frontmatter 后插入对方语言的链接。"""
    if "zh" not in pub_info or "en" not in pub_info:
        return  # 只有一篇文章时不需要交叉链接

    zh_link = pub_info["zh"]["link"]
    en_link = pub_info["en"]["link"]

    for lang, other_link, link_text, lang_slug in [
        ("zh", en_link, "> [🇬🇧 English Version]({})", slug_zh),
        ("en", zh_link, "> [🇨🇳 中文版]({})", slug_en),
    ]:
        article_path = articles_dir / lang / f"{lang_slug}.md"
        # 兼容旧文件名
        if not article_path.exists():
            legacy = articles_dir / lang / f"{slug}.md"
            if legacy.exists():
                article_path = legacy
        if not article_path.exists():
            continue

        content = article_path.read_text(encoding="utf-8")
        # 检查是否已有交叉链接
        if "English Version" in content or "中文版" in content:
            # 更新现有链接
            if lang == "zh":
                content = re.sub(
                    r"> \[🇬🇧 English Version\]\([^)]+\)",
                    link_text.format(other_link),
                    content,
                )
            else:
                content = re.sub(
                    r"> \[🇨🇳 中文版\]\([^)]+\)",
                    link_text.format(other_link),
                    content,
                )
        else:
            # 在 frontmatter 后插入新链接
            fm_end = content.find("---", 3)
            if fm_end != -1:
                insert_pos = fm_end + 3
                # 跳过换行符
                while insert_pos < len(content) and content[insert_pos] in "\r\n":
                    insert_pos += 1
                new_line = link_text.format(other_link) + "\n\n"
                content = content[:insert_pos] + new_line + content[insert_pos:]

        article_path.write_text(content, encoding="utf-8")
        print(f"  🔗 已更新 {lang} 文章的交叉链接")


def _fetch_post_title(link: str) -> str | None:
    """从 WordPress 按链接 slug 获取文章真实标题（用于链接页面展示）。"""
    import base64
    import json
    import urllib.parse
    import urllib.request

    slug = link.rstrip("/").split("/")[-1]
    if not slug:
        return None
    try:
        url = f"{WP_API_URL}/posts?slug={urllib.parse.quote(slug)}&per_page=1"
        req = urllib.request.Request(url)
        auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read().decode())
            if isinstance(data, list) and data:
                return data[0].get("title", {}).get("rendered")
    except Exception as e:
        print(f"  ⚠️ 获取文章真实标题失败：{e}")
    return None


def _update_links_page(project_key: str, pub_info: dict) -> None:
    """更新链接页面，添加或更新新发布的文章。

    链接文字使用文章在 WordPress 上的【真实标题】（完整、不截断），
    而非 projects.json 的 desc，避免链接页文字与文章标题不一致。
    已存在的链接会更新其锚点文字（幂等），不存在才在列表头部插入。
    """
    if not WP_APP_PASSWORD:
        print("  ⚠️ 未设置 WP_APP_PASSWORD，跳过链接页面更新")
        return

    if "zh" not in pub_info:
        return  # 只有中文文章才添加到链接页面

    import base64
    import json
    import urllib.error
    import urllib.request
    from datetime import date

    link = pub_info["zh"]["link"]

    # 优先使用文章在 WordPress 上的真实标题
    title = _fetch_post_title(link)
    if not title:
        # 兜底：用 projects 配置里的 desc（不再截断），避免覆盖成空白
        title = _get_project_desc(project_key)

    month = date.today().strftime("%Y-%m")

    # 获取当前链接页面原始内容（context=edit 拿到 raw，避免实体被二次转义）
    try:
        url = f"{WP_API_URL}/pages/{LINKS_PAGE_ID}?context=edit"
        req = urllib.request.Request(url)
        auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(req) as resp:
            page_data = json.loads(resp.read().decode())
            current_content = page_data.get("content", {}).get("raw", "")
    except Exception as e:
        print(f"  ⚠️ 获取链接页面失败：{e}")
        return

    # 构建条目：中文标题 + （若已发布英文版）英文链接
    en_link = pub_info.get("en", {}).get("link")
    en_part = ""
    if en_link:
        en_part = (
            f' · <a href="{en_link}" '
            f'style="color:#2563eb;text-decoration:none;">English</a>'
        )
    new_item = (
        f'<li style="margin-bottom:8px;">'
        f'<span style="color:#9ca3af;">[{month}]</span> '
        f'<a href="{link}" style="color:#374151;text-decoration:none;">{title}</a>'
        f'{en_part} '
        f'<span style="color:#9ca3af;font-size:12px;">(AI)</span></li>\n'
    )

    # 若链接已存在，整条 <li> 替换（幂等更新标题与英文链接）；否则在列表头部插入新条目
    # 关键：用「不跨越 </li>」约束，确保从「包含该链接的那个 <li>」开始匹配，
    # 避免从更靠前的 <li> 起跳、把前一条目也吞掉。
    li_pattern = re.compile(
        r"<li[^>]*>(?:(?!</li>).)*?" + re.escape(link) + r".*?</li>", re.DOTALL
    )
    if li_pattern.search(current_content):
        new_content = li_pattern.sub(new_item, current_content, count=1)
        print("  🔄 链接页面已存在该文章，已更新标题与英文链接")
    else:
        first_li = current_content.find("<li")
        if first_li != -1:
            new_content = current_content[:first_li] + new_item + current_content[first_li:]
        else:
            first_ul = current_content.find("<ul")
            if first_ul != -1:
                close_tag = current_content.find(">", first_ul)
                new_content = current_content[:close_tag + 1] + "\n" + new_item + current_content[close_tag + 1:]
            else:
                print("  ️ 无法找到链接列表位置")
                return

    # 更新页面
    try:
        update_url = f"{WP_API_URL}/pages/{LINKS_PAGE_ID}"
        data = json.dumps({"content": new_content}).encode()
        req = urllib.request.Request(update_url, data=data, method="POST")
        auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()
        req.add_header("Authorization", f"Basic {auth}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            print("  ✅ 已更新链接页面")
    except Exception as e:
        print(f"  ️ 更新链接页面失败：{e}")


def _set_lang_meta(pub_info: dict) -> None:
    """为双语文章设置 english_url / chinese_url post meta。

    主题 language-switcher.php 在首页列表（render_block 钩子）与详情页（the_content 钩子）
    渲染语言切换链接时，优先读取这两个 meta；若为空则按 "slug + -en" 约定回退。
    本站中文文章 slug 带 -zh 后缀（如 photo_library-zh），回退会算成 photo_library-zh-en
    （不存在），导致英文链接缺失。显式写入 meta 可彻底规避 slug 约定问题，
    且对 personal_crm 等裸 slug 文章同样兼容（主题先读 meta，回退仅作兜底）。
    """
    if not WP_APP_PASSWORD:
        print("  ⚠️ 未设置 WP_APP_PASSWORD，跳过语言 meta 设置")
        return
    if "zh" not in pub_info or "en" not in pub_info:
        return

    zh = pub_info["zh"]
    en = pub_info["en"]
    zh_link = zh.get("link")
    en_link = en.get("link")
    zh_id = zh.get("wp_id")
    en_id = en.get("wp_id")
    if not (zh_link and en_link and zh_id and en_id):
        print("  ⚠️ 缺少 link / wp_id，跳过语言 meta 设置")
        return

    import base64
    import json
    import urllib.request

    auth = base64.b64encode(f"{WP_USERNAME}:{WP_APP_PASSWORD}".encode()).decode()

    # 中文文章为 post、英文为 page。主题 language-switcher.php 的详情页切换器逻辑：
    #   english_url 有值 → 显示「🇬🇧 English」按钮；chinese_url 有值 → 显示「🇨🇳 中文版」按钮。
    # 因此每侧【只写对方的链接、并把自身链接字段清空】，否则两侧都会同时带两个 meta，
    # 导致中文详情页把「中文版」(指向自己)按钮也渲染出来，无意义且重复。
    # ⚠️ 关键：WP REST 更新自定义 meta 必须把字段放进 "meta" 子对象，
    # 顶层传 {"english_url": ...} 会被 REST 静默忽略（不报错但不写入）。
    # 主题 register_post_meta(..., show_in_rest=true) 已将该 meta 暴露为 REST 的 meta 字段；
    # 写入空串 "" 即清空（get_post_meta 返回空串为假，按钮不渲染）。
    updates = [
        # 中文 post：只设 english_url（对方），chinese_url 置空清除历史误写
        (f"{WP_API_URL}/posts/{zh_id}", {"meta": {"english_url": en_link, "chinese_url": ""}}),
        # 英文 page：只设 chinese_url（对方），english_url 置空清除历史误写
        (f"{WP_API_URL}/pages/{en_id}", {"meta": {"chinese_url": zh_link, "english_url": ""}}),
    ]
    for url, body in updates:
        try:
            data = json.dumps(body).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Authorization", f"Basic {auth}")
            req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req) as resp:
                print(f"  ✅ 已写入语言 meta: {url.split('/')[-1]} -> {list(body['meta'].keys())}")
        except Exception as e:
            print(f"  ⚠️ 写入语言 meta 失败 {url}: {e}")


def _publish_article(wp_dir: Path, filename: str, lang: str, prod: bool) -> dict | None:
    """调用 wordpress-tools 发布单篇文章，成功返回 {link, wp_id}，失败返回 None。"""
    # wordpress-tools determines language from --en flag, not from path
    # English articles are published as pages (--page), not posts
    cmd = ["npm", "run", "write:prod" if prod else "write", "--", filename]
    if prod:
        cmd.append("--prod")
    if lang == "en":
        cmd.append("--en")
        cmd.append("--page")

    label = "生产" if prod else "本地"
    print(f"  📤 发布 {lang} 文章到 {label} 环境: {filename}")
    print(f"     命令: {' '.join(cmd)}")

    result = subprocess.run(
        cmd,
        cwd=wp_dir,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        for line in result.stdout.strip().split("\n"):
            print(f"     {line}")
    if result.returncode != 0:
        print(f"  ❌ 发布失败 (exit {result.returncode})")
        if result.stderr:
            for line in result.stderr.strip().split("\n")[-5:]:
                print(f"     {line}")
        return None

    return _parse_output(result.stdout)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    prod = "--local" not in flags

    pending = _load_pending()
    published = _load_published()
    if not args or (args[0] not in pending and args[0] not in published):
        print("用法: python publish.py <项目名> [--local]")
        avail = ", ".join(sorted(set(list(pending) + list(published))))
        print(f"可用项目: {avail}")
        sys.exit(1)

    project_key = args[0]
    # 待写队列优先；已发存档中的项目名也可重跑（仅更新链接）
    source = "published" if project_key in published else "pending"
    slug = project_key.replace("-", "_")
    slug_zh = f"{slug}-zh"
    slug_en = f"{slug}-en"

    # 检查 wordpress-tools 路径
    if not WP_TOOLS_DIR or not WP_TOOLS_DIR.exists():
        print("❌ 请设置 WP_TOOLS_DIR 环境变量指向 wordpress-tools 目录")
        print("   例: export WP_TOOLS_DIR=/path/to/wordpress-tools")
        sys.exit(1)

    # 检查 ARTICLES_DIR
    if not ARTICLES_DIR or not ARTICLES_DIR.exists():
        print("❌ ARTICLES_DIR 未设置或目录不存在")
        sys.exit(1)

    published_count = 0
    failed = 0
    pub_info = {}  # { "zh": {"link": ..., "wp_id": ...}, "en": {...} }

    for lang in ("zh", "en"):
        lang_slug = slug_zh if lang == "zh" else slug_en
        article_path = ARTICLES_DIR / lang / f"{lang_slug}.md"
        # 兼容旧文件名（无语言后缀）
        if not article_path.exists():
            legacy_path = ARTICLES_DIR / lang / f"{slug}.md"
            if legacy_path.exists():
                article_path = legacy_path
                lang_slug = slug
        if not article_path.exists():
            # 兜底：归档（旧 move 语义）可能已将源搬到 wordpress-tools/articles/，
            # 从该目录回读，确保归档后仍能发布。
            wt = os.getenv("WP_TOOLS_DIR")
            if wt:
                for cand in (
                    Path(wt) / "articles" / lang / f"{lang_slug}.md",
                    Path(wt) / "articles" / lang / f"{slug}.md",
                ):
                    if cand.exists():
                        article_path = cand
                        break
        if not article_path.exists():
            print(f"  ⏭️  {lang} 文章不存在，跳过: {article_path}")
            continue

        result = _publish_article(WP_TOOLS_DIR, f"{lang_slug}.md", lang, prod)
        if result:
            pub_info[lang] = result
            published_count += 1
        else:
            failed += 1

    # 添加交叉语言链接并重新发布
    if len(pub_info) == 2:
        print("\n🔗 添加交叉语言链接...")
        _add_cross_links(ARTICLES_DIR, slug, slug_zh, slug_en, pub_info)
        # 重新发布以更新内容
        for lang in ("zh", "en"):
            lang_slug = slug_zh if lang == "zh" else slug_en
            result = _publish_article(WP_TOOLS_DIR, f"{lang_slug}.md", lang, prod)
            if result:
                pub_info[lang] = result

    # 更新链接页面
    if pub_info:
        print("\n 更新链接页面...")
        _update_links_page(project_key, pub_info)

    # 设置双语语言 meta（english_url / chinese_url），驱动首页列表与详情页的语言切换链接。
    # 主题 language-switcher.php 优先读该 meta；slug 约定回退对 -zh 后缀中文 slug 失效
    # （photo_library-zh -> photo_library-zh-en 不存在），故显式写入最稳，且不依赖 slug 命名约定。
    if "zh" in pub_info and "en" in pub_info:
        print("\n🔗 设置双语语言 meta（english_url / chinese_url）...")
        _set_lang_meta(pub_info)

    # 回写链接和 wp_id；发完后将项目从待写队列移至已发存档
    if pub_info:
        if source == "pending":
            proj = pending[project_key]
        else:
            proj = published[project_key]
        proj.setdefault("published", {})
        for lang, info in pub_info.items():
            entry = proj["published"].setdefault(lang, {})
            if "link" in info:
                entry["link"] = info["link"]
            if "wp_id" in info:
                entry["wp_id"] = info["wp_id"]

        if source == "pending":
            # 从待写队列移除，并入已发存档
            pending.pop(project_key, None)
            published[project_key] = proj
            _save_pending(pending)
            _save_published(published)
            print(f"\n📝 已发布 {project_key}：已从 projects.json 移至 projects-published.json")
        else:
            # 已发项目重新发布，仅更新链接
            published[project_key] = proj
            _save_published(published)
            print("\n📝 已更新 projects-published.json 中的发布链接")

    print(f"\n📊 发布完成: {published_count} 成功, {failed} 失败")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
