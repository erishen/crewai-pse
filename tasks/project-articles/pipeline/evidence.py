"""论断—源码一致性：程序化判定文章里的因果/排他论断是否有源码依据。

背景（2026-11-06）：`verify_article` 只判断反引号里的符号/路径「是否存在」，
`project_relevance` 只判断文章「是否在讲这个项目」。二者都拦不住把 README 的
描述性事实包装成虚构因果：例如把「镜像里没有 nginx」升级成「SSE 长连接导致
不能用 nginx」，而项目里 `nginx.conf` 本来就配了 1800s。每个名词都真实，
整段论证却站不住 —— 那篇 resolve-lume 稿因此拿到 37 分全绿放行。

本模块补「论断级」证据链，全部确定性、不调用 LLM：

1. `build_anchor_block()` 由程序从真实源码抽出带行号的锚点与 Makefile target，
   拼成清单喂给 Writer —— 行号不靠模型自己编。
2. 成品必须同时满足：≥2 个可核查 `path:line` 锚点、≥1 个逐字匹配真实源码的
   代码块、≥1 个可复现命令；排他/因果论断必须就近带锚点。
3. 不达标时 `grounding_issue()` 返回可操作的隔离原因，绝不落盘/翻译/发布。

与 `verify.py` 的分工：verify 查「写出来的东西是否存在」，本模块查
「说出来的因果有没有源码依据」。文档型文件（*.md）不进源码索引，
避免「复述 README」被误当成源码证据。

论断检测只面向中文定稿：英文稿是 Phase 5 的翻译产物，中文过闸即可，
不需要（也不应该）拿中文措辞去检测英文正文。
"""

from __future__ import annotations

import difflib
import re
from functools import lru_cache
from pathlib import Path

from pipeline.config import (
    MAX_UNGROUNDED_CLAIMS,
    MIN_LINE_ANCHORS,
    MIN_REAL_SOURCE_BLOCKS,
    MIN_REPRO_COMMANDS,
)

# 只有实现文件算源码证据；README / ARCHITECTURE 等 .md 一律排除，
# 否则抄文档也能凑出「真实源码片段」，闸门形同虚设。
_SOURCE_EXTS = (
    ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".c", ".h",
    ".lume", ".json", ".yml", ".yaml", ".sh", ".toml", ".go", ".rs",
    ".java", ".kt", ".cpp", ".cc",
)
_NAMED_SOURCE_FILE_PATTERNS = ("Makefile*", "Dockerfile*")
_SKIP_PARTS = frozenset({
    "node_modules", "__pycache__", "dist", "coverage", "test-results",
    "target", "build", ".gradle", "venv", ".venv",
})

_EXT_ALT = "py|ts|tsx|js|jsx|mjs|cjs|c|h|lume|json|yml|yaml|sh|toml|go|rs|java|kt|cpp|cc"
# `frontend/src/api.ts:44` —— 冒号让 _extract_refs 的反引号正则匹配不到，
# 因此不会被 verify_article 当成「虚构代码引用」误删。
# Makefile / Dockerfile.backend 这类无扩展名的实现文件也必须能做锚点，
# 否则程序生成的清单里会出现闸门自己不认的条目。
_ANCHOR_PATH = (
    r"(?:[A-Za-z0-9_.@-]+(?:/[A-Za-z0-9_.@-]+)*\.(?:" + _EXT_ALT + r")"
    r"|(?:Makefile|Dockerfile)[A-Za-z0-9_.-]*)"
)
_ANCHOR_RE = re.compile(r"(?<!\w)(" + _ANCHOR_PATH + r"):(\d+)(?!\d)")
_CODE_BLOCK_RE = re.compile(r"```[^\n]*\n([\s\S]*?)```")
_CMD_WORDS = r"make|docker|curl|python3?|npm|pnpm|go|git|cargo|uv"
_INLINE_CMD_RE = re.compile(r"`((?:" + _CMD_WORDS + r")\s+[^`\n]+)`")
_LINE_CMD_RE = re.compile(r"(?:^|\n)\s*((?:" + _CMD_WORDS + r")\s+[^\n`]+)")
_MAKE_RE = re.compile(r"^\s*make\s+([A-Za-z0-9_.-]+)")
_TARGET_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:[^=]|$)", re.MULTILINE)
_DECL_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|const|let|var|interface|type|"
    r"struct|static|int|void|#define|tool|FROM|RUN)\b"
)
# 只挑强因果 / 排他性措辞：「因为 / 所以」太常见，纳入会误伤正常论述。
_CLAIM_MARKERS = re.compile(
    r"(?:不能|无法|不可能|必须|唯一|只能|决定了|之所以|直接原因|根本原因|"
    r"物理约束|别无选择|不得不|被迫|淘汰|不允许|只允许|不可替代)"
)
_PATH_REF_RE = re.compile(r"[A-Za-z0-9_.@-]+(?:/[A-Za-z0-9_.@-]+)*\.[A-Za-z0-9]+")


def _norm(line: str) -> str:
    """逐行比对用的归一形态：只去空白，保留注释（源码注释常是关键证据）。"""
    return re.sub(r"\s+", "", line)


@lru_cache(maxsize=8)
def _source_index(source_dir_str: str) -> tuple[frozenset[str], dict[str, int]]:
    """(所有实现文件的归一行集合, 相对路径 → 行数)，按源码目录缓存一次。"""
    root = Path(source_dir_str)
    lines: set[str] = set()
    counts: dict[str, int] = {}
    candidates: list[Path] = []
    for ext in _SOURCE_EXTS:
        candidates.extend(root.rglob("*" + ext))
    for pattern in _NAMED_SOURCE_FILE_PATTERNS:
        candidates.extend(root.rglob(pattern))
    for f in candidates:
        if not f.is_file():
            continue
        try:
            rel = f.relative_to(root).as_posix()
        except ValueError:
            continue
        parts = rel.split("/")
        # 排除隐藏/备份目录与依赖产物：既避免 .src_cache 之类镜像副本污染，
        # 也避免把 .backup 里的旧实现当成现行源码。
        if any(p.startswith(".") for p in parts[:-1]):
            continue
        if any(p in _SKIP_PARTS for p in parts[:-1]):
            continue
        try:
            ls = f.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            continue
        counts[rel.lower()] = len(ls)
        for ln in ls:
            n = _norm(ln)
            if len(n) >= 12:
                lines.add(n)
    return frozenset(lines), counts


@lru_cache(maxsize=8)
def _make_targets(source_dir_str: str) -> frozenset[str]:
    mf = Path(source_dir_str) / "Makefile"
    if not mf.exists():
        return frozenset()
    try:
        text = mf.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return frozenset()
    return frozenset(m.group(1) for m in _TARGET_RE.finditer(text))


def _resolve_path(path: str, counts: dict[str, int]) -> str | None:
    """把文章里的路径/末级文件名归一到真实相对路径，取层级最短的正式源码。"""
    p = path.lower().lstrip("./")
    if p in counts:
        return p
    matches = [fp for fp in counts if fp.endswith("/" + p)]
    if not matches:
        return None
    return min(matches, key=lambda fp: (fp.count("/"), fp))


def line_anchors(article: str, source_dir: Path | None) -> tuple[list[str], list[str]]:
    """返回 (有效 `path:line` 锚点, 无效锚点及原因)。文件必须存在、行号不能越界。"""
    if source_dir is None:
        return [], []
    _lines, counts = _source_index(str(source_dir))
    ok: list[str] = []
    bad: list[str] = []
    for m in _ANCHOR_RE.finditer(article):
        full, path, num = m.group(0), m.group(1), int(m.group(2))
        fp = _resolve_path(path, counts)
        if fp is None:
            bad.append(f"{full}（源码里无此文件）")
        elif num < 1 or num > counts[fp]:
            bad.append(f"{full}（行号越界，该文件共 {counts[fp]} 行）")
        else:
            ok.append(full)
    return list(dict.fromkeys(ok)), list(dict.fromkeys(bad))


def real_source_blocks(article: str, source_dir: Path | None) -> int:
    """逐字匹配真实源码的代码块数量（每块至少 2 行命中，架构图/伪代码不算）。"""
    if source_dir is None:
        return 0
    lines, _counts = _source_index(str(source_dir))
    n = 0
    for body in _CODE_BLOCK_RE.findall(article):
        hit = sum(1 for raw in body.splitlines()
                  if len(_norm(raw)) >= 12 and _norm(raw) in lines)
        if hit >= 2:
            n += 1
    return n


def repro_commands(article: str, source_dir: Path | None) -> tuple[list[str], list[str]]:
    """返回 (有效命令, 无效命令及原因)。`make <target>` 必须在 Makefile 里存在。"""
    cmds: list[str] = []
    for body in _CODE_BLOCK_RE.findall(article):
        cmds.extend(m.group(1).strip() for m in _LINE_CMD_RE.finditer(body))
    cmds.extend(m.group(1).strip() for m in _INLINE_CMD_RE.finditer(article))
    cmds = list(dict.fromkeys(c.strip() for c in cmds if c.strip()))
    targets = _make_targets(str(source_dir)) if source_dir is not None else frozenset()
    ok, bad = [], []
    for cmd in cmds:
        mm = _MAKE_RE.match(cmd)
        if mm and targets and mm.group(1) not in targets:
            bad.append(f"{cmd}（Makefile 无此 target）")
        else:
            ok.append(cmd)
    return ok, bad


def _body_for_claims(article: str) -> str:
    """论断检测范围：去掉 frontmatter / 代码块 / FAQ / TL;DR / 源码导航。

    FAQ 与速览是结论复述，锚点通常在正文；把它们纳入会系统性误伤好稿。
    """
    text = re.sub(r"^---[\s\S]*?---\s*", "", article, count=1)
    text = re.sub(r"\[faq\][\s\S]*?\[/faq\]", "", text, flags=re.IGNORECASE)
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(
        r"^##\s*(?:速览（TL;DR）|速览|TL;DR|源码导航|Source Code Navigation)"
        r"[\s\S]*?(?=^##\s|\Z)",
        "", text, flags=re.MULTILINE | re.IGNORECASE,
    )
    return text


def claim_sentences(article: str) -> list[tuple[int, str]]:
    """返回 (段落序号, 含因果/排他措辞的句子)。

    标题（`#`/`##`/`###` 开头）不算论断句：标题天然是独立段落，身边不可能出现
    `path:line` 或源码块，把它计入就是 100% 误报——而且它会排到「例如：…」的最前面，
    让隔离原因指向一个根本没法改的句子（2026-11-14 nsbp-lume 实跑：
    `### 代价：Node 必须自己实现 FastCGI 协议` 就是被这样误判的第一条）。
    """
    out: list[tuple[int, str]] = []
    for idx, para in enumerate(_body_for_claims(article).split("\n\n")):
        for sent in re.split(r"[。！？；\n]", para):
            s = sent.strip()
            if s.startswith("#"):
                continue
            if 12 <= len(s) <= 220 and _CLAIM_MARKERS.search(s):
                out.append((idx, s))
    return out


def _paragraph_evidence(para: str, anchors: list[str], lines: frozenset[str],
                        counts: dict[str, int]) -> bool:
    """本段是否带可核查证据：有效行号锚点 / 真实源码块 / ≥2 个不同实现文件。"""
    if any(a in para for a in anchors):
        return True
    for body in _CODE_BLOCK_RE.findall(para):
        hit = sum(1 for raw in body.splitlines()
                  if len(_norm(raw)) >= 12 and _norm(raw) in lines)
        if hit >= 2:
            return True
    refs = set()
    for m in _PATH_REF_RE.finditer(para):
        p = m.group(0)
        if p.lower().endswith(".md"):
            continue
        fp = _resolve_path(p, counts)
        if fp:
            refs.add(fp)
    return len(refs) >= 2


def ungrounded_claims(article: str, source_dir: Path | None,
                      anchors: list[str] | None = None) -> list[str]:
    """论断句所在段落及相邻 ±1 段都没有源码证据的，逐条列出（截断 80 字）。"""
    if source_dir is None:
        return []
    lines, counts = _source_index(str(source_dir))
    if anchors is None:
        anchors, _bad = line_anchors(article, source_dir)
    paras = _body_for_claims(article).split("\n\n")
    ev = [_paragraph_evidence(p, anchors, lines, counts) for p in paras]
    out = []
    for idx, sent in claim_sentences(article):
        if not any(ev[max(0, idx - 1):idx + 2]):
            out.append(sent[:80])
    return out


def grounding_report(article: str, source_dir: Path | None) -> dict:
    """成品证据链体检明细，供隔离原因与测试断言使用。"""
    if source_dir is None:
        return {"skipped": True, "anchors": [], "invalid_anchors": [],
                "source_blocks": 0, "commands": [], "invalid_commands": [],
                "claims": [], "ungrounded_claims": []}
    anchors, bad_anchors = line_anchors(article, source_dir)
    cmds, bad_cmds = repro_commands(article, source_dir)
    claims = [s for _idx, s in claim_sentences(article)]
    return {
        "skipped": False,
        "anchors": anchors,
        "invalid_anchors": bad_anchors,
        "source_blocks": real_source_blocks(article, source_dir),
        "commands": cmds,
        "invalid_commands": bad_cmds,
        "claims": claims,
        "ungrounded_claims": ungrounded_claims(article, source_dir, anchors),
    }


def claims_only_failure(article: str, source_dir: Path | None) -> list[str] | None:
    """门禁失败是否**只**卡在「无就近依据的论断」上。

    是则返回这些论断（供定向回填逐条处理），否则返回 None。锚点 / 逐字源码块 /
    可复现命令任一不达标都算「不是」——那种情况缺的是素材本身，回填救不了。
    """
    if source_dir is None:
        return None
    r = grounding_report(article, source_dir)
    if len(r["anchors"]) < MIN_LINE_ANCHORS or r["invalid_anchors"]:
        return None
    if r["source_blocks"] < MIN_REAL_SOURCE_BLOCKS:
        return None
    if len(r["commands"]) < MIN_REPRO_COMMANDS or r["invalid_commands"]:
        return None
    claims = r["ungrounded_claims"]
    if len(claims) <= MAX_UNGROUNDED_CLAIMS:
        return None
    return claims


def grounding_issue(article: str, source_dir: Path | None) -> str | None:
    """证据链不达标则返回可操作的中文隔离原因；通过返回 None。

    source_dir 为 None 时保持向后兼容（纯文本校验场景不拦），与
    project_relevance 的哨兵语义一致。
    """
    if source_dir is None:
        return None
    r = grounding_report(article, source_dir)
    problems = []
    if len(r["anchors"]) < MIN_LINE_ANCHORS:
        problems.append(
            f"可核查行号锚点 {len(r['anchors'])} 个（需 ≥{MIN_LINE_ANCHORS}）"
        )
    if r["invalid_anchors"]:
        problems.append("编造/越界锚点：" + "、".join(r["invalid_anchors"][:4]))
    if r["source_blocks"] < MIN_REAL_SOURCE_BLOCKS:
        problems.append(
            f"逐字摘录真实源码的代码块 {r['source_blocks']} 个"
            f"（需 ≥{MIN_REAL_SOURCE_BLOCKS}，README/架构图不算）"
        )
    if len(r["commands"]) < MIN_REPRO_COMMANDS:
        problems.append(f"可复现命令 {len(r['commands'])} 条（需 ≥{MIN_REPRO_COMMANDS}）")
    if r["invalid_commands"]:
        problems.append("不可复现命令：" + "、".join(r["invalid_commands"][:4]))
    if len(r["ungrounded_claims"]) > MAX_UNGROUNDED_CLAIMS:
        problems.append(
            f"无就近源码依据的因果/排他论断 {len(r['ungrounded_claims'])} 条"
            f"（上限 {MAX_UNGROUNDED_CLAIMS}），例如："
            + "；".join(r["ungrounded_claims"][:3])
        )
    if not problems:
        return None
    return (
        "论断—源码一致性未通过：" + "；".join(problems) + "。"
        "修法：每个「不能/无法/必须/唯一/只能/决定了/之所以」类论断句末紧跟 "
        "`相对路径:行号`（只能取自「源码证据锚点」清单，不得自编）；"
        "至少 1 个代码块逐字摘录真实源码 ≥2 行并标注出处；"
        "至少 1 条 Makefile 里真实存在的 `make <target>`；"
        "严禁把 README 描述性事实（如「镜像内没有 nginx」）升级成"
        "「不能用 nginx」这类排他性因果结论。换风格重跑不会修复证据缺失。"
    )


def build_anchor_block(sandbox_dir: Path, batches: list, max_files: int = 8,
                       max_anchors_per_file: int = 6) -> str:
    """从 Planner 选中的真实源文件抽取带行号的锚点 + Makefile 命令清单。

    这是「不靠模型自觉」的关键：行号由程序读文件算出，Writer 只能从清单里
    挑选引用；闸门再逐条反查文件与行数，自编行号一律被判无效。
    """
    files: list[str] = []
    for batch in batches:
        for f in batch.get("files", []):
            if f not in files:
                files.append(f)
    entries: list[str] = []
    for rel in files[:max_files]:
        fp = sandbox_dir / rel
        # 与闸门保持一致：README / ARCHITECTURE 等文档不是实现文件，
        # 生成它们的锚点只会诱导 Writer 复述文档然后被隔离。
        if fp.suffix.lower() == ".md":
            continue
        if not (fp.exists() and fp.is_file()):
            continue
        try:
            content = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        picked: list[tuple[int, str]] = []
        for i, ln in enumerate(content.splitlines(), 1):
            s = ln.strip()
            if not (12 <= len(s) <= 160):
                continue
            # 分隔注释（// -----）没有信息量，不能占锚点名额；实质注释
            # （如 "Equivalent of nginx.conf's 1800s"）恰恰是最有力的因果证据。
            if re.fullmatch(r"[-=/*#\s]+", s) or re.match(r"^(?://|#)\s*[-=*]{4,}", s):
                continue
            is_decl = bool(_DECL_RE.search(ln))
            is_code = bool(re.search(r"(?:\bconst\b|\bexport\b|=|:)", ln)) and not s.startswith(("//", "#", "*"))
            is_comment = s.startswith(("//", "#")) and len(s) >= 40 and bool(
                re.search(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{4,}", s)
            )
            if is_decl or is_code or is_comment:
                picked.append((i, s[:110]))
                if len(picked) >= max_anchors_per_file:
                    break
        if not picked:
            picked = [(i, ln.strip()[:110])
                      for i, ln in enumerate(content.splitlines(), 1)
                      if len(ln.strip()) >= 12][:max_anchors_per_file]
        entries.extend(f"- `{rel}:{i}` — {s}" for i, s in picked)
    cmds = []
    for t in sorted(_make_targets(str(sandbox_dir))):
        if t.startswith(".") or "%" in t:
            continue
        cmds.append(f"- `make {t}`")
        if len(cmds) >= 6:
            break
    return (
        "## 源码证据锚点（可核查，只能原样引用，严禁自行编造路径或行号）\n"
        + ("\n".join(entries) if entries else "（无可用锚点：本轮源码片段为空）")
        + "\n\n## 可复现命令（来自项目 Makefile，target 真实存在）\n"
        + ("\n".join(cmds) if cmds
           else "（项目无 Makefile，正文须给出真实存在的命令，不得编造）")
    )


def apply_grounding_repairs(article: str, pairs: list[tuple[str, str]]) -> tuple[str, int, int]:
    """按字符串精确替换回填补丁；找不到原句、或改动过大（换主题）的补丁一律丢弃。

    「最小改动」校验是必要的：实测模型会把「登录页无法走中继」整句换成「Home 页的数据
    预取在 SSR 阶段…」——语义换题，虽然锚点真实、闸门也过，但读者读到的已经不是原文的
    论证了。用字符相似度兜住：只接受「原句 + 补锚点 / 换几个词」这种级别的手术。
    """
    applied = rejected = 0
    for old, new in pairs:
        old = old.strip()
        new = new.strip()
        if not old or old not in article:
            continue
        if new.upper().startswith("DELETE"):
            article = article.replace(old, "", 1)
            applied += 1
            continue
        if difflib.SequenceMatcher(None, old, new).ratio() < 0.45:
            rejected += 1
            continue
        article = article.replace(old, new, 1)
        applied += 1
    # 删句后可能留下多余空行
    article = re.sub(r"\n{3,}", "\n\n", article)
    return article, applied, rejected
