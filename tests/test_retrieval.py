from langchain_core.documents import Document

from app.config import settings
from app.database.chroma_db import ChromaDBManager
from app.rag.rag_pipeline import RAGPipeline
from app.rag.retriever import Retriever


def test_embedding_model_uses_cached_files_without_network(monkeypatch):
    from app.rag import embedding

    calls = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name, **kwargs):
            calls["model_name"] = model_name
            calls.update(kwargs)

        def get_embedding_dimension(self):
            return 384

    monkeypatch.setattr(embedding, "SentenceTransformer", FakeSentenceTransformer)

    model = embedding.EmbeddingModel()

    assert model.dimension == 384
    assert calls["model_name"] == settings.EMBEDDING_MODEL
    assert calls["local_files_only"] is True


def test_retrieval_filter_uses_semantic_distance_not_query_word_matching():
    pipeline = RAGPipeline.__new__(RAGPipeline)
    query = "Could I get my money back if I kept the purchase in its original condition?"
    document_distances = [
        (
            "An item in unused condition may be refunded when the buyer provides the original receipt.",
            0.12,
        ),
        ("Local delivery depends on the customer's address and order size.", 0.55),
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


def test_shop_name_query_keeps_semantically_retrieved_profile_and_policy_context():
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
                    {"type": "business_knowledge", "business_id": "shop-a"},
                    {"type": "business", "business_id": "shop-a"},
                ],
            }

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.retriever = FakeRetriever()

    context = pipeline.retrieve_context("business_shop-a", "what is the shop name?")

    assert pipeline.retriever.metadata_filter is None
    assert "Business Name: North Shop" in context
    assert "Delivery Information" in context


def test_product_query_uses_semantic_similarity_without_type_filter():
    class FakeRetriever:
        metadata_filter = None

        def retrieve(self, collection_name, query, top_k=10, metadata_filter=None):
            self.metadata_filter = metadata_filter
            return {
                "documents": ["Product Name: USB-C Fast Charger\nCategory: Mobile Accessories"],
                "distances": [0.1],
                "metadatas": [{"document_type": "product", "business_id": "shop-a"}],
            }

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.retriever = FakeRetriever()

    context = pipeline.retrieve_context("business_shop-a", "tell me about USB-C Fast Charger")

    assert pipeline.retriever.metadata_filter is None
    assert "Product Name: USB-C Fast Charger" in context
    assert "Category: Mobile Accessories" in context
    assert "Price:" not in context


def test_chroma_semantic_retrieval_supports_paraphrases_and_isolates_businesses(
    tmp_path,
    monkeypatch,
):
    class SemanticTestEmbedding:
        concepts = {
            0: ("refund", "money back", "return"),
            1: ("unused", "original condition", "never used"),
            2: ("receipt", "invoice", "proof of purchase"),
            3: ("delivery", "deliver", "dispatch", "doorstep"),
        }

        @classmethod
        def vector(cls, text):
            text = text.casefold()
            return [
                float(any(term in text for term in terms))
                for terms in cls.concepts.values()
            ]

        def embed_documents(self, documents):
            return [self.vector(document.page_content) for document in documents]

        def embed_query(self, query):
            return self.vector(query)

    monkeypatch.setattr(settings, "CHROMA_DB_PATH", str(tmp_path / "chroma"))
    monkeypatch.setattr(
        "app.rag.vector_store.EmbeddingModel",
        SemanticTestEmbedding,
    )
    from app.rag.vector_store import VectorStore

    store = VectorStore()
    store.add_documents(
        "business_shop-a",
        [
            Document(
                page_content=(
                    "An item in unused condition may be refunded when the buyer "
                    "provides the original receipt."
                ),
                metadata={"document_name": "shop-a-policy.txt"},
            ),
            Document(
                page_content="Local delivery to a customer's doorstep depends on order size.",
                metadata={"document_name": "shop-a-delivery.txt"},
            ),
        ],
        "shop-a",
    )
    store.add_documents(
        "business_shop-b",
        [
            Document(
                page_content="Shop B requires manager approval for all returns.",
                metadata={"document_name": "shop-b-policy.txt"},
            )
        ],
        "shop-b",
    )

    pipeline = RAGPipeline.__new__(RAGPipeline)
    pipeline.retriever = Retriever()
    context = pipeline.retrieve_context(
        "business_shop-a",
        "Could I get my money back if I kept the purchase in its original condition and have the invoice?",
    )

    assert "unused condition may be refunded" in context
    assert "Shop B" not in context
    assert "doorstep" not in context