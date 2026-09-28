from app.business.upload_documents import DocumentUploader
from app.config import settings
from app.database.chroma_db import ChromaDBManager
import app.rag.vector_store as vector_store_module


class FakeEmbeddingModel:
    def embed_documents(self, documents):
        return [[float(index), 0.0, 1.0] for index, _ in enumerate(documents)]


def test_upload_stores_documents_with_content_and_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(vector_store_module, "EmbeddingModel", FakeEmbeddingModel)

    source = tmp_path / "products.txt"
    source.write_text(
        "Category: Smartphones\n\n"
        "Product Name: Samsung Galaxy S25\n"
        "Brand: Samsung\n"
        "Price: ₹75,000\n"
        "Availability: In Stock\n",
        encoding="utf-8",
    )
    business_id = "test-business"

    result = DocumentUploader().upload(business_id, str(source))

    collection = ChromaDBManager().get_collection(f"business_{business_id}")
    stored = collection.get(include=["documents", "metadatas"])

    assert result["business_id"] == business_id
    assert result["documents_added"] == collection.count()
    assert result["status"]["status"] == "success"
    assert any("Samsung Galaxy S25" in document for document in stored["documents"])
    assert all(metadata["business_id"] == business_id for metadata in stored["metadatas"])
    assert any(metadata["type"] == "product" for metadata in stored["metadatas"])