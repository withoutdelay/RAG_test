# 售前智能系统 MVP — 工程实现规格说明书 (SPEC)

> **用途：** 本文档是面向 AI 编码工具（Cursor / Claude Code / Antigravity）及后端工程师的**可执行规格书**。  
> **约定：** 所有代码使用 Python 3.11+；前端使用 TypeScript + React；数据库统一 UTF-8。

---

## 1. 项目目录结构

```
presale-copilot/
├── docker-compose.yml              # 全栈本地开发环境
├── .env.example                     # 环境变量模板
├── README.md
│
├── backend/                         # FastAPI 后端（主服务）
│   ├── Dockerfile
│   ├── pyproject.toml               # 依赖管理 (Poetry/uv)
│   ├── alembic/                     # 数据库迁移
│   │   ├── alembic.ini
│   │   └── versions/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI 入口, CORS, 生命周期
│   │   ├── config.py                # pydantic-settings 配置
│   │   │
│   │   ├── api/                     # 路由层
│   │   │   ├── __init__.py
│   │   │   ├── router.py            # 总路由注册
│   │   │   ├── projects.py          # 项目管理 CRUD
│   │   │   ├── documents.py         # 文档上传 & 解析
│   │   │   ├── retrieval.py         # 知识检索
│   │   │   ├── generation.py        # 方案生成 & SSE 流
│   │   │   └── review.py            # 人机审批
│   │   │
│   │   ├── models/                  # SQLAlchemy ORM 模型
│   │   │   ├── __init__.py
│   │   │   ├── project.py
│   │   │   ├── document.py
│   │   │   ├── chunk.py
│   │   │   ├── generation_task.py
│   │   │   └── audit_log.py
│   │   │
│   │   ├── schemas/                 # Pydantic 请求/响应 Schema
│   │   │   ├── __init__.py
│   │   │   ├── project.py
│   │   │   ├── document.py
│   │   │   ├── retrieval.py
│   │   │   ├── generation.py
│   │   │   └── review.py
│   │   │
│   │   ├── services/                # 业务逻辑层
│   │   │   ├── __init__.py
│   │   │   ├── parsing/             # M1 文档解析
│   │   │   │   ├── __init__.py
│   │   │   │   ├── parser.py        # 解析调度器
│   │   │   │   ├── docling_parser.py
│   │   │   │   ├── table_parser.py
│   │   │   │   └── image_extractor.py
│   │   │   │
│   │   │   ├── vectorstore/         # M2 向量存储 & 检索
│   │   │   │   ├── __init__.py
│   │   │   │   ├── embedder.py      # Embedding 封装
│   │   │   │   ├── chunker.py       # 智能分片
│   │   │   │   ├── qdrant_client.py # Qdrant 操作
│   │   │   │   └── retriever.py     # 混合检索
│   │   │   │
│   │   │   ├── llm/                 # M4 LLM 推理层
│   │   │   │   ├── __init__.py
│   │   │   │   ├── client.py        # 统一 LLM Client
│   │   │   │   ├── router.py        # 模型路由策略
│   │   │   │   └── prompts/         # Prompt 模板
│   │   │   │       ├── outline.py
│   │   │   │       ├── section.py
│   │   │   │       ├── holistic.py
│   │   │   │       └── rewrite.py
│   │   │   │
│   │   │   └── agents/              # M5 智能体工作流
│   │   │       ├── __init__.py
│   │   │       ├── workflow.py      # DAG 工作流引擎
│   │   │       ├── planner.py       # 大纲智能体
│   │   │       ├── retriever.py     # 检索智能体
│   │   │       ├── executor.py      # 专注智能体
│   │   │       ├── holistic.py      # 融合智能体
│   │   │       └── state.py         # 全局状态管理
│   │   │
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── object_storage.py    # MinIO/OSS 封装
│   │       └── export.py            # Markdown → Word/PDF
│   │
│   └── tests/
│       ├── test_parsing.py
│       ├── test_retrieval.py
│       ├── test_gateway.py
│       └── test_agents.py
│
├── gateway/                         # M3 可逆数据脱敏网关（独立服务）
│   ├── Dockerfile
│   ├── pyproject.toml
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                  # FastAPI 入口
│   │   ├── config.py
│   │   ├── detector.py              # NER + 正则敏感探测
│   │   ├── masker.py                # 占位符映射引擎
│   │   ├── restorer.py              # 响应还原
│   │   ├── mapping_store.py         # Redis 映射表管理
│   │   ├── presidio_config.py       # Presidio 中文规则配置
│   │   └── schemas.py               # 请求/响应 Schema
│   └── tests/
│       ├── test_detector.py
│       ├── test_masker.py
│       └── test_restorer.py
│
└── frontend/                        # M6 前端
    ├── Dockerfile
    ├── package.json
    ├── tsconfig.json
    ├── next.config.js
    ├── public/
    ├── src/
    │   ├── app/                     # Next.js App Router
    │   │   ├── layout.tsx
    │   │   ├── page.tsx             # 首页 / 项目列表
    │   │   ├── projects/
    │   │   │   └── [id]/
    │   │   │       ├── page.tsx     # 项目详情
    │   │   │       ├── upload/
    │   │   │       │   └── page.tsx # RFP 上传
    │   │   │       ├── outline/
    │   │   │       │   └── page.tsx # 大纲编辑
    │   │   │       ├── editor/
    │   │   │       │   └── page.tsx # Block 编辑器
    │   │   │       └── export/
    │   │   │           └── page.tsx # 导出预览
    │   │   └── globals.css
    │   │
    │   ├── components/
    │   │   ├── layout/
    │   │   │   ├── Sidebar.tsx
    │   │   │   └── Header.tsx
    │   │   ├── upload/
    │   │   │   └── FileUploader.tsx
    │   │   ├── outline/
    │   │   │   ├── OutlineTree.tsx
    │   │   │   └── OutlineNode.tsx
    │   │   ├── editor/
    │   │   │   ├── BlockEditor.tsx       # 块状编辑器主体
    │   │   │   ├── SectionBlock.tsx      # 单 Block 组件
    │   │   │   ├── InlineCommandBar.tsx  # 内联指令面板
    │   │   │   ├── SourceCitation.tsx    # 引用溯源
    │   │   │   └── ReviewCard.tsx        # 审批断点卡片
    │   │   ├── status/
    │   │   │   └── AgentProgress.tsx     # Agent 执行进度
    │   │   └── common/
    │   │       ├── Button.tsx
    │   │       ├── Modal.tsx
    │   │       └── Toast.tsx
    │   │
    │   ├── hooks/
    │   │   ├── useSSE.ts            # SSE 流式数据 Hook
    │   │   ├── useProject.ts
    │   │   └── useGeneration.ts
    │   │
    │   ├── lib/
    │   │   ├── api.ts               # Axios 封装
    │   │   └── types.ts             # 共享 TypeScript 类型
    │   │
    │   └── stores/
    │       ├── projectStore.ts      # Zustand 项目状态
    │       └── editorStore.ts       # Zustand 编辑器状态
    │
    └── tests/
```

---

## 2. 环境变量清单 (`.env.example`)

```bash
# ============== 通用 ==============
APP_ENV=development                   # development | staging | production
LOG_LEVEL=INFO
SECRET_KEY=change-me-to-random-string

# ============== 数据库 ==============
DATABASE_URL=postgresql+asyncpg://copilot:copilot@localhost:5432/copilot_db

# ============== Redis ==============
REDIS_URL=redis://localhost:6379/0

# ============== 向量数据库 (Qdrant) ==============
QDRANT_HOST=localhost
QDRANT_PORT=6333
QDRANT_COLLECTION=presale_knowledge
EMBEDDING_MODEL=BAAI/bge-large-zh-v1.5
EMBEDDING_DIMENSION=1024

# ============== 对象存储 (MinIO) ==============
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=presale-documents

# ============== LLM API 密钥 ==============
# DeepSeek
DEEPSEEK_API_KEY=sk-xxxxx
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1
DEEPSEEK_MODEL=deepseek-chat

# Qwen (阿里云百炼)
QWEN_API_KEY=sk-xxxxx
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus

# Azure OpenAI (可选备用)
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_ENDPOINT=
AZURE_OPENAI_DEPLOYMENT=

# ============== 脱敏网关 ==============
GATEWAY_URL=http://localhost:8001
GATEWAY_MASKING_ENABLED=true

# ============== 前端 ==============
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1
```

---

## 3. Docker Compose 服务清单

```yaml
# docker-compose.yml
version: "3.9"

services:
  # ---- 数据底座 ----
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB: copilot_db
      POSTGRES_USER: copilot
      POSTGRES_PASSWORD: copilot
    ports: ["5432:5432"]
    volumes: [pg_data:/var/lib/postgresql/data]

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

  qdrant:
    image: qdrant/qdrant:v1.12.4
    ports: ["6333:6333", "6334:6334"]
    volumes: [qdrant_data:/qdrant/storage]

  minio:
    image: minio/minio:latest
    command: server /data --console-address ":9001"
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    ports: ["9000:9000", "9001:9001"]
    volumes: [minio_data:/data]

  # ---- 应用服务 ----
  gateway:
    build: ./gateway
    ports: ["8001:8001"]
    environment:
      REDIS_URL: redis://redis:6379/1
    depends_on: [redis]

  backend:
    build: ./backend
    ports: ["8000:8000"]
    env_file: .env
    environment:
      DATABASE_URL: postgresql+asyncpg://copilot:copilot@postgres:5432/copilot_db
      REDIS_URL: redis://redis:6379/0
      QDRANT_HOST: qdrant
      MINIO_ENDPOINT: minio:9000
      GATEWAY_URL: http://gateway:8001
    depends_on: [postgres, redis, qdrant, minio, gateway]

  frontend:
    build: ./frontend
    ports: ["3000:3000"]
    environment:
      NEXT_PUBLIC_API_BASE_URL: http://localhost:8000/api/v1
    depends_on: [backend]

volumes:
  pg_data:
  qdrant_data:
  minio_data:
```

---

## 4. 数据库 Schema (PostgreSQL)

```sql
-- ======================================================
-- 项目表：每个方案生成任务归属一个项目
-- ======================================================
CREATE TABLE projects (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            VARCHAR(255) NOT NULL,
    industry        VARCHAR(100),              -- 行业标签: '电气', '医疗', '金融'
    description     TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- ======================================================
-- 文档表：上传的 RFP / 历史方案原始文件
-- ======================================================
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    filename        VARCHAR(500) NOT NULL,
    file_type       VARCHAR(20) NOT NULL,      -- 'pdf', 'docx', 'doc', 'pptx'
    file_size_bytes BIGINT,
    storage_path    VARCHAR(1000) NOT NULL,     -- MinIO 对象路径
    doc_type        VARCHAR(50) NOT NULL,       -- 'rfp' | 'historical_proposal' | 'contract'
    parse_status    VARCHAR(20) DEFAULT 'pending',  -- 'pending' | 'parsing' | 'done' | 'failed'
    metadata        JSONB DEFAULT '{}',        -- { industry, year, amount_range, client_name }
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_documents_project ON documents(project_id);
CREATE INDEX idx_documents_status ON documents(parse_status);

-- ======================================================
-- 文本块表：解析后的分片，与向量库 point_id 关联
-- ======================================================
CREATE TABLE chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id     UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,          -- 块在文档中的序号
    chunk_type      VARCHAR(20) NOT NULL,      -- 'PLAIN' | 'TABLE' | 'IMAGE' | 'QUESTION' | 'METADATA'
    content         TEXT NOT NULL,             -- Markdown 格式内容
    token_count     INTEGER,
    heading_path    TEXT,                      -- 层次标题路径: '第3章 > 3.2 硬件配置 > 3.2.1 变频器'
    image_url       VARCHAR(1000),            -- chunk_type='IMAGE' 时指向 MinIO URL
    qdrant_point_id UUID,                     -- 关联 Qdrant 中的向量点 ID
    metadata        JSONB DEFAULT '{}',       -- 元数据 payload (同步至 Qdrant)
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_chunks_document ON chunks(document_id);
CREATE INDEX idx_chunks_type ON chunks(chunk_type);

-- ======================================================
-- 方案生成任务表：每次生成操作的状态追踪
-- ======================================================
CREATE TABLE generation_tasks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID REFERENCES projects(id) ON DELETE CASCADE,
    rfp_document_id UUID REFERENCES documents(id),
    status          VARCHAR(20) DEFAULT 'created',
                    -- 'created' | 'outlining' | 'generating' | 'reviewing' | 'completed' | 'failed'
    outline         JSONB,                    -- 大纲 JSON (Planner Agent 输出)
    global_params   JSONB DEFAULT '{}',       -- 全局业务参数 { project_amount, total_power, ... }
    sections        JSONB DEFAULT '[]',       -- 各章节生成状态 & 内容
    final_markdown  TEXT,                     -- 融合后终稿 Markdown
    total_tokens    INTEGER DEFAULT 0,        -- 累计 Token 消耗
    estimated_cost  DECIMAL(10, 4),           -- 预估费用 (元)
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);
CREATE INDEX idx_gen_tasks_project ON generation_tasks(project_id);
CREATE INDEX idx_gen_tasks_status ON generation_tasks(status);

-- ======================================================
-- 审批断点表：人机交互审批节点
-- ======================================================
CREATE TABLE review_points (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         UUID REFERENCES generation_tasks(id) ON DELETE CASCADE,
    section_index   INTEGER NOT NULL,          -- 对应大纲中的章节序号
    review_type     VARCHAR(30) NOT NULL,      -- 'image_confirm' | 'content_review' | 'param_verify'
    description     TEXT NOT NULL,             -- 提示给用户的审核说明
    payload         JSONB DEFAULT '{}',        -- { image_url, suggested_params, ... }
    status          VARCHAR(20) DEFAULT 'pending',  -- 'pending' | 'approved' | 'rejected' | 'revised'
    user_feedback   TEXT,                      -- 用户反馈/修改指令
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ
);

-- ======================================================
-- 脱敏审计日志表
-- ======================================================
CREATE TABLE masking_audit_logs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      VARCHAR(100) NOT NULL,     -- 脱敏会话 ID
    direction       VARCHAR(10) NOT NULL,      -- 'outbound' | 'inbound'
    entity_count    INTEGER,                   -- 本次脱敏/还原的实体数量
    entity_types    JSONB,                     -- ['PERSON', 'COMPANY', 'AMOUNT', ...]
    success         BOOLEAN DEFAULT TRUE,
    error_message   TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_audit_session ON masking_audit_logs(session_id);
```

---

## 5. 后端 API 接口定义

> **Base URL:** `http://localhost:8000/api/v1`  
> **通用响应包装：**

```json
{
  "code": 200,
  "message": "success",
  "data": { ... }
}
```

### 5.1 项目管理 `/projects`

```
POST   /projects                  创建项目
GET    /projects                  项目列表 (?page=1&size=20&industry=电气)
GET    /projects/{id}             项目详情
PUT    /projects/{id}             更新项目
DELETE /projects/{id}             删除项目
```

#### `POST /projects` — 创建项目

**请求体：**
```json
{
  "name": "2026年国网变电站智能化项目",
  "industry": "电气",
  "description": "110kV变电站综合自动化改造"
}
```

**响应体 (201)：**
```json
{
  "code": 201,
  "data": {
    "id": "uuid-xxx",
    "name": "2026年国网变电站智能化项目",
    "industry": "电气",
    "description": "110kV变电站综合自动化改造",
    "created_at": "2026-03-22T10:00:00Z"
  }
}
```

---

### 5.2 文档管理 `/documents`

```
POST   /projects/{pid}/documents/upload    上传文档 (multipart/form-data)
GET    /projects/{pid}/documents           文档列表
GET    /documents/{id}                     文档详情 (含解析状态)
POST   /documents/{id}/reparse             重新解析
DELETE /documents/{id}                     删除文档
GET    /documents/{id}/chunks              获取文档分片列表
```

#### `POST /projects/{pid}/documents/upload` — 上传文档

**请求：** `multipart/form-data`
| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `file` | binary | 是 | PDF/Word 文件 |
| `doc_type` | string | 是 | `rfp` \| `historical_proposal` \| `contract` |
| `metadata` | JSON string | 否 | `{"industry":"电气","year":2025,"amount_range":"500万以上"}` |

**响应体 (202 Accepted)：**
```json
{
  "code": 202,
  "data": {
    "id": "uuid-doc",
    "filename": "国网变电站方案.pdf",
    "parse_status": "pending",
    "message": "文档已接收，解析任务已入队"
  }
}
```

---

### 5.3 知识检索 `/retrieval`

```
POST   /retrieval/search           语义检索
```

#### `POST /retrieval/search` — 混合检索

**请求体：**
```json
{
  "query": "110kV变电站综合自动化方案中的硬件配置清单",
  "project_id": "uuid-xxx",
  "top_k": 10,
  "filters": {
    "industry": "电气",
    "year_gte": 2024,
    "chunk_type": ["PLAIN", "TABLE"],
    "doc_type": "historical_proposal"
  },
  "search_mode": "hybrid"
}
```

其中 `search_mode` 可选：
- `"vector"` — 纯向量语义检索
- `"keyword"` — BM25 关键词检索
- `"hybrid"` — 向量 + BM25 融合排序（默认）

**响应体 (200)：**
```json
{
  "code": 200,
  "data": {
    "results": [
      {
        "chunk_id": "uuid-chunk",
        "document_id": "uuid-doc",
        "document_name": "2024国网变电站方案.pdf",
        "heading_path": "第4章 > 4.2 硬件选型 > 4.2.1 变频器规格",
        "chunk_type": "TABLE",
        "content": "| 设备名称 | 型号 | 数量 | 单价(万元) |\n|---|---|---|---|\n| 变频器 | ABB ACS880 | 3 | 12.5 |",
        "score": 0.892,
        "metadata": { "industry": "电气", "year": 2024 }
      }
    ],
    "total": 10
  }
}
```

---

### 5.4 方案生成 `/generation`

```
POST   /generation/start                     启动方案生成
GET    /generation/{task_id}                  获取任务状态
GET    /generation/{task_id}/stream           SSE 流式输出
POST   /generation/{task_id}/outline/confirm  确认大纲
POST   /generation/{task_id}/sections/{idx}/rewrite  局部重写
POST   /generation/{task_id}/finalize         融合终稿
GET    /generation/{task_id}/export           导出 Word/PDF
```

#### `POST /generation/start` — 启动方案生成

**请求体：**
```json
{
  "project_id": "uuid-xxx",
  "rfp_document_id": "uuid-rfp",
  "instructions": "请以我们在电气行业的成功经验为基础，生成110kV变电站综合自动化技术方案",
  "global_params": {
    "project_name": "2026年国网变电站智能化项目",
    "total_budget": "800万",
    "total_power": "5000kW",
    "delivery_deadline": "2026-12-31"
  }
}
```

**响应体 (202)：**
```json
{
  "code": 202,
  "data": {
    "task_id": "uuid-task",
    "status": "outlining",
    "message": "大纲生成中，请通过 SSE 端点获取实时进度"
  }
}
```

#### `GET /generation/{task_id}/stream` — SSE 实时流

**SSE 事件类型：**

```
event: outline_ready
data: {"outline": {"title": "...", "sections": [...]}}

event: section_start
data: {"section_index": 0, "title": "项目概述"}

event: section_chunk
data: {"section_index": 0, "delta": "本项目旨在..."}

event: section_done
data: {"section_index": 0, "token_count": 1234}

event: review_required
data: {"review_point_id": "uuid", "section_index": 3, "type": "image_confirm", "description": "系统已插入历史电气架构图，新参数为5000kW，请确认"}

event: agent_status
data: {"agent": "executor", "section_index": 2, "status": "retrieving", "progress": 0.6}

event: generation_complete
data: {"task_id": "uuid-task", "total_tokens": 45000, "estimated_cost": 2.35}

event: error
data: {"message": "LLM API 调用超时，正在重试..."}
```

#### `POST /generation/{task_id}/sections/{idx}/rewrite` — 局部重写

**请求体：**
```json
{
  "instruction": "加入我们在金融行业的成功案例，并将语气改得更专业",
  "selected_text": "本系统采用常规架构设计..."
}
```

**响应体 (200)：**
```json
{
  "code": 200,
  "data": {
    "section_index": 2,
    "new_content": "本系统采用业界领先的分布式微服务架构...",
    "citations": [
      { "source": "2024招商银行核心系统方案.pdf", "heading": "3.1 架构设计" }
    ]
  }
}
```

---

### 5.5 审批管理 `/review`

```
GET    /generation/{task_id}/reviews              获取所有审批点
POST   /review/{review_id}/approve                审批通过
POST   /review/{review_id}/reject                 驳回并附反馈
```

#### `POST /review/{review_id}/approve`

**请求体：**
```json
{
  "feedback": "图纸确认无误，但请将功率参数更新为5500kW"
}
```

---

### 5.6 脱敏网关 API（独立服务 `:8001`）

```
POST   /mask       脱敏
POST   /restore    还原
GET    /health     健康检查
```

#### `POST /mask` — 数据脱敏

**请求体：**
```json
{
  "session_id": "uuid-session",
  "text": "甲方上海电气集团，联系人张三，合同金额500万元，身份证号310101199001011234",
  "entity_types": ["PERSON", "COMPANY", "AMOUNT", "ID_CARD", "PHONE"]
}
```

**响应体 (200)：**
```json
{
  "code": 200,
  "data": {
    "masked_text": "甲方[Company_A]，联系人[Person_1]，合同金额[Amount_1]，身份证号[ID_Card_1]",
    "entity_count": 4,
    "entities_detected": [
      { "original": "上海电气集团", "placeholder": "[Company_A]", "type": "COMPANY", "start": 2, "end": 8 },
      { "original": "张三", "placeholder": "[Person_1]", "type": "PERSON", "start": 12, "end": 14 },
      { "original": "500万元", "placeholder": "[Amount_1]", "type": "AMOUNT", "start": 18, "end": 22 },
      { "original": "310101199001011234", "placeholder": "[ID_Card_1]", "type": "ID_CARD", "start": 27, "end": 45 }
    ]
  }
}
```

#### `POST /restore` — 数据还原

**请求体：**
```json
{
  "session_id": "uuid-session",
  "text": "根据分析，[Company_A]与[Person_1]签订的[Amount_1]合同存在以下风险..."
}
```

**响应体 (200)：**
```json
{
  "code": 200,
  "data": {
    "restored_text": "根据分析，上海电气集团与张三签订的500万元合同存在以下风险...",
    "restored_count": 3
  }
}
```

---

## 6. 脱敏 Pipeline 数据流规约

```
                         企业内网                    │              云端
                                                    │
 ┌──────────┐    ┌──────────────────┐               │
 │  后端服务  │───▶│  脱敏网关 /mask   │               │
 │ (backend) │    │                  │               │
 │           │    │ 1. Presidio NER  │               │
 │ 原始文本:  │    │    探测敏感实体   │               │
 │ "甲方上海  │    │ 2. 生成占位符     │               │    ┌──────────────┐
 │  电气集团" │    │    Company_A     │──── HTTPS ────┼───▶│  LLM API      │
 │           │    │ 3. Redis 存储     │  (脱敏后文本)  │    │  (ZDR 协议)    │
 │           │    │    映射表         │               │    │              │
 └──────────┘    └────────┬─────────┘               │    │ 推理 & 生成   │
                          │                          │    └──────┬───────┘
                          │                          │           │
 ┌──────────┐    ┌────────▼─────────┐               │           │
 │  前端展示  │◀───│ 脱敏网关 /restore │◀──── HTTPS ────┼───────────┘
 │ (完整原文) │    │                  │  (含占位符文本)  │
 │           │    │ 4. 正则匹配占位符  │               │
 │           │    │ 5. 查 Redis 还原  │               │
 └──────────┘    └──────────────────┘               │
```

### 脱敏实体类型与占位符命名规则

| 实体类型 | Presidio Entity | 占位符格式 | 示例 |
|---------|-----------------|-----------|------|
| 人名 | `PERSON` | `[Person_N]` | `[Person_1]`, `[Person_2]` |
| 公司名 | `COMPANY` (自定义) | `[Company_N]` | `[Company_A]`, `[Company_B]` |
| 金额 | `AMOUNT` (自定义) | `[Amount_N]` | `[Amount_1]` |
| 身份证号 | `CN_ID_CARD` (自定义) | `[ID_Card_N]` | `[ID_Card_1]` |
| 手机号 | `PHONE_NUMBER` | `[Phone_N]` | `[Phone_1]` |
| 银行卡号 | `CREDIT_CARD` | `[BankCard_N]` | `[BankCard_1]` |
| 地址 | `LOCATION` | `[Address_N]` | `[Address_1]` |
| 项目代号 | `PROJECT_CODE` (自定义) | `[Project_N]` | `[Project_Alpha]` |

### Redis 映射存储结构

```
Key:    masking:session:{session_id}
Type:   Hash
TTL:    3600s (1 小时，可配置)
Fields:
  "[Company_A]"   → "上海电气集团"
  "[Person_1]"    → "张三"
  "[Amount_1]"    → "500万元"
  "[ID_Card_1]"   → "310101199001011234"
```

---

## 7. 向量数据库 Schema (Qdrant)

### Collection 创建

```python
from qdrant_client import QdrantClient
from qdrant_client.models import VectorParams, Distance

client = QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT)

client.create_collection(
    collection_name="presale_knowledge",
    vectors_config=VectorParams(
        size=1024,             # BGE-large-zh-v1.5 维度
        distance=Distance.COSINE
    )
)
```

### Point Payload 结构

每个向量点携带以下 Payload 字段（支持过滤）：

```json
{
  "document_id": "uuid-doc",
  "document_name": "2024国网变电站方案.pdf",
  "chunk_id": "uuid-chunk",
  "chunk_index": 15,
  "chunk_type": "TABLE",
  "heading_path": "第4章 > 4.2 硬件选型 > 4.2.1 变频器",
  "content": "| 设备名称 | 型号 | ...",
  "industry": "电气",
  "year": 2024,
  "amount_range": "500万以上",
  "doc_type": "historical_proposal",
  "image_url": null,
  "token_count": 256
}
```

### 检索过滤示例

```python
from qdrant_client.models import Filter, FieldCondition, MatchValue, Range

results = client.search(
    collection_name="presale_knowledge",
    query_vector=query_embedding,
    limit=10,
    query_filter=Filter(
        must=[
            FieldCondition(key="industry", match=MatchValue(value="电气")),
            FieldCondition(key="year", range=Range(gte=2024)),
            FieldCondition(key="doc_type", match=MatchValue(value="historical_proposal")),
        ]
    )
)
```

---

## 8. 智能体工作流状态机定义

### 工作流 DAG

```
               ┌─────────┐
               │  START   │
               └────┬─────┘
                    │
                    ▼
           ┌────────────────┐
           │ Planner Agent  │  输入: RFP 文本 + 全局参数
           │ (大纲生成)      │  输出: JSON 大纲
           └────────┬───────┘
                    │
                    ▼
        ┌───────────────────────┐
        │  HUMAN REVIEW POINT   │  用户确认/修改大纲
        │  (outline_confirm)    │
        └───────────┬───────────┘
                    │
          ┌─────────┴──────────┐
          ▼                    ▼         (并行 N 个章节)
  ┌───────────────┐    ┌───────────────┐
  │ Retriever [0] │    │ Retriever [N] │  按章节检索相关知识
  └───────┬───────┘    └───────┬───────┘
          ▼                    ▼
  ┌───────────────┐    ┌───────────────┐
  │ Executor  [0] │    │ Executor  [N] │  逐章节生成 Markdown
  └───────┬───────┘    └───────┬───────┘
          │                    │
          ▼                    ▼
  ┌───────────────────────────────────┐
  │   HUMAN REVIEW POINTS (可选)      │  图片确认、参数核验
  └───────────────┬───────────────────┘
                  │
                  ▼
          ┌───────────────┐
          │Holistic Agent │  全文融合 + 一致性校验 + 润色
          └───────┬───────┘
                  │
                  ▼
             ┌────────┐
             │  DONE   │  终稿就绪
             └────────┘
```

### 工作流状态枚举

```python
from enum import Enum

class WorkflowStatus(str, Enum):
    CREATED      = "created"         # 任务已创建
    OUTLINING    = "outlining"       # Planner 正在生成大纲
    OUTLINE_REVIEW = "outline_review"  # 等待用户确认大纲
    GENERATING   = "generating"      # Executor 正在逐章节生成
    SECTION_REVIEW = "section_review"  # 等待用户审批断点
    FINALIZING   = "finalizing"      # Holistic 正在融合终稿
    COMPLETED    = "completed"       # 已完成
    FAILED       = "failed"          # 失败
```

### 全局状态对象 (State)

每次 Agent 调用时，从 Redis 加载并注入 Prompt：

```python
@dataclass
class WorkflowState:
    task_id: str
    project_name: str
    global_params: dict          # { total_budget, total_power, deadline, ... }
    outline: dict | None         # 确认后的大纲 JSON
    completed_sections: dict     # { section_idx: markdown_content }
    referenced_images: list      # [{ url, description, section_idx }]
    referenced_sources: list     # [{ doc_name, heading, chunk_id }]
    current_agent: str           # 当前执行的 Agent 名称
    current_section_idx: int     # 当前处理的章节序号
    token_usage: int             # 累计 Token
```

---

## 9. Prompt 模板骨架

### 9.1 Planner Agent — 大纲生成

```python
PLANNER_SYSTEM_PROMPT = """你是一位资深的售前技术方案架构师。你的任务是根据客户的需求文档(RFP)，
生成一份专业、完整的技术方案大纲(目录结构)。

## 规则
1. 大纲必须包含 3-5 个一级章节，每个一级章节下有 2-4 个二级章节
2. 必须包含：项目概述、需求分析、技术架构、硬件配置清单、实施排期、售后服务
3. 输出必须严格为 JSON 格式，符合以下 Schema
4. 每个章节节点必须包含 title, description, keywords(用于后续检索)

## 全局参数（所有章节必须遵循）
- 项目名称：{project_name}
- 项目预算：{total_budget}
- 关键参数：{key_params}

## 输出 JSON Schema
{json_schema}
"""

PLANNER_OUTLINE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "方案总标题"},
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "title": {"type": "string"},
                    "description": {"type": "string", "description": "本章节要解决的问题"},
                    "keywords": {"type": "array", "items": {"type": "string"}, "description": "RAG检索关键词"},
                    "subsections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "index": {"type": "integer"},
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                                "keywords": {"type": "array", "items": {"type": "string"}}
                            },
                            "required": ["index", "title", "description", "keywords"]
                        }
                    }
                },
                "required": ["index", "title", "description", "keywords"]
            }
        }
    },
    "required": ["title", "sections"]
}
```

### 9.2 Executor Agent — 章节内容生成

```python
EXECUTOR_SYSTEM_PROMPT = """你是一位专业的技术文档撰写专家。你的任务是根据提供的参考资料，
撰写技术方案中的特定章节。

## 当前章节
- 标题：{section_title}
- 描述：{section_description}
- 在大纲中的位置：{heading_path}

## 全局参数（必须在本章节中保持一致）
{global_params_formatted}

## 参考资料（来自历史成功方案的检索结果）
{retrieved_context}

## 撰写规则
1. 基于参考资料撰写，禁止编造不存在的产品型号或技术参数
2. 如果参考资料中包含表格数据，必须以 Markdown 表格格式还原
3. 当引用某段参考资料时，在段末标注来源：[来源：{文件名} > {章节}]
4. 如果需要插入图片/图纸，输出占位符：![{图片描述}](REVIEW_REQUIRED:{image_url})
5. 输出格式为 Markdown，使用适当的标题层级
6. 同一文档中的金额、功率等数值参数必须与全局参数保持一致
"""
```

### 9.3 Holistic Agent — 融合润色

```python
HOLISTIC_SYSTEM_PROMPT = """你是一位技术文档终审编辑。你的任务是将多个独立撰写的章节
融合为一份完整、连贯的技术方案。

## 审查要求
1. **一致性校验**：检查全文中的项目名称、金额、功率等参数是否完全一致
2. **术语统一**：确保同一概念在全文中使用一致的术语
3. **衔接润色**：在章节衔接处添加过渡段落，使全文读起来浑然一体
4. **格式规范**：统一标题层级、表格格式、列表风格
5. **合规检查**：确认方案中不包含与全局参数矛盾的表述

## 全局参数
{global_params_formatted}

## 各章节内容
{all_sections_markdown}

## 输出
输出完整的融合后 Markdown 文档，在修改处用 HTML 注释标注修改原因：
<!-- 修改：将"600万"统一为全局参数中的"800万" -->
"""
```

### 9.4 Rewrite Agent — 局部重写

```python
REWRITE_SYSTEM_PROMPT = """你是一位技术文档编辑助手。用户选中了方案中的一段文字，
并给出了修改指令，请仅修改选中段落，保持前后文的连贯性。

## 当前章节上下文
{section_context}

## 用户选中的文字
{selected_text}

## 用户指令
{user_instruction}

## 全局参数
{global_params_formatted}

## 规则
1. 仅修改选中部分及必要的上下文衔接，不要重写整个章节
2. 保持原有的格式（标题层级、列表、表格）
3. 如果指令要求引用新案例，请标注 [来源：需检索补充]
"""
```

---

## 10. 统一 LLM Client 规约

```python
# backend/app/services/llm/client.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

class ModelType(str, Enum):
    DEEPSEEK = "deepseek"      # 快速提取、结构化输出
    QWEN = "qwen"              # 深度推理、长文生成
    AZURE = "azure"            # 备用

class TaskType(str, Enum):
    EXTRACTION = "extraction"         # 快速提取 → DeepSeek
    OUTLINE = "outline"               # 大纲生成 → DeepSeek
    SECTION_WRITE = "section_write"   # 章节撰写 → Qwen
    HOLISTIC = "holistic"             # 融合润色 → Qwen
    REWRITE = "rewrite"               # 局部重写 → Qwen
    QUESTION_GEN = "question_gen"     # 逆向问题生成 → DeepSeek

# 路由策略：TaskType → ModelType
ROUTING_TABLE = {
    TaskType.EXTRACTION:    ModelType.DEEPSEEK,
    TaskType.OUTLINE:       ModelType.DEEPSEEK,
    TaskType.SECTION_WRITE: ModelType.QWEN,
    TaskType.HOLISTIC:      ModelType.QWEN,
    TaskType.REWRITE:       ModelType.QWEN,
    TaskType.QUESTION_GEN:  ModelType.DEEPSEEK,
}

@dataclass
class LLMRequest:
    task_type: TaskType
    system_prompt: str
    user_prompt: str
    temperature: float = 0.3
    max_tokens: int = 4096
    json_schema: dict | None = None   # 非 None 时强制 JSON 输出
    stream: bool = False

@dataclass
class LLMResponse:
    content: str
    model_used: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_estimate: float              # 元

class LLMClient:
    """统一 LLM 客户端，内部封装多模型路由与容错。"""

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """
        1. 根据 ROUTING_TABLE 选择模型
        2. 经脱敏网关处理 user_prompt
        3. 调用对应 LLM API
        4. 经脱敏网关还原响应
        5. 失败时自动切换备用模型 (DeepSeek ↔ Qwen 互为 fallback)
        """
        ...

    async def invoke_stream(self, request: LLMRequest):
        """流式版本，yield SSE 格式的 delta chunks。"""
        ...
```

---

## 11. 前端页面路由与组件树

### 页面路由

| 路径 | 页面 | 核心功能 |
|------|------|---------|
| `/` | 首页 | 项目列表、创建项目入口 |
| `/projects/[id]` | 项目详情 | 文档列表、历史生成任务 |
| `/projects/[id]/upload` | 文档上传 | 拖拽上传 RFP/历史方案 |
| `/projects/[id]/outline` | 大纲编辑 | 树形大纲预览、拖拽排序、确认 |
| `/projects/[id]/editor` | 方案编辑 | Block Editor、内联指令、审批 |
| `/projects/[id]/export` | 导出预览 | 终稿预览、Word/PDF 导出 |

### 前端状态管理 (Zustand)

```typescript
// stores/editorStore.ts

interface Section {
  index: number;
  title: string;
  content: string;          // Markdown
  status: 'pending' | 'generating' | 'done' | 'reviewing';
  citations: Citation[];
  reviewPoints: ReviewPoint[];
}

interface EditorState {
  taskId: string | null;
  outline: OutlineNode | null;
  sections: Section[];
  globalParams: Record<string, string>;
  agentProgress: {
    currentAgent: string;
    currentSection: number;
    progress: number;       // 0-1
  };

  // Actions
  setOutline: (outline: OutlineNode) => void;
  updateSection: (index: number, patch: Partial<Section>) => void;
  appendSectionDelta: (index: number, delta: string) => void;
  setAgentProgress: (progress: AgentProgress) => void;
}
```

### 核心组件交互流程

```
用户打开项目 → 上传 RFP 文件
    │
    ▼  POST /documents/upload
解析完成通知 → 点击"生成方案"
    │
    ▼  POST /generation/start
SSE 连接建立 → GET /generation/{id}/stream
    │
    ▼  event: outline_ready
大纲预览页面 → 用户拖拽调整 → 点击"确认大纲"
    │
    ▼  POST /generation/{id}/outline/confirm
SSE 接收 section_chunk 事件 → Block Editor 实时渲染
    │
    ├─ event: review_required → ReviewCard 弹出，用户操作后
    │   ▼  POST /review/{id}/approve 或 /reject
    │
    ├─ 用户框选文字 → InlineCommandBar 浮出
    │   ▼  POST /generation/{id}/sections/{idx}/rewrite
    │
    ▼  event: generation_complete
终稿就绪 → 导出页面 → 点击"导出Word"
    ▼  GET /generation/{id}/export?format=docx
```

---

## 12. 关键依赖清单

### 后端 (Python)

```toml
# pyproject.toml [tool.poetry.dependencies]
python = "^3.11"
fastapi = "^0.115"
uvicorn = { extras = ["standard"], version = "^0.34" }
sqlalchemy = { extras = ["asyncio"], version = "^2.0" }
asyncpg = "^0.30"
alembic = "^1.14"
pydantic = "^2.10"
pydantic-settings = "^2.7"
redis = { extras = ["hiredis"], version = "^5.2" }
qdrant-client = "^1.12"
httpx = "^0.28"                  # 调用 LLM API & 脱敏网关
openai = "^1.60"                 # OpenAI 兼容 SDK (DeepSeek/Qwen 均兼容)
python-multipart = "^0.0.18"     # 文件上传
minio = "^7.2"                   # MinIO SDK
docling = "^2.0"                 # 文档解析
sentence-transformers = "^3.4"   # BGE Embedding
sse-starlette = "^2.2"          # SSE 支持
python-docx = "^1.1"            # Word 导出
```

### 脱敏网关 (Python)

```toml
# gateway/pyproject.toml
python = "^3.11"
fastapi = "^0.115"
uvicorn = { extras = ["standard"], version = "^0.34" }
presidio-analyzer = "^2.2"
presidio-anonymizer = "^2.2"
redis = { extras = ["hiredis"], version = "^5.2" }
spacy = "^3.8"
```

需预下载中文 NER 模型：
```bash
python -m spacy download zh_core_web_trf
```

### 前端 (TypeScript)

```json
{
  "dependencies": {
    "next": "^15.0",
    "react": "^19.0",
    "react-dom": "^19.0",
    "@tiptap/react": "^2.11",
    "@tiptap/starter-kit": "^2.11",
    "@tiptap/extension-placeholder": "^2.11",
    "zustand": "^5.0",
    "axios": "^1.7",
    "react-markdown": "^9.0",
    "react-sortable-tree": "^2.8",
    "@radix-ui/react-dialog": "^1.1",
    "@radix-ui/react-toast": "^1.2",
    "lucide-react": "^0.460",
    "file-saver": "^2.0"
  }
}
```

---

## 13. 开发优先级指引（面向 AI 编码工具）

> **建议 AI 编码工具按以下顺序实现各模块：**

### Phase 1 — 基础骨架（第 1 步）
```
实现顺序：
1. docker-compose.yml (启动所有依赖服务)
2. backend/app/main.py + config.py (FastAPI 骨架)
3. backend/app/models/ (全部 ORM 模型)
4. alembic 初始化 + 第一个 migration
5. backend/app/api/projects.py (项目 CRUD，验证数据库连接)
```

### Phase 2 — 数据管线（第 2 步）
```
实现顺序：
1. backend/app/services/parsing/docling_parser.py
2. backend/app/services/vectorstore/chunker.py
3. backend/app/services/vectorstore/embedder.py
4. backend/app/services/vectorstore/qdrant_client.py
5. backend/app/api/documents.py (上传 + 触发解析)
6. backend/app/services/vectorstore/retriever.py
7. backend/app/api/retrieval.py
```

### Phase 3 — 脱敏网关（第 3 步）
```
实现顺序：
1. gateway/app/presidio_config.py (配置中文实体规则)
2. gateway/app/detector.py
3. gateway/app/masker.py
4. gateway/app/mapping_store.py
5. gateway/app/restorer.py
6. gateway/app/main.py (组装 /mask 和 /restore 端点)
```

### Phase 4 — LLM 与智能体（第 4 步）
```
实现顺序：
1. backend/app/services/llm/client.py (统一 Client + 路由)
2. backend/app/services/llm/prompts/*.py (所有 Prompt 模板)
3. backend/app/services/agents/state.py (全局状态)
4. backend/app/services/agents/planner.py
5. backend/app/services/agents/retriever.py
6. backend/app/services/agents/executor.py
7. backend/app/services/agents/holistic.py
8. backend/app/services/agents/workflow.py (DAG 编排)
9. backend/app/api/generation.py (含 SSE 流)
10. backend/app/api/review.py
```

### Phase 5 — 前端（第 5 步）
```
实现顺序：
1. Next.js 项目初始化 + 全局布局
2. lib/api.ts + lib/types.ts
3. stores/ (Zustand 状态管理)
4. 项目列表页 + 创建项目
5. 文档上传页
6. 大纲编辑页
7. Block Editor 主体
8. InlineCommandBar + ReviewCard
9. AgentProgress 状态面板
10. 导出功能
```

---

> **本文档可直接作为 AI 编码工具的上下文输入。使用方式示例：**
> - `"请按照 spec.md Phase 1 实现基础骨架"`
> - `"按照 spec.md 第6节的数据流规约，实现 gateway/app/masker.py"`
> - `"按照 spec.md 5.4 节的 SSE 事件定义，实现 generation.py 的流式接口"`
