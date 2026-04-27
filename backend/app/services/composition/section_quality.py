from __future__ import annotations

import math
import json
import re
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from typing import Any

from app.config import Settings, get_settings
from app.services.agents.executor import ExecutorAgent
from app.services.knowledge import KnowledgeWikiContextProvider
from app.services.llm.client import LLMClient, LLMRequest, TaskType
from app.services.llm.prompts import SECTION_QUALITY_REVIEW_SCHEMA, build_section_quality_prompts
from app.services.vectorstore.block_taxonomy import infer_target_taxonomy


INTERNAL_HEADING_PATTERNS = (
    re.compile(r"^(建议插入图表|建议图表|建议参考资产|推荐资产|可用参考资料|可用复用包|替换与禁用约束|参考摘要)$", re.IGNORECASE),
    re.compile(r"^(图表建议|插图建议|图表清单)$", re.IGNORECASE),
)
LATIN_ENUM_HEADING_PATTERN = re.compile(r"^[A-Z]\.\s*.+$")
GENERIC_HEADING_PATTERN = re.compile(r"^(概述|说明|补充说明|其他|附加说明)$")
MARKDOWN_HEADING_PATTERN = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
ASSET_PLACEHOLDER_PATTERN = re.compile(r"\[\[ASSET:[A-Z]+:[^\]]+\]\]")
SECTION_QUALITY_MIN_TOKENS = 1400
SECTION_QUALITY_MAX_TOKENS = 2600
SECTION_QUALITY_BASE_TOKENS = 1100
SECTION_QUALITY_TOKEN_RATIO = 0.3
SECTION_QUALITY_ASSET_TOKEN_BONUS = 80
FLUFF_PATTERNS = (
    re.compile(r"综上所述"),
    re.compile(r"总之"),
    re.compile(r"具有(?:较强|良好|显著)?(?:先进性|可靠性|经济性|前瞻性)"),
    re.compile(r"处于(?:国内|行业)?领先水平"),
    re.compile(r"经过(?:我们|本团队|研发团队)?(?:深入研究|反复验证)"),
)
INTERNAL_VOICE_PATTERNS = (
    re.compile(r"我们"),
    re.compile(r"本团队"),
    re.compile(r"研发团队"),
    re.compile(r"内部测试"),
)
INTERNAL_PROMPT_RESIDUE_PATTERNS = (
    re.compile(r"建议插入图表"),
    re.compile(r"建议参考资产"),
    re.compile(r"推荐资产"),
    re.compile(r"可用参考资料"),
    re.compile(r"可用复用包"),
    re.compile(r"替换与禁用约束"),
    re.compile(r"章节关键词[:：]"),
    re.compile(r"目标章节标题[:：]"),
    re.compile(r"参考摘要[:：]"),
    re.compile(r"<(?:section_request|authoring_context|quality_review_request|review_contract|recommended_assets|reuse_pack|assembled_draft|section_markdown)>", re.IGNORECASE),
)
TECHNICAL_SECTION_HINTS = ("技术", "架构", "配置", "参数", "实施", "系统", "方案", "控制", "接口", "主回路")
TECHNICAL_SECTION_CLASSES = {"architecture", "configuration", "implementation", "custom"}
LEGACY_PROCESS_SCENARIO_TOKENS = ("高浓磨机", "磨机", "高炉鼓风机", "鼓风机", "压缩机", "油站", "冷却器")
LEGACY_LCI_SEQUENCE_TOKENS = ("lci", "sfc", "变频软起", "软起动", "软启动", "纯加速", "换相", "同步切换", "工频切换", "励磁柜", "总启动时间")
LEGACY_LCI_ALLOWED_SECTION_TOKENS = ("lci", "sfc", "变频软起", "软起动", "软启动", "同步切换", "工频切换", "启动时间", "启动过程", "同步电机")
TECHNICAL_SCHEME_SECTION_TYPES = {
    "overall_solution",
    "main_circuit_scheme",
    "vfd_spec",
    "starter_spec",
    "motor_spec",
    "transformer_spec",
    "control_logic",
    "communication_interface",
    "protection_interlock",
}
TECHNICAL_SCOPE_DRIFT_HEADING_TOKENS = (
    "培训",
    "售后",
    "维保",
    "质保",
    "巡检",
    "备品备件",
    "配件及工具",
    "建设、经营",
    "建设经营",
    "经营方案",
    "运营模式",
    "项目建设",
    "项目实施",
    "实施进度",
    "进度规划",
    "进度计划",
    "进度安排",
    "施工进度",
    "节能效益分享",
    "收益回收",
    "所有权",
    "合同期满",
)
METADATA_ONLY_ISSUE_CODES = {
    "INTERNAL_METADATA_PRESENT",
    "INTERNAL_HINT",
    "INTERNAL_ASSET_RESIDUE",
    "INTERNAL_HINT_REMAIN",
    "INTERNAL_ASSET_REFERENCE",
    "INTERNAL_ASSET_META",
}
ADVISORY_ISSUE_CODES = {
    *METADATA_ONLY_ISSUE_CODES,
    "TITLE_SCOPE_SOFT",
    "CONTENT_REDUNDANCY",
    "WORDING_GENERIC",
    "NAMING_CONSISTENCY",
    "TITLE_STYLE_OVERSEGMENTED",
    "TITLE_STYLE_INCONSISTENT",
    "TITLE_STYLE_GENERIC",
    "TITLE_QUALITY",
    "TITLE_SCOPE_WEAK",
    "TITLE_COVERAGE_WEAK",
    "STRUCTURE_REDUNDANCY",
    "STRUCTURE_OPTIMIZATION",
    "CUSTOMER_TONE",
    "CUSTOMER_TONE_WEAK",
    "CUSTOMER_TONE_SOFT",
    "CUSTOMER_TONE_RISK",
    "CUSTOMER_TONE_PARTIAL_INTERNAL",
    "CUSTOMER_TONE_SLIGHTLY_REPETITIVE",
    "CUSTOMER_TONE_COMMITMENT",
    "CUSTOMER_READINESS_RISK",
    "CUSTOMER_TONE_INSTABILITY",
    "CUSTOMER_CALIBER_RISK",
    "CLIENT_TONE",
    "CLIENT_TONE_GENERAL",
    "DETAIL_DENSITY_SLIGHTLY_THIN",
    "CONTENT_OVERLAP",
    "HEADING_STYLE_INCONSISTENT",
    "SERVICE_TONE_WEAK",
    "TITLE_STYLE_OPT",
    "TITLE_NAMING",
    "CONSISTENCY_ROLE",
    "CONTENT_FOCUS",
    "CUSTOMER_READABILITY",
    "TONE_INTERNAL",
    "TECH_BOUNDARY_CLARITY",
    "CUSTOMER_TONE_OPT",
    "CONTROL_SCOPE_PRECISION",
    "SECTION_CLOSURE_OPT",
    "SQ999",
}
HIGH_SCORE_SOFT_MEDIUM_ISSUE_CODES = {
    "TITLE_GENERIC",
    "SCOPE_BLEND",
    "TECH_RISK_WORDING",
    "TITLE_OVERLAP",
    "PARAMETER_COMMITMENT_RISK",
    "CONTROL_SCOPE_GENERAL",
    "TECHNICAL_CAUTION",
    "CLOSING_SECTION_WEAK",
    "WORDING_CUSTOMER_TONE",
    "PARAMETER_PREMATURE_COMMITMENT",
    "TITLE_STYLE_OPTIMIZATION",
    "CONTENT_PRECISION",
    "TITLE_SCOPE_MISMATCH",
    "GOAL_COVERAGE_WEAK",
    "CUSTOMER_TONE_REFINEMENT",
    "TERMINOLOGY_CONSISTENCY",
    "TITLE_SCOPE_REFINEMENT",
    "TECH_APPLICABILITY_AMBIGUOUS",
    "TECH_TERM_CAUTION",
    "CONSISTENCY_DETAIL_LEVEL",
    "CLIENT_TONE_OPTIMIZATION",
    "TITLE_HIERARCHY_INCONSISTENT",
    "TERM_INCONSISTENT",
    "TECH_SCOPE_ALIGNMENT",
    "CUSTOMER_CLARITY",
    "PARAMETER_SOURCE_RISK",
    "TERM_CONSISTENCY",
    "COMMITMENT_RISK",
    "TECH_PRECISION",
    "STYLE_REDUNDANCY",
    "STRUCTURE_OVERLAP",
    "TITLE_TIGHTENING",
    "CONTENT_ABSTRACT",
    "CONTROL_SCOPE_CLARITY",
    "CLIENT_TONE_REFINEMENT",
    "STRUCTURE_TIGHTENING",
    "TONE_CONTRACT_HEAVY",
    "TRAINING_WORDING_RISK",
    "DEBUG_TEST_GRANULARITY",
}


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
        knowledge_wiki: KnowledgeWikiContextProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.llm_client = llm_client or LLMClient()
        self.executor = executor or ExecutorAgent(llm_client=self.llm_client)
        self.knowledge_wiki = knowledge_wiki

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
        knowledge_review_context = (
            self.knowledge_wiki.build_quality_review_context(section=section, global_params=global_params)
            if self.knowledge_wiki is not None
            else ""
        )
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
                        knowledge_review_context=knowledge_review_context,
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
            "knowledge_review_context_chars": len(knowledge_review_context),
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
        knowledge_review_bundle = (
            self.knowledge_wiki.collect_quality_review_bundle(section=section, global_params=global_params)
            if self.knowledge_wiki is not None
            else {}
        )
        knowledge_review_context = (
            self.knowledge_wiki.build_quality_review_context(section=section, global_params=global_params)
            if self.knowledge_wiki is not None
            else ""
        )
        section_title = str(section.get("title") or "")
        rule_issues = [
            *analyze_section_heading_quality(section_title=section_title, content_md=content_md),
            *analyze_section_content_quality(
                section=section,
                content_md=content_md,
                global_params=global_params,
                knowledge_review_bundle=knowledge_review_bundle,
            ),
        ]
        llm_review = await self._review_with_llm(
            task_id=task_id,
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            content_md=content_md,
            recommended_assets=recommended_assets or [],
            knowledge_review_context=knowledge_review_context,
        )
        merged_issues = _normalize_quality_issues(
            _merge_issues(rule_issues, llm_review.issues),
            content_md=content_md,
        )
        blocking_issue_count = sum(1 for issue in merged_issues if _is_blocking_issue(issue))
        score = min(llm_review.score, _score_from_rule_issues(rule_issues))
        substantive_medium_issue_count = sum(
            1
            for issue in merged_issues
            if issue.severity == "medium" and not _is_effectively_advisory_issue(issue, score=score)
        )
        passed = blocking_issue_count == 0 and score >= 0.78
        if substantive_medium_issue_count >= 3:
            passed = False
        elif substantive_medium_issue_count >= 2 and score < 0.84:
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
        knowledge_review_context: str = "",
    ) -> SectionQualityReview:
        system_prompt, user_prompt = build_section_quality_prompts(
            section=section,
            outline_title=outline_title,
            global_params=global_params,
            content_md=content_md,
            recommended_assets=recommended_assets,
            quality_constraints=knowledge_review_context,
        )
        try:
            max_tokens = _estimate_section_quality_max_tokens(
                content_md=content_md,
                recommended_assets=recommended_assets,
            )
            response = await self.llm_client.invoke(
                LLMRequest(
                    task_type=TaskType.SECTION_QUALITY,
                    session_id=f"{task_id}-quality",
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.1,
                    max_tokens=max_tokens,
                    json_schema=SECTION_QUALITY_REVIEW_SCHEMA,
                    metadata={"section": section, "quality_max_tokens": max_tokens},
                )
            )
            payload = json.loads(response.content)
        except Exception as exc:  # noqa: BLE001
            fallback_issue = SectionQualityIssue(
                code="SQ999",
                severity="low",
                target="章节整体",
                message=f"章节自动质检失败，已降级为规则审查：{exc}",
                suggested_fix="请人工检查小标题风格、内部提示语和章节完整度。",
                source="llm_fallback",
            )
            return SectionQualityReview(
                passed=True,
                score=0.88,
                summary="章节自动质检调用失败，已回退到规则审查。",
                issues=[fallback_issue],
                rewrite_instruction="请统一小标题风格、删除内部提示语，并保持技术内容密度与图表占位符不变。",
                source="rule_fallback",
            )

        llm_issues = _coerce_llm_quality_issues(payload.get("issues"))

        return SectionQualityReview(
            passed=bool(payload.get("pass")),
            score=_clamp_score(payload.get("score")),
            summary=str(payload.get("summary") or "").strip(),
            issues=llm_issues,
            rewrite_instruction=str(payload.get("rewrite_instruction") or "").strip(),
            source="llm",
        )


def _coerce_llm_quality_issues(raw_issues: Any) -> list[SectionQualityIssue]:
    if raw_issues is None:
        return []
    if isinstance(raw_issues, list):
        issue_items = raw_issues
    else:
        issue_items = [raw_issues]

    issues: list[SectionQualityIssue] = []
    for item in issue_items:
        if isinstance(item, dict):
            issues.append(
                SectionQualityIssue(
                    code=str(item.get("code") or "SQLLM"),
                    severity=_normalize_severity(item.get("severity")),
                    target=str(item.get("target") or "章节整体").strip() or "章节整体",
                    message=str(item.get("message") or "").strip() or "章节质量需人工确认。",
                    suggested_fix=str(item.get("suggested_fix") or "").strip() or "请按章节目标重写并统一标题风格。",
                    source="llm",
                )
            )
            continue

        message = str(item or "").strip()
        if not message:
            continue
        issues.append(
            SectionQualityIssue(
                code="SQLLM",
                severity="low",
                target="章节整体",
                message=message,
                suggested_fix="请人工复核该模型质检建议，并按章节目标修订。",
                source="llm",
            )
        )
    return issues


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


def analyze_section_content_quality(
    *,
    section: dict[str, Any],
    content_md: str,
    global_params: dict[str, Any] | None = None,
    knowledge_review_bundle: dict[str, Any] | None = None,
) -> list[SectionQualityIssue]:
    issues: list[SectionQualityIssue] = []
    paragraphs = _extract_quality_paragraphs(content_md)
    body_text = "\n\n".join(paragraphs).strip()
    if not body_text:
        return issues

    for pattern in FLUFF_PATTERNS:
        match = pattern.search(body_text)
        if not match:
            continue
        snippet = _excerpt_around_match(body_text, match.start(), match.end())
        issues.append(
            SectionQualityIssue(
                code="SQ010",
                severity="medium",
                target=snippet or "章节整体",
                message="正文存在空泛套话，技术信息密度不足。",
                suggested_fix="删除空泛总结，改为具体技术结论、参数或实现方式。",
            )
        )
        break

    for pattern in INTERNAL_VOICE_PATTERNS:
        match = pattern.search(body_text)
        if not match:
            continue
        snippet = _excerpt_around_match(body_text, match.start(), match.end())
        issues.append(
            SectionQualityIssue(
                code="SQ011",
                severity="high",
                target=snippet or "章节整体",
                message="正文出现内部口径，不适合直接对客户输出。",
                suggested_fix="将“我们 / 本团队 / 内部测试”等表达改为客户视角的正式技术表述。",
            )
        )
        break

    scenario_issue = _detect_legacy_scenario_drift(section=section, body_text=body_text, global_params=global_params or {})
    if scenario_issue:
        issues.append(scenario_issue)
    scope_drift_issue = _detect_technical_scope_drift(section=section, content_md=content_md)
    if scope_drift_issue:
        issues.append(scope_drift_issue)

    issues.extend(_detect_repeated_paragraph_issues(paragraphs))
    issues.extend(_detect_knowledge_wiki_quality_issues(body_text=body_text, knowledge_review_bundle=knowledge_review_bundle or {}))

    if _is_technical_section(section=section) and _visible_text_length(body_text) < 300:
        issues.append(
            SectionQualityIssue(
                code="SQ013",
                severity="medium",
                target="章节整体",
                message="技术类章节正文偏短，可能尚未充分覆盖核心技术细节。",
                suggested_fix="补充系统构成、控制逻辑、接口边界、参数或实施约束等具体技术内容。",
            )
        )

    return _dedupe_issue_list(issues)


def _detect_knowledge_wiki_quality_issues(
    *,
    body_text: str,
    knowledge_review_bundle: dict[str, Any],
) -> list[SectionQualityIssue]:
    if not knowledge_review_bundle:
        return []
    issues: list[SectionQualityIssue] = []
    issues.extend(
        _detect_forbidden_phrase_issues(
            body_text=body_text,
            forbidden_phrases=list(knowledge_review_bundle.get("forbidden_phrases") or []),
        )
    )
    issues.extend(
        _detect_glossary_consistency_issues(
            body_text=body_text,
            glossary_entries=list(knowledge_review_bundle.get("glossary_entries") or []),
        )
    )
    return _dedupe_issue_list(issues)


def _detect_forbidden_phrase_issues(
    *,
    body_text: str,
    forbidden_phrases: list[dict[str, Any]],
) -> list[SectionQualityIssue]:
    issues: list[SectionQualityIssue] = []
    haystack = str(body_text or "")
    haystack_lower = haystack.casefold()
    for item in forbidden_phrases[:4]:
        phrase = str(item.get("phrase") or "").strip()
        preferred = str(item.get("preferred") or "").strip()
        if not phrase:
            continue
        index = haystack_lower.find(phrase.casefold())
        if index < 0:
            continue
        severity = "high" if phrase in {"我公司"} else "medium"
        issues.append(
            SectionQualityIssue(
                code="KW001",
                severity=severity,
                target=phrase,
                message=f"正文出现 AI Wiki 禁用表述“{phrase}”，客户口径不稳。",
                suggested_fix=f"将“{phrase}”改为“{preferred}”。" if preferred else f"删除“{phrase}”这类表述。",
            )
        )
    return issues


def _detect_glossary_consistency_issues(
    *,
    body_text: str,
    glossary_entries: list[dict[str, Any]],
) -> list[SectionQualityIssue]:
    issues: list[SectionQualityIssue] = []
    normalized_text = str(body_text or "").casefold()
    for entry in glossary_entries[:4]:
        primary_term = str(entry.get("display_primary_term") or entry.get("primary_term") or "").strip()
        aliases = [
            str(alias).strip()
            for alias in (entry.get("display_aliases") or entry.get("aliases") or [])
            if str(alias).strip()
        ]
        if not primary_term or not aliases:
            continue
        counts: dict[str, int] = {}
        for form in [primary_term, *aliases]:
            count = _count_term_occurrences(normalized_text, form)
            if count > 0:
                counts[form] = count
        if len(counts) < 2:
            continue
        alias_hits = sum(count for form, count in counts.items() if form != primary_term)
        if alias_hits < 1 or sum(counts.values()) < 3:
            continue
        mixed_forms = " / ".join(counts.keys())
        issues.append(
            SectionQualityIssue(
                code="KW010",
                severity="medium",
                target=primary_term,
                message=f"同一对象同时使用“{mixed_forms}”等称呼，术语不统一。",
                suggested_fix=f"统一使用“{primary_term}”作为主称谓，避免与“{' / '.join(aliases[:3])}”混用。",
            )
        )
    return issues


def _count_term_occurrences(normalized_text: str, term: str) -> int:
    normalized_term = str(term or "").strip().casefold()
    if not normalized_term:
        return 0
    if re.fullmatch(r"[a-z0-9_./+-]+", normalized_term):
        pattern = re.compile(rf"(?<![a-z0-9]){re.escape(normalized_term)}(?![a-z0-9])")
        return len(pattern.findall(normalized_text))
    return normalized_text.count(normalized_term)


def _estimate_section_quality_max_tokens(*, content_md: str, recommended_assets: list[dict[str, Any]] | None = None) -> int:
    text = str(content_md or "")
    char_count = len(text)
    cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
    ascii_chars = max(0, char_count - cjk_chars)
    estimated_input_tokens = math.ceil(cjk_chars / 2 + ascii_chars / 4)
    asset_bonus = min(len(recommended_assets or []), 5) * SECTION_QUALITY_ASSET_TOKEN_BONUS
    budget = math.ceil(SECTION_QUALITY_BASE_TOKENS + estimated_input_tokens * SECTION_QUALITY_TOKEN_RATIO + asset_bonus)
    return max(SECTION_QUALITY_MIN_TOKENS, min(budget, SECTION_QUALITY_MAX_TOKENS))


def _detect_legacy_scenario_drift(
    *,
    section: dict[str, Any],
    body_text: str,
    global_params: dict[str, Any],
) -> SectionQualityIssue | None:
    section_signal = _section_quality_signal_text(section)
    context_signal = f"{section_signal} {_global_quality_signal_text(global_params)}"
    process_hits = _matched_tokens(body_text, LEGACY_PROCESS_SCENARIO_TOKENS)
    section_allows_lci_auxiliary = _contains_any_quality_token(context_signal, LEGACY_LCI_ALLOWED_SECTION_TOKENS)
    if (
        process_hits
        and not section_allows_lci_auxiliary
        and not _contains_any_quality_token(context_signal, LEGACY_PROCESS_SCENARIO_TOKENS)
    ):
        return SectionQualityIssue(
            code="SQ014",
            severity="high",
            target="、".join(process_hits[:4]),
            message="正文混入历史方案的具体工艺对象或辅机边界，存在场景漂移风险。",
            suggested_fix="删除与当前项目无关的历史工艺对象、辅机系统和专有启动边界，只保留可迁移的系统架构或控制原则。",
        )

    lci_hits = _matched_tokens(body_text, LEGACY_LCI_SEQUENCE_TOKENS)
    if len(lci_hits) >= 2 and not _contains_any_quality_token(context_signal, LEGACY_LCI_ALLOWED_SECTION_TOKENS):
        return SectionQualityIssue(
            code="SQ014",
            severity="high",
            target="、".join(lci_hits[:4]),
            message="正文混入 LCI 软起/同步切换等历史方案专用过程，超出当前章节边界。",
            suggested_fix="如果当前项目未明确采用 LCI 软起或同步切换，不得复用历史启动时序、励磁等待、换相模式和工频切换参数。",
        )
    return None


def _detect_technical_scope_drift(*, section: dict[str, Any], content_md: str) -> SectionQualityIssue | None:
    section_type = str(infer_target_taxonomy(section).get("section_type") or "unknown").lower()
    if section_type not in TECHNICAL_SCHEME_SECTION_TYPES:
        return None
    for level, heading in _extract_markdown_headings(content_md):
        if level <= 2:
            continue
        if not _contains_any_quality_token(heading, TECHNICAL_SCOPE_DRIFT_HEADING_TOKENS):
            continue
        return SectionQualityIssue(
            code="SQ015",
            severity="high",
            target=heading,
            message=f"技术章节出现离题小标题“{heading}”，疑似混入培训、服务、经营或实施计划内容。",
            suggested_fix="删除该离题小节，改为围绕本章节目标补充主回路、系统架构、设备构成、控制边界或参数证据。",
        )
    return None


def _section_quality_signal_text(section: dict[str, Any]) -> str:
    parts = [
        str(section.get("title") or "").strip(),
        str(section.get("purpose") or section.get("description") or "").strip(),
        " ".join(str(item) for item in (section.get("keywords") or []) if item),
    ]
    return " ".join(part for part in parts if part)


def _global_quality_signal_text(global_params: dict[str, Any]) -> str:
    parts = [
        str(global_params.get("project_name") or "").strip(),
        str(global_params.get("industry") or "").strip(),
        str(global_params.get("product_line") or "").strip(),
        str(global_params.get("business_objective") or "").strip(),
    ]
    return " ".join(part for part in parts if part)


def _contains_any_quality_token(text: str, tokens: tuple[str, ...]) -> bool:
    return bool(_matched_tokens(text, tokens))


def _matched_tokens(text: str, tokens: tuple[str, ...]) -> list[str]:
    haystack = str(text or "").casefold()
    compact_haystack = re.sub(r"\s+", "", haystack)
    hits: list[str] = []
    for token in tokens:
        normalized = token.casefold()
        if normalized in haystack or normalized.replace(" ", "") in compact_haystack:
            hits.append(token)
    return hits


def _extract_markdown_headings(content_md: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    for line in str(content_md or "").splitlines():
        match = MARKDOWN_HEADING_PATTERN.match(line.strip())
        if not match:
            continue
        headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def _extract_quality_paragraphs(content_md: str) -> list[str]:
    cleaned_lines: list[str] = []
    for raw_line in str(content_md or "").splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append("")
            continue
        if MARKDOWN_HEADING_PATTERN.match(line):
            continue
        if ASSET_PLACEHOLDER_PATTERN.search(line):
            continue
        if line.startswith("- [[ASSET:"):
            continue
        cleaned_lines.append(raw_line.rstrip())
    text = "\n".join(cleaned_lines).strip()
    if not text:
        return []
    return [segment.strip() for segment in re.split(r"\n\s*\n", text) if segment.strip()]


def _excerpt_around_match(text: str, start: int, end: int, *, radius: int = 18) -> str:
    prefix = max(0, start - radius)
    suffix = min(len(text), end + radius)
    excerpt = text[prefix:suffix].strip()
    excerpt = re.sub(r"\s+", " ", excerpt)
    return excerpt[:80]


def _detect_repeated_paragraph_issues(paragraphs: list[str]) -> list[SectionQualityIssue]:
    issues: list[SectionQualityIssue] = []
    normalized = [_normalize_similarity_text(item) for item in paragraphs]
    for left_index, left in enumerate(normalized):
        if len(left) < 40:
            continue
        for right_index in range(left_index + 1, len(normalized)):
            right = normalized[right_index]
            if len(right) < 40:
                continue
            score = SequenceMatcher(None, left, right).ratio()
            if score < 0.78:
                continue
            issues.append(
                SectionQualityIssue(
                    code="SQ012",
                    severity="medium",
                    target=f"段落{left_index + 1}/段落{right_index + 1}",
                    message="正文存在高相似重复段，章节结构不够紧凑。",
                    suggested_fix="合并重复段落，只保留一次完整表述，避免在同章重复说明同一观点。",
                )
            )
            if len(issues) >= 2:
                return issues
    return issues


def _normalize_similarity_text(text: str) -> str:
    normalized = str(text or "")
    normalized = ASSET_PLACEHOLDER_PATTERN.sub(" ", normalized)
    normalized = re.sub(r"[#>*`_\-\|\[\]\(\)]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().lower()


def _is_technical_section(*, section: dict[str, Any]) -> bool:
    section_class = str(section.get("section_class") or "").strip().lower()
    if section_class in TECHNICAL_SECTION_CLASSES:
        return True
    title = str(section.get("title") or "")
    purpose = str(section.get("purpose") or section.get("description") or "")
    haystack = f"{title} {purpose}"
    return any(token in haystack for token in TECHNICAL_SECTION_HINTS)


def _visible_text_length(text: str) -> int:
    normalized = re.sub(r"\s+", "", str(text or ""))
    normalized = re.sub(r"[`*_>#\-|]", "", normalized)
    return len(normalized)


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


def _normalize_quality_issues(
    issues: list[SectionQualityIssue],
    *,
    content_md: str,
) -> list[SectionQualityIssue]:
    explicit_internal_residue = _contains_explicit_internal_prompt_residue(content_md)
    normalized: list[SectionQualityIssue] = []
    for issue in issues:
        severity = issue.severity
        if _is_minor_issue_code(issue.code):
            severity = "low"
        elif issue.code in METADATA_ONLY_ISSUE_CODES and not explicit_internal_residue:
            severity = "low"
        elif issue.code in ADVISORY_ISSUE_CODES and severity == "high":
            severity = "medium"
        normalized.append(replace(issue, severity=severity))
    return _dedupe_issue_list(normalized)


def _contains_explicit_internal_prompt_residue(content_md: str) -> bool:
    text = str(content_md or "")
    return any(pattern.search(text) for pattern in INTERNAL_PROMPT_RESIDUE_PATTERNS)


def _is_advisory_issue(issue: SectionQualityIssue) -> bool:
    return issue.code in ADVISORY_ISSUE_CODES or _is_minor_issue_code(issue.code) or issue.severity == "low"


def _is_effectively_advisory_issue(issue: SectionQualityIssue, *, score: float) -> bool:
    if _is_advisory_issue(issue):
        return True
    return score >= 0.93 and issue.severity == "medium" and issue.code in HIGH_SCORE_SOFT_MEDIUM_ISSUE_CODES


def _is_minor_issue_code(code: str) -> bool:
    return str(code or "").strip().upper().endswith("_MINOR")


def _is_blocking_issue(issue: SectionQualityIssue) -> bool:
    if issue.code in {"SQ001", "SQ002", "SQ011"}:
        return True
    if issue.code == "SQ999":
        return False
    return issue.severity == "high" and not _is_advisory_issue(issue)


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
    knowledge_review_context: str = "",
) -> str:
    key_params = ", ".join(f"{key}={value}" for key, value in global_params.items() if value not in (None, "", [], {})) or "无"
    lines = [
        f"章节标题: {section.get('title') or ''}",
        f"章节目的: {section.get('purpose') or section.get('description') or ''}",
        f"当前关键参数: {key_params}",
        f"当前质量摘要: {initial_review.summary}",
        "当前问题:",
        *[f"- {issue.message}" for issue in initial_review.issues[:6]],
    ]
    if knowledge_review_context:
        lines.extend(["AI Wiki 约束:", knowledge_review_context])
    return "\n".join(lines)


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
