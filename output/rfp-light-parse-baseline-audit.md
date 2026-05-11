# RFP 轻量解析与任务队列隔离 — Baseline Audit

> 关联：`output/rfp-light-parse-and-priority-queue-plan.md`
> 分支：`codex/next-iteration-optimization-20260419`
> Audit 时间：2026-05-11

## 1. 工作模式判定

- **mode**: `evolve`（在已有解析/任务队列代码上做隔离改造，不是 `new`）
- **WIP 状态**: 工作树仅 `output/rfp-light-parse-and-priority-queue-plan.md` 一个 untracked 文件，干净。

## 2. Plan 描述 ↔ 当前代码逐条核对

### 2.1 task_queue.py 是单例（Plan §当前问题2）✅ 准确

`@/Volumes/thunder/code/RAG_test/backend/app/services/task_queue.py:153-160`:

```python
_queue: BackgroundTaskQueue | None = None

def get_background_task_queue() -> BackgroundTaskQueue:
    global _queue
    if _queue is None:
        _queue = BackgroundTaskQueue(worker_count=get_settings().background_job_worker_count)
    return _queue
```

无队列名概念，`PriorityQueue` 单例，工作线程数受 `BACKGROUND_JOB_WORKER_COUNT` 控制（默认 1，硬上限 4，见 `@/Volumes/thunder/code/RAG_test/backend/app/config.py:290-293`）。

### 2.2 RFP 走重链路（Plan §当前问题1）✅ 准确

调用链：

```
upload_document  (@/Volumes/thunder/code/RAG_test/backend/app/api/documents.py:1295-1316, doc_type=Form(...))
  → _accept_document_upload  (line 1163-1287, 不区分 doc_type)
    → 创建 Job(job_type="document_parse")
    → queue.submit(job_type="document_parse", ..., priority=40)
    → _run_document_parse_job → _parse_and_index_document  (line 359+)
      → Chunker / Embedder / QdrantService.upsert_chunk / _upsert_raw_document / _replace_figure_assets
      → 若 _should_refresh_history_library → request_case_library_refresh()
```

确认 RFP 也走完整链路；前端固定传 `doc_type='rfp'`（`@/Volumes/thunder/code/RAG_test/frontend/src/app/projects/[id]/documents/page.tsx:23`）。

### 2.3 当前 7 个 queue.submit 提交点

| # | 文件 | 行号 | job_type | 当前 priority | Plan 建议队列 |
|---|---|---|---|---|---|
| 1 | `backend/app/api/documents.py` | 730 | `document_parse` (recovery) | 20 | 按 doc_type 分流：rfp→interactive，其他→library_parse |
| 2 | `backend/app/api/documents.py` | 1264 | `document_parse` (upload) | 40 | 同上 |
| 3 | `backend/app/api/documents.py` | 1465 | `document_parse` (reparse) | 默认 100 | 同上 |
| 4 | `backend/app/api/library.py` | 794 | `library_material_rebuild` | 30 | `library_parse` |
| 5 | `backend/app/api/library.py` | 1001 | `library_material_rebuild` (recovery) | 30 | `library_parse` |
| 6 | `backend/app/api/artifacts.py` | 381 | `retrieve` | 20 | `interactive` |
| 7 | `backend/app/api/artifacts.py` | 650 | `generate_section` | 10 | `interactive` |

**`/jobs/queue` 端点**：`@/Volumes/thunder/code/RAG_test/backend/app/api/artifacts.py:880-882` — 当前直接 dump 单队列 status，需改成多队列结构。

### 2.4 BackgroundTasks（FastAPI starlette）使用情况 ⚠️ Plan 未细化

`@/Volumes/thunder/code/RAG_test/backend/app/api/artifacts.py:473, 575` — `generate_outline` 与 `generate_sections` 用 FastAPI `BackgroundTasks.add_task()` 提交，**不进 `BackgroundTaskQueue`**。Plan §1.5 列了 outline/draft → interactive，这意味着这两处也要迁到 queue manager 才能体现"interactive 优先级"。这是一个**新引入的工作量**：本 audit 发现的非显式工作。

**建议处理**：
- **MVP 不强迁**（保持 starlette BackgroundTasks），因为 starlette 任务跑在 fastapi worker 线程里不占 BackgroundTaskQueue 的 worker，本质上已经"独立"。
- 仅在 `/jobs/queue` 文档中说明这两类任务不进 queue。
- 如果未来需要可观测/dedupe，再补迁。

我会把这条作为"P2 deferred"留在 tasks.md。

### 2.5 case library refresh（Plan §1.5）⚠️ 已是异步循环，不在 BackgroundTaskQueue

`@/Volumes/thunder/code/RAG_test/backend/app/services/knowledge/library_refresh.py:36-38, 157-160, 363-455`:

```python
_CASE_LIBRARY_REFRESH_LOCK = asyncio.Lock()
_CASE_LIBRARY_REFRESH_PENDING = False
_VISUAL_CACHE_REFRESH_LOCK = asyncio.Lock()
_VISUAL_CACHE_REFRESH_PENDING = False

async def request_case_library_refresh() -> None:
    await asyncio.gather(
        _request_case_library_pipeline_refresh(),
        _request_visual_cache_pipeline_refresh(),
    )
```

它不通过 `queue.submit`，而是用全局 asyncio.Lock + pending flag 在调用 task 内部串行化。**实际上已经"独立"于 BackgroundTaskQueue**，不会占 worker。

**建议处理**：MVP 不动，仅在 audit/tasks 中说明。如果未来需要把它纳入 maintenance queue 做统一观测，再单开 PR。Plan §1.5 提到的"library_refresh.py → maintenance" 视为**P2 deferred**。

### 2.6 RequirementService._load_source_context（Plan §3.1）✅ 必须改

`@/Volumes/thunder/code/RAG_test/backend/app/services/requirement/service.py:347-375`:

```python
async def _load_source_context(self, *, session, document, max_chunks=5):
    if document is None:
        return "", []
    chunks = await session.scalars(
        select(Chunk).where(Chunk.document_id == document.id)
        .order_by(Chunk.chunk_index.asc()).limit(max_chunks)
    ).all()
    excerpt = "\n\n".join(chunk.content for chunk in chunks)
    source_refs = [...]
    return excerpt, source_refs
```

不区分 `doc_type`，直接读 `Chunk` 表。**RFP 走轻量解析后不再写 chunks → 这里会拿到空 excerpt → 需求卡抽取直接掉到 fallback**。Phase 3 必须改。

### 2.7 Recovery startup 路径（Plan §"风险与注意事项"末条）✅ 必须改

- `@/Volumes/thunder/code/RAG_test/backend/app/api/documents.py:642-756` `recover_document_parse_jobs_on_startup`
- `@/Volumes/thunder/code/RAG_test/backend/app/api/library.py:921-1030` `recover_library_material_rebuild_jobs_on_startup`

两个 recovery 都直接 `get_background_task_queue().submit(...)`。改造后必须按 job_type 投递到正确队列：
- `document_parse` 按 doc_type 分流（rfp → interactive，其他 → library_parse）
- `rfp_light_parse` → interactive（**新增 recovery 分支**）
- `library_material_rebuild` → library_parse

### 2.8 前端文档页（Plan §6）✅ 准确

`@/Volumes/thunder/code/RAG_test/frontend/src/app/projects/[id]/documents/page.tsx`（413 行）：
- L23: `const docType = 'rfp'` 硬编码
- L41: 轮询 `/documents/history-library/status`
- L336: `<h2>Documents</h2>`
- L338: `Upload current project RFP documents...`
- L355: `Upload RFP`
- L366: `Documents are automatically parsed into intelligent chunks...`
- L387: Badge `{doc.doc_type === 'rfp' ? 'RFP' : 'Historical Data'}`
- L238-300: `renderHistoryLibraryAlert` 显示 case library / wiki / visual 缓存状态

`/documents/history-library/status` 端点本身保留（library 仍需要），但项目文档页**应停止轮询**它。

### 2.9 已具备依赖

- `python-docx >= 1.2.0` — 已在 `pyproject.toml:14` ✅
- `pypdfium2` — `docling_parser.py:620, 666` 已 lazy import 使用 ✅（说明已通过 docling 间接安装）
- 不需要新增 dependency。

## 3. Plan 之外发现的细节

| 编号 | 发现 | 影响 |
|---|---|---|
| F1 | `BACKGROUND_JOB_WORKER_COUNT` 默认 1 + 硬上限 4 | 升级到三队列后默认值 `interactive=3 / library_parse=1 / maintenance=1` 总和 5，需要打破"≤4" 上限或保持 fallback 兼容；已在 plan §1.2 隐含 |
| F2 | `_accept_document_upload` 是上传统一入口（被 `upload_document` + `import_library_material` 共用） | RFP 分流必须放在 `upload_document` 入口而非 `_accept_document_upload`，否则 library import 也被改 |
| F3 | `import_library_material` 用 `historical_proposal` doc_type，与 RFP 分流互不冲突 | 不需要额外保护 |
| F4 | 现有 `Document.parse_status` 可选值含 `pending / queued / parsing / done / parse_insufficient / failed` | RFP 状态可直接复用，不需要新增 status 值 |
| F5 | 现有 `Job.input_ref` 已是 JSONB | RFP `rfp_text_excerpt` / `rfp_text_storage_path` 可直接进 `Document.meta`，不影响其它读路径 |
| F6 | `priority` 排序：数字越小越优先（10 > 30 > 40 > 100） | 三队列改造后队列内仍用 priority，跨队列由 worker 数隔离 |
| F7 | `RECOVERABLE_DOCUMENT_PARSE_STATUSES = {pending, queued, parsing}` | RFP recovery 复用同一集合即可 |
| F8 | docling parser 已有 `parse_document(file_path, ...)` 但参数列表不含 `include_asset_enrichment` | Plan §2.2 提到的 fallback 调用要新增形参或封装 helper；**MVP 不依赖 fallback**，先用 `pypdfium2 + python-docx` 直接实现，避免改 ParserService |

## 4. 风险与注意事项确认

Plan §"风险与注意事项" 全部成立，无新增。重点：
- 不删 `_parse_and_index_document`：✅ 历史方案库仍依赖
- RFP 不写 Qdrant：✅ Phase 2 RfpLightParser 不调 Embedder/QdrantService
- RFP 不进 published wiki：✅ Phase 4 默认关闭
- recover startup 按 job type 投递：✅ Phase 1 + Phase 2 都要处理

## 5. 实施可行性结论

Plan 设计合理且与代码现状对齐。改造是"加路径 + 路由"，不是"改既有路径"——历史方案路径不动。

**建议落地范围**（vs Plan）：
- ✅ Phase 1 / 2 / 3 / 5 / 6 / 7 全部按 plan 实施（MVP 必须）
- ⏸️ Phase 4 (`rfp_knowledge_extract`) 默认 `enabled=false`，先留 config + job_type 占位，不实装 extractor（plan §4.1 也建议先关闭）
- ⏸️ Plan §1.5 的 "outline/draft → interactive" / "library_refresh → maintenance" 由于这两个不进 BackgroundTaskQueue，本轮**不强迁**，仅在文档说明，留作 P2

**后续迭代跟踪**：F1-F7 全部固化到 `@/Volumes/thunder/code/RAG_test/.super-dev/changes/rfp-light-parse-20260511/follow-ups.md`。

## 6. 涉及文件 + 改动估算

| 文件 | 状态 | diff 估 | Phase |
|---|---|---|---|
| `backend/app/services/task_queue.py` | 修改（重构） | ~150 行 | 1 |
| `backend/app/config.py` | 修改 | ~15 行 | 1 |
| `backend/app/api/documents.py` | 修改 | ~80 行（分流 + recovery 分支） | 1, 2, 5 |
| `backend/app/api/library.py` | 修改 | ~10 行（队列名传入） | 1 |
| `backend/app/api/artifacts.py` | 修改 | ~20 行（队列名传入 + /jobs/queue 改造） | 1 |
| `backend/app/services/parsing/rfp_light_parser.py` | 新增 | ~200 行 | 2 |
| `backend/app/services/requirement/service.py` | 修改 | ~60 行（_load_source_context 分支） | 3 |
| `backend/app/main.py` | 微调（recovery wiring） | ~5 行 | 1 |
| `backend/tests/test_task_queue_routing.py` | 新增 | ~120 行 | 7 |
| `backend/tests/test_rfp_light_parser.py` | 新增 | ~150 行 | 7 |
| `backend/tests/test_rfp_upload_flow.py` | 新增 | ~120 行 | 7 |
| `backend/tests/test_requirement_service_rfp_context.py` | 新增 | ~80 行 | 7 |
| `frontend/src/app/projects/[id]/documents/page.tsx` | 修改 | ~30 行（文案 + 移除轮询 + 状态文案） | 6 |
| `frontend/src/lib/types.ts` | 可能微调 | ~5 行 | 6 |
| `.env.example` / `.env.deploy.example` | 修改 | ~10 行 | 1, 2 |
| **总计** | 9 修改 + 4 新增 | **~1050 行** | — |

## 7. 文档 gate

按 Super Dev evolve 模式，本 baseline audit + 配套 `tasks.md` 即文档产物。请确认：

1. 上面 §2.4 / §2.5 / §5 中的"P2 deferred"决定是否接受
2. 是否同意 Phase 4 `rfp_knowledge_extract` 仅留占位（不实装 extractor）
3. 是否同意三队列默认 worker 数 `interactive=3, library_parse=1, maintenance=1`

确认后开始 Phase 1 实施。
