"""源码 grounding 核查：文章里写的符号，源码里必须真的有。

这是整条流水线信任闸门的底座——LLM 自评不可靠，改用 grep + 文件系统比对：
文章引用的每个函数/类/文件/环境变量/安装命令/CLI 入口，都必须在目标项目
源码里找得到。找不到的判为「虚构」，触发隔离或删除。

注意区分对待两类数字：
- 代码引用/环境变量/夸大词 → 确定虚构，触发隔离；
- 疑似基准数字（99.7% / 12ms / 3.2s）→ 只告警不隔离，避免误伤合法的
  端口号、版本号、HTTP 状态码。
"""

import re
from pathlib import Path

from pipeline.config import EXAGGERATED_TERMS
from pipeline.sanitize import has_reasoning_leak

# Python 关键字和内置名称，不应作为项目代码引用检查
PYTHON_KEYWORDS = {
    "def", "class", "import", "from", "return", "yield", "raise",
    "try", "except", "finally", "with", "as", "if", "elif", "else",
    "for", "while", "break", "continue", "pass", "lambda", "and",
    "or", "not", "in", "is", "True", "False", "None", "self", "cls",
    "async", "await", "global", "nonlocal", "del", "assert",
    "print", "len", "range", "str", "int", "float", "list", "dict",
    "set", "tuple", "bool", "type", "super", "isinstance", "issubclass",
    "hasattr", "getattr", "setattr", "enumerate", "zip", "map", "filter",
    "sorted", "reversed", "any", "all", "open", "input", "format",
    "property", "staticmethod", "classmethod", "abstractmethod",
    "Optional", "Union", "List", "Dict", "Set", "Tuple", "Any",
    "Callable", "Iterable", "Iterator", "Generator", "Sequence",
}

# JS/TS 关键字和内置名称，不应作为项目代码引用检查
JS_TS_KEYWORDS = {
    "const", "let", "var", "function", "return", "if", "else",
    "for", "while", "do", "switch", "case", "break", "continue",
    "try", "catch", "finally", "throw", "new", "delete", "typeof",
    "instanceof", "void", "this", "super", "class", "extends",
    "import", "from", "export", "default", "async", "await",
    "yield", "static", "get", "set", "public", "private",
    "protected", "readonly", "interface", "type", "enum",
    "namespace", "module", "declare", "abstract", "implements",
    "true", "false", "null", "undefined", "void", "Promise",
    "console", "window", "document", "require", "module",
    "exports", "process", "Buffer", "JSON", "Math", "Date",
    "Array", "Object", "String", "Number", "Boolean", "Symbol",
    "Map", "Set", "WeakMap", "WeakSet", "Error", "RegExp",
    "parseInt", "parseFloat", "isNaN", "isFinite", "setTimeout",
    "setInterval", "clearTimeout", "clearInterval",
}

BUILTIN_KEYWORDS = PYTHON_KEYWORDS | JS_TS_KEYWORDS

# Java/Kotlin/Go/Rust/C 关键字与内置类型——不应作为项目代码引用检查。
# 许多 Java 类只是语言/框架内置类型（String/List/Map/Exception...），
# 把它们当作「项目符号」会误报大量虚构引用，故一律跳过。
JAVA_BUILTIN_TYPES = {
    "String", "Integer", "Long", "Double", "Float", "Boolean", "Character", "Byte",
    "Short", "Object", "Class", "Void", "Exception", "RuntimeException", "Error",
    "Throwable", "List", "ArrayList", "LinkedList", "Map", "HashMap", "LinkedHashMap",
    "Set", "HashSet", "TreeSet", "Optional", "Iterable", "Iterator", "Collection",
    "Array", "StringBuilder", "StringBuffer", "Runnable", "Thread", "ProcessBuilder",
    "Future", "CompletableFuture", "Path", "Paths", "File", "Files", "InputStream",
    "OutputStream", "ByteArrayOutputStream", "BufferedReader", "PrintStream",
    "System", "Math", "Arrays", "Collections", "UUID", "Date", "TimeUnit",
    "ConcurrentHashMap", "Logger", "LoggerFactory", "Servlet", "JsonNode", "JsonParser",
}
JAVA_KEYWORDS = {
    "public", "private", "protected", "static", "final", "void", "class", "interface",
    "record", "enum", "extends", "implements", "return", "new", "this", "super",
    "if", "else", "for", "while", "do", "switch", "case", "default", "break",
    "continue", "try", "catch", "finally", "throw", "throws", "import", "package",
    "true", "false", "null", "instanceof", "abstract", "synchronized", "volatile",
    "transient", "native", "strictfp", "assert", "var", "yield", "sealed", "permits",
    "requireNonNull", "override", "fmt", "println", "printStackTrace",
}

BUILTIN_KEYWORDS |= JAVA_BUILTIN_TYPES | JAVA_KEYWORDS

# 疑似编造的基准/实验指标：先挑出「宣称 + 数值」的句式，再挑游离的精确量词
_CLAIM_RE = re.compile(
    r"(实测|实验数据|真实实验|基准(?:测试|结果)?|压测|测量|跑分)[^。\n]{0,40}?"
    r"(\d+(?:\.\d+)?\s*(?:ms|%|倍|秒|s|rps|qps|tps))",
    re.IGNORECASE,
)
_PRECISE_RE = re.compile(
    r"(?<![\w.])(?:\d{2,3}(?:\.\d+)?\s*%|"
    r"\d+(?:\.\d+)?\s*ms\b|"
    r"\d+(?:\.\d+)?\s*[skm]?s\b|"
    r"\d+\s*倍\b|"
    r"\d{1,3}/\d{1,3}\b)"
)
# 明显合法的技术常量上下文：端口、版本号、HTTP 动词/状态码
_LEGIT_CONTEXT_RE = re.compile(
    r"(?:\b(?:POST|GET|PUT|DELETE|PATCH)\b|\bport\b|\bversion\b|\bv\d|"
    r"\blocalhost:\d|\b127\.0\.0\.1:\d)",
    re.IGNORECASE,
)

_SCAN_EXTS = (
    "*.py", "*.md", "*.ts", "*.tsx", "*.js", "*.jsx",
    "*.java", "*.kt", "*.go", "*.rs", "*.c", "*.h", "*.cpp", "*.cc",
)
_SKIP_DIRS = (".venv", "__pycache__", "node_modules", ".src_cache", "target", "build", ".gradle")


def _iter_source_files(source_dir: Path, exts=_SCAN_EXTS):
    """遍历源码目录下需要扫描的文件（跳过依赖/缓存目录）。"""
    for ext in exts:
        for f in source_dir.rglob(ext):
            if any(skip in str(f) for skip in _SKIP_DIRS):
                continue
            yield f


def _extract_refs(article: str) -> set[str]:
    """从文章中提取所有可能是「项目代码符号」的引用。

    两路来源：正文里的反引号代码片段，以及代码块里的定义/导入/调用。
    """
    refs = set(re.findall(r"`([A-Za-z_][\w._]*(?:/[A-Za-z_][\w._]*)*)`", article))
    # 代码块内提取：def/class 定义、import 目标、函数调用（snake_case 或 CamelCase）
    # 支持 Python / JS/TS / Java 代码块
    for block in re.finditer(
            r"```(?:python|typescript|ts|javascript|js|tsx|jsx|java|kotlin|kt)?\s*\n(.*?)```",
            article, re.DOTALL):
        for line in block.group(1).split("\n"):
            # def/class 定义 + import ...
            m = re.match(r"^\s*(?:def|class)\s+(\w+)", line)
            if m:
                refs.add(m.group(1))
                continue
            # from X import Y [, Z]  → 提取每个导入名
            m = re.match(r"^\s*from\s+[\w.]+?\s+import\s+(.+)", line)
            if m:
                for part in re.split(r"[,\s]+", m.group(1)):
                    part = part.strip()
                    if part and part != "as":
                        refs.add(part.split(" as ")[0].strip())
                continue
            # import X [as Y]  → 提取模块名
            m = re.match(r"^\s*import\s+(.+)", line)
            if m:
                for part in re.split(r"[,\s]+", m.group(1)):
                    part = part.strip()
                    if part:
                        refs.add(part.split(" as ")[0].strip())
                continue
            # Java 类/接口/record 声明、方法调用、static final 常量
            m = re.match(
                r"^\s*(?:public\s+|private\s+|protected\s+)?"
                r"(?:abstract\s+|static\s+|final\s+)*"
                r"(?:class|interface|record|enum)\s+([A-Za-z_]\w*)",
                line,
            )
            if m:
                refs.add(m.group(1))
                continue
            m = re.match(
                r"^\s*(?:public\s+|private\s+|protected\s+)?"
                r"static\s+final\s+[A-Za-z_][\w.<>\[\]]*\s+([A-Z][A-Z0-9_]*)",
                line,
            )
            if m:
                refs.add(m.group(1))
                continue
            # Java 方法声明/调用：classCamelCase 或 snake_case 后跟 (
            for cm in re.finditer(r"\b([a-z][A-Za-z0-9]*|[\w]+_\w+)|\b([A-Z][A-Za-z0-9]+)\s*\(", line):
                if cm.group(1):
                    refs.add(cm.group(1))
                elif cm.group(2):
                    refs.add(cm.group(2))
            # 函数调用：snake_case 或 CamelCase，长度>=3（排除 print/len 等无下划线小写词）
            for cm in re.finditer(r"\b([a-z]+(?:_[a-z0-9]+)+|[A-Z][a-zA-Z0-9]+)\s*\(", line):
                name = cm.group(1)
                if len(name) >= 3:
                    refs.add(name)
            # JS/TS: function name( / const name = / class Name / interface Name
            m = re.match(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", line)
            if m:
                refs.add(m.group(1))
                continue
            m = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)", line)
            if m:
                refs.add(m.group(1))
                continue
            m = re.match(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(\w+)", line)
            if m:
                refs.add(m.group(1))
                continue
            m = re.match(r"^\s*(?:export\s+)?interface\s+(\w+)", line)
            if m:
                refs.add(m.group(1))
                continue
            # JS/TS: import { X, Y } from '...'  → 提取每个导入名
            m = re.match(r"^\s*import\s+\{(.+?)\}\s+from", line)
            if m:
                for part in re.split(r"[,\s]+", m.group(1)):
                    part = part.strip()
                    if part and part != "as" and part != "type":
                        refs.add(part.split(" as ")[0].strip())
                continue
    return refs


def _check_code_refs(refs: set[str], source_dir: Path):
    """逐个符号核查：文件路径递归搜磁盘，类名/函数名 grep 源码。"""
    fictitious: list[str] = []
    verified: list[str] = []
    for ref in sorted(refs):
        if len(ref) < 3 or ref.startswith("http"):
            continue
        if ref in BUILTIN_KEYWORDS:
            continue
        # 文件路径 → 递归搜磁盘
        if "/" in ref or ref.endswith((".py", ".md", ".ts", ".tsx", ".js", ".jsx",
                                      ".java", ".kt", ".go", ".rs", ".c", ".h", ".cpp", ".cc")):
            found = any(
                f.name == ref.rsplit("/", 1)[-1]
                for ext in _SCAN_EXTS
                for f in _iter_source_files(source_dir, (ext,))
            )
            if found:
                verified.append(f"{ref} (文件存在)")
            else:
                fictitious.append(ref)
            continue
        # 类名/函数名 → grep（排除注释行，搜全部扫描语言文件）
        found = False
        for f in _iter_source_files(source_dir):
            try:
                for line in f.read_text(encoding="utf-8").split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    # JS/TS 注释行
                    if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                        continue
                    if ref in stripped:
                        found = True
                        verified.append(f"{ref} → {f.relative_to(source_dir)}")
                        break
                if found:
                    break
            except Exception:
                continue
        if not found:
            fictitious.append(ref)
    return fictitious, verified


def _check_command_paths(article: str, source_dir: Path) -> list[str]:
    """检查 `cd xxx`、`pip install ...` 等命令里引用的路径是否真实存在。"""
    bad = []
    for cmd_match in re.finditer(r"`(pip\s+install[^`]+|python\s+-m\s+[^`]+|cd\s+[^`]+)`", article):
        cmd = cmd_match.group(1)
        for p in re.findall(r"\S+/\S+", cmd):
            if not (source_dir / p.lstrip("/")).exists() and not (source_dir.parent / p.lstrip("/")).exists():
                bad.append(f"[命令] {cmd.strip()[:60]} (路径不存在)")
    return bad


def _check_env_vars(article: str, source_dir: Path):
    """环境变量名核查：与 .env.example 里的真实变量名比对。"""
    env_example = source_dir / ".env.example"
    if not env_example.exists():
        return [], []
    real_vars = set()
    for line in env_example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            real_vars.add(line.split("=", 1)[0].strip())
    # 也提取注释里的变量（如 # FREE_KEY=...）
    for line in env_example.read_text(encoding="utf-8").splitlines():
        m = re.match(r"#\s*(\w+)=", line)
        if m:
            real_vars.add(m.group(1))
    # 检查文章中出现的 export XXX 和 XXX=yyy 格式
    article_vars = set()
    # 只匹配 shell 风格的 export VARNAME（全大写），跳过 TS 的 export interface/const/function/async
    for m in re.finditer(r"export\s+([A-Z][A-Z0-9_]*)", article):
        article_vars.add(m.group(1))
    for m in re.finditer(r"^(\w+)=\S+", article, re.MULTILINE):
        var = m.group(1)
        if var.isupper() or var.startswith(("OPENAI_", "FREE_", "PSE_", "CRM_")):
            article_vars.add(var)
    verified, fictitious = [], []
    for var in sorted(article_vars):
        if var in real_vars:
            verified.append(f"{var} (环境变量存在于 .env.example)")
        else:
            fictitious.append(f"[环境变量] {var} 不在 .env.example 中，可能是虚构的")
    return fictitious, verified


def _check_install_commands(article: str, source_dir: Path):
    """安装命令核查：pip install <pkg> 是否匹配 pyproject.toml 的 name；uv 项目不应出现 pip。

    返回 (虚构列表, 已验证列表)——pip install 与 pyproject name 一致也算一次正向验证。
    """
    bad, good = [], []
    pyproject = source_dir / "pyproject.toml"
    if not pyproject.exists():
        return bad, good
    content = pyproject.read_text(encoding="utf-8")
    m = re.search(r'name\s*=\s*"([^"]+)"', content)
    if m:
        real_pkg_name = m.group(1)
        for pip_match in re.finditer(r"pip\s+install\s+(\S+)", article):
            claimed_pkg = pip_match.group(1)
            if claimed_pkg == real_pkg_name:
                good.append(f"pip install {claimed_pkg} (匹配 pyproject.toml)")
            else:
                bad.append(
                    f"[安装] pip install {claimed_pkg} 与 pyproject.toml name={real_pkg_name} 不匹配"
                )
    # 检查是否应该用 uv sync 而非 pip install
    if (source_dir / "uv.lock").exists() and "pip install" in article:
        bad.append("[安装] 项目使用 uv 管理（存在 uv.lock），文章中不应出现 pip install，应改为 uv sync")
    return bad, good


def _check_cli_entrypoints(article: str, source_dir: Path):
    """CLI 入口点核查：python -m <module> 是否有对应的 __main__.py。

    返回 (虚构列表, 已验证列表)。
    """
    bad, good = [], []
    for m in re.finditer(r"python\s+-m\s+(\S+)", article):
        module_path = m.group(1).replace(".", "/")
        main_file = source_dir / "src" / module_path / "__main__.py"
        if main_file.exists():
            good.append(f"python -m {m.group(1)} (入口点存在)")
            continue
        # 也检查 tasks/ 下的 run.py
        alt_file = source_dir / "tasks" / module_path / "run.py"
        if not alt_file.exists():
            bad.append(f"[CLI] python -m {m.group(1)} 入口点不存在，无 __main__.py")
    return bad, good


def _collect_metrics_to_verify(article: str) -> list[str]:
    """收集「疑似编造的基准/实验指标」——仅告警，不触发隔离。"""
    metrics: list[str] = []
    claim_spans: list[tuple[int, int]] = []
    for m in _CLAIM_RE.finditer(article):
        claim_spans.append((m.start(), m.end()))
        metrics.append(f"[待核数字] 宣称「{m.group(1)}」并给出数值 {m.group(2).strip()}，请确认来自真实实验")
    for m in _PRECISE_RE.finditer(article):
        # 若已是被「宣称」句式覆盖的数值，跳过，避免重复告警
        if any(s <= m.start() < e for s, e in claim_spans):
            continue
        tok = m.group(0).strip()
        # 排除明显合法的技术常量：端口、版本号、@Transactional 隔离级别、HTTP 状态码等
        if _LEGIT_CONTEXT_RE.search(article[max(0, m.start() - 30):m.end() + 10]):
            continue
        metrics.append(f"[待核数字] 含游离精确量词 {tok}，若属性能/效果数据须来自真实实验")
    return metrics


def verify_article(article: str, source_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """程序化验证：grep 检查代码引用 + 环境变量 + 安装命令 + CLI 入口点。

    返回 (虚构列表, 正确列表, 待核数字列表)：
    - 虚构列表：确定虚构（代码引用/环境变量/夸大词/思维链泄漏），触发隔离；
    - 正确列表：已验证存在；
    - 待核数字列表：疑似实验/基准数字（如 99.7% / 12ms / 3.2s），需人工确认是否来自真实实验。
      仅告警、不隔离——避免误伤含合法端口号/版本号/配置值的技术文。
    """
    refs = _extract_refs(article)
    fictitious, verified = _check_code_refs(refs, source_dir)

    fictitious += _check_command_paths(article, source_dir)

    bad_env, ok_env = _check_env_vars(article, source_dir)
    fictitious += bad_env
    verified += ok_env

    bad_pkg, ok_pkg = _check_install_commands(article, source_dir)
    bad_cli, ok_cli = _check_cli_entrypoints(article, source_dir)
    fictitious += bad_pkg + bad_cli
    verified += ok_pkg + ok_cli

    # 描述夸大检查
    for keyword, reason in EXAGGERATED_TERMS.items():
        if keyword in article:
            fictitious.append(f"[夸大] {keyword} — {reason}")

    # ── 思维链/内部独白泄漏硬闸 ──
    # 任何风格都不允许 Thought:/Answer:/内容大纲/关键发现 等推理独白进入成品。
    # 此处为兜底：即便正文清洗漏网，也标记为虚构，由调用方隔离到 needs-review。
    if has_reasoning_leak(article):
        fictitious.append("[思维链泄漏] 检测到内部推理独白（Thought:/Answer:/内容大纲 等）泄漏到成品，需人工复核")

    # ── 待核数字（疑似编造的基准/实验指标）──
    # 用户铁律：所有数字必须由真实实验获得，严禁编造。但代码层无法判断数字真假，
    # 故仅做「疑似基准指标」软提示，交由人工复核，不触发隔离。
    metrics_to_verify = _collect_metrics_to_verify(article)

    return fictitious, verified, metrics_to_verify


def extract_real_symbols(source_dir: Path) -> set[str]:
    """从真实源码提取符号表（函数/类名、文件名、全大写常量），供 grounding 比对与 Writer 白名单。

    程序化提取，不依赖模型：扫描 .py/.ts/.tsx/.js/.java/.kt 等文件的
    def/class/interface/record/方法定义、文件名（不含扩展名）、全大写常量赋值。
    返回小写化集合便于比对。Java 符号单独提取，避免与内置类型混入。
    """
    symbols: set[str] = set()
    java_symbols = _extract_java_symbols(source_dir)
    symbols |= java_symbols
    for f in _iter_source_files(source_dir):
        symbols.add(f.stem.lower())
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                continue
            # JS/TS 注释行
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            # Python: def / async def
            m = re.match(r"^\s*(?:async\s+)?def\s+(\w+)", line)
            if m:
                symbols.add(m.group(1).lower())
                continue
            # Python: class
            m = re.match(r"^\s*class\s+(\w+)", line)
            if m:
                symbols.add(m.group(1).lower())
                continue
            # Python: 全大写常量
            m = re.match(r"^\s*([A-Z][A-Z0-9_]{2,})\s*=", line)
            if m:
                symbols.add(m.group(1).lower())
                continue
            # JS/TS 代码后续符号已由 Java 等其他语言覆盖；此处仍保留 JS/TS 提取
            m = re.match(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(\w+)", line)
            if m:
                symbols.add(m.group(1).lower())
                continue
            m = re.match(r"^\s*(?:export\s+)?(?:const|let|var)\s+(\w+)", line)
            if m:
                symbols.add(m.group(1).lower())
                continue
            m = re.match(r"^\s*(?:export\s+)?(?:default\s+)?(?:class|interface)\s+(\w+)", line)
            if m:
                symbols.add(m.group(1).lower())
    return symbols


def _extract_java_symbols(source_dir: Path) -> set[str]:
    """扫描 .java/.kt 文件，提取类/接口/record 名、类型名、static final 常量名。

    与 `extract_real_symbols` 分开，因为 Java 的类声明语法与 Python/JS 差异大，
    且 Java 代码引用核查（_check_code_refs）需要单独处理（方法调用以 `(` 结尾、
    类名是 PascalCase、常量是 UPPER_SNAKE_CASE）。
    """
    symbols: set[str] = set()
    for f in _iter_source_files(source_dir, ("*.java", "*.kt")):
        symbols.add(f.stem.lower())
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in text.split("\n"):
            stripped = line.strip()
            # 跳过注释、import、package
            if stripped.startswith(("//", "/*", "*", "import", "package", "@")):
                continue
            # public/private/protected class|interface|record|enum Name
            m = re.match(
                r"^\s*(?:public\s+|private\s+|protected\s+)?"
                r"(?:abstract\s+|static\s+|final\s+)*"
                r"(?:class|interface|record|enum)\s+([A-Za-z_]\w*)",
                line,
            )
            if m:
                symbols.add(m.group(1).lower())
                continue
            # public static final TYPE NAME = ;
            m = re.match(
                r"^\s*(?:public\s+|private\s+|protected\s+)?"
                r"static\s+final\s+[A-Za-z_][\w.<>\[\]]*\s+([A-Z][A-Z0-9_]*)",
                line,
            )
            if m:
                symbols.add(m.group(1).lower())
                continue
            # 方法声明:  public RETURN_TYPE methodName(args) {  或  public RETURN_TYPE methodName();
            m = re.match(
                r"^\s*(?:public\s+|private\s+|protected\s+)?"
                r"(?:static\s+|final\s+|default\s+|synchronized\s+)*"
                r"[A-Za-z_][\w.<>\[\],\s]*\s+([a-z][\w]*)\s*\(",
                line,
            )
            if m and not m.group(1).lower().startswith(("if", "for", "while", "switch", "return", "new", "catch")):
                symbols.add(m.group(1).lower())
    return symbols
