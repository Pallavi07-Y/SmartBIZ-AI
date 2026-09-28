from types import SimpleNamespace

import pytest

from app.rag.chunking import DocumentChunker
from app.rag.rag_pipeline import RAGPipeline


@pytest.mark.parametrize(
    ("text", "expected_category"),
    [
        ("Product Name: USB Charger\nPrice: ₹1,000", "product"),
        ("Product Name: ASUS Vivobook\nLaptop with SSD and RAM", "laptop"),
        ("Product Name: Samsung Galaxy S25\n12GB RAM", "smartphone"),
        ("Product Name: OnePlus 13\nPhone with 8GB RAM and 256GB SSD", "smartphone"),
        ("Product Name: Generic SSD Upgrade\n8GB RAM and 256GB SSD", "laptop"),
    ],
)
def test_product_chunk_metadata_classification(text, expected_category):
    chunker = DocumentChunker()
    chunk = SimpleNamespace(page_content=text, metadata={})

    classified = chunker._classify_chunk(chunk)

    assert classified.metadata["type"] == "product"
    assert classified.metadata["category"] == expected_category


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        (
            "what products are available",
            {"product": True, "general": True, "laptop": False, "phone": False, "service": False},
        ),
        (
            "suggest a laptop for coding",
            {"product": False, "general": False, "laptop": True, "phone": False, "service": False},
        ),
        (
            "what smartphones are available",
            {"product": True, "general": False, "laptop": False, "phone": True, "service": False},
        ),
        (
            "do you provide home delivery",
            {"product": False, "general": False, "laptop": False, "phone": False, "service": True},
        ),
    ],
)
def test_query_classification(query, expected):
    pipeline = RAGPipeline.__new__(RAGPipeline)

    assert pipeline._is_product_query(query) is expected["product"]
    assert pipeline._is_general_product_query(query) is expected["general"]
    assert pipeline._is_laptop_query(query) is expected["laptop"]
    assert pipeline._is_phone_query(query) is expected["phone"]
    assert pipeline._is_service_query(query) is expected["service"]