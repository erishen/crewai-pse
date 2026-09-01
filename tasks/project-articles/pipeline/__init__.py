"""project-articles 文章生成流水线。

原 2300 行的 run.py 按职责拆分为以下模块：

- `bootstrap`   环境引导（sys.path / .env / crewai 依赖），必须最先导入
- `config`      全局路径、常量、正则、禁忌词、叙事风格表
- `frontmatter` frontmatter 解析与字段读写（slug/tags/categories/description/excerpt）
- `blocks`      FAQ / TL;DR 区块的归一化、计数与自动生成
- `sanitize`    思维链泄漏净化、计划独白剥离、虚构引用删除、重复章节去重
- `seo`         meta description / excerpt / 同系列内链（SEO 程序化层）
- `verify`      源码 grounding 核查（代码引用 / 环境变量 / 安装命令 / CLI 入口）
- `source`      源码镜像进沙箱、提纲分批解析、项目清单加载
- `styles`      叙事风格选择（最少使用优先轮转）与表述变体
- `prompts`     所有 LLM 提示词构造（集中管理，便于整体调优）
- `translate`   英文翻译阶段（翻译 + token 统计 + 可选发布）

`run.py` 只保留流程编排：Phase 1 提纲 → Phase 2 写作 → 核查循环 → 保存 → 翻译/发布。
"""
