import unittest

from app.services.parsing.formula_candidates import (
    extract_formula_candidates,
    has_garbled_formula_text,
    is_formula_like_text,
    to_latex_hint,
)


class FormulaCandidateTests(unittest.TestCase):
    def test_to_latex_hint_converts_unicode_super_subscript(self) -> None:
        self.assertEqual(to_latex_hint("TH₁ = V²"), "TH_{1} = V^{2}")

    def test_extract_formula_candidates_returns_latex_hints(self) -> None:
        markdown = "# 标题\n\nTH₁ = V²\n\nI/O 容量(数字输入)\n"
        candidates = extract_formula_candidates(markdown)
        self.assertEqual(len(candidates), 2)
        self.assertEqual(candidates[0].latex_hint, "TH_{1} = V^{2}")
        self.assertFalse(candidates[0].garbled)

    def test_extract_formula_candidates_marks_garbled_lines(self) -> None:
        candidates = extract_formula_candidates("4Ă TH₴₩÷\n")
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0].garbled)

    def test_formula_text_helpers(self) -> None:
        self.assertTrue(is_formula_like_text("TH₁ = V²"))
        self.assertTrue(has_garbled_formula_text("4Ă TH₴₩÷"))
        self.assertFalse(is_formula_like_text("纯中文标题"))


if __name__ == "__main__":
    unittest.main()
