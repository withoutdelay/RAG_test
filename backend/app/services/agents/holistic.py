from __future__ import annotations

import math

from app.services.llm.client import LLMClient, LLMRequest, LLMResponse, TaskType
from app.services.llm.prompts import build_holistic_prompts

# Minimum and maximum token budget for the holistic finalization pass.
# The actual budget scales with the total section content length.
HOLISTIC_MIN_TOKENS = 4000
HOLISTIC_MAX_TOKENS = 12000
# Ratio of estimated input tokens to allocate as output budget.
# 1.15 accounts for ~15% overhead from transitions, formatting fixes, etc.
HOLISTIC_TOKEN_RATIO = 1.15


def _estimate_holistic_max_tokens(all_sections_markdown: str) -> int:
    """Dynamically estimate the max_tokens budget for the holistic pass.

    The output needs to be at least as long as the combined section input
    (since the holistic agent should *not* compress content), plus a small
    margin for transitions and formatting.  We clamp to a reasonable range.
    """
    # Rough estimate: 1 token ≈ 2 CJK characters or 4 ASCII characters
    char_count = len(all_sections_markdown)
    cjk_chars = sum(1 for ch in all_sections_markdown if '\u4e00' <= ch <= '\u9fff')
    ascii_chars = char_count - cjk_chars
    estimated_tokens = math.ceil(cjk_chars / 2 + ascii_chars / 4)
    budget = math.ceil(estimated_tokens * HOLISTIC_TOKEN_RATIO)
    return max(HOLISTIC_MIN_TOKENS, min(budget, HOLISTIC_MAX_TOKENS))


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
        max_tokens = _estimate_holistic_max_tokens(all_sections_markdown)
        return await self.llm_client.invoke(
            LLMRequest(
                task_type=TaskType.HOLISTIC,
                session_id=task_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_tokens=max_tokens,
                metadata={
                    "outline_title": outline_title,
                    "sections_markdown": all_sections_markdown,
                },
            )
        )
