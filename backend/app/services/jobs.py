from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.job import Job
from app.services.v2_errors import ArtifactNotFoundError


class JobService:
    async def get_job(self, *, session: AsyncSession, job_id: UUID) -> Job:
        job = await session.get(Job, job_id)
        if not job:
            raise ArtifactNotFoundError("Job not found")
        return job
