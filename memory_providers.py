"""CE's local-file constructors for Mem0's dense and BM25 embeddings.

Imported lazily by Mem0's embedder factory after Memory telemetry is disabled.
"""

from fastembed import SparseTextEmbedding, TextEmbedding
from mem0.embeddings.base import EmbeddingBase
from mem0.embeddings.fastembed import FastEmbedEmbedding
from mem0.vector_stores.qdrant import Qdrant
from qdrant_client.local.qdrant_local import QdrantLocal

from memory_service import BM25_ARTIFACTS_PATH, _log


class LocalFastEmbedEmbedding(FastEmbedEmbedding):
    def __init__(self, config):
        # Keep Mem0's embed/embed_batch behavior, bypassing its downloading constructor.
        EmbeddingBase.__init__(self, config)
        model_path = self.config.model_kwargs["specific_model_path"]
        self.dense_model = TextEmbedding(
            model_name=self.config.model,
            specific_model_path=model_path,
            cache_dir=model_path,
            providers=["CPUExecutionProvider"],
            cuda=False,
            local_files_only=True,
        )


class LocalQdrant(Qdrant):
    def _create_filter_indexes(self):
        # Mem0 marks supplied clients as remote, including CE's embedded client.
        # Local Qdrant filters work without these unsupported payload indexes.
        if isinstance(self.client._client, QdrantLocal):
            return
        return super()._create_filter_indexes()

    def _get_bm25_encoder(self):
        # Preserve Mem0's lazy sparse encoder and failure fallback, using only local files.
        if self._bm25_encoder is None:
            try:
                self._bm25_encoder = SparseTextEmbedding(
                    model_name="Qdrant/bm25",
                    specific_model_path=str(BM25_ARTIFACTS_PATH),
                    cache_dir=str(BM25_ARTIFACTS_PATH),
                    local_files_only=True,
                )
            except Exception as exc:
                _log(f"Local BM25 initialization failed ({type(exc).__name__}); keyword search unavailable.", "WARN")
                self._bm25_encoder = False
        return self._bm25_encoder if self._bm25_encoder is not False else None
