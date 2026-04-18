from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.evidence_bundle import EvidenceBundle
from app.models.proposal_outline import ProposalOutline
from app.services.v2_errors import ArtifactNotFoundError, ArtifactValidationError


def _bundle_order_key(bundle: EvidenceBundle | None) -> tuple[int, datetime]:
    if bundle is None:
        return (-1, datetime.min.replace(tzinfo=timezone.utc))
    created_at = bundle.created_at
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return (int(bundle.retrieval_version or 0), created_at)


async def resolve_outline_evidence_bundle(
    *,
    session: AsyncSession,
    outline: ProposalOutline,
) -> EvidenceBundle:
    bound_bundle: EvidenceBundle | None = None
    if outline.evidence_bundle_id is not None:
        bound_bundle = await session.get(EvidenceBundle, outline.evidence_bundle_id)
        if bound_bundle and bound_bundle.project_id != outline.project_id:
            raise ArtifactNotFoundError("Evidence bundle not found")

    result = await session.scalars(
        select(EvidenceBundle)
        .where(EvidenceBundle.project_id == outline.project_id)
        .order_by(EvidenceBundle.retrieval_version.desc(), EvidenceBundle.created_at.desc())
        .limit(1)
    )
    latest_bundle = result.first()

    if latest_bundle and _bundle_order_key(latest_bundle) > _bundle_order_key(bound_bundle):
        outline.evidence_bundle_id = latest_bundle.id
        return latest_bundle
    if bound_bundle:
        return bound_bundle
    if latest_bundle:
        outline.evidence_bundle_id = latest_bundle.id
        return latest_bundle
    if outline.evidence_bundle_id is None:
        raise ArtifactValidationError("Outline is not bound to an evidence bundle")
    raise ArtifactNotFoundError("Evidence bundle not found")
