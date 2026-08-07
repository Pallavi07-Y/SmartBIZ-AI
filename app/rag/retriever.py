from app.rag.vector_store import VectorStore


class Retriever:

    def __init__(self):

        self.vector_store = VectorStore()


    def retrieve(
        self,
        collection_name,
        query,
        top_k=10,
        metadata_filter=None
    ):

        results = self.vector_store.search(
            collection_name,
            query,
            top_k,
            metadata_filter=metadata_filter
        )

        documents = results.get("documents", [[]])
        distances = results.get("distances", [[]])
        metadatas = results.get("metadatas", [[]])

        documents = documents[0] if documents else []
        distances = distances[0] if distances else []
        metadatas = metadatas[0] if metadatas else []

        return {
            "documents": documents,
            "distances": distances,
            "metadatas": metadatas
        }