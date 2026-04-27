from __future__ import annotations

import argparse
import importlib.util
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SyntheticMaterial:
    material_key: str
    family_code: str | None
    material_type: str
    file_name: str
    title: str
    body: str
    source_material_keys: list[str]
    source_document_names: list[str]
    source_family_codes: list[str]
    tags: list[str]


def _load_defaults(repo_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    defaults_path = repo_root / "backend" / "app" / "services" / "catalog" / "defaults.py"
    spec = importlib.util.spec_from_file_location("synthetic_catalog_defaults", defaults_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load catalog defaults module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return (
        list(module.DEFAULT_PRODUCT_FAMILIES),
        list(module.DEFAULT_PRODUCT_CATALOG),
        list(module.DEFAULT_PRODUCT_MODELS),
        list(module.DEFAULT_PRODUCT_INTERFACES),
        list(module.DEFAULT_PRODUCT_COMPATIBILITY),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic test-only product materials derived from real proposal samples and catalog defaults."
    )
    parser.add_argument(
        "--sample-manifest",
        default=None,
        help="Path to the enriched real sample manifest. Defaults to output/product-driven-sample-manifest-enriched.json.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Directory to write generated synthetic material markdown files. Defaults to output/synthetic-materials.",
    )
    parser.add_argument(
        "--manifest-output",
        default=None,
        help="Path to write the generated synthetic material manifest JSON. Defaults to output/product-driven-synthetic-test-materials-manifest.json.",
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("\n", "<br>") for cell in row) + " |")
    return "\n".join(lines)


def _bullet_list(items: list[str]) -> str:
    if not items:
        return "- 无"
    return "\n".join(f"- {item}" for item in items)


def _series_code_to_name(series_code: str, series_by_code: dict[str, dict[str, Any]]) -> str:
    series = series_by_code.get(series_code) or {}
    return str(series.get("series_name") or series_code)


def _summarize_signal_spec(spec: dict[str, Any]) -> str:
    parts: list[str] = []
    for key, value in spec.items():
        label = str(key).replace("_", " ")
        if isinstance(value, list):
            rendered_value = " / ".join(str(entry) for entry in value if str(entry).strip())
        elif isinstance(value, dict):
            rendered_value = "；".join(
                f"{str(sub_key).replace('_', ' ')}={sub_value}"
                for sub_key, sub_value in value.items()
                if str(sub_value).strip()
            )
        else:
            rendered_value = str(value)
        if rendered_value.strip():
            parts.append(f"{label}: {rendered_value}")
    return "；".join(parts) or "-"


def _collect_family_sources(entries: list[dict[str, Any]], family_code: str) -> tuple[list[str], list[str], list[str]]:
    family_entries = [entry for entry in entries if str(entry.get("family_code") or "").strip() == family_code]
    material_keys = [str(entry.get("sample_id") or "").strip() for entry in family_entries if str(entry.get("sample_id") or "").strip()]
    document_names = [str(entry.get("file_name") or "").strip() for entry in family_entries if str(entry.get("file_name") or "").strip()]
    notes = [str(entry.get("manual_notes") or "").strip() for entry in family_entries if str(entry.get("manual_notes") or "").strip()]
    return material_keys, document_names, notes


def _build_product_manual_material(
    *,
    family: dict[str, Any],
    series: dict[str, Any],
    models: list[dict[str, Any]],
    interfaces: list[dict[str, Any]],
    compatibility_rules: list[dict[str, Any]],
    series_by_code: dict[str, dict[str, Any]],
    source_material_keys: list[str],
    source_document_names: list[str],
    source_notes: list[str],
) -> SyntheticMaterial:
    family_code = str(family.get("code") or "")
    display_name = str(family.get("display_name") or family.get("name") or family_code)
    title = f"{display_name} 测试开发用产品手册"
    model_rows = [
        [
            str(item.get("model_number") or ""),
            str(item.get("rated_voltage") or "待确认"),
            str(item.get("rated_power_kw") or "待确认"),
            str(item.get("rated_current") or "待确认"),
            str(item.get("source_material_key") or "待确认"),
        ]
        for item in models
    ]
    config_rows: list[list[str]] = []
    for config in series.get("standard_configs") or []:
        components = config.get("components") or []
        if not components:
            config_rows.append(
                [
                    str(config.get("config_name") or "标准配置"),
                    "主设备",
                    str(series.get("series_name") or family_code),
                    "1",
                    str(config.get("description") or "标准主机配置"),
                ]
            )
            continue
        config_rows.append(
            [
                str(config.get("config_name") or "标准配置"),
                "主设备",
                str(series.get("series_name") or family_code),
                "1",
                str(config.get("description") or "主机配置"),
            ]
        )
        for component in components:
            config_rows.append(
                [
                    str(config.get("config_name") or "标准配置"),
                    str(component.get("role") or "配套设备"),
                    _series_code_to_name(str(component.get("series_code") or ""), series_by_code),
                    str(component.get("quantity") or 1),
                    str(component.get("config") or "标准配置"),
                ]
            )
    interface_rows: list[list[str]] = []
    for item in interfaces:
        spec = item.get("signal_spec") or {}
        interface_rows.append(
            [
                str(item.get("interface_type") or "unknown"),
                str(item.get("protocol") or "-"),
                _summarize_signal_spec(spec),
                str(item.get("notes") or "").strip() or "-",
            ]
        )
    rule_items = [
        f"{str(item.get('condition') or '').strip()}：{str(item.get('action') or '').strip()}（{str(item.get('severity') or 'warning')}）"
        for item in series.get("constraints") or []
        if str(item.get("condition") or "").strip() and str(item.get("action") or "").strip()
    ]
    rule_items.extend(
        (
            f"{str(item.get('condition') or '成套配套')}: "
            f"建议补齐 "
            f"{', '.join(_series_code_to_name(str(code), series_by_code) for code in (item.get('preferred_series_codes') or [])) or str(item.get('target_family_code') or '')}"
        )
        for item in compatibility_rules
    )
    alias_items = [str(alias) for alias in (family.get("aliases") or []) if str(alias).strip()]

    body = "\n".join(
        [
            f"# {title}",
            "",
            "> 声明：本文件为 `synthetic_test_only` 测试开发材料，由真实方案文档与当前产品目录结构化信息派生，仅用于研发验证，不得替代客户正式手册。",
            "",
            "## 来源依据",
            "",
            _bullet_list(source_document_names),
            "",
            "### 来源摘要",
            "",
            _bullet_list(source_notes[:4]),
            "",
            "## 产品定位",
            "",
            f"- 产品族：{display_name}",
            f"- 系列名称：{str(series.get('series_name') or family_code)}",
            f"- 适用场景：{', '.join(str(item) for item in (series.get('preferred_scenarios') or [])) or '待补充'}",
            f"- 适用负载：{', '.join(str(item) for item in (series.get('applicable_loads') or [])) or '待补充'}",
            f"- 适用电机：{', '.join(str(item) for item in (series.get('applicable_motors') or [])) or '待补充'}",
            f"- 电压等级：{', '.join(str(item) for item in (series.get('voltage_levels') or [])) or '待补充'}",
            f"- 拓扑/原理：{str(series.get('topology') or '待补充')}",
            "",
            "## 型号基线",
            "",
            _markdown_table(
                ["型号", "电压等级", "额定功率(kW)", "额定电流", "来源 material_key"],
                model_rows or [["待补充", "待补充", "待补充", "待补充", "待补充"]],
            ),
            "",
            "## 标准配置",
            "",
            _markdown_table(
                ["配置名称", "角色", "设备/系列", "数量", "说明"],
                config_rows or [["标准配置", "主设备", str(series.get("series_name") or family_code), "1", "待补充"]],
            ),
            "",
            "## 接口能力基线",
            "",
            _markdown_table(
                ["接口类型", "协议", "信号/接口要点", "备注"],
                interface_rows or [["待补充", "-", "-", "-"]],
            ),
            "",
            "## 选型与边界提醒",
            "",
            _bullet_list(rule_items[:8]),
            "",
            "## 名称与术语别名",
            "",
            _bullet_list(alias_items),
            "",
        ]
    ).strip() + "\n"
    return SyntheticMaterial(
        material_key=f"synthetic-{family_code}-product-manual-v1",
        family_code=family_code,
        material_type="product_manual",
        file_name=f"{family_code}-product-manual-synthetic-test-only.md",
        title=title,
        body=body,
        source_material_keys=source_material_keys,
        source_document_names=source_document_names,
        source_family_codes=[family_code],
        tags=["synthetic_test_only", "derived_from_real_docs", "product_manual"],
    )


def _build_standard_bom_material(
    *,
    family: dict[str, Any],
    series: dict[str, Any],
    source_material_keys: list[str],
    source_document_names: list[str],
    source_notes: list[str],
    series_by_code: dict[str, dict[str, Any]],
) -> SyntheticMaterial:
    family_code = str(family.get("code") or "")
    display_name = str(family.get("display_name") or family.get("name") or family_code)
    rows: list[list[str]] = []
    for config in series.get("standard_configs") or []:
        config_name = str(config.get("config_name") or "标准配置")
        rows.append([config_name, "主设备", str(series.get("series_name") or family_code), "1", "主机本体"])
        for component in config.get("components") or []:
            component_series_code = str(component.get("series_code") or "")
            rows.append(
                [
                    config_name,
                    str(component.get("role") or "配套设备"),
                    _series_code_to_name(component_series_code, series_by_code),
                    str(component.get("quantity") or 1),
                    str(component.get("config") or "标准配置"),
                ]
            )
    if not rows:
        rows.append(["标准配置", "主设备", str(series.get("series_name") or family_code), "1", "主机本体"])
    body = "\n".join(
        [
            f"# {display_name} 测试开发用标准 BOM",
            "",
            "> 声明：本文件为 `synthetic_test_only` 目录级标准 BOM，由真实方案样本和目录标准配置派生，仅用于研发验证，不替代客户最终 BOM。",
            "",
            "## 来源依据",
            "",
            _bullet_list(source_document_names),
            "",
            "## 来源摘要",
            "",
            _bullet_list(source_notes[:3]),
            "",
            "## 标准配置矩阵",
            "",
            _markdown_table(["配置名称", "角色", "设备/系列", "数量", "说明"], rows),
            "",
            "## 使用边界",
            "",
            _bullet_list(
                [
                    "本 BOM 仅反映当前目录级标准配置与关键配套关系。",
                    "备品备件、随机资料、线缆附件及安装施工边界仍需结合正式供货清单锁定。",
                    "若项目存在旁路、不停机检修或特殊环境条件，应在正式 BOM 中单独确认扩展配置。",
                ]
            ),
            "",
        ]
    ).strip() + "\n"
    return SyntheticMaterial(
        material_key=f"synthetic-{family_code}-standard-bom-v1",
        family_code=family_code,
        material_type="standard_bom",
        file_name=f"{family_code}-standard-bom-synthetic-test-only.md",
        title=f"{display_name} 测试开发用标准 BOM",
        body=body,
        source_material_keys=source_material_keys,
        source_document_names=source_document_names,
        source_family_codes=[family_code],
        tags=["synthetic_test_only", "derived_from_real_docs", "standard_bom"],
    )


def _flatten_interface_rows(interfaces: list[dict[str, Any]]) -> list[list[str]]:
    rows: list[list[str]] = []
    for item in interfaces:
        interface_type = str(item.get("interface_type") or "unknown")
        protocol = str(item.get("protocol") or "-")
        spec = item.get("signal_spec") or {}
        notes = str(item.get("notes") or "").strip() or "-"
        for key, value in spec.items():
            if isinstance(value, list):
                for entry in value:
                    rows.append([interface_type, key, str(entry), protocol, notes])
            else:
                rows.append([interface_type, key, str(value), protocol, notes])
    return rows


def _build_interface_schedule_material(
    *,
    family: dict[str, Any],
    series: dict[str, Any],
    interfaces: list[dict[str, Any]],
    source_material_keys: list[str],
    source_document_names: list[str],
    source_notes: list[str],
) -> SyntheticMaterial:
    family_code = str(family.get("code") or "")
    display_name = str(family.get("display_name") or family.get("name") or family_code)
    rows = _flatten_interface_rows(interfaces)
    if not rows:
        rows = [["待补充", "待补充", "待补充", "-", "-"]]
    body = "\n".join(
        [
            f"# {display_name} 测试开发用接口/点表基线",
            "",
            "> 声明：本文件为 `synthetic_test_only` 接口/点表基线，由真实方案样本与当前接口注册表派生，仅用于研发验证，不替代客户正式点表。",
            "",
            "## 来源依据",
            "",
            _bullet_list(source_document_names),
            "",
            "## 来源摘要",
            "",
            _bullet_list(source_notes[:3]),
            "",
            "## 接口/信号基线",
            "",
            _markdown_table(["接口类型", "信号分组", "信号/要点", "协议", "备注"], rows),
            "",
            "## 使用边界",
            "",
            _bullet_list(
                [
                    f"当前接口基线面向 {str(series.get('series_name') or family_code)} 的最小可用方案设计层。",
                    "正式站点地址、点位编号、I/O 地址分配和联锁矩阵需在客户接口表到位后补齐。",
                    "若项目使用非默认协议或存在一拖二/旁路联锁场景，应在正式点表阶段重新核定。",
                ]
            ),
            "",
        ]
    ).strip() + "\n"
    return SyntheticMaterial(
        material_key=f"synthetic-{family_code}-interface-schedule-v1",
        family_code=family_code,
        material_type="interface_schedule",
        file_name=f"{family_code}-interface-schedule-synthetic-test-only.md",
        title=f"{display_name} 测试开发用接口/点表基线",
        body=body,
        source_material_keys=source_material_keys,
        source_document_names=source_document_names,
        source_family_codes=[family_code],
        tags=["synthetic_test_only", "derived_from_real_docs", "interface_schedule"],
    )


def _build_selection_rule_material(
    *,
    family: dict[str, Any],
    series: dict[str, Any],
    compatibility_rules: list[dict[str, Any]],
    source_material_keys: list[str],
    source_document_names: list[str],
    source_notes: list[str],
    series_by_code: dict[str, dict[str, Any]],
) -> SyntheticMaterial:
    family_code = str(family.get("code") or "")
    display_name = str(family.get("display_name") or family.get("name") or family_code)
    rows: list[list[str]] = []
    for constraint in series.get("constraints") or []:
        rows.append(
            [
                str(constraint.get("constraint_type") or "general"),
                str(constraint.get("condition") or "待确认"),
                str(constraint.get("action") or "待确认"),
                str(constraint.get("severity") or "warning"),
                "catalog_default_constraint",
            ]
        )
    for item in compatibility_rules:
        preferred_codes = [str(code) for code in (item.get("preferred_series_codes") or []) if str(code).strip()]
        rows.append(
            [
                str(item.get("relation_type") or "recommended"),
                str(item.get("condition") or "成套配套"),
                ", ".join(_series_code_to_name(code, series_by_code) for code in preferred_codes) or str(item.get("target_family_code") or ""),
                "info",
                "catalog_compatibility_rule",
            ]
        )
    if not rows:
        rows.append(["general", "待确认", "待确认", "warning", "synthetic"])
    body = "\n".join(
        [
            f"# {display_name} 测试开发用选型规则",
            "",
            "> 声明：本文件为 `synthetic_test_only` 选型规则集，由真实方案样本和目录约束/配套关系派生，仅用于研发验证，不替代正式选型规范。",
            "",
            "## 来源依据",
            "",
            _bullet_list(source_document_names),
            "",
            "## 来源摘要",
            "",
            _bullet_list(source_notes[:3]),
            "",
            "## 规则清单",
            "",
            _markdown_table(["规则类型", "触发条件", "动作/要求", "严重级别", "来源"], rows),
            "",
            "## 使用边界",
            "",
            _bullet_list(
                [
                    "本规则集当前仅覆盖目录中已经沉淀的主设备、配套关系和环境约束。",
                    "客户现场电网条件、海拔、负载惯量、检修策略等仍需结合真实技术协议补充。",
                    "正式选型规则应以客户确认版约束文件为准。",
                ]
            ),
            "",
        ]
    ).strip() + "\n"
    return SyntheticMaterial(
        material_key=f"synthetic-{family_code}-selection-rule-v1",
        family_code=family_code,
        material_type="selection_rule",
        file_name=f"{family_code}-selection-rule-synthetic-test-only.md",
        title=f"{display_name} 测试开发用选型规则",
        body=body,
        source_material_keys=source_material_keys,
        source_document_names=source_document_names,
        source_family_codes=[family_code],
        tags=["synthetic_test_only", "derived_from_real_docs", "selection_rule"],
    )


def _build_model_alias_map_material(
    *,
    families: list[dict[str, Any]],
    series_rows: list[dict[str, Any]],
    model_rows: list[dict[str, Any]],
    manifest_entries: list[dict[str, Any]],
) -> SyntheticMaterial:
    family_map = {str(item.get("code") or ""): item for item in families}
    rows: list[list[str]] = []
    for family_code in ("lci_sync_drive", "hv_vfd_multilevel", "hv_solid_state_starter"):
        family = family_map.get(family_code) or {}
        canonical_name = str(family.get("display_name") or family.get("name") or family_code)
        for alias in family.get("aliases") or []:
            rows.append(["family_alias", family_code, canonical_name, str(alias), "catalog_family_alias"])
    for item in series_rows:
        family_code = str(item.get("family_code") or "")
        if family_code not in {"lci_sync_drive", "hv_vfd_multilevel", "hv_solid_state_starter"}:
            continue
        canonical_name = str(item.get("series_name") or item.get("code") or "")
        rows.append(["series_code", family_code, canonical_name, str(item.get("code") or ""), "catalog_series_code"])
    for item in model_rows:
        series_code = str(item.get("series_code") or "")
        if series_code not in {"lci_sync_drive", "hv_vfd_multilevel", "hv_solid_state_starter"}:
            continue
        rows.append(
            [
                "model_number",
                series_code,
                series_code,
                str(item.get("model_number") or ""),
                str(item.get("source_material_key") or "catalog_model"),
            ]
        )
    source_material_keys = [str(entry.get("sample_id") or "").strip() for entry in manifest_entries if str(entry.get("sample_id") or "").strip()]
    source_document_names = [str(entry.get("file_name") or "").strip() for entry in manifest_entries if str(entry.get("file_name") or "").strip()]
    body = "\n".join(
        [
            "# 测试开发用型号与术语映射表",
            "",
            "> 声明：本文件为 `synthetic_test_only` 型号与术语映射材料，由真实样本名称、产品目录和型号基线派生，仅用于研发验证，不替代正式主数据编码规范。",
            "",
            "## 覆盖范围",
            "",
            _bullet_list(
                [
                    "LCI / 同步电机软起动",
                    "高压变频器多电平驱动系统",
                    "高压固态软起动系统",
                ]
            ),
            "",
            "## 映射清单",
            "",
            _markdown_table(["映射类型", "canonical_code", "canonical_name", "alias_or_model", "来源"], rows),
            "",
            "## 使用边界",
            "",
            _bullet_list(
                [
                    "该映射表主要服务于样本召回、目录命中和方案章节归一，不保证覆盖客户内部全部口径。",
                    "正式术语/型号映射应在客户提供主数据编码规范后替换。",
                ]
            ),
            "",
        ]
    ).strip() + "\n"
    return SyntheticMaterial(
        material_key="synthetic-model-alias-map-v1",
        family_code=None,
        material_type="model_alias_map",
        file_name="model-alias-map-synthetic-test-only.md",
        title="测试开发用型号与术语映射表",
        body=body,
        source_material_keys=source_material_keys,
        source_document_names=source_document_names,
        source_family_codes=["lci_sync_drive", "hv_vfd_multilevel", "hv_solid_state_starter"],
        tags=["synthetic_test_only", "derived_from_real_docs", "model_alias_map"],
    )


def _build_readme(materials: list[SyntheticMaterial]) -> str:
    rows = [
        [
            material.material_key,
            material.material_type,
            material.family_code or "unclassified",
            material.file_name,
            ", ".join(material.source_document_names[:3]),
        ]
        for material in materials
    ]
    return "\n".join(
        [
            "# Synthetic Test-Only Materials",
            "",
            "本目录中的材料均为基于当前真实方案文档库和目录结构化信息派生的测试开发用材料。",
            "它们只用于研发验证，不可视为真实客户资料，也不应被计入长期路线图 Entry Gate 的真实门禁。",
            "",
            _markdown_table(["material_key", "material_type", "family_code", "file_name", "source_documents"], rows),
            "",
        ]
    ).strip() + "\n"


def _build_manifest_entry(
    *,
    material: SyntheticMaterial,
    target_path: Path,
) -> dict[str, Any]:
    return {
        "material_key": material.material_key,
        "document_name": material.title,
        "file_name": target_path.name,
        "file_path": str(target_path),
        "source_kind": "synthetic_test_only",
        "file_format": "md",
        "file_size_bytes": target_path.stat().st_size,
        "family_code": material.family_code,
        "material_type": material.material_type,
        "availability_status": "available",
        "assigned_track": "synthetic_dev",
        "suggested_track": "synthetic_dev",
        "priority_tier": "p1",
        "manual_notes": "测试开发用 synthetic 材料，由真实方案文档与目录结构化信息派生，不计入真实门禁。",
        "tags": material.tags,
        "details": {
            "synthetic_test_only": True,
            "formal_gate_eligible": False,
            "generation_method": "catalog_defaults_plus_real_samples",
            "derived_from_material_keys": material.source_material_keys,
            "source_document_names": material.source_document_names,
            "source_family_codes": material.source_family_codes,
        },
    }


def main() -> None:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    sample_manifest_path = (
        Path(args.sample_manifest).expanduser()
        if args.sample_manifest
        else repo_root / "output" / "product-driven-sample-manifest-enriched.json"
    )
    output_dir = (
        Path(args.output_dir).expanduser()
        if args.output_dir
        else repo_root / "output" / "synthetic-materials"
    )
    manifest_output_path = (
        Path(args.manifest_output).expanduser()
        if args.manifest_output
        else repo_root / "output" / "product-driven-synthetic-test-materials-manifest.json"
    )

    if not sample_manifest_path.exists():
        raise SystemExit(f"sample manifest not found: {sample_manifest_path}")

    families, series_rows, model_rows, interface_rows, compatibility_rows = _load_defaults(repo_root)
    manifest_payload = _load_json(sample_manifest_path)
    manifest_entries = list(manifest_payload.get("entries") or [])
    family_by_code = {str(item.get("code") or ""): item for item in families}
    series_by_code = {str(item.get("code") or ""): item for item in series_rows}
    models_by_series_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in model_rows:
        models_by_series_code[str(item.get("series_code") or "")].append(item)
    interfaces_by_series_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in interface_rows:
        interfaces_by_series_code[str(item.get("series_code") or "")].append(item)
    compatibility_by_source_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in compatibility_rows:
        compatibility_by_source_family[str(item.get("source_family_code") or "")].append(item)

    synthetic_materials: list[SyntheticMaterial] = []
    for family_code in ("lci_sync_drive", "hv_vfd_multilevel"):
        family = family_by_code[family_code]
        series = series_by_code[family_code]
        source_keys, source_docs, source_notes = _collect_family_sources(manifest_entries, family_code)
        synthetic_materials.append(
            _build_product_manual_material(
                family=family,
                series=series,
                models=models_by_series_code.get(family_code, []),
                interfaces=interfaces_by_series_code.get(family_code, []),
                compatibility_rules=compatibility_by_source_family.get(family_code, []),
                series_by_code=series_by_code,
                source_material_keys=source_keys,
                source_document_names=source_docs,
                source_notes=source_notes,
            )
        )
        synthetic_materials.append(
            _build_standard_bom_material(
                family=family,
                series=series,
                source_material_keys=source_keys,
                source_document_names=source_docs,
                source_notes=source_notes,
                series_by_code=series_by_code,
            )
        )
        synthetic_materials.append(
            _build_interface_schedule_material(
                family=family,
                series=series,
                interfaces=interfaces_by_series_code.get(family_code, []),
                source_material_keys=source_keys,
                source_document_names=source_docs,
                source_notes=source_notes,
            )
        )
        synthetic_materials.append(
            _build_selection_rule_material(
                family=family,
                series=series,
                compatibility_rules=compatibility_by_source_family.get(family_code, []),
                source_material_keys=source_keys,
                source_document_names=source_docs,
                source_notes=source_notes,
                series_by_code=series_by_code,
            )
        )

    synthetic_materials.append(
        _build_model_alias_map_material(
            families=families,
            series_rows=series_rows,
            model_rows=model_rows,
            manifest_entries=manifest_entries,
        )
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_entries_out: list[dict[str, Any]] = []
    for material in synthetic_materials:
        target_path = output_dir / material.file_name
        target_path.write_text(material.body, encoding="utf-8")
        manifest_entries_out.append(_build_manifest_entry(material=material, target_path=target_path))

    readme_path = output_dir / "README.md"
    readme_path.write_text(_build_readme(synthetic_materials), encoding="utf-8")

    manifest_output_path.write_text(
        json.dumps(
            {
                "version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "generator": "backend/scripts/generate_synthetic_test_materials.py",
                "source_manifest_path": str(sample_manifest_path),
                "source_kind": "synthetic_test_only",
                "entries": manifest_entries_out,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"generated_material_count={len(synthetic_materials)}")
    print(f"output_dir={output_dir}")
    print(f"manifest_path={manifest_output_path}")


if __name__ == "__main__":
    main()
