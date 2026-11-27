"""Text embedding with a shared, lazily loaded multilingual encoder."""
from collections.abc import Sequence
from functools import lru_cache
import os

import numpy as np


def embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL") or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


@lru_cache(maxsize=2)
def _load_encoder(model_name: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name)


class SentenceTransformerEmbedder:
    """Embedder backed by sentence-transformers; `encoder` may be injected for tests."""

    def __init__(self, model_name: str | None = None, encoder=None):
        self.model_name = model_name or embedding_model_name()
        self._encoder = encoder

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        encoder = self._encoder if self._encoder is not None else _load_encoder(self.model_name)
        return np.asarray(encoder.encode(list(texts), normalize_embeddings=True), dtype=float)
