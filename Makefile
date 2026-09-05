.PHONY: install lint test clean articles articles-free articles-paid publish archive translate translate-free translate-paid validate discover sync-links check-links

# 出网代理：统一从 .env 的 WP_PROXY 读取（换端口只改 .env 一处）。
# 原先 shell 里是 7890 已失效，会导致发布/调外部 API 时 ECONNREFUSED 127.0.0.1:7890；
# 这里导出正确的端口，覆盖 shell 里的旧值，axios(node)/uv 均会沿用。
export WP_PROXY := $(shell grep '^WP_PROXY=' .env 2>/dev/null | cut -d= -f2)
export HTTP_PROXY := $(WP_PROXY)
export HTTPS_PROXY := $(WP_PROXY)

PY := uv run python
P ?= $(shell grep '^DEFAULT_PROJECT=' .env 2>/dev/null | cut -d= -f2)
# 归档后顺带在各平台目录重建该文章的副本（staging，等下次发平台时用最新源）
NODE ?= node
WP_TOOLS_DIR ?= $(shell grep '^WP_TOOLS_DIR=' .env 2>/dev/null | cut -d= -f2)
# 项目 key(rag-task-service) -> zh 文件名(rag_task_service-zh)
SLUG_ZH = $(shell echo "$(P)" | tr '-' '_')-zh

install:
	uv sync

lint:
	uv run ruff check src/ tasks/

test: ## 运行 pipeline/ 单元测试（tasks/project-articles）
	cd tasks/project-articles && $(PY) -m pytest -q

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .crewai/ dist/ *.egg-info tasks/project-articles/.src_cache

# 默认用免费 free 模型（成本敏感模式）
articles articles-free: ## CrewAI 撰写项目技术文章（默认 free 免费）用法: make articles [P=llamaindex-pse] [FLAGS=--publish]
	OPENAI_API_KEY=$(shell grep '^FREE_KEY=' .env | cut -d= -f2) \
	OPENAI_MODEL=openai/free-2.0-flash \
	OPENAI_BASE_URL=$(shell grep '^FREE_BASE_URL=' .env | cut -d= -f2) \
	$(PY) tasks/project-articles/run.py $(P) $(FLAGS)

articles-paid: ## CrewAI 撰写项目技术文章（付费 deepseek，质量优先）用法: make articles-paid [P=...] [FLAGS=...]
	$(PY) tasks/project-articles/run.py $(P) $(FLAGS)

# 默认用免费 free 模型（成本敏感模式）
translate translate-free: ## 仅翻译已有中文文章（默认 free 免费）用法: make translate [P=llamaindex-pse]
	OPENAI_API_KEY=$(shell grep '^FREE_KEY=' .env | cut -d= -f2) \
	OPENAI_MODEL=openai/free-2.0-flash \
	OPENAI_BASE_URL=$(shell grep '^FREE_BASE_URL=' .env | cut -d= -f2) \
	$(PY) tasks/project-articles/run.py $(P) --translate

translate-paid: ## 仅翻译（付费 deepseek，质量优先）用法: make translate-paid [P=...]
	$(PY) tasks/project-articles/run.py $(P) --translate

publish: ## 发布文章到线上 用法: make publish [P=llamaindex-pse]
	$(PY) tasks/project-articles/publish.py $(P) $(FLAGS)

archive: ## 归档文章到 wordpress-tools，并重建 juejin/segmentfault/wechat 副本 + 关键词索引 用法: make archive [P=rag-task-service]
	$(PY) tasks/project-articles/archive.py $(P)
	@if [ -n "$(WP_TOOLS_DIR)" ] && [ -d "$(WP_TOOLS_DIR)" ]; then \
	  cd "$(WP_TOOLS_DIR)" && \
	  $(NODE) src/publish/build-juejin-from-zh.mjs $(SLUG_ZH).md && \
	  $(NODE) src/publish/build-segmentfault.mjs $(SLUG_ZH) && \
	  $(NODE) src/wechat/wechat-convert.js $(SLUG_ZH).md ; \
	  python3 tools/gen_keywords_index.py ; \
	else \
	  echo "⚠️ 未设置/不存在 WP_TOOLS_DIR，跳过 juejin/segmentfault/wechat 副本重建与关键词索引更新"; \
	fi
	@echo "🧹 清理 $(P) 的源码镜像缓存"
	@rm -rf tasks/project-articles/.src_cache/$(P) && echo "  已删除 .src_cache/$(P)/" || echo "  .src_cache/$(P)/ 不存在，跳过"

validate: ## 发布前校验文章正确性 用法: make validate [P=rag-platform]
	$(PY) tasks/project-articles/validate.py $(P)

discover: ## 扫描大项目下有 github remote 的子项目，建议加入 projects.json 用法: make discover [FLAGS=--add]
	$(PY) tasks/project-articles/discover_projects.py $(FLAGS)

# README 文章回链：projects-published.json 是唯一真相源。
# sync 写入各项目本地 README（幂等），check 只读校验（exit!=0 可直接挂 CI/pre-commit）。
# 发布流水线的最后一步应是 make check-links。
# 用 python3 而非 $(PY)/uv run：两脚本纯 stdlib 零依赖；uv 启动会探测写
# ~/.cache/uv，在 resolve-studio harness 的 Seatbelt 沙箱（仅可写 cwd+tmp）
# 下被拒，且 uv 冷启动可能撞 shell 工具 15s 超时。
sync-links: ## 同步 erishen.cn 文章回链到各项目 README 用法: make sync-links [FLAGS=--dry]
	python3 tasks/project-articles/sync_readme_backlinks.py $(FLAGS)

check-links: ## 只读校验各项目 README 回链完整性（29 项全绿才 exit 0）
	python3 tasks/project-articles/check_readme_links.py
