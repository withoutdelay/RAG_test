import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.utils.object_storage import LocalObjectStorage, MinioObjectStorage, get_object_storage


class FakeMinioClient:
    def __init__(self, *args, **kwargs) -> None:
        self.objects: dict[str, bytes] = {}
        self.bucket_created = False

    def bucket_exists(self, bucket: str) -> bool:
        return self.bucket_created

    def make_bucket(self, bucket: str) -> None:
        self.bucket_created = True

    def fput_object(self, bucket: str, object_name: str, file_path: str) -> None:
        self.objects[object_name] = Path(file_path).read_bytes()

    def fget_object(self, bucket: str, object_name: str, file_path: str) -> None:
        Path(file_path).write_bytes(self.objects[object_name])

    def remove_object(self, bucket: str, object_name: str) -> None:
        self.objects.pop(object_name, None)


class StorageTests(unittest.TestCase):
    def test_local_storage_save_materialize_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "uploads"
            source = Path(temp_dir) / "example.txt"
            source.write_text("hello", encoding="utf-8")

            storage = LocalObjectStorage(str(root))
            storage_path = storage.save(source, prefix="demo_")

            self.assertTrue(Path(storage_path).exists())
            materialized = storage.materialize(storage_path)
            self.assertEqual(materialized.path.read_text(encoding="utf-8"), "hello")
            self.assertFalse(materialized.temporary)

            storage.delete(storage_path)
            self.assertFalse(Path(storage_path).exists())

    def test_minio_storage_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "example.md"
            source.write_text("# demo", encoding="utf-8")

            with patch("app.utils.object_storage.Minio", FakeMinioClient):
                storage = MinioObjectStorage(
                    endpoint="localhost:9000",
                    access_key="minioadmin",
                    secret_key="minioadmin",
                    bucket="presale-documents",
                )
                storage_path = storage.save(source, prefix="demo_")
                self.assertTrue(storage_path.startswith("minio://presale-documents/"))

                materialized = storage.materialize(storage_path)
                self.assertTrue(materialized.temporary)
                self.assertEqual(materialized.path.read_text(encoding="utf-8"), "# demo")
                materialized.cleanup()
                self.assertFalse(materialized.path.exists())

                storage.delete(storage_path)

    def test_get_object_storage_falls_back_to_local(self) -> None:
        with patch("app.utils.object_storage.Minio", None):
            storage = get_object_storage()
        self.assertIsInstance(storage, LocalObjectStorage)


if __name__ == "__main__":
    unittest.main()
