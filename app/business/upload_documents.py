import os

from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker
from app.rag.vector_store import VectorStore


class DocumentUploader:

    def __init__(self):

        self.loader = DocumentLoader()
        self.chunker = DocumentChunker()
        self.vector_store = VectorStore()

    def upload(
        self,
        business_id,
        file_path
    ):

        documents = self.loader.load_document(
            file_path
        )

        chunks = self.chunker.split_documents(
            documents
        )

        collection_name = f"business_{business_id}"

        result = self.vector_store.add_documents(
            collection_name,
            chunks,
            business_id
        )

        return {
            "business_id": business_id,
            "documents_added": len(chunks),
            "status": result
        }