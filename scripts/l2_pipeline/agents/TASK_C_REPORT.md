# Task C：实现与验收记录

实现已落地；完整验收仍被共享目录中、Task C 范围外的新动态导入依赖阻塞。

## 实现

- `agents/contract_tests/` 注册 14 类契约、73 个输出样例（14 个 golden、59 个 invalid）。包括 purpose、literature、通用/V7 coder、原始/后续 auditor、third-read、通用/嵌入模板 forecast、draft/sealed reviewer。真实 emitter 与 collector 在不同临时目录检查每个样例，避免旧 promotion 的写入冲突被误当作有效拒绝。
- dispatcher 在任何 reservation、CLI preflight 或 agent 启动前执行契约门禁，失败即拒绝该波；任务文件损坏、输出已预留及单个 future 异常均保留逐任务失败并继续其他任务。CLI flag 解析仍通过本地 `--help` 检查，不产生模型调用。
- 每个当前模板和生成的 host plan 使用统一 hub，并写明 `--max-concurrency` 为 1..4、默认 4。S0 purpose 的最小有效输出不要求可选 blindness 字段；S0L 使用 query/source_url 结构，失败搜索可保留 null URL。
- `hub.py` 提供 status、dispatch、collect、approve-run，以及 prepare/run。批量 prepare/run 逐设计调用现有单设计接口；collect 逐任务处理。approve-run 必须显式 `--only-pass`，只签 sealed PASS、MUST=[]，继续使用现有候选哈希、独立性、SHOULD 闭合、冻结、签署者与 UTC 时点检查；签署本身不执行估计。
- 八个 run6 适配器迁至 `scripts/l2_pipeline/run6_adapters/`。原 `data/` 文件 SHA/大小保持不变，记入 MIGRATION.json；run2/run4 原件均已有脚本映射。去除 PDAPS 导入时写入；显式验证在 -O 下保留；CDC/PDAPS/social/候选选择和 verifier 的设计/任务失败隔离。默认 verifier 从报告取计数；旧 15-card/4-audit 快照须显式 `--historical-snapshot`。
- data-code lint 检查实际运行模块、导入/调用别名、拆分或标注路径、exec/read、文件 loader 和 symlink；允许将归档代码作为证据读取。11 个当前模板由各版本的 `dependencies.py` 绑定，fixture JSON 由静态资源 loader 绑定，复用现有冻结闭包，不修改冻结器。

## 检查结果

使用本地 `.venv_did/bin/python -B`；普通及 `-O` 都运行。

| 检查 | 普通 | -O |
|---|---:|---:|
| 本任务独立机械测试 | 42 PASS，exit 0 | 42 PASS，exit 0 |
| 非 S6 契约探针 | 12 类 / 61 样例 PASS | 12 类 / 61 样例 PASS |
| 全量 regression --no-archival-fits | 526 tests；3 failures、40 errors；exit 1 | 526 tests；3 failures、40 errors；exit 1 |

全量失败共同根因为静态闭包拒绝
`scripts/l2_pipeline/sources/research_panel/build.py` 的
`importlib.import_module(__package__ + '.' + adapter)`：
`Dynamic import needs a static repository binding`。该文件在本任务开始后出现，不在允许修改范围内，未改动或绕过检查。之前同一共享目录中的 outcome_v2 connector 文件还曾在写入期间缺少目录 JSON；最后一次全量验收的首个阻塞已是 research_panel 动态导入。

独立检查没有伪装成完整验收：明确排除 5 个受全局依赖库存阻塞的测试方法（三个真实 final-approval 集成测试、全库存测试、完整 14 类契约测试）；完整套件仍保留并实际运行它们，因此 S6 的 12 个样例和真实签署集成目前未通过验收。未修改 CI-provider 配置；新增契约测试由现有 regression/unittest discover 自动发现。

日志与独立检查入口位于本任务 scratchpad 的 `task-c/`：
`l2-task-c-normal.log`、`l2-task-c-optimized.log`、
`scoped-normal.log`、`scoped-optimized.log`、`check_scoped.py`。
`git diff --check` exit 0；hub `--help` exit 0。

最终两个冻结验证器均 exit **0**，`issues=[]`、`actual_data_estimation_calls=0`：

```sh
.venv_did/bin/python -B scripts/did_samples/freeze.py --verify
.venv_did/bin/python -B scripts/did_samples/cases/freeze_cases.py --verify --code-path scripts/did_samples/amendment5_audit.py --code-path scripts/did_samples/crosscheck/run_crosscheck.py
```

本任务公共数据下载：**0 个文件、0 bytes**；URL/大小/许可清单为空。
无付费 API/LLM 调用、真实 agent 启动、真实估计、未来研究的结局关联拟合或制表、上传/发布、账号/密码操作、git commit/push。未改原有冻结文件、结果目录或归档 data 原件。旧 CLI/registration/stage 实现按路径范围保持原状；统一 hub 使用逐设计入口规避旧批次中止分支。
