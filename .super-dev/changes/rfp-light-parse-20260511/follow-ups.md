# RFP 轻量解析 — Follow-ups 跟踪

> 来源：本轮 docs gate 用户确认时明确"如需后续迭代请固化跟踪"。
> 这些项不在 MVP 范围（见 `proposal.md` §"Out"），后续单独立项 PR。
> 创建：2026-05-11

## F1：Outline / Draft generation 提交点迁到 BackgroundTaskQueueManager

**当前位置**:
- `@/Volumes/thunder/code/RAG_test/backend/app/api/artifacts.py:473` `generate_outline` 用 `BackgroundTasks.add_task(_run_generate_outline_job, ...)`
- `@/Volumes/thunder/code/RAG_test/backend/app/api/artifacts.py:575` `generate_sections` 用 `BackgroundTasks.add_task(_run_generate_sections_job, ...)`

**问题**：
- 跑在 starlette/fastapi 工作线程池里，**不进 BackgroundTaskQueueManager**
- 无 dedupe_key 保护：用户连点两次"生成大纲"会跑两次
- `/jobs/queue` 看不到这两类任务的运行状态
- 单 LLM 流式调用可能阻塞 fastapi worker（响应其他 HTTP 请求变慢）

**触发条件**：
- 当用户报告"大纲生成会重复跑" 或 "上传文档后页面变卡"
- 或想统一所有长任务的观测面板

**范围估算**：~80 行（artifacts.py 两个 endpoint + recovery startup）

**依赖**：需要 MVP Phase 1 的 `BackgroundTaskQueueManager` 已经稳定。

**风险**：starlette `BackgroundTasks` 与 `BackgroundTaskQueueManager` 的生命周期/异常处理不同；迁移时需要复测 outline/draft 错误回写到 `Job.output_ref` 的路径。

---

## F2：`request_case_library_refresh` 迁到 `maintenance` 队列

**当前位置**:
- `@/Volumes/thunder/code/RAG_test/backend/app/services/knowledge/library_refresh.py:36-455`
- 用 `asyncio.Lock + _CASE_LIBRARY_REFRESH_PENDING` 全局 flag 在调用方协程内串行化
- 触发点 7 处：`@/Volumes/thunder/code/RAG_test/backend/app/api/documents.py:639, 1525`、`@/Volumes/thunder/code/RAG_test/backend/app/api/library.py:651, 1083` 等

**问题**：
- 实际上不会阻塞 BackgroundTaskQueue worker（无问题）
- 但缺乏统一观测：`/jobs/queue` 看不到 case library / visual cache / wiki 刷新进度
- 当前只能查 `/documents/history-library/status` 单独端点

**触发条件**：
- 把所有长任务汇到一个 `/jobs/queue` 总览面板时
- 或案例库刷新出现卡死/重入 bug 时

**范围估算**：~150 行（library_refresh.py 改造为 Job 模型 + 7 处调用点 + maintenance queue 配置）

**依赖**：F1 完成后做更顺；可独立。

**风险**：现有 `read_case_library_refresh_status()` 读 JSON 文件返回前端，迁移后需要保留 API 向后兼容，否则前端 history-library 状态卡片会断。

---

## F3：Phase 4 `rfp_knowledge_extract` Extractor 实装

**MVP 状态**：已在 Task 4.1 留 `RFP_KNOWLEDGE_EXTRACT_ENABLED=false` 配置 + `rfp_knowledge_extract` job_type 占位（提交到 `maintenance`）。

**R4 已落地（不再属于 F3 范围）**：
- ✅ `Document.meta.rfp_text_storage_path` 落盘的是**完整 RFP 正文**（R4 #1 修复）。
- ✅ `RequirementService._load_rfp_light_context` 优先读全量 storage + 调用 `select_requirement_excerpt`（R4 #3 修复，新增 `app/services/parsing/rfp_excerpt_selector.py`）。规则驱动的智能片段选择已实现：
  - 章节标题命中 +10（项目概况 / 供货范围 / 技术参数 / 设备清单 / 接口 / 工期 / 验收 / 服务 / 评分 / 投标人资格）
  - 需求标记命中 +1 each, capped at 5（必须 / 应当 / 不得 / 投标人 / 供方 / ≥ / ≤ 等）
  - 数值规格命中 +1（数字+单位的量化要求）
  - 章节编号开头 +2（1. / 1.1 / 一、/ 第三章）
  - 长度奖励 +0.5（仅当其他信号已命中）
  - 按 score 降序选段落，达到 `excerpt_chars` 上限即停；按原文顺序重排，非相邻段落用 `[...]` 标记
  - 没有任何 RFP 信号时回落到机械头切片（保证非空）
- ✅ 新增 `tests/test_rfp_excerpt_selector.py`（11 个单测）+ `tests/test_requirement_service_rfp_context.py` 后半段需求回归保护测试。

**F3 仍需实装的部分（范围已收窄）**：
- LLM extractor：从智能 excerpt 进一步提炼项目需求事实 → 写入 `rfp_requirement_facts` 表（需新建）
- 隔离约束（plan §4.2）：
  - 输出 metadata 必须含 `{source_doc_type: "rfp", project_id, knowledge_scope: "project_requirement", publishable: false}`
  - 不得写 `historical case library` / `global visual index` / `published wiki`
- 触发方式：RFP light parse succeed 后异步提交（仅当 `RFP_KNOWLEDGE_EXTRACT_ENABLED=true`）

**触发条件**：
- 用户报告"RFP 中的需求被忽略"或要做 draft wiki 增强时
- 或评估指标显示需求卡 confidence < 0.6 频繁出现
- *已不再适用*：R4 #3 之前"超过 80K 字符的 RFP 只把前 20K 送 LLM"已修复，规则版智能 excerpt 已覆盖后半段需求。

**范围估算**：~200 行（缩减自原 300 行 — 智能 excerpt 已落地）。LLM prompt + extractor service + DB 表 + 隔离写入校验 + 单测。

**依赖**：MVP 全部完成 + 需求卡抽取链路稳定 + R4 全量落盘 + R4 #3 智能 excerpt（均已就绪）。

**风险**：需求事实泄漏到历史方案库 → 污染检索。隔离写入必须有强校验（单测覆盖 forbidden write paths）。

---

## F4：`.doc`（旧版二进制 Word）异步转码

**MVP 状态**：`RfpLightParser` 对 `.doc` 直接抛 `RfpLightParseInsufficient(reason="legacy_doc_format")`，返回 `parse_insufficient` + 提示用户上传 `.docx` 或 `.pdf`。

**待实装**：
- 上传 `.doc` 时仍保存原文件
- 提交 `maintenance` 队列低优先级 LibreOffice 转换 job（`doc_legacy_convert`）→ 转出 `.docx` 后回写 + 重新触发 `rfp_light_parse`
- 转换失败 → 维持 `parse_insufficient` 不变

**触发条件**：
- 客户实际上传 `.doc` 比例超过 5% 且抱怨"必须改成 docx 才能上传"

**范围估算**：~120 行

**依赖**：F2（maintenance queue 观测）做完更顺；可独立。

**风险**：LibreOffice 转换在 macOS dev 环境 vs Linux 服务器行为差异；需要单独测试。

---

## F5：多人并发上传同一 RFP 去重

**MVP 状态**：沿用现有 `dedupe_key="document_parse:<document_id>" / "rfp_light_parse:<document_id>"`。但**不同用户上传同一文件**会创建多份 Document 记录，分别有不同 document_id，触发多个 parse job。

**待考量**：
- 是否对 `content_sha256` 做项目级去重（已在 library import 路径用 `dedupe_global_library_upload=True`，但项目 RFP 路径未启用）
- 如果启用：返回已有 Document 引用而非创建新记录

**触发条件**：
- 团队协作场景报告"上传同一 RFP 跑了多次解析"

**范围估算**：~40 行

**依赖**：无。

**风险**：多个项目共享同一 Document 引用时的权限模型需要梳理（一般 RFP 是项目私有，去重应该限定在同一 project_id 内）。

---

## F6：跨队列全局 dedupe_key 命名空间

**MVP 状态**：每个 `BackgroundTaskQueue` 内部独立维护 `_active_by_key`。当前所有 dedupe_key 都已按 job 类型前缀（`document_parse:xxx` / `retrieve:xxx` / `generate-section:xxx` 等），不会跨队列冲突。

**待考量**：
- 如果未来出现"同一资源对应不同 job 类型"的 dedupe 需求（如同一 document 不能同时 reparse + extract knowledge），需要全局 dedupe 索引

**触发条件**：
- 出现跨 job 类型互斥需求时

**范围估算**：~30 行（`BackgroundTaskQueueManager` 内增加全局 `_active_by_key` + `find_active_job_id(key)` API）

**依赖**：无。

---

## F9：RFP 解析硬超时（子进程隔离 + kill）— **已完成**

**Round-2 review 已修复**：`_run_extract_with_subprocess()` 用 `multiprocessing.get_context("spawn").Process` 隔离每次解析，主进程通过 `process.join(timeout=max_seconds)` 实现硬超时。超时后 `_terminate_subprocess()` 先 SIGTERM (`process.terminate()`) 再 grace 2s，仍存活则 SIGKILL (`process.kill()`)。Pipe IPC 把结果/异常以 dict 序列化回主进程，子进程沉默退出会被识别为 `subprocess_exited_silently`。

**实测结果**：
- 主进程线程不会再被卡死的 PDF 占用
- 同时跑多份 RFP 时由 `RFP_LIGHT_PARSE_MAX_WORKERS` 控制并发（默认 2），主线程池只用于跑子进程 watchdog，不再有线程泄漏风险

**新增测试覆盖**：
- `tests/test_review_followup_fixes.py::RfpHardTimeoutSubprocessIntegrationTests` 三项：spawn 上下文断言 + `_terminate_subprocess` SIGTERM-only / SIGKILL escalation 两路分支
- 既有 `tests/test_rfp_light_parser.py::test_parse_timeout_raises_parse_insufficient` 在新方案下仍 pass（spawn 启动开销自然超过 100ms 触发 hard timeout 路径）

**冷启开销**：
- spawn 子进程在 macOS 首次启动 ~150-300ms（导入 docx/pypdfium2 子进程内首次加载）
- 在 60s 上限下完全可接受；如需要进一步优化，未来可考虑 PreForkPoolExecutor

---

## F8：历史 `_run_document_parse_job` 写阶段 stage + timings

**MVP 状态**：`_run_rfp_light_parse_job` 已写完整 `stage` (`materializing_file` → `extracting_text` → `saving_requirement_text` → `completed`) + `timings` 字典；但历史路径 `_run_document_parse_job` / `_parse_and_index_document` 仅写 `stage="parsing"` → `"completed"`，中间阶段不可见。

**Plan §5.1 建议的历史阶段**：
- `materializing_file` / `parsing_document` / `chunking` / `embedding` / `writing_vectors` / `extracting_assets` / `writing_assets` / `refreshing_library` / `completed`

**待实装**：
- 把 `job` 和 `timings` 字典传入 `_parse_and_index_document`
- 在 Chunker / Embedder / QdrantService.upsert / raw_document / figure_asset 等关键步骤前后 commit `progress.stage` + `timings[stage]`
- 测试：`tests/test_document_parse_observability.py` 验证每个阶段被写入

**触发条件**：
- 历史方案上传解析卡住或过长时，需要定位卡在哪一步
- 或 `/jobs/queue` 与 `/jobs/{id}` 统一前端 progress UI 时

**范围估算**：~120 行（修改 `_parse_and_index_document` 多点 commit + session 传递）

**依赖**：无。

**风险**：`_parse_and_index_document` 当前在单 session 内做大量写入；多次 commit 后，下游的 `document.meta` / `raw_document` / `figure_assets` 需要确保幂等。测试必须覆盖中途失败的回滚。

---

## F7：`BACKGROUND_JOB_WORKER_COUNT` Fallback 兼容期

**MVP 状态**：保留 `BACKGROUND_JOB_WORKER_COUNT` 作为 fallback；若用户显式设置且分层三项均为默认值，把 `interactive` worker 数设为 fallback 值；分层 `INTERACTIVE_JOB_WORKER_COUNT` 等显式设置时优先生效。

**待清理**：1-2 个 release 周期后（部署都迁到分层配置）→ 删 `BACKGROUND_JOB_WORKER_COUNT`，更新 docs。

**触发条件**：
- 所有生产环境部署都已迁到分层配置
- 或 6 个月后

**范围估算**：~20 行（config + env templates + docs）

**依赖**：需要部署侧迁移完成。

---

## 优先级建议（再下次启动迭代时参考）

> **本 PR 已完成**：
> - **R1（首轮 review hot-fix）**：F1 outline/draft 入 interactive queue；F2 case_library_refresh HTTP 触发点入 maintenance queue；recovery 守卫 legacy RFP；专属 `ThreadPoolExecutor` 软超时
> - **R2（二轮 review hot-fix）**：F9 RFP 解析硬超时（子进程隔离 + SIGKILL）；F2 剩余项 worker 内 await refresh 改 maintenance 提交（`_run_document_parse_job` + library_material_rebuild 终结分支）；新增 `recover_interactive_jobs_on_startup` 处理 outline/draft/retrieve/section 启动僵尸态；legacy RFP `document_parse` 自动迁移为 `rfp_light_parse`（不再要求重新上传）
> - **R3（三轮 review hot-fix）**：R2 引入的回归 bug 全部修复——子进程 IPC 大文本误判 timeout（child pre-truncate to `max_chars` + parent poll/recv 循环替代 join-then-recv）；legacy RFP 迁移 `Job(id=...)` + `trace_id` 缺失导致 flush IntegrityError；interactive 启动恢复漏掉真实 DB `job_type="generate"`（full draft）
> - **R4（四轮 review hot-fix）**：R3 的"子进程内 max_chars 截断"被指出会丢失后半段 RFP 需求 → 子进程改为把全量文本写到父进程持有的 tmpfile（`tempfile.NamedTemporaryFile`），Pipe 只传 ~200 字节 metadata，父进程读完整 text 并清理 tmpfile；`RfpLightParser.parse` 不再 `text[:max_chars]` 截断 `result.text`，storage 落盘的是完整 RFP 正文（避免需求丢失）；recovery 检查 `interactive_queue.submit` 返回值，发现 dedupe 命中时把重复 DB job 标记 `failed + DuplicateRecoveredJob` 并写入 `deduplicated_to_job_id`，避免孤儿 queued job 永远不被消费；**R4 #3** 进一步指出 R4 #1 修了 storage 但 `RequirementService._load_rfp_light_context` 仍然先吃 `meta.rfp_text_excerpt`（机械前 20K），关键条款仍丢失 → 新增 `app/services/parsing/rfp_excerpt_selector.py`（章节标题 / 需求标记 / 数值规格 / 章节编号打分），`RequirementService` 优先读全量 storage 经 `select_requirement_excerpt` 选段，旧 `rfp_text_excerpt` 降级为兼容 fallback
> - **R5（五轮 review hot-fix）**：R4 #3 修好了 `_load_rfp_light_context` 读全文，但 `build_requirement_content` 紧跟着把 selector 选好的全选段 `[:600]` 截成 UI 摘要写到 `RequirementCard.content.source_excerpt`，后续 outline (`outline_service.build_outline_inputs:rfp_context`) / retrieval (`_extract_requirement_query_hints`) / section (`build_section_global_params._source_excerpt`) **全部只读这个 600 字字段**，IP55 / ≥2.5MW / 评分条款这类后半段需求仍然进不了 LLM 上下文，端到端没闭环。修复：`build_requirement_content` 同时写 `source_excerpt[:600]`（UI 短摘要，向后兼容）和新字段 `source_context`（selector 选出的完整筛选上下文，~20K）；新增 helper `resolve_requirement_source_context(content)` 优先读 `source_context` 回退 `source_excerpt`，给老需求卡留 fallback；outline / retrieval / section 三个下游全部切到 helper，UI/前端零改动（`source_excerpt` 600 字契约保留）

| 优先级 | Item | 估时 |
|---|---|---|
| P1 | F3 RFP knowledge extractor (LLM extractor + 隔离；智能 excerpt 已 R4 #3 落地) | 1-2 days |
| P2 | F8 历史 document_parse stage 观测 | 1 day |
| P3 | F4 `.doc` 转码 | 1 day |
| P3 | F5 项目级 SHA256 去重 | 0.5 day |
| P4 | F6 全局 dedupe | 0.5 day |
| P4 | F7 老配置清理 | 0.5 day |
