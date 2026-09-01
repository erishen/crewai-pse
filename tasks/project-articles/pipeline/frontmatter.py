"""frontmatter 解析与字段读写。

所有函数都是纯文本变换（str → str 或 str → tuple），不碰磁盘、不调 LLM，
因此可被 validate.py 等只读工具安全复用。
"""

import re

from pipeline.config import STANDARD_TAGS_EN, STANDARD_TAGS_ZH, TAXONOMY_EN


def extract_frontmatter(text: str):
    """返回 (frontmatter 含分隔行, 余下正文)。无 frontmatter 则 ('', text)。"""
    m = re.match(r"^---\n.*?\n---\n?", text, re.DOTALL)
    if m:
        return m.group(0), text[m.end():]
    return "", text


def strip_outer_fence(text: str) -> str:
    """去掉模型偶尔加在最外层的 ```markdown ... ``` 包裹（含残缺情形）。

    WordPress 发布时会把整个内容当代码块渲染，必须剥掉。

    覆盖两种情形：
    - 成对包裹：整体以 ``` 开头且以 ``` 结尾 → 首尾围栏都剥。
    - 残缺包裹：只有开头 ```markdown、结尾忘记闭合 → 只剥首行围栏即可
      （正文中其余 ``` 代码块保持不动）。

    正常文章以 frontmatter 的 --- 开头，首行不是围栏，不受影响。
    """
    t = text.strip()
    lines = t.split("\n")
    # 剥首行围栏（成对 / 只有开头 两种情形都覆盖）
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    # 剥结尾闭合围栏（仅当存在，处理成对情形）
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def clean_code_block_whitespace(text: str) -> str:
    """清理代码块内仅包含空格的行（保留真正的空行）。"""
    lines = text.split("\n")
    in_code_block = False
    result = []
    for line in lines:
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
        elif in_code_block and line.strip() == "" and len(line) > 0:
            # 代码块内只包含空格的行 → 改为真正空行
            result.append("")
        else:
            result.append(line)
    return "\n".join(result)


def yaml_str(s: str) -> str:
    """转义后包双引号的 YAML 标量（description 可能含冒号/引号）。"""
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def fix_frontmatter_slug(text: str, suffix: str = "") -> str:
    """归一化 frontmatter 的 slug：去掉可能的 -zh/-en 后缀，再追加指定后缀。

    中文版 suffix=""（无后缀），英文版 suffix="-en"。与既有文章命名规则统一：
    中文版 slug 不加语言后缀，英文版加 -en。
    """
    def repl(m):
        base = re.sub(r"-(zh|en)$", "", m.group(1).strip().strip('"').strip("'"))
        return f"slug: {base}{suffix}"
    return re.sub(r'^slug:\s*["\']?(.+?)["\']?\s*$', repl, text, count=1, flags=re.MULTILINE)


def set_frontmatter_tags(text: str, tags: list[str]) -> str:
    """强制覆写 frontmatter 的 tags 为给定标准标签集（统一全站标签）。"""
    tags_yaml = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
    if re.search(r'^tags:\s*.+$', text, flags=re.MULTILINE):
        return re.sub(r'^tags:\s*.+$', f"tags: {tags_yaml}", text, count=1, flags=re.MULTILINE)
    # 没有 tags 行则在首个 --- 后插入
    return re.sub(r'^(---\n)', lambda m: f"{m.group(1)}tags: {tags_yaml}\n", text, count=1)


def set_frontmatter_categories(text: str, cats: list[str]) -> str:
    """强制覆写 frontmatter 的 categories 为给定分类集（英文稿用英文分类名）。"""
    cats_yaml = "[" + ", ".join(f'"{c}"' for c in cats) + "]"
    if re.search(r'^categories:\s*.+$', text, flags=re.MULTILINE):
        return re.sub(r'^categories:\s*.+$', f"categories: {cats_yaml}", text, count=1, flags=re.MULTILINE)
    # 没有 categories 行则在首个 --- 后插入
    return re.sub(r'^(---\n)', lambda m: f"{m.group(1)}categories: {cats_yaml}\n", text, count=1)


def project_categories(p: dict) -> list:
    """项目分类解析：projects.json 声明 categories 则用之，否则回退 ['AI']（保持旧行为）。"""
    cats = p.get("categories") if isinstance(p, dict) else None
    return cats if isinstance(cats, list) and cats else ["AI"]


def project_tags(p: dict, lang: str = "zh") -> list:
    """项目标签解析：projects.json 声明 tags 则用之，否则回退 STANDARD_TAGS_*。"""
    tags = p.get("tags") if isinstance(p, dict) else None
    if isinstance(tags, list) and tags:
        return tags
    return STANDARD_TAGS_EN if lang == "en" else STANDARD_TAGS_ZH


def en_taxonomy(names) -> list:
    """把中文分类/标签名翻译成英文；不在映射表里的原样保留。"""
    return [TAXONOMY_EN.get(n, n) for n in names]


def inject_frontmatter_field(text: str, field: str, value: str) -> str:
    """向 frontmatter 注入任意字段（已存在同名字段则不覆盖）。"""
    if not value:
        return text
    m = re.match(r"^(---\n.*?\n)---\n", text, re.DOTALL)
    if not m:
        return text
    if re.search(rf"^{field}:", m.group(1), re.MULTILINE):
        return text
    new_fm = m.group(1) + f"\n{field}: {yaml_str(value)}\n---\n"
    return new_fm + text[m.end():]


def inject_frontmatter_description(text: str, desc: str) -> str:
    """向 frontmatter 注入 description 字段（已存在则不覆盖）。"""
    return inject_frontmatter_field(text, "description", desc)


def inject_frontmatter_excerpt(text: str, excerpt: str) -> str:
    """向 frontmatter 注入 excerpt 字段（已存在则不覆盖）。"""
    return inject_frontmatter_field(text, "excerpt", excerpt)
