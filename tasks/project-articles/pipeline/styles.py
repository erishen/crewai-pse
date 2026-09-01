"""叙事风格调度：选风格、选表述变体、记录历史。

为什么不让 LLM 自己选：模型有惯性，自由选择时连续几篇都会落到同一种风格。
这里用「最少使用优先 + 随机轮转」程序化决定，再强制注入 Planner 与 Writer。
"""

import json
import random

from pipeline.config import STYLE_HISTORY_FILE, STYLE_NAMES


def pick_variants(style: str) -> tuple:
    """为一次生成随机选择「表述变体」（开场钩子/人称/时间轴），
    让同一风格在不同文章间也有书写差异，进一步降低成稿雷同感。

    变体是方向性指导，不改变风格的核心结构与禁止项。
    """
    hooks = [
        "开场先用一个具体场景/画面把读者拉进上下文（谁在什么处境下、看到了什么）。",
        "开场先抛出一组能反映问题分量的数据或数字，再进入叙事。",
        "开场直接点出本文的核心结论或反直觉的地方，再展开论证。",
        "开场就用第一人称说出当时的处境与纠结（我在做什么、卡在哪）。",
    ]
    person = random.choice([
        "全文以第一人称「我」贯穿，保留真实思考痕迹。",
        "全文以客观陈述为主，多用第三人称，少出现「我」。",
    ])
    timeline = random.choice([
        "按时间顺叙推进：先因后果。",
        "用倒叙：先把最终结果亮出来，再回溯过程。",
    ])
    return random.choice(hooks), person, timeline


def load_style_history() -> dict:
    if STYLE_HISTORY_FILE.exists():
        try:
            return json.loads(STYLE_HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def pick_style(project_key: str, p: dict) -> str:
    """自动选择叙事风格，保证成稿多样性。

    优先级：
    1. projects.json 该项目显式声明 `style` 字段 → 以人力指定为准（最高优先）。
    2. 否则从 6 种风格里选「历史上用得最少」的；候选仍多个时随机挑一个。
    3. 避开本项目上次用过的风格（若因此只剩一种候选，则允许复用）。
    """
    explicit = (p or {}).get("style")
    if explicit and str(explicit).strip().upper() in STYLE_NAMES:
        return str(explicit).strip().upper()
    history = load_style_history()
    counts = {k: 0 for k in STYLE_NAMES}
    for letter in history.values():
        letter = str(letter).upper()
        if letter in counts:
            counts[letter] += 1
    min_count = min(counts.values())
    candidates = [k for k, c in counts.items() if c == min_count]
    last = str(history.get(project_key, "")).upper()
    if len(candidates) > 1 and last in candidates:
        candidates = [k for k in candidates if k != last]
    if not candidates:
        candidates = list(STYLE_NAMES)
    return random.choice(candidates)


def record_style(project_key: str, letter: str) -> None:
    history = load_style_history()
    history[project_key] = letter
    try:
        STYLE_HISTORY_FILE.write_text(
            json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError as e:
        print(f"⚠️ 记录风格历史失败（不影响本次产出）: {e}")
