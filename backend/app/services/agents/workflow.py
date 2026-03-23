from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.agents.executor import ExecutorAgent
from app.services.agents.holistic import HolisticAgent
from app.services.agents.planner import PlannerAgent
from app.services.agents.retriever import RetrieverAgent
from app.services.agents.state import WorkflowState
from app.services.llm.client import LLMResponse


@dataclass(slots=True)
class ReviewDraft:
    section_index: int
    review_type: str
    description: str
    payload: dict


class WorkflowOrchestrator:
    def __init__(
        self,
        *,
        planner: PlannerAgent | None = None,
        retriever: RetrieverAgent | None = None,
        executor: ExecutorAgent | None = None,
        holistic: HolisticAgent | None = None,
    ) -> None:
        self.planner = planner or PlannerAgent()
        self.retriever = retriever or RetrieverAgent()
        self.executor = executor or ExecutorAgent()
        self.holistic = holistic or HolisticAgent()

    async def generate_outline(
        self,
        *,
        state: WorkflowState,
        instructions: str,
    ) -> LLMResponse:
        state.current_agent = "planner"
        response = await self.planner.generate_outline(
            task_id=state.task_id,
            project_name=state.project_name,
            instructions=instructions,
            global_params=state.global_params,
            rfp_context=state.rfp_context,
        )
        state.record_usage(total_tokens=response.total_tokens, cost_estimate=response.cost_estimate)
        return response

    async def generate_sections(
        self,
        *,
        session: AsyncSession,
        state: WorkflowState,
        document_id,
    ) -> tuple[list[dict], list[ReviewDraft]]:
        outline = state.outline or {"title": "技术方案", "sections": []}
        sections_state: list[dict] = []
        review_drafts: list[ReviewDraft] = []

        for index, section in enumerate(outline.get("sections", [])):
            state.current_section_idx = index
            state.current_agent = "retriever"
            retrieved_context, references = await self.retriever.retrieve_context(
                session=session,
                document_id=document_id,
                keywords=[*section.get("keywords", []), section.get("title", "")],
            )
            if references:
                state.referenced_sources.extend(references)

            state.current_agent = "executor"
            response = await self.executor.write_section(
                task_id=state.task_id,
                section=section,
                global_params=state.global_params,
                retrieved_context=retrieved_context,
                outline_title=outline.get("title", "技术方案"),
            )
            state.record_usage(total_tokens=response.total_tokens, cost_estimate=response.cost_estimate)
            state.set_section_content(index, response.content)

            section_state = {
                "index": section.get("index", index),
                "title": section.get("title"),
                "description": section.get("description"),
                "keywords": section.get("keywords", []),
                "status": "reviewing",
                "content": response.content,
                "token_count": response.total_tokens,
            }
            sections_state.append(section_state)
            review_drafts.append(
                ReviewDraft(
                    section_index=index,
                    review_type="content_review",
                    description=f"章节《{section.get('title', f'章节 {index + 1}')}》已生成，请确认内容是否满足要求。",
                    payload={
                        "section_title": section.get("title"),
                        "section_preview": response.content[:240],
                        "source_refs": references,
                    },
                )
            )

        state.current_section_idx = -1
        return sections_state, review_drafts

    async def rewrite_section(
        self,
        *,
        state: WorkflowState,
        section_index: int,
        section_context: str,
        selected_text: str,
        instruction: str,
    ) -> LLMResponse:
        state.current_agent = "executor"
        state.current_section_idx = section_index
        response = await self.executor.rewrite_section(
            task_id=state.task_id,
            section_context=section_context,
            selected_text=selected_text,
            instruction=instruction,
            global_params=state.global_params,
        )
        state.record_usage(total_tokens=response.total_tokens, cost_estimate=response.cost_estimate)
        state.set_section_content(section_index, response.content)
        state.current_section_idx = -1
        return response

    async def finalize(self, *, state: WorkflowState) -> LLMResponse:
        state.current_agent = "holistic"
        response = await self.holistic.finalize(
            task_id=state.task_id,
            outline_title=(state.outline or {}).get("title", "技术方案"),
            global_params=state.global_params,
            all_sections_markdown=state.to_sections_markdown((state.outline or {}).get("title")),
        )
        state.record_usage(total_tokens=response.total_tokens, cost_estimate=response.cost_estimate)
        state.current_agent = "done"
        return response
