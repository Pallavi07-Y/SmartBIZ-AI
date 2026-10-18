from pathlib import Path
import re

from app.database import Business, KnowledgeDocument, get_session
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker
from app.rag.vector_store import VectorStore


class DocumentUploader:
    LIVE_TRANSACTION_FIELD = re.compile(
        r"^\s*(?:[-*]\s*)?(?:(?:live|current)\s+)?"
        r"(?:unit\s+price|price|stock(?:\s+quantity)?|inventory(?:\s+quantity)?|"
        r"availability|order\s+total|order\s+status)\s*:",
        re.IGNORECASE,
    )

    def __init__(self, vector_store=None):

        self.loader = DocumentLoader()
        self.chunker = DocumentChunker()
        self.vector_store = vector_store or VectorStore()

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
        if not chunks:
            raise ValueError(f"No readable text was found in {file_name}.")

        for chunk in chunks:
            chunk.page_content = "\n".join(
                line
                for line in chunk.page_content.splitlines()
                if not self.LIVE_TRANSACTION_FIELD.match(line)
            ).strip()
        chunks = [
            chunk
            for chunk in chunks
            if any(
                line.strip()
                and not self.chunker.SECTION_HEADER.fullmatch(line.strip())
                for line in chunk.page_content.splitlines()
            )
        ]

        collection_name = f"business_{business_id}"
        metadata_filter = {"$and": [{"business_id": business_id}, {"document_name": file_name}]}

        session = get_session()
        business_record = None
        try:
            business_record = session.query(Business).filter_by(business_id=business_id).first()
        finally:
            session.close()

        for index, chunk in enumerate(chunks):
            chunk.metadata["business_id"] = business_id
            chunk.metadata["document_name"] = file_name
            chunk.metadata.setdefault("source", file_name)
            chunk.metadata["document_type"] = "business_knowledge"
            file_key = f"{path.stem}_{path.suffix.lower().lstrip('.')}"
            chunk.metadata["document_id"] = f"{business_id}_{file_key}_{index:04d}"
            chunk.metadata["business_name"] = business_record.name if business_record is not None else business_id
            chunk.metadata["category"] = business_record.category if business_record is not None else "General"

        result = self.vector_store.replace_documents(
            collection_name,
            chunks,
            business_id,
            metadata_filter,
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