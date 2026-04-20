from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


_OVERRIDES_BY_FILE_NAME: dict[str, dict[str, Any]] = {
    "005-230311-050.乌海市包钢万腾钢铁有限责任公司技术协议.pdf": {
        "family_code": "lci_sync_drive",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "holdout_eval",
        "suggested_track": "holdout_eval",
        "phase_b_track": "holdout_eval",
        "document_type_hint": "mixed_engineering_pdf",
        "detected_profile": "mixed_engineering_pdf",
        "ingestion_recommendation": "text_primary_with_asset_review",
        "industry": "冶金",
        "product_line": "lci",
        "solution_family": "LCI / 同步电机变频软起动",
        "key_equipment": ["LCI变频启动装置", "整流变压器", "励磁控制柜"],
        "quality_tier": "medium",
        "manual_notes": "按文件名与 PDF 内部 QLCI 标记判定为 LCI 类技术协议，可作为高炉/鼓风机同步电机软起动参考样本。",
        "tags": ["curated", "lci", "冶金", "holdout_eval"],
        "details": {
            "source_confidence": "medium",
            "curation_basis": ["file_name", "pdf_internal_marker:QLCI"],
        },
    },
    "099-230101-001西安陕鼓（秦风气体）技术协议.pdf": {
        "family_code": "hv_vfd_multilevel",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "holdout_eval",
        "suggested_track": "holdout_eval",
        "phase_b_track": "holdout_eval",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "空分 / 气体 / 压缩机",
        "product_line": "hv_vfd",
        "solution_family": "高压变频器 / 多电平中压变频启动",
        "key_equipment": ["中压变频启动装置", "预充柜", "变压器柜", "功率柜", "控制柜", "输出电抗器柜"],
        "quality_tier": "high",
        "manual_notes": "已通过 PDFKit 抽取正文。该文档明确是 10kV 中压变频启动装置采购技术协议，覆盖空压机/增压机一拖二、移相整流变压器、IGBT 多级模块串联和 DCS/旁路接口，归入 hv_vfd_multilevel，而非 LCI。",
        "tags": ["curated", "hv_vfd_multilevel", "holdout_eval", "pdfkit_extracted", "压缩机"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["pdfkit_text_extraction"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/099-230101-001西安陕鼓（秦风气体）技术协议.pdf",
            "extracted_text_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/material-extraction/pdfkit/099-230101-001西安陕鼓（秦风气体）技术协议.txt",
        },
    },
    "099-230220-036.中韩石化技术协议10-26.pdf": {
        "family_code": "lci_sync_drive",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "holdout_eval",
        "suggested_track": "holdout_eval",
        "phase_b_track": "holdout_eval",
        "document_type_hint": "mixed_engineering_pdf",
        "detected_profile": "mixed_engineering_pdf",
        "ingestion_recommendation": "text_primary_with_asset_review",
        "industry": "石化",
        "product_line": "lci",
        "solution_family": "LCI / 同步电机变频软起动",
        "key_equipment": ["SFC启动装置", "整流变压器"],
        "quality_tier": "medium",
        "manual_notes": "PDF 内部可见 SFC 标记，归入 LCI / SFC 同步电机起动技术协议样本。",
        "tags": ["curated", "lci", "sfc", "石化", "holdout_eval"],
        "details": {
            "source_confidence": "medium",
            "curation_basis": ["file_name", "pdf_internal_marker:SFC"],
        },
    },
    "上电湛江中纸高浓磨机项目成套方案VerA.pdf": {
        "family_code": "lci_sync_drive",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "造纸 / 高浓磨机",
        "product_line": "lci",
        "solution_family": "LCI / 同步电机变频软起动",
        "key_equipment": ["LCI变频器", "输入变压器", "输出变压器", "本地控制单元PLC", "励磁柜", "高压开关柜"],
        "quality_tier": "high",
        "manual_notes": "已通过 PDFKit 抽取正文。该 37 页方案明确包含 LCI 变频软起系统、SFC 启动和同步过程、输入/输出变压器、励磁柜、Profibus-DP 与 I/O 接口，可作为 LCI 主样本。",
        "tags": ["curated", "lci", "pilot_main", "sfc", "pdfkit_extracted", "造纸"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["pdfkit_text_extraction"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/上电湛江中纸高浓磨机项目成套方案VerA.pdf",
            "extracted_text_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/material-extraction/pdfkit/上电湛江中纸高浓磨机项目成套方案VerA.txt",
        },
    },
    "10KV-高压固态及变频软起动技术方案-2025.3-荣信.doc": {
        "family_code": "hv_solid_state_starter",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "converted_docx",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "压缩机 / 通用工业",
        "product_line": "hv_softstart",
        "solution_family": "高压固态软起动",
        "key_equipment": ["高压固态软起柜", "高压变频软起装置", "高压开关柜"],
        "quality_tier": "high",
        "manual_notes": "已从 legacy DOC 转成 DOCX。正文同时覆盖高压固态软起和一拖二高压变频软起方案，当前以高压固态软起动作为主 family，并保留高压变频软起 secondary family 提示。",
        "tags": ["curated", "hv_solid_state", "hv_vfd_candidate", "pilot_main", "legacy_doc_converted", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["legacy_doc_conversion", "converted_docx_text_excerpt"],
            "secondary_family_codes": ["hv_vfd_multilevel"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/10KV-高压固态及变频软起动技术方案-2025.3-荣信.doc",
            "converted_asset_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/material-conversion/docx/10KV-高压固态及变频软起动技术方案-2025.3-荣信.docx",
        },
    },
    "V1-2024.10.14-自耦变软起动技术方案.doc": {
        "family_code": "autotransformer_starter",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "holdout_eval",
        "suggested_track": "holdout_eval",
        "phase_b_track": "holdout_eval",
        "document_type_hint": "converted_docx",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "通用工业",
        "product_line": "autotransformer",
        "solution_family": "自耦变软起动",
        "key_equipment": ["起动调压器", "调补控制柜", "电容柜"],
        "quality_tier": "high",
        "manual_notes": "已从 legacy DOC 转成 DOCX。正文包含 15000kW/6kV 电机参数、降压软起流程和供货清单，可作为自耦变软起动 holdout 样本。",
        "tags": ["curated", "autotransformer", "holdout_eval", "legacy_doc_converted", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["legacy_doc_conversion", "converted_docx_text_excerpt"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/V1-2024.10.14-自耦变软起动技术方案.doc",
            "converted_asset_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/material-conversion/docx/V1-2024.10.14-自耦变软起动技术方案.docx",
        },
    },
    "李勇--鑫义高压固态技术方案.doc": {
        "family_code": "hv_solid_state_starter",
        "material_type": "product_manual",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "converted_docx",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "通用工业",
        "product_line": "ggq",
        "solution_family": "高压固态软起动",
        "key_equipment": ["GGQ高压固态软起动装置"],
        "quality_tier": "high",
        "manual_notes": "已从 legacy DOC 转成 DOCX。正文更接近产品级技术协议/样本册，包含公司介绍、产品原理和技术特点，适合作为高压固态软起产品手册类资料。",
        "tags": ["curated", "hv_solid_state", "product_manual", "pilot_main", "legacy_doc_converted", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["legacy_doc_conversion", "converted_docx_text_excerpt"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/李勇--鑫义高压固态技术方案.doc",
            "converted_asset_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/material-conversion/docx/李勇--鑫义高压固态技术方案.docx",
        },
    },
    "玉溪汇钢水电阻软起动技术方案（）.doc": {
        "family_code": "liquid_resistor_starter",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "holdout_eval",
        "suggested_track": "holdout_eval",
        "phase_b_track": "holdout_eval",
        "document_type_hint": "converted_docx",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "冶金",
        "product_line": "liquid_resistor",
        "solution_family": "水电阻 / 液阻软起动",
        "key_equipment": ["高压液态软起动装置", "高压水电阻起动柜", "星点柜"],
        "quality_tier": "high",
        "manual_notes": "已从 legacy DOC 转成 DOCX。正文包含液态软起原理、联络信号、6300kW/10kV 电机参数和供货范围，可作为液阻软起 holdout 样本。",
        "tags": ["curated", "liquid_resistor", "holdout_eval", "legacy_doc_converted", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["legacy_doc_conversion", "converted_docx_text_excerpt"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/玉溪汇钢水电阻软起动技术方案（）.doc",
            "converted_asset_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/output/material-conversion/docx/玉溪汇钢水电阻软起动技术方案（）.docx",
        },
    },
    "2025-SDQ技术方案.docx": {
        "family_code": "liquid_resistor_starter",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "通用工业",
        "product_line": "sdq",
        "solution_family": "水电阻 / 液阻软起动",
        "key_equipment": ["水电阻起动柜", "高压电容柜"],
        "quality_tier": "high",
        "manual_notes": "正文明确为绕线水阻软启动柜方案，包含供货范围、元器件清单和技术指标，可作为 liquid_resistor_starter 主样本。",
        "tags": ["curated", "liquid_resistor", "pilot_main", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["docx_text_excerpt"],
        },
    },
    "临沂钢铁鼓风机电机及启动装置技术方案（9.24）.docx": {
        "family_code": "lci_sync_drive",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "冶金",
        "product_line": "lci",
        "solution_family": "LCI / 同步电机变频软起动",
        "key_equipment": ["LCI变频软起系统", "输入/输出变压器", "电机控制盘"],
        "quality_tier": "high",
        "manual_notes": "正文明确包含 LCI 变频启动特性、启动和同步过程、输入/输出变压器规范，是高质量 LCI 主样本。",
        "tags": ["curated", "lci", "pilot_main", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["docx_text_excerpt"],
        },
    },
    "乌海建龙钢铁环冷风机永磁电机+变频节能改造技术方案20240319(1).docx": {
        "family_code": "pm_motor_vfd_retrofit",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "冶金",
        "product_line": "pm_vfd_retrofit",
        "solution_family": "永磁电机 + 高压变频节能改造",
        "key_equipment": ["高压永磁同步电机", "高压变频柜"],
        "quality_tier": "high",
        "manual_notes": "正文明确为永磁电机配套高压变频节能改造，含设备清单、点位和节电估算，可作为 pm_motor_vfd_retrofit 主样本。",
        "tags": ["curated", "pm_vfd_retrofit", "pilot_main", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["docx_text_excerpt"],
        },
    },
    "宝山钢铁股份有限公司三鼓风LCI改造方案.docx": {
        "family_code": "lci_sync_drive",
        "material_type": "proposal_sample",
        "availability_status": "available",
        "assigned_track": "pilot_main",
        "suggested_track": "pilot_main",
        "phase_b_track": "pilot_main",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "冶金",
        "product_line": "lci",
        "solution_family": "LCI / 同步电机变频软起动",
        "key_equipment": ["LCI变频启动装置", "整流变压器", "输入变压器"],
        "quality_tier": "high",
        "manual_notes": "正文明确为三鼓风 LCI 变频启动装置技术协议，包含启动同步过程和变频器技术数据，是 LCI 主样本。",
        "tags": ["curated", "lci", "pilot_main", "docx_ready"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["docx_text_excerpt"],
        },
    },
    "赞比亚-恩多拉机场-成套设备维保巡检方案.docx": {
        "family_code": None,
        "material_type": "service_plan",
        "availability_status": "available",
        "assigned_track": "holdout_eval",
        "suggested_track": "holdout_eval",
        "phase_b_track": "holdout_eval",
        "document_type_hint": "text_digital",
        "detected_profile": "text_digital",
        "ingestion_recommendation": "main_vector_ready",
        "industry": "机场配电 / 运维",
        "product_line": "service_plan",
        "solution_family": None,
        "key_equipment": ["高低压柜", "SVG", "直流屏", "环网柜"],
        "quality_tier": "medium",
        "manual_notes": "已确认正文完整可解析。这是成套设备维保巡检方案，不属于首批产品族建模范围；保留在 registry 里作为服务类参考样本，无需继续人工复核。",
        "tags": ["curated", "service_plan", "holdout_eval"],
        "details": {
            "source_confidence": "high",
            "curation_basis": ["docx_text_excerpt"],
            "working_copy_path": "/Volumes/thunder/code/RAG_test-product-driven-solution/private_samples/real_proposals/赞比亚-恩多拉机场-成套设备维保巡检方案.docx",
        },
    },
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Enrich a product-driven sample manifest with curated material labels.")
    parser.add_argument(
        "--input-json",
        default=None,
        help="Path to the input manifest JSON. Defaults to output/product-driven-sample-manifest.json under the repo root.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Path to the enriched manifest JSON. Defaults to output/product-driven-sample-manifest-enriched.json under the repo root.",
    )
    parser.add_argument(
        "--output-md",
        default=None,
        help="Path to the enriched manifest markdown. Defaults to output/product-driven-sample-manifest-enriched.md under the repo root.",
    )
    return parser.parse_args()


def _render_counter(counter: Counter[str]) -> dict[str, int]:
    return {key: counter[key] for key in sorted(counter)}


def _merge_entry(entry: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    merged = dict(entry)
    merged.update({key: value for key, value in override.items() if key not in {"details"}})
    details = dict(entry.get("details") or {}) if isinstance(entry.get("details"), dict) else {}
    if isinstance(override.get("details"), dict):
        details.update(override["details"])
    if details:
        merged["details"] = details
    return merged


def _summarize(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "track_counts": _render_counter(Counter(str(entry.get("assigned_track") or "unknown") for entry in entries)),
        "suggested_track_counts": _render_counter(Counter(str(entry.get("suggested_track") or "unknown") for entry in entries)),
        "profile_counts": _render_counter(Counter(str(entry.get("detected_profile") or "unknown") for entry in entries)),
        "ingestion_recommendation_counts": _render_counter(
            Counter(str(entry.get("ingestion_recommendation") or "unknown") for entry in entries)
        ),
        "format_counts": _render_counter(Counter(str(entry.get("file_format") or "unknown") for entry in entries)),
        "family_counts": _render_counter(Counter(str(entry.get("family_code") or "unclassified") for entry in entries)),
        "material_type_counts": _render_counter(Counter(str(entry.get("material_type") or "proposal_sample") for entry in entries)),
        "availability_status_counts": _render_counter(
            Counter(str(entry.get("availability_status") or "review_needed") for entry in entries)
        ),
    }


def _build_phase_b_plan(entries: list[dict[str, Any]]) -> dict[str, Any]:
    payload: dict[str, list[dict[str, Any]]] = {
        "holdout_eval": [],
        "pilot_main": [],
        "needs_review": [],
        "ocr_asset_only": [],
    }
    for entry in entries:
        track = str(entry.get("phase_b_track") or entry.get("assigned_track") or "").strip()
        if track in payload:
            payload[track].append(
                {
                    "sample_id": entry.get("sample_id"),
                    "file_name": entry.get("file_name"),
                    "family_code": entry.get("family_code"),
                    "material_type": entry.get("material_type"),
                }
            )
    return payload


def _render_markdown(manifest: dict[str, Any]) -> str:
    lines = [
        "# Product-Driven Sample Manifest Enriched",
        "",
        f"- Generated at: `{manifest.get('generated_at')}`",
        f"- Total samples: `{manifest.get('total_samples')}`",
        "",
        "## Summary",
        "",
    ]
    summary = manifest.get("summary") or {}
    for key in (
        "family_counts",
        "material_type_counts",
        "availability_status_counts",
        "track_counts",
        "profile_counts",
        "ingestion_recommendation_counts",
    ):
        payload = summary.get(key) or {}
        joined = ", ".join(f"`{item}={count}`" for item, count in payload.items()) if payload else "`none`"
        lines.append(f"- {key}: {joined}")

    lines.extend(
        [
            "",
            "## Entries",
            "",
            "| file_name | family_code | material_type | availability_status | assigned_track | quality_tier |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    for entry in manifest.get("entries") or []:
        lines.append(
            "| {file_name} | {family_code} | {material_type} | {availability_status} | {assigned_track} | {quality_tier} |".format(
                file_name=entry.get("file_name") or "",
                family_code=entry.get("family_code") or "unclassified",
                material_type=entry.get("material_type") or "proposal_sample",
                availability_status=entry.get("availability_status") or "review_needed",
                assigned_track=entry.get("assigned_track") or "",
                quality_tier=entry.get("quality_tier") or "",
            )
        )

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    input_json = Path(args.input_json).expanduser() if args.input_json else repo_root / "output" / "product-driven-sample-manifest.json"
    output_json = Path(args.output_json).expanduser() if args.output_json else repo_root / "output" / "product-driven-sample-manifest-enriched.json"
    output_md = Path(args.output_md).expanduser() if args.output_md else repo_root / "output" / "product-driven-sample-manifest-enriched.md"

    manifest = json.loads(input_json.read_text(encoding="utf-8"))
    entries = manifest.get("entries")
    if not isinstance(entries, list):
        raise SystemExit("Input manifest does not contain an entries list.")

    enriched_entries: list[dict[str, Any]] = []
    missing_overrides: list[str] = []
    for entry in entries:
        file_name = str(entry.get("file_name") or "")
        override = _OVERRIDES_BY_FILE_NAME.get(file_name)
        if override is None:
            missing_overrides.append(file_name)
            enriched_entries.append(entry)
            continue
        enriched_entries.append(_merge_entry(entry, override))

    enriched_manifest = {
        **manifest,
        "version": 2,
        "source_manifest_path": str(input_json),
        "summary": _summarize(enriched_entries),
        "phase_b_plan": _build_phase_b_plan(enriched_entries),
        "entries": enriched_entries,
    }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_md.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(enriched_manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    output_md.write_text(_render_markdown(enriched_manifest), encoding="utf-8")

    print(f"input={input_json}")
    print(f"output_json={output_json}")
    print(f"output_md={output_md}")
    print(f"entries={len(enriched_entries)}")
    print(f"overrides_applied={len(enriched_entries) - len(missing_overrides)}")
    if missing_overrides:
        print("missing_overrides=" + ",".join(missing_overrides))


if __name__ == "__main__":
    main()
