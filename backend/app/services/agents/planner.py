from __future__ import annotations

from app.services.llm.client import LLMClient, LLMRequest, LLMResponse, TaskType
from app.services.llm.prompts import build_outline_prompts


class PlannerAgent:
    def __init__(self, *, llm_client: LLMClient | None = None) -> None:
        self.llm_client = llm_client or LLMClient()

    async def generate_outline(
        self,
        *,
        task_id: str,
        project_name: str,
        instructions: str,
        global_params: dict,
        rfp_context: str,
        outline_examples: list[dict] | None = None,
    ) -> LLMResponse:
        system_prompt, user_prompt = build_outline_prompts(
            project_name=project_name,
            instructions=instructions,
            global_params=global_params,
            rfp_context=rfp_context,
            outline_examples=outline_examples or [],
        )
        return await self.llm_client.invoke(
            LLMRequest(
                task_type=TaskType.OUTLINE,
                session_id=task_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=1200,
                metadata={
                    "project_name": project_name,
                    "instructions": instructions,
                    "global_params": global_params,
                    "outline_examples": outline_examples or [],
                },
            )
        )
