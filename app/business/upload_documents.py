import os
from pathlib import Path

from app.database import KnowledgeDocument, get_session
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

        path = Path(file_path)
        file_name = path.name
        documents = self.loader.load_document(
            file_path
        )

        chunks = self.chunker.split_documents(
            documents
        )

        collection_name = f"business_{business_id}"
        metadata_filter = {"$and": [{"business_id": business_id}, {"document_name": file_name}]}
        self.vector_store.db.delete_by_metadata(collection_name, metadata_filter)

        session = get_session()
        business_record = None
        try:
            business_record = session.query(__import__('app.database', fromlist=['Business']).Business).filter_by(business_id=business_id).first()
        finally:
            session.close()

        for chunk in chunks:
            chunk.metadata.setdefault("business_id", business_id)
            chunk.metadata.setdefault("document_name", file_name)
            chunk.metadata.setdefault("source", file_name)
            chunk.metadata.setdefault("document_type", "business_knowledge")
            chunk.metadata["business_name"] = business_record.name if business_record is not None else business_id
            chunk.metadata["category"] = business_record.category if business_record is not None else "General"

        result = self.vector_store.add_documents(
            collection_name,
            chunks,
            business_id
        )

        session = get_session()
        try:
            record = (
                session.query(KnowledgeDocument)
                .filter(KnowledgeDocument.business_id == business_id, KnowledgeDocument.document_name == file_name)
                .order_by(KnowledgeDocument.updated_at.desc())
                .first()
            )
            if record is None:
                record = KnowledgeDocument(
                    business_id=business_id,
                    document_name=file_name,
                    document_type=path.suffix.lower().lstrip("."),
                    source="upload",
                    status="INDEXED",
                    chunk_count=len(chunks),
                )
                session.add(record)
            else:
                record.document_type = path.suffix.lower().lstrip(".")
                record.status = "INDEXED"
                record.chunk_count = len(chunks)
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

        return {
            "business_id": business_id,
            "documents_added": len(chunks),
            "status": result
        }