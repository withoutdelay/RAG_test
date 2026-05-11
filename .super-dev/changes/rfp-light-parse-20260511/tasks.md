# RFP 轻量解析与任务队列隔离 — Tasks

> 顺序：Phase 1 → 2 → 3（核心通路）→ 5（观测）→ 6（前端）→ 7（测试）→ 4（占位）
> 每个 Task 含【验收】用例。所有 Phase 1/2/3 的 Task 单测在 Phase 7 集中跑。

## Phase 1：任务队列分层（P0）

### Task 1.1 — `BackgroundTaskQueueManager` 多队列实现
**文件**: `backend/app/services/task_queue.py`

- 引入 `QueueName = Literal["interactive", "library_parse", "maintenance"]`
- 新增 `BackgroundTaskQueueManager`：`queue(name) -> BackgroundTaskQueue`、`submit(...)`、`active_job_id(name, key)`、`status() -> {queues: {...}, total: {...}}`
- 保留 `get_background_task_queue(queue_name: QueueName = "interactive")` 兼容签名
- 新增 `get_background_task_queue_manager()`
- `_QueuedTask` 不变；每个 `BackgroundTaskQueue` 独立 PriorityQueue + worker pool

【验收】`tests/test_task_queue_routing.py`：
- 三个队列各自独立 worker pool + 互不阻塞
- `manager.status()` 返回 `{queues: {interactive, library_parse, maintenance}, total}`
- `get_background_task_queue()` 不传参时返回 `interactive` 队列（向后兼容）
- 老式 dedupe_key 在单队列内仍生效；跨队列 dedupe_key 独立空间

### Task 1.2 — 配置项
**文件**: `backend/app/config.py` + `.env.example` + `.env.deploy.example`

```python
interactive_job_worker_count: int = Field(default=3, validation_alias="INTERACTIVE_JOB_WORKER_COUNT")
library_parse_job_worker_count: int = Field(default=1, validation_alias="LIBRARY_PARSE_JOB_WORKER_COUNT")
maintenance_job_worker_count: int = Field(default=1, validation_alias="MAINTENANCE_JOB_WORKER_COUNT")
```

`get_settings()` 中加 clamp：`interactive ∈ [1, 8]`、`library_parse ∈ [1, 4]`、`maintenance ∈ [1, 2]`。

`BACKGROUND_JOB_WORKER_COUNT` 仅作 fallback；如果用户显式设置且分层 3 项均为默认，则把 `interactive` worker 数设为 fallback 值。

【验收】`tests/test_task_queue_routing.py::test_default_worker_counts`

### Task 1.3 — `/jobs/queue` 多队列响应
**文件**: `backend/app/api/artifacts.py:880-882`

```json
{
  "queues": {
    "interactive": {"worker_count": 3, "queued_count": 0, "running_count": 1, "queued": [], "running": [...]},
    "library_parse": {...},
    "maintenance": {...}
  },
  "total": {"queued_count": 0, "running_count": 1}
}
```

【验收】`tests/test_task_queue_routing.py::test_jobs_queue_endpoint_returns_multi_queue`

### Task 1.4 — 7 个 submit 提交点路由
**文件**: `backend/app/api/documents.py`、`backend/app/api/library.py`、`backend/app/api/artifacts.py`

| # | 提交点 | 目标队列 |
|---|---|---|
| 1 | `documents.py:1264` upload | `document_parse` 默认 → `library_parse`；后续 Phase 2 用 `rfp_light_parse` 走 `interactive` |
| 2 | `documents.py:1465` reparse | 同上 |
| 3 | `documents.py:730` recovery | 按 `document.doc_type` 分流：rfp → interactive，其他 → library_parse；`rfp_light_parse` job_type recovery 也加上 |
| 4 | `library.py:794` rebuild | `library_parse` |
| 5 | `library.py:1001` recovery | `library_parse` |
| 6 | `artifacts.py:381` retrieve | `interactive` |
| 7 | `artifacts.py:650` regenerate_section | `interactive` |

提交方式从 `queue.submit(...)` 改为 `manager.submit(queue_name=..., ...)` 或 `get_background_task_queue("...").submit(...)`。

【验收】`tests/test_task_queue_routing.py::test_submit_routing_per_endpoint`

### Task 1.5 — Recovery 按 job type + doc_type 投递
**文件**: `backend/app/api/documents.py:642-756` + `backend/app/api/library.py:921-1030`

`recover_document_parse_jobs_on_startup` 按 `Document.doc_type == "rfp"` 决定队列；同时新增 `rfp_light_parse` job_type recovery（Phase 2 完成后再补这一分支或合并到本任务）。

【验收】`tests/test_task_queue_routing.py::test_recovery_routes_per_doc_type`

---

## Phase 2：RFP 轻量解析链路（P0）

### Task 2.1 — `RfpLightParser`
**新文件**: `backend/app/services/parsing/rfp_light_parser.py`

```python
@dataclass
class RfpLightParseResult:
    text: str           # 全文（截断到 max_chars）
    excerpt: str        # 摘要（截断到 excerpt_chars）
    page_count: int
    char_count: int
    elapsed_seconds: float
    metadata: dict[str, Any]   # 包含 source_format, fallback_used 等

class RfpLightParser:
    async def parse(self, file_path: str) -> RfpLightParseResult: ...
```

- `.txt` / `.md`：直接读，UTF-8 + GBK fallback
- `.docx`：`docx.Document(path)`，按 paragraphs + tables 顺序拼接
- `.pdf`：`pypdfium2.PdfDocument`，`page.get_textpage().get_text_range()`，前 `RFP_LIGHT_PARSE_MAX_PAGES` 页
- `.doc`：直接抛 `RfpLightParseInsufficient(reason="legacy_doc_format")`，不调任何转换
- 全程包裹 `asyncio.wait_for(asyncio.to_thread(...), timeout=RFP_LIGHT_PARSE_MAX_SECONDS)`，超时 → `RfpLightParseInsufficient(reason="timeout")`
- 不调 Embedder / Qdrant / ParserService / docling / aliyun

【验收】`tests/test_rfp_light_parser.py`：
- 4 种格式正常解析返回 RfpLightParseResult
- `.doc` → `RfpLightParseInsufficient(reason="legacy_doc_format")`
- 超过 `MAX_PAGES` → 只返回前 N 页文本
- 超过 `MAX_CHARS` → 截断 + metadata 标记 `truncated_chars=True`
- 超时 mock → `RfpLightParseInsufficient(reason="timeout")`

### Task 2.2 — 配置项
**文件**: `backend/app/config.py` + `.env*.example`

```python
rfp_light_parse_enabled: bool = Field(default=True, validation_alias="RFP_LIGHT_PARSE_ENABLED")
rfp_light_parse_max_seconds: float = Field(default=60.0, validation_alias="RFP_LIGHT_PARSE_MAX_SECONDS")
rfp_light_parse_max_pages: int = Field(default=30, validation_alias="RFP_LIGHT_PARSE_MAX_PAGES")
rfp_light_parse_max_chars: int = Field(default=80000, validation_alias="RFP_LIGHT_PARSE_MAX_CHARS")
rfp_light_parse_excerpt_chars: int = Field(default=20000, validation_alias="RFP_LIGHT_PARSE_EXCERPT_CHARS")
rfp_light_parse_store_full_text: bool = Field(default=True, validation_alias="RFP_LIGHT_PARSE_STORE_FULL_TEXT")
rfp_light_parse_embedding_enabled: bool = Field(default=False, validation_alias="RFP_LIGHT_PARSE_EMBEDDING_ENABLED")
```

clamp：`max_seconds ∈ [5, 300]`、`max_pages ∈ [1, 200]`、`max_chars ∈ [1000, 500000]`、`excerpt_chars ≤ max_chars`。

【验收】`tests/test_rfp_light_parser.py::test_settings_clamps`

### Task 2.3 — `_run_rfp_light_parse_job` + `_accept_rfp_light_upload`
**文件**: `backend/app/api/documents.py`（新增 helper）

```python
async def _accept_rfp_light_upload(...) -> APIResponse[DocumentUploadAccepted]:
    # 与 _accept_document_upload 类似，但：
    # - job_type="rfp_light_parse"
    # - queue_name="interactive"
    # - run=lambda: _run_rfp_light_parse_job(job_id, document_id)

async def _run_rfp_light_parse_job(job_id, document_id) -> None:
    # 1. set Document.parse_status="parsing"
    # 2. download via storage to temp_path
    # 3. update job.output_ref.progress.stage = "extracting_text"
    # 4. result = await RfpLightParser().parse(temp_path)
    # 5. write data/rfp_text/<document_id>.txt（如启用 store_full_text）
    # 6. document.meta = {**meta, "rfp_parse_mode": "light", "rfp_text_excerpt": result.excerpt[:excerpt_chars], ...}
    # 7. document.parse_status = "done" / "parse_insufficient" / "failed"
    # 8. job.status, job.output_ref.progress.stage = "completed"
    # 9. 不调 _parse_and_index_document / Chunker / Embedder / Qdrant / raw_document / figure asset / library_refresh
```

【验收】`tests/test_rfp_upload_flow.py::test_rfp_upload_does_not_call_chunker_or_qdrant_or_library_refresh`

### Task 2.4 — `upload_document` 分流
**文件**: `backend/app/api/documents.py:1295-1316`

```python
async def upload_document(...):
    project = await session.get(Project, project_id)
    if not project: raise 404
    settings = get_settings()
    if doc_type == "rfp" and settings.rfp_light_parse_enabled:
        return await _accept_rfp_light_upload(...)
    return await _accept_document_upload(...)
```

`reparse` 端点（`@router.post("/documents/{document_id}/reparse")`）也按 `document.doc_type == "rfp"` 分流。

【验收】`tests/test_rfp_upload_flow.py::test_upload_rfp_routes_to_light_parser`

### Task 2.5 — Job recovery 加 `rfp_light_parse` 分支
**文件**: `backend/app/api/documents.py:642-756`

复制 `recover_document_parse_jobs_on_startup` 模式，让它同时收 `document_parse` + `rfp_light_parse` 两个 job_type；按 doc_type 投到正确队列。

【验收】`tests/test_rfp_upload_flow.py::test_rfp_recovery_routes_to_interactive_queue`

---

## Phase 3：需求卡抽取改造（P0）

### Task 3.1 — `_load_source_context` 按 doc_type 分流
**文件**: `backend/app/services/requirement/service.py:347-375`

```python
async def _load_source_context(self, *, session, document, max_chunks=5):
    if document is None:
        return "", []
    if (document.doc_type or "").lower() == "rfp":
        return await self._load_rfp_light_context(session=session, document=document, max_chunks=max_chunks)
    return await self._load_chunk_context(session=session, document=document, max_chunks=max_chunks)

async def _load_rfp_light_context(self, *, session, document, max_chunks):
    # 1. document.meta["rfp_text_excerpt"] 非空 → 返回 (excerpt, [{document_id, document_name}])
    # 2. document.meta["rfp_text_storage_path"] 存在 → 读文件前 N 字符
    # 3. fallback 到 chunks（兼容老数据）
    # 4. 全空 → return "", []
```

【验收】`tests/test_requirement_service_rfp_context.py`：
- 写好 `meta.rfp_text_excerpt` → 命中分支 1
- 仅有 `meta.rfp_text_storage_path` → 命中分支 2
- 既无 meta 也有 chunks → 命中分支 3
- 全空 → 返回 `("", [])`

### Task 3.2 — RFP 仍 parsing 时的体验
**文件**: `backend/app/services/requirement/service.py:extract_requirement_card`

按 plan §3.2 MVP 推荐：
- 如果 RFP `parse_status == "parsing"` 且项目 `description` 非空 → 仍生成 requirement card，`confidence=0.55`、`source_refs=[]`、`missing_items` 加一条 `"RFP 解析未完成，需复核"`
- 如果 `description` 为空 → raise `ArtifactValidationError("需求文档仍在解析，请稍后重试")`（HTTP 409）

【验收】`tests/test_requirement_service_rfp_context.py::test_extract_with_parsing_rfp_uses_description_fallback`

---

## Phase 5：阶段进度 / 耗时 / 超时（P1）

### Task 5.1 — `_run_rfp_light_parse_job` 写 stage + timings
**文件**: `backend/app/api/documents.py`

`job.output_ref.progress = {"stage": "...", "timings": {...}}`

stages：`materializing_file` → `extracting_text` → `saving_requirement_text` → `completed`。每段 `time.perf_counter()` 差值写 `timings[stage]`。

### Task 5.2 — `_run_document_parse_job` 写 stage（历史路径）
**文件**: `backend/app/api/documents.py:_run_document_parse_job` + `_parse_and_index_document`

stages：`materializing_file` → `parsing_document` → `chunking` → `embedding` → `writing_vectors` → `extracting_assets` → `writing_assets` → `refreshing_library` → `completed`。本任务范围只写 stage 标记，不重构现有解析逻辑。

【验收】（手动）调一次 historical 上传，查看 job.output_ref.progress.stage 流转。

### Task 5.3 — 60s 硬超时落地
（已在 Task 2.1 RfpLightParser 中实现 `asyncio.wait_for`）

---

## Phase 6：前端文档页（P1）

### Task 6.1 — 文案更新
**文件**: `frontend/src/app/projects/[id]/documents/page.tsx`

| 行 | 旧 | 新 |
|---|---|---|
| L336 | `<h2>Documents</h2>` | `<h2>项目需求文档</h2>` |
| L338 | `Upload current project RFP documents...` | `用于抽取项目需求；不进入历史方案库。` |
| L355 | `Upload RFP` | `上传需求文档` |
| L366 | `Documents are automatically parsed...` | `仅做需求文本抽取，不参与历史方案复用。` |
| L387 | `RFP / Historical Data` | `项目需求 / 历史方案`（按 doc_type 翻译） |

### Task 6.2 — 状态文案映射
**文件**: 同上

```ts
const PARSE_STATUS_LABEL: Record<string, string> = {
  parsing: '需求解析中',
  done: '需求解析完成',
  parse_insufficient: '需求解析不充分',
  failed: '需求解析失败',
  pending: '已加入队列',
  queued: '已加入队列',
};
```

### Task 6.3 — 移除 history-library 轮询
**文件**: 同上

- 删 `historyLibraryStatus` state、`fetchHistoryLibraryStatus` callback、L48-63 / L69-74 的两个 useEffect、`renderHistoryLibraryAlert` 调用
- 保留 `setTimeout(fetchDocuments, 3000)` 当文档处于 parsing 时
- L387 Badge 不再需要 history library 区分

### Task 6.4 — Layout / 仓库入口（保留历史方案库链接）
**文件**: `frontend/src/app/projects/[id]/layout.tsx:39`

仅微调：`{ name: 'Documents', ... }` → `{ name: '项目需求', ... }`。"历史方案库"入口在 sidebar 其他位置已有，不动。

---

## Phase 7：测试（P0）

### Task 7.1 — `tests/test_task_queue_routing.py`（新增）
覆盖 Task 1.1 / 1.2 / 1.3 / 1.4 / 1.5。

### Task 7.2 — `tests/test_rfp_light_parser.py`（新增）
覆盖 Task 2.1 / 2.2。

### Task 7.3 — `tests/test_rfp_upload_flow.py`（新增）
覆盖 Task 2.3 / 2.4 / 2.5。集成场景：
- RFP `.md` 上传 → 2 秒返回 → job done → chunks/embeddings 均为 0
- RFP `.pdf` 上传 → ≤60s → meta.rfp_text_excerpt 非空
- RFP recovery → 进入 interactive 队列
- 老 `document_parse` job (doc_type=historical) recovery → 进入 library_parse

### Task 7.4 — `tests/test_requirement_service_rfp_context.py`（新增）
覆盖 Task 3.1 / 3.2。

---

## Phase 4：rfp_knowledge_extract 占位（P2 deferred）

### Task 4.1 — 配置 + 常量占位
- 加 `rfp_knowledge_extract_enabled: bool = False` 配置
- `task_queue.py` 中允许 `job_type="rfp_knowledge_extract"` 提交到 `maintenance` 队列
- 不实装 extractor；后续单独 PR → 见 `follow-ups.md` §F3

---

## Follow-ups 跟踪

MVP 范围之外、需后续单独立项的工作均已固化到 `@/Volumes/thunder/code/RAG_test/.super-dev/changes/rfp-light-parse-20260511/follow-ups.md`：

- **F1** Outline/Draft 提交点迁到 BackgroundTaskQueueManager
- **F2** `request_case_library_refresh` 迁到 maintenance queue
- **F3** RFP knowledge extractor 实装（Phase 4 占位的实体）
- **F4** `.doc` 旧版 Word 异步转码
- **F5** 项目级 SHA256 RFP 去重
- **F6** 跨队列全局 dedupe_key
- **F7** `BACKGROUND_JOB_WORKER_COUNT` 老配置清理

---

## Definition of Done (MVP)

- [ ] `pnpm --filter frontend lint && pnpm --filter frontend build` 全绿
- [ ] `cd backend && pytest tests/test_task_queue_routing.py tests/test_rfp_light_parser.py tests/test_rfp_upload_flow.py tests/test_requirement_service_rfp_context.py -q` 全绿
- [ ] `curl /api/v1/jobs/queue` 返回三队列结构
- [ ] 上传 4-5MB PDF RFP：2 秒返回 + ≤60s 完成；不写 Qdrant 不写 raw_document 不触发 library refresh
- [ ] 同时跑 historical rebuild 时，RFP 上传不被阻塞（manually verify）
- [ ] 需求卡抽取可读取 RFP excerpt
- [ ] 前端文档页文案 + 状态符合 §6
