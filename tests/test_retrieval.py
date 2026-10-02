from app.database.chroma_db import ChromaDBManager
from app.rag.rag_pipeline import RAGPipeline
from app.rag.retriever import Retriever


def test_exact_product_filtering_uses_phrase_and_distance_thresholds():
    pipeline = RAGPipeline.__new__(RAGPipeline)
    query = "What is the price of Samsung Galaxy S25?"
    document_distances = [
        (
            "Product Name: Samsung Galaxy S25\nPrice: ₹75,000\nRAM: 12GB",
            0.12,
        ),
        ("Product Name: iPhone 16\nPrice: ₹79,000", 0.18),
        ("Business Name: ABC Mobiles\nContact Number: 1234567890", 0.29),
    ]

    threshold = pipeline._choose_distance_threshold(document_distances, query)
    filtered = pipeline._filter_documents_for_query(document_distances, query)

    assert threshold == 0.22
    assert filtered == [document_distances[0]]


def test_chroma_search_passes_metadata_filter_and_preserves_distances():
    class FakeCollection:
        def __init__(self):
            self.query_kwargs = None

        def query(self, **kwargs):
            self.query_kwargs = kwargs
            return {
                "documents": [["product document"]],
                "distances": [[0.14]],
                "metadatas": [[{"type": "product", "business_id": "demo"}]],
            }

    collection = FakeCollection()
    manager = ChromaDBManager.__new__(ChromaDBManager)
    manager.get_collection = lambda collection_name: collection

    result = manager.search(
        "business_demo",
        [0.1, 0.2, 0.3],
        "demo",
        top_k=3,
        metadata_filter={"type": "product"},
    )

    assert result["documents"] == [["product document"]]
    assert result["distances"] == [[0.14]]
    assert result["metadatas"][0][0]["type"] == "product"
    assert collection.query_kwargs["n_results"] == 3
    assert collection.query_kwargs["where"] == {
        "$and": [{"business_id": "demo"}, {"type": "product"}]
    }


def test_retriever_flattens_single_query_chroma_results():
    class FakeVectorStore:
        def search(self, *args, **kwargs):
            return {
                "documents": [["first", "second"]],
                "distances": [[0.1, 0.2]],
                "metadatas": [[{"rank": 1}, {"rank": 2}]],
            }

    retriever = Retriever.__new__(Retriever)
    retriever.vector_store = FakeVectorStore()

    result = retriever.retrieve("business_demo", "list products", top_k=2)

    assert result == {
        "documents": ["first", "second"],
        "distances": [0.1, 0.2],
        "metadatas": [{"rank": 1}, {"rank": 2}],
    }


def test_shop_name_query_uses_business_profile_context():
    class FakeRetriever:
        metadata_filter = None

        def retrieve(self, collection_name, query, top_k=10, metadata_filter=None):
            self.metadata_filter = metadata_filter
            return {
                "documents": [
                    "Delivery Information: Local dispatch is available.",
                    "Business Name: North Shop\nBusiness ID: shop-a\nCategory: Grocery",
                ],
                "distances": [0.1, 0.2],
                "metadatas": [
                    {"type": "faq", "business_id": "shop-a"},
                    {"type": "business", "business_id": "shop-a"},
                ],
            }

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.retriever = FakeRetriever()

    context = pipeline.retrieve_context("business_shop-a", "what is the shop name?")

    assert pipeline.retriever.metadata_filter == {"type": "business"}
    assert "Business Name: North Shop" in context
    assert "Delivery Information" not in context


def test_exact_charger_query_uses_product_document_filter():
    class FakeRetriever:
        metadata_filter = None

        def retrieve(self, collection_name, query, top_k=10, metadata_filter=None):
            self.metadata_filter = metadata_filter
            return {
                "documents": ["Product Name: USB-C Fast Charger\nCategory: Mobile Accessories\nPrice: ₹1,999"],
                "distances": [0.1],
                "metadatas": [{"document_type": "product", "business_id": "shop-a"}],
            }

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.retriever = FakeRetriever()

    context = pipeline.retrieve_context("business_shop-a", "tell me about USB-C Fast Charger")

    assert pipeline.retriever.metadata_filter == {"type": "product"}
    assert "Product Name: USB-C Fast Charger" in context
    assert "Category: Mobile Accessories" in context