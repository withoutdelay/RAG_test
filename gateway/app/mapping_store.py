from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from app.config import get_settings
from app.placeholders import PlaceholderRegistry

try:
    from redis.asyncio import Redis
except ImportError:  # pragma: no cover - optional dependency
    Redis = None


@dataclass(slots=True)
class _SessionState:
    mappings: dict[str, str] = field(default_factory=dict)
    expires_at: float = 0.0


class InMemoryMappingStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, _SessionState] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get_all(self, session_id: str) -> dict[str, str]:
        async with self._get_lock(session_id):
            self._prune_expired()
            session = self._sessions.get(session_id)
            if session is None:
                return {}
            session.expires_at = time.monotonic() + self.ttl_seconds
            return dict(session.mappings)

    async def allocate_placeholders(
        self,
        session_id: str,
        entity_pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], str]:
        if not entity_pairs:
            return {}

        async with self._get_lock(session_id):
            self._prune_expired()
            session = self._sessions.get(session_id)
            if session is None:
                session = _SessionState(expires_at=time.monotonic() + self.ttl_seconds)
                self._sessions[session_id] = session

            registry = PlaceholderRegistry(session.mappings)
            allocations, created_mappings = registry.allocate_many(entity_pairs)
            session.mappings.update(created_mappings)
            session.expires_at = time.monotonic() + self.ttl_seconds
            return allocations

    async def set_many(self, session_id: str, mappings: dict[str, str]) -> None:
        if not mappings:
            return
        async with self._get_lock(session_id):
            self._prune_expired()
            session = self._sessions.get(session_id)
            if session is None:
                session = _SessionState(expires_at=time.monotonic() + self.ttl_seconds)
                self._sessions[session_id] = session
            session.mappings.update(mappings)
            session.expires_at = time.monotonic() + self.ttl_seconds

    async def aclose(self) -> None:
        return None

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        return self._locks.setdefault(session_id, asyncio.Lock())

    def _prune_expired(self) -> None:
        now = time.monotonic()
        expired = [session_id for session_id, session in self._sessions.items() if session.expires_at <= now]
        for session_id in expired:
            self._sessions.pop(session_id, None)
            self._locks.pop(session_id, None)


class RedisMappingStore:
    def __init__(
        self,
        redis_url: str,
        ttl_seconds: int,
        *,
        lock_timeout_seconds: int,
        lock_blocking_timeout_seconds: int,
    ) -> None:
        if Redis is None:
            raise RuntimeError("redis package is not installed")
        self.ttl_seconds = ttl_seconds
        self.lock_timeout_seconds = lock_timeout_seconds
        self.lock_blocking_timeout_seconds = lock_blocking_timeout_seconds
        self._client = Redis.from_url(redis_url, decode_responses=True)

    async def get_all(self, session_id: str) -> dict[str, str]:
        key = self._key(session_id)
        values = await self._client.hgetall(key)
        if values:
            await self._client.expire(key, self.ttl_seconds)
        return dict(values)

    async def allocate_placeholders(
        self,
        session_id: str,
        entity_pairs: list[tuple[str, str]],
    ) -> dict[tuple[str, str], str]:
        if not entity_pairs:
            return {}

        async with self._session_lock(session_id):
            key = self._key(session_id)
            values = await self._client.hgetall(key)
            registry = PlaceholderRegistry(values)
            allocations, created_mappings = registry.allocate_many(entity_pairs)
            if created_mappings:
                await self._client.hset(key, mapping=created_mappings)
            await self._client.expire(key, self.ttl_seconds)
            return allocations

    async def set_many(self, session_id: str, mappings: dict[str, str]) -> None:
        if not mappings:
            return
        async with self._session_lock(session_id):
            key = self._key(session_id)
            await self._client.hset(key, mapping=mappings)
            await self._client.expire(key, self.ttl_seconds)

    async def aclose(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _key(session_id: str) -> str:
        return f"masking:session:{session_id}"

    @staticmethod
    def _lock_key(session_id: str) -> str:
        return f"masking:session:{session_id}:lock"

    @asynccontextmanager
    async def _session_lock(self, session_id: str):
        lock = self._client.lock(
            self._lock_key(session_id),
            timeout=self.lock_timeout_seconds,
            blocking_timeout=self.lock_blocking_timeout_seconds,
        )
        acquired = await lock.acquire()
        if not acquired:
            raise RuntimeError(f"Failed to acquire session lock for {session_id}")
        try:
            yield
        finally:
            try:
                await lock.release()
            except Exception:
                pass


_store = None


def get_mapping_store():
    global _store
    if _store is None:
        settings = get_settings()
        if settings.redis_enabled:
            _store = RedisMappingStore(
                settings.redis_url,
                settings.mapping_ttl_seconds,
                lock_timeout_seconds=settings.mapping_lock_timeout_seconds,
                lock_blocking_timeout_seconds=settings.mapping_lock_blocking_timeout_seconds,
            )
        else:
            _store = InMemoryMappingStore(settings.mapping_ttl_seconds)
    return _store


def reset_mapping_store_state() -> None:
    global _store
    _store = None
