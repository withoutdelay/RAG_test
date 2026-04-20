from __future__ import annotations

from typing import Any


PRODUCT_LINE_QUERY_HINTS: dict[str, tuple[str, ...]] = {
    "lci": ("LCI", "SFC", "同步电机", "变频软起", "软起动", "整流变压器", "励磁控制柜"),
    "hv_vfd": ("高压变频", "高压变频器", "中压变频", "多电平", "移相整流变压器", "IGBT", "变频软起", "一拖二"),
    "hv_softstart": ("高压固态", "高压固态软起动", "晶闸管", "旁路", "软起动", "变频软起"),
    "ggq": ("高压固态", "高压固态软起动", "晶闸管", "旁路", "软起动"),
    "autotransformer": ("自耦变", "自耦降压", "自耦变软起动", "起动调压器", "软起动"),
    "liquid_resistor": ("水电阻", "液阻", "液态软起动", "电解液", "软起动"),
    "sdq": ("水电阻", "液阻", "液态软起动", "电解液", "软起动"),
    "pm_vfd_retrofit": ("永磁电机", "高压变频", "节能改造", "环冷风机", "永磁变频"),
}

ENTRY_SIGNAL_HINTS: dict[str, tuple[str, ...]] = {
    "lci_sync_drive": PRODUCT_LINE_QUERY_HINTS["lci"],
    "hv_vfd_multilevel": PRODUCT_LINE_QUERY_HINTS["hv_vfd"],
    "hv_solid_state_starter": PRODUCT_LINE_QUERY_HINTS["hv_softstart"],
    "autotransformer_starter": PRODUCT_LINE_QUERY_HINTS["autotransformer"],
    "liquid_resistor_starter": PRODUCT_LINE_QUERY_HINTS["liquid_resistor"],
    "pm_motor_vfd_retrofit": PRODUCT_LINE_QUERY_HINTS["pm_vfd_retrofit"],
    "hv_vfd_candidate": PRODUCT_LINE_QUERY_HINTS["hv_vfd"],
    "hv_solid_state": PRODUCT_LINE_QUERY_HINTS["hv_softstart"],
    "hv_vfd_multilevel_candidate": PRODUCT_LINE_QUERY_HINTS["hv_vfd"],
    "lci": PRODUCT_LINE_QUERY_HINTS["lci"],
    "hv_vfd": PRODUCT_LINE_QUERY_HINTS["hv_vfd"],
    "hv_softstart": PRODUCT_LINE_QUERY_HINTS["hv_softstart"],
    "ggq": PRODUCT_LINE_QUERY_HINTS["ggq"],
    "autotransformer": PRODUCT_LINE_QUERY_HINTS["autotransformer"],
    "liquid_resistor": PRODUCT_LINE_QUERY_HINTS["liquid_resistor"],
    "sdq": PRODUCT_LINE_QUERY_HINTS["sdq"],
    "pm_vfd_retrofit": PRODUCT_LINE_QUERY_HINTS["pm_vfd_retrofit"],
}


def dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for raw_value in values:
        value = str(raw_value or "").strip()
        if not value or value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def normalize_string_list(values: Any) -> list[str]:
    if isinstance(values, (list, tuple, set)):
        return dedupe_keep_order([str(item) for item in values if str(item or "").strip()])
    normalized = str(values or "").strip()
    return [normalized] if normalized else []


def expand_signal_terms(values: list[str]) -> list[str]:
    expanded: list[str] = []
    for value in values:
        normalized = str(value or "").strip()
        if not normalized:
            continue
        expanded.append(normalized)
        expanded.extend(ENTRY_SIGNAL_HINTS.get(normalized.casefold(), ()))
    return dedupe_keep_order(expanded)

