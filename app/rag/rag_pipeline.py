import re

from app.llm.llm import LLM
from app.rag.retriever import Retriever
from app.utils.logger import logger


class RAGPipeline:
    LIVE_TRANSACTION_LINE = re.compile(
        r"^\s*(?:[-*]\s*)?(?:\|\s*)?(?:(?:live|current)\s+)?"
        r"(?:unit\s+price|price|mrp|stock(?:\s+quantity)?|inventory(?:\s+quantity)?|"
        r"availability|order\s+total|order\s+status|shipment\s+status)\s*[:=]",
        re.IGNORECASE,
    )
    LIVE_CURRENCY_VALUE = re.compile(
        r"(?:₹|\bINR\b|\bRs\.?|\bUSD\b|\$)\s*[\d,]+(?:\.\d{1,2})?",
        re.IGNORECASE,
    )
    LIVE_STOCK_VALUE = re.compile(
        r"\b\d+\s+(?:units?|pieces?|items?)\s+(?:in\s+stock|available)\b",
        re.IGNORECASE,
    )
    BUSINESS_QUERY_KEYWORDS = (
        "business",
        "shop",
        "product",
        "products",
        "sell",
        "brand",
        "catalog",
        "charger",
        "accessories",
        "service",
        "support",
        "delivery",
        "warranty",
        "return",
        "refund",
        "exchange",
        "policy",
        "payment",
        "hours",
        "open",
        "close",
        "contact",
        "address",
        "bring back",
        "send back",
        "swap",
        "money back",
        "take back",
        "undo purchase",
        "ship",
        "deliver",
    )
    BUSINESS_QUESTION_WORDS = (
        "what",
        "where",
        "when",
        "who",
        "how",
        "would",
        "should",
        "may i",
        "do you",
        "can i",
        "could i",
        "is",
        "are",
        "tell me",
        "describe",
        "recommend",
    )
    CASUAL_QUERIES = (
        r"^\s*(hello+|hi+|hey+|good\s+(morning|afternoon|evening))\b",
        r"\b(thanks|thank you|thank you very much|ty|bye|goodbye|see you|see ya)\b",
    )

    @staticmethod
    def _is_laptop_query(query):
        return bool(re.search(r"\b(?:laptops?|notebooks?|computers?)\b", str(query or ""), re.I))

    @staticmethod
    def _is_phone_query(query):
        return bool(re.search(
            r"\b(?:phones?|smart\s*phones?|mobiles?|iphones?|galax(?:y|ies)|oneplus)\b",
            str(query or ""),
            re.I,
        ))

    @staticmethod
    def _is_general_product_query(query):
        text = str(query or "")
        if RAGPipeline._is_laptop_query(text) or RAGPipeline._is_phone_query(text):
            return False
        return bool(re.search(
            r"\b(?:products?|items?|catalog|inventory)\b|what\s+do\s+you\s+sell",
            text,
            re.I,
        ))

    @classmethod
    def _is_product_query(cls, query):
        if cls._is_laptop_query(query):
            return False
        return cls._is_general_product_query(query) or cls._is_phone_query(query) or bool(
            re.search(r"\b(?:charger|accessories|gadgets?)\b", str(query or ""), re.I)
        )

    @staticmethod
    def _is_service_query(query):
        return bool(re.search(
            r"\b(?:services?|delivery|shipping|repairs?|installation|home\s+delivery)\b",
            str(query or ""),
            re.I,
        ))

    def __init__(self):
        self.retriever = Retriever()
        self.llm = LLM()

    def is_casual_query(self, query):
        text = query.strip().lower() if isinstance(query, str) else ""
        return bool(text) and any(re.search(pattern, text) for pattern in self.CASUAL_QUERIES)

    def is_business_query(self, query):
        text = query.strip().lower() if isinstance(query, str) else ""
        if not text or self.is_casual_query(text):
            return False
        if any(
            re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", text)
            for keyword in self.BUSINESS_QUERY_KEYWORDS
        ):
            return True
        if any(keyword in text for keyword in self.BUSINESS_QUESTION_WORDS):
            return True
        return bool(re.search(
            r"\b(?:changed my mind|change my mind|regret buying|send it back|"
            r"bring it back|take it back|return it|refund me|money back|"
            r"swap it|replace it|give it back|ship to me|deliver to me)\b",
            text,
        ))

    @staticmethod
    def _choose_distance_threshold(document_distances, query):
        distances = [distance for _, distance in document_distances if distance is not None]
        if not distances:
            return None

        closest_distance = min(distances)
        max_allowed_distance = 0.82
        if closest_distance > max_allowed_distance:
            return None
        return min(closest_distance + 0.10, max_allowed_distance)

    def _filter_documents_for_query(self, document_distances, query):
        threshold = self._choose_distance_threshold(document_distances, query)
        if threshold is None:
            logger.debug("No semantically relevant documents found.")
            return []

        unique_documents = []
        seen = set()
        for document, distance in document_distances:
            if not document or distance is None or distance > threshold:
                continue
            text = document.strip()
            if text and text not in seen:
                seen.add(text)
                unique_documents.append((text, distance))
        return unique_documents

    def retrieve_context(self, collection_name, query):
        results = self.retriever.retrieve(collection_name, query, top_k=10)
        documents = results.get("documents", [])
        distances = results.get("distances", [])

        if documents and isinstance(documents[0], list):
            documents = documents[0]
        if distances and isinstance(distances[0], list):
            distances = distances[0]
        if not documents:
            return "No business information found."

        relevant_documents = []
        for document, distance in self._filter_documents_for_query(
            list(zip(documents, distances)),
            query,
        ):
            safe_lines = []
            for line in document.splitlines():
                if self.LIVE_TRANSACTION_LINE.match(line):
                    continue
                if self.LIVE_CURRENCY_VALUE.search(line) or self.LIVE_STOCK_VALUE.search(line):
                    continue
                safe_lines.append(line)
            safe_document = "\n".join(safe_lines).strip()
            if safe_document:
                relevant_documents.append((safe_document, distance))
        if not relevant_documents:
            return "No business information found."

        for index, (_, distance) in enumerate(relevant_documents, start=1):
            logger.debug("Retrieved chunk %s (cosine distance=%s).", index, distance)

        return "\n\n".join(document for document, _ in relevant_documents)

    def generate_answer(self, collection_name, query):
        context = self.retrieve_context(collection_name, query)
        prompt = f"""
        Retrieved knowledge for the active business:
        {context}

        Customer question:
        {query}

        Answer only from the retrieved knowledge. Do not use facts from another business
        or general knowledge. Live prices, stock, availability, order totals, and order
        status must be obtained from transactional tools, never from retrieved documents.
        """
        return self.llm.generate_response(prompt)
