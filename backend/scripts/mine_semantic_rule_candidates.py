from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.services.composition.semantic_rules import get_semantic_rules


DEFAULT_SOURCES = [
    Path("backend/data/uploads"),
    Path("backend/data/knowledge_wiki/library_refresh_cache"),
    Path("backend/data/case_library/block_library.json"),
]
DEFAULT_OUTPUT = Path("output/semantic-rule-candidates.json")

STOP_TERMS = {
    "本项目",
    "本章节",
    "方案",
    "系统",
    "技术",
    "要求",
    "说明",
    "项目",
    "设备",
    "公司",
    "用户",
    "客户",
    "如下",
    "以及",
    "进行",
    "提供",
    "满足",
}

GROUP_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("installation.focus", ("安装", "布置", "基础", "通风", "散热", "吊装", "进出线", "维护通道", "电缆沟")),
    ("spare_parts.focus", ("备品", "备件", "易损", "spare", "随机备件")),
    ("technical_reuse.hard_noise", ("培训", "售后", "维保", "巡检", "质保", "进度", "认证", "证书", "检测报告")),
    ("overall_solution.focus", ("总体方案", "系统方案", "主回路", "主接线", "一次接线", "单线图", "拓扑", "系统构成")),
    ("parameter_summary.focus", ("技术参数", "技术数据", "额定", "电压", "电流", "功率", "频率", "短路容量")),
    ("control_logic.focus", ("控制", "联锁", "保护", "切换", "同期", "同步", "顺控")),
    ("supply_scope.focus", ("供货", "清单", "随机资料", "专用工具", "接口分工")),
)


def _iter_source_files(sources: list[Path]) -> list[Path]:
    files: list[Path] = []
    for source in sources:
        if source.is_dir():
            files.extend(
                path
                for path in source.rglob("*")
                if path.suffix.lower() in {".json", ".md", ".txt"}
            )
        elif source.is_file():
            files.append(source)
    return sorted(files)


def _collect_json_strings(value: Any) -> list[str]:
    strings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {
                "content_md",
                "contextual_text",
                "semantic_retrieval_text",
                "section_path",
                "source_heading",
                "heading_path",
                "title",
                "display_title",
                "summary",
            }:
                strings.extend(_collect_json_strings(item))
            elif isinstance(item, (dict, list)):
                strings.extend(_collect_json_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.extend(_collect_json_strings(item))
    elif isinstance(value, str):
        strings.append(value)
    return strings


def _read_text_fragments(path: Path) -> list[str]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return [raw]
        return _collect_json_strings(payload)
    return [raw]


def _candidate_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for line in str(text or "").splitlines():
        stripped = re.sub(r"^\s*#{1,6}\s*", "", line).strip()
        stripped = re.sub(r"\[\[ASSET:[^\]]+\]\]", " ", stripped)
        for match in re.finditer(r"[\u4e00-\u9fffA-Za-z0-9+/\- ]{2,40}", stripped):
            phrase = re.sub(r"\s+", " ", match.group(0)).strip(" .。:：,，;；|")
            if not phrase:
                continue
            if len(phrase) < 2 or len(phrase) > 28:
                continue
            if phrase in STOP_TERMS:
                continue
            if re.fullmatch(r"[0-9+\-/ .]+", phrase):
                continue
            terms.add(phrase)
    return terms


def _classify_group(term: str) -> str | None:
    lowered = term.casefold()
    compact = re.sub(r"\s+", "", lowered)
    for group, hints in GROUP_HINTS:
        if any(hint.casefold().replace(" ", "") in compact for hint in hints):
            return group
    return None


def _existing_tokens() -> set[str]:
    rules = get_semantic_rules()
    tokens: set[str] = set()
    for group in rules.token_groups.values():
        for token in group:
            tokens.add(token.casefold())
            tokens.add(token.casefold().replace(" ", ""))
    return tokens


def mine_candidates(sources: list[Path], *, min_count: int, limit_per_group: int) -> dict[str, Any]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    examples: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    existing = _existing_tokens()
    source_files = _iter_source_files(sources)
    for path in source_files:
        for fragment in _read_text_fragments(path):
            for term in _candidate_terms(fragment):
                normalized = term.casefold().replace(" ", "")
                if normalized in existing:
                    continue
                group = _classify_group(term)
                if not group:
                    continue
                counts[group][term] += 1
                if len(examples[group][term]) < 3:
                    examples[group][term].add(str(path))

    candidates: dict[str, list[dict[str, Any]]] = {}
    for group, counter in sorted(counts.items()):
        rows: list[dict[str, Any]] = []
        for term, count in counter.most_common():
            if count < min_count:
                continue
            rows.append(
                {
                    "token": term,
                    "count": count,
                    "example_files": sorted(examples[group][term]),
                    "review_status": "candidate",
                    "recommended_action": "manual_review_then_regression",
                }
            )
            if len(rows) >= limit_per_group:
                break
        if rows:
            candidates[group] = rows

    return {
        "schema_version": 1,
        "source_files_scanned": len(source_files),
        "min_count": min_count,
        "limit_per_group": limit_per_group,
        "policy": "候选词只进入审计文件，不自动写入线上 semantic_rules.json。",
        "candidates": candidates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Mine semantic rule candidates from processed proposal text.")
    parser.add_argument("--source", action="append", default=[], help="File or directory to scan. Can be repeated.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path.")
    parser.add_argument("--min-count", type=int, default=2)
    parser.add_argument("--limit-per-group", type=int, default=40)
    args = parser.parse_args()

    sources = [Path(item) for item in args.source] if args.source else DEFAULT_SOURCES
    result = mine_candidates(sources, min_count=max(1, args.min_count), limit_per_group=max(1, args.limit_per_group))
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {output_path} with {sum(len(items) for items in result['candidates'].values())} candidates")


if __name__ == "__main__":
    main()
