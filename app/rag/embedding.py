import os

os.environ["TOKENIZERS_PARALLELISM"] = "false"

from sentence_transformers import SentenceTransformer
from app.config import settings


class EmbeddingModel:

    def __init__(self):

        self.model = SentenceTransformer(
            settings.EMBEDDING_MODEL
        )

    def embed_documents(self, documents):

        texts = [
            doc.page_content
            for doc in documents
        ]

        embeddings = self.model.encode(
            texts,
            convert_to_numpy=True,
            show_progress_bar=False
        )

        return embeddings.tolist()

    def embed_query(self, query):

        embedding = self.model.encode(
            query,
            convert_to_numpy=True,
            show_progress_bar=False
        )

        return embedding.tolist()

    def embed_text(self, text):

        embedding = self.model.encode(
            text,
            convert_to_numpy=True,
            show_progress_bar=False
        )

        return embedding.tolist()