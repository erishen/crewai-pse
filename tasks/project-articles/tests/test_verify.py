"""源码核查（安装命令 / CLI 入口点）的单测。

verify._check_* 返回 (虚构列表, 已验证列表)，纯函数、给定 source_dir 即可。
"""
import tempfile
from pathlib import Path

from pipeline import verify
from pipeline.config import MIN_PROJECT_RELEVANCE


def _w(p: Path, content: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


def test_check_install_commands_mismatch():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "pyproject.toml", '[project]\nname = "real-pkg"\n')
        bad, good = verify._check_install_commands("运行 pip install wrong-pkg 安装", sd)
        assert any("不匹配" in b for b in bad)
        assert good == []


def test_check_install_commands_match_good():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "pyproject.toml", '[project]\nname = "real-pkg"\n')
        bad, good = verify._check_install_commands("运行 pip install real-pkg 安装", sd)
        assert bad == []
        assert any("real-pkg" in g for g in good)


def test_check_install_commands_uv_project():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "pyproject.toml", '[project]\nname = "p"\n')
        _w(sd / "uv.lock", "")
        bad, good = verify._check_install_commands("使用 pip install p 安装", sd)
        assert any("uv" in b for b in bad)


def test_check_cli_entrypoints_missing():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        bad, good = verify._check_cli_entrypoints("运行 python -m mymod 启动", sd)
        assert any("入口点不存在" in b for b in bad)


def test_check_cli_entrypoints_ok():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "mymod" / "__main__.py", "print(1)")
        bad, good = verify._check_cli_entrypoints("运行 python -m mymod 启动", sd)
        assert bad == []
        assert any("入口点存在" in g for g in good)


def test_extract_java_symbols_classes_methods_constants():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "main" / "java" / "DockerSandboxExecutor.java",
            'package com.example.springharness.sandbox;\n'
            'public class DockerSandboxExecutor {\n'
            '    public static final String DEFAULT_MESSAGE = "hi";\n'
            '    public SandboxResult execute(String code, String language, Integer timeout) {\n'
            '        return SandboxResult.error("no");\n'
            '    }\n'
            '    private String buildDockerCommand(String language) { return "x"; }\n'
            '}\n'
            'public record SandboxResult(String out, String err, int code) {}\n')
        syms = verify.extract_real_symbols(sd)
        # 类名 + 文件名
        assert "dockersandboxexecutor" in syms
        # 方法名（camelCase，小写化）
        assert "execute" in syms
        assert "builddockercommand" in syms
        # static final 常量
        assert "default_message" in syms
        # record 类型名
        assert "sandboxresult" in syms


def test_extract_java_symbols_skips_comments_and_imports():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "Main.java",
            'import java.util.List;\n'
            '// public class FakeNotReal\n'
            '/* block\n'
            ' * public class AlsoFake\n'
            ' */\n'
            'public class Main {\n'
            '    // String fakeField;\n'
            '    private int real(@interface) { return 0; }\n'
            '{ return 0; }\n')
        syms = verify.extract_real_symbols(sd)
        assert "main" in syms
        assert "fakenotreal" not in syms
        assert "alsofake" not in syms


def test_check_code_refs_java_symbol_found():
    import tempfile as tf
    with tf.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "main" / "java" / "TaskManager.java",
            'public class TaskManager { public String restart(String id) { return id; } }')
        f, v = verify._check_code_refs({"TaskManager", "restart", "GhostClass"}, sd)
        assert any("TaskManager" in x for x in v)
        assert any("restart" in x for x in v)
        assert any("GhostClass" in x for x in f)


def test_check_code_refs_java_file_path_check():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "DockerSandboxExecutor.java", "public class X {}")
        # 文章反引号里引用 .java 路径/文件名 → 程序应按文件路径核查命中
        f, v = verify._check_code_refs({"./DockerSandboxExecutor.java"}, sd)
        assert any("文件存在" in x for x in v)
        assert f == []


# ── 回归：空代码块不产生任何可核查符号，旧逻辑会误判「无虚构」而放行 ──
def test_verify_article_flags_empty_code_block():
    with tempfile.TemporaryDirectory() as td:
        bad, _ok, _metrics = verify.verify_article(
            "正文讨论架构。\n\n```js\n```\n\n这段代码可以被当作证据。\n", Path(td)
        )
        assert any("证据残缺" in x for x in bad)


def test_verify_article_passes_real_code_block():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "a.ts", "export function realFn() { return 1; }")
        bad, _ok, _metrics = verify.verify_article(
            "正文讨论 `realFn`。\n\n```ts\nexport function realFn() { return 1; }\n```\n", sd
        )
        assert not any("证据残缺" in x for x in bad)
        assert bad == []


# ── 回归：跑题闸门 + Writer 符号候选（resolve-lume 通用议论文残稿事故）──

def test_rank_real_symbols_drops_noise_and_preserves_case():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "api.ts",
           "export async function streamChat() {}\n"
           "function readFrames() {}\n"
           "const CONTENT_LENGTH = 4096;\n"
           "const a = 1;\nlet ab = 2;\n")
        _w(sd / "README.md", "server backend server backend streamChat\n")
        _w(sd / "evidence" / "0d3b7f31f19f4eea4585f8e40b0b6be48f0f8d00.md", "screenshot\n")
        ranked = verify.rank_real_symbols(sd, 40)
        # 真实声明保留原大小写，噪声（单双字母 / 下划线 / 全大写宏 / hash 文件名）全部剔除
        assert "streamChat" in ranked
        assert "readFrames" in ranked
        assert "CONTENT_LENGTH" not in ranked
        assert "0d3b7f31f19f4eea4585f8e40b0b6be48f0f8d00" not in ranked
        assert all(not (s.startswith("_") or len(s) <= 2 or s.isupper()) for s in ranked)


def test_rank_real_symbols_does_not_collapse_to_alphabetical_head():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        # 104 个同分声明，首字母轮转 a-z。旧 sorted()[:N] 会全挤在 a/b/c 段。
        lines = [f"function {chr(97 + i % 26)}Api{i:03d}() {{}}" for i in range(104)]
        _w(sd / "many.ts", "\n".join(lines))
        ranked = verify.rank_real_symbols(sd, 40)
        assert len(ranked) == 40
        assert len({s[0].lower() for s in ranked}) >= 10


def test_project_relevance_flags_generic_essay():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "api.ts", "export async function streamChat() {}\nfunction readFrames() {}\n")
        generic = (
            "## 流行做法\n\n" + "多数团队会把脚本搬到网页上，这是合理的默认选择。" * 20
            + "\n\n## 质疑\n\n" + "当任务变长，长连接就不够用了。" * 20
            + "\n\n这是 my-app 的一点说明。\n"
        )
        score, detail = verify.project_relevance(generic, "my-app", sd)
        assert score < MIN_PROJECT_RELEVANCE
        assert detail["symbol_hits"] == []


def test_project_relevance_passes_grounded_article():
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "api.ts",
           "export async function streamChat() {}\n"
           "function readFrames() {}\nfunction parseFrame() {}\n")
        art = ("## 实现\n\nmy-app 的流式链路：`streamChat` 调用 `readFrames`，"
               "每帧交给 `parseFrame`。\n\n" + "my-app 的细节说明。" * 10)
        score, detail = verify.project_relevance(art, "my-app", sd)
        assert score >= MIN_PROJECT_RELEVANCE
        assert {"streamchat", "readframes", "parseframe"} <= set(detail["symbol_hits"])


def test_project_relevance_is_permissive_without_source():
    # 纯文本校验场景（无 project_key / source_dir）保持向后兼容，不能误拦
    assert verify.project_relevance("任意文字", "my-app", None)[0] == 999
    assert verify.project_relevance("任意文字", "", Path("/tmp"))[0] == 999


def test_project_relevance_credits_real_source_paths():
    """叙事型风格用真实文件路径指认项目，不能只按代码符号判跑题。

    11-06 实测：alva-e2e 的 A 风格稿写了 tests/comps.spec.ts 与
    tests/helpers/common.ts，却因 symbol_hits=['comps'] 只得 5 分被隔离。
    """
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "tests" / "comps.spec.ts", "test('comps', async () => {})")
        _w(sd / "tests" / "helpers" / "common.ts", "export const loadDashboard = 1")
        art = (
            "## 结构\n\n"
            "alva-e2e 把公共动作收进 `tests/helpers/common.ts`，"
            "用例按验证对象拆到 `tests/comps.spec.ts`。"
            "配置外置、公共逻辑收拢，spec 层只关心验证什么。\n\n"
            "alva-e2e 的取舍说明。\n"
        )
        score, detail = verify.project_relevance(art, "alva-e2e", sd)
        assert score >= MIN_PROJECT_RELEVANCE
        assert "tests/comps.spec.ts" in detail["path_hits"]
        assert "tests/helpers/common.ts" in detail["path_hits"]


def test_project_relevance_ignores_fictional_paths():
    """虚构路径不能拿分，否则闸门对幻觉稿形同虚设。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "src" / "real.ts", "export function realFn() {}")
        art = (
            "## 架构\n\nmy-app 的核心是 `src/makeJobId.ts` 与 "
            "`src/registerJob.ts`，还有 `src/jobStore.ts`。"
            + "这是一篇通用架构议论文。" * 20
        )
        score, detail = verify.project_relevance(art, "my-app", sd)
        assert detail["path_hits"] == []
        assert score < MIN_PROJECT_RELEVANCE


def test_project_relevance_accepts_path_suffix_form():
    """文章省略顶层目录（comps.spec.ts）也应命中真实文件，并归一到完整路径。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "tests" / "comps.spec.ts", "test('x', () => {})")
        art = "my-app 的用例集中在 `comps.spec.ts` 一个文件里。\n"
        score, detail = verify.project_relevance(art, "my-app", sd)
        assert detail["path_hits"] == ["tests/comps.spec.ts"]
        assert score >= 3


def test_project_relevance_dedupes_one_file_written_two_ways():
    """同一文件的完整路径 / 后缀形式只算 1 个证据，不能双计分白拿分。

    11-06 实测：resolve-lume 通用议论文只引 frontend/server-entrypoint.mjs
    一个文件，却因两种写法被算成 2 个 path_hits（6 分），加上项目名刷屏
    凑够 11 分放行。
    """
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "frontend" / "entrypoint.mjs", "// thin entry point\n")
        art = (
            "resolve-lume 的入口是 `frontend/entrypoint.mjs`，"
            "也就是 `entrypoint.mjs` 这个文件。\n\n" + "resolve-lume 的取舍。" * 12
        )
        score, detail = verify.project_relevance(art, "resolve-lume", sd)
        assert detail["path_hits"] == ["frontend/entrypoint.mjs"]
        # 只有一个真实文件、且没有真实 API 符号 ⇒ 证据硬条件不成立，
        # 即使项目名刷屏把分数顶到 8 分也必须隔离
        assert detail["evidence_ok"] is False
        assert score == 8


def test_project_relevance_picks_canonical_path_over_backup_copy():
    """后缀匹配多个文件时必须确定性地选正式源码，不能随 set 迭代顺序漂移。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "frontend" / "entrypoint.mjs", "// real\n")
        _w(sd / ".backup" / "old" / "frontend" / "entrypoint.mjs", "// backup\n")
        art = "my-app 的入口是 `entrypoint.mjs`，见 `frontend/entrypoint.mjs`。\n"
        _score, detail = verify.project_relevance(art, "my-app", sd)
        assert detail["path_hits"] == ["frontend/entrypoint.mjs"]


def test_project_relevance_ignores_root_level_generic_files():
    """README.md / package.json 太通用，不能作为项目绑定证据。"""
    with tempfile.TemporaryDirectory() as td:
        sd = Path(td)
        _w(sd / "README.md", "# my-app")
        _w(sd / "package.json", "{}")
        art = "参考 README.md 与 package.json 即可，my-app 用法很简单。\n"
        _score, detail = verify.project_relevance(art, "my-app", sd)
        assert detail["path_hits"] == []


def test_english_stopwords_are_not_fictitious_symbols():
    """正文反引号里的英文常用词（called/during）不该被当成虚构代码符号。"""
    with tempfile.TemporaryDirectory() as td:
        _w(Path(td) / "src" / "main.ts", "export function boot() {}\n")
        art = "这里所谓 `called` 时机，与 `during` 阶段一致，均由 `boot` 接管。"
        fictitious, verified = verify._check_code_refs(verify._extract_refs(art), Path(td))
        assert "called" not in fictitious and "during" not in fictitious
        assert any(v.startswith("boot") for v in verified)
