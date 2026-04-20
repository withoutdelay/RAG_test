from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.vectorstore.qdrant_client import QdrantService, _CLIENT_CACHE


class QdrantClientInitTests(unittest.TestCase):
    def setUp(self) -> None:
        _CLIENT_CACHE.clear()

    def test_local_directory_uses_path_mode(self) -> None:
        settings = SimpleNamespace(
            qdrant_collection="presale_knowledge",
            embedding_dimension=16,
            qdrant_location="/tmp/qdrant-local",
            qdrant_host="localhost",
            qdrant_port=6333,
        )

        with patch("app.services.vectorstore.qdrant_client.get_settings", return_value=settings):
            with patch("app.services.vectorstore.qdrant_client.QdrantClient") as mock_client:
                QdrantService()

        mock_client.assert_called_once_with(path="/tmp/qdrant-local")

    def test_memory_location_uses_memory_mode(self) -> None:
        settings = SimpleNamespace(
            qdrant_collection="presale_knowledge",
            embedding_dimension=16,
            qdrant_location=":memory:",
            qdrant_host="localhost",
            qdrant_port=6333,
        )

        with patch("app.services.vectorstore.qdrant_client.get_settings", return_value=settings):
            with patch("app.services.vectorstore.qdrant_client.QdrantClient") as mock_client:
                QdrantService()

        mock_client.assert_called_once_with(location=":memory:")


if __name__ == "__main__":
    unittest.main()
