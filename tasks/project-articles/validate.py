#!/usr/bin/env python3
"""发布前校验：检查 articles/pse/zh 和 en 下待发布文章的正确性。

校验项：
1. 文件存在性（zh + en）
2. frontmatter 完整性（title/date/slug/categories/tags/description/excerpt）
3. 标题污染硬校验（Action: read_file / I need to… 等工具调用与思维链残片）
4. 正文有效性（长度、标题数、非计划口吻）
5. 思维链泄漏检测（Thought:/Answer:/内容大纲 等）
6. FAQ 区块存在性 + 中英文数量一致
7. slug 命名规范（zh 不带 -zh，en 带 -en）
8. 代码块闭合（``` 配对）
9. 日期格式 YYYY-MM-DD
10. 源码真实性对拍（source-truth，软警告）：抽取文章里反引号/代码块中的函数名、
    命令、文件路径、常量，去 projects.json 声明的 source_dir 真实源码树 grep，
    找不到的发警告（不阻断发布）——专治「文档名≠代码名 / 编造符号」类失真。

用法：python validate.py <project_key> [--no-source-truth]
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
PUBLISHED_FILE = BASE / "projects-published.json"

# 文章输出目录（与 run.py 保持一致）
ARTICLES_DIR = ROOT.parent.parent / "personal" / "personal-site" / "wordpress-tools" / "articles" / "pse"


# 源码对拍根目录：projects.json 里的 source_dir 相对它解析。
# 与 ARTICLES_DIR 同基：ROOT(crewai-pse) 的祖父目录即 individular-invest。
SOURCE_ROOT = ROOT.parent.parent  # individular-invest

# ── 源码真实性对拍（source-truth）────────────────────────
# 仅做「软警告」，不阻断发布：文章里反引号包裹/代码块中的符号、命令、路径、
# 常量若在整个源码树中 grep 不到，极可能是「文档名≠代码名」或编造，提示人工核对。
# 触发场景（真实事故）：loadNativeModule(真实为 loadNative)、make prepare(真实为
# npm run native:build)、electron-rebuild(真实用 node-gyp)、ERR_DLOPEN_FAILED(代码无此串)。
_EXCLUDED_DIRS = {
    ".git", "node_modules", "build", "dist", ".next", ".nuxt", "target",
    ".venv", "venv", ".terraform", ".cache", ".idea", ".vscode",
    "__pycache__", ".src_cache", "out", "coverage",
}
_MAX_FILE_BYTES = 1_000_000     # 单文件超过 1MB 跳过（二进制/产物）
# 源码目录优先入索引（排在构建产物/文档前），确保真实符号先被索引到。
_SRC_PRIORITY_DIRS = (
    "native", "src", "lib", "packages", "apps", "internal", "cmd",
    "core", "source", "scripts", "tools", "include",
)

_CALL_RE = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(")
_PATH_RE = re.compile(r"^[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,6}$")
_CONST_RE = re.compile(r"^[A-Z][A-Z0-9_/\-]{4,}$")
_CMD_RE = re.compile(
    r"^(?:make|npm|pnpm|yarn|npx|cargo|cmake|node-gyp|electron-rebuild|"
    r"bazel|gradle|mvn|git|deno|bun)\b", re.IGNORECASE)
_IDENT_RE = re.compile(r"^[A-Za-z_]\w{4,}$")
_IGN_IDENT = {
    "react", "electron", "typescript", "javascript", "python", "python3",
    "nodejs", "node", "three", "threejs", "webgl", "canvas", "blender",
    "docker", "kubernetes", "vscode", "github", "gitlab", "webpack", "vite",
    "linux", "macos", "darwin", "windows", "ubuntu", "debian", "chrome",
    "safari", "sqlite", "postgres", "postgresql", "redis", "npm", "yarn",
    "pnpm", "cmake", "cargo", "gradle", "nextjs", "nuxt", "svelte",
}


# 文档扩展名单独归一类：docs/README 会撒谎（含过时的文档名），不视为代码真相，
# 但用于区分「仅在文档出现（疑似文档名≠代码名）」与「完全搜不到」。
_DOC_EXT = {".md", ".rst", ".txt", ".markdown"}
# 仅这些扩展名算「代码真相」；其余（图片/二进制/锁文件等）跳过。
_CODE_EXT = {
    ".cc", ".cpp", ".c", ".h", ".hpp", ".hxx", ".js", ".mjs", ".cjs",
    ".ts", ".tsx", ".jsx", ".py", ".go", ".rs", ".java", ".kt", ".swift",
    ".m", ".mm", ".sh", ".json", ".gradle", ".gyp", ".toml", ".yml", ".yaml",
}
# TODO 编号（如 P0-001）：以文档 TODO.md 为权威，仅出现在文档里不算失真。
_TODO_ID_RE = re.compile(r"^P\d-\d{3}$")


def _index_source(source_dir: Path) -> tuple[set[str], str, str]:
    """索引源码树：返回 (源码相对路径集合, 源码文本拼接, 文档文本拼接)。

    - 只有「代码文件」（源码扩展名、且不在 docs/构建产物目录）算权威真相；
      docs/README 单独存一份，用于区分「仅在文档出现（疑似文档名≠代码名）」。
    - 源码目录优先入索引，确保 native/src、src 等核心代码先被索引。
    """
    src_relpaths: set[str] = set()
    src_texts: list[str] = []
    doc_texts: list[str] = []
    if not source_dir.exists():
        return src_relpaths, "", ""
    files: list[tuple[int, str, Path]] = []  # (priority, rel, path)
    for p in source_dir.rglob("*"):
        if not p.is_file():
            continue
        if set(p.parts) & _EXCLUDED_DIRS:
            continue
        try:
            rel = str(p.relative_to(source_dir))
        except ValueError:
            continue
        ext = p.suffix.lower()
        # 配置样例（.env.example 等）里的常量名也是代码真相，按代码索引
        is_cfg_sample = p.name == ".env.example" or p.name.endswith(".env.example")
        if ext in _DOC_EXT:
            try:
                doc_texts.append(p.read_text(encoding="utf-8", errors="strict"))
            except (UnicodeDecodeError, OSError):
                pass
            continue
        if ext not in _CODE_EXT and not is_cfg_sample:
            continue
        src_relpaths.add(rel)
        priority = 0 if any(d in p.parts for d in _SRC_PRIORITY_DIRS) else 1
        if p.stat().st_size > _MAX_FILE_BYTES:
            continue
        files.append((priority, rel, p))

    files.sort(key=lambda x: (x[0], x[1]))
    for _, rel, p in files:
        try:
            src_texts.append(p.read_text(encoding="utf-8", errors="strict"))
        except (UnicodeDecodeError, OSError):
            continue
    return src_relpaths, "\n".join(src_texts), "\n".join(doc_texts)


def _classify(tok: str, from_inline: bool) -> str | None:
    if _CALL_RE.match(tok):
        return "call"
    if _CMD_RE.match(tok):
        return "cmd"
    if _CONST_RE.match(tok):
        return "const"
    if _PATH_RE.match(tok) or tok.endswith((".node", ".so", ".dll")):
        return "path"
    if from_inline and _IDENT_RE.match(tok) and tok.lower() not in _IGN_IDENT:
        return "ident"
    return None


def _verify_cmd(tok: str, src_relpaths: set[str], src_blob: str, doc_blob: str) -> str | None:
    low = tok.lower()
    if low.startswith("make "):
        target = tok.split(None, 1)[1].strip() if len(tok.split(None, 1)) > 1 else ""
        if not target:
            return None
        if any(rp.endswith("Makefile") or rp.endswith("makefile") for rp in src_relpaths):
            if not re.search(r"^" + re.escape(target) + r"\s*:", src_blob, re.MULTILINE):
                return f"make 目标 `{target}` 在源码 Makefile 中未找到"
            return None
        if target in doc_blob:
            return f"make 目标 `{target}` 仅在文档/README 出现，源码 Makefile 中未找到（疑似文档名≠代码名）"
        return None
    if low.startswith(("npm run ", "pnpm ", "yarn ", "npx ")):
        script = tok.split()[-1].strip() if tok.split() else ""
        if not script:
            return None
        if any(rp.endswith("package.json") for rp in src_relpaths):
            if not re.search(r'["\']?' + re.escape(script) + r'["\']?\s*:', src_blob):
                return f"npm/pnpm 脚本 `{script}` 在源码 package.json 中未找到"
            return None
        if script in doc_blob:
            return f"脚本 `{script}` 仅在文档/README 出现，源码 package.json 中未找到（疑似文档名≠代码名）"
        return None
    for tool in ("node-gyp", "electron-rebuild", "cmake", "bazel", "gradle", "mvn", "cargo"):
        if low == tool or low.startswith(tool + " "):
            if tool in src_blob:
                return None
            if tool in doc_blob:
                return f"构建工具 `{tool}` 仅在文档/README 出现，源码中未找到（疑似文档名≠代码名）"
            return f"构建工具 `{tool}` 在源码中未找到（可能构建链描述有误）"
    return None


def _verify_path(tok: str, src_relpaths: set[str], src_blob: str, doc_blob: str, source_dir: Path) -> str | None:
    if (source_dir / tok).exists():
        return None
    if tok in src_relpaths or tok in src_blob:
        return None
    # 仅写文件名的引用（如 `imageUtils.ts` 实际在 src/lib/ 下）按 basename 反查
    if any(Path(rp).name == tok for rp in src_relpaths):
        return None
    # 扩展名不是真实源码/文档扩展名 → 基本是对象属性访问（file.width / chunk.date），非文件，跳过
    ext = tok.rsplit(".", 1)[-1].lower() if "." in tok else ""
    if ext and ext not in _CODE_EXT and ext not in _DOC_EXT:
        return None
    if tok in doc_blob:
        return f"文件路径/构件 `{tok}` 仅在文档/README 提及，源码目录中未找到（疑似文档名≠代码名）"
    return f"文件路径/构件 `{tok}` 在源码目录中未找到"


def _verify(tok: str, cls: str, src_relpaths: set[str], src_blob: str,
            doc_blob: str, source_dir: Path) -> str | None:
    if cls == "call":
        m = re.match(r"^([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)", tok)
        needle = m.group(1) if m else tok.rstrip("()")
        if needle and needle in src_blob:
            return None
        # 调用写成 `agent._invoke(...)` 时，裸方法名 `_invoke` 才是真符号
        if "." in needle:
            bare = needle.split(".")[-1]
            if bare and bare in src_blob:
                return None
        if needle and needle in doc_blob:
            return f"函数/标识符 `{needle}` 仅在文档/README 出现，源码中未找到（疑似文档名≠代码名）"
        return f"函数/标识符 `{needle}` 在源码中未找到（疑似文档名≠代码名或编造）"
    if cls == "cmd":
        return _verify_cmd(tok, src_relpaths, src_blob, doc_blob)
    if cls == "const":
        if tok in src_blob:
            return None
        if _TODO_ID_RE.match(tok) and tok in doc_blob:
            return None  # TODO 编号以文档 TODO.md 为权威
        if tok in doc_blob:
            return f"常量/编号 `{tok}` 仅在文档/README 出现，源码中未找到（疑似文档名≠代码名）"
        return f"常量/编号 `{tok}` 在源码中未找到（可能是编造或文档名≠代码名）"
    if cls == "path":
        return _verify_path(tok, src_relpaths, src_blob, doc_blob, source_dir)
    if cls == "ident":
        if tok in src_blob:
            return None
        if tok in doc_blob:
            return f"标识符 `{tok}` 仅在文档/README 出现，源码中未找到（疑似文档名≠代码名）"
        return f"标识符 `{tok}` 在源码中未找到（建议核对真实符号名）"
    return None


def check_source_truth(text: str, source_dir: Path, src_relpaths: set[str],
                       src_blob: str, doc_blob: str, label: str, r: "CheckResult"):
    """抽取文章中的代码符号并对照真实源码树，仅发软警告。"""
    cands: list[tuple[str, bool]] = []
    for tok in re.findall(r"`([^`\n]+)`", text):  # 行内反引号
        tok = tok.strip()
        if tok:
            cands.append((tok, True))
    for m in re.finditer(r"```[a-zA-Z]*\n(.*?)```", text, re.DOTALL):  # 代码块
        block = m.group(1)
        for line in block.splitlines():
            for mt in re.finditer(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\s*\(", line):
                cands.append((mt.group(0).strip(), False))
            for mt in re.finditer(r"[A-Za-z0-9_./\-]+\.[A-Za-z0-9]{1,6}", line):
                cands.append((mt.group(0), False))
            for mt in re.finditer(r"[A-Z][A-Z0-9_/\-]{4,}", line):
                cands.append((mt.group(0), False))

    seen: set[tuple[str, str]] = set()
    n_warn = 0
    for tok, from_inline in cands:
        cls = _classify(tok, from_inline)
        if cls is None:
            continue
        msg = _verify(tok, cls, src_relpaths, src_blob, doc_blob, source_dir)
        if msg is None:
            continue
        key = (label, msg)
        if key in seen:
            continue
        seen.add(key)
        n_warn += 1
        if n_warn > 40:
            r.warn("  ⚠️  [source-truth] 警告过多，已截断（建议人工通读源码核对）")
            return
        r.warn(f"  ⚠️  [source-truth] {label} {msg}")


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


# ── 标题污染检测（与 run.py 的 _FM_VALUE_LEAK_RE / _sanitize_frontmatter 对齐）──
# 真实样本：`title: Action: read_file`、`title: I need to read the source code
# first to ground my outline in`。这类串写在 front matter 里不会触发任何正文级
# 思维链检测，validate 曾全绿放行，发布后直接变成文章标题，故单独设硬校验。
_TITLE_LEAK_RE = re.compile(
    r"^\s*(?:"
    r"Action\s*(?:Input)?\s*:\s*\S+"
    r"|Thought\s*:\s*\S+"
    r"|Observation\s*:\s*\S+"
    r"|Final\s+Answer\s*:\s*\S+"
    r"|(?:read|write|edit|delete|list|glob|grep|search|run)_(?:file|files|dir|dirs|code)\s*(?:\(.*)?"
    r")\s*$",
    re.IGNORECASE,
)
_TITLE_MARKERS = (
    "Thought:", "最终 Answer:", "内容大纲", "关键发现", "让我开始撰写",
    "Action Input:", "Observation:", "Final Answer:",
    "I need to", "let me read", "I will read", "I'll read", "to ground my",
)
# 第一人称 + 意图动词：思考过程泄漏（"I will analyze the repo"）
_TITLE_FIRST_PERSON_RE = re.compile(
    r"\b(?:I|we|my|let's)\b[^.!?\n]*?\b(?:need|needed|will|'ll|should|must|am|'m|gonna|have to)\b",
    re.IGNORECASE,
)


def check_title_sanity(fm: dict[str, str], label: str, r: CheckResult):
    """标题污染硬校验：宁可报错，也不能把工具调用串发布成文章标题。"""
    title = (fm.get("title") or "").strip()
    if not title:
        return  # 空值由 check_frontmatter 的必填校验负责
    if _TITLE_LEAK_RE.match(title):
        r.error(f"  ❌ {label} title 疑似工具调用/思维链残片: {title!r}")
        return
    for mk in _TITLE_MARKERS:
        if mk in title:
            r.error(f"  ❌ {label} title 含思维链标记 {mk!r}: {title!r}")
            return
    if _TITLE_FIRST_PERSON_RE.search(title):
        r.error(f"  ❌ {label} title 疑似第一人称思考过程: {title!r}")
        return
    if len(title) < 6:
        r.error(f"  ❌ {label} title 过短（{len(title)} 字符），疑似残片: {title!r}")
        return
    r.ok(f"  ✅ {label} title 无污染: {title[:50]}")


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
def validate_project(project_key: str, no_source_truth: bool = False) -> CheckResult:
    r = CheckResult()
    slug = project_key.replace("-", "_")
    zh_path = ARTICLES_DIR / "zh" / f"{slug}-zh.md"
    en_path = ARTICLES_DIR / "en" / f"{slug}-en.md"

    print(f"\n{'='*60}")
    print(f"📋 发布前校验: {project_key}")
    print(f"{'='*60}")

    # 0. 解析源码目录（供 source-truth 对拍）
    source_dir = None
    if not no_source_truth and PROJECTS_FILE.exists():
        try:
            pj = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            sd = (pj.get(project_key) or {}).get("source_dir")
            if sd:
                cand = SOURCE_ROOT / sd
                if cand.exists():
                    source_dir = cand
                else:
                    r.warn(f"  ⚠️  [source-truth] projects.json 的 source_dir 不存在: {cand}")
        except Exception as e:  # noqa: BLE001
            r.warn(f"  ⚠️  [source-truth] 读取 projects.json 失败: {e}")

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
    check_title_sanity(zh_fm, "中文", r)
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
        check_title_sanity(en_fm, "英文", r)
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

    # 5. 源码真实性对拍（软警告，不阻断发布）
    if source_dir is not None and not no_source_truth:
        print("\n🔎 源码真实性对拍 (source-truth)")
        src_relpaths, src_blob, doc_blob = _index_source(source_dir)
        if src_blob or doc_blob:
            check_source_truth(zh_text, source_dir, src_relpaths, src_blob, doc_blob, "中文", r)
            if en_path.exists():
                check_source_truth(en_text, source_dir, src_relpaths, src_blob, doc_blob, "英文", r)
        else:
            r.warn("  ⚠️  [source-truth] 源码目录为空或不可索引，跳过")

    # 6. 汇总
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
    args = sys.argv[1:]
    no_source_truth = "--no-source-truth" in args
    args = [a for a in args if a != "--no-source-truth"]
    if len(args) < 1:
        print("用法: python validate.py <project_key> [--no-source-truth]")
        print("  --no-source-truth  跳过源码真实性对拍（软警告检查）")
        print("可用项目:")
        if PROJECTS_FILE.exists():
            projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
            for k in sorted(projects.keys()):
                print(f"  - {k}")
        sys.exit(2)

    project_key = args[0]

    # 验证项目是否在 projects.json（待写队列）或 projects-published.json（已发存档）中。
    # 已发布项目可重新校验/重新发布（publish.py 同样认两个文件）。
    known: set[str] = set()
    for path in (PROJECTS_FILE, PUBLISHED_FILE):
        if path.exists():
            known |= set(json.loads(path.read_text(encoding="utf-8")).keys())
    if project_key not in known:
        print(f"❌ 未知项目: {project_key}")
        print(f"可用项目: {', '.join(sorted(known))}")
        sys.exit(2)

    result = validate_project(project_key, no_source_truth=no_source_truth)
    sys.exit(0 if result.passed else 1)


if __name__ == "__main__":
    main()
