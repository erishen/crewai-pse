"""论断—源码一致性闸门单测（2026-11-06：README 改写稿曾 37 分全绿放行）。

全部用临时源码目录，验证的是确定性规则，不依赖 LLM：
- 有效 `path:line`（文件存在、行号不越界）才算锚点
- 代码块必须逐字命中真实实现文件，README / 编造代码都不算
- `make <target>` 必须在 Makefile 里存在
- 因果 / 排他论断必须就近带锚点
"""
import tempfile
from pathlib import Path

from pipeline import evidence
from pipeline.config import (
    MIN_LINE_ANCHORS,
    MIN_REAL_SOURCE_BLOCKS,
    MIN_REPRO_COMMANDS,
)

API_LINES = (
    "export async function streamChat() { return 1; }\n"
    "const timeout = AbortSignal.timeout(5000);\n"
    "export function readFrames() { return 2; }\n"
)
SERVER_LINES = (
    "// Equivalent of nginx.conf's proxy_read/send_timeout 1800s.\n"
    "server.requestTimeout = 0;\n"
    "server.timeout = 0;\n"
)


def _w(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _source(sd: Path) -> None:
    """一个迷你但真实的项目：两个实现文件 + Makefile + 一份 README。"""
    _w(sd / "src" / "api.ts", API_LINES)
    _w(sd / "src" / "server.mjs", SERVER_LINES)
    _w(sd / "Makefile", "check:\n\t@echo ok\nup:\n\t@echo up\n")
    _w(sd / "README.md", "# demo\n因为 SSE 是长连接，容器里不能用 nginx。\n")


def _good_article() -> str:
    return """---
title: demo
---

## 实现

前端把 SSE 超时关掉，因为 Node 默认 300s 会掐断长流，所以 `server.requestTimeout` 必须设为 0（`src/server.mjs:2`）。

`src/api.ts:1` 里的 `streamChat` 调用 `readFrames`，预检超时是 5000ms（`src/api.ts:2`）。

```ts
export async function streamChat() { return 1; }
const timeout = AbortSignal.timeout(5000);
```

复现：

```bash
make check
```

## 源码导航

- `src/api.ts`
- `src/server.mjs`

[faq]
问：为什么不能用 nginx？
答：本项目没用 nginx。
[/faq]
"""


def _readme_rewrite() -> str:
    """典型坏稿：名词全真实，因果全是编的，零源码证据。"""
    return """---
title: demo
---

## 架构

因为 SSE 是长连接，nginx 默认的代理超时会把连接掐掉，所以容器里不能用 nginx，只能换成 Node handler。

这决定了整个前端的形态，也是唯一的架构选择；镜像里没有 nginx 的根本原因就在这里。

```text
Browser -> frontend -> sidecar -> backend
```

## 源码导航

- `README.md`
"""


def test_good_article_passes_grounding():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        assert evidence.grounding_issue(_good_article(), sd) is None


def test_readme_rewrite_is_rejected():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        issue = evidence.grounding_issue(_readme_rewrite(), sd)
        assert issue is not None
        assert "论断—源码一致性未通过" in issue
        assert "可核查行号锚点" in issue
        assert "无就近源码依据的因果" in issue


def test_report_counts_all_four_dimensions():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        r = evidence.grounding_report(_good_article(), sd)
        assert len(r["anchors"]) >= MIN_LINE_ANCHORS
        assert r["source_blocks"] >= MIN_REAL_SOURCE_BLOCKS
        assert len(r["commands"]) >= MIN_REPRO_COMMANDS
        assert r["invalid_anchors"] == []
        assert r["invalid_commands"] == []
        assert r["ungrounded_claims"] == []


def test_invalid_anchors_are_flagged():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        ok, bad = evidence.line_anchors(
            "锚点 `src/api.ts:999` 与 `src/ghost.ts:3` 都是编的。", sd
        )
        assert ok == []
        assert any("行号越界" in b for b in bad)
        assert any("无此文件" in b for b in bad)


def test_named_source_files_are_valid_anchors():
    """Makefile / Dockerfile.backend 无扩展名，也必须与闸门双向对齐。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        _w(sd / "Dockerfile.backend", "FROM debian:bookworm-slim\nRUN apt-get update\n")
        ok, bad = evidence.line_anchors(
            "见 `Makefile:2`、`Dockerfile.backend:1` 与 `README.md:3`。", sd
        )
        assert "Makefile:2" in ok
        assert "Dockerfile.backend:1" in ok
        assert bad == []
        assert not any("README" in x for x in ok)  # 文档不是可核查锚点


def test_anchor_in_backticks_is_recognized():
    """Writer 按 prompt 写成 `path:line`（前后带反引号）必须能识别。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        ok, bad = evidence.line_anchors("见 `src/api.ts:1` 的实现。", sd)
        assert ok == ["src/api.ts:1"]
        assert bad == []


def test_readme_text_block_is_not_source_evidence():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = ("```md\n因为 SSE 是长连接，容器里不能用 nginx。\n"
               "镜像里没有 nginx 的根本原因就在这里。\n```\n")
        assert evidence.real_source_blocks(art, sd) == 0


def test_fabricated_code_is_not_source_evidence():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = ("```ts\nconst magic = totallyMadeUp(42);\n"
               "export function ghostFunction() { return 999; }\n```\n")
        assert evidence.real_source_blocks(art, sd) == 0


def test_real_code_block_is_counted():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = ("```ts\nexport async function streamChat() { return 1; }\n"
               "const timeout = AbortSignal.timeout(5000);\n```\n")
        assert evidence.real_source_blocks(art, sd) == 1


def test_make_target_must_exist():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        ok, bad = evidence.repro_commands("```bash\nmake ghost\n```\n", sd)
        assert ok == []
        assert any("Makefile 无此 target" in b for b in bad)
        ok2, bad2 = evidence.repro_commands("```bash\nmake check\n```\n", sd)
        assert "make check" in ok2 and bad2 == []


def test_claim_without_anchor_is_flagged():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = ("## 架构\n\n因为 SSE 是长连接，所以容器里不能用 nginx，"
               "只能换成 Node handler。\n\n这段没有锚点。\n")
        claims = evidence.ungrounded_claims(art, sd)
        assert len(claims) == 1
        assert "不能用 nginx" in claims[0]


def test_claim_with_anchor_passes():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = ("## 架构\n\n因为 SSE 是长连接，所以 `server.requestTimeout` "
               "必须设为 0（`src/server.mjs:2`）。\n")
        assert evidence.ungrounded_claims(art, sd) == []


def test_faq_and_navigation_are_excluded_from_claims():
    """FAQ 与源码导航是结论复述，锚点在正文；纳入会系统性误伤好稿。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = ("[faq]\n问：为什么不能用 nginx？\n答：因为不能用，所以必须换成 Node。\n[/faq]\n\n"
               "## 正文\n\n普通描述，没有因果词。\n\n"
               "## 源码导航\n\n- `src/api.ts`\n")
        assert evidence.claim_sentences(art) == []


def test_none_source_dir_is_permissive():
    """纯文本校验场景保持向后兼容，与 project_relevance 的哨兵语义一致。"""
    assert evidence.grounding_issue(_readme_rewrite(), None) is None
    r = evidence.grounding_report(_readme_rewrite(), None)
    assert r["skipped"] is True


def test_build_anchor_block_uses_real_lines_and_targets():
    """行号与 make target 必须由程序从真实文件读出，不能让模型自编。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        block = evidence.build_anchor_block(
            sd, [{"files": ["src/api.ts", "src/server.mjs"]}]
        )
        assert "`src/api.ts:1`" in block
        assert "`src/server.mjs:2`" in block
        assert "`make check`" in block
        assert "README" not in block


def test_build_anchor_block_skips_markdown_docs():
    """清单里不能出现闸门不认的 README.md:行号。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        block = evidence.build_anchor_block(
            sd, [{"files": ["README.md", "src/api.ts"]}]
        )
        assert "README.md:" not in block
        assert "`src/api.ts:1`" in block


def test_headings_are_not_claims():
    """标题不得计入论断句：它天然独立成段，身边永远没有证据 → 100% 误报。"""
    art = ("## 正文\n\n"
           "### 代价：Node 必须自己实现 FastCGI 协议\n\n"
           "这一段给出真实锚点 `src/api.ts:3`，所以 Node 必须自己实现协议这一点有据可查。\n")
    claims = evidence.claim_sentences(art)
    assert all(not s.startswith("#") for _i, s in claims), "标题不能被当成论断句"
    assert any("必须有据可查" in s or "必须自己实现协议" in s for _i, s in claims)


def test_claims_only_failure_detects_repairable_case():
    """证据齐备、只有论断缺就近锚点 → 可以交给定向回填。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = _good_article().replace(
            "## 源码导航",
            "## 代价\n\n"
            "Node 必须自己从头实现这套协议，因为上游没有现成的可复用实现。\n\n"
            "这个组合决定了请求的三条去向，必须由引擎侧独占端口。\n\n"
            "所以登录页无法走中继做渲染，只能由引擎自己接管。\n\n"
            "## 源码导航",
        )
        claims = evidence.claims_only_failure(art, sd)
        assert claims is not None, "锚点/源码块/命令都达标时应当判为可回填"
        assert len(claims) == 3, claims
        assert evidence.grounding_issue(art, sd) is not None


def test_claims_only_failure_rejects_missing_evidence_case():
    """素材本身就不达标时不能回填（回填救不了缺源码块的稿）。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _source(sd)
        art = "## 架构\n\n因为 SSE 是长连接，所以容器里不能用 nginx，只能换 Node。\n"
        assert evidence.claims_only_failure(art, sd) is None


def test_apply_grounding_repairs_accepts_minimal_edit():
    """补锚点属于最小改动，应当被采纳。"""
    old = "这个组合决定了请求的三条去向，静态资产交给 docroot。"
    art = f"## 边界\n\n{old}\n"
    fixed, applied, rejected = evidence.apply_grounding_repairs(
        art, [(old, old[:-1] + "（见 `src/api.ts:2`）。")])
    assert applied == 1 and rejected == 0
    assert "src/api.ts:2" in fixed


def test_apply_grounding_repairs_rejects_topic_switch():
    """整句换主题（哪怕新句锚点真实）必须丢弃：读者读到的已不是原文的论证。"""
    old = "所以登录页无法通过中继做 SSR，只能由引擎自己接管。"
    new = "Home 页的数据预取在 SSR 阶段完成，返回 { data, status } 结构（见 `src/api.ts:3`）。"
    art = f"## 边界\n\n{old}\n"
    fixed, applied, rejected = evidence.apply_grounding_repairs(art, [(old, new)])
    assert applied == 0 and rejected == 1
    assert old in fixed


def test_apply_grounding_repairs_delete_sentence():
    """DELETE 表示整句删除，且不留多余空行。"""
    art = "A\n\n必须只有一个答案，因为这是唯一的做法。\n\nB\n"
    fixed, applied, _ = evidence.apply_grounding_repairs(
        art, [("必须只有一个答案，因为这是唯一的做法。", "DELETE")])
    assert applied == 1
    assert "唯一的做法" not in fixed
    assert "\n\n\n" not in fixed


def test_repair_target_tolerates_label_prefix_and_punctuation():
    """模型把「原文」写成 `论断1：…`（漏句末标点）时仍要定位到同一句。

    实跑出现 12 条补丁全部精确匹配失败、退回成本更高的全文形态，根因就是这个前缀。
    """
    art = "## 边界\n\n`cacheSet` 在超过上限时淘汰最老的一条。\n\n别的段落。\n"
    fixed, applied, rejected = evidence.apply_grounding_repairs(
        art, [("论断2：`cacheSet` 在超过上限时淘汰最老的一条",
               "`cacheSet` 在超过上限时淘汰最老的一条（见 `src/cache.ts:9`）")])
    assert applied == 1 and rejected == 0
    assert "src/cache.ts:9" in fixed
    assert "论断2" not in fixed
    assert "。\n\n别的段落" in fixed  # 句末标点被保留


def test_repair_target_does_not_touch_unrelated_sentence():
    """相似度不足就宁可不改：绝不能在别的句子上动刀。"""
    art = "## 边界\n\n登录页由引擎自己接管，不走 React 中继。\n"
    fixed, applied, rejected = evidence.apply_grounding_repairs(
        art, [("完全无关的另一句话，讲的是缓存淘汰策略与命中率",
               "无关的改写句，也讲缓存（见 `src/a.ts:1`）")])
    assert applied == 0 and rejected == 0
    assert fixed == art


def test_repair_rejects_anchor_glued_inside_a_word():
    """模型把锚点插进词中间（`JS 模块` → `J（见 src:x）S 模块`）必须丢弃。"""
    art = "## 边界\n\n不是能 `import` 进 Node 的 JS 模块，React 只能挂 socket。\n"
    fixed, applied, rejected = evidence.apply_grounding_repairs(
        art, [("不是能 `import` 进 Node 的 JS 模块",
               "不是能 `import` 进 Node 的 J（见 `src/a.ts:3`）S 模块")])
    assert applied == 0 and rejected == 1
    assert fixed == art
