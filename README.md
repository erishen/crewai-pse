<div align="right">
  <a href="README.zh.md">🇨🇳 中文</a>
</div>

# CrewAI PSE

A **Planner-Specialist-Evaluator** multi-agent framework built on [CrewAI](https://www.crewai.com/) for automated technical article generation. Three specialized AI agents collaborate to produce high-quality, source-code-verified technical articles about open-source projects — in both Chinese and English.

## How It Works

```
┌───────────┐     outline     ┌──────────────┐    article     ┌────────────┐
│  Planner   │──────────────▶│  Specialist   │──────────────▶│  Evaluator  │
│            │               │               │               │             │
│ • Read src │               │ • Write draft │               │ • Verify    │
│ • Outline  │               │ • Code quotes │               │   references│
│ • Strategy │               │ • Full article│               │ • Grep src  │
└───────────┘               └──────────────┘               └──────┬──────┘
                                                                  │
                                              ┌───────────────────┘
                                              ▼
                                    ┌──────────────────┐
                                    │  Programmatic    │
                                    │  Verification    │
                                    │  (regex + grep)  │
                                    └────────┬─────────┘
                                             │
                              ┌──────────────┼──────────────┐
                              ▼              ▼              ▼
                          ✅ Pass     🔄 Fix & Retry   ❌ Fail
                         (save)      (up to 3x)      (abort)
                              │              │
                              ▼              ▼
                        ┌──────────────────────┐
                        │  Translate (ZH→EN)   │
                        │  Save both versions  │
                        └──────────────────────┘
```

The pipeline runs in three phases:

1. **CrewAI Phase** — Planner reads source code and creates an outline; Specialist expands it into a full article
2. **Programmatic Verification** — Extracts all code references from the article and verifies them against actual source files (file existence, symbol grep, path validation). Fictitious content triggers auto-correction via LLM, up to 3 retries
3. **Translation** — Chinese article is translated to English via LLM, preserving all code examples

## Project Structure

```
crewai-pse/
├── src/crewai_pse/           # Core framework
│   ├── __init__.py           # Public API: create_crew()
│   ├── agents.py             # Agent definitions (Planner, Specialist, Evaluator)
│   ├── config.py             # Settings from environment / .env
│   ├── prompts.py            # Prompt loader (tasks/<task>/prompts/*.md)
│   └── tools.py              # read_file (sandboxed) + run_bash tools
├── tasks/
│   └── project-articles/     # Task: generate technical articles
│       ├── run.py            # Main pipeline entry point
│       ├── publish.py        # Publish to CMS via publishing tools
│       ├── archive.py        # Archive articles to publishing tools directory
│       ├── projects.json     # Project configs (gitignored)
│       ├── projects.json.example
│       └── prompts/          # Agent system prompts
│           ├── planner.md
│           ├── specialist.md
│           └── evaluator.md
├── pyproject.toml
├── Makefile
└── .env.example
```

## Installation

```bash
# Clone the repository
git clone <your-repo-url>/crewai-pse.git
cd crewai-pse

# Install dependencies (requires uv)
make install
# or directly:
uv sync
```

## Configuration

### Environment Variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

| Variable | Required | Description |
|---|---|---|
| `OPENAI_API_KEY` | ✅ | LLM API key (OpenAI-compatible) |
| `OPENAI_BASE_URL` | ✅ | LLM API base URL |
| `OPENAI_MODEL` | ✅ | Model name (e.g. `openai/gpt-4o`) |
| `PSE_ROOT` | ✅ | Workspace root — base path for `source_dir` in projects.json; also sandbox root for `read_file` |
| `ARTICLES_DIR` | ✅ | Output directory for generated articles |
| `WP_TOOLS_DIR` | ✅ | Path to publishing tools directory (for CMS publishing) |
| `PSE_MAX_RETRIES` | | Max verification retry rounds (default: `3`) |
| `AGNES_KEY` | | Alternative: Agnes API key (free model) |
| `AGNES_BASE_URL` | | Alternative: Agnes base URL |

### Project Configuration

Create `tasks/project-articles/projects.json` from the example:

```bash
cp tasks/project-articles/projects.json.example tasks/project-articles/projects.json
```

Each project entry requires:

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

## Usage

### Generate Articles

```bash
# Generate article for a project (uses DEFAULT_PROJECT from .env if omitted)
make articles P=my-project

# Generate and immediately publish
make articles P=my-project FLAGS=--publish

# Use the free Agnes model instead
make articles-agnes P=my-project
```

Or run directly:

```bash
uv run python tasks/project-articles/run.py my-project
uv run python tasks/project-articles/run.py my-project --publish
```

### Publish to WordPress

Publishes articles to your CMS via the publishing tools. Defaults to production; use `--local` for local development.

```bash
make publish P=my-project          # publish to production
make publish P=my-project FLAGS=--local  # publish to local WordPress
```

After successful publishing, the article links and WordPress post IDs are automatically written back to `projects.json`.

### Archive Articles

Moves generated articles from the PSE output directory to the publishing tools articles directory (`articles/{zh,en}/`) for long-term storage:

```bash
make archive P=my-project
```

### Lint

```bash
make lint
```

## Key Design Decisions

**Why programmatic verification instead of pure LLM evaluation?** The Evaluator agent is defined in the crew for architectural completeness, but the actual verification is done programmatically in `run.py` using regex-based code reference extraction and filesystem grep. This is more reliable than asking an LLM to judge its own output — deterministic checks catch hallucinated function names, nonexistent file paths, and fabricated API usage that an LLM might "approve."

**Why a separate fix loop instead of Evaluator-driven fixes?** The correction loop uses a direct OpenAI client call rather than going through the CrewAI framework. This avoids the overhead of re-running the full agent pipeline for each fix iteration, and gives precise control over the fix prompt (instructing the LLM to *delete* fictitious content rather than creatively replace it).

**Sandboxed file access.** The `read_file` tool enforces a path boundary — agents can only read files under `PSE_ROOT`. This prevents the agents from accessing sensitive files outside the project scope.

## License

MIT
