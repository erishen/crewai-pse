<div align="right">
  <a href="README.md">🇬🇧 English</a>
</div>

# CrewAI PSE

基于 [CrewAI](https://www.crewai.com/) 构建的、 **任务无关** 的 **Planner–Specialist–Evaluator (PSE)** 三角色多智能体框架。引擎负责装配 PSE 智能体组、从任务目录加载各角色的系统提示词、并运行「生成→核查→修正」循环。**在 `tasks/` 下新建一个文件夹即可添加一个任务**——核心代码无需改动。

仓库当前内置 **一个任务**：`project-articles`（源码 → 中英文双语文章 → 经 `wordpress-tools` 发布到 WordPress）。下文把它作为「已落地的参考实现」来写；框架本身并不局限于它。

> [!NOTE]
> **成本 — 运行前必读。** `project-articles` 一次完整的 CrewAI 运行约 **¥0.05–0.10 / 篇**（约为直接 API 调用的 6–12 倍），因为每个角色都会驱动 LLM，token 消耗很重，所以**不要每次提交都自动跑**。要廉价出草稿请用 `autogen-pse`（两段式直接 API，约 ¥0.01/篇）。`run.py` 每次结束都会打印真实 token 用量与费用估算。

## 工作原理

框架提供一套可复用的 **PSE 引擎**；每个 *任务* 自行提供提示词、编排逻辑与输入输出。

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PSE 引擎  (src/crewai_pse — 任务无关)                                      │
│                                                                            │
│   create_crew(task) ──▶ Planner ──▶ Specialist ──▶ (核查 + 修正 ≤N)         │
│                          │            │                 │                  │
│                          │            │           程序化核查                │
│                          │            │           (grep / schema / …)       │
│                          │            │                 ├─ ✅ 通过           │
│                          │            │                 └─ 🔄 未过 ─▶ 修正  │
│                          │            ▼                                    │
│                    task.run() 负责 IO：读输入、存输出                       │
│                                                                            │
│   ⚙ Evaluator 智能体已定义，但被排除在 Sequential 流程外；               │
│     核查是程序化完成的（比 LLM 自评更可靠）。                              │
└──────────────────────────────────────────────────────────────────────────┘
            ▲
            │  每个任务通过  tasks/<task>/prompts/*.md + run.py 接入
┌───────────┴─────────────────────────────────────────────────────────────┐
│  tasks/project-articles/   ← 首个内置任务（参考实现）                       │
│  tasks/<your-task>/        ← 你的新任务；引擎保持不变                       │
└──────────────────────────────────────────────────────────────────────────┘
```

引擎在**所有任务间完全一致**。每个任务变化的是：
- 三份系统提示词（`planner.md` / `specialist.md` / `evaluator.md`），
- 负责把智能体组接入任务专属输入/输出、并承载核查逻辑的 `run.py`。

## 项目结构

```
crewai-pse/
├── src/crewai_pse/           # 核心框架（任务无关）
│   ├── __init__.py           # 公开 API: create_crew()
│   ├── agents.py             # Planner / Specialist / Evaluator + RetryLLM（指数退避）
│   ├── config.py             # 环境变量 / .env 配置
│   ├── prompts.py            # 提示词加载器 → tasks/<task>/prompts/<name>.md
│   └── tools.py              # read_file（沙箱限制）+ run_bash 工具
├── tasks/                    # ← 扩展点：每个任务一个文件夹
│   └── project-articles/     # 任务 1（参考）：生成技术文章
│       ├── run.py            # 任务流水线（生成 + 核查 + 翻译）
│       ├── publish.py        # 通过 wordpress-tools 发布到 WordPress
│       ├── archive.py        # 归档文章到 wordpress-tools/articles/
│       ├── projects.json     # 任务配置（已 gitignore）
│       ├── projects.json.example
│       └── prompts/
│           ├── planner.md
│           ├── specialist.md
│           └── evaluator.md
├── pyproject.toml
├── Makefile
└── .env.example
```

## 如何添加一个新任务

因为引擎是任务无关的，你**永远不需要改 `src/`**。要新增一个名为 `my-task` 的任务：

**1. 创建提示词文件夹** —— 至少 `planner.md` 和 `specialist.md`（evaluator 可选）：

```
tasks/my-task/prompts/planner.md
tasks/my-task/prompts/specialist.md
tasks/my-task/prompts/evaluator.md   # 可选
```

当你把 `task="my-task"` 传给 `create_crew` / `create_planner` / `create_specialist` 时，加载器会自动解析 `tasks/my-task/prompts/<name>.md`。

**2. 编写 `tasks/my-task/run.py`** —— 它负责一切任务专属逻辑：

```python
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent.parent / "src"))
from crewai_pse import create_crew

def main():
    crew = create_crew(task="my-task")   # 加载 tasks/my-task/prompts/*.md
    # ... 用你自己的输入构造 CrewAI Task(...) ...
    # ... 读取输入、运行 crew.kickoff()、保存输出 ...
    # ... 可选：程序化核查 + 修正循环 ...

if __name__ == "__main__":
    main()
```

在提示词里使用 `tools.read_file` / `tools.run_bash`（均限定在 `PSE_ROOT` 沙箱内）。像 `project-articles` 那样在 `run.py` 里加一道程序化核查+修正循环——它比 LLM 自评更可靠。

**3. （可选）加一个 Makefile 目标** —— 沿用既有模式：

```makefile
my-task: ## 运行 my-task 用法: make my-task [P=...] [FLAGS=...]
	$(PY) tasks/my-task/run.py $(P) $(FLAGS)
```

**4. （可选）加任务配置** —— 例如 `tasks/my-task/config.json`，由你的 `run.py` 读取。

就这样。核心引擎、智能体角色、重试逻辑与沙箱全部原样复用。

## 任务

### `project-articles`（参考任务）

把一个项目的源码转化为**中英文双语技术文章**，并通过 `wordpress-tools` 发布到 WordPress 站点（如 `erishen.cn`）。它与同级的 `autogen-pse` 框架目标一致，但用 CrewAI 的 `Sequential` 流程替代了群聊编排。

整条流水线分**三个独立步骤**。按项目决定，*写 / 发 / 归档* 各自是独立的 Makefile 目标，**绝不合并成一条命令**：

```
┌──────────────────────────────────────────────────────────────────────────┐
│  步骤 1 — 写   ( make articles  ·  run.py )                               │
│   Planner ──▶ Specialist ──▶ (程序化核查 + 自动修正 ≤3次) ──▶ 中文文章      │
│   (提纲       (分批写作+合并)       │                            │          │
│    +文件分批)               ├─ ✅ 通过 ─▶ 翻译中文→英文 ─▶ 保存    │          │
│                                    │                  中英文双版  │          │
│                                    └─ 🔄 未过 ─▶ LLM 修正 ─▶ 重新核查 │          │
└──────────────────────────────────────────────────────────────────────────┘
        │  (可选： run.py --publish 会串起步骤 2)
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  步骤 2 — 发布   ( make publish  ·  publish.py )   [独立步骤]              │
│   wordpress-tools ( npm run write:prod )：中文=文章(post)，英文=页面(page) │
│   → 添加跨语言链接 → 更新链接页（WP REST API）                             │
│   → 把链接与 wp_id 回写到 projects.json                                   │
└──────────────────────────────────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  步骤 3 — 归档   ( make archive  ·  archive.py )   [独立步骤]              │
│   把  ARTICLES_DIR/{zh,en}  →  WP_TOOLS_DIR/articles/{zh,en}/  移动        │
└──────────────────────────────────────────────────────────────────────────┘
```

"写"这一步内部又分三个子阶段：

1. **CrewAI 阶段** — Planner 通过沙箱化 `read_file` 读取源码，产出提纲与文件分批；Specialist 将其展开为完整中文文章（含 Front Matter 与 GitHub 源码导航表）。
2. **程序化核查** — 从文章提取所有代码引用，与真实源码比对（文件存在性、符号 grep、路径校验），再加一道"禁用夸大词"检查。发现虚构内容则触发 LLM 自动修正（最多 3 次重试）；最后还有一道确定性兜底，不依赖 LLM 直接删除顽固引用。
3. **翻译阶段** — 通过 LLM 将中文文章译为英文（保留代码、路径、类名/函数名；slug 追加 `-en` 后缀），中英文双版本均落盘。

#### 配置

复制 `.env.example` 为 `.env` 并填写实际值：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API 密钥（兼容 OpenAI 接口，当前为 DeepSeek） |
| `OPENAI_BASE_URL` | ✅ | LLM API 地址 |
| `OPENAI_MODEL` | ✅ | 模型名称；使用自定义 base_url 时 CrewAI 要求加 `openai/` 前缀（如 `openai/deepseek-chat`） |
| `PSE_ROOT` | ✅ | 工作区根目录 — `source_dir` 的基准路径；也是 `read_file` 的沙箱边界 |
| `ARTICLES_DIR` | ✅ | 生成文章的输出目录（`{zh,en}/`） |
| `WP_TOOLS_DIR` | ✅ | `wordpress-tools` 目录路径（用于发布/归档） |
| `PSE_MAX_RETRIES` | | 核查修正最大重试次数（默认 `3`） |
| `AGNES_KEY` | | 备选：Agnes 免费模型 API Key |
| `AGNES_BASE_URL` | | 备选：Agnes 免费模型 API 地址 |
| `WP_API_URL` | ✅* | WordPress REST API 地址，如 `https://your-site.com/wp-json/wp/v2` |
| `WP_USERNAME` | ✅* | WordPress 用户名（用于链接页 Basic Auth） |
| `WP_APP_PASSWORD` | ✅* | WordPress 应用密码 |
| `LINKS_PAGE_ID` | ✅* | 已发布文章列表页的 ID |
| `DEFAULT_PROJECT` | | 省略 `P=` 时 `make` 目标使用的默认项目 |

\* 仅**发布**步骤需要。

**项目配置** —— 从模板创建 `tasks/project-articles/projects.json`：

```bash
cp tasks/project-articles/projects.json.example tasks/project-articles/projects.json
```

```json
{
  "my-project": {
    "repo": "your-org/my-project",
    "desc": "项目一句话描述",
    "highlights": "核心技术亮点",
    "source_dir": "frameworks/my-project"
  }
}
```

| 字段 | 说明 |
|---|---|
| `repo` | GitHub 仓库，格式 `owner/repo` |
| `desc` | 项目简述，用于文章生成 |
| `highlights` | 技术亮点，引导文章聚焦方向 |
| `source_dir` | 源码路径（相对于 `PSE_ROOT`） |

`published` 字段在发布后自动生成，无需手动编辑。

#### 使用

```bash
# 步骤 1 — 写（省略 P= 则使用 .env 中的 DEFAULT_PROJECT）
make articles P=my-project
make articles-agnes P=my-project         # 改用免费的 Agnes 模型
make translate P=my-project              # 仅把已有的中文文章翻译成英文
make translate-agnes P=my-project

# 步骤 2 — 发布（独立步骤）
make publish P=my-project                 # 发布到线上
make publish P=my-project FLAGS=--local  # 发布到本地 WordPress

# 步骤 3 — 归档（独立步骤）
make archive P=my-project
```

也可以直接运行：

```bash
uv run python tasks/project-articles/run.py my-project
uv run python tasks/project-articles/run.py my-project --publish    # 同时串起步骤 2
uv run python tasks/project-articles/run.py my-project --translate  # 仅翻译
```

> `--publish` 会从 `run.py` 内部串起步骤 2，能跑通；但**推荐**做法是把三步拆成独立的 `make` 目标，这样能在文章上线前先审阅内容与程序化核查结果。

#### 安装

```bash
uv sync            # 或：make install
```

## 关键设计决策

**用程序化核查而非纯 LLM 评估。** Evaluator 智能体在 `agents.py` 中为架构完整性而存在，但实际核查在 `run.py` 中通过正则提取代码引用 + 文件系统 grep 完成。这比让 LLM 评判自己的产出更可靠——确定性检查能抓住 LLM 可能"放行"的虚构函数名、不存在的文件路径和编造的 API 用法。

**修正循环独立于 Evaluator。** 修正循环使用独立的 LLM 客户端直接调用，不走 CrewAI 框架。这样避免每次修正都重跑整个 Agent 流水线带来的开销，也能精确控制修正提示词——指示 LLM **删除**虚构内容，而不是创造性地替换。

**三步保持分离。** 写 / 发 / 归档 是各自独立的 Makefile 目标。这样你能在任何内容推上线前先审阅生成文章（及其程序化核查结果），并独立重跑任意单步。

**沙箱化文件访问。** `read_file` 工具强制路径边界——Agent 只能读取 `PSE_ROOT` 下的文件，防止访问项目范围外的敏感文件。

**为什么 CrewAI 贵。** 每个角色（Planner、Specialist，外加修正循环的 LLM 调用）都消耗 token。单篇文章运行通常 ¥0.05–0.10，是直接两段式 API 的 6–12 倍。这是多智能体结构的代价；当成本比编排更重要时，用 `autogen-pse`。

## 与同级框架的关系

四者都共享 **PSE 角色模型** 与 **核查→修正循环**，区别在于编排层：

| | `autogen-pse` | `crewai-pse` | `langgraph-pse` | `llamaindex-pse` |
|---|---|---|---|---|
| 编排方式 | 两段式直接 API（建任务 → 写作 → grep 核查 → 修正） | **CrewAI `Sequential`**（Planner → Specialist）+ 程序化核查 | LangGraph 状态图 + verify-retry 重试 | LlamaIndex `Workflow` + `@step` + Event |
| 任务模型 | 任务专属脚本 | **任务无关引擎 + `tasks/` 文件夹** | 任务无关引擎 + `tasks/` 文件夹 | 任务无关引擎 + `tasks/` 文件夹 |
| RAG | 可选 | — | — | **内置**（`retriever`，源头接地） |
| 单次成本 | 约 ¥0.01 | 约 ¥0.05–0.10 | 零（确定性）/ 便宜（`--llm`） | 取决于 provider |
| 实际用途 | 资产数据 → 周期性分析建议 | **项目代码 → 中英文章 → WordPress** | 结构化数据质量 QA + 周期复盘 | 文档定制（RAG 接地） |
| 适用场景 | 廉价、高频的草稿 | 需要更丰富多智能体编排时 | 需要显式状态控制 + 防幻觉闸门的流程 | RAG 接地生成 |

## 许可证

MIT

---

## 相关文章

- 中文: [CrewAI PSE：程序化校验](https://erishen.cn/crewai-pse-programmatic-verification/)
- English: [CrewAI PSE: Programmatic Verification](https://erishen.cn/crewai-pse-programmatic-verification-en/)