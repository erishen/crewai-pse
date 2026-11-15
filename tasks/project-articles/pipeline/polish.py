"""定稿后的编辑层体检。

来源是 2026-11-14 对 nsbp-lume 定稿的人工评审：四个问题都不是"写得对不对"，
而是"排出来好不好看"——标题中英混排、同一段源码贴两三遍、「这一节到此收口」
这类写作腔、结尾把前文复述一遍。

其中两类是确定性可修的（去重、标题整形），程序直接改；另两类只能报位置，
交给人工或后续重写判断，不做自动删改，避免误伤正文。
"""
from __future__ import annotations

import hashlib
import re

CJK = "\u4e00-\u9fff"

# 只在正文（去代码块后）里找，避免把源码注释里的英文当写作腔
# 只收「写作腔」的固定搭配；裸的「这一节」在正常叙述里也会出现
# （"「起点」这一节的结论很平"），按短语收才不误报。
META_ZH = (
    "这一节到此收口",
    "这一节我按",
    "这一节读下来",
    "把这一节",
    "到这里收口",
    "收口成一句",
    "本文",
    "下面我们",
    "接下来就是",
)
META_EN = (
    "This section closes here",
    "This article",
    "In this article",
    "Next, we will",
    "To sum up",
    "In summary",
)

FENCE_RE = re.compile(r"```[a-zA-Z]*\n.*?```", re.S)
H2_RE = re.compile(r"^## (.+)$", re.M)
# 只挑"短小的单行围栏"以外的块去重：`make check` 这种十来个字符重复一次不碍眼，
# 替换成一句话反而显得啰嗦
MIN_DEDUPE_CHARS = 40
ANCHOR_IN_BLOCK = re.compile(r"([A-Za-z0-9_./-]+\.(?:ts|tsx|js|mjs|cjs|json|sh|md|css|ya?ml|toml):\d+)")


def _section_of(text: str, pos: int) -> str:
    """取某个偏移量所在的最近一个 `## ` 小节标题。"""
    heads = [(m.start(), m.group(1).strip()) for m in re.finditer(r"^## (.+)$", text[:pos], re.M)]
    return heads[-1][1] if heads else "开头"


def dedupe_code_blocks(text: str, lang: str = "zh") -> tuple[str, int]:
    """同一段源码只保留首次出现，后续重复块改成一句指回原处的指引。

    2026-11-14 nsbp-lume 定稿：32 个代码块里 1660 字符（34%）是逐字重复，
    同一个 `while (!existsSync(sock))` 在三个小节各贴一遍。读者第二遍就开始跳。
    """
    seen: dict[str, tuple[str, str]] = {}
    replacements: list[tuple[int, int, str]] = []
    for m in FENCE_RE.finditer(text):
        raw = m.group(0)
        body = raw.split("\n", 1)[1][:-3].strip()
        if len(body) < MIN_DEDUPE_CHARS:
            continue
        key = hashlib.md5(body.encode("utf-8")).hexdigest()
        if key not in seen:
            anchor = ANCHOR_IN_BLOCK.search(body)
            seen[key] = (_section_of(text, m.start()), anchor.group(1) if anchor else "")
            continue
        sec, anchor = seen[key]
        where = f"（见 `{anchor}`）" if anchor else ""
        if lang == "en":
            hint = f'The same source snippet was already quoted verbatim in "{sec}"{where}; not repeating it here.'
        else:
            hint = f"同一段源码已在「{sec}」里逐字贴过{where}，这里不再重复。"
        replacements.append((m.start(), m.end(), hint))

    for start, end, hint in reversed(replacements):
        text = text[:start] + hint + text[end:]
    return text, len(replacements)


def normalize_title_spacing(text: str, lang: str = "zh") -> str:
    """标题中英混排整形：中文版把半角括号换全角、按需补空格、`）+` 改「与」。"""
    m = re.search(r"^title:\s*(.+)$", text, re.M)
    if not m:
        return text
    title = m.group(1).strip()
    fixed = title

    if lang == "zh":
        fixed = fixed.replace("(", "（").replace(")", "）")
        # 「（SSR）+ Lume」这种括号后面悬空的加号
        fixed = re.sub(r"）\s*\+\s*", "）与 ", fixed)
        # 「React服务端渲染」→「React 服务端渲染」（放在最后，才管得住刚补进来的「与」）
        fixed = re.sub(rf"([{CJK}])([A-Za-z0-9])", r"\1 \2", fixed)
        fixed = re.sub(rf"([A-Za-z0-9])([{CJK}])", r"\1 \2", fixed)
        fixed = re.sub(r"（\s*", "（", fixed)
        fixed = re.sub(r"\s*）", "）", fixed)
    else:
        fixed = re.sub(r"\s*\+\s*", " + ", fixed)

    fixed = re.sub(r"\s{2,}", " ", fixed).strip()
    if fixed == title:
        return text
    return text[: m.start(1)] + fixed + text[m.end(1) :]


def _body_without_fences(text: str) -> str:
    return FENCE_RE.sub(lambda m: "\n" * m.group(0).count("\n"), text)


def find_meta_narration(text: str, lang: str = "zh") -> list[tuple[int, str]]:
    """"这一节到此收口""This section closes here" 这类写作腔的位置清单。"""
    body = _body_without_fences(text)
    phrases = META_EN if lang == "en" else META_ZH
    hits: list[tuple[int, str]] = []
    for phrase in phrases:
        for m in re.finditer(re.escape(phrase), body):
            line = body[: m.start()].count("\n") + 1
            hits.append((line, phrase))
    return hits


POINTER_PREFIXES = (
    "同一段源码已在",
    "The same source snippet was already quoted",
    "The same source code was already pasted",
)


def _sentences(text: str) -> list[str]:
    """切句子，跳过列表项。

    源码导航那种「- `scripts/start.js` — 启动编排」的条目天然与前文同名，
    按句子比会一片误报；它们本来就该在结尾重复文件名。
    """
    out: list[str] = []
    for chunk in text.split("\n"):
        stripped = chunk.strip()
        if not stripped or stripped[0] in "-*|>#":  # 列表、表格、引用、标题
            continue
        if re.match(r"\d+[.、]", stripped):
            continue
        for part in re.split(r"[。！？]|(?<=[.!?])\s+", stripped):
            part = part.strip()
            if len(part) < 20 or part.startswith(POINTER_PREFIXES):
                continue
            out.append(part)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[\s，。！？；：、,.!?;:`*_\"“”（）()<>-]", "", s)


def find_tail_repetition(text: str) -> list[str]:
    """结尾两节里与前文重复的长句（"这段话前面说过一遍"）。"""
    heads = list(H2_RE.finditer(text))
    if len(heads) < 3:
        return []
    tail_start = heads[-2].start()
    earlier = _body_without_fences(text[:tail_start])
    tail = _body_without_fences(text[tail_start:])
    earlier_norm = {_norm(s) for s in _sentences(earlier)}
    dupes: list[str] = []
    for sent in _sentences(tail):
        n = _norm(sent)
        if n and any(n in e or e in n for e in earlier_norm):
            dupes.append(sent[:80])
    return dupes


def polish_article(text: str, lang: str = "zh") -> tuple[str, dict]:
    """跑一遍编辑层体检：能改的直接改，改不动的报出来。

    报告必须在去重**之前**算：去重会插进「同一段源码已在X节贴过」这类指引句，
    同一篇里出现多次后，再拿去比对就会把它们误判成"结尾复述前文"（2026-11-15 实跑命中）。
    """
    text = normalize_title_spacing(text, lang)
    meta = find_meta_narration(text, lang)
    tail = find_tail_repetition(text)
    text, deduped = dedupe_code_blocks(text, lang)
    return text, {
        "deduped_blocks": deduped,
        "meta_narration": meta,
        "tail_repetition": tail,
    }


def report_polish(report: dict) -> None:
    """把体检结果打进运行日志（不阻断流程：这些属于编辑质量，不是事实错误）。"""
    if report["deduped_blocks"]:
        print(f"🧽 源码去重：{report['deduped_blocks']} 个重复代码块改为指回原处")
    for line, phrase in report["meta_narration"]:
        print(f"⚠️ 写作腔（行 {line}）：{phrase} —— 建议改成叙述本身")
    for sent in report["tail_repetition"]:
        print(f"⚠️ 结尾复述前文：{sent}…")
