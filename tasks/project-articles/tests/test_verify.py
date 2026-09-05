"""源码核查（安装命令 / CLI 入口点）的单测。

verify._check_* 返回 (虚构列表, 已验证列表)，纯函数、给定 source_dir 即可。
"""
import tempfile
from pathlib import Path

from pipeline import verify


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
