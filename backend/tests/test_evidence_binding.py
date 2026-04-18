from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.services.evidence_binding import resolve_outline_evidence_bundle
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def first(self):
        return self._value


class _FakeSession:
    def __init__(self, *, objects=None, latest_bundle=None) -> None:
        self.objects = objects or {}
        self.latest_bundle = latest_bundle

    async def get(self, _model, object_id):
        return self.objects.get(object_id)

    async def scalars(self, _statement):
        return _ScalarResult(self.latest_bundle)


class EvidenceBindingTests(unittest.TestCase):
    def test_resolve_outline_evidence_bundle_updates_to_latest_bundle(self) -> None:
        outline = SimpleNamespace(project_id=uuid4(), evidence_bundle_id=uuid4())
        bound_bundle = SimpleNamespace(
            id=outline.evidence_bundle_id,
            project_id=outline.project_id,
            retrieval_version=1,
            created_at=datetime(2026, 4, 17, 9, 0, tzinfo=timezone.utc),
        )
        latest_bundle = SimpleNamespace(
            id=uuid4(),
            project_id=outline.project_id,
            retrieval_version=2,
            created_at=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        )
        session = _FakeSession(objects={bound_bundle.id: bound_bundle}, latest_bundle=latest_bundle)

        resolved = asyncio.run(resolve_outline_evidence_bundle(session=session, outline=outline))

        self.assertIs(resolved, latest_bundle)
        self.assertEqual(outline.evidence_bundle_id, latest_bundle.id)

    def test_resolve_outline_evidence_bundle_keeps_current_when_already_latest(self) -> None:
        bundle = SimpleNamespace(
            id=uuid4(),
            project_id=uuid4(),
            retrieval_version=2,
            created_at=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        )
        outline = SimpleNamespace(project_id=bundle.project_id, evidence_bundle_id=bundle.id)
        session = _FakeSession(objects={bundle.id: bundle}, latest_bundle=bundle)

        resolved = asyncio.run(resolve_outline_evidence_bundle(session=session, outline=outline))

        self.assertIs(resolved, bundle)
        self.assertEqual(outline.evidence_bundle_id, bundle.id)

    def test_resolve_outline_evidence_bundle_binds_latest_when_outline_has_no_binding(self) -> None:
        latest_bundle = SimpleNamespace(
            id=uuid4(),
            project_id=uuid4(),
            retrieval_version=3,
            created_at=datetime(2026, 4, 17, 11, 0, tzinfo=timezone.utc),
        )
        outline = SimpleNamespace(project_id=latest_bundle.project_id, evidence_bundle_id=None)
        session = _FakeSession(latest_bundle=latest_bundle)

        resolved = asyncio.run(resolve_outline_evidence_bundle(session=session, outline=outline))

        self.assertIs(resolved, latest_bundle)
        self.assertEqual(outline.evidence_bundle_id, latest_bundle.id)

    def test_resolve_outline_evidence_bundle_raises_when_binding_is_missing_and_no_latest_exists(self) -> None:
        outline = SimpleNamespace(project_id=uuid4(), evidence_bundle_id=None)
        session = _FakeSession(latest_bundle=None)

        with self.assertRaises(ArtifactValidationError):
            asyncio.run(resolve_outline_evidence_bundle(session=session, outline=outline))

    def test_resolve_outline_evidence_bundle_raises_when_bound_bundle_is_missing(self) -> None:
        outline = SimpleNamespace(project_id=uuid4(), evidence_bundle_id=uuid4())
        session = _FakeSession(latest_bundle=None)

        with self.assertRaises(ArtifactNotFoundError):
            asyncio.run(resolve_outline_evidence_bundle(session=session, outline=outline))


if __name__ == "__main__":
    unittest.main()
