.PHONY: install lint test articles articles-agnes

PY := uv run python

install:
	uv sync

lint:
	uv run ruff check src/ tasks/

test:
	uv run pytest tests/ -v

articles: ## CrewAI 撰写项目技术文章（中文 → 自动翻译英文）用法: make articles P=autogen-pse
	$(PY) tasks/project-articles/run.py $(P)

articles-agnes: ## CrewAI 撰写项目技术文章（Agnes 免费）
	OPENAI_API_KEY=$(shell grep '^AGNES_KEY=' .env | cut -d= -f2) \
	OPENAI_MODEL=openai/agnes-2.0-flash \
	OPENAI_BASE_URL=https://apihub.agnes-ai.com/v1 \
	$(PY) tasks/project-articles/run.py $(P)
