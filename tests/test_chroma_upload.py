from types import SimpleNamespace

from app.business.catalog import BusinessCatalog
from app.business.register_business import BusinessManager
from app.agent.tools import AgentTools
from app.business.upload_documents import DocumentUploader
from app.config import settings
from app.database import Product, get_session
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
    stored_text = "\n".join(stored["documents"])
    assert "Samsung Galaxy S25" in stored_text
    assert "Price:" not in stored_text
    assert "Availability:" not in stored_text


def test_upload_does_not_fabricate_products_without_database_records(isolated_database):
    shop = BusinessManager().register_business(
        "Empty Catalog Shop",
        "Electronics",
        business_id="empty-catalog-shop",
    )

    catalog = BusinessCatalog(shop["business_id"])

    assert catalog.products == []
    assert catalog.get_product_names() == []


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


def test_product_rag_context_excludes_live_transaction_fields():
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
    assert "SKU: CHG-42" in context
    assert "Description: Compact fast charger" in context
    assert "Price:" not in context
    assert "Availability:" not in context
    assert "Stock Quantity:" not in context


def test_upload_replaces_stale_q_and_a_chunks_with_normal_sectioned_prose(
    isolated_database,
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(vector_store_module, "EmbeddingModel", FakeEmbeddingModel)

    source = tmp_path / "shop-knowledge.txt"
    source.write_text(
        "Frequently Asked Questions:\n"
        "Q: Can I return an item?\n"
        "A: Yes, an unused item can be returned with a receipt.\n\n"
        "Delivery:\n"
        + ("Delivery is available for local orders. " * 40),
        encoding="utf-8",
    )
    uploader = DocumentUploader()
    uploader.upload("shop-a", source)
    collection = ChromaDBManager().get_collection("business_shop-a")
    assert collection.count() > 1

    source.write_text(
        "Return Policy:\n"
        "Unused products with proof of purchase may be returned.\n",
        encoding="utf-8",
    )
    result = uploader.upload("shop-a", source)
    stored = collection.get(include=["documents", "metadatas"])

    assert result["documents_added"] == 1
    assert collection.count() == 1
    assert "Frequently Asked Questions" not in stored["documents"][0]
    assert "Q:" not in stored["documents"][0]
    assert stored["metadatas"][0]["business_id"] == "shop-a"
    assert stored["metadatas"][0]["document_type"] == "business_knowledge"


def test_same_filename_stem_in_different_formats_keeps_separate_chunks(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(vector_store_module, "EmbeddingModel", FakeEmbeddingModel)

    text_file = tmp_path / "shop-info.txt"
    csv_file = tmp_path / "shop-info.csv"
    text_file.write_text(
        "Return Policy:\nUnused items may be returned with proof of purchase.",
        encoding="utf-8",
    )
    csv_file.write_text(
        "Topic,Details\nDelivery,Orders can be delivered locally.",
        encoding="utf-8",
    )
    uploader = DocumentUploader()
    uploader.upload("shop-a", text_file)
    uploader.upload("shop-a", csv_file)

    collection = ChromaDBManager().get_collection("business_shop-a")
    stored = collection.get(include=["documents", "metadatas"])

    assert collection.count() == 2
    assert {metadata["document_name"] for metadata in stored["metadatas"]} == {
        "shop-info.txt",
        "shop-info.csv",
    }


def test_uploading_only_transactional_fields_removes_stale_indexed_values(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(vector_store_module, "EmbeddingModel", FakeEmbeddingModel)

    source = tmp_path / "product.txt"
    source.write_text(
        "Product Name: Charger One\n"
        "Brand: Example\n"
        "Price: ₹1,999\n"
        "Stock Quantity: 8\n",
        encoding="utf-8",
    )
    uploader = DocumentUploader()
    uploader.upload("shop-a", source)
    collection = ChromaDBManager().get_collection("business_shop-a")
    assert collection.count() == 1

    source.write_text(
        "Price: ₹2,099\n"
        "Stock Quantity: 4\n",
        encoding="utf-8",
    )
    result = uploader.upload("shop-a", source)

    assert result["documents_added"] == 0
    assert collection.count() == 0


def test_live_product_prices_and_stock_are_read_from_the_active_business_database(
    isolated_database,
):
    shop = BusinessManager().register_business(
        "Live Catalog Shop",
        "Electronics",
        business_id="live-catalog-shop",
    )
    session = get_session()
    try:
        product = Product(
            business_id=shop["business_id"],
            product_name="USB-C Charger",
            category="Accessories",
            price=1900,
            stock_quantity=7,
            availability="In Stock",
            is_active=True,
        )
        session.add(product)
        session.commit()
        product_id = product.id
    finally:
        session.close()

    tools = AgentTools.__new__(AgentTools)
    current_product = tools.get_live_products(shop["business_id"], query="charger")[0]
    assert current_product["price"] == 1900
    assert current_product["stock_quantity"] == 7
    assert current_product["availability"] == "In Stock"

    session = get_session()
    try:
        product = session.get(Product, product_id)
        product.price = 2100
        product.stock_quantity = 0
        product.availability = "Out of Stock"
        session.commit()
    finally:
        session.close()

    updated_product = tools.get_live_products(shop["business_id"], query="charger")[0]
    assert updated_product["price"] == 2100
    assert updated_product["stock_quantity"] == 0
    assert updated_product["availability"] == "Out of Stock"