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

# WordPress API 配置（用于更新链接页面）
WP_API_URL = os.getenv("WP_API_URL", "https://your-site.com/wp-json/wp/v2")
WP_USERNAME = os.getenv("WP_USERNAME", "your-username")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
LINKS_PAGE_ID = int(os.getenv("LINKS_PAGE_ID", "1"))


def _load_projects() -> dict:
    if not PROJECTS_FILE.exists():
        print(f"❌ 找不到项目配置文件: {PROJECTS_FILE}")
        sys.exit(1)
    with open(PROJECTS_FILE, encoding="utf-8") as f:
        return json.load(f)


def _save_projects(projects: dict) -> None:
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)
        f.write("\n")


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


def _add_cross_links(articles_dir: Path, slug: str, pub_info: dict) -> None:
    """在文章 frontmatter 后插入对方语言的链接。"""
    if "zh" not in pub_info or "en" not in pub_info:
        return  # 只有一篇文章时不需要交叉链接

    zh_link = pub_info["zh"]["link"]
    en_link = pub_info["en"]["link"]

    for lang, other_link, link_text in [
        ("zh", en_link, "> [🇬🇧 English Version]({})"),
        ("en", zh_link, "> [🇨🇳 中文版]({})"),
    ]:
        article_path = articles_dir / lang / f"{slug}.md"
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


def _update_links_page(project_key: str, pub_info: dict) -> None:
    """更新链接页面，添加新发布的文章。"""
    if not WP_APP_PASSWORD:
        print("  ⚠️ 未设置 WP_APP_PASSWORD，跳过链接页面更新")
        return

    if "zh" not in pub_info:
        return  # 只有中文文章才添加到链接页面

    import urllib.request
    import urllib.error
    from datetime import date

    # 获取文章标题（从 projects.json 的 desc 字段）
    projects = _load_projects()
    proj = projects.get(project_key, {})
    title = proj.get("desc", project_key)
    # 截取前 30 个字符作为显示标题
    if len(title) > 30:
        title = title[:30] + "..."

    link = pub_info["zh"]["link"]
    month = date.today().strftime("%Y-%m")

    # 获取当前链接页面内容
    try:
        url = f"{WP_API_URL}/pages/{LINKS_PAGE_ID}"
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"Basic {__import__('base64').b64encode(f'{WP_USERNAME}:{WP_APP_PASSWORD}'.encode()).decode()}")
        with urllib.request.urlopen(req) as resp:
            page_data = json.loads(resp.read().decode())
            current_content = page_data.get("content", {}).get("rendered", "")
    except Exception as e:
        print(f"  ⚠️ 获取链接页面失败：{e}")
        return

    # 检查是否已存在该链接
    if link in current_content:
        print("  ℹ️ 链接页面已包含该文章，跳过更新")
        return

    # 在第一个 <li> 前插入新文章
    new_item = f'<li style="margin-bottom:8px;"><span style="color:#9ca3af;">[{month}]</span> <a href="{link}" style="color:#374151;text-decoration:none;">{title}</a> <span style="color:#9ca3af;font-size:12px;">(AI)</span></li>\n'

    # 找到第一个 <li> 的位置
    first_li = current_content.find("<li")
    if first_li != -1:
        new_content = current_content[:first_li] + new_item + current_content[first_li:]
    else:
        # 如果没有 <li>，在 <ul> 后插入
        first_ul = current_content.find("<ul")
        if first_ul != -1:
            close_tag = current_content.find(">", first_ul)
            new_content = current_content[:close_tag+1] + "\n" + new_item + current_content[close_tag+1:]
        else:
            print("  ️ 无法找到链接列表位置")
            return

    # 更新页面
    try:
        update_url = f"{WP_API_URL}/pages/{LINKS_PAGE_ID}"
        data = json.dumps({"content": new_content}).encode()
        req = urllib.request.Request(update_url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {__import__('base64').b64encode(f'{WP_USERNAME}:{WP_APP_PASSWORD}'.encode()).decode()}")
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as resp:
            print(f"  ✅ 已更新链接页面")
    except Exception as e:
        print(f"  ️ 更新链接页面失败：{e}")


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

    projects = _load_projects()
    if not args or args[0] not in projects:
        print("用法: python publish.py <项目名> [--local]")
        print(f"可用项目: {', '.join(projects.keys())}")
        sys.exit(1)

    project_key = args[0]
    slug = project_key.replace("-", "_")

    # 检查 wordpress-tools 路径
    if not WP_TOOLS_DIR or not WP_TOOLS_DIR.exists():
        print("❌ 请设置 WP_TOOLS_DIR 环境变量指向 wordpress-tools 目录")
        print("   例: export WP_TOOLS_DIR=/path/to/wordpress-tools")
        sys.exit(1)

    # 检查 ARTICLES_DIR
    if not ARTICLES_DIR or not ARTICLES_DIR.exists():
        print("❌ ARTICLES_DIR 未设置或目录不存在")
        sys.exit(1)

    published = 0
    failed = 0
    pub_info = {}  # { "zh": {"link": ..., "wp_id": ...}, "en": {...} }

    for lang in ("zh", "en"):
        article_path = ARTICLES_DIR / lang / f"{slug}.md"
        if not article_path.exists():
            print(f"  ⏭️  {lang} 文章不存在，跳过: {article_path}")
            continue

        result = _publish_article(WP_TOOLS_DIR, f"{slug}.md", lang, prod)
        if result:
            pub_info[lang] = result
            published += 1
        else:
            failed += 1

    # 添加交叉语言链接并重新发布
    if len(pub_info) == 2:
        print("\n🔗 添加交叉语言链接...")
        _add_cross_links(ARTICLES_DIR, slug, pub_info)
        # 重新发布以更新内容
        for lang in ("zh", "en"):
            result = _publish_article(WP_TOOLS_DIR, f"{slug}.md", lang, prod)
            if result:
                pub_info[lang] = result

    # 更新链接页面
    if pub_info:
        print("\n 更新链接页面...")
        _update_links_page(project_key, pub_info)

    # 回写链接和 wp_id 到 projects.json
    if pub_info:
        proj = projects[project_key]
        proj.setdefault("published", {})
        for lang, info in pub_info.items():
            entry = proj["published"].setdefault(lang, {})
            if "link" in info:
                entry["link"] = info["link"]
            if "wp_id" in info:
                entry["wp_id"] = info["wp_id"]
        _save_projects(projects)
        print("\n📝 已更新 projects.json 中的发布链接")

    print(f"\n📊 发布完成: {published} 成功, {failed} 失败")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
