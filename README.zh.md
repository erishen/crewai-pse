<div align="right">
  <a href="README.md">🇬🇧 English</a>
</div>

# CrewAI PSE

基于 [CrewAI](https://www.crewai.com/) 构建的 **Planner-Specialist-Evaluator** 三角色多智能体框架，用于自动化技术文章生成。三个专业化 AI 角色协作，产出经过源码验证的高质量技术文章——中英文双版本。

## 工作原理

```
┌───────────┐     提纲       ┌──────────────┐    文章        ┌────────────┐
│  Planner   │──────────────▶│  Specialist   │──────────────▶│  Evaluator  │
│            │               │               │               │             │
│ • 读源码   │               │ • 撰写初稿    │               │ • 验证引用  │
│ • 列提纲   │               │ • 代码摘录    │               │ • Grep 源码 │
│ • 定策略   │               │ • 完整文章    │               │ • 判定真伪  │
└───────────┘               └──────────────┘               └──────┬──────┘
                                                                  │
                                              ┌───────────────────┘
                                              ▼
                                    ┌──────────────────┐
                                    │   程序化核查      │
                                    │  (正则 + grep)    │
                                    └────────┬─────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                          ✅ 通过       🔄 修正重试     ❌ 失败
                         (保存)        (最多 3 次)     (中止)
                              │              │
                              ▼              ▼
                        ┌──────────────────────┐
                        │   翻译 (中文→英文)    │
                        │   保存双版本          │
                        └──────────────────────┘
```

整个流水线分三个阶段：

1. **CrewAI 阶段** — Planner 读取源码、输出文章提纲；Specialist 展开为完整文章
2. **程序化核查** — 从文章中提取所有代码引用，与实际源码文件比对（文件存在性、符号 grep、路径校验）。发现虚构内容则调用 LLM 自动修正，最多重试 3 次
3. **翻译阶段** — 通过 LLM 将中文文章翻译为英文，保留所有代码示例

## 项目结构

```
crewai-pse/
├── src/crewai_pse/           # 核心框架
│   ├── __init__.py           # 公开 API: create_crew()
│   ├── agents.py             # 三角色 Agent 定义
│   ├── config.py             # 环境变量 / .env 配置
│   ├── prompts.py            # 提示词加载器
│   └── tools.py              # read_file（沙箱限制）+ run_bash 工具
├── tasks/
│   └── project-articles/     # 任务：生成技术文章
│       ├── run.py            # 主流水线入口
│       ├── publish.py        # 通过发布工具发布到 CMS
│       ├── archive.py        # 归档文章到发布工具目录
│       ├── projects.json     # 项目配置（已 gitignore）
│       ├── projects.json.example
│       └── prompts/          # Agent 系统提示词
│           ├── planner.md
│           ├── specialist.md
│           └── evaluator.md
├── pyproject.toml
├── Makefile
└── .env.example
```

## 安装

```bash
# 克隆仓库
git clone <your-repo-url>/crewai-pse.git
cd crewai-pse

# 安装依赖（需要 uv）
make install
# 或直接：
uv sync
```

## 配置

### 环境变量

复制 `.env.example` 为 `.env` 并填写实际值：

```bash
cp .env.example .env
```

| 变量 | 必填 | 说明 |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API 密钥（兼容 OpenAI 接口） |
| `OPENAI_BASE_URL` | ✅ | LLM API 地址 |
| `OPENAI_MODEL` | ✅ | 模型名称（如 `openai/gpt-4o`） |
| `PSE_ROOT` | ✅ | 工作区根目录 — projects.json 中 `source_dir` 的基准路径；也是 `read_file` 的沙箱边界 |
| `ARTICLES_DIR` | ✅ | 生成文章的输出目录 |
| `WP_TOOLS_DIR` | ✅ | 发布工具目录路径（用于 CMS 发布） |
| `PSE_MAX_RETRIES` | | 核查修正最大重试次数（默认 `3`） |
| `AGNES_KEY` | | 备选：Agnes 免费模型 API Key |
| `AGNES_BASE_URL` | | 备选：Agnes 免费模型 API 地址 |

### 项目配置

从模板创建 `tasks/project-articles/projects.json`：

```bash
cp tasks/project-articles/projects.json.example tasks/project-articles/projects.json
```

每个项目条目需要：

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

## 使用

### 生成文章

```bash
# 为指定项目生成文章（省略 P= 则使用 .env 中的 DEFAULT_PROJECT）
make articles P=my-project

# 生成后自动发布
make articles P=my-project FLAGS=--publish

# 使用 Agnes 免费模型
make articles-agnes P=my-project
```

也可以直接运行：

```bash
uv run python tasks/project-articles/run.py my-project
uv run python tasks/project-articles/run.py my-project --publish
```

### 发布到 WordPress

通过发布工具将文章发布到 CMS。默认发布到线上，加 `--local` 发布到本地。

```bash
make publish P=my-project              # 发布到线上
make publish P=my-project FLAGS=--local  # 发布到本地
```

发布成功后，文章链接和 WordPress 文章 ID 会自动回写到 `projects.json`。

### 归档文章

将生成的文章从 PSE 输出目录移动到发布工具的文章目录（`articles/{zh,en}/`）长期保存：

```bash
make archive P=my-project
```

### 代码检查

```bash
make lint
```

## 关键设计决策

**为什么用程序化核查而不是纯 LLM 评估？** Evaluator Agent 在 Crew 中有定义，但实际核查是在 `run.py` 中通过程序完成的——用正则提取代码引用，用文件系统 grep 验证。这比让 LLM 评判自己的输出更可靠：确定性检查能抓住 LLM 可能"放行"的虚构函数名、不存在的文件路径和编造的 API 用法。

**为什么修正循环独立于 Evaluator？** 修正循环使用独立的 OpenAI 客户端直接调用，不走 CrewAI 框架。这样避免了每次修正都要重跑整个 Agent 流水线的开销，同时能精确控制修正提示词——指示 LLM **删除**虚构内容，而不是创造性地替换。

**沙箱化文件访问。** `read_file` 工具强制路径边界——Agent 只能读取 `PSE_ROOT` 下的文件，防止访问项目范围外的敏感文件。

## 许可证

MIT
