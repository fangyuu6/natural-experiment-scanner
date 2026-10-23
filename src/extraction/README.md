# extraction

核心分析包，实现从法案文本到自然实验可行性评估的完整 LLM 提取与后处理管道（含预过滤、提示生成、评分、混杂检测等）。

## 文件

- `__init__.py` — 包初始化，导出主入口函数 `extract_rd_did`
- `confound_detector.py` — 混杂政策检测器：维护联邦与各州雇员规模门槛知识库，交叉匹配法案的运行变量与截断值以识别重叠政策
- `cross_matcher.py` — 跨法案匹配引擎：对已分析的法案库进行事后审计，识别门槛混杂和同族跨州 DID 机会
- `dataset_info.py` — 数据源信息库：提供 CPS、ACS 等常用经济学数据集的变量列表、访问方式和适用场景
- `family_normalizer.py` — 政策族标签规范化器：通过拼写修正、别名映射、后缀合并三步确定性流程防止标签碎片化
- `feasibility_scorer.py` — 研究可行性评分系统（1-5 分制）：基于加权复合分数和规则调整计算最终可行性分数、标签与证据层级
- `llm_analyzer.py` — 主 LLM 分析模块：对每条法案调用大模型提取 RD/DID 设计信息，并整合预过滤、混杂检测、评分等后处理步骤。内置多 API key round-robin 轮转（`.env` 中 `OPENAI_API_KEYS=key1,key2,...`），所有 LLM 调用自动均衡负载
- `pre_filter.py` — 标题预过滤器：通过关键词和正则匹配在调用 LLM 前跳过明显的纪念性/非实质性法案
- `pre_hints.py` — 设计提示生成器：扫描法案文本中的模式（如复审门槛），生成提示前缀引导 LLM 评估特定研究设计
- `prompts_aer.py` — LLM 提示模板：包含主分析和文献评估的系统提示与用户提示，附带版本迭代日志（当前 `prompt.20260420.2`：`all_did_evidences: List` + 每 DID 风险字段）
- `schemas.py` — Pydantic 校验 schema：镜像 `prompts_aer.USER_PROMPT_TEMPLATE`，用于 tool_use schema 生成和 JSON 响应的语义校验；含 `@model_validator` 从旧版 `design_evidence_did` 单 dict 迁移到 `all_did_evidences` 列表
- `llm_results_db.py` — LLM 结果 SQLite 持久层：与 `fulltext_cache.db` 并列，永久保存每次分析输出；部署管道从两库联合生成 `policy_db.json`
- `chapter_split.py` — 联邦 omnibus 法案章节分割器（`govinfo-bills-*` + `govinfo-crpt-*`）：结构正则（TITLE/DIVISION/CHAPTER/SUBTITLE/PART）、触发规则（markers≥3 AND char_len≥10k）、TOC 剥离、8 章上限尾合并、boilerplate 短章节预过滤；输出 `Chapter` 对象序列，每个带 `{policy_id}--{ord:02d}-{slug(marker)}` 格式的 chapter_id
- `chapter_runner.py` — 章节抽取编排层：`extract_with_chapters(policy)` 包装 `extract_rd_did`，根据 `should_split` 分派到章节路径或单-bill 路径。Manifest-first（可选 `chapter_writer` 回调先持久化章节）、boilerplate 章节写桩不调 LLM、每个章节结果带 `chapter_id` 和 `parent_policy_id`
