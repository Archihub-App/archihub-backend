"""Qdrant vector database access.

Points are written as ``PointStruct``s carrying both the vector and its payload,
searches go through ``query_points`` with a real ``SearchParams``, and
``VECTOR_SIZE`` is an ``int`` setting - ``VectorParams(size=...)`` requires one.

The embedding model is loaded eagerly by the caller at application startup
rather than as a side effect of first instantiation - see ``get_vectors()``.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from qdrant_client import QdrantClient, models

from archihub.core.settings import get_settings

logger = logging.getLogger(__name__)

METADATA_RESOURCES = "metadata_resources"
TRANSCRIPT_RECORDS = "transcript_records"

COLLECTIONS = (METADATA_RESOURCES, TRANSCRIPT_RECORDS)


class VectorClient:
    """Synchronous Qdrant client plus the sentence-transformers embedder."""

    def __init__(self, *, load_model: bool = True) -> None:
        settings = get_settings()
        self.vector_size = settings.vector_size
        self.qdrant = QdrantClient(host=settings.vector_host, port=settings.vector_port)
        self._embedding_model: Any | None = None
        if load_model:
            self.load_embedding_model()

    # -- model ----------------------------------------------------------
    def load_embedding_model(self) -> Any:
        """Load the sentence-transformers model (heavyweight, seconds to minutes).

        Imported lazily so that merely importing this module does not pull in
        torch/transformers - which matters for the CLI tools and tests.
        """
        if self._embedding_model is None:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model jinaai/jina-embeddings-v2-base-es")
            self._embedding_model = SentenceTransformer(
                "jinaai/jina-embeddings-v2-base-es", trust_remote_code=True
            )
        return self._embedding_model

    @property
    def embedding_model(self) -> Any:
        return self.load_embedding_model()

    def encode(self, text: str) -> list[float]:
        """Encode a single string into one embedding vector."""
        return self.embedding_model.encode(text).tolist()

    # -- schema ---------------------------------------------------------
    def ensure_collections(self) -> None:
        """Create the two fixed collections and the ``id`` payload index."""
        for collection in COLLECTIONS:
            if not self.qdrant.collection_exists(collection):
                logger.info("Creating Qdrant collection %s", collection)
                self.qdrant.create_collection(
                    collection,
                    vectors_config=models.VectorParams(
                        size=self.vector_size,
                        distance=models.Distance.COSINE,
                    ),
                )

        collection_info = self.qdrant.get_collection(METADATA_RESOURCES)
        if "id" not in (collection_info.payload_schema or {}):
            self.qdrant.create_payload_index(
                collection_name=METADATA_RESOURCES,
                field_name="id",
                field_schema=models.PayloadSchemaType.KEYWORD,
            )

    # -- connectivity ---------------------------------------------------
    def ping(self) -> None:
        """Raise if Qdrant is unreachable. Used by /health/ready."""
        self.qdrant.get_collections()

    # -- data -----------------------------------------------------------
    def insert_vector(
        self, collection: str, text: str, payload: dict, point_id: str | None = None
    ) -> None:
        """Upsert one embedded point.

        Builds a ``PointStruct`` carrying both the vector and its payload.
        """
        self.qdrant.upsert(
            collection_name=collection,
            points=[
                models.PointStruct(
                    id=point_id or str(uuid.uuid4()),
                    vector=self.encode(text),
                    payload=payload,
                )
            ],
        )

    def search_vector(self, collection: str, text: str, limit: int = 5):
        """Nearest-neighbour search.

        Uses ``query_points`` with a real ``SearchParams``.
        """
        response = self.qdrant.query_points(
            collection_name=collection,
            query=self.encode(text),
            limit=limit,
            search_params=models.SearchParams(hnsw_ef=128, exact=False, indexed_only=True),
        )
        return response.points


_vectors: VectorClient | None = None


def get_vectors(*, load_model: bool = True) -> VectorClient:
    """Return the process-wide Qdrant client.

    ``load_model=False`` gives a client that can talk to Qdrant without paying
    for the embedding model - used by ``/health/ready``, which only needs to
    know whether the service answers.
    """
    global _vectors
    if _vectors is None:
        _vectors = VectorClient(load_model=load_model)
    elif load_model:
        _vectors.load_embedding_model()
    return _vectors


def reset_vectors() -> None:
    """Drop the cached client (tests)."""
    global _vectors
    _vectors = None
