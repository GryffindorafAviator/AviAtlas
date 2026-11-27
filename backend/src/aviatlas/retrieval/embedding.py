"""Text embedding with a shared, lazily loaded multilingual encoder."""
from collections.abc import Sequence
from functools import lru_cache
from threading import Lock
import os
import time

import numpy as np


def embedding_model_name() -> str:
    return os.getenv("EMBEDDING_MODEL") or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


# One call into the shared model at a time (it is not thread-safe), and load it once.
_MODEL_LOCK = Lock()
BATCH_SIZE = 64


def embedding_device() -> str:
    """CPU by default. Docling's layout and table models use the GPU, and two models using
    an Apple GPU from different threads at once abort the process. Queries cost ~20 ms on CPU."""
    return os.getenv("EMBEDDING_DEVICE") or "cpu"


@lru_cache(maxsize=2)
def _load_encoder(model_name: str):
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name, device=embedding_device())


class SentenceTransformerEmbedder:
    """Embedder backed by sentence-transformers; `encoder` may be injected for tests."""

    def __init__(self, model_name: str | None = None, encoder=None):
        self.model_name = model_name or embedding_model_name()
        self._encoder = encoder

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        texts = list(texts)
        if self._encoder is not None:
            encoder = self._encoder
        else:
            with _MODEL_LOCK:
                encoder = _load_encoder(self.model_name)
        # Batches release the lock in between, so a query embedding waits for one
        # batch, not for a whole document being indexed.
        rows = []
        for start in range(0, len(texts), BATCH_SIZE):
            if start:
                # Python locks do not hand off to waiters; without a pause this loop takes
                # the lock straight back and a query waits for the whole document.
                time.sleep(0.005)
            with _MODEL_LOCK:
                rows.append(np.asarray(encoder.encode(texts[start:start + BATCH_SIZE], normalize_embeddings=True), dtype=float))
        return np.vstack(rows) if rows else np.zeros((0, 0))
