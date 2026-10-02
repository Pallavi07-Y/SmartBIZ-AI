import hashlib
import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except Exception:  # pragma: no cover - fallback when ML stack is unavailable
    SentenceTransformer = None

from app.config import settings


class EmbeddingModel:

    def __init__(self):
        self.model = None
        self.dimension = 384

        if SentenceTransformer is None:
            return

        try:
            self.model = SentenceTransformer(settings.EMBEDDING_MODEL)
            self.dimension = getattr(self.model, "get_sentence_embedding_dimension", lambda: self.dimension)() or self.dimension
        except Exception:
            self.model = None

    def _fallback_vector(self, text, dimension=None):
        if dimension is None:
            dimension = self.dimension
        if not isinstance(text, str):
            text = str(text)

        vector = np.zeros(dimension, dtype=np.float32)
        for index in range(dimension):
            digest = hashlib.sha256(f"{text}|{index}".encode("utf-8")).digest()
            value = int.from_bytes(digest[:4], byteorder="big", signed=False)
            vector[index] = ((value / 4294967295.0) * 2.0) - 1.0
        return vector

    def _encode_text(self, text):
        if self.model is not None:
            try:
                return self.model.encode(
                    text,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
            except Exception:
                pass
        return self._fallback_vector(text)

    def embed_documents(self, documents):
        texts = [doc.page_content for doc in documents]
        embeddings = [self._encode_text(text) for text in texts]
        return np.vstack(embeddings).astype(np.float32).tolist()

    def embed_query(self, query):
        embedding = self._encode_text(query)
        return np.asarray(embedding, dtype=np.float32).tolist()

    def embed_text(self, text):
        embedding = self._encode_text(text)
        return np.asarray(embedding, dtype=np.float32).tolist()