# RFP 轻量解析与任务队列隔离开发方案

## 目标

当前 `doc_type=rfp` 的项目需求文档上传复用了历史方案库的完整解析链路，导致 RFP 上传后会执行完整 parser、chunk、embedding、Qdrant upsert、raw_document、figure/table asset 抽取与可能的 LLM 资产审核。该行为会拖慢新建项目主流程，并可能被历史文档解析任务阻塞。

本方案目标：

- Project RFP documents 只服务于需求提取和需求分析，不走历史方案入库链路。
- 历史方案解析与项目主流程任务隔离，历史任务不能阻塞客户正在操作的项目流程。
- RFP 后续如果参与 Wiki/知识抽取，必须走独立低优先级流程，不污染历史方案库、case library、visual index、published wiki。
- 提供明确的状态、进度、超时和队列观测能力，避免再次出现 4MB RFP 解析 70 分钟的情况。

## 当前问题

### 1. RFP 上传走了重解析链路

入口：

- `backend/app/api/documents.py`
- `upload_document()`
- `_accept_document_upload()`
- `_run_document_parse_job()`
- `_parse_and_index_document()`

现状：

- 前端项目文档页固定传 `doc_type=rfp`。
- 后端仍把 RFP 提交为 `document_parse` job。
- `document_parse` job 调用 `_parse_and_index_document()`。
- `_parse_and_index_document()` 会进行完整 chunk、embedding、Qdrant upsert、raw_document、figure asset 处理。

这对历史方案库合理，但对 RFP 主流程过重。

### 2. 所有任务共用同一个 BackgroundTaskQueue

当前：

- `backend/app/services/task_queue.py`
- 单例 `_queue`
- worker 数由 `BACKGROUND_JOB_WORKER_COUNT` 控制

问题：

- 历史文档解析、library rebuild、RFP 解析、evidence retrieval、draft generation 都可能共用同一组 worker。
- 低优先级重任务可能占满 worker，阻塞客户主流程。
- 只有 priority 排序，但运行中的长任务无法被抢占。

### 3. RFP 与历史方案知识边界不清晰

RFP 是客户需求、约束和招标边界，不是可复用历史方案。即使未来从 RFP 中抽取知识，也只能作为项目需求事实或 draft wiki 输入，不能直接进入：

- historical proposal case library
- visual index
- published wiki
- historical reusable blocks

## 设计原则

1. 主流程优先。
   Project 新建、RFP 需求解析、需求卡抽取、证据检索、大纲和章节生成属于客户实时流程，必须高优先级。

2. 历史解析隔离。
   历史方案入库可以慢，但不能占用主流程 worker。

3. RFP 只做轻量需求解析。
   RFP 上传阶段只提取需求分析所需文本，不抽图、不做 embedding、不写 Qdrant。

4. 后处理异步低优先级。
   RFP 的 Wiki/知识增强必须作为后续低优先级任务，且输出隔离。

5. 状态可解释。
   每个 job 必须暴露 queue、stage、耗时和失败原因。

## 开发范围

### Backend

新增或修改：

- `backend/app/services/task_queue.py`
- `backend/app/config.py`
- `backend/app/api/documents.py`
- `backend/app/services/requirement/service.py`
- 新增 `backend/app/services/parsing/rfp_light_parser.py`
- 可选新增 `backend/app/services/jobs/queue_router.py`
- `backend/app/api/artifacts.py`
- `backend/app/api/library.py`
- `backend/app/main.py`
- 测试文件

### Frontend

修改：

- `frontend/src/app/projects/[id]/documents/page.tsx`
- 可能涉及 `frontend/src/lib/types.ts`
- 可能涉及 jobs queue 展示页面或组件

## Phase 1：任务队列分层

### 1.1 新增队列类型

在 `backend/app/services/task_queue.py` 中引入队列名称：

```python
QueueName = Literal["interactive", "library_parse", "maintenance"]
```

建议队列职责：

- `interactive`
  - RFP 轻量解析
  - requirement extraction
  - evidence retrieval
  - outline generation
  - draft generation
  - section generation

- `library_parse`
  - historical proposal import
  - material reparse
  - rebuild library material item

- `maintenance`
  - case library refresh
  - AI Wiki compile
  - visual index refresh
  - table reconstruction
  - RFP knowledge extraction

### 1.2 配置项

在 `backend/app/config.py` 新增：

```env
INTERACTIVE_JOB_WORKER_COUNT=3
LIBRARY_PARSE_JOB_WORKER_COUNT=1
MAINTENANCE_JOB_WORKER_COUNT=1
```

保留 `BACKGROUND_JOB_WORKER_COUNT` 作为兼容 fallback，但新代码应优先使用以上分层配置。

### 1.3 Queue Manager

将单例 `_queue` 替换为多队列管理器：

```python
class BackgroundTaskQueueManager:
    def queue(self, name: QueueName) -> BackgroundTaskQueue: ...
    def submit(..., queue_name: QueueName, ...): ...
    def active_job_id(queue_name: QueueName, dedupe_key: str): ...
    def status(): ...
```

保留兼容函数：

```python
def get_background_task_queue(queue_name: QueueName = "interactive") -> BackgroundTaskQueue:
    return get_background_task_queue_manager().queue(queue_name)
```

### 1.4 队列状态 API

当前 `GET /jobs/queue` 需要返回分队列状态：

```json
{
  "queues": {
    "interactive": {
      "worker_count": 3,
      "queued_count": 0,
      "running_count": 1,
      "queued": [],
      "running": []
    },
    "library_parse": {},
    "maintenance": {}
  },
  "total": {
    "queued_count": 0,
    "running_count": 2
  }
}
```

### 1.5 调整任务提交点

必须调整：

- `backend/app/api/documents.py`
  - RFP upload/reparse -> `interactive`
  - historical library import/reparse -> `library_parse`

- `backend/app/api/library.py`
  - material rebuild/reparse -> `library_parse`

- `backend/app/api/artifacts.py`
  - requirement/retrieve/outline/draft/section -> `interactive`

- `backend/app/services/knowledge/library_refresh.py`
  - case library/wiki/visual refresh -> `maintenance`

验收：

- 历史方案 rebuild 正在运行时，RFP 上传、需求抽取、证据检索可以立即进入 `interactive` queue 并执行。
- `library_parse` worker 被占满时，不影响 `interactive` worker。

## Phase 2：RFP 轻量解析链路

### 2.1 新增 RfpLightParser

新增文件：

- `backend/app/services/parsing/rfp_light_parser.py`

职责：

- 从 RFP 文件中快速提取需求文本。
- 限制页数、字符数、耗时。
- 不抽图片。
- 不做视觉审核。
- 不做 embedding。
- 不写 Qdrant。

建议接口：

```python
@dataclass
class RfpLightParseResult:
    text: str
    excerpt: str
    metadata: dict[str, Any]

class RfpLightParser:
    async def parse(self, file_path: str) -> RfpLightParseResult:
        ...
```

### 2.2 支持格式

`.txt` / `.md`：

- 直接读取文本。
- 限制 `RFP_LIGHT_PARSE_MAX_CHARS`。

`.docx`：

- 使用已有依赖 `python-docx`。
- 抽取段落与表格文本。
- 不处理图片。

`.pdf`：

- 优先使用轻量文本抽取。
- 推荐使用 `pypdfium2`，因为 docling 环境已有相关依赖。
- 只抽前 `RFP_LIGHT_PARSE_MAX_PAGES` 页。
- 如果文本为空，再 fallback 到已有 parser 的轻量模式，但必须关闭资产增强：
  `ParserService.parse_document(file_path, include_asset_enrichment=False)`

`.doc`：

- 不建议在主流程同步转换。
- 可返回 `parse_insufficient` 或提示“旧版 Word 可作为附件保存，需求建议上传 docx/pdf”。
- 后续可低优先级转码，不阻塞主流程。

### 2.3 配置项

新增：

```env
RFP_LIGHT_PARSE_ENABLED=true
RFP_LIGHT_PARSE_MAX_SECONDS=60
RFP_LIGHT_PARSE_MAX_PAGES=30
RFP_LIGHT_PARSE_MAX_CHARS=80000
RFP_LIGHT_PARSE_EXCERPT_CHARS=20000
RFP_LIGHT_PARSE_STORE_FULL_TEXT=true
RFP_LIGHT_PARSE_EMBEDDING_ENABLED=false
```

### 2.4 新增 RFP 解析 Job

建议不要复用 `document_parse`，新增：

```text
job_type = "rfp_light_parse"
```

RFP 上传时：

- 创建 `Document`
- `doc_type="rfp"`
- `parse_status="parsing"`
- 提交 `rfp_light_parse` 到 `interactive` queue
- 快速返回 202

RFP 解析成功后：

```json
{
  "rfp_parse_mode": "light",
  "rfp_text_excerpt": "...",
  "rfp_text_storage_path": "data/rfp_text/xxx.txt",
  "rfp_text_char_count": 12345,
  "rfp_light_parse_pages": 12,
  "rfp_light_parse_elapsed_seconds": 4.2,
  "chunk_count": 0,
  "indexed_chunk_count": 0,
  "figure_asset_count": 0,
  "raw_document_id": null
}
```

`parse_status="done"`。

RFP 解析失败后：

- `parse_status="parse_insufficient"` 或 `failed`
- 必须保留原文件
- 不阻塞用户继续手动输入需求

### 2.5 避免调用重链路

在 `upload_document()` 或 `_accept_document_upload()` 层分流：

```python
if doc_type == "rfp" and settings.rfp_light_parse_enabled:
    return await _accept_rfp_light_upload(...)
```

RFP 不得调用：

- `_parse_and_index_document()`
- `Chunker().split()` 用于入库
- `Embedder().embed_texts()`
- `QdrantService().upsert_chunk()`
- `_upsert_raw_document()`
- `_replace_figure_assets()`
- `request_case_library_refresh()`

## Phase 3：需求卡抽取改造

### 3.1 修改 RequirementService

文件：

- `backend/app/services/requirement/service.py`

当前 `_load_source_context()` 从 `Chunk` 表取前 5 个 chunk。

改为优先级：

1. `document.metadata["rfp_text_excerpt"]`
2. `document.metadata["rfp_text_storage_path"]`
3. RFP light chunks，如果后续保留轻量 chunk 表
4. 旧 chunks fallback
5. 空字符串

建议：

```python
async def _load_source_context(...):
    if document.doc_type == "rfp":
        return await self._load_rfp_light_context(...)
    return await self._load_chunk_context(...)
```

### 3.2 支持未解析完成时的体验

如果 RFP 仍在 `parsing`：

- Requirement extraction API 可以返回 409，提示“需求文档仍在解析，请稍后重试”。
- 或基于项目表单字段先生成低置信度需求卡。

MVP 推荐：

- 如果项目 `description` 不为空，允许先生成需求卡。
- `confidence=0.55`
- `source_refs=[]`
- `missing_items` 标记“RFP 解析未完成，需复核”。

## Phase 4：RFP 后续知识抽取隔离

### 4.1 新增 RFP knowledge job

新增 job type：

```text
rfp_knowledge_extract
```

队列：

```text
maintenance
```

触发方式：

- RFP light parse 成功后可选择性提交。
- 默认建议关闭，等主流程稳定后再启用。

配置：

```env
RFP_KNOWLEDGE_EXTRACT_ENABLED=false
```

### 4.2 输出隔离

RFP knowledge 只能写：

- `draft_wiki`
- `rfp_requirement_facts`
- project-scoped knowledge

不得写入：

- historical case library
- global visual index
- `published_wiki`
- historical reusable blocks

必须携带 metadata：

```json
{
  "source_doc_type": "rfp",
  "project_id": "...",
  "knowledge_scope": "project_requirement",
  "publishable": false
}
```

## Phase 5：历史文档解析超时与观测

### 5.1 阶段进度

`_run_document_parse_job()` 和 RFP light parse job 都要写 `job.output_ref.progress.stage`。

历史解析阶段建议：

- `materializing_file`
- `parsing_document`
- `chunking`
- `embedding`
- `writing_vectors`
- `extracting_assets`
- `writing_assets`
- `refreshing_library`
- `completed`

RFP 轻量解析阶段：

- `materializing_file`
- `extracting_text`
- `saving_requirement_text`
- `completed`

### 5.2 阶段耗时

`output_ref.progress.timings`：

```json
{
  "materializing_file": 0.2,
  "extracting_text": 3.4,
  "saving_requirement_text": 0.1
}
```

### 5.3 超时策略

RFP light parse：

- 默认 60 秒硬上限。
- 超时后标记 `parse_insufficient`。
- 不继续占用 worker。

历史 document parse：

- 保留较长超时。
- 但只能占用 `library_parse` worker。
- 单任务失败不能影响队列继续处理后续材料。

### 5.4 排查 70 分钟案例

需要用服务器上的文档复测：

- `攀钢永磁电机改造节能分析.pdf`
- document id: `54bb3a09-4cb1-46f8-837d-ea9b5c87df6a`
- 原 job id: `3a9aa3f7-e38c-4e44-8e37-484e42900012`

复测目标：

- RFP light parse 不超过 60 秒。
- 不进入历史解析链路。
- 不写 Qdrant。
- 不产生 figure assets。

## Phase 6：前端交互修正

文件：

- `frontend/src/app/projects/[id]/documents/page.tsx`

调整：

- 页面标题从 `Documents` 改为 `项目需求文档` 或 i18n key。
- 上传按钮从 `Upload RFP` 改为 `上传需求文档`。
- 描述从“Documents are automatically parsed into intelligent chunks...”改为“用于抽取项目需求，不进入历史方案库”。
- 移除项目文档页对 `/documents/history-library/status` 的轮询。
- 状态文案：
  - `parsing` -> `需求解析中`
  - `done` -> `需求解析完成`
  - `parse_insufficient` -> `需求解析不充分`
  - `failed` -> `需求解析失败`

保留历史方案库入口，但文案明确：

- “历史方案库”是独立入口。
- 项目 RFP 上传不会进入历史方案库。

## Phase 7：测试计划

### Unit Tests

新增：

- `backend/tests/test_rfp_light_parser.py`
- `backend/tests/test_task_queue_routing.py`
- `backend/tests/test_rfp_upload_flow.py`
- `backend/tests/test_requirement_service_rfp_context.py`

覆盖：

- RFP upload 不调用 `_parse_and_index_document()`。
- RFP light parse 不调用 embedder/qdrant/raw_document/figure asset。
- historical import 使用 `library_parse` queue。
- requirement service 优先读取 `rfp_text_excerpt`。
- RFP parsing 超时返回 `parse_insufficient`。

### Integration Tests

1. 上传 `.md` RFP：
   - 2 秒内返回 202。
   - job 进入 `interactive`。
   - parse_status 最终 done。
   - chunk_count/indexed_chunk_count/figure_asset_count 均为 0。

2. 上传 PDF RFP：
   - 轻量解析不超过 60 秒。
   - requirement card 能读取 RFP excerpt。

3. 同时启动历史库 rebuild：
   - `library_parse` running=1。
   - 上传 RFP 后 `interactive` 能立即运行。

4. 历史方案导入：
   - 仍完整解析。
   - 仍生成 chunks/assets/vectors。
   - 不受 RFP light parse 改动影响。

### Server Validation

在服务器验证：

```bash
curl /api/v1/jobs/queue
```

应看到：

- `interactive`
- `library_parse`
- `maintenance`

复测文档：

- `攀钢永磁电机改造节能分析.pdf`

验收：

- 不再出现 70 分钟 RFP parse。
- 主流程页面不会一直等待历史解析。

## 实施顺序

1. 改造 `task_queue.py`，支持多队列。
2. 调整所有 queue submit 调用点。
3. 增加 `RfpLightParser`。
4. 增加 `_accept_rfp_light_upload()` 与 `_run_rfp_light_parse_job()`。
5. 修改 `upload_document()` 对 `doc_type=rfp` 分流。
6. 修改 `RequirementService` 读取 RFP light context。
7. 修改前端项目文档页文案和状态。
8. 增加单测。
9. 本地/服务器验证。
10. 再考虑是否启用 `rfp_knowledge_extract`。

## 风险与注意事项

- 不要直接删除旧 `_parse_and_index_document()`，历史方案库仍依赖它。
- 不要让 RFP 默认写 Qdrant，否则后续 evidence retrieval 可能把当前 RFP 当历史参考材料。
- 不要让 RFP 直接进入 published wiki。
- 如果实现过程中为了兼容旧数据保留 RFP chunks fallback，必须只读，不应新增重入库。
- 队列拆分后，recover startup 逻辑也要按 job type 投递回正确队列。
- 前端不要再把 RFP 解析状态和 history library refresh 混合展示。

## MVP 完成定义

- Project RFP 上传接口 2 秒内返回。
- 4-5MB PDF RFP 轻量解析目标 30 秒内，硬上限 60 秒。
- 历史文档解析/rebuild 运行时，不阻塞 requirement/retrieval/draft。
- RFP 不产生 historical raw_document、case library entry、visual index asset。
- 需求卡抽取能使用 RFP 文本摘要。
- `/jobs/queue` 能清楚展示三类 worker 的运行状态。
