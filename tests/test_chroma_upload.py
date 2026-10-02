from types import SimpleNamespace

from app.business.catalog import BusinessCatalog
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


def test_product_catalog_sync_uses_stable_product_vector_ids(monkeypatch):
    captured = {}

    class FakeVectorStore:
        def __init__(self):
            self.db = SimpleNamespace(delete_by_metadata=lambda *args: True)

        def add_documents(self, collection_name, documents, business_id):
            captured["collection_name"] = collection_name
            captured["documents"] = documents
            captured["business_id"] = business_id
            return {"status": "success", "documents_added": len(documents)}

    import app.rag.vector_store as vector_store_module

    monkeypatch.setattr(vector_store_module, "VectorStore", FakeVectorStore)
    catalog = BusinessCatalog.__new__(BusinessCatalog)
    catalog.business_id = "shop-42"
    catalog.products = [{"id": 42, "product_name": "Phone Alpha", "category": "Phones", "brand": "Acme"}]
    catalog.get_product_context = lambda product_name: f"Product Name: {product_name}\nBrand: Acme"

    result = catalog.sync_products_to_rag()

    assert result["status"]["documents_added"] == 1
    assert captured["collection_name"] == "business_shop-42"
    assert captured["documents"][0].metadata == {
        "business_id": "shop-42",
        "product_id": "product_42",
        "document_type": "product",
        "type": "product",
        "category": "Phones",
        "brand": "Acme",
    }


def test_product_context_includes_live_catalog_fields():
    catalog = BusinessCatalog.__new__(BusinessCatalog)
    catalog.business_id = "shop-42"
    catalog.products = [{
        "id": 42,
        "product_name": "USB-C Fast Charger",
        "sku": "CHG-42",
        "brand": "VoltLine",
        "category": "Accessories",
        "price": 1999,
        "availability": "In Stock",
        "stock_quantity": 30,
        "ram": None,
        "storage": None,
        "processor": None,
        "description": "Compact fast charger for modern phones.",
    }]

    context = catalog.get_product_context("USB-C Fast Charger")

    assert "Product Name: USB-C Fast Charger" in context
    assert "Price: ₹1,999" in context
    assert "Availability: In Stock" in context
    assert "Stock Quantity: 30" in context
    assert "SKU: CHG-42" in context
    assert "Description: Compact fast charger" in context