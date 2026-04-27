from __future__ import annotations

from app.services.composition.semantic_rules import get_semantic_rules


def test_semantic_rules_load_external_token_groups() -> None:
    rules = get_semantic_rules()

    assert "启动曲线" in rules.tokens("installation.noise")
    assert "备品备件" in rules.tokens("spare_parts.focus")
    assert "负载数据" in rules.tokens("spec.curve_or_load_noise")
    assert "短路阻抗" in rules.tokens("transformer_spec.concrete")
    assert "额定功率" in rules.tokens("motor_spec.concrete")
    assert "技术规范" in rules.tokens("spec.parameter_signal")
    assert "input_transformer" in rules.tokens("parameter_keys.transformer_spec")
    assert "motor_type" in rules.tokens("parameter_keys.motor_spec")
    assert "overall_solution" in rules.section_types("technical_scheme")


def test_semantic_rules_missing_group_returns_fallback() -> None:
    rules = get_semantic_rules()

    assert rules.tokens("missing.group", ("fallback",)) == ("fallback",)
    assert rules.section_types("missing.group", {"fallback_type"}) == {"fallback_type"}
