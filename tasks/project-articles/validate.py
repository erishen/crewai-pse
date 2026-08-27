#!/usr/bin/env python3
"""发布前校验：检查 articles/pse/zh 和 en 下待发布文章的正确性。

校验项：
1. 文件存在性（zh + en）
2. frontmatter 完整性（title/date/slug/categories/tags/description/excerpt）
3. 正文有效性（长度、标题数、非计划口吻）
4. 思维链泄漏检测（Thought:/Answer:/内容大纲 等）
5. FAQ 区块存在性 + 中英文数量一致
6. slug 命名规范（zh 不带 -zh，en 带 -en）
7. 代码块闭合（``` 配对）
8. 日期格式 YYYY-MM-DD

用法：python validate.py <project_key>
退出码：0=全部通过，1=有错误，2=参数错误
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ── 路径配置 ──────────────────────────────────────────────
BASE = Path(__file__).resolve().parent
ROOT = BASE.parent.parent  # crewai-pse 根
PROJECTS_FILE = BASE / "projects.json"

# 文章输出目录（与 run.py 保持一致）
ARTICLES_DIR = ROOT.parent.parent / "personal" / "personal-site" / "wordpress-tools" / "articles" / "pse"


# ── 校验结果收集 ──────────────────────────────────────────
class CheckResult:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.info: list[str] = []

    def error(self, msg: str):
        self.errors.append(msg)
        print(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)
        print(msg)

    def ok(self, msg: str):
        self.info.append(msg)
        print(msg)

    @property
    def passed(self) -> bool:
        return len(self.errors) == 0


# ── frontmatter 解析 ─────────────────────────────────────
def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """解析 YAML frontmatter，返回 (fields, body)。不支持复杂嵌套。"""
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not m:
        return {}, text
    fm = {}
    for line in m.group(1).split("\n"):
        if ":" in line:
            key, val = line.split(":", 1)
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm, text[m.end():]


# ── 校验函数 ─────────────────────────────────────────────
def check_file_exists(path: Path, label: str, r: CheckResult):
    if path.exists():
        r.ok(f"  ✅ {label} 存在: {path.name} ({path.stat().st_size} bytes)")
    else:
        r.error(f"  ❌ {label} 不存在: {path}")


def check_frontmatter(fm: dict[str, str], label: str, r: CheckResult):
    required = ["title", "date", "slug", "categories", "tags"]
    recommended = ["description", "excerpt"]
    for key in required:
        if key not in fm or not fm[key]:
            r.error(f"  ❌ {label} frontmatter 缺少必填字段: {key}")
        else:
            r.ok(f"  ✅ {label} {key}: {fm[key][:60]}")
    for key in recommended:
        if key not in fm or not fm[key]:
            r.warn(f"  ⚠️  {label} frontmatter 缺少推荐字段: {key}")
        else:
            r.ok(f"  ✅ {label} {key}: {fm[key][:60]}")


def check_slug(fm: dict[str, str], label: str, expected_suffix: str, r: CheckResult):
    slug = fm.get("slug", "")
    if expected_suffix == "-en":
        if not slug.endswith("-en"):
            r.error(f"  ❌ {label} slug 应以 -en 结尾，当前: {slug}")
        else:
            r.ok(f"  ✅ {label} slug 规范: {slug}")
    else:  # zh
        if slug.endswith("-zh"):
            r.error(f"  ❌ {label} slug 不应以 -zh 结尾，当前: {slug}")
        else:
            r.ok(f"  ✅ {label} slug 规范: {slug}")


def check_date(fm: dict[str, str], label: str, r: CheckResult):
    date = fm.get("date", "")
    if re.match(r"^\d{4}-\d{2}-\d{2}$", date):
        r.ok(f"  ✅ {label} 日期格式正确: {date}")
    else:
        r.error(f"  ❌ {label} 日期格式应为 YYYY-MM-DD，当前: {date}")


def check_body_valid(body: str, label: str, r: CheckResult):
    """正文有效性：长度、标题数、非计划口吻。"""
    if len(body.strip()) < 600:
        r.error(f"  ❌ {label} 正文过短（{len(body.strip())} 字），疑似非文章")
    else:
        r.ok(f"  ✅ {label} 正文长度: {len(body.strip())} 字")

    headings = re.findall(r"^#{1,3}\s", body, re.MULTILINE)
    if len(headings) < 2:
        r.error(f"  ❌ {label} 标题数过少（{len(headings)} 个），疑似非文章")
    else:
        r.ok(f"  ✅ {label} 标题数: {len(headings)} 个")

    head = body.strip()[:150]
    if re.search(r"(step\s*\d|好的，我|我现在需要|读取提纲|合并草稿为|```json)", head, re.IGNORECASE):
        r.error(f"  ❌ {label} 开头疑似计划口吻/工具调用残片")
    else:
        r.ok(f"  ✅ {label} 开头正常")


# 思维链泄漏标记
_REASONING_LEAK_MARKERS = [
    "Thought:", "Thought：", "Answer:", "Answer：",
    "内容大纲", "关键发现", "思考过程", "推理过程",
    "让我先", "我需要先", "接下来我",
]
_REASONING_LEAK_LINE_RE = re.compile(
    r"^\s*(?:Thought|Answer|思考|推理|关键发现|内容大纲)\s*[:：].*$",
    re.MULTILINE | re.IGNORECASE,
)


def check_reasoning_leak(text: str, label: str, r: CheckResult):
    """检测思维链/内部独白泄漏。"""
    if _REASONING_LEAK_LINE_RE.search(text):
        r.error(f"  ❌ {label} 检测到思维链泄漏行（Thought:/Answer: 等）")
        return
    for mk in _REASONING_LEAK_MARKERS:
        if mk in text:
            r.warn(f"  ⚠️  {label} 可能包含思维链标记: {mk}")
            return
    r.ok(f"  ✅ {label} 无思维链泄漏")


def check_code_fences(text: str, label: str, r: CheckResult):
    """代码块 ``` 是否配对。"""
    count = len(re.findall(r"^```", text, re.MULTILINE))
    if count % 2 == 0:
        r.ok(f"  ✅ {label} 代码块闭合正常（{count // 2} 个）")
    else:
        r.error(f"  ❌ {label} 代码块未闭合（{count} 个 ```，奇数）")


def check_faq(text: str, label: str, r: CheckResult) -> int:
    """检查 [faq] 区块，返回数量。"""
    opens = len(re.findall(r"^\[faq\]", text, re.MULTILINE))
    closes = len(re.findall(r"^\[/faq\]", text, re.MULTILINE))
    if opens == 0:
        r.warn(f"  ⚠️  {label} 没有 [faq] 区块")
        return 0
    if opens != closes:
        r.error(f"  ❌ {label} [faq] 区块不配对：开 {opens} / 关 {closes}")
    else:
        r.ok(f"  ✅ {label} FAQ 区块: {opens} 条")
    return opens


def check_faq_consistency(zh_count: int, en_count: int, r: CheckResult):
    if zh_count == en_count:
        r.ok(f"  ✅ 中英文 FAQ 数量一致: {zh_count} 条")
    else:
        r.error(f"  ❌ 中英文 FAQ 数量不一致：中文 {zh_count} / 英文 {en_count}")


# ── 主流程 ───────────────────────────────────────────────
def validate_project(project_key: str) -> CheckResult:
    r = CheckResult()
    slug = project_key.replace("-", "_")
    zh_path = ARTICLES_DIR / "zh" / f"{slug}-zh.md"
    en_path = ARTICLES_DIR / "en" / f"{slug}-en.md"

    print(f"\n{'='*60}")
    print(f"📋 发布前校验: {project_key}")
    print(f"{'='*60}")

    # 1. 文件存在性
    print("\n📁 文件存在性")
    check_file_exists(zh_path, "中文", r)
    check_file_exists(en_path, "英文", r)

    if not zh_path.exists():
        r.error("  ❌ 中文文章不存在，无法继续校验")
        return r

    zh_text = zh_path.read_text(encoding="utf-8")
    zh_fm, zh_body = parse_frontmatter(zh_text)

    # 2. 中文 frontmatter
    print("\n📝 中文 frontmatter")
    check_frontmatter(zh_fm, "中文", r)
    check_slug(zh_fm, "中文", "", r)
    check_date(zh_fm, "中文", r)

    # 3. 中文正文
    print("\n✍️  中文正文")
    check_body_valid(zh_body, "中文", r)
    check_reasoning_leak(zh_text, "中文", r)
    check_code_fences(zh_text, "中文", r)
    zh_faq = check_faq(zh_text, "中文", r)

    # 4. 英文校验（如果存在）
    if en_path.exists():
        en_text = en_path.read_text(encoding="utf-8")
        en_fm, en_body = parse_frontmatter(en_text)

        print("\n📝 英文 frontmatter")
        check_frontmatter(en_fm, "英文", r)
        check_slug(en_fm, "英文", "-en", r)
        check_date(en_fm, "英文", r)

        print("\n✍️  英文正文")
        check_body_valid(en_body, "英文", r)
        check_reasoning_leak(en_text, "英文", r)
        check_code_fences(en_text, "英文", r)
        en_faq = check_faq(en_text, "英文", r)

        print("\n🔗 中英文一致性")
        check_faq_consistency(zh_faq, en_faq, r)
    else:
        r.warn("  ⚠️  英文文章不存在，跳过英文校验和一致性检查")

    # 5. 汇总
    print(f"\n{'='*60}")
    if r.passed:
        print(f"✅ 全部通过！({len(r.info)} 项检查通过，{len(r.warnings)} 个警告)")
    else:
        print(f"❌ 校验失败：{len(r.errors)} 个错误，{len(r.warnings)} 个警告")
        for e in r.errors:
            print(f"  {e}")
    if r.warnings:
        print("\n⚠️  警告：")
        for w in r.warnings:
            print(f"  {w}")
    print(f"{'='*60}\n")

    return r


def main():
    if len(sys.argv) < 2:
        print("用法: python validate.py <project_key>")
        print("可用项目:")
        if PROJECTS_FILE.exists():
            projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            for k in sorted(projects.keys()):
                print(f"  - {k}")
        sys.exit(2)

    project_key = sys.argv[1]

    # 验证项目是否在 projects.json 中
    if PROJECTS_FILE.exists():
        projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        if project_key not in projects:
            print(f"❌ 未知项目: {project_key}")
            print(f"可用项目: {', '.join(sorted(projects.keys()))}")
            sys.exit(2)

    result = validate_project(project_key)
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
