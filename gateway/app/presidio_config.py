from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache


SUPPORTED_ENTITY_TYPES = (
    "PERSON",
    "COMPANY",
    "AMOUNT",
    "ID_CARD",
    "PHONE",
    "BANK_CARD",
    "LOCATION",
    "PROJECT_CODE",
)

ENTITY_TYPE_ALIASES = {
    "CN_ID_CARD": "ID_CARD",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "BANK_CARD",
}

PLACEHOLDER_LABELS = {
    "PERSON": "Person",
    "COMPANY": "Company",
    "AMOUNT": "Amount",
    "ID_CARD": "ID_Card",
    "PHONE": "Phone",
    "BANK_CARD": "BankCard",
    "LOCATION": "Address",
    "PROJECT_CODE": "Project",
}

PLACEHOLDER_TO_ENTITY = {value: key for key, value in PLACEHOLDER_LABELS.items()}

COMPANY_SUFFIXES = (
    "股份有限公司",
    "有限责任公司",
    "有限公司",
    "集团公司",
    "集团",
    "公司",
    "研究院",
    "银行",
    "医院",
    "大学",
)

COMPANY_STOP_MARKERS = (
    "法定代表人",
    "项目经理",
    "联系人",
    "负责人",
    "收件人",
    "姓名",
    "确认",
    "追加",
    "根据",
    "分析",
    "签订",
    "合同",
    "甲方",
    "乙方",
    "客户",
    "供应商",
    "合作方",
)

COMPANY_LEADING_MARKERS = (
    "本合同由",
    "项目由",
    "请联系",
    "请联络",
    "联系人",
    "联系",
    "甲方",
    "乙方",
    "客户",
    "供应商",
    "合作方",
    "根据",
    "由",
)

ENTITY_PRIORITY = {
    "ID_CARD": 0,
    "PHONE": 1,
    "BANK_CARD": 2,
    "AMOUNT": 3,
    "PROJECT_CODE": 4,
    "COMPANY": 5,
    "LOCATION": 6,
    "PERSON": 7,
}

ENTITY_REGEX_PATTERNS: dict[str, tuple[tuple[re.Pattern[str], int | None], ...]] = {
    "ID_CARD": (
        (re.compile(r"(?<![0-9A-Za-z])\d{17}[0-9Xx](?![0-9A-Za-z])"), None),
    ),
    "PHONE": (
        (re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)"), None),
    ),
    "BANK_CARD": (
        (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), None),
    ),
    "AMOUNT": (
        (re.compile(r"(?<!\d)(?:人民币)?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?(?:亿元|万元|万|亿|元)"), None),
    ),
    "COMPANY": (),
    "PERSON": (
        (re.compile(r"(?:联系人|姓名|负责人|签约人|代表人|项目经理|收件人|法人|法定代表人)[：:\s]{0,3}([\u4e00-\u9fff]{2,4})(?=[，。；;、\s]|$|确认|电话|联系电话|负责|签订|与|和)"), 1),
    ),
    "LOCATION": (
        (re.compile(r"(?:地址|办公地址|联系地址)[：:\s]{0,3}([\u4e00-\u9fffA-Za-z0-9号弄室楼栋单元区市省路街道镇乡村]{6,80})"), 1),
    ),
    "PROJECT_CODE": (
        (re.compile(r"(?:项目代号|项目编号|项目编码)[：:\s]{0,3}([A-Za-z][A-Za-z0-9_-]{2,24})"), 1),
    ),
}

PLACEHOLDER_PATTERN = re.compile(
    r"\[(?:Person|Company|Amount|ID_Card|Phone|BankCard|Address|Project)_[A-Za-z0-9]+\]"
)


@dataclass(frozen=True, slots=True)
class PresidioSdk:
    AnalyzerEngine: type
    LocalRecognizer: type
    NlpArtifacts: type
    NlpEngine: type
    RecognizerResult: type
    RecognizerRegistry: type


def normalize_entity_type(value: str) -> str:
    normalized = value.strip().upper()
    normalized = ENTITY_TYPE_ALIASES.get(normalized, normalized)
    if normalized not in SUPPORTED_ENTITY_TYPES:
        raise ValueError(f"Unsupported entity type: {value}")
    return normalized


def normalize_entity_types(values: list[str] | tuple[str, ...] | None) -> tuple[str, ...]:
    if not values:
        return SUPPORTED_ENTITY_TYPES

    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        entity_type = normalize_entity_type(value)
        if entity_type in seen:
            continue
        normalized.append(entity_type)
        seen.add(entity_type)
    return tuple(normalized)


@lru_cache
def get_presidio_sdk() -> PresidioSdk | None:
    try:
        from presidio_analyzer import AnalyzerEngine, LocalRecognizer, RecognizerResult
        from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngine
        from presidio_analyzer.recognizer_registry import RecognizerRegistry
    except Exception:
        return None

    try:
        return PresidioSdk(
            AnalyzerEngine=AnalyzerEngine,
            LocalRecognizer=LocalRecognizer,
            NlpArtifacts=NlpArtifacts,
            NlpEngine=NlpEngine,
            RecognizerResult=RecognizerResult,
            RecognizerRegistry=RecognizerRegistry,
        )
    except Exception:
        return None
