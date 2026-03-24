from __future__ import annotations

from app.services.llm.client import LLMClient, LLMRequest, LLMResponse, TaskType
from app.services.llm.prompts import build_holistic_prompts


class HolisticAgent:
    def __init__(self, *, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    async def finalize(
        self,
        *,
        task_id: str,
        outline_title: str,
        global_params: dict,
        all_sections_markdown: str,
    ) -> LLMResponse:
        system_prompt, user_prompt = build_holistic_prompts(
            global_params=global_params,
            all_sections_markdown=all_sections_markdown,
        )
        return await self.llm_client.invoke(
            LLMRequest(
                task_type=TaskType.HOLISTIC,
                session_id=task_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=2200,
                metadata={
                    "outline_title": outline_title,
                    "sections_markdown": all_sections_markdown,
                },
            )
        )
