from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.config import Settings, get_settings
from app.services.agents.executor import ExecutorAgent
from app.services.llm.client import LLMClient, LLMRequest, TaskType
from app.services.llm.prompts import SECTION_QUALITY_REVIEW_SCHEMA, build_section_quality_prompts


INTERNAL_HEADING_PATTERNS = (
    re.compile(r"^(建议插入图表|建议图表|建议参考资产|推荐资产|可用参考资料|可用复用包|替换与禁用约束|参考摘要)$", re.IGNORECASE),
    re.compile(r"^(图表建议|插图建议|图表清单)$", re.IGNORECASE),
)
LATIN_ENUM_HEADING_PATTERN = re.compile(r"^[A-Z]\.\s*.+$")
GENERIC_HEADING_PATTERN = re.compile(r"^(概述|说明|补充说明|其他|附加说明)$")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{2,6})\s+(.+?)\s*$")


@dataclass(slots=True)
class SectionQualityIssue:
    code: str
    severity: str
    target: str
    message: str
    suggested_fix: str
    source: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "target": self.target,
            "message": self.message,
            "suggested_fix": self.suggested_fix,
            "source": self.source,
        }


@dataclass(slots=True)
class SectionQualityReview:
    passed: bool
    score: float
    summary: str
    issues: list[SectionQualityIssue]
    rewrite_instruction: str
    source: str = "rule+llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "score": round(self.score, 4),
            "summary": self.summary,
            "issues": [issue.to_dict() for issue in self.issues],
            "rewrite_instruction": self.rewrite_instruction,
            "source": self.source,
        }


class SectionQualityGateService:
    def __init__(
        self,
        *,
        llm_client: LLMClient | None = None,
        executor: ExecutorAgent | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client or LLMClient()
        self.executor = executor or ExecutorAgent(llm_client=self.llm_client)

    async def review_and_repair(
        self,
        *,
        task_id: str,
        section: dict[str, Any],
        outline_title: str,
        global_params: dict[str, Any],
        content_md: str,
        recommended_assets: list[dict[str, Any]] | None = None,
        allow_rewrite: bool = True,
    ) -> tuple[str, str, dict[str, Any]]:
        initial_review = await self.review(
            task_id=task_id,
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            content_md=content_md,
            recommended_assets=recommended_assets,
        )
        best_content = content_md
        best_review = initial_review
        rewrite_attempted = False
        rewrite_applied = False
        rewrite_discard_reason: str | None = None

        if allow_rewrite and not initial_review.passed:
            rewrite_attempted = True
            try:
                rewritten = await self.executor.rewrite_section(
                    task_id=task_id,
                    section_context=_build_quality_rewrite_context(
                        section=section,
                        initial_review=initial_review,
                        global_params=global_params,
                    ),
                    selected_text=content_md,
                    instruction=initial_review.rewrite_instruction,
                    global_params=global_params,
                )
                candidate_content = str(rewritten.content or "").strip()
                if not _rewrite_looks_safe(original=content_md, rewritten=candidate_content):
                    rewrite_discard_reason = "rewrite_unsafe"
                else:
                    follow_review = await self.review(
                        task_id=task_id,
                        section=section,
                        outline_title=outline_title,
                        global_params=global_params,
                        content_md=candidate_content,
                        recommended_assets=recommended_assets,
                    )
                    if _prefer_rewrite(initial=initial_review, follow_up=follow_review):
                        best_content = candidate_content
                        best_review = follow_review
                        rewrite_applied = True
                    else:
                        rewrite_discard_reason = "rewrite_not_better"
            except Exception as exc:  # noqa: BLE001
                rewrite_discard_reason = f"rewrite_error:{exc}"

        final_status = "generated" if best_review.passed else "review_required"
        quality_meta = {
            "status": "passed" if best_review.passed else "review_required",
            "score": round(best_review.score, 4),
            "summary": best_review.summary,
            "issues": [issue.to_dict() for issue in best_review.issues],
            "rewrite_instruction": best_review.rewrite_instruction,
            "rewrite_attempted": rewrite_attempted,
            "rewrite_applied": rewrite_applied,
            "rewrite_discard_reason": rewrite_discard_reason,
            "initial_review": initial_review.to_dict(),
            "final_review": best_review.to_dict(),
        }
        return best_content, final_status, quality_meta

    async def review(
        self,
        *,
        task_id: str,
        section: dict[str, Any],
        outline_title: str,
        global_params: dict[str, Any],
        content_md: str,
        recommended_assets: list[dict[str, Any]] | None = None,
    ) -> SectionQualityReview:
        rule_issues = analyze_section_heading_quality(
            section_title=str(section.get("title") or ""),
            content_md=content_md,
        )
        llm_review = await self._review_with_llm(
            task_id=task_id,
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            content_md=content_md,
            recommended_assets=recommended_assets or [],
        )
        merged_issues = _merge_issues(rule_issues, llm_review.issues)
        blocking_issue_count = sum(1 for issue in merged_issues if issue.severity == "high")
        medium_issue_count = sum(1 for issue in merged_issues if issue.severity == "medium")
        score = min(llm_review.score, _score_from_rule_issues(rule_issues))
        passed = llm_review.passed and blocking_issue_count == 0 and score >= 0.78
        if blocking_issue_count > 0:
            passed = False
        if medium_issue_count >= 2 and score < 0.84:
            passed = False
        summary = llm_review.summary or ("章节质量通过。" if passed else "章节存在标题、结构或客户口径问题。")
        rewrite_instruction = _build_rewrite_instruction(
            section=section,
            llm_instruction=llm_review.rewrite_instruction,
            issues=merged_issues,
        )
        return SectionQualityReview(
            passed=passed,
            score=score,
            summary=summary,
            issues=merged_issues,
            rewrite_instruction=rewrite_instruction,
        )

    async def _review_with_llm(
        self,
        *,
        task_id: str,
        section: dict[str, Any],
        outline_title: str,
        global_params: dict[str, Any],
        content_md: str,
        recommended_assets: list[dict[str, Any]],
    ) -> SectionQualityReview:
        system_prompt, user_prompt = build_section_quality_prompts(
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            content_md=content_md,
            recommended_assets=recommended_assets,
        )
        try:
            response = await self.llm_client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_QUALITY,
                    session_id=f"{task_id}-quality",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    max_tokens=1400,
                    json_schema=SECTION_QUALITY_REVIEW_SCHEMA,
                    metadata={"section": section},
                )
            )
            payload = json.loads(response.content)
        except Exception as exc:  # noqa: BLE001
            fallback_issue = SectionQualityIssue(
                code="SQ999",
                severity="medium",
                target="章节整体",
                message=f"章节自动质检失败，已降级为规则审查：{exc}",
                suggested_fix="请人工检查小标题风格、内部提示语和章节完整度。",
                source="llm_fallback",
            )
            return SectionQualityReview(
                passed=False,
                score=0.6,
                summary="章节自动质检调用失败，已回退到规则审查。",
                issues=[fallback_issue],
                rewrite_instruction="请统一小标题风格、删除内部提示语，并保持技术内容密度与图表占位符不变。",
                source="rule_fallback",
            )

        llm_issues: list[SectionQualityIssue] = []
        for item in payload.get("issues") or []:
            llm_issues.append(
                SectionQualityIssue(
                    code=str(item.get("code") or "SQLLM"),
                    severity=_normalize_severity(item.get("severity")),
                    target=str(item.get("target") or "章节整体").strip() or "章节整体",
                    message=str(item.get("message") or "").strip() or "章节质量需人工确认。",
                    suggested_fix=str(item.get("suggested_fix") or "").strip() or "请按章节目标重写并统一标题风格。",
                    source="llm",
                )
            )

        return SectionQualityReview(
            passed=bool(payload.get("pass")),
            score=_clamp_score(payload.get("score")),
            summary=str(payload.get("summary") or "").strip(),
            issues=llm_issues,
            rewrite_instruction=str(payload.get("rewrite_instruction") or "").strip(),
            source="llm",
        )


def analyze_section_heading_quality(*, section_title: str, content_md: str) -> list[SectionQualityIssue]:
    headings = _extract_markdown_headings(content_md)
    issues: list[SectionQualityIssue] = []
    seen: set[str] = set()
    for level, heading in headings:
        normalized = heading.strip()
        if level <= 2:
            continue
        if any(pattern.match(normalized) for pattern in INTERNAL_HEADING_PATTERNS):
            issues.append(
                SectionQualityIssue(
                    code="SQ001",
                    severity="high",
                    target=normalized,
                    message=f"小标题“{normalized}”属于内部编辑提示，不应出现在客户稿中。",
                    suggested_fix="删除该内部标题，改为自然融入正文或换成技术主题型小标题。",
                )
            )
        if LATIN_ENUM_HEADING_PATTERN.match(normalized):
            issues.append(
                SectionQualityIssue(
                    code="SQ002",
                    severity="high",
                    target=normalized,
                    message=f"小标题“{normalized}”存在英文编号风格，与中文技术方案章节风格不一致。",
                    suggested_fix="改成直接表达技术对象或功能主题的中文小标题，不要使用 A./B. 这类编号。",
                )
            )
        if GENERIC_HEADING_PATTERN.match(normalized) and normalized not in section_title:
            issues.append(
                SectionQualityIssue(
                    code="SQ003",
                    severity="medium",
                    target=normalized,
                    message=f"小标题“{normalized}”过于空泛，无法体现本段技术主题。",
                    suggested_fix="将小标题改成能直接表达系统、回路、控制对象或功能主题的技术标题。",
                )
            )
        dedupe_key = normalized.casefold()
        if dedupe_key in seen:
            issues.append(
                SectionQualityIssue(
                    code="SQ004",
                    severity="medium",
                    target=normalized,
                    message=f"小标题“{normalized}”重复出现，章节结构不够清晰。",
                    suggested_fix="合并重复小标题，或将其改成更具体的技术主题。",
                )
            )
        else:
            seen.add(dedupe_key)
    return _dedupe_issue_list(issues)


def _extract_markdown_headings(content_md: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for line in str(content_md or "").splitlines():
        match = MARKDOWN_HEADING_PATTERN.match(line.strip())
        if not match:
            continue
        headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def _merge_issues(rule_issues: list[SectionQualityIssue], llm_issues: list[SectionQualityIssue]) -> list[SectionQualityIssue]:
    merged = [*rule_issues]
    seen = {(issue.code, issue.target, issue.message) for issue in merged}
    for issue in llm_issues:
        signature = (issue.code, issue.target, issue.message)
        if signature in seen:
            continue
        merged.append(issue)
        seen.add(signature)
    return merged


def _build_rewrite_instruction(
    *,
    section: dict[str, Any],
    llm_instruction: str,
    issues: list[SectionQualityIssue],
) -> str:
    section_title = str(section.get("title") or "当前章节").strip() or "当前章节"
    lines = [
        f"请把《{section_title}》整理为客户可阅读的正式技术章节。",
        "统一三级小标题风格，只保留能直接表达技术对象、系统功能或方案主题的小标题。",
        "删除内部编辑痕迹、素材清单式标题和对用户不可见的提示语。",
        "保持现有技术信息密度、列表结构、参数表达和 [[ASSET:...]] 占位符，不要把技术段压缩成空泛总结。",
    ]
    for issue in issues[:4]:
        lines.append(f"- {issue.suggested_fix}")
    if llm_instruction:
        lines.append(f"- {llm_instruction}")
    return "\n".join(lines)


def _build_quality_rewrite_context(
    *,
    section: dict[str, Any],
    initial_review: SectionQualityReview,
    global_params: dict[str, Any],
) -> str:
    key_params = ", ".join(f"{key}={value}" for key, value in global_params.items() if value not in (None, "", [], {})) or "无"
    return "\n".join(
        [
            f"章节标题: {section.get('title') or ''}",
            f"章节目的: {section.get('purpose') or section.get('description') or ''}",
            f"当前关键参数: {key_params}",
            f"当前质量摘要: {initial_review.summary}",
            "当前问题:",
            *[f"- {issue.message}" for issue in initial_review.issues[:6]],
        ]
    )


def _prefer_rewrite(*, initial: SectionQualityReview, follow_up: SectionQualityReview) -> bool:
    if follow_up.passed and not initial.passed:
        return True
    initial_high = sum(1 for item in initial.issues if item.severity == "high")
    follow_high = sum(1 for item in follow_up.issues if item.severity == "high")
    if follow_high < initial_high:
        return True
    if follow_up.score >= initial.score + 0.04:
        return True
    if follow_up.score >= initial.score and len(follow_up.issues) < len(initial.issues):
        return True
    return False


def _rewrite_looks_safe(*, original: str, rewritten: str) -> bool:
    original_text = str(original or "").strip()
    rewritten_text = str(rewritten or "").strip()
    if not rewritten_text:
        return False
    if "[[ASSET:" in original_text and rewritten_text.count("[[ASSET:") < original_text.count("[[ASSET:"):
        return False
    if len(rewritten_text) < max(40, int(len(original_text) * 0.55)):
        return False
    if any(token in rewritten_text for token in ("章节标题:", "章节目的:", "当前关键参数:", "当前问题:")):
        return False
    return True


def _score_from_rule_issues(issues: list[SectionQualityIssue]) -> float:
    score = 0.96
    for issue in issues:
        if issue.severity == "high":
            score -= 0.24
        elif issue.severity == "medium":
            score -= 0.12
        else:
            score -= 0.05
    return max(0.0, min(1.0, score))


def _normalize_severity(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"high", "medium", "low"}:
        return normalized
    return "medium"


def _clamp_score(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _dedupe_issue_list(issues: list[SectionQualityIssue]) -> list[SectionQualityIssue]:
    deduped: list[SectionQualityIssue] = []
    seen: set[tuple[str, str, str]] = set()
    for issue in issues:
        signature = (issue.code, issue.target, issue.message)
        if signature in seen:
            continue
        deduped.append(issue)
        seen.add(signature)
    return deduped
