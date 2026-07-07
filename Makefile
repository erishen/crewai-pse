.PHONY: install lint clean articles articles-agnes publish archive

PY := uv run python
P ?= $(shell grep '^DEFAULT_PROJECT=' .env 2>/dev/null | cut -d= -f2)

install:
	uv sync

lint:
	uv run ruff check src/ tasks/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .crewai/ dist/ *.egg-info

articles: ## CrewAI 撰写项目技术文章 用法: make articles [P=autogen-pse] [FLAGS=--publish]
	$(PY) tasks/project-articles/run.py $(P) $(FLAGS)

articles-agnes: ## CrewAI 撰写项目技术文章（Agnes 免费）
	OPENAI_API_KEY=$(shell grep '^AGNES_KEY=' .env | cut -d= -f2) \
	OPENAI_MODEL=openai/agnes-2.0-flash \
	OPENAI_BASE_URL=$(shell grep '^AGNES_BASE_URL=' .env | cut -d= -f2) \
	$(PY) tasks/project-articles/run.py $(P) $(FLAGS)

publish: ## 发布文章到线上 用法: make publish [P=autogen-pse]
	$(PY) tasks/project-articles/publish.py $(P) $(FLAGS)

archive: ## 归档文章到 wordpress-tools 用法: make archive [P=autogen-pse]
	$(PY) tasks/project-articles/archive.py $(P)
