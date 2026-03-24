from __future__ import annotations

from dataclasses import dataclass
import re


MATH_LINE_PATTERN = re.compile(r"(∑|∫|√|≤|≥|≈|Ω|μ|α|β|γ|×|÷|=|\^|[_]|/\s*[A-Za-z])")
SUPERSCRIPT_SUBSCRIPT_PATTERN = re.compile(r"[⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾ⁿᵃᵇᶜᵈᵉᶠᵍʰᶦʲᵏˡᵐᵒᵖʳˢᵗᵘᵛʷˣʸᶻ₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎ₐₑₕᵢⱼₖₗₘₙₒₚᵣₛₜᵤᵥₓ]")
FORMULA_GARBLED_PATTERN = re.compile(r"[Ă₴₩÷�ÃÐÑþÿ]")

SUPERSCRIPT_MAP = {
    "⁰": "0",
    "¹": "1",
    "²": "2",
    "³": "3",
    "⁴": "4",
    "⁵": "5",
    "⁶": "6",
    "⁷": "7",
    "⁸": "8",
    "⁹": "9",
    "⁺": "+",
    "⁻": "-",
    "⁼": "=",
    "⁽": "(",
    "⁾": ")",
    "ⁿ": "n",
    "ᵃ": "a",
    "ᵇ": "b",
    "ᶜ": "c",
    "ᵈ": "d",
    "ᵉ": "e",
    "ᶠ": "f",
    "ᵍ": "g",
    "ʰ": "h",
    "ᶦ": "i",
    "ʲ": "j",
    "ᵏ": "k",
    "ˡ": "l",
    "ᵐ": "m",
    "ᵒ": "o",
    "ᵖ": "p",
    "ʳ": "r",
    "ˢ": "s",
    "ᵗ": "t",
    "ᵘ": "u",
    "ᵛ": "v",
    "ʷ": "w",
    "ˣ": "x",
    "ʸ": "y",
    "ᶻ": "z",
}

SUBSCRIPT_MAP = {
    "₀": "0",
    "₁": "1",
    "₂": "2",
    "₃": "3",
    "₄": "4",
    "₅": "5",
    "₆": "6",
    "₇": "7",
    "₈": "8",
    "₉": "9",
    "₊": "+",
    "₋": "-",
    "₌": "=",
    "₍": "(",
    "₎": ")",
    "ₐ": "a",
    "ₑ": "e",
    "ₕ": "h",
    "ᵢ": "i",
    "ⱼ": "j",
    "ₖ": "k",
    "ₗ": "l",
    "ₘ": "m",
    "ₙ": "n",
    "ₒ": "o",
    "ₚ": "p",
    "ᵣ": "r",
    "ₛ": "s",
    "ₜ": "t",
    "ᵤ": "u",
    "ᵥ": "v",
    "ₓ": "x",
}


@dataclass
class FormulaCandidate:
    line_number: int
    text: str
    latex_hint: str | None
    garbled: bool = False


def extract_formula_candidates(markdown: str) -> list[FormulaCandidate]:
    candidates: list[FormulaCandidate] = []
    for line_number, raw_line in enumerate(markdown.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if not MATH_LINE_PATTERN.search(stripped) and not SUPERSCRIPT_SUBSCRIPT_PATTERN.search(stripped):
            continue
        candidates.append(
            FormulaCandidate(
                line_number=line_number,
                text=stripped,
                latex_hint=to_latex_hint(stripped),
                garbled=bool(FORMULA_GARBLED_PATTERN.search(stripped)),
            )
        )
    return candidates


def is_formula_like_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return bool(MATH_LINE_PATTERN.search(stripped) or SUPERSCRIPT_SUBSCRIPT_PATTERN.search(stripped))


def has_garbled_formula_text(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    return bool(FORMULA_GARBLED_PATTERN.search(stripped))


def to_latex_hint(text: str) -> str | None:
    if not text:
        return None

    parts: list[str] = []
    index = 0
    changed = False
    while index < len(text):
        char = text[index]
        if char in SUPERSCRIPT_MAP:
            token = []
            while index < len(text) and text[index] in SUPERSCRIPT_MAP:
                token.append(SUPERSCRIPT_MAP[text[index]])
                index += 1
            parts.append(f"^{{{''.join(token)}}}")
            changed = True
            continue
        if char in SUBSCRIPT_MAP:
            token = []
            while index < len(text) and text[index] in SUBSCRIPT_MAP:
                token.append(SUBSCRIPT_MAP[text[index]])
                index += 1
            parts.append(f"_{{{''.join(token)}}}")
            changed = True
            continue
        parts.append(char)
        index += 1

    if not changed and not MATH_LINE_PATTERN.search(text):
        return None
    return "".join(parts)
