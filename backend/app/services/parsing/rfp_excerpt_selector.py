"""Deterministic requirement-aware excerpt selector for RFP light parses.

**Review R4 #3 background.**  R4 #1 fixed the storage layer to keep the full
extracted RFP text, but ``RequirementService._load_rfp_light_context``
still consumed only the first ``RFP_LIGHT_PARSE_EXCERPT_CHARS`` characters
(via ``Document.meta.rfp_text_excerpt`` written as ``text[:excerpt_chars]``,
or the same head slice when falling back to the storage path).  Any
requirement that lived past the head slice — typically 通用 RFPs have the
评分条款, 工期, 验收 etc. in the back half — never reached the LLM prompt
that produces the requirement card.

This module replaces the mechanical head slice with a deterministic
section-and-keyword aware selector.  It is intentionally rule-based (no
LLM call) so it adds zero latency to the requirement-card flow and is
safe to run synchronously inside the existing async pipeline.  The LLM
``rfp_knowledge_extract`` step that does deeper extraction stays as F3.

Algorithm:

1. Split the text into paragraphs (double-newline; single-newline as
   fallback for files with no blank lines).
2. Score each paragraph by adding:
   - **+10** for every heading keyword hit
     (项目概况 / 供货范围 / 技术参数 / 设备清单 / 接口 / 工期 / 验收 / 服务 /
     评分 / 投标人资格 …).
   - **+1** per requirement marker hit, capped at 5
     (必须 / 应当 / 应 / 不得 / 不应 / 投标人 / 供方 / 技术要求 / 强制 / 关键 /
     ≥ / ≤ / 不少于 / 不低于 / 不大于 / 不高于 …).
   - **+1** if the paragraph contains digits next to a unit
     (e.g. ``≥220V``, ``≤2.5kW``) — strong signal of a quantitative requirement.
   - **+0.5** for short-to-medium paragraphs (40 ≤ len ≤ 800).
3. Keep paragraphs with non-zero score in descending score order until the
   accumulated character count exceeds ``max_chars``.  Always include at
   least the top one paragraph even if it overshoots ``max_chars``.
4. Re-order the kept paragraphs by their original position and join them
   with ``\n\n[...]\n\n`` so the LLM knows the cut points.
5. If nothing scored above zero (e.g. plain prose without RFP shape), fall
   back to ``text[:max_chars]`` so we always return *something* usable.

The function is pure and side-effect free.  Returned excerpt length is
``len(excerpt) <= max_chars + small_overhead`` (the overhead comes from
the explicit ``[...]`` separators).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

# ---------------------------------------------------------------------------
# Keyword corpora
# ---------------------------------------------------------------------------

# Section-heading keywords drawn directly from Review R4's checklist of RFP
# sections that commonly carry hard requirements.  Each one is matched
# case-insensitively as a substring in the paragraph.  Hits add +10.
HEADING_KEYWORDS: tuple[str, ...] = (
    # 项目背景 / 概况
    "项目概况",
    "项目背景",
    "项目简介",
    "项目说明",
    "招标范围",
    # 供货 / 采购 / 建设范围
    "供货范围",
    "采购范围",
    "建设范围",
    "工程范围",
    # 技术要求 / 参数 / 规格
    "技术参数",
    "技术规格",
    "技术要求",
    "技术指标",
    "技术规范",
    "性能要求",
    # 设备 / 物料清单
    "设备清单",
    "供货清单",
    "材料清单",
    "物料清单",
    # 接口 / 边界
    "接口边界",
    "外部接口",
    "系统接口",
    # 工期 / 交付 / 进度
    "工期要求",
    "交货期",
    "交付要求",
    "交付时间",
    "进度计划",
    # 验收 / 质量
    "验收标准",
    "质量验收",
    "验收要求",
    "质量要求",
    # 服务 / 售后 / 运维
    "售后服务",
    "运维要求",
    "服务要求",
    "保修要求",
    "技术服务",
    # 评分 / 评标
    "评分标准",
    "评分细则",
    "评标办法",
    "评审标准",
    # 资格 / 投标人
    "投标人资格",
    "资格要求",
    "资质要求",
)

# Requirement markers that signal a hard / strong requirement statement.
# Capped contribution: at most 5 hits count per paragraph so that one
# paragraph cannot dominate purely by repeating "应".
REQUIREMENT_MARKERS: tuple[str, ...] = (
    "必须",
    "应当",
    "应该",
    "不得",
    "不应",
    "禁止",
    "强制",
    "投标人",
    "供方",
    "卖方",
    "甲方",
    "乙方",
    "关键",
    "核心要求",
    "强制要求",
    "≥",
    "≤",
    "不少于",
    "不低于",
    "不大于",
    "不高于",
    "不超过",
    "不小于",
)

# Pattern that catches paragraphs with a numerical specification: digit
# followed by a unit-ish token (Chinese or ASCII).  Hits add +1.
NUMERIC_SPEC_PATTERN = re.compile(
    r"\d+(?:\.\d+)?\s*"
    r"(?:[VAWkKMmGgHhz]|kW|MW|Hz|kHz|MHz|GHz|kg|km|cm|mm|m|s|ms|μs|ns|"
    r"°C|℃|%|个|台|套|件|页|条|项|分钟|小时|天|月|年|"
    r"V|A|Ω|Pa|kPa|MPa)",
    flags=re.IGNORECASE,
)

# A heading also looks like one of "N. xxx", "N.M xxx", "一、xxx", "（一）xxx" etc.
# We treat such opener as +2 (in addition to the keyword-based +10) so prose
# without a checklist hit but with a numeric heading still gets weight.
HEADING_OPENER_PATTERN = re.compile(
    r"^\s*(?:"
    r"\d+(?:\.\d+){0,3}\s*[、.．]"  # 1. / 1.1 / 1.2.3.
    r"|[一二三四五六七八九十百零]+[、.．]"  # 一、 二、
    r"|（[一二三四五六七八九十百零\d]+）"  # （一） （1）
    r"|\([一二三四五六七八九十百零\d]+\)"  # (一) (1)
    r"|第[一二三四五六七八九十百零\d]+[章节条款部分]"  # 第三章 第五条
    r")",
)


# ---------------------------------------------------------------------------
# Paragraph splitter
# ---------------------------------------------------------------------------


def _split_paragraphs(text: str) -> list[str]:
    """Split text into non-empty paragraphs.

    Prefers double-newline boundaries; falls back to single-newline if the
    document has no blank lines (common for plain ``.txt`` exports).  Each
    paragraph is stripped of leading/trailing whitespace.
    """

    if not text:
        return []
    chunks = re.split(r"\n\s*\n+", text)
    if len(chunks) == 1:
        # No blank-line breaks; fall back to single newline.
        chunks = text.split("\n")
    paragraphs: list[str] = []
    for chunk in chunks:
        stripped = chunk.strip()
        if stripped:
            paragraphs.append(stripped)
    return paragraphs


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ScoredParagraph:
    index: int
    text: str
    score: float


def _score_paragraph(paragraph: str) -> float:
    """Return a non-negative requirement-relevance score for ``paragraph``.

    Note: a length bonus is added *only* when at least one positive signal
    (heading keyword / requirement marker / numeric spec / heading opener)
    has already fired, so that pure prose filler can never reach a non-zero
    score on length alone.  Otherwise a 200-char prose paragraph would
    falsely be treated as relevant.
    """

    if not paragraph:
        return 0.0
    score = 0.0

    # Section-heading keywords (+10 each).  Use ``in`` rather than regex so
    # we are robust to whitespace / punctuation between keyword characters
    # only at the cost of false positives, which is fine for scoring.
    for keyword in HEADING_KEYWORDS:
        if keyword in paragraph:
            score += 10.0

    # Requirement markers (+1 each, capped at 5 per paragraph).
    marker_hits = 0
    for marker in REQUIREMENT_MARKERS:
        if marker in paragraph:
            marker_hits += 1
            if marker_hits >= 5:
                break
    score += float(marker_hits)

    # Numeric specs (+1 if any match).
    if NUMERIC_SPEC_PATTERN.search(paragraph):
        score += 1.0

    # Numeric / Chinese-numeral heading openers (+2).
    if HEADING_OPENER_PATTERN.match(paragraph):
        score += 2.0

    # Mild length bonus, but only when something else fired so plain prose
    # cannot reach a non-zero score on length alone.
    if score > 0:
        length = len(paragraph)
        if 40 <= length <= 800:
            score += 0.5

    return score


# ---------------------------------------------------------------------------
# Public selector
# ---------------------------------------------------------------------------


SEPARATOR = "\n\n[...]\n\n"


def select_requirement_excerpt(full_text: str, *, max_chars: int) -> str:
    """Return a requirement-aware excerpt of ``full_text`` capped at ``max_chars``.

    Contract:

    - If ``full_text`` is shorter than ``max_chars`` return it unchanged.
    - Otherwise score each paragraph (heading keywords / requirement markers
      / numeric specs / heading openers) and greedily pick the highest-scoring
      paragraphs in descending score order until adding the next one would
      exceed ``max_chars``.
    - The kept paragraphs are re-ordered to match their original positions,
      then joined with ``"\\n\\n[...]\\n\\n"`` so the LLM can see where
      content was elided.
    - If *no* paragraph scored above zero (e.g. unformatted prose without
      RFP keywords), fall back to ``full_text[:max_chars]`` so the caller
      always gets a usable string.

    Returned string is guaranteed non-empty when ``full_text`` is non-empty.
    """

    if max_chars <= 0:
        return ""
    if not full_text:
        return ""

    text = full_text
    if len(text) <= max_chars:
        return text

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return text[:max_chars]

    scored = [
        _ScoredParagraph(index=idx, text=para, score=_score_paragraph(para))
        for idx, para in enumerate(paragraphs)
    ]
    has_signal = any(item.score > 0 for item in scored)
    if not has_signal:
        # No RFP-shaped signal: keep the existing mechanical head-slice
        # behaviour so the caller is no worse off than before.
        return text[:max_chars]

    # Pick by descending score, breaking ties by ascending original position
    # so the front of the document is preferred when scores are equal.
    ranked = sorted(scored, key=lambda item: (-item.score, item.index))

    kept_indices: set[int] = set()
    accumulated_chars = 0
    sep_overhead = len(SEPARATOR)
    for item in ranked:
        if item.score <= 0 and kept_indices:
            break
        # Always include the top paragraph even if it overshoots; partial
        # truncation would change the meaning of a strong-requirement
        # paragraph and is worse than overshooting by a few hundred chars.
        if not kept_indices:
            kept_indices.add(item.index)
            accumulated_chars = len(item.text)
            continue
        addition = len(item.text) + sep_overhead
        if accumulated_chars + addition > max_chars:
            continue
        kept_indices.add(item.index)
        accumulated_chars += addition

    ordered = sorted(kept_indices)
    selected = [paragraphs[idx] for idx in ordered]
    excerpt = _join_with_gap_markers(paragraphs, ordered, selected)

    # Cap excerpt to a sane upper bound (max_chars + a small slack for the
    # separator markers).  We deliberately do NOT mid-paragraph truncate the
    # last kept paragraph: that would mangle a requirement sentence.  But we
    # do hard-cap to ``2 * max_chars`` so an extremely long single keep
    # paragraph cannot blow up the prompt budget.
    upper = max(max_chars, 2 * max_chars)
    if len(excerpt) > upper:
        excerpt = excerpt[:upper]
    return excerpt


def _join_with_gap_markers(
    paragraphs: list[str],
    ordered_indices: Iterable[int],
    selected_paragraphs: list[str],
) -> str:
    """Join ``selected_paragraphs`` with ``[...]`` markers between gaps.

    A gap marker is inserted between two kept paragraphs whose original
    indices are not adjacent (i.e. at least one paragraph was elided).
    """

    indices = list(ordered_indices)
    if not indices:
        return ""
    parts: list[str] = [selected_paragraphs[0]]
    for prev_pos, idx in enumerate(indices[1:], start=1):
        prev_idx = indices[prev_pos - 1]
        if idx - prev_idx > 1:
            parts.append(SEPARATOR.strip("\n"))
        parts.append(selected_paragraphs[prev_pos])
    return "\n\n".join(parts)


__all__ = [
    "select_requirement_excerpt",
    "HEADING_KEYWORDS",
    "REQUIREMENT_MARKERS",
]
