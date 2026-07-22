.PHONY: install lint clean articles articles-agnes articles-paid publish archive translate translate-agnes translate-paid

PY := uv run python
P ?= $(shell grep '^DEFAULT_PROJECT=' .env 2>/dev/null | cut -d= -f2)

install:
	uv sync

lint:
	uv run ruff check src/ tasks/

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .crewai/ dist/ *.egg-info

# 默认用免费 Agnes 模型（成本敏感模式）
articles articles-agnes: ## CrewAI 撰写项目技术文章（默认 Agnes 免费）用法: make articles [P=llamaindex-pse] [FLAGS=--publish]
	OPENAI_API_KEY=$(shell grep '^AGNES_KEY=' .env | cut -d= -f2) \
	OPENAI_MODEL=openai/agnes-2.0-flash \
	OPENAI_BASE_URL=$(shell grep '^AGNES_BASE_URL=' .env | cut -d= -f2) \
	$(PY) tasks/project-articles/run.py $(P) $(FLAGS)

articles-paid: ## CrewAI 撰写项目技术文章（付费 deepseek，质量优先）用法: make articles-paid [P=...] [FLAGS=...]
	$(PY) tasks/project-articles/run.py $(P) $(FLAGS)

# 默认用免费 Agnes 模型（成本敏感模式）
translate translate-agnes: ## 仅翻译已有中文文章（默认 Agnes 免费）用法: make translate [P=llamaindex-pse]
	OPENAI_API_KEY=$(shell grep '^AGNES_KEY=' .env | cut -d= -f2) \
	OPENAI_MODEL=openai/agnes-2.0-flash \
	OPENAI_BASE_URL=$(shell grep '^AGNES_BASE_URL=' .env | cut -d= -f2) \
	$(PY) tasks/project-articles/run.py $(P) --translate

translate-paid: ## 仅翻译（付费 deepseek，质量优先）用法: make translate-paid [P=...]
	$(PY) tasks/project-articles/run.py $(P) --translate

publish: ## 发布文章到线上 用法: make publish [P=llamaindex-pse]
	$(PY) tasks/project-articles/publish.py $(P) $(FLAGS)

archive: ## 归档文章到 wordpress-tools 用法: make archive [P=llamaindex-pse]
	$(PY) tasks/project-articles/archive.py $(P)
