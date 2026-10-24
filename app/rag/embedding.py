import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np

try:
    from sentence_transformers import SentenceTransformer
except Exception as exc:  # pragma: no cover - depends on the installed ML runtime
    SentenceTransformer = None
    _sentence_transformers_error = exc
else:
    _sentence_transformers_error = None

from app.config import settings


class EmbeddingModel:

    def __init__(self):
        if SentenceTransformer is None:
            raise RuntimeError(
                "Semantic embeddings require the installed sentence-transformers runtime."
            ) from _sentence_transformers_error

        try:
            self.model = SentenceTransformer(
                settings.EMBEDDING_MODEL,
                local_files_only=True,
            )
            self.dimension = (
                self.model.get_embedding_dimension()
                or self.model.get_sentence_embedding_dimension()
            )
        except Exception as exc:
            raise RuntimeError(
                f"Unable to load semantic embedding model {settings.EMBEDDING_MODEL!r}."
            ) from exc

    def _encode_text(self, text):
        try:
            return self.model.encode(
                text,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
        except Exception as exc:
            raise RuntimeError("Semantic text embedding failed.") from exc

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