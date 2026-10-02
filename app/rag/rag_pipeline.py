import re
from app.rag.retriever import Retriever
from app.llm.llm import LLM
from app.utils.logger import logger


class RAGPipeline:

    PRODUCT_QUERY_KEYWORDS = [
        "product",
        "products",
        "available",
        "sell",
        "brand",
        "price",
        "inventory",
        "item",
        "catalog",
        "list",
        "charger",
        "chargers",
        "accessory",
        "accessories",
        "usb-c",
    ]

    SPECIFIC_PRODUCT_KEYWORDS = [
        "laptop",
        "notebook",
        "smartphone",
        "mobile",
        "phone",
        "accessories",
        "charger",
        "power bank",
        "screen guard"
    ]

    SERVICE_QUERY_KEYWORDS = [
        "service",
        "services",
        "support",
        "delivery",
        "warranty",
        "return",
        "refund",
        "exchange",
        "policy",
        "policies",
        "home delivery",
        "payment"
    ]

    BUSINESS_QUERY_KEYWORDS = [
        "working hours",
        "hours",
        "open",
        "close",
        "contact",
        "address"
    ]

    FAQ_QUERY_KEYWORDS = [
        "emi",
        "delivery",
        "warranty",
        "support",
        "do you provide",
        "do you offer",
        "can i",
        "how do",
        "what are your"
    ]

    PRODUCT_SECTION_TERMS = [
        "products available",
        "product",
        "brand:",
        "price:",
        "availability:",
        "accessories",
        "ram:",
        "storage:",
        "battery:",
        "processor:",
        "display:"
    ]

    SERVICE_SECTION_TERMS = [
        "services",
        "emi",
        "delivery",
        "warranty",
        "support",
        "home delivery"
    ]

    BUSINESS_PROFILE_TERMS = [
        "business name:",
        "business id:",
        "category:",
        "owner:",
        "address:",
        "contact number:",
        "email:",
        "website:",
        "working hours:",
        "payment methods:",
        "complete business knowledge base",
        "about our shop"
    ]

    BUSINESS_PROFILE_QUERY_TERMS = [
        "shop name",
        "business name",
        "name of the shop",
        "name of this shop",
        "name of the business",
        "what is this shop",
    ]

    BUSINESS_PROFILE_DOCUMENT_MARKERS = [
        "business name:",
        "business id:",
        "owner:",
        "address:",
        "contact number:",
        "email:",
        "website:",
        "working hours:",
        "payment methods:",
        "complete business knowledge base",
        "about our shop",
    ]

    LAPTOP_TERMS = [
        "laptop",
        "notebook",
        "coding",
        "programming",
        "intel",
        "amd",
        "ryzen",
        "core",
        "processor",
        "ssd",
        "ram",
        "storage",
        "battery"
    ]

    PHONE_TERMS = [
        "phone",
        "mobile",
        "iphone",
        "samsung",
        "oneplus",
        "nothing",
        "vivo",
        "realme",
        "xiaomi",
        "oppo"
    ]

    CASUAL_QUERIES = [
        r"^\s*(hello+|hi+|hey+|good\s+(morning|afternoon|evening))\b",
        r"\b(thanks|thank you|thank you very much|ty|bye|goodbye|see you|see ya)\b"
    ]

    BUSINESS_QUESTION_WORDS = [
        "what",
        "where",
        "when",
        "who",
        "how",
        "do you",
        "can i",
        "could i",
        "is",
        "are",
        "tell me",
        "describe",
        "recommend"
    ]

    def __init__(self):

        self.retriever = Retriever()
        self.llm = LLM()


    def _is_product_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in self.PRODUCT_QUERY_KEYWORDS)


    def _is_general_product_query(self, query):

        text = query.lower()

        return self._is_product_query(query) and not any(keyword in text for keyword in self.SPECIFIC_PRODUCT_KEYWORDS)


    def _is_service_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in self.SERVICE_QUERY_KEYWORDS)


    def _is_business_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in self.BUSINESS_QUERY_KEYWORDS + self.BUSINESS_PROFILE_QUERY_TERMS)


    def _is_faq_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in self.FAQ_QUERY_KEYWORDS)


    def _is_laptop_query(self, query):

        text = query.lower()

        return "laptop" in text or "notebook" in text or "coding" in text or "performance" in text


    def _is_smartphone_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in ["smartphone", "phone", "mobile", "samsung", "iphone", "oneplus", "nothing", "vivo", "realme", "xiaomi", "oppo"])


    def _is_accessory_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in ["accessory", "accessories", "charger", "power bank", "screen guard", "earphones", "headphones"])


    def _is_phone_query(self, query):

        text = query.lower()

        return any(keyword in text for keyword in self.PHONE_TERMS)


    def is_casual_query(self, query):

        text = query.strip().lower()
        if not text:
            return False

        for pattern in self.CASUAL_QUERIES:
            if re.search(pattern, text):
                return True

        return False


    def is_business_query(self, query):

        text = query.strip().lower()
        if not text:
            return False

        if any(term in text for term in self.BUSINESS_PROFILE_QUERY_TERMS):
            return True

        if self.is_casual_query(text):
            return False

        if any(keyword in text for keyword in self.PRODUCT_QUERY_KEYWORDS + self.SERVICE_QUERY_KEYWORDS + self.BUSINESS_QUERY_KEYWORDS + self.SPECIFIC_PRODUCT_KEYWORDS):
            return True

        if any(word in text for word in self.BUSINESS_QUESTION_WORDS):
            if any(term in text for term in ["price", "warranty", "delivery", "home delivery", "working hours", "open", "close", "available", "availability", "product", "sell", "brand", "catalog", "stock", "macbook", "iphone", "oneplus", "samsung", "asus", "vivo", "realme", "xiaomi", "oppo", "laptop", "phone", "smartphone", "accessory"]):
                return True
            return "tell me about" in text or "describe" in text or "recommend" in text

        return False


    def _remove_profile_documents(self, documents):

        return [
            doc for doc in documents
            if not any(term in doc.lower() for term in self.BUSINESS_PROFILE_TERMS)
        ]


    def _extract_product_phrases(self, query):

        text = query.strip()
        if not text:
            return []

        phrases = []

        # Exact quoted phrases
        for match in re.finditer(r'"([^"]+)"', text):
            phrase = match.group(1).strip()
            if len(phrase.split()) > 1:
                phrases.append(phrase)

        # Common consumer electronics brands and model patterns
        brand_pattern = re.compile(
            r"\b(?:Samsung|Apple|OnePlus|Nothing|Vivo|Realme|Xiaomi|Oppo|ASUS|iPhone|Galaxy|MacBook|HP|OnePlus)"
            r"(?:\s+[A-Za-z0-9\-]+){0,4}\b",
            re.I
        )
        for match in brand_pattern.finditer(text):
            phrase = match.group(0).strip()
            if len(phrase.split()) > 1:
                phrases.append(phrase)

        # Numeric product model names like 'Galaxy S25' or 'iPhone 16'
        for match in re.finditer(r"\b(?:Galaxy|S25|S25 Ultra|iPhone 16|HP Victus|ASUS Vivobook Pro|OnePlus 13|Nothing Phone 3|Vivo X200|Realme GT 7)\b", text, re.I):
            phrases.append(match.group(0).strip())

        # Title-cased multi-word phrases (product names)
        for match in re.finditer(r"\b([A-Z][A-Za-z0-9]+(?:\s+[A-Z][A-Za-z0-9]+){1,4})\b", text):
            phrase = match.group(1).strip()
            if phrase.lower() not in {"what products", "do you", "tell me", "iPhone", "samsung", "apple", "oneplus", "nothing", "vivo", "realme", "xiaomi", "oppo"}:
                phrases.append(phrase)

        unique_phrases = []
        for phrase in phrases:
            normalized = phrase.strip()
            if normalized and normalized.lower() not in {p.lower() for p in unique_phrases}:
                unique_phrases.append(normalized)

        return unique_phrases


    def _normalize_text_for_matching(self, text):

        normalized = re.sub(r"[^a-z0-9\s]", " ", text.lower())
        normalized = re.sub(r"\s+", " ", normalized).strip()
        return normalized


    def _phrase_matches(self, doc, phrase):

        doc_norm = self._normalize_text_for_matching(doc)
        phrase_norm = self._normalize_text_for_matching(phrase)

        if phrase_norm in doc_norm:
            return True

        words = phrase_norm.split()
        return all(word in doc_norm for word in words)


    def _choose_distance_threshold(self, document_distances, query):

        distances = [dist for _, dist in document_distances if dist is not None]
        if not distances:
            return None

        min_dist = min(distances)
        # Chroma with cosine space returns cosine distance: lower is better.
        max_allowed = 0.82

        if min_dist > max_allowed:
            return None

        relative_threshold = min(min_dist + 0.10, max_allowed)
        return relative_threshold


    def _filter_documents_for_query(self, document_distances, query):

        documents, distances = zip(*document_distances) if document_distances else ([], [])
        text = query.lower()
        threshold = self._choose_distance_threshold(document_distances, query)

        if threshold is None:
            logger.debug("RAG filter threshold=None for query='%s' -> no relevant documents.", query[:80].strip())
            return []

        logger.debug("RAG filter threshold=%0.3f for query='%s'", threshold, query[:80].strip())

        def matches_any(doc, terms):
            return any(term in doc.lower() for term in terms)

        filtered = []
        product_phrases = self._extract_product_phrases(query)
        if any(term in text for term in self.BUSINESS_PROFILE_QUERY_TERMS):
            filtered = [
                (doc, dist) for doc, dist in document_distances
                if dist <= threshold and any(term in doc.casefold() for term in ("business name:", "shop name:"))
            ]

        if product_phrases:
            logger.debug("RAG query product phrases: %s", product_phrases)
            phrase_filtered = [
                (doc, dist) for doc, dist in document_distances
                if dist <= threshold and any(self._phrase_matches(doc, phrase) for phrase in product_phrases)
            ]
            if phrase_filtered and not filtered:
                filtered = phrase_filtered

        if not filtered:
            if self._is_laptop_query(text):
                filtered = [
                    (doc, dist) for doc, dist in document_distances
                    if dist <= threshold and matches_any(doc, self.LAPTOP_TERMS)
                ]

            elif self._is_phone_query(text):
                filtered = [
                    (doc, dist) for doc, dist in document_distances
                    if dist <= threshold and matches_any(doc, self.PHONE_TERMS)
                ]

            elif self._is_service_query(text):
                filtered = [
                    (doc, dist) for doc, dist in document_distances
                    if dist <= threshold and matches_any(doc, self.SERVICE_SECTION_TERMS)
                ]

            elif self._is_general_product_query(query):
                filtered = [
                    (doc, dist) for doc, dist in document_distances
                    if dist <= threshold
                ]

            elif self._is_product_query(text):
                filtered = [
                    (doc, dist) for doc, dist in document_distances
                    if dist <= threshold and matches_any(doc, self.PRODUCT_SECTION_TERMS)
                ]

            else:
                filtered = [
                    (doc, dist) for doc, dist in document_distances
                    if dist <= threshold
                ]

        if not filtered:
            logger.debug("RAG filter returned no documents using threshold. No relevant documents found.")
            return []

        seen = set()
        unique = []
        for doc, dist in filtered:
            normalized = doc.strip()
            if not normalized or normalized in seen:
                continue
            if any(term in normalized.lower() for term in self.BUSINESS_PROFILE_DOCUMENT_MARKERS) and self._is_product_query(text):
                continue
            seen.add(normalized)
            unique.append((normalized, dist))

        return unique


    def _get_metadata_filter(self, query):

        if self._is_product_query(query):
            return {"type": "product"}

        if any(term in query.casefold() for term in self.BUSINESS_PROFILE_QUERY_TERMS):
            return {"type": "business"}

        if self._is_faq_query(query):
            return {"type": "faq"}

        if self._is_business_query(query):
            return {"type": "business"}

        return None


    def retrieve_context(
        self,
        collection_name,
        query
    ):

        metadata_filter = self._get_metadata_filter(query)

        query_is_general_product = self._is_general_product_query(query)
        query_is_specific_product = self._is_laptop_query(query) or self._is_smartphone_query(query) or self._is_accessory_query(query)

        results = self.retriever.retrieve(
            collection_name,
            query,
            top_k=20 if query_is_general_product else 10,
            metadata_filter=metadata_filter
        )

        documents = results.get("documents", [])
        distances = results.get("distances", [])
        metadatas = results.get("metadatas", [])
        logger.debug("RAG search returned documents=%s distances=%s metadata=%s", len(documents), len(distances), len(metadatas))

        # Chroma returns lists-of-lists for query results (one list per query).
        # Flatten to simple lists when a single-query was used.
        if documents and isinstance(documents[0], list):
            documents = documents[0]

        if distances and isinstance(distances[0], list):
            distances = distances[0]

        if metadatas and isinstance(metadatas[0], list):
            metadatas = metadatas[0]

        if not documents:
            return "No business information found."

        document_distances = list(zip(documents, distances))

        filtered = self._filter_documents_for_query(
            document_distances,
            query
        )

        if not filtered:
            return "No business information found."

        unique_documents = []
        unique_distances = []
        seen = set()

        for doc, dist in filtered:
            normalized = doc.strip()
            if normalized and normalized not in seen:
                unique_documents.append(normalized)
                unique_distances.append(dist)
                seen.add(normalized)

        context = "\n\n".join(unique_documents)

        logger.debug("Retrieved Documents:")
        for i, doc in enumerate(unique_documents, start=1):
            logger.debug("Chunk %s: %s...", i, doc[:200].replace('\n', ' '))

        logger.debug("Similarity Scores:")
        for idx, score in enumerate(unique_distances, start=1):
            logger.debug("Chunk %s: %s", idx, score)

        return context



    def generate_answer(
        self,
        collection_name,
        query
    ):

        context = self.retrieve_context(
            collection_name,
            query
        )


        prompt = f"""
        Context:
        {context}

        Question:
        {query}

        Answer using only the given context.
        """


        response = self.llm.generate_response(
            prompt
        )


        return response