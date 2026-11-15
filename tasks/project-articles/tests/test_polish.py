"""编辑层体检的单测（去重、标题整形、写作腔、结尾复述）。"""
from pipeline import polish

DUP = "```javascript\nwhile (!existsSync(sock)) {\n  await wait(100);\n  waited += 100;\n}\n```"


def test_dedupe_keeps_first_and_hints_the_rest():
    text = "## 一\n\n" + DUP + "\n\n## 二\n\n" + DUP + "\n"
    out, n = polish.dedupe_code_blocks(text, "zh")
    assert n == 1
    assert out.count("while (!existsSync(sock))") == 1
    assert "「一」" in out
    # 幂等：再跑一次不该再改
    again, n2 = polish.dedupe_code_blocks(out, "zh")
    assert n2 == 0 and again == out


def test_trivial_blocks_are_left_alone():
    text = "## 一\n\n```bash\nmake check\n```\n\n## 二\n\n```bash\nmake check\n```\n"
    out, n = polish.dedupe_code_blocks(text, "zh")
    assert n == 0 and out == text


def test_title_spacing_zh():
    fm = "---\ntitle: React 服务端渲染(SSR)+ Lume 引擎\ntags: []\n---\n"
    out = polish.normalize_title_spacing(fm, "zh")
    assert "title: React 服务端渲染（SSR）与 Lume 引擎" in out


def test_title_spacing_en():
    fm = "---\ntitle: React SSR+Lume Engine\n---\n"
    out = polish.normalize_title_spacing(fm, "en")
    assert "title: React SSR + Lume Engine" in out


def test_meta_narration_located_not_rewritten():
    text = "## 一\n\n正文。\n\n初步排查到这里收口：下面就是拆解。\n"
    hits = polish.find_meta_narration(text, "zh")
    assert hits and hits[0][1] == "到这里收口"


def test_meta_narration_ignores_code_blocks():
    text = "## 一\n\n```javascript\nconst s = '这一节到此收口';\n```\n"
    assert polish.find_meta_narration(text, "zh") == []


def test_tail_repetition_detected():
    root = "真正让这套编排失守的，是 existsSync 把文件存在当成了后端在，这句话说得很明白了。"
    text = f"## 一\n\n{root}\n\n## 二\n\n{root}\n\n## 三\n\n更短的收尾。\n"
    assert polish.find_tail_repetition(text)


def test_dedupe_hint_is_not_reported_as_tail_repetition():
    """去重插进去的指引句不算"结尾复述前文"。"""
    body = "## 一\n\n甲句讲一件事，讲了二十来个字，具体内容在这里。\n\n## 二\n\n"
    hint = "同一段源码已在「一」里逐字贴过，这里不再重复。"
    text = body + hint + "\n\n乙句讲另一件事，也是二十来个字，内容如下。\n\n## 三\n\n" + hint + "\n"
    out, rep = polish.polish_article(text, "zh")
    assert rep["tail_repetition"] == []


def test_dedupe_drops_orphaned_anchor_caption():
    """块被换成指引句后，块下方那行纯锚点图注不能留下来悬空。"""
    block = "```ts\nconst a = 1;\nconst b = 2;\nconst c = 3;\nconst d = 4;\n```"
    text = f"## 一\n\n{block}\n\n（`scripts/build-ssr.js:1`–`scripts/build-ssr.js:5`）\n\n甲段。\n\n## 二\n\n{block}\n\n（`scripts/build-ssr.js:1`–`scripts/build-ssr.js:5`）\n\n乙段。\n"
    out, n = polish.dedupe_code_blocks(text, "zh")
    assert n == 1
    assert out.count("scripts/build-ssr.js:1") == 1
    assert "这里不再重复。" in out


def test_orphaned_caption_after_existing_hint_is_removed():
    """历史稿里已有的「指引句 + 悬空图注」也要清掉，不只是新生成的。"""
    text = ("## 一\n\n同一段源码已在「映射」里逐字贴过（见 `a.ts:3`），这里不再重复。\n\n"
            "（`scripts/build-ssr.js:1`–`scripts/build-ssr.js:5`）\n\n正文。\n")
    out, n = polish.dedupe_code_blocks(text, "zh")
    assert n == 0
    assert "build-ssr.js" not in out
