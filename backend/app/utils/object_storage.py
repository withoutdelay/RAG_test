from __future__ import annotations

import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

try:
    from minio import Minio
except Exception:  # pragma: no cover - optional runtime dependency
    Minio = None

try:
    from app.config import get_settings
except Exception:  # pragma: no cover - optional during lightweight test runs
    get_settings = None


@dataclass
class MaterializedObject:
    path: Path
    temporary: bool

    def cleanup(self) -> None:
        if self.temporary:
            self.path.unlink(missing_ok=True)


class LocalObjectStorage:
    def __init__(self, root: str = "data/uploads") -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, source_path: Path, *, prefix: str = "") -> str:
        extension = source_path.suffix
        object_name = f"{prefix}{uuid4()}{extension}"
        destination = self.root / object_name
        shutil.copy2(source_path, destination)
        return str(destination)

    def save_bytes(self, content: bytes, *, suffix: str = ".bin", prefix: str = "") -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        try:
            return self.save(temp_path, prefix=prefix)
        finally:
            temp_path.unlink(missing_ok=True)

    def materialize(self, storage_path: str) -> MaterializedObject:
        return MaterializedObject(path=Path(storage_path), temporary=False)

    def delete(self, storage_path: str) -> None:
        Path(storage_path).unlink(missing_ok=True)


class MinioObjectStorage:
    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        secure: bool = False,
    ) -> None:
        if Minio is None:
            raise RuntimeError("minio package is not installed")
        self.bucket = bucket
        self.client = Minio(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
        )
        self._ensure_bucket()

    def save(self, source_path: Path, *, prefix: str = "") -> str:
        extension = source_path.suffix
        object_name = f"{prefix}{uuid4()}{extension}"
        self.client.fput_object(self.bucket, object_name, str(source_path))
        return f"minio://{self.bucket}/{object_name}"

    def save_bytes(self, content: bytes, *, suffix: str = ".bin", prefix: str = "") -> str:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(content)
            temp_path = Path(handle.name)
        try:
            return self.save(temp_path, prefix=prefix)
        finally:
            temp_path.unlink(missing_ok=True)

    def materialize(self, storage_path: str) -> MaterializedObject:
        object_name = self._parse_object_name(storage_path)
        suffix = Path(object_name).suffix
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        handle.close()
        self.client.fget_object(self.bucket, object_name, handle.name)
        return MaterializedObject(path=Path(handle.name), temporary=True)

    def delete(self, storage_path: str) -> None:
        object_name = self._parse_object_name(storage_path)
        self.client.remove_object(self.bucket, object_name)

    def _ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)

    def _parse_object_name(self, storage_path: str) -> str:
        prefix = f"minio://{self.bucket}/"
        if not storage_path.startswith(prefix):
            raise ValueError(f"Unsupported MinIO path: {storage_path}")
        return storage_path[len(prefix) :]


def get_object_storage() -> LocalObjectStorage | MinioObjectStorage:
    if get_settings is not None:
        settings = get_settings()
        endpoint = settings.minio_endpoint
        access_key = settings.minio_access_key
        secret_key = settings.minio_secret_key
        bucket = settings.minio_bucket
    else:
        endpoint = os.getenv("MINIO_ENDPOINT", "localhost:9000")
        access_key = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        secret_key = os.getenv("MINIO_SECRET_KEY", "minioadmin")
        bucket = os.getenv("MINIO_BUCKET", "presale-documents")

    if Minio is not None:
        try:
            return MinioObjectStorage(
                endpoint=endpoint,
                access_key=access_key,
                secret_key=secret_key,
                bucket=bucket,
                secure=False,
            )
        except Exception:
            pass

    return LocalObjectStorage()
