from __future__ import annotations

from app.services.llm.client import LLMClient, LLMRequest, LLMResponse, TaskType
from app.services.llm.prompts import build_rewrite_prompts, build_section_prompts


class ExecutorAgent:
    def __init__(self, *, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    async def write_section(
        self,
        *,
        task_id: str,
        section: dict,
        global_params: dict,
        retrieved_context: str,
        outline_title: str,
        recommended_assets: list[dict] | None = None,
    ) -> LLMResponse:
        system_prompt, user_prompt = build_section_prompts(
            section=section,
            global_params=global_params,
            retrieved_context=retrieved_context,
            outline_title=outline_title,
            recommended_assets=recommended_assets or [],
        )
        return await self.llm_client.invoke(
            LLMRequest(
                task_type=TaskType.SECTION_WRITE,
                session_id=task_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1600,
                metadata={
                    "section": section,
                    "retrieved_context": retrieved_context,
                    "recommended_assets": recommended_assets or [],
                },
            )
        )

    async def rewrite_section(
        self,
        *,
        task_id: str,
        section_context: str,
        selected_text: str,
        instruction: str,
        global_params: dict,
    ) -> LLMResponse:
        system_prompt, user_prompt = build_rewrite_prompts(
            section_context=section_context,
            selected_text=selected_text,
            user_instruction=instruction,
            global_params=global_params,
        )
        return await self.llm_client.invoke(
            LLMRequest(
                task_type=TaskType.REWRITE,
                session_id=task_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1200,
                metadata={
                    "section_context": section_context,
                    "selected_text": selected_text,
                    "instruction": instruction,
                },
            )
        )
