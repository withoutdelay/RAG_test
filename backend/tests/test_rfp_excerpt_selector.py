"""Unit tests for :func:`select_requirement_excerpt`.

Review R4 #3: deterministic, requirement-aware selection from the *full*
RFP body, replacing the mechanical head-slice that lost any requirement
past character ``RFP_LIGHT_PARSE_EXCERPT_CHARS``.

Each test pins a single contract:

- short documents pass through unchanged;
- the selector picks paragraphs by heading-keyword / requirement-marker
  signal rather than by position;
- the kept paragraphs are stitched in original order with explicit
  ``[...]`` gap markers so the LLM sees where content was elided;
- when no RFP signal is present at all the selector falls back to the
  legacy head slice rather than returning empty.
"""

from __future__ import annotations

from unittest import TestCase

from app.services.parsing.rfp_excerpt_selector import (
    HEADING_KEYWORDS,
    REQUIREMENT_MARKERS,
    select_requirement_excerpt,
)


class SelectRequirementExcerptTests(TestCase):
    def test_short_document_returns_unchanged(self) -> None:
        text = "项目概况：电机改造工程。"
        # Body fits well under the cap → return as-is.
        self.assertEqual(
            select_requirement_excerpt(text, max_chars=20_000),
            text,
        )

    def test_zero_max_chars_returns_empty(self) -> None:
        self.assertEqual(
            select_requirement_excerpt("项目概况：电机改造工程。", max_chars=0),
            "",
        )

    def test_empty_text_returns_empty(self) -> None:
        self.assertEqual(select_requirement_excerpt("", max_chars=1000), "")

    def test_keyword_paragraph_wins_over_filler(self) -> None:
        """A heading-keyword paragraph beats prose filler at the same length."""

        filler = "此处为大量背景描述。" * 200  # ~2000 chars, scores 0
        requirement = "技术要求：投标人提供的电机必须 ≥ 2.5MW。"
        full = "\n\n".join([filler, requirement])
        excerpt = select_requirement_excerpt(full, max_chars=200)
        # Even with a tiny budget, the requirement paragraph wins because
        # it scores >= 10 (heading keyword) + marker hits + numeric spec.
        self.assertIn("技术要求", excerpt)
        self.assertIn("≥ 2.5MW", excerpt)

    def test_paragraphs_are_kept_in_original_order(self) -> None:
        first = "供货范围：电机一台。"
        middle_filler = "公司沿革段落。" * 50
        last = "评分标准：技术分占 60%。"
        full = "\n\n".join([first, middle_filler, last])

        excerpt = select_requirement_excerpt(full, max_chars=2_000)
        # The two heading-keyword paragraphs must reach the excerpt and the
        # ``供货范围`` paragraph must come BEFORE the ``评分标准`` paragraph.
        first_idx = excerpt.find("供货范围")
        last_idx = excerpt.find("评分标准")
        self.assertGreaterEqual(first_idx, 0)
        self.assertGreaterEqual(last_idx, 0)
        self.assertLess(first_idx, last_idx)

    def test_gap_marker_is_inserted_between_non_adjacent_kept_paragraphs(self) -> None:
        first = "供货范围：电机一台。"
        # Make the gap large enough that the whole body exceeds ``max_chars``
        # so the selector actually has to choose between paragraphs (otherwise
        # short bodies pass through as-is).
        gap = "无关段落。" * 400  # ~2000 chars, scores 0
        last = "评分标准：技术分占 60%。"
        full = "\n\n".join([first, gap, last])
        # Sanity: body must overflow max_chars to exercise the selection path.
        self.assertGreater(len(full), 1500)

        excerpt = select_requirement_excerpt(full, max_chars=1_500)
        # Selector keeps ``first`` and ``last`` but drops the prose ``gap``
        # so a ``[...]`` marker must show the elision.
        self.assertIn("[...]", excerpt)
        self.assertIn("供货范围", excerpt)
        self.assertIn("评分标准", excerpt)
        self.assertNotIn("无关段落", excerpt)

    def test_no_rfp_signal_falls_back_to_head_slice(self) -> None:
        """No keyword / marker → never empty: fall back to head slice."""

        text = ("非 RFP 形态的纯散文段落。" * 2000)
        excerpt = select_requirement_excerpt(text, max_chars=500)
        # Length-bounded, non-empty, and matches the start of the body.
        self.assertEqual(len(excerpt), 500)
        self.assertEqual(excerpt, text[:500])

    def test_marker_only_paragraph_is_kept(self) -> None:
        """A paragraph with requirement markers but no heading still scores."""

        filler = "此处为大量背景描述。" * 200
        marker_para = "投标人必须提供完整的备件清单，且不得遗漏任何关键部件。"
        full = "\n\n".join([filler, marker_para])

        excerpt = select_requirement_excerpt(full, max_chars=300)
        self.assertIn("投标人必须", excerpt)

    def test_top_paragraph_is_always_kept_even_if_overshoots(self) -> None:
        """Selector overshoots rather than mid-paragraph cutting a requirement."""

        long_requirement = "技术要求：" + ("投标人必须满足以下条款。" * 60)
        # max_chars deliberately smaller than the single requirement paragraph
        excerpt = select_requirement_excerpt(long_requirement, max_chars=80)
        # We never mid-paragraph truncate a requirement sentence — keep the
        # whole paragraph (overshoot is bounded by ``2 * max_chars`` upper).
        self.assertIn("技术要求", excerpt)
        self.assertIn("投标人必须", excerpt)

    def test_keyword_corpus_covers_review_r4_checklist(self) -> None:
        """Static guard: the heading corpus contains every Review R4 #3 hint."""

        expected = (
            "项目概况",
            "供货范围",
            "技术参数",
            "技术要求",
            "设备清单",
            "工期",
            "验收",
            "评分",
        )
        for needle in expected:
            with self.subTest(keyword=needle):
                # We accept any HEADING_KEYWORDS entry that contains the
                # short hint as a substring (e.g. "工期要求" satisfies "工期").
                self.assertTrue(
                    any(needle in keyword for keyword in HEADING_KEYWORDS),
                    f"missing heading keyword family for {needle!r}",
                )

    def test_marker_corpus_covers_required_strong_markers(self) -> None:
        # We accept either an exact match or a substring family (e.g. "应"
        # is covered by "应当" / "应该" / "不应" rather than as a bare token,
        # avoiding false positives on common compound words like "对应").
        for marker in ("必须", "应", "不得", "投标人", "供方", "≥", "≤"):
            with self.subTest(marker=marker):
                self.assertTrue(
                    any(marker in entry for entry in REQUIREMENT_MARKERS),
                    f"missing marker family for {marker!r}",
                )

    def test_back_half_section_outscores_head_filler(self) -> None:
        """The whole point: a 50k-char body with the requirement at the end."""

        head = "此处为大量背景描述。" * 1500  # ~12k, scores 0
        middle = "公司沿革与历史采购数据。" * 2200  # ~26k, scores 0
        back_half = "技术要求：投标人提供的电机必须 ≥ 2.5MW，保护等级不低于 IP55。"
        full = "\n\n".join([head, middle, back_half])
        self.assertGreater(len(full), 30_000)

        excerpt = select_requirement_excerpt(full, max_chars=2_000)
        self.assertIn("技术要求", excerpt)
        self.assertIn("≥ 2.5MW", excerpt)
        self.assertIn("IP55", excerpt)
        # The prose-only middle filler must NOT crowd the budget.
        self.assertNotIn("公司沿革", excerpt)
