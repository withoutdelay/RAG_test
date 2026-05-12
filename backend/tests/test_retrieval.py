import os
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

from app.config import get_settings
from app.services.retrieval.asset_service import (
    AssetRetrievalService,
    AssetCard,
    _asset_anchor_boost,
    _asset_noise_penalty,
    _asset_quality_flags,
    _asset_source_binding,
    _asset_summary_boost,
    _asset_taxonomy_boost,
    _build_asset_display_title,
    _build_preview_text,
    _build_visual_retrieval_text,
    _compose_asset_score,
    _derive_visual_role,
    _promote_source_section_asset_matches,
    _resolve_visual_query_key,
    _to_result,
)
from app.services.retrieval.service import (
    EvidenceBundleService,
    build_case_fallback_evidence_items,
    build_evidence_items,
    build_evidence_search_plan,
    filter_evidence_results,
)
from app.services.retrieval.semantic_scorer import EmbeddingSemanticScorer
from app.services.vectorstore.retriever import Retriever, _build_chunk_retrieval_text, _rank_chunk_candidates
from app.services.vectorstore.chunker import Chunker
from app.services.vectorstore.embedder import Embedder
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


class FakeReranker:
    @property
    def available(self) -> bool:
        return True

    def score_many(self, *, query: str, texts: list[str]) -> list[float]:
        return [0.92 if "控制接口" in text else 0.18 for text in texts]


class FakeEmbedder:
    dimension = 4

    async def embed_text(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


class FakeQdrant:
    def __init__(self, *, hits: list[SimpleNamespace]) -> None:
        self._hits = hits

    def search(self, *, query_vector: list[float], top_k: int, filters: dict | None = None) -> list[SimpleNamespace]:
        return list(self._hits)


class FakeExecuteResult:
    def __init__(self, rows: list[tuple[object, object]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[object, object]]:
        return list(self._rows)


class FakeAsyncSession:
    def __init__(self, rows_by_call: list[list[tuple[object, object]]]) -> None:
        self._rows_by_call = list(rows_by_call)

    async def execute(self, stmt: object) -> FakeExecuteResult:
        if not self._rows_by_call:
            raise AssertionError("unexpected execute call")
        return FakeExecuteResult(self._rows_by_call.pop(0))


class FakeProjectSession:
    def __init__(self, project: object) -> None:
        self._project = project

    async def get(self, model: object, key: object) -> object:
        del model
        del key
        return self._project


class FakeVisualEmbedder:
    def __init__(self) -> None:
        self.backend_name = "clip"
        self.build_query_calls = 0
        self.embed_asset_calls = 0

    async def build_query_vectors(self, text: str) -> dict[str, list[float]]:
        del text
        self.build_query_calls += 1
        return {"text_proxy": [0.3, 0.7], "image": [0.5, 0.5]}

    async def embed_asset(self, *, asset_uri: str, fallback_text: str, asset_id: str = "") -> tuple[list[float], str]:
        del asset_uri
        del fallback_text
        del asset_id
        self.embed_asset_calls += 1
        return [0.4, 0.6], "image"


class RetrievalBuildingBlockTests(unittest.TestCase):
    def test_resolve_visual_query_key_maps_cache_to_base_channel(self) -> None:
        self.assertEqual(_resolve_visual_query_key("image_cache"), "image")
        self.assertEqual(_resolve_visual_query_key("text_proxy_cache"), "text_proxy")
        self.assertEqual(_resolve_visual_query_key("unknown_cache"), "text_proxy")

    def test_chunker_preserves_table_blocks(self) -> None:
        markdown = "# 标题\n\n说明文字\n\n| 设备 | 型号 |\n|---|---|\n| 变频器 | ABB |\n"
        chunks = Chunker(max_chars=80).split(markdown, base_metadata={"industry": "电气"})

        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(any(chunk.chunk_type == "TABLE" for chunk in chunks))
        self.assertTrue(all("content_risk_level" in chunk.metadata for chunk in chunks))

    def test_chunker_does_not_split_tables_by_default(self) -> None:
        rows = "\n".join(f"| 参数{i} | 数值{i} | 补充说明{i} |" for i in range(12))
        markdown = f"# 参数表\n\n| 名称 | 值 | 说明 |\n|---|---|---|\n{rows}\n"

        chunks = Chunker(max_chars=160).split(markdown, base_metadata={"industry": "电气"})

        table_chunks = [chunk for chunk in chunks if chunk.chunk_type == "TABLE"]
        self.assertEqual(len(table_chunks), 1)
        self.assertIn("| 参数0 | 数值0 | 补充说明0 |", table_chunks[0].content)
        self.assertIn("| 参数11 | 数值11 | 补充说明11 |", table_chunks[0].content)

    def test_chunker_can_split_tables_when_explicitly_enabled(self) -> None:
        rows = "\n".join(f"| 参数{i} | 数值{i} | 补充说明{i} |" for i in range(12))
        markdown = f"# 参数表\n\n| 名称 | 值 | 说明 |\n|---|---|---|\n{rows}\n"

        chunks = Chunker(
            max_chars=160,
            max_table_rows_per_chunk=4,
            split_tables=True,
        ).split(markdown, base_metadata={"industry": "电气"})

        table_chunks = [chunk for chunk in chunks if chunk.chunk_type == "TABLE"]
        self.assertGreaterEqual(len(table_chunks), 3)
        self.assertTrue(all(chunk.token_count > 0 for chunk in table_chunks))
        self.assertTrue(all(chunk.metadata["content_risk_level"] == "medium" for chunk in table_chunks))

    def test_chunker_keeps_nested_subsections_inside_major_section_chunk(self) -> None:
        markdown = "\n".join(
            [
                "# 文档标题",
                "",
                "## 4 LCI 变频软起系统方案",
                "",
                "### 4.2 启动和同步过程描述",
                "",
                "同步电机的启动和同步由变频器控制，达到同步条件后平滑切换至工频运行。",
                "",
                "### 4.3 本地控制单元 PLC 对电机辅助设备监控功能描述",
                "",
                "本地控制单元负责监控高压柜、低压柜、油站、冷却器和励磁系统信号。",
                "",
                "### 4.4 LCI 变频启动特性",
                "",
                "系统支持连续启动、转速曲线跟踪和切换过程监测。",
            ]
        )

        chunks = Chunker(max_chars=1600).split(markdown, base_metadata={"industry": "电气"})

        section_chunks = [chunk for chunk in chunks if chunk.heading_path == "4 LCI 变频软起系统方案" and chunk.chunk_type == "PLAIN"]
        self.assertEqual(len(section_chunks), 1)
        self.assertIn("### 4.2 启动和同步过程描述", section_chunks[0].content)
        self.assertIn("### 4.3 本地控制单元 PLC 对电机辅助设备监控功能描述", section_chunks[0].content)
        self.assertIn("### 4.4 LCI 变频启动特性", section_chunks[0].content)

    def test_chunker_marks_asset_lookup_for_diagram_references(self) -> None:
        markdown = "# 控制原理\n\n原理图与波形图详见附件，接线图如下。"

        chunks = Chunker().split(markdown, base_metadata={"industry": "电气"})

        self.assertTrue(any(chunk.metadata["needs_asset_lookup"] for chunk in chunks))
        self.assertTrue(any(chunk.metadata["content_risk_level"] == "medium" for chunk in chunks))
        self.assertTrue(any(chunk.metadata["content_form"] == "figure" for chunk in chunks))

    def test_build_evidence_items_keeps_raw_content_and_reusability_score(self) -> None:
        items = build_evidence_items(
            [
                {
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "第4章 > 技术架构",
                    "chunk_type": "PLAIN",
                    "content": "项目名称：旧项目A\n采用双机冗余架构。",
                    "score": 0.82,
                    "metadata": {
                        "content_risk_level": "low",
                        "front_matter": False,
                        "needs_asset_lookup": False,
                        "section_type": "overall_solution",
                        "equipment_type": "vfd",
                        "content_form": "narrative",
                    },
                }
            ]
        )
        self.assertEqual(items[0]["source_chunk_type"], "PLAIN")
        self.assertIn("双机冗余架构", items[0]["raw_content"])
        self.assertGreater(items[0]["reusability_score"], 0.8)
        self.assertEqual(items[0]["section_type"], "overall_solution")
        self.assertEqual(items[0]["equipment_type"], "vfd")

    def test_build_evidence_items_preserves_structured_retrieval_trace(self) -> None:
        items = build_evidence_items(
            [
                {
                    "chunk_id": "chunk-2",
                    "document_id": "doc-2",
                    "document_name": "历史方案B",
                    "heading_path": "4.2 控制接口说明",
                    "chunk_type": "PLAIN",
                    "content": "DCS 至变频器提供 DI/DO、AI/AO 和联锁接口。",
                    "score": 0.86,
                    "reason": "semantic_match=0.780; sparse_match=0.910; final_score=0.860",
                    "reason_trace": [
                        "semantic_match=0.780",
                        "sparse_match=0.910",
                        "hybrid_shortlist=0.820",
                        "final_score=0.860",
                    ],
                    "score_breakdown": {
                        "base": 0.78,
                        "semantic": 0.78,
                        "sparse": 0.91,
                        "hybrid_rrf": 0.82,
                        "rerank": 0.64,
                        "hybrid_rerank": 0.04,
                        "final": 0.86,
                    },
                    "metadata": {
                        "content_risk_level": "low",
                        "front_matter": False,
                        "needs_asset_lookup": False,
                        "section_type": "control_system",
                        "equipment_type": "vfd",
                        "content_form": "narrative",
                    },
                }
            ]
        )

        self.assertEqual(items[0]["retrieval_reason"], "semantic_match=0.780; sparse_match=0.910; final_score=0.860")
        self.assertEqual(items[0]["reason_trace"][0], "semantic_match=0.780")
        self.assertEqual(items[0]["retrieval_score_breakdown"]["final"], 0.86)

    def test_filter_evidence_results_drops_short_garbled_plain_fragment(self) -> None:
        results = filter_evidence_results(
            [
                {
                    "chunk_id": "chunk-bad",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "6 Л",
                    "chunk_type": "PLAIN",
                    "content": "# 6 Л\n\nof",
                    "score": 0.52,
                    "metadata": {},
                },
                {
                    "chunk_id": "chunk-good",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "4.2 控制接口",
                    "chunk_type": "PLAIN",
                    "content": "## 4.2 控制接口\n\nDCS 至变频器提供 DI/DO、AI/AO 以及 Modbus/RS485 通讯接口。",
                    "score": 0.61,
                    "metadata": {},
                },
            ]
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "chunk-good")

    def test_filter_evidence_results_drops_numeric_table_fragment(self) -> None:
        results = filter_evidence_results(
            [
                {
                    "chunk_id": "chunk-bad-table",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "47.8 17.5",
                    "chunk_type": "TABLE",
                    "content": "| 37.5 | 17.5 | 20.0 |\n|---|---|---|\n| 47.3 | 17.5 | 29.8 |\n| 43.5 | 17.5 | 26 |",
                    "score": 0.49,
                    "metadata": {},
                },
                {
                    "chunk_id": "chunk-good-table",
                    "document_id": "doc-1",
                    "document_name": "历史方案A",
                    "heading_path": "5.1 乙方提供设备清单",
                    "chunk_type": "TABLE",
                    "content": "| 编号 | 名称 | 规格 | 数量 |\n|---|---|---|---|\n| 1 | 变频柜 | GB/T-MVSG0900 | 1套 |",
                    "score": 0.77,
                    "metadata": {},
                },
            ]
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["chunk_id"], "chunk-good-table")

    def test_build_evidence_search_plan_uses_global_scope_for_historical_proposals(self) -> None:
        plan = build_evidence_search_plan(
            project_id=uuid4(),
            industry="钢铁",
            doc_type="historical_proposal",
            chunk_types=["PLAIN", "TABLE"],
            scoped_document_names=["样板A.docx", "样板B.pdf"],
        )

        self.assertEqual(plan[0][0], "case_first")
        self.assertIsNone(plan[0][1])
        self.assertEqual(plan[0][2].document_names, ["样板A.docx", "样板B.pdf"])
        self.assertEqual(plan[1][0], "case_first_relaxed_industry")
        self.assertIsNone(plan[1][2].industry)
        self.assertEqual(plan[2][0], "case_first_fallback_global")

    def test_build_evidence_search_plan_adds_relaxed_global_fallback_without_case_candidates(self) -> None:
        project_id = uuid4()
        plan = build_evidence_search_plan(
            project_id=project_id,
            industry="电气",
            doc_type="rfp",
            chunk_types=["PLAIN"],
            scoped_document_names=[],
        )

        self.assertEqual(plan[0][0], "global_fallback")
        self.assertEqual(plan[0][1], project_id)
        self.assertEqual(plan[0][2].industry, "电气")
        self.assertEqual(plan[1][0], "global_fallback_relaxed_industry")
        self.assertIsNone(plan[1][2].industry)

    def test_build_case_fallback_evidence_items_creates_case_summary_entries(self) -> None:
        items = build_case_fallback_evidence_items(
            [
                {
                    "sample_id": "sample-a",
                    "file_name": "历史方案A.docx",
                    "score": 0.52,
                    "reason": "query_overlap=LCI,同步电机",
                    "top_level_titles": ["1 工厂设计环境", "2 供货范围", "3 系统方案"],
                    "profile": "mixed_engineering_doc",
                    "library_track": "pilot_main",
                }
            ]
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "case_summary")
        self.assertEqual(items[0]["source_chunk_type"], "CASE_SUMMARY")
        self.assertEqual(items[0]["metadata"]["fallback_source"], "case_library")
        self.assertIn("供货范围", items[0]["raw_content"])

    def test_compute_quality_score_uses_discounted_case_fallback_formula(self) -> None:
        service = EvidenceBundleService()
        score = service._compute_quality_score(
            [
                {"relevance_score": 0.51},
                {"relevance_score": 0.48},
                {"relevance_score": 0.46},
            ],
            quality_source="case_fallback",
        )

        self.assertGreater(score, 0)
        self.assertLess(score, 0.70)

    def test_evidence_bundle_service_defers_retriever_initialization(self) -> None:
        with patch("app.services.retrieval.service.Retriever", side_effect=AssertionError("retriever should be lazy")):
            service = EvidenceBundleService()
            with self.assertRaises(AssertionError):
                _ = service.retriever

    def test_embedder_returns_configured_dimension(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_BACKEND": "fallback",
                "EMBEDDING_DIMENSION": "1024",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            embedder = Embedder()
        vector = asyncio.run(embedder.embed_text("110kV 变电站综合自动化方案"))
        get_settings.cache_clear()
        self.assertEqual(len(vector), embedder.dimension)

    def test_embedder_uses_fallback_backend_when_configured(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_BACKEND": "fallback",
                "EMBEDDING_DIMENSION": "16",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            embedder = Embedder()

        vector = asyncio.run(embedder.embed_text("ABB ACS880"))
        get_settings.cache_clear()

        self.assertEqual(embedder.backend_name, "fallback")
        self.assertEqual(len(vector), 16)

    def test_embedder_fails_fast_for_missing_explicit_sentence_transformers_backend(self) -> None:
        with patch.dict(Embedder.__init__.__globals__, {"get_settings": None, "SentenceTransformer": None}):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "sentence-transformers",
                    "EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                with self.assertRaises(RuntimeError):
                    Embedder()
        get_settings.cache_clear()

    def test_embedder_passes_local_files_only_flag_to_sentence_transformer(self) -> None:
        mock_model = object()
        mock_loader = Mock(return_value=mock_model)
        with patch.dict(Embedder.__init__.__globals__, {"get_settings": None, "SentenceTransformer": mock_loader}):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "sentence-transformers",
                    "EMBEDDING_MODEL": "BAAI/bge-large-zh-v1.5",
                    "EMBEDDING_LOCAL_FILES_ONLY": "true",
                    "EMBEDDING_DEVICE": "cpu",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                embedder = Embedder()

        get_settings.cache_clear()

        self.assertIs(embedder._model, mock_model)
        mock_loader.assert_called_once_with("BAAI/bge-large-zh-v1.5", local_files_only=True, device="cpu")

    def test_embedder_uses_openai_compatible_embedding_backend(self) -> None:
        requests: list[dict] = []

        class FakeResponse:
            def __init__(self, inputs: list[str]) -> None:
                self.inputs = inputs

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "data": [
                        {"index": index, "embedding": [float(index), float(index + 1), float(index + 2)]}
                        for index, _text in enumerate(self.inputs)
                    ]
                }

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
                requests.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
                return FakeResponse(list(json["input"]))

        with patch("app.services.vectorstore.embedder.httpx.AsyncClient", FakeAsyncClient):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "openai-compatible",
                    "EMBEDDING_BASE_URL": "https://embedding.example.test/v1",
                    "EMBEDDING_API_KEY": "sk-real",
                    "EMBEDDING_MODEL": "text-embedding-v4",
                    "EMBEDDING_DIMENSION": "3",
                    "EMBEDDING_BATCH_SIZE": "2",
                    "QWEN_API_KEY": "",
                    "OPENAI_API_KEY": "",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                embedder = Embedder()
                vectors = asyncio.run(embedder.embed_texts(["alpha", "beta", "gamma"]))

        get_settings.cache_clear()

        self.assertEqual(embedder.backend_name, "openai-compatible")
        self.assertEqual(vectors, [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [0.0, 1.0, 2.0]])
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["url"], "https://embedding.example.test/v1/embeddings")
        self.assertEqual(requests[0]["headers"]["Authorization"], "Bearer sk-real")
        self.assertEqual(requests[0]["json"]["model"], "text-embedding-v4")
        self.assertEqual(requests[0]["json"]["input"], ["alpha", "beta"])

    def test_embedder_fails_fast_for_missing_openai_compatible_key(self) -> None:
        with patch.dict(
            os.environ,
            {
                "EMBEDDING_BACKEND": "openai-compatible",
                "EMBEDDING_BASE_URL": "https://embedding.example.test/v1",
                "EMBEDDING_API_KEY": "",
                "QWEN_API_KEY": "",
                "OPENAI_API_KEY": "",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            with self.assertRaisesRegex(RuntimeError, "EMBEDDING_API_KEY"):
                Embedder()
        get_settings.cache_clear()

    def test_embedder_uses_dashscope_multimodal_embedding_backend(self) -> None:
        requests: list[dict] = []

        class FakeResponse:
            def __init__(self, inputs: list[dict]) -> None:
                self.inputs = inputs

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "status_code": 200,
                    "request_id": "test-request",
                    "code": "",
                    "message": "",
                    "output": {
                        "embeddings": [
                            {
                                "index": index,
                                "type": "text",
                                "embedding": [float(index), float(index + 1), float(index + 2)],
                            }
                            for index, _item in enumerate(self.inputs)
                        ]
                    },
                }

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
                requests.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
                return FakeResponse(list(json["input"]["contents"]))

        with patch("app.services.vectorstore.embedder.httpx.AsyncClient", FakeAsyncClient):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "dashscope-multimodal",
                    "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "EMBEDDING_API_KEY": "sk-real",
                    "EMBEDDING_ENDPOINT_PATH": "/services/embeddings/multimodal-embedding/multimodal-embedding",
                    "EMBEDDING_MODEL": "qwen3-vl-embedding",
                    "EMBEDDING_DIMENSION": "3",
                    "EMBEDDING_BATCH_SIZE": "2",
                    "QWEN_API_KEY": "",
                    "OPENAI_API_KEY": "",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                embedder = Embedder()
                vectors = asyncio.run(embedder.embed_texts(["alpha", "beta", "gamma"]))

        get_settings.cache_clear()

        self.assertEqual(embedder.backend_name, "dashscope-multimodal")
        self.assertEqual(vectors, [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [0.0, 1.0, 2.0]])
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[0]["url"],
            "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
        )
        self.assertEqual(requests[0]["headers"]["Authorization"], "Bearer sk-real")
        self.assertEqual(requests[0]["json"]["model"], "qwen3-vl-embedding")
        self.assertEqual(requests[0]["json"]["input"]["contents"], [{"text": "alpha"}, {"text": "beta"}])
        self.assertEqual(requests[0]["json"]["parameters"]["dimension"], 3)

    def test_embedder_sync_uses_dashscope_multimodal_embedding_backend(self) -> None:
        requests: list[dict] = []

        class FakeResponse:
            def __init__(self, inputs: list[dict]) -> None:
                self.inputs = inputs

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "status_code": 200,
                    "code": "",
                    "message": "",
                    "output": {
                        "embeddings": [
                            {"index": index, "embedding": [float(index), float(index + 1), float(index + 2)]}
                            for index, _item in enumerate(self.inputs)
                        ]
                    },
                }

        class FakeClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            def __enter__(self) -> "FakeClient":
                return self

            def __exit__(self, exc_type, exc, tb) -> None:
                return None

            def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
                requests.append({"url": url, "headers": headers, "json": json, "timeout": self.timeout})
                return FakeResponse(list(json["input"]["contents"]))

        with patch("app.services.vectorstore.embedder.httpx.Client", FakeClient):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "dashscope-multimodal",
                    "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "EMBEDDING_API_KEY": "sk-real",
                    "EMBEDDING_MODEL": "qwen3-vl-embedding",
                    "EMBEDDING_DIMENSION": "3",
                    "EMBEDDING_BATCH_SIZE": "2",
                    "QWEN_API_KEY": "",
                    "OPENAI_API_KEY": "",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                embedder = Embedder()
                vectors = embedder.embed_texts_sync(["alpha", "beta", "gamma"])

        get_settings.cache_clear()

        self.assertEqual(vectors, [[0.0, 1.0, 2.0], [1.0, 2.0, 3.0], [0.0, 1.0, 2.0]])
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[0]["url"],
            "https://dashscope.aliyuncs.com/api/v1/services/embeddings/multimodal-embedding/multimodal-embedding",
        )

    def test_semantic_scorer_can_use_api_embedder_sync_batches(self) -> None:
        class FakeApiEmbedder:
            backend_name = "dashscope-multimodal"
            _model = None

            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def embed_texts_sync(self, texts: list[str]) -> list[list[float]]:
                self.calls.append(list(texts))
                vectors = {
                    "query": [1.0, 0.0, 0.0],
                    "strong": [0.9, 0.1, 0.0],
                    "weak": [0.0, 1.0, 0.0],
                }
                return [vectors[text] for text in texts]

        embedder = FakeApiEmbedder()
        scorer = EmbeddingSemanticScorer(embedder=embedder)  # type: ignore[arg-type]
        scores = scorer.score_many(query="query", texts=["strong", "weak"])

        self.assertTrue(scorer.available)
        self.assertEqual(scores, [0.9, 0.0])
        self.assertEqual(embedder.calls, [["query"], ["strong", "weak"]])

    def test_dashscope_multimodal_batch_falls_back_when_provider_fuses_vectors(self) -> None:
        requests: list[dict] = []

        class FakeResponse:
            def __init__(self, inputs: list[dict]) -> None:
                self.inputs = inputs

            def raise_for_status(self) -> None:
                return None

            def json(self) -> dict:
                return {
                    "status_code": 200,
                    "code": "",
                    "message": "",
                    "output": {
                        "embeddings": [
                            {
                                "index": 0,
                                "embedding": [float(len(self.inputs)), 1.0, 2.0],
                            }
                        ]
                    },
                }

        class FakeAsyncClient:
            def __init__(self, *, timeout: float) -> None:
                self.timeout = timeout

            async def __aenter__(self) -> "FakeAsyncClient":
                return self

            async def __aexit__(self, exc_type, exc, tb) -> None:
                return None

            async def post(self, url: str, *, headers: dict, json: dict) -> FakeResponse:
                requests.append({"url": url, "json": json})
                return FakeResponse(list(json["input"]["contents"]))

        with patch("app.services.vectorstore.embedder.httpx.AsyncClient", FakeAsyncClient):
            with patch.dict(
                os.environ,
                {
                    "EMBEDDING_BACKEND": "dashscope-multimodal",
                    "EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/api/v1",
                    "EMBEDDING_API_KEY": "sk-real",
                    "EMBEDDING_MODEL": "qwen3-vl-embedding",
                    "EMBEDDING_DIMENSION": "3",
                    "EMBEDDING_BATCH_SIZE": "2",
                    "QWEN_API_KEY": "",
                    "OPENAI_API_KEY": "",
                },
                clear=False,
            ):
                get_settings.cache_clear()
                embedder = Embedder()
                vectors = asyncio.run(embedder.embed_texts(["alpha", "beta"]))

        get_settings.cache_clear()

        self.assertEqual(vectors, [[1.0, 1.0, 2.0], [1.0, 1.0, 2.0]])
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0]["json"]["input"]["contents"], [{"text": "alpha"}, {"text": "beta"}])
        self.assertEqual(requests[1]["json"]["input"]["contents"], [{"text": "alpha"}])
        self.assertEqual(requests[2]["json"]["input"]["contents"], [{"text": "beta"}])

    def test_asset_taxonomy_boost_prefers_main_circuit_figure(self) -> None:
        target = infer_target_taxonomy(
            {
                "title": "主回路系统方案",
                "purpose": "说明高压变频器主回路、旁路切换和一次接线方案。",
                "expected_evidence_types": ["section", "figure"],
            }
        )
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="2.2 高压变频器主回路方案说明",
            title="2.2 高压变频器主回路方案说明",
            display_title="高压变频器主回路方案说明",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="主回路采用一拖一输入输出隔离方案，一次原理如下图所示。",
            retrieval_text="主回路 一次接线 旁路切换",
            section_type="main_circuit_scheme",
            equipment_type="vfd",
            content_form="figure",
            metadata={},
        )

        self.assertGreater(_asset_taxonomy_boost(card=card, target_taxonomy=target), 0.2)

    def test_asset_noise_penalty_hits_certificate_like_assets(self) -> None:
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="page_furniture",
            risk_level="low",
            usage_mode="reference_only",
            review_required=False,
            page_no=1,
            heading_path="4.2 电 力 工业电气设备 质 量 检 验测试中 心检 测 报告",
            title="电 力 工业电气设备 检 测 报告",
            display_title="电 力 工业电气设备 检 测 报告",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="检测报告",
            retrieval_text="检测报告 认证",
            section_type="commissioning_acceptance",
            equipment_type="generic",
            content_form="figure",
            metadata={},
        )

        self.assertGreater(_asset_noise_penalty(card=card, target_section_type="main_circuit_scheme"), 0.3)

    def test_asset_noise_penalty_demotes_layout_illustrations_for_overall_solution(self) -> None:
        card = AssetCard(
            asset_card_id="asset:layout",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="illustration",
            risk_level="low",
            usage_mode="reference_only",
            review_required=False,
            page_no=12,
            heading_path="总布置图",
            title="方案一高度关系示意",
            display_title="方案一高度关系示意",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="该图用于说明新SFC顶部与电缆层架相对地面的高度关系。",
            retrieval_text="SFC 高度关系 外观图",
            section_type="cabinet_layout",
            equipment_type="vfd",
            content_form="figure",
            metadata={},
        )

        self.assertGreater(_asset_noise_penalty(card=card, target_section_type="overall_solution"), 0.3)
        self.assertEqual(_asset_noise_penalty(card=card, target_section_type="cabinet_layout"), 0.0)

    def test_asset_noise_penalty_demotes_unfocused_tables_for_interlock_sections(self) -> None:
        spare_table = AssetCard(
            asset_card_id="asset:spares",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-lci-blower-starting-solution.docx",
            doc_type="historical_proposal",
            asset_type="table",
            visual_role="table_asset",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=40,
            heading_path="8 备品备件清单",
            title="8 备品备件清单",
            display_title="8 备品备件清单",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="备品备件名称、型号、数量",
            retrieval_text="备品备件 清单 型号 数量",
            section_type="supply_scope",
            equipment_type="motor",
            content_form="bom_table",
            metadata={},
        )
        signal_table = AssetCard(
            asset_card_id="asset:signals",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-lci-blower-starting-solution.docx",
            doc_type="historical_proposal",
            asset_type="table",
            visual_role="table_asset",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="DCS/PLC 接口信号表",
            title="DCS/PLC 接口信号表",
            display_title="DCS/PLC 接口信号表",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="启动允许、故障、报警、断路器反馈、联锁保护信号",
            retrieval_text="DCS PLC 接口 信号 联锁 保护 断路器反馈",
            section_type="protection_interlock",
            equipment_type="motor",
            content_form="interface_table",
            metadata={},
        )

        self.assertGreater(
            _asset_noise_penalty(card=spare_table, target_section_type="protection_interlock"),
            _asset_noise_penalty(card=signal_table, target_section_type="protection_interlock") + 0.4,
        )

    def test_asset_noise_penalty_demotes_auxiliary_lube_curves_for_vfd_sections(self) -> None:
        lube_curve = AssetCard(
            asset_card_id="asset:lube",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-lci-blower-starting-solution.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=29,
            heading_path="应急润滑油需求曲线",
            title="应急润滑油需求曲线",
            display_title="应急润滑油需求曲线",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="润滑油流量与时间曲线",
            retrieval_text="应急润滑油 需求曲线 流量 时间",
            section_type="motor_spec",
            equipment_type="motor",
            content_form="figure",
            metadata={},
        )
        lci_diagram = AssetCard(
            asset_card_id="asset:lci",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-pulp-mill-lci-solution.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=7,
            heading_path="4.1 LCI 变频软起系统方案",
            title="LCI变频软起系统图",
            display_title="LCI变频软起系统图",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="LCI、SFC、同步电机、励磁柜和DCS接口关系",
            retrieval_text="LCI SFC 变频软起 同步切换 工频切换 主回路",
            section_type="vfd_spec",
            equipment_type="lci",
            content_form="figure",
            metadata={},
        )

        self.assertGreater(
            _asset_noise_penalty(card=lube_curve, target_section_type="vfd_spec"),
            _asset_noise_penalty(card=lci_diagram, target_section_type="vfd_spec") + 0.3,
        )

    def test_asset_anchor_boost_prefers_same_document_and_heading_family(self) -> None:
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-lci-blower-starting-solution.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="2.2 高压变频器主回路方案说明",
            title="2.2 高压变频器主回路方案说明",
            display_title="高压变频器主回路方案说明",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="主回路采用一拖一输入输出隔离方案，一次原理如下图所示。",
            retrieval_text="主回路 一次接线 旁路切换",
            section_type="main_circuit_scheme",
            equipment_type="vfd",
            content_form="figure",
            metadata={},
        )

        boost = _asset_anchor_boost(
            card=card,
            anchor_document_names={"sample-lci-blower-starting-solution.docx"},
            anchor_headings=["2.2 高压变频器主回路方案说明", "2.3 高压变频器主要技术参数"],
        )

        self.assertGreater(boost, 0.3)

    def test_asset_anchor_boost_matches_descendant_source_section_id(self) -> None:
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="某钢铁厂技术方案.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="2.2 高压变频器主回路方案说明",
            title="2.2 高压变频器主回路方案说明",
            display_title="高压变频器主回路方案说明",
            caption=None,
            source_ref=None,
            asset_uri="s3://asset",
            preview_text="主回路采用一拖一输入输出隔离方案，一次原理如下图所示。",
            retrieval_text="主回路 一次接线 QS1 QS2 QF M",
            section_type="main_circuit_scheme",
            equipment_type="vfd",
            content_form="figure",
            metadata={"sample_id": "sample-a", "source_section_id": "3.2.2"},
        )

        boost = _asset_anchor_boost(
            card=card,
            anchor_document_names=set(),
            anchor_headings=[],
            anchor_sample_ids={"sample-a"},
            anchor_source_section_ids={"3.2"},
        )

        self.assertGreaterEqual(boost, 0.6)

    def test_promote_source_section_asset_matches_recovers_image_marker_descendant_asset(self) -> None:
        correct_asset_id = uuid4()
        correct = AssetCard(
            asset_card_id="asset:correct",
            asset_id=correct_asset_id,
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="某钢铁厂技术方案.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=18,
            heading_path="2.2 高压变频器主回路方案说明",
            title="2.2 高压变频器主回路方案说明",
            display_title="高压变频器主回路方案说明",
            caption=None,
            source_ref=None,
            asset_uri="s3://correct",
            preview_text="主回路采用一拖一输入输出隔离方案，一次原理如下图所示。",
            retrieval_text="主回路 一次接线 QS1 QS2 QF M",
            section_type="main_circuit_scheme",
            equipment_type="vfd",
            content_form="figure",
            metadata={
                "sample_id": "sample-a",
                "source_section_id": "3.2.2",
                "asset_audit_status": "review_pending",
                "asset_quality_score": 0.52,
            },
        )
        unrelated = AssetCard(
            asset_card_id="asset:unrelated",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="其他方案.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="low",
            usage_mode="reference_only",
            review_required=False,
            page_no=3,
            heading_path="培训计划",
            title="培训计划",
            display_title="培训计划",
            caption=None,
            source_ref=None,
            asset_uri="s3://unrelated",
            preview_text="培训安排。",
            retrieval_text="培训计划",
            section_type="service_support",
            equipment_type="generic",
            content_form="figure",
            metadata={"sample_id": "sample-b", "source_section_id": "5.5"},
        )

        promoted = _promote_source_section_asset_matches(
            scored_cards=[
                (0.95, unrelated, {"final": 0.95}, ["high_text_score"]),
                (0.2, correct, {"final": 0.2}, ["low_text_score"]),
            ],
            anchor_document_names={"某钢铁厂技术方案.docx"},
            anchor_headings=["3.2 高压变频器主回路方案说明"],
            anchor_sample_ids={"sample-a"},
            anchor_image_document_names={"某钢铁厂技术方案.docx"},
            anchor_image_sample_ids={"sample-a"},
            anchor_image_source_section_ids={"3.2"},
        )

        self.assertEqual([item[1].asset_id for item in promoted], [correct_asset_id])
        self.assertEqual(promoted[0][2]["source_section_relation"], "descendant")

    def test_promote_source_section_asset_matches_recovers_heading_child_when_section_id_missing(self) -> None:
        correct_asset_id = uuid4()
        correct = AssetCard(
            asset_card_id="asset:correct",
            asset_id=correct_asset_id,
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-blower-lci-retrofit.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="illustration",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=False,
            page_no=None,
            heading_path="3.1 变频软起系统单线图 Single line Diagram",
            title="3.1 变频软起系统单线图 Single line Diagram",
            display_title="3.1 变频软起系统单线图 Single line Diagram",
            caption=None,
            source_ref="#/pictures/2",
            asset_uri="s3://correct",
            preview_text="单套变频驱动系统的单线图如下所示。",
            retrieval_text="LCI SFC ICB OCB RCB 单线图 主回路",
            section_type="unknown",
            equipment_type="generic",
            content_form="figure",
            metadata={
                "sample_id": "uploaded-863a4465",
                "asset_audit_status": "review_passed",
                "asset_quality_score": 0.86,
            },
        )
        unrelated = AssetCard(
            asset_card_id="asset:unrelated",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="其他方案.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="low",
            usage_mode="reference_only",
            review_required=False,
            page_no=3,
            heading_path="3.1 一次方案图",
            title="高压固态软起动一次方案图",
            display_title="高压固态软起动一次方案图",
            caption=None,
            source_ref="#/pictures/1",
            asset_uri="s3://unrelated",
            preview_text="高压固态软起动主回路。",
            retrieval_text="高压固态软起动 一次方案图",
            section_type="starter_spec",
            equipment_type="soft_starter",
            content_form="figure",
            metadata={"sample_id": "sample-b", "source_section_id": "3.1"},
        )

        promoted = _promote_source_section_asset_matches(
            scored_cards=[
                (0.95, unrelated, {"final": 0.95}, ["high_text_score"]),
                (0.2, correct, {"final": 0.2}, ["low_text_score"]),
            ],
            anchor_document_names={"sample-blower-lci-retrofit.docx"},
            anchor_headings=["3. 系统方案 SYSTEM SOLUTION"],
            anchor_sample_ids={"uploaded-863a4465"},
            anchor_image_document_names={"sample-blower-lci-retrofit.docx"},
            anchor_image_sample_ids={"uploaded-863a4465"},
            anchor_image_source_section_ids={"3"},
        )

        self.assertEqual([item[1].asset_id for item in promoted], [correct_asset_id])
        self.assertEqual(promoted[0][2]["source_section_relation"], "descendant")

    def test_asset_summary_boost_prefers_semantic_match(self) -> None:
        card = AssetCard(
            asset_card_id="asset:test",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=6,
            heading_path="3 高浓磨机电机控制及电机辅助设备监控系统方案",
            title="系统功能描述",
            display_title="电机辅助设备监控与联锁图",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/mock.png",
            preview_text="图摘要",
            retrieval_text="heading:系统功能描述",
            section_type="control_system",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={},
            semantic_summary_text="该图用于说明 LCI PLC、本地控制单元、励磁柜和同步电机辅助设备之间的监控与联锁关系。",
            semantic_summary_confidence=0.86,
        )

        strong = _asset_summary_boost(
            query="电机辅助设备监控与联锁方案",
            section_title="控制系统及联锁保护方案",
            card=card,
        )
        weak = _asset_summary_boost(
            query="供货范围表",
            section_title="供货范围",
            card=card,
        )

        self.assertGreater(strong, weak)
        self.assertGreater(strong, 0.1)

    def test_asset_quality_flags_and_penalty_demote_cropped_fragments(self) -> None:
        fragment = AssetCard(
            asset_card_id="asset:fragment",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-blower-lci-retrofit.docx",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=None,
            heading_path="3 系统方案 System Solution",
            title="symbol / cropped figure fragment",
            display_title="symbol / cropped figure fragment",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/fragment.png",
            preview_text="该候选图仅显示一个黑色三角形图形，无法确认其是否属于变频器系统示意图中的有效结构内容。",
            retrieval_text="symbol cropped figure fragment",
            section_type="main_circuit_scheme",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "title_hint": "symbol / cropped figure fragment",
                    "summary": "该候选图仅显示一个黑色三角形图形，无法确认其是否属于变频器系统示意图中的有效结构内容。",
                    "review_required": True,
                    "confidence": 0.28,
                }
            },
            semantic_summary_text="该候选图仅显示一个黑色三角形图形，无法确认其是否属于变频器系统示意图中的有效结构内容。",
            semantic_summary_confidence=0.28,
        )
        complete = AssetCard(
            asset_card_id="asset:complete",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="sample-pulp-mill-lci-solution.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=7,
            heading_path="4.1 LCI 变频软起系统方案",
            title="LCI变频软起系统图",
            display_title="LCI变频软起系统图",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/lci.png",
            preview_text="该图展示了LCI变频软起系统的主电力链路及其与本地PLC、励磁柜和DCS的接口关系。",
            retrieval_text="LCI 变频软起 系统图 主电力链路 本地PLC 励磁柜 DCS",
            section_type="main_circuit_scheme",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "title_hint": "LCI变频软起系统图",
                    "summary": "该图展示了LCI变频软起系统的主电力链路及其与本地PLC、励磁柜和DCS的接口关系。",
                    "review_required": False,
                    "confidence": 0.86,
                }
            },
            semantic_summary_text="该图展示了LCI变频软起系统的主电力链路及其与本地PLC、励磁柜和DCS的接口关系。",
            semantic_summary_confidence=0.86,
        )

        fragment_flags = _asset_quality_flags(card=fragment)
        complete_flags = _asset_quality_flags(card=complete)

        self.assertTrue(fragment_flags["low_information"])
        self.assertFalse(complete_flags["low_information"])
        self.assertTrue(complete_flags["complete_diagram"])
        self.assertGreater(
            _asset_noise_penalty(card=fragment, target_section_type="main_circuit_scheme"),
            _asset_noise_penalty(card=complete, target_section_type="main_circuit_scheme") + 0.5,
        )

    def test_asset_quality_flags_marks_logo_assets_low_information(self) -> None:
        logo = AssetCard(
            asset_card_id="asset:logo",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=1,
            heading_path="1 主要功能特点",
            title="大禹标识图",
            display_title="大禹标识图",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/logo.png",
            preview_text="DAYU ELECTRIC 公司标识",
            retrieval_text="大禹标识图 DAYU ELECTRIC",
            section_type="unknown",
            equipment_type="generic",
            content_form="figure",
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "title_hint": "大禹标识图",
                    "summary": "该图为公司标识，不是工程方案图。",
                    "confidence": 0.98,
                    "review_required": False,
                }
            },
            semantic_summary_text="该图为公司标识，不是工程方案图。",
            semantic_summary_confidence=0.98,
        )

        self.assertTrue(_asset_quality_flags(card=logo)["low_information"])

    def test_asset_source_binding_marks_missing_section_as_non_high_confidence(self) -> None:
        card = AssetCard(
            asset_card_id="asset:weak",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="样板.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=3,
            heading_path="4.1 LCI 变频软起系统方案",
            title="LCI变频软起系统图",
            display_title="LCI变频软起系统图",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/lci.png",
            preview_text="LCI 主回路拓扑。",
            retrieval_text="LCI 主回路拓扑",
            section_type="main_circuit_scheme",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={"sample_id": "sample-a"},
        )

        binding = _asset_source_binding(card=card)
        quality = _asset_quality_flags(card=card)

        self.assertEqual(binding["tier"], "medium")
        self.assertIn("source_section_id", binding["missing_fields"])
        self.assertFalse(binding["high_confidence_eligible"])
        self.assertTrue(quality["source_section_missing"])

    def test_build_preview_text_uses_semantic_summary_when_context_is_sparse(self) -> None:
        preview = _build_preview_text(
            title=None,
            caption=None,
            context_before=None,
            context_after=None,
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "summary": "该图用于说明变频软起装置与同步电机之间的主回路关系。",
                    "problem_solved": "解释软启动与工频切换逻辑。",
                    "confidence": 0.82,
                }
            },
        )

        self.assertIn("变频软起装置", preview)

    def test_build_asset_display_title_prefers_title_hint_for_garbled_title(self) -> None:
        display_title = _build_asset_display_title(
            title="1508 375 196 108 83 48 36 18 11 3 [A] [A] [A] [A]",
            heading_path="4.4.2 变频启动曲线",
            caption=None,
            page_no=19,
            semantic_summary={
                "status": "summarized",
                "title_hint": "变压器谐波波形图",
                "diagram_type": "波形图",
                "summary": "该图用于说明变压器在 LCI 工况下的电压电流波形。",
            },
        )

        self.assertEqual(display_title, "变压器谐波波形图")

    def test_derive_visual_role_downgrades_footer_logo_like_asset(self) -> None:
        role = _derive_visual_role(
            asset=SimpleNamespace(asset_type="figure", reuse_mode="reference_only"),
            metadata={
                "visual_role": "engineering_figure",
                "bbox": {"l": 57.29, "r": 134.47, "b": 20.51, "t": 45.55},
                "page_width": 595.32,
                "page_height": 841.92,
                "width": 154,
                "height": 50,
            },
            title="变压器一次侧和二次侧绕组间屏蔽层",
            caption=None,
            context_before="为实现一次侧和二次侧绕组的解耦，接地屏蔽层如下图所示。",
            context_after="HV: 高压侧正弦波电压",
        )

        self.assertEqual(role, "page_furniture")

    def test_derive_visual_role_keeps_large_named_diagram_as_engineering_figure(self) -> None:
        role = _derive_visual_role(
            asset=SimpleNamespace(asset_type="figure", reuse_mode="reference_only"),
            metadata={
                "visual_role": "page_furniture",
                "bbox": {"l": 83.78, "r": 544.68, "b": 517.33, "t": 689.26},
                "page_width": 595.32,
                "page_height": 841.92,
                "width": 921,
                "height": 344,
            },
            title="5.1.1 变频器系统示意图",
            caption=None,
            context_before="5.1 变频器配置 | 5.1.1 变频器系统示意图",
            context_after="5.1.2 变频器主要数据 | 版本",
        )

        self.assertEqual(role, "engineering_figure")

    def test_build_visual_retrieval_text_prefers_semantic_summary_fields(self) -> None:
        visual_text = _build_visual_retrieval_text(
            asset_type="figure",
            visual_role="engineering_figure",
            title="系统功能描述",
            display_title="LCI变频软起系统图",
            caption=None,
            heading_path="4.1 LCI 变频软起系统方案",
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "title_hint": "LCI变频软起系统图",
                    "diagram_type": "系统图",
                    "summary": "该图展示了主电力链路以及 PLC、励磁柜和 DCS 的接口关系。",
                    "problem_solved": "解释启动与同步切换过程中的主回路与控制边界。",
                    "key_components": ["PLC", "励磁柜", "DCS", "同步电机"],
                    "retrieval_keywords": ["主回路", "同步切换", "接口"],
                }
            },
        )

        self.assertIn("title_hint:LCI变频软起系统图", visual_text)
        self.assertIn("diagram_type:系统图", visual_text)
        self.assertIn("key_components:PLC 励磁柜 DCS 同步电机", visual_text)

    def test_compose_asset_score_prefers_visual_complete_diagram(self) -> None:
        complete = AssetCard(
            asset_card_id="asset:complete",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="案例A.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=12,
            heading_path="4.1 LCI 变频软起系统方案",
            title="LCI变频软起系统图",
            display_title="LCI变频软起系统图",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/lci.png",
            preview_text="该图展示了主电力链路以及 PLC、励磁柜和 DCS 的接口关系。",
            retrieval_text="LCI 变频软起 系统图 主电力链路 PLC 励磁柜 DCS",
            section_type="main_circuit_scheme",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "title_hint": "LCI变频软起系统图",
                    "diagram_type": "系统图",
                    "summary": "该图展示了主电力链路以及 PLC、励磁柜和 DCS 的接口关系。",
                    "problem_solved": "解释启动与同步切换过程中的主回路与控制边界。",
                    "confidence": 0.88,
                    "review_required": False,
                }
            },
            semantic_summary_text="LCI变频软起系统图；系统图；该图展示了主电力链路以及 PLC、励磁柜和 DCS 的接口关系。",
            semantic_summary_confidence=0.88,
            visual_retrieval_text="display_title:LCI变频软起系统图\ndiagram_type:系统图\nsummary:主电力链路 PLC 励磁柜 DCS",
        )
        fragment = AssetCard(
            asset_card_id="asset:fragment",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="案例B.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=12,
            heading_path="3 系统方案",
            title="symbol / cropped figure fragment",
            display_title="symbol / cropped figure fragment",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/fragment.png",
            preview_text="局部裁剪图，难以判断有效图意。",
            retrieval_text="fragment partial symbol",
            section_type="main_circuit_scheme",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={
                "semantic_summary": {
                    "status": "summarized",
                    "title_hint": "symbol / cropped figure fragment",
                    "summary": "局部裁剪图，难以判断有效图意。",
                    "confidence": 0.24,
                    "review_required": True,
                }
            },
            semantic_summary_text="局部裁剪图，难以判断有效图意。",
            semantic_summary_confidence=0.24,
            visual_retrieval_text="fragment partial symbol",
        )
        target_taxonomy = infer_target_taxonomy(
            {
                "title": "主回路与控制接口示意",
                "purpose": "说明主回路连接关系、同步切换以及 PLC/DCS 接口边界。",
                "expected_evidence_types": ["figure"],
            }
        )

        complete_score, complete_breakdown = _compose_asset_score(
            query="主回路与控制接口示意",
            section_title="主回路与控制接口示意",
            expected_types=["figure"],
            target_taxonomy=target_taxonomy,
            anchor_document_names=set(),
            anchor_headings=[],
            card=complete,
            textual_semantic_score=0.18,
            visual_semantic_score=0.82,
        )
        fragment_score, fragment_breakdown = _compose_asset_score(
            query="主回路与控制接口示意",
            section_title="主回路与控制接口示意",
            expected_types=["figure"],
            target_taxonomy=target_taxonomy,
            anchor_document_names=set(),
            anchor_headings=[],
            card=fragment,
            textual_semantic_score=0.24,
            visual_semantic_score=0.05,
        )

        self.assertGreater(complete_score, fragment_score)
        self.assertGreater(complete_breakdown["visual"], fragment_breakdown["visual"])
        self.assertLess(complete_breakdown["penalty"], fragment_breakdown["penalty"])

    def test_to_result_includes_asset_retrieval_breakdown(self) -> None:
        card = AssetCard(
            asset_card_id="asset:complete",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="案例A.pdf",
            doc_type="historical_proposal",
            asset_type="figure",
            visual_role="engineering_figure",
            risk_level="medium",
            usage_mode="reference_only",
            review_required=True,
            page_no=12,
            heading_path="4.1 LCI 变频软起系统方案",
            title="LCI变频软起系统图",
            display_title="LCI变频软起系统图",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/lci.png",
            preview_text="该图展示了主电力链路以及 PLC、励磁柜和 DCS 的接口关系。",
            retrieval_text="LCI 变频软起 系统图",
            section_type="main_circuit_scheme",
            equipment_type="motor_drive",
            content_form="figure",
            metadata={},
            semantic_summary_text="该图展示了主电力链路以及 PLC、励磁柜和 DCS 的接口关系。",
            semantic_summary_confidence=0.88,
            visual_retrieval_text="display_title:LCI变频软起系统图\ndiagram_type:系统图",
        )

        result = _to_result(
            card=card,
            score=0.77,
            section_title="主回路与控制接口示意",
            reason_trace=["branch=textual+visual", "visual=0.620", "final=0.770"],
            score_breakdown={
                "branch": "textual+visual",
                "visual_backend": "clip",
                "visual_source": "image",
                "visual": 0.62,
                "final": 0.77,
            },
        )

        self.assertIn("retrieval_score_breakdown", result.metadata)
        self.assertEqual(result.metadata["retrieval_score_breakdown"]["branch"], "textual+visual")
        self.assertEqual(result.metadata["visual_backend"], "clip")
        self.assertEqual(result.metadata["visual_source"], "image")
        self.assertIn("visual_retrieval_text_preview", result.metadata)
        self.assertEqual(result.reason_trace[0], "branch=textual+visual")
        self.assertEqual(result.score_breakdown["final"], 0.77)

    def test_compose_asset_score_disables_visual_branch_for_text_only_context(self) -> None:
        card = AssetCard(
            asset_card_id="asset:text-only",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="案例A.pdf",
            doc_type="historical_proposal",
            asset_type="table",
            visual_role="table_asset",
            risk_level="high",
            usage_mode="reference_only",
            review_required=True,
            page_no=8,
            heading_path="5.1 控制接口点表",
            title="控制接口点表",
            display_title="控制接口点表",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/table.png",
            preview_text="DCS 至变频器 DI/DO、AI/AO 接口点表。",
            retrieval_text="控制接口 点表 DCS PLC AI AO DI DO",
            section_type="communication_interface",
            equipment_type="vfd",
            content_form="parameter_table",
            metadata={},
            semantic_summary_text=None,
            semantic_summary_confidence=0.0,
            visual_retrieval_text="display_title:控制接口点表",
        )
        target_taxonomy = infer_target_taxonomy(
            {
                "title": "控制接口与点表",
                "purpose": "说明 DCS / PLC 接口点表。",
                "expected_evidence_types": ["table", "parameter"],
            }
        )

        score, breakdown = _compose_asset_score(
            query="控制接口与点表",
            section_title="控制接口与点表",
            expected_types=["table", "parameter"],
            target_taxonomy=target_taxonomy,
            anchor_document_names=set(),
            anchor_headings=[],
            card=card,
            textual_semantic_score=0.66,
            visual_semantic_score=0.91,
            visual_collection_score=0.88,
            visual_enabled=False,
            visual_backend="disabled",
            visual_source="disabled",
        )

        self.assertGreater(score, 0.0)
        self.assertEqual(breakdown["branch"], "textual_only")
        self.assertEqual(breakdown["visual"], 0.0)
        self.assertEqual(breakdown["visual_backend"], "disabled")

    def test_asset_retrieval_service_skips_visual_branch_when_figure_not_requested(self) -> None:
        visual_embedder = FakeVisualEmbedder()
        service = AssetRetrievalService(embedder=FakeEmbedder(), visual_embedder=visual_embedder)
        card = AssetCard(
            asset_card_id="asset:table-1",
            asset_id=uuid4(),
            document_id=None,
            raw_document_id=uuid4(),
            project_id=uuid4(),
            document_name="案例A.pdf",
            doc_type="historical_proposal",
            asset_type="table",
            visual_role="table_asset",
            risk_level="high",
            usage_mode="reference_only",
            review_required=True,
            page_no=8,
            heading_path="5.1 控制接口点表",
            title="控制接口点表",
            display_title="控制接口点表",
            caption=None,
            source_ref=None,
            asset_uri="/tmp/table.png",
            preview_text="DCS 至变频器 DI/DO、AI/AO 接口点表。",
            retrieval_text="控制接口 点表 DCS PLC AI AO DI DO",
            section_type="communication_interface",
            equipment_type="vfd",
            content_form="parameter_table",
            metadata={},
            semantic_summary_text=None,
            semantic_summary_confidence=0.0,
            visual_retrieval_text="display_title:控制接口点表",
        )

        with patch.object(service, "_load_asset_cards", return_value=[card]):
            response = asyncio.run(
                service.search_project_assets(
                    session=FakeProjectSession(SimpleNamespace(id=uuid4())),
                    project_id=uuid4(),
                    query="控制接口 DI DO 点表",
                    top_k=3,
                    asset_types=["table"],
                    section_context={
                        "section_title": "控制接口与点表",
                        "expected_evidence_types": ["table", "parameter"],
                    },
                )
            )

        self.assertEqual(visual_embedder.build_query_calls, 0)
        self.assertEqual(visual_embedder.embed_asset_calls, 0)
        self.assertIsNotNone(response.search_trace)
        self.assertFalse(bool(response.search_trace.visual_branch_enabled))
        self.assertEqual(response.results[0].score_breakdown["branch"], "textual_only")
        self.assertEqual(response.results[0].reason_trace[0], "branch=textual_only")

    def test_build_chunk_retrieval_text_prefers_semantic_retrieval_text(self) -> None:
        chunk = SimpleNamespace(
            meta={"semantic_retrieval_text": "文档A\n控制接口说明\nAI AO DI DO"},
            heading_path="4.2 控制接口说明",
            content="原始正文",
        )

        text = _build_chunk_retrieval_text(
            chunk=chunk,
            document_name="文档A.pdf",
            payload={},
        )

        self.assertEqual(text, "文档A\n控制接口说明\nAI AO DI DO")

    def test_rank_chunk_candidates_prefers_sparse_and_rerank_supported_match(self) -> None:
        best_chunk = SimpleNamespace(meta={}, heading_path="4.2 控制接口说明", content="控制接口")
        noisy_chunk = SimpleNamespace(meta={}, heading_path="1. 项目概述", content="项目概述")
        ranked = _rank_chunk_candidates(
            query_text="控制接口硬接点说明",
            search_mode="hybrid",
            candidates=[
                {
                    "chunk": noisy_chunk,
                    "document": SimpleNamespace(filename="案例B.pdf"),
                    "dense_score": 0.89,
                    "retrieval_text": "案例B\n项目概述\n交付范围与组织安排",
                },
                {
                    "chunk": best_chunk,
                    "document": SimpleNamespace(filename="案例A.pdf"),
                    "dense_score": 0.82,
                    "retrieval_text": "案例A\n控制接口说明\nDCS PLC AI AO DI DO 硬接点",
                },
            ],
            reranker=FakeReranker(),
        )

        self.assertIs(ranked[0]["chunk"], best_chunk)
        self.assertTrue(any(item.startswith("semantic_match=") for item in ranked[0]["reason_trace"]))
        self.assertEqual(ranked[0]["score_breakdown"]["final"], ranked[0]["hybrid_score"])
        self.assertGreater(ranked[0]["score_breakdown"]["semantic_raw"], 0.0)
        self.assertGreater(ranked[0]["score_breakdown"]["hybrid_rrf"], 0.0)
        self.assertGreater(ranked[0]["score_breakdown"]["hybrid_rerank"], 0.0)
        self.assertGreater(ranked[0]["score_breakdown"]["sparse"], ranked[1]["score_breakdown"]["sparse"])
        self.assertGreater(ranked[0]["score_breakdown"]["rerank"], ranked[1]["score_breakdown"]["rerank"])

    def test_retriever_keyword_mode_can_return_sparse_only_candidate(self) -> None:
        chunk_id = uuid4()
        document_id = uuid4()
        project_id = uuid4()
        sparse_chunk = SimpleNamespace(
            id=chunk_id,
            document_id=document_id,
            heading_path="4.2 控制接口说明",
            chunk_type="PLAIN",
            content="DCS 至变频器提供 DI/DO、AI/AO 和联锁接口。",
            meta={"semantic_retrieval_text": "案例A\n4.2 控制接口说明\nDCS PLC AI AO DI DO 联锁接口"},
        )
        sparse_document = SimpleNamespace(
            id=document_id,
            filename="案例A.pdf",
            project_id=project_id,
            doc_type="historical_proposal",
        )
        retriever = Retriever(
            embedder=FakeEmbedder(),
            qdrant=FakeQdrant(hits=[]),
            reranker=FakeReranker(),
        )
        session = FakeAsyncSession(rows_by_call=[[(sparse_chunk, sparse_document)]])

        response = asyncio.run(
            retriever.search(
                session=session,
                request=SimpleNamespace(
                    query="控制接口硬接点说明",
                    project_id=project_id,
                    top_k=5,
                    filters=SimpleNamespace(model_dump=lambda exclude_none=True: {"doc_type": "historical_proposal", "chunk_type": ["PLAIN"]}),
                    search_mode="keyword",
                ),
            )
        )

        self.assertEqual(response.total, 1)
        self.assertEqual(response.search_trace.sparse_hit_count, 1)
        self.assertEqual(response.search_trace.dense_hit_count, 0)
        self.assertEqual(response.search_trace.sparse_candidate_count, 1)
        self.assertEqual(response.results[0].chunk_id, chunk_id)
        self.assertIn("candidate_sources=sparse", response.results[0].reason_trace[0])
        self.assertGreater(response.results[0].score_breakdown["sparse"], 0.0)
        self.assertEqual(response.results[0].metadata["candidate_sources"], ["sparse"])

    def test_retriever_hybrid_mode_merges_dense_and_sparse_candidate_sources(self) -> None:
        chunk_id = uuid4()
        document_id = uuid4()
        project_id = uuid4()
        dense_sparse_chunk = SimpleNamespace(
            id=chunk_id,
            document_id=document_id,
            heading_path="4.2 控制接口说明",
            chunk_type="PLAIN",
            content="DCS 至变频器提供 DI/DO、AI/AO 和联锁接口。",
            meta={"semantic_retrieval_text": "案例A\n4.2 控制接口说明\nDCS PLC AI AO DI DO 联锁接口"},
        )
        dense_sparse_document = SimpleNamespace(
            id=document_id,
            filename="案例A.pdf",
            project_id=project_id,
            doc_type="historical_proposal",
        )
        qdrant_hit = SimpleNamespace(payload={"chunk_id": str(chunk_id)}, score=0.88)
        retriever = Retriever(
            embedder=FakeEmbedder(),
            qdrant=FakeQdrant(hits=[qdrant_hit]),
            reranker=FakeReranker(),
        )
        session = FakeAsyncSession(
            rows_by_call=[
                [(dense_sparse_chunk, dense_sparse_document)],
                [(dense_sparse_chunk, dense_sparse_document)],
            ]
        )

        response = asyncio.run(
            retriever.search(
                session=session,
                request=SimpleNamespace(
                    query="控制接口硬接点说明",
                    project_id=project_id,
                    top_k=5,
                    filters=SimpleNamespace(model_dump=lambda exclude_none=True: {"doc_type": "historical_proposal", "chunk_type": ["PLAIN"]}),
                    search_mode="hybrid",
                ),
            )
        )

        self.assertEqual(response.total, 1)
        self.assertEqual(response.search_trace.dense_hit_count, 1)
        self.assertEqual(response.search_trace.dense_candidate_count, 1)
        self.assertEqual(response.search_trace.sparse_candidate_count, 1)
        self.assertEqual(response.results[0].metadata["candidate_sources"], ["dense", "sparse"])
        self.assertIn("candidate_sources=dense,sparse", response.results[0].reason_trace[0])


if __name__ == "__main__":
    unittest.main()
