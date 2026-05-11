# RFP 轻量解析与任务队列隔离 — Proposal

> Branch: `codex/next-iteration-optimization-20260419`
> Plan source of truth: `output/rfp-light-parse-and-priority-queue-plan.md`
> Baseline audit: `output/rfp-light-parse-baseline-audit.md`

## 目标

1. **隔离主流程**：RFP 上传/解析、需求抽取、证据检索、章节生成等"客户实时操作"必须独占 worker，不被历史方案 import / library rebuild 阻塞。
2. **RFP 轻量解析**：`doc_type=rfp` 上传走全新 `rfp_light_parse` job，2 秒返回 202，60 秒硬上限完成解析；不抽图、不写 Qdrant、不进历史方案库、不进 published wiki。
3. **三层队列可观测**：`/jobs/queue` 暴露 `interactive / library_parse / maintenance` 三类 worker 状态。

## 决策（结合 Baseline Audit）

| 主题 | 决策 | 备注 |
|---|---|---|
| 队列分层 | `interactive=3 / library_parse=1 / maintenance=1` 默认 worker 数 | Plan §1.2 |
| `BACKGROUND_JOB_WORKER_COUNT` 兼容 | 保留作为 fallback：若分层配置全为缺省值且本字段被显式设置，按旧逻辑全部投到 `interactive` | 兼容旧部署 |
| RFP 分流入口 | 在 HTTP endpoint `upload_document` 入口判断 `doc_type == "rfp" + settings.rfp_light_parse_enabled`，**不动** `_accept_document_upload`（library import 共用） | Audit F2 |
| RFP 解析器 | 新文件 `services/parsing/rfp_light_parser.py`，自实现 .txt/.md/.docx/.pdf 提取（pypdfium2 + python-docx 已有依赖） | Plan §2.1-2.2 |
| RFP 文本存储 | 写到 `data/rfp_text/<document_id>.txt` 全文 + `Document.meta.rfp_text_excerpt` 前 20k 字符 | Plan §2.3 + §2.4 |
| Phase 4 (rfp_knowledge_extract) | **默认关闭**：仅留 `RFP_KNOWLEDGE_EXTRACT_ENABLED=false` 配置 + `rfp_knowledge_extract` job_type 常量占位，extractor 本轮不实装 | Plan §4.1 |
| FastAPI BackgroundTasks 迁移 | **本轮不动** outline/draft 的 starlette `BackgroundTasks.add_task`（不占 BackgroundTaskQueue worker，已天然隔离），仅在 `/jobs/queue` 文档说明 | Audit §2.4 |
| `request_case_library_refresh` 迁移 | **本轮不动**（已 asyncio.Lock 串行，独立于 worker） | Audit §2.5 |
| Recovery startup | 按 job_type 路由：`document_parse` 按 doc_type 分流 / `rfp_light_parse` → interactive / `library_material_rebuild` → library_parse | Audit §2.7 |
| RequirementService 兼容 | `_load_source_context(document)` 在 `document.doc_type == "rfp"` 时改读 `meta.rfp_text_excerpt` → fallback 到 `rfp_text_storage_path` 文件 → fallback 到 chunks（兼容老 RFP 文档） → 空 | Plan §3.1 + Audit §2.6 |

## 范围（in / out）

**In**

- Phase 1：`BackgroundTaskQueueManager` + 7 个提交点路由 + `/jobs/queue` 多队列结构 + recovery 分支
- Phase 2：`RfpLightParser` + `rfp_light_parse` job + `upload_document` 分流 + `Document.meta` 写入
- Phase 3：`RequirementService._load_source_context` 按 doc_type 分支
- Phase 5：阶段进度（`stage`） + 阶段耗时（`timings`） + 60s 超时 → `parse_insufficient`
- Phase 6：前端文档页文案 + 状态映射 + 停止 history-library 轮询
- Phase 7：4 个新单测 + 4 个集成场景

**Out（本轮不做）**

- Phase 4 `rfp_knowledge_extract` extractor 实现（仅占位）
- Outline / Section generation / Draft generation 提交点迁到 BackgroundTaskQueue
- `request_case_library_refresh` 迁到 BackgroundTaskQueue
- Word `.doc`（旧版二进制）转码（plan §2.2 已建议低优先级后处理，先返回 `parse_insufficient` 提示）
- 多人并发上传同一 RFP 的场景（沿用 dedupe_key 即可）

## 风险

| 风险 | 缓解 |
|---|---|
| 三队列总 worker 数突破旧上限 4 → 内存/连接占用上升 | 默认 3+1+1=5；`config.py` 的 `background_job_worker_count` 仅作 fallback；新分层配置上限单独控制（`interactive≤8, library_parse≤4, maintenance≤2`） |
| 单例 `_queue` 已被多处文件 import → 重构风险 | 保留 `get_background_task_queue(queue_name="interactive")` 作为兼容签名；新接口 `get_background_task_queue_manager()` |
| RFP `.pdf` 文本抽取质量不如 docling | 接受降级：plan 明确"轻量需求文本即可"；fallback 到 `parse_insufficient` 而非完整解析（避免 70 分钟阻塞） |
| 老 RFP 文档（已写 chunks）兼容 | RequirementService fallback 链最后一级仍读 chunks |
| Recovery 时无法判断 `document_parse` job 应进哪个队列 | 从 `Document.doc_type` 读取，直接路由 |
