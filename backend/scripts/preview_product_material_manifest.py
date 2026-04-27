from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from app.db import get_session_factory
from app.services.catalog import ProductCatalogService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview a product materials manifest and render a markdown intake report.")
    parser.add_argument("--manifest", required=True, help="Path to the manifest JSON to preview.")
    parser.add_argument(
        "--source-kind",
        default=None,
        help="Optional source_kind override. When omitted, use the source_kind already present in the manifest entries.",
    )
    parser.add_argument(
        "--replace-existing",
        action="store_true",
        help="Preview the manifest as if existing rows with the same material_key would be replaced.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Path to write the markdown report. Defaults to output/<manifest-stem>-preview.md.",
    )
    return parser.parse_args()


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(cell.replace("\n", "<br>") for cell in row) + " |")
    return "\n".join(lines)


def bool_flag(value: bool | None) -> str:
    if value is None:
        return "-"
    return "Yes" if value else "No"


def render_preview_markdown(preview: dict[str, object]) -> str:
    issues = preview.get("issues") or []
    preview_entries = preview.get("preview_entries") or []
    family_counts = preview.get("family_counts") or {}
    material_type_counts = preview.get("material_type_counts") or {}
    availability_status_counts = preview.get("availability_status_counts") or {}
    source_kind_counts = preview.get("source_kind_counts") or {}
    gate_ready_family_material_counts = preview.get("gate_ready_family_material_counts") or {}

    issue_lines = [
        f"- `{issue['severity']}` `{issue['issue_type']}`: {issue['message']}"
        for issue in issues
    ] or ["- 无"]

    family_rows = [
        [str(family_code), str(count)]
        for family_code, count in sorted(family_counts.items(), key=lambda item: (item[0], item[1]))
    ] or [["无", "0"]]
    type_rows = [
        [str(material_type), str(count)]
        for material_type, count in sorted(material_type_counts.items(), key=lambda item: (item[0], item[1]))
    ] or [["无", "0"]]
    status_rows = [
        [str(status), str(count)]
        for status, count in sorted(availability_status_counts.items(), key=lambda item: (item[0], item[1]))
    ] or [["无", "0"]]
    source_rows = [
        [str(source_kind), str(count)]
        for source_kind, count in sorted(source_kind_counts.items(), key=lambda item: (item[0], item[1]))
    ] or [["无", "0"]]

    gate_rows: list[list[str]] = []
    for family_code, counts in sorted(gate_ready_family_material_counts.items(), key=lambda item: item[0]):
        metric_parts = [
            f"{metric}={value}"
            for metric, value in sorted(counts.items(), key=lambda item: item[0])
            if metric != "total"
        ]
        gate_rows.append([family_code, str(counts.get("total", 0)), "；".join(metric_parts) or "-"])
    if not gate_rows:
        gate_rows = [["无", "0", "-"]]

    entry_rows = [
        [
            str(entry["material_key"]),
            str(entry["document_name"]),
            str(entry["family_code"] or "unclassified"),
            str(entry["material_type"]),
            str(entry["availability_status"]),
            str(entry["source_kind"]),
            bool_flag(bool(entry["counted_toward_gate"])),
            bool_flag(bool(entry["existing_material"])),
            bool_flag(bool(entry["duplicate_material_key"])),
            f"family={bool_flag(bool(entry['explicit_family_code']))}; "
            f"type={bool_flag(bool(entry['explicit_material_type']))}; "
            f"status={bool_flag(bool(entry['explicit_availability_status']))}",
            "；".join(str(item) for item in (entry.get("issues") or [])) or "-",
        ]
        for entry in preview_entries
    ] or [["无", "-", "-", "-", "-", "-", "-", "-", "-", "-", "-"]]

    lines = [
        "# Product Material Manifest Preview",
        "",
        "## Summary",
        "",
        f"- Manifest: `{preview['manifest_path']}`",
        f"- Source kind: `{preview['source_kind']}`",
        f"- Replace existing: `{preview['replace_existing']}`",
        f"- Import blocked: `{preview['import_blocked']}`",
        f"- Total entries: `{preview['total_entry_count']}`",
        f"- Unique material keys: `{preview['unique_material_key_count']}`",
        f"- New materials: `{preview['new_material_count']}`",
        f"- Existing materials: `{preview['existing_material_count']}`",
        f"- Would import: `{preview['would_import_count']}`",
        f"- Would skip existing: `{preview['would_skip_existing_count']}`",
        f"- Would replace existing: `{preview['would_replace_existing_count']}`",
        f"- Gate-ready materials: `{preview['gate_ready_material_count']}`",
        f"- Non-synthetic materials: `{preview['non_synthetic_material_count']}`",
        "",
        "## Diagnostics",
        "",
        f"- Inferred family count: `{preview['inferred_family_count']}`",
        f"- Inferred material type count: `{preview['inferred_material_type_count']}`",
        f"- Inferred availability status count: `{preview['inferred_status_count']}`",
        f"- Missing source path count: `{preview['missing_source_path_count']}`",
        f"- Missing source file count: `{preview['missing_source_file_count']}`",
        f"- Duplicate material key count: `{preview['duplicate_material_key_count']}`",
        f"- Duplicate material keys: `{', '.join(preview.get('duplicate_material_keys') or []) or '-'}`",
        "",
        "## Issues",
        "",
        *issue_lines,
        "",
        "## Counts By Family",
        "",
        markdown_table(["Family", "Count"], family_rows),
        "",
        "## Counts By Material Type",
        "",
        markdown_table(["Material Type", "Count"], type_rows),
        "",
        "## Counts By Availability",
        "",
        markdown_table(["Availability", "Count"], status_rows),
        "",
        "## Counts By Source Kind",
        "",
        markdown_table(["Source Kind", "Count"], source_rows),
        "",
        "## Gate-Ready Coverage",
        "",
        markdown_table(["Family", "Total", "Breakdown"], gate_rows),
        "",
        "## Entry Preview",
        "",
        markdown_table(
            [
                "material_key",
                "document_name",
                "family_code",
                "material_type",
                "availability_status",
                "source_kind",
                "counted_toward_gate",
                "existing_material",
                "duplicate_material_key",
                "explicit_fields",
                "issues",
            ],
            entry_rows,
        ),
        "",
    ]
    return "\n".join(lines)


async def main() -> None:
    args = parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = Path(args.manifest).expanduser()
    output_path = (
        Path(args.output).expanduser()
        if args.output
        else repo_root / "output" / f"{manifest_path.stem}-preview.md"
    )

    service = ProductCatalogService()
    async with get_session_factory()() as session:
        preview = await service.preview_material_manifest(
            session=session,
            manifest_path=str(manifest_path),
            replace_existing=args.replace_existing,
            source_kind=args.source_kind,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(render_preview_markdown(preview), encoding="utf-8")

    print(f"manifest={preview['manifest_path']}")
    print(f"output={output_path}")
    print(f"import_blocked={preview['import_blocked']}")
    print(f"would_import_count={preview['would_import_count']}")
    print(f"gate_ready_material_count={preview['gate_ready_material_count']}")


if __name__ == "__main__":
    asyncio.run(main())
