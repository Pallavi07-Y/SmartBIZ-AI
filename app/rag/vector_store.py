from app.database.chroma_db import ChromaDBManager
from app.rag.embedding import EmbeddingModel


class VectorStore:

    def __init__(self):

        self.db = ChromaDBManager()
        self.embedding_model = EmbeddingModel()

    def add_documents(
        self,
        collection_name,
        documents,
        business_id
    ):

        embeddings = self.embedding_model.embed_documents(
            documents
        )

        return self.db.add_documents(
            collection_name,
            documents,
            embeddings,
            business_id
        )

    def search(
        self,
        collection_name,
        query,
        top_k=5,
        metadata_filter=None
    ):

        query_embedding = self.embedding_model.embed_query(
            query
        )

        business_id = collection_name.replace(
            "business_",
            "",
            1
        )

        return self.db.search(
            collection_name,
            query_embedding,
            business_id,
            top_k,
            metadata_filter=metadata_filter
        )