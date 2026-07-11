<div align="right">
  <a href="README.zh.md">🇨🇳 中文</a>
</div>

# CrewAI PSE

A **task-agnostic** [CrewAI](https://www.crewai.com/)-powered **Planner–Specialist–Evaluator (PSE)** multi-agent framework. The engine assembles a PSE crew, loads each role's system prompt from a task directory, and runs a generate→verify→fix loop. **Add any task by dropping a folder under `tasks/`** — the core never changes.

The repository currently ships **one task**: `project-articles` (source code → bilingual 中文/English article → publish to WordPress). It is documented below as a worked example; the framework is not limited to it.

> [!NOTE]
> **Cost — read before running.** A full CrewAI run of the `project-articles` task costs roughly **¥0.05–0.10 per article** (about 6–12× a direct API call) because every role drives an LLM. Token usage is heavy, so **do not auto-execute** on every commit. For cheap drafts prefer `autogen-pse` (two-stage direct API, ~¥0.01/article). `run.py` prints actual token usage + a cost estimate at the end of every run.

## How It Works

The framework provides a reusable **PSE engine**; each *task* supplies its own prompts, orchestration, and I/O.

```
┌──────────────────────────────────────────────────────────────────────────┐
│  PSE ENGINE  (src/crewai_pse — task-agnostic)                              │
│                                                                            │
│   create_crew(task) ──▶ Planner ──▶ Specialist ──▶ (verify + fix ≤N)       │
│                          │            │                 │                  │
│                          │            │           programmatic verify      │
│                          │            │            (grep / schema / …)     │
│                          │            │                 ├─ ✅ pass          │
│                          │            │                 └─ 🔄 fail ─▶ fix  │
│                          │            ▼                                    │
│                    task.run() owns the I/O: reads inputs, saves outputs   │
│                                                                            │
│   ⚙ Evaluator agent is DEFINED but EXCLUDED from the Sequential flow;     │
│     verification is done programmatically (more reliable than LLM judge).  │
└──────────────────────────────────────────────────────────────────────────┘
            ▲
            │  each task plugs in via  tasks/<task>/prompts/*.md + run.py
┌───────────┴─────────────────────────────────────────────────────────────┐
│  tasks/project-articles/   ← first shipped task (reference implementation) │
│  tasks/<your-task>/        ← add your own; the engine stays untouched     │
└──────────────────────────────────────────────────────────────────────────┘
```

The engine is **identical across tasks**. What changes per task:
- the three system prompts (`planner.md` / `specialist.md` / `evaluator.md`),
- the `run.py` that wires the crew into task-specific inputs/outputs and the verify logic.

## Project Structure

```
crewai-pse/
├── src/crewai_pse/           # Core framework (task-agnostic)
│   ├── __init__.py           # Public API: create_crew()
│   ├── agents.py             # Planner / Specialist / Evaluator + RetryLLM (exp. backoff)
│   ├── config.py             # Settings from environment / .env
│   ├── prompts.py            # Prompt loader → tasks/<task>/prompts/<name>.md
│   └── tools.py              # read_file (sandboxed) + run_bash tools
├── tasks/                    # ← extension point: one folder per task
│   └── project-articles/     # Task 1 (reference): generate technical articles
│       ├── run.py            # Task pipeline (gen + verify + translate)
│       ├── publish.py        # Publish to WordPress via wordpress-tools
│       ├── archive.py        # Archive articles into wordpress-tools/articles/
│       ├── projects.json     # Task config (gitignored)
│       ├── projects.json.example
│       └── prompts/
│           ├── planner.md
│           ├── specialist.md
│           └── evaluator.md
├── pyproject.toml
├── Makefile
└── .env.example
```

## Adding a New Task

Because the engine is task-agnostic, you **never edit `src/`**. To add a task named `my-task`:

**1. Create the prompt folder** — at minimum `planner.md` and `specialist.md` (evaluator optional):

```
tasks/my-task/prompts/planner.md
tasks/my-task/prompts/specialist.md
tasks/my-task/prompts/evaluator.md   # optional
```

The loader resolves `tasks/my-task/prompts/<name>.md` automatically when you pass `task="my-task"` to `create_crew` / `create_planner` / `create_specialist`.

**2. Write `tasks/my-task/run.py`** — it owns everything task-specific:

```python
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE.parent.parent / "src"))
from crewai_pse import create_crew

def main():
    crew = create_crew(task="my-task")   # loads tasks/my-task/prompts/*.md
    # ... build CrewAI Task(s) with your own inputs ...
    # ... read inputs, run crew.kickoff(), save outputs ...
    # ... optional programmatic verify + fix loop ...

if __name__ == "__main__":
    main()
```

Use `tools.read_file` / `tools.run_bash` (sandboxed under `PSE_ROOT`) inside agent prompts. Add a programmatic verify+fix loop in `run.py` the same way `project-articles` does — it is more reliable than LLM self-judgement.

**3. (Optional) Add a Makefile target** — following the existing pattern:

```makefile
my-task: ## Run my-task 用法: make my-task [P=...] [FLAGS=...]
	$(PY) tasks/my-task/run.py $(P) $(FLAGS)
```

**4. (Optional) Add task config** — e.g. `tasks/my-task/config.json`, read by your `run.py`.

That's it. The core engine, agent roles, retry logic, and sandbox are reused as-is.

## Tasks

### `project-articles` (reference task)

Turns a project's source code into a **bilingual (中文 / English) technical article** and publishes it to a WordPress site (e.g. `erishen.cn`) via `wordpress-tools`. It mirrors the sibling `autogen-pse` framework but uses CrewAI's `Sequential` process instead of a group-chat.

The pipeline runs in **three separate steps**. Per project decision, *write / publish / archive* are kept as distinct Makefile targets and are **never merged into a single command**:

```
┌──────────────────────────────────────────────────────────────────────────┐
│  STEP 1 — WRITE   ( make articles  ·  run.py )                            │
│   Planner ──▶ Specialist ──▶ (programmatic verify + auto-fix ≤3) ──▶ ZH    │
│   (outline   (draft+merge)        │                          article      │
│    + file batches)                ├─ ✅ pass ─▶ translate ZH→EN ─▶ save    │
│                                    │                  both versions        │
│                                    └─ 🔄 fail ─▶ LLM fix ─▶ re-verify      │
└──────────────────────────────────────────────────────────────────────────┘
        │  (optional:  run.py --publish  chains Step 2)
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  STEP 2 — PUBLISH   ( make publish  ·  publish.py )   [separate step]      │
│   wordpress-tools ( npm run write:prod ): zh = post, en = page            │
│   → add cross-language links → update links page (WP REST API)            │
│   → write links + wp_id back to projects.json                             │
└──────────────────────────────────────────────────────────────────────────┘
        ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  STEP 3 — ARCHIVE   ( make archive  ·  archive.py )   [separate step]     │
│   move  ARTICLES_DIR/{zh,en}  →  WP_TOOLS_DIR/articles/{zh,en}/           │
└──────────────────────────────────────────────────────────────────────────┘
```

The write step itself has three internal phases:

1. **CrewAI phase** — Planner reads source code (via the sandboxed `read_file`) and produces an outline + file batching; Specialist expands it into a full Chinese article (with Front Matter and a GitHub source-navigation table).
2. **Programmatic verification** — Extracts every code reference from the article and checks it against the actual source (file existence, symbol grep, path validation) plus an exaggeration-term check. Fictitious content triggers an LLM auto-fix (up to 3 retries); a final deterministic fallback strips stubborn refs without LLM.
3. **Translation** — The Chinese article is translated to English via LLM (code, paths, and class/function names preserved; slug gets a `-en` suffix), and both versions are saved.

#### Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API key (OpenAI-compatible — currently DeepSeek) |
| `OPENAI_BASE_URL` | ✅ | LLM API base URL |
| `OPENAI_MODEL` | ✅ | Model name; CrewAI requires the `openai/` prefix when using a custom base URL (e.g. `openai/deepseek-chat`) |
| `PSE_ROOT` | ✅ | Workspace root — base path for `source_dir`; also the `read_file` sandbox boundary |
| `ARTICLES_DIR` | ✅ | Output directory for generated articles (`{zh,en}/`) |
| `WP_TOOLS_DIR` | ✅ | Path to the `wordpress-tools` directory (for publishing/archiving) |
| `PSE_MAX_RETRIES` | | Max verification retry rounds (default: `3`) |
| `AGNES_KEY` | | Alternative: Agnes API key (free model) |
| `AGNES_BASE_URL` | | Alternative: Agnes base URL |
| `WP_API_URL` | ✅* | WordPress REST API base, e.g. `https://your-site.com/wp-json/wp/v2` |
| `WP_USERNAME` | ✅* | WordPress username (for the links page via Basic Auth) |
| `WP_APP_PASSWORD` | ✅* | WordPress application password |
| `LINKS_PAGE_ID` | ✅* | ID of the page that lists published articles |
| `DEFAULT_PROJECT` | | Project key used when `P=` is omitted in `make` targets |

\* Required only for the **publish** step.

**Project config** — create `tasks/project-articles/projects.json` from the example:

```bash
cp tasks/project-articles/projects.json.example tasks/project-articles/projects.json
```

```json
{
  "my-project": {
    "repo": "your-org/my-project",
    "desc": "One-line description of the project",
    "highlights": "Key technical highlights",
    "source_dir": "frameworks/my-project"
  }
}
```

| Field | Description |
|---|---|
| `repo` | GitHub repository in `owner/repo` format |
| `desc` | Short description used in the article |
| `highlights` | Technical highlights to focus on |
| `source_dir` | Path to source code (relative to `PSE_ROOT`) |

The `published` field is automatically populated after publishing — do not edit manually.

#### Usage

```bash
# Step 1 — Write (uses DEFAULT_PROJECT from .env if P= omitted)
make articles P=my-project
make articles-agnes P=my-project         # use the free Agnes model
make translate P=my-project              # translate existing ZH → EN only
make translate-agnes P=my-project

# Step 2 — Publish (separate step)
make publish P=my-project                 # to production
make publish P=my-project FLAGS=--local   # to local WordPress

# Step 3 — Archive (separate step)
make archive P=my-project
```

Or run directly:

```bash
uv run python tasks/project-articles/run.py my-project
uv run python tasks/project-articles/run.py my-project --publish    # also chain Step 2
uv run python tasks/project-articles/run.py my-project --translate  # translate only
```

> The `--publish` flag chains Step 2 from inside `run.py`. It works, but the **recommended** practice is to run the three steps as separate `make` targets so you can review the article before it goes live.

#### Installation

```bash
uv sync            # or: make install
```

## Key Design Decisions

**Programmatic verification instead of pure LLM evaluation.** The Evaluator agent exists in `agents.py` for architectural completeness, but the actual verification runs in `run.py` via regex-based code-reference extraction and filesystem grep. This is more reliable than asking an LLM to judge its own output — deterministic checks catch hallucinated function names, nonexistent file paths, and fabricated API usage that an LLM might "approve."

**A separate fix loop, not Evaluator-driven fixes.** The correction loop uses a direct LLM client call rather than re-running the CrewAI pipeline. This avoids the overhead of spinning up the full agent flow for each fix iteration and gives precise control over the fix prompt (instructing the LLM to *delete* fictitious content rather than creatively replace it).

**Three steps kept separate.** Write / publish / archive are distinct Makefile targets. This lets you review the generated article (and its programmatic verification result) before anything is pushed to your site, and re-run any single step independently.

**Sandboxed file access.** The `read_file` tool enforces a path boundary — agents can only read files under `PSE_ROOT`. This prevents the agents from reaching sensitive files outside the project scope.

**Why CrewAI is expensive.** Every role (Planner, Specialist, plus the fix-loop LLM call) consumes tokens. A single article run typically costs ¥0.05–0.10 — 6–12× a direct two-stage API call. That is the trade-off for the multi-agent structure; use `autogen-pse` when cost matters more than the agent choreography.

## Relation to Sibling Frameworks

All four share the **PSE role model** and a **verify→fix loop**, but differ in orchestration:

| | `autogen-pse` | `crewai-pse` | `langgraph-pse` | `llamaindex-pse` |
|---|---|---|---|---|
| Orchestration | Direct two-stage API (build → write → grep-check → fix) | **CrewAI `Sequential`** (Planner → Specialist) + programmatic verify | LangGraph state graph + verify-retry | LlamaIndex `Workflow` + `@step` + Event |
| Task model | Task-specific script | **Task-agnostic engine + `tasks/` folder** | Task-agnostic engine + `tasks/` folder | Task-agnostic engine + `tasks/` folder |
| RAG | optional | — | — | **built-in** (`retriever`, source-grounded) |
| Cost / run | ~¥0.01 | ~¥0.05–0.10 | zero (deterministic) / cheap (`--llm`) | depends on provider |
| Reference use | asset-lens → next-week investment advice | **project code → bilingual article → WordPress** | CRM data-quality QA + weekly relationship review | résumé tailoring (RAG) |
| Best for | Cheap, frequent drafts | Richer multi-agent publishing | Explicit state control + anti-hallucination gates | RAG-grounded generation |

## License

MIT
