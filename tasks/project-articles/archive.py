"""将 ARTICLES_DIR 中的文章归档到 wordpress-tools/articles/{zh,en}/，并重建各平台副本与关键词索引。

用法:
    python archive.py <项目名>

独立于发布流程，可在发布后按需手动执行。归档会把 pse/ 下的源文章搬入
wordpress-tools/articles/，随后本地重建 juejin/segmentfault/wechat 副本并刷新
关键词索引（仅本地构建，不调平台 API、不需要凭据）。
"""

import json
import os
import shutil
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


def _load_projects() -> dict:
    pending = {}
    if PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, encoding="utf-8") as f:
            pending = json.load(f)
    published = {}
    if PUBLISHED_FILE.exists():
        with open(PUBLISHED_FILE, encoding="utf-8") as f:
            published = json.load(f)
    return {**published, **pending}


def rebuild_platform_copies(wp_tools_dir: Path, slug: str) -> None:
    """归档后重建 juejin/segmentfault/wechat 副本并刷新关键词索引。

    仅跑本地构建（不调平台 API、不需要凭据）：
      - gen_keywords_index.py        刷新关键词索引
      - build-juejin-from-zh.mjs      重建掘金副本
      - build-segmentfault.mjs        重建思否副本
      - wechat-convert.js             本地转微信 HTML 副本（不建草稿/不发布）
    任一步失败仅告警，不中断整体归档。
    """
    local_steps = [
        (["python3", "tools/gen_keywords_index.py"], "关键词索引"),
        (["node", "src/publish/build-juejin-from-zh.mjs"], "掘金副本"),
        (["node", "src/publish/build-segmentfault.mjs"], "思否副本"),
    ]
    for cmd, label in local_steps:
        try:
            r = subprocess.run(cmd, cwd=wp_tools_dir, capture_output=True, text=True, timeout=300)
            if r.returncode == 0:
                print(f"  ✅ {label} 重建完成")
            else:
                print(f"  ⚠️ {label} 重建失败(exit {r.returncode}):\n{r.stderr[-2000:]}")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ {label} 重建异常: {e}")

    # 微信 HTML 副本（本地转换，不调 API/不建草稿）。
    # 微信公众号仅中文，故只转 zh 源；en 源不建微信副本。
    src = wp_tools_dir / "articles" / "zh" / f"{slug}-zh.md"
    if src.exists():
        try:
            r = subprocess.run(
                ["node", "src/wechat/wechat-convert.js", f"articles/zh/{slug}-zh.md"],
                cwd=wp_tools_dir, capture_output=True, text=True, timeout=300,
            )
            if r.returncode == 0:
                print("  ✅ 微信副本重建完成（仅 zh）")
            else:
                print(f"  ⚠️ 微信副本重建失败:\n{r.stderr[-1500:]}")
        except Exception as e:  # noqa: BLE001
            print(f"  ⚠️ 微信副本重建异常: {e}")
    else:
        print("  ⏭️ 微信副本跳过（无 zh 源）")


def main():
    projects = _load_projects()
    if len(sys.argv) < 2 or sys.argv[1] not in projects:
        print("用法: python archive.py <项目名>")
        print(f"可用项目: {', '.join(projects.keys())}")
        sys.exit(1)

    project_key = sys.argv[1]
    slug = project_key.replace("-", "_")
    slug_zh = f"{slug}-zh"
    slug_en = f"{slug}-en"

    if not WP_TOOLS_DIR or not WP_TOOLS_DIR.exists():
        print("❌ 请设置 WP_TOOLS_DIR 环境变量指向 wordpress-tools 目录")
        sys.exit(1)

    if not ARTICLES_DIR:
        print("❌ ARTICLES_DIR 未设置")
        sys.exit(1)
    # 保证 pse/ 目录结构（含 zh/en 子目录）始终存在，归档只移走具体文章，不删目录
    for lang in ("zh", "en"):
        (ARTICLES_DIR / lang).mkdir(parents=True, exist_ok=True)

    archived = 0
    for lang in ("zh", "en"):
        lang_slug = slug_zh if lang == "zh" else slug_en
        src = ARTICLES_DIR / lang / f"{lang_slug}.md"
        # 兼容旧文件名（无语言后缀）
        if not src.exists():
            legacy = ARTICLES_DIR / lang / f"{slug}.md"
            if legacy.exists():
                src = legacy
                lang_slug = slug
        if not src.exists():
            print(f"  ⏭️  {lang} 文章不存在，跳过")
            continue
        dest_dir = WP_TOOLS_DIR / "articles" / lang
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{lang_slug}.md"
        # 归档即搬走工作副本：源码移动到 wordpress-tools/articles/{zh,en} 供
        # juejin/segmentfault/wechat 构建读取。translate/publish 在 pse/ 缺源时
        # 会从 wordpress-tools/articles/ 回读（见 run.py / publish.py 兜底逻辑），
        # 因此归档后无需保留 pse/ 副本。
        shutil.move(src, dest)
        print(f"  📁 {lang} 已归档（移走）→ {dest}")
        archived += 1

    if archived > 0 and WP_TOOLS_DIR and WP_TOOLS_DIR.exists():
        print("\n🔄 重建各平台副本 + 关键词索引...")
        rebuild_platform_copies(WP_TOOLS_DIR, slug)
    elif archived == 0:
        print("\n⚠️ 没有文件被归档，跳过副本重建")

    print(f"\n📊 归档完成: {archived} 篇（pse/ 目录结构保留，文件已移走）")


if __name__ == "__main__":
    main()
