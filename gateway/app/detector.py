from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from app.config import get_settings
from app.presidio_config import COMPANY_LEADING_MARKERS, COMPANY_STOP_MARKERS, COMPANY_SUFFIXES, ENTITY_PRIORITY, ENTITY_REGEX_PATTERNS, SUPPORTED_ENTITY_TYPES, get_presidio_sdk, normalize_entity_type, normalize_entity_types


@dataclass(frozen=True, slots=True)
class DetectedEntity:
    original: str
    entity_type: str
    start: int
    end: int
    score: float = 1.0


class _PresidioNoopNlpEngine:
    def __init__(
        self,
        *,
        sdk,
    ) -> None:
        nlp_engine_base = sdk.NlpEngine
        nlp_artifacts_cls = sdk.NlpArtifacts

        class NoopNlpEngine(nlp_engine_base):
            def __init__(self) -> None:
                self._loaded = True

            def load(self) -> None:
                self._loaded = True

            def is_loaded(self) -> bool:
                return self._loaded

            def process_text(self, text: str, language: str):
                lemmas = [token.lower() for token in text.split() if token.strip()]
                indices: list[int] = []
                if lemmas:
                    cursor = 0
                    for token in text.split():
                        position = text.find(token, cursor)
                        indices.append(position)
                        cursor = position + len(token)
                return nlp_artifacts_cls(
                    entities=[],
                    tokens=[],
                    tokens_indices=indices,
                    lemmas=lemmas,
                    nlp_engine=self,
                    language=language,
                    scores=[],
                )

            def process_batch(
                self,
                texts,
                language: str,
                batch_size: int = 1,
                n_process: int = 1,
                **kwargs,
            ):
                for text in texts:
                    yield text, self.process_text(text, language)

            def is_stopword(self, word: str, language: str) -> bool:
                return False

            def is_punct(self, word: str, language: str) -> bool:
                return bool(re.fullmatch(r"\W+", word))

            def get_supported_entities(self) -> list[str]:
                return []

            def get_supported_languages(self) -> list[str]:
                return ["zh"]

        self.engine = NoopNlpEngine()


class _PresidioRuleRecognizer:
    def __init__(
        self,
        *,
        sdk,
        entity_type: str,
        matcher: Callable[[str], list[DetectedEntity]],
    ) -> None:
        self._recognizer_result_cls = sdk.RecognizerResult
        local_recognizer_base = sdk.LocalRecognizer

        class RuleRecognizer(local_recognizer_base):
            def __init__(self, supported_entity: str, callback: Callable[[str], list[DetectedEntity]], result_cls) -> None:
                self._callback = callback
                self._result_cls = result_cls
                self._entity_type = supported_entity
                super().__init__(
                    supported_entities=[supported_entity],
                    name=f"{supported_entity}RuleRecognizer",
                    supported_language="zh",
                )

            def load(self) -> None:
                return None

            def analyze(self, text: str, entities: list[str], nlp_artifacts=None):
                if self._entity_type not in entities:
                    return []
                return [
                    self._result_cls(
                        entity_type=self._entity_type,
                        start=entity.start,
                        end=entity.end,
                        score=entity.score,
                    )
                    for entity in self._callback(text)
                ]

        self.recognizer = RuleRecognizer(entity_type, matcher, self._recognizer_result_cls)


class EntityDetector:
    def __init__(self, *, enable_presidio: bool | None = None) -> None:
        settings = get_settings()
        self._enable_presidio = settings.enable_presidio if enable_presidio is None else enable_presidio
        self._presidio_sdk = get_presidio_sdk() if self._enable_presidio else None
        self._analyzer = self._build_presidio_analyzer() if self._presidio_sdk else None

    def detect(self, text: str, entity_types: list[str] | tuple[str, ...] | None = None) -> list[DetectedEntity]:
        normalized_types = normalize_entity_types(entity_types)
        candidates = self._detect_with_presidio(text, normalized_types)
        if not candidates:
            candidates = self._detect_with_regex(text, normalized_types)
        return self._dedupe_and_resolve(candidates)

    def _detect_with_regex(self, text: str, entity_types: tuple[str, ...]) -> list[DetectedEntity]:
        matches: list[DetectedEntity] = []
        for entity_type in entity_types:
            matches.extend(self._detect_entity_type_with_rules(text, entity_type))
        return matches

    def _detect_entity_type_with_rules(self, text: str, entity_type: str) -> list[DetectedEntity]:
        if entity_type == "COMPANY":
            return self._detect_companies(text)

        matches: list[DetectedEntity] = []
        for pattern, group_index in ENTITY_REGEX_PATTERNS.get(entity_type, ()):
            for match in pattern.finditer(text):
                if group_index is None:
                    start, end = match.span()
                    original = match.group(0)
                else:
                    start, end = match.span(group_index)
                    original = match.group(group_index)
                original = original.strip()
                if not original:
                    continue
                matches.append(
                    DetectedEntity(
                        original=original,
                        entity_type=entity_type,
                        start=start,
                        end=end,
                    )
                )
        return matches

    def _detect_companies(self, text: str) -> list[DetectedEntity]:
        suffix_pattern = "|".join(sorted(COMPANY_SUFFIXES, key=len, reverse=True))
        matches: list[DetectedEntity] = []
        for suffix_match in re.finditer(suffix_pattern, text):
            end = suffix_match.end()
            start = suffix_match.start()
            while start > 0 and self._is_company_char(text[start - 1]):
                start -= 1

            raw_candidate = text[start:end].strip()
            candidate = self._trim_company_candidate(raw_candidate)
            if len(candidate) < len(suffix_match.group(0)):
                continue

            actual_start = end - len(candidate)
            matches.append(
                DetectedEntity(
                    original=candidate,
                    entity_type="COMPANY",
                    start=actual_start,
                    end=end,
                )
            )
        return matches

    def _detect_with_presidio(self, text: str, entity_types: tuple[str, ...]) -> list[DetectedEntity]:
        if self._presidio_sdk is None or self._analyzer is None:
            return []

        detected: list[DetectedEntity] = []
        try:
            results = self._analyzer.analyze(text=text, language="zh", entities=list(entity_types))
        except Exception:
            return []

        for item in results:
            try:
                entity_type = normalize_entity_type(item.entity_type)
            except ValueError:
                continue
            detected.append(
                DetectedEntity(
                    original=text[item.start : item.end],
                    entity_type=entity_type,
                    start=item.start,
                    end=item.end,
                    score=float(getattr(item, "score", 1.0)),
                )
            )
        return detected

    def _build_presidio_analyzer(self):
        if self._presidio_sdk is None:
            return None

        try:
            registry = self._presidio_sdk.RecognizerRegistry(supported_languages=["zh"])
            for entity_type in SUPPORTED_ENTITY_TYPES:
                recognizer = _PresidioRuleRecognizer(
                    sdk=self._presidio_sdk,
                    entity_type=entity_type,
                    matcher=lambda current_text, current_type=entity_type: self._detect_entity_type_with_rules(
                        current_text,
                        current_type,
                    ),
                ).recognizer
                registry.add_recognizer(recognizer)

            nlp_engine = _PresidioNoopNlpEngine(sdk=self._presidio_sdk).engine
            return self._presidio_sdk.AnalyzerEngine(
                registry=registry,
                nlp_engine=nlp_engine,
                supported_languages=["zh"],
            )
        except Exception:
            return None

    def _dedupe_and_resolve(self, entities: list[DetectedEntity]) -> list[DetectedEntity]:
        unique: dict[tuple[int, int, str, str], DetectedEntity] = {}
        for entity in entities:
            key = (entity.start, entity.end, entity.entity_type, entity.original)
            if key not in unique or entity.score > unique[key].score:
                unique[key] = entity

        ranked = sorted(
            unique.values(),
            key=lambda item: (
                ENTITY_PRIORITY.get(item.entity_type, 999),
                -(item.end - item.start),
                item.start,
            ),
        )

        accepted: list[DetectedEntity] = []
        for candidate in ranked:
            if any(self._overlaps(candidate, existing) for existing in accepted):
                continue
            accepted.append(candidate)

        return sorted(accepted, key=lambda item: item.start)

    @staticmethod
    def _overlaps(left: DetectedEntity, right: DetectedEntity) -> bool:
        return left.start < right.end and right.start < left.end

    @staticmethod
    def _trim_company_candidate(candidate: str) -> str:
        trimmed = EntityDetector._strip_company_prefixes(candidate.strip())
        while True:
            best_index = -1
            best_tail: str | None = None
            for marker in COMPANY_STOP_MARKERS:
                index = trimmed.rfind(marker)
                if index <= 0:
                    continue
                tail = trimmed[index + len(marker) :].strip()
                if not tail or not any(tail.endswith(suffix) for suffix in COMPANY_SUFFIXES):
                    continue
                if index > best_index:
                    best_index = index
                    best_tail = EntityDetector._strip_company_prefixes(tail)
            if best_tail is None:
                break
            trimmed = best_tail
        return trimmed

    @staticmethod
    def _is_company_char(value: str) -> bool:
        return value.isalnum() or value in {"·", "（", "）", "(", ")"}

    @staticmethod
    def _strip_company_prefixes(candidate: str) -> str:
        trimmed = candidate
        changed = True
        while changed:
            changed = False
            for marker in sorted(COMPANY_LEADING_MARKERS, key=len, reverse=True):
                if not trimmed.startswith(marker):
                    continue
                tail = trimmed[len(marker) :].strip()
                if tail and any(tail.endswith(suffix) for suffix in COMPANY_SUFFIXES):
                    trimmed = tail
                    changed = True
                    break
        return trimmed
