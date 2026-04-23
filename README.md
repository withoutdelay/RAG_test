# Presale Copilot

面向工业电气售前场景的方案生成系统。系统把客户需求、真实历史方案、图表资产和质量门禁串成一条端到端链路，目标不是“凭空写一份方案”，而是“优先复用相似历史方案中的高价值工程内容，再用 LLM 做受控改写、补齐和审校”。

当前版本：`2.3.0`

## Super Dev 使用入口

本仓库已经接入 Super Dev 工作流，默认宿主是 Codex CLI。

安装与升级参考：

- `pip install -U super-dev`
- `uv tool install super-dev`
- `super-dev update`

在支持 slash command 的宿主中，可使用 `/super-dev` 触发；在当前 Codex CLI 仓库中，优先使用：

- `super-dev: 你的需求`

本仓库还提供了宿主侧本地质量/发布门禁入口，优先用于交付前自检：

- `super-dev host release-gate`
- `super-dev host project-replay-gate`
- `super-dev host quality-smoke`

执行 `super-dev host release-gate` 后，会在 `output/` 下落地：

- `RAG_test-release-gate.md`
- `RAG_test-release-gate.json`

如果仓库里已有最新的 `output/RAG_test-project-replay-eval.json`，`release-gate` / `project-replay-gate` 会自动复用其中的 `project_id`；也可以显式传入：

- `super-dev host release-gate --project-id <uuid>`

如果是从空白想法开始，也可先用：

- `super-dev start --idea "你的需求"`

文档入口：

- [Quickstart](docs/QUICKSTART.md)
- [Host Usage Guide](docs/HOST_USAGE_GUIDE.md)
- [Workflow Guide](docs/WORKFLOW_GUIDE.md)
- [Product Audit](docs/PRODUCT_AUDIT.md)

当前 MVP 的核心路线是 `reuse-first`：

```text
需求文档/人工输入
  -> 需求结构化
  -> 历史方案证据检索
  -> 大纲生成与人工确认
  -> 按章节检索可复用内容和图表资产
  -> LLM 受控成稿
  -> 章节质量审查与自动修复
  -> 项目级校验
  -> Markdown 导出
```

## 项目定位

这个项目服务于售前工程师编写技术方案的过程，尤其是“已有大量真实历史方案可复用”的行业项目。

典型输入：

- 客户 RFP、招标文件、需求说明、技术附件。
- 公司历史投标方案、成套技术方案、设备配置说明、图纸截图、参数表。
- 售前工程师补充的项目名称、行业、产品线、约束条件。

典型输出：

- 结构化需求卡片。
- 可追溯的 evidence bundle。
- 可编辑的方案大纲。
- 按章节生成的技术方案草稿。
- 推荐引用的历史段落、图、表、公式资产。
- 项目级校验报告和人工 review task。
- 可导出的 Markdown 技术方案。

## 当前 MVP 范围

已重点维护的主链路是 `composition/artifacts` 管线，对应 API 在 `backend/app/api/artifacts.py`：

1. `POST /api/v1/projects/{project_id}/extract-requirement`
2. `POST /api/v1/projects/{project_id}/retrieve-evidence`
3. `POST /api/v1/projects/{project_id}/generate-outline`
4. `POST /api/v1/projects/{project_id}/generate-sections`
5. `POST /api/v1/projects/{project_id}/validate`
6. `POST /api/v1/projects/{project_id}/export`

旧的 `/api/v1/generation/*` 和 legacy review 路由默认关闭，仅作为兼容或调试入口保留。当前质量优化和产品演示都应围绕 artifacts 主链路。

## 总体架构

```text
┌────────────────────────────────────────────────────────────────────┐
│ Frontend: Next.js                                                  │
│ 项目列表 / 文档上传 / 需求卡 / Evidence / Outline / Draft / Export │
└───────────────────────────────┬────────────────────────────────────┘
                                │ HTTP JSON
┌───────────────────────────────▼────────────────────────────────────┐
│ Backend: FastAPI                                                   │
│ Projects / Documents / Retrieval / Artifacts / Validation / Export │
│                                                                    │
│  Parsing Layer       PDF/DOCX/MD -> Markdown / chunk / asset        │
│  Vector Layer        embedding -> Qdrant hybrid retrieval           │
│  Case Library        历史方案目录和章节级复用索引                  │
│  Composition Layer   requirement/evidence/outline/section draft     │
│  Quality Layer       section quality gate + project validation      │
│  Export Layer        Markdown export + optional holistic finalize   │
└───────────────┬─────────────┬──────────────┬─────────────┬─────────┘
                │             │              │             │
        ┌───────▼──────┐ ┌────▼────┐ ┌───────▼──────┐ ┌────▼─────┐
        │ PostgreSQL   │ │ Qdrant  │ │ MinIO/local  │ │ Redis    │
        │ 业务状态库   │ │ 向量库  │ │ 文档/图片存储│ │ Gateway  │
        └──────────────┘ └─────────┘ └──────────────┘ └──────────┘
                                │
┌───────────────────────────────▼────────────────────────────────────┐
│ Gateway: FastAPI                                                   │
│ 可逆脱敏、还原、审计；用于 LLM 调用前后的敏感信息保护              │
└────────────────────────────────────────────────────────────────────┘
```

## 仓库结构

```text
backend/                 FastAPI 后端、SQLAlchemy 模型、RAG/生成/校验逻辑
backend/app/api/         API 路由
backend/app/models/      PostgreSQL 数据模型
backend/app/services/    解析、检索、生成、校验、导出等业务服务
backend/scripts/         历史样本入库、case library 构建、PDF 审计等脚本
frontend/                Next.js 16 + React 19 前端
frontend/src/app/        App Router 页面
frontend/src/components/ 编辑器、布局、UI 组件
frontend/src/lib/        API client 和类型
gateway/                 可逆脱敏网关
output/                  设计文档、质量计划、访谈模板、交付记录
private_samples/         本地私有样本文档说明和放置位置
scripts/                 本地启动和服务器部署脚本
docker-compose.yml       本地开发依赖和服务编排
docker-compose.prod.yml  生产部署编排
```

## 核心组件

### Frontend

前端在 `frontend/`，使用 Next.js App Router、React 19、Zustand、shadcn 风格组件、Tiptap 编辑能力和 `sonner` toast。

主要页面：

- `frontend/src/app/projects/page.tsx`：项目列表和新建项目。
- `frontend/src/app/projects/[id]/documents/page.tsx`：项目文档上传与解析状态。
- `frontend/src/app/projects/[id]/requirement/page.tsx`：需求卡查看与确认。
- `frontend/src/app/projects/[id]/evidence/page.tsx`：证据包、历史方案候选、检索结果。
- `frontend/src/app/projects/[id]/outline/page.tsx`：大纲生成、编辑、批准。
- `frontend/src/app/projects/[id]/editor/page.tsx`：章节 draft 查看、引用、资产、重生成、编辑。
- `frontend/src/app/projects/[id]/validation/page.tsx`：项目级校验报告和 review task。
- `frontend/src/app/projects/[id]/export/page.tsx`：导出结果。

前端 API base URL 由 `NEXT_PUBLIC_API_BASE_URL` 控制，默认是 `http://127.0.0.1:8000/api/v1`。

### Backend

后端在 `backend/`，使用 FastAPI、SQLAlchemy async、Alembic、Pydantic Settings。

主要 API 模块：

- `projects.py`：项目 CRUD。
- `documents.py`：文档上传、解析、chunk、图表资产、资产搜索。
- `retrieval.py`：底层向量检索搜索接口。
- `artifacts.py`：当前 MVP 主链路，负责需求、证据、大纲、章节、校验、导出。
- `generation.py` / `review.py`：legacy 路由，默认隐藏或关闭。

主要服务模块：

- `services/parsing/`：文档解析、清洗、表格/图片/公式资产处理、章节目录抽取、资产 LLM 审校和语义摘要。
- `services/vectorstore/`：chunk、embedding、Qdrant 客户端、索引过滤、chunk 质量判断。
- `services/retrieval/`：evidence 检索、case library 检索、图表资产检索。
- `services/requirement/`：需求抽取、缺失项和澄清项。
- `services/composition/`：大纲生成、章节生成、reuse-first 组装、章节质量门禁。
- `services/validation/`：项目级校验、错误/警告、review task。
- `services/export/`：导出 Markdown，保留快照。
- `services/llm/`：LLM provider 抽象、mock/live、路由、重试和网关脱敏接入。

### Gateway

`gateway/` 是独立 FastAPI 服务，用于 LLM 调用前后的敏感信息处理。

能力包括：

- `/mask`：识别敏感实体并替换成占位符。
- `/restore`：把模型输出中的占位符还原。
- 可选 Redis-backed mapping store。
- 审计日志和 stream-safe restoration helper。

后端通过 `GATEWAY_MASKING_ENABLED` 和 `GATEWAY_URL` 控制是否接入 gateway。

## 数据模型

核心业务表：

- `projects`：项目主表，保存当前需求卡、大纲、draft version 和项目状态。
- `documents`：上传文档记录，关联项目、存储路径、解析状态、文档类型。
- `raw_documents`：更接近真实文件的原始文档层，用于挂接图表资产和全局历史样本。
- `chunks`：文档文本块，保存 chunk 类型、正文、标题路径、Qdrant point id 和入库 metadata。
- `figure_assets`：图、表、公式等资产，保存页码、标题、caption、asset uri、reuse mode 和资产 metadata。
- `requirement_cards`：结构化需求卡，保存抽取内容、缺失项、blocking 澄清项和置信度。
- `evidence_bundles`：项目级证据包，保存查询、case candidates、检索结果和质量分。
- `proposal_outlines`：方案大纲，保存 LLM 生成或人工编辑后的 JSON 结构和批准状态。
- `section_drafts`：章节草稿，保存 Markdown 正文、引用、推荐资产、生成 trace 和质量结果。
- `validation_reports`：项目级校验报告，保存 errors、warnings、review tasks。
- `review_tasks`：需要人工确认的问题。
- `exports`：导出文件、Markdown 正文和导出快照。
- `jobs`：长任务或流程任务记录，保存输入输出引用、状态、trace id 和 generation summary。
- `audit_logs` / `masking_audit_logs`：后端和脱敏网关审计记录。

## 文档入库逻辑

文档上传入口是：

```text
POST /api/v1/projects/{project_id}/documents/upload
```

处理流程：

1. `ParserService` 根据 `PARSER_BACKEND` 选择解析后端。
2. `DoclingParser` 或 fallback parser 把 PDF/DOCX/Markdown 转成 Markdown、结构信息和原生 assets。
3. `markdown_cleaner` 清理目录噪声、页眉页脚、异常 Markdown。
4. `TableParser` 提取 Markdown 表格。
5. `ImageExtractor` 补充从原始文件中抽取的图片。
6. `AssetReviewService` 可选调用 LLM/vision 审校资产，过滤 logo、装饰图、低价值截图等误召回风险。
7. `AssetSemanticSummaryService` 可选为图资产生成语义摘要，用于后续检索排序。
8. `DocumentProfile` 生成文档画像，用于区分方案、清单、说明书、低价值附件等。
9. `Chunker` 按更大的章节/块粒度切分正文，默认避免把表格拆得过细。
10. `SafeIngestionFilter` 判断 chunk 是否可入向量库，低价值表格或高风险内容可只保留为资产。
11. `Embedder` 生成向量，`QdrantService` 写入 Qdrant。
12. 文档原文和图片通过 MinIO 或本地 fallback 存储。
13. `FigureAsset` 保存图、表、公式资产，并把 heading、caption、context、bbox、semantic summary 等写入 metadata。

表格策略：

- 可索引的文本/表格会进入 `chunks` 和 Qdrant。
- 过大或不适合直接向量化的表格会作为 `table asset` 保留，状态为 `pending_reconstruction`。
- `/documents/{document_id}/table-assets/{asset_id}/reconstruct` 目前是预留队列入口，用于后续表格重构能力。

## 历史方案与 Case Library

系统除了在线 Qdrant 检索，还维护一层轻量 case library：

- `backend/data/case_library/outline_library.json`：历史方案的目录和章节结构索引。
- `backend/data/case_library/block_library.json`：历史方案的章节块和可复用段落索引。

相关脚本：

- `make sample-manifest PATHS=/path/to/samples WITH_PROFILE=1`
- `make case-library MANIFEST=backend/data/sample_manifests/sample_manifest.json`
- `make historical-corpus MANIFEST=backend/data/sample_manifests/sample_manifest.json RECREATE_COLLECTION=1`

case library 主要用于：

- 在项目级 evidence retrieval 中先找相似历史方案。
- 在章节生成时按标题、章节家族、设备类型、工程语义寻找相似章节。
- 当向量检索为空时，提供 case fallback，避免项目级 evidence bundle 被误判为空。

## Reuse-First 生成逻辑

当前章节生成主实现是 `SectionDraftService`，位于：

```text
backend/app/services/composition/section_service.py
```

每一章的处理逻辑：

1. 读取已批准的大纲、需求卡和最新 evidence bundle。
2. 根据章节标题、purpose、generation mode、产品线、设备类型构造章节级复用查询。
3. 从 case library 中做 `section shortlist`，优先匹配标题、标题家族和相似章节路径。
4. 从命中的章节内挑选可复用 block，而不是全局随机拿 chunk。
5. 补充邻近 block，但优先保持在同章节或同章节族内。
6. 调用 `AssetRetrievalService` 回填图、表、公式资产。
7. 生成 `reuse_pack`，包含 reusable blocks、recommended assets、参数候选、禁用词、替换提示、风险标记。
8. 根据 token 预算选择装配模式：
   - `full_section`：高置信、整章可控时，把更完整的历史章节材料提供给 LLM。
   - `section_pack`：默认模式，提供候选章节摘要、精选 block 和必要资产。
   - `baseline_fallback`：复用证据不足时，退回普通生成。
9. 先组装可复用材料，再强制走一次 LLM 成稿，避免只复制旧段落或只返回拼接内容。
10. 写入 `SectionDraft.content_md`、`citation_refs`、`recommended_assets`、`reuse_pack` 和 `generation_details`。
11. 调用章节质量门禁做 deterministic check 和必要的 LLM repair。
12. 在 `Job.output_ref.generation_summary` 中记录章节生成摘要、fallback 比例、effective path、retrieval mode、refinement 状态等观测信息。

这条路线的关键原则：

- 先找相似真实方案，再写新方案。
- 技术章节优先复用高密度工程内容。
- 图表资产不是事后展示，而是参与章节生成和引用。
- LLM 的职责是受控改写、参数替换、去旧客户痕迹、补桥接语，而不是自由发挥。

## 图表资产检索逻辑

图表资产由 `FigureAsset` 承载，检索服务在：

```text
backend/app/services/retrieval/asset_service.py
```

检索信号包括：

- 章节标题、purpose、产品线、设备类型。
- 资产标题、caption、OCR/semantic summary、上下文段落。
- 所属文档、页码、heading path、source section。
- 资产类型：`figure`、`table`、`formula` 等。
- 资产质量标记、noise penalty、taxonomy boost、anchor boost。

资产在前端章节编辑器中以卡片展示，可打开原始图片内容：

```text
GET /api/v1/assets/{asset_id}/content
```

当前策略会尽量降低公司 logo、封面图、装饰图被当作工程方案图召回的概率；如果开启 LLM vision 审校，还会进一步对资产用途、工程含义、关键部件和复用风险进行判断。

## 质量门禁

质量门禁分两层。

章节级：

- 实现在 `backend/app/services/composition/section_quality.py`。
- 检查标题质量、prompt 泄露、内部标签残留、空洞内容、参数占位、资产占位、隐式待确认、表格物化等问题。
- 对可修复问题可调用 LLM 做一次 repair。
- 章节状态可能是 `generated`、`review_required`、`edited` 等。

项目级：

- 实现在 `backend/app/services/validation/service.py`。
- 校验 requirement、evidence bundle、outline、section drafts、citations、assets、review tasks。
- 生成 `ValidationReport`，包含 errors、warnings 和待人工处理事项。
- 项目只有通过关键门禁后才适合导出或交付给用户查看。

## LLM Provider 逻辑

LLM 抽象在：

```text
backend/app/services/llm/client.py
```

支持模式：

- `LLM_PROVIDER_BACKEND=mock`：测试和离线开发模式。
- `LLM_PROVIDER_BACKEND=live`：真实模型调用。

支持 provider：

- DeepSeek：`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`
- Qwen：`QWEN_API_KEY`、`QWEN_BASE_URL`、`QWEN_MODEL`
- Doubao：`DOUBAO_API_KEY`、`DOUBAO_BASE_URL`、`DOUBAO_MODEL`
- Azure OpenAI：`AZURE_OPENAI_API_KEY`、`AZURE_OPENAI_ENDPOINT`、`AZURE_OPENAI_DEPLOYMENT`
- OpenAI-compatible relay：`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`

当前 routing table 按任务类型选择模型，典型任务包括：

- `EXTRACTION`：需求抽取。
- `OUTLINE`：大纲生成。
- `SECTION_WRITE`：章节成稿。
- `SECTION_QUALITY`：章节审查与修复。
- `ASSET_REVIEW`：图表资产审校。
- `ASSET_SUMMARY`：图语义摘要生成。
- `HOLISTIC`：导出前全文融合增强。
- `REWRITE`：局部改写。

生产环境如果最终选择阿里千问，应优先评估 DashScope SDK 对 OCR、文件理解和 vision-heavy asset enrichment 的支持；开发阶段可以继续使用 OpenAI-compatible relay。

## 主要 API

项目：

- `POST /api/v1/projects`
- `GET /api/v1/projects`
- `GET /api/v1/projects/{project_id}`
- `DELETE /api/v1/projects/{project_id}`

文档与资产：

- `POST /api/v1/projects/{project_id}/documents/upload`
- `GET /api/v1/projects/{project_id}/documents`
- `GET /api/v1/documents/{document_id}`
- `POST /api/v1/documents/{document_id}/reparse`
- `DELETE /api/v1/documents/{document_id}`
- `GET /api/v1/documents/{document_id}/chunks`
- `GET /api/v1/documents/{document_id}/figure-assets`
- `GET /api/v1/documents/{document_id}/table-assets`
- `GET /api/v1/assets/{asset_id}/content`
- `POST /api/v1/projects/{project_id}/assets/search`

Artifacts 主流程：

- `POST /api/v1/projects/{project_id}/extract-requirement`
- `GET /api/v1/projects/{project_id}/requirement-card/latest`
- `PATCH /api/v1/projects/{project_id}/requirement-card/{card_id}`
- `POST /api/v1/projects/{project_id}/clarifications/{item_id}/resolve`
- `POST /api/v1/projects/{project_id}/retrieve-evidence`
- `GET /api/v1/projects/{project_id}/evidence-bundles/latest`
- `POST /api/v1/projects/{project_id}/generate-outline`
- `GET /api/v1/projects/{project_id}/outlines/latest`
- `PATCH /api/v1/projects/{project_id}/outlines/{outline_id}`
- `POST /api/v1/projects/{project_id}/outlines/{outline_id}/approve`
- `POST /api/v1/projects/{project_id}/generate-sections`
- `GET /api/v1/projects/{project_id}/sections`
- `POST /api/v1/projects/{project_id}/sections/{section_id}/regenerate`
- `PATCH /api/v1/projects/{project_id}/sections/{section_id}`
- `POST /api/v1/projects/{project_id}/validate`
- `GET /api/v1/projects/{project_id}/validation/latest`
- `GET /api/v1/projects/{project_id}/review-tasks`
- `POST /api/v1/projects/{project_id}/review-tasks/{task_id}/resolve`
- `POST /api/v1/projects/{project_id}/export`
- `GET /api/v1/projects/{project_id}/exports/latest`
- `GET /api/v1/jobs/{job_id}`

底层检索：

- `POST /api/v1/retrieval/search`

Gateway：

- `POST http://localhost:8001/mask`
- `POST http://localhost:8001/restore`
- `GET http://localhost:8001/health`

## 本地启动

首次安装：

```bash
python3 -m venv .venv
.venv/bin/pip install -e ./backend
npm --prefix frontend install
cp .env.example .env
```

如果需要真实 Docling、公式 OCR 或 sentence-transformers：

```bash
.venv/bin/pip install -e './backend[full]'
```

推荐一键启动：

```bash
make dev-up
```

修改 `.env` 或需要重启后端/前端：

```bash
make dev-restart
```

启动后访问：

- 前端：`http://127.0.0.1:3000/projects`
- 后端健康检查：`http://127.0.0.1:8000/health`
- 后端 OpenAPI：`http://127.0.0.1:8000/docs`
- 脱敏网关：`http://127.0.0.1:8001/docs`

手动启动方式：

```bash
docker compose up -d postgres redis qdrant minio gateway
make backend-migrate
make backend-run
npm --prefix frontend run dev -- --hostname 127.0.0.1 --port 3000
```

## 关键环境变量

基础：

- `APP_ENV=development|production`
- `DATABASE_URL=postgresql+asyncpg://...`
- `REDIS_URL=redis://...`
- `NEXT_PUBLIC_API_BASE_URL=http://127.0.0.1:8000/api/v1`

解析：

- `PARSER_BACKEND=auto|docling|fallback`
- `DOCLING_LIBREOFFICE_CMD=/absolute/path/to/soffice`
- `SAFE_INGESTION_ENABLED=true|false`
- `FORMULA_OCR_BACKEND=none|pix2tex`

图表资产 LLM 审校和摘要：

- `PARSER_LLM_ASSET_REVIEW_ENABLED=true|false`
- `PARSER_LLM_ASSET_REVIEW_USE_VISION=true|false`
- `PARSER_LLM_ASSET_SUMMARY_ENABLED=true|false`
- `PARSER_LLM_ASSET_SUMMARY_USE_VISION=true|false`

向量与 embedding：

- `QDRANT_HOST=localhost`
- `QDRANT_PORT=6333`
- `QDRANT_LOCATION=:memory:`：测试时使用内存 Qdrant。
- `QDRANT_COLLECTION=presale_knowledge`
- `EMBEDDING_BACKEND=auto|sentence-transformers|fallback`
- `EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5`
- `EMBEDDING_DIMENSION=1024`
- `EMBEDDING_LOCAL_FILES_ONLY=true|false`

LLM：

- `LLM_PROVIDER_BACKEND=mock|live`
- `LLM_TIMEOUT_SECONDS=60`
- `LLM_RETRY_ATTEMPTS=1`
- `OPENAI_API_KEY=...`
- `OPENAI_BASE_URL=...`
- `OPENAI_MODEL=...`
- `DOUBAO_API_KEY=...`
- `DOUBAO_BASE_URL=...`
- `DOUBAO_MODEL=...`
- `QWEN_API_KEY=...`
- `QWEN_BASE_URL=...`
- `QWEN_MODEL=...`

网关：

- `GATEWAY_URL=http://localhost:8001`
- `GATEWAY_MASKING_ENABLED=true|false`
- `REDIS_ENABLED=true|false`

导出：

- `VALIDATION_REQUIRE_FINAL_REVIEW=true|false`
- `EXPORT_HOLISTIC_FINALIZATION_ENABLED=true|false`
- `EXPORT_HOLISTIC_FINALIZATION_MODE=section|document`

## 测试

常用回归：

```bash
make phase2-test
make phase2-test-api
make phaseb-test
make phasec-test
make phased-test
make phasee-test
make phasev2-test-api
make phase4-test
```

完整 MVP 主链路通常至少跑：

```bash
make phase2-test phase2-test-api phaseb-test phasec-test phased-test phasee-test phasev2-test-api phase4-test
```

Gateway：

```bash
make phase3-test
```

前端：

```bash
npm --prefix frontend run lint
npm --prefix frontend run build
```

## 部署

生产部署使用：

```bash
./scripts/deploy.sh
```

常用命令：

```bash
./scripts/deploy.sh restart
./scripts/deploy.sh status
./scripts/deploy.sh logs
./scripts/deploy.sh down
```

部署前需要在服务器 `.env` 中设置：

- `APP_ENV=production`
- `NEXT_PUBLIC_API_BASE_URL` 为外部浏览器可访问的后端地址。
- 真实数据库、对象存储、LLM provider 和模型配置。
- 如需在容器内启用 Docling / sentence-transformers，设置 `BACKEND_EXTRAS=full`。

部署脚本默认阻止 `NEXT_PUBLIC_API_BASE_URL=localhost` 或 `127.0.0.1`，因为这会导致外部浏览器访问失败。仅同机部署测试时可使用：

```bash
DEPLOY_ALLOW_LOCAL_API=1 ./scripts/deploy.sh
```

## 常见问题

### 打开 draft 时前端 toast 网络错误

先看后端日志：

```bash
tail -n 200 .run/backend.log
```

如果 `/sections` 或 `/evidence-bundles/latest` 返回 500，通常是后端依赖初始化、数据库、embedding 或 LLM provider 问题。查看已有 draft 理论上不应触发 embedding；如果仍发生，优先检查最近改动是否让只读接口提前构造了检索服务。

### sentence-transformers 本地模型加载失败

开发阶段可临时使用轻量 fallback：

```text
EMBEDDING_BACKEND=fallback
EMBEDDING_DIMENSION=1024
```

修改 `.env` 后执行：

```bash
make dev-restart
```

真实检索效果验证仍建议使用生产一致的 embedding 模型和完整历史语料入库。

### 改了 `.env` 但没有生效

本地开发脚本支持重启并重新加载 `.env`：

```bash
make dev-restart
```

### curl 本机接口返回代理错误

如果本机 shell 配了代理，访问 `127.0.0.1` 时可绕过代理：

```bash
curl --noproxy '*' http://127.0.0.1:8000/health
```

## 重要设计文档

- `reuse_first_rag_technical_route.md`：reuse-first 总体技术路线。
- `output/reuse-first-architecture.md`：章节真值层、section shortlist、block selection、asset backfill 架构。
- `output/reuse-first-quality-next-plan.md`：MVP 质量优化计划和已完成状态。
- `section_truth_and_heading_retrieval_plan.md`：章节标题和章节真值检索规划。
- `asset_retrieval_phase4_design.md`：图表资产检索设计。
- `pdf_parsing_optimization_roadmap.md`：PDF 解析优化路线。
- `output/val103-sales-interview-guide.md`：售前相似方案判断访谈提纲。
- `output/similar-proposal-judgement-template.md`：相似方案判断表模板。

## 当前工程约束

- 当前主链路是 artifacts/composition，不再优先演进 legacy generation workflow。
- 历史方案复用效果高度依赖真实样本文档、case library 和向量库是否同步重建。
- 图表资产质量依赖 PDF 解析质量；复杂图纸、扫描件和低清图片需要 vision/OCR 能力补强。
- 开发阶段可以使用 relay 或 mock provider，但演示前应确认 `LLM_PROVIDER_BACKEND=live`、API key、模型、超时和重试参数均可用。
- 质量门禁不是最终人工审查替代品。系统会自动修复一部分低风险问题，但 `review_required` 仍代表需要售前工程师确认。
