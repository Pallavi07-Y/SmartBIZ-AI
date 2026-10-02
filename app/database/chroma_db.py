import uuid
import chromadb

from app.config import settings


class ChromaDBManager:

    def __init__(self):

        self.client = chromadb.PersistentClient(
            path=settings.CHROMA_DB_PATH
        )

    def get_collection(self, collection_name):

        return self.client.get_or_create_collection(
            name=collection_name,
            metadata={
                "hnsw:space": "cosine"
            }
        )

    def add_documents(
        self,
        collection_name,
        documents,
        embeddings,
        business_id
    ):

        collection = self.get_collection(
            collection_name
        )

        ids = []
        texts = []
        metadatas = []

        for index, doc in enumerate(documents):
            metadata = dict(doc.metadata or {})
            metadata["business_id"] = business_id

            stable_id = metadata.get("product_id") or metadata.get("document_id")
            if stable_id is None:
                source_label = (metadata.get("document_name") or metadata.get("source") or "chunk").replace(".", "_").replace(" ", "_")
                stable_id = f"{business_id}_{source_label}_{index:03d}"

            ids.append(str(stable_id))
            texts.append(doc.page_content)
            metadatas.append(metadata)

        collection.add(
            ids=ids,
            documents=texts,
            embeddings=embeddings,
            metadatas=metadatas
        )

        return {
            "status": "success",
            "documents_added": len(texts)
        }

    def search(
        self,
        collection_name,
        query_embedding,
        business_id,
        top_k=5,
        metadata_filter=None
    ):

        collection = self.get_collection(
            collection_name
        )

        if metadata_filter:
            where_filter = {
                "$and": [
                    {"business_id": business_id},
                    metadata_filter
                ]
            }
        else:
            where_filter = {
                "business_id": business_id
            }

        results = collection.query(
            query_embeddings=[query_embedding],
            n_results=top_k,
            where=where_filter,
            include=["documents", "distances", "metadatas"]
        )

        return results

    def delete_by_metadata(self, collection_name, metadata_filter):
        collection = self.get_collection(collection_name)
        try:
            collection.delete(where=metadata_filter)
            return True
        except Exception:
            return False

    def delete_collection(self, collection_name):

        self.client.delete_collection(
            name=collection_name
        )

        return True

    def list_collections(self):

        return self.client.list_collections()

    def collection_exists(self, collection_name):

        collections = self.client.list_collections()

        names = [c.name for c in collections]

        return collection_name in names