"""
chunking.py

Splits documents into smaller chunks
before generating embeddings.
"""

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


class DocumentChunker:

    def __init__(self):

        self.text_splitter = RecursiveCharacterTextSplitter(

            chunk_size=settings.CHUNK_SIZE,

            chunk_overlap=settings.CHUNK_OVERLAP,

            separators=[
                "\n\n",
                "\n",
                ". ",
                " ",
                ""
            ]
        )

        self.business_terms = [
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
            "about our shop"
        ]

        self.faq_terms = [
            "q:",
            "a:",
            "do you provide",
            "do you offer",
            "do you sell",
            "how do",
            "can i",
            "what are your",
            "what products do you sell"
        ]

        self.product_terms = [
            "brand:",
            "product name:",
            "price:",
            "price range:",
            "ram:",
            "storage:",
            "processor:",
            "display:",
            "battery:",
            "availability:",
            "category:",
            "samsung",
            "iphone",
            "oneplus",
            "nothing",
            "vivo",
            "realme",
            "xiaomi",
            "oppo",
            "accessories",
            "mobile covers",
            "screen guards",
            "chargers",
            "fast chargers",
            "power banks",
            "laptop",
            "notebook",
            "intel",
            "amd",
            "ryzen",
            "core",
            "ssd",
            "gpu"
        ]

        self.smartphone_brands = [
            "samsung",
            "iphone",
            "oneplus",
            "nothing",
            "vivo",
            "realme",
            "xiaomi",
            "oppo"
        ]

        self.laptop_terms = [
            "laptop",
            "notebook",
            "intel",
            "amd",
            "ryzen",
            "core",
            "processor",
            "ssd",
            "gpu",
            "graphics",
            "ram",
            "storage",
            "battery"
        ]

    def _classify_chunk(self, chunk):

        text = chunk.page_content.lower()
        metadata = dict(chunk.metadata or {})

        if any(term in text for term in self.business_terms):
            metadata["type"] = "business"

        elif any(term in text for term in self.faq_terms):
            metadata["type"] = "faq"

        elif any(term in text for term in self.product_terms):
            metadata["type"] = "product"

            explicit_category_keywords = [
                ("laptop", "laptop"),
                ("notebook", "laptop"),
                ("smartphone", "smartphone"),
                ("phone", "smartphone"),
            ]
            for keyword, category in explicit_category_keywords:
                if keyword in text:
                    metadata["category"] = category
                    break
            else:
                # Literal product keywords must outrank generic hardware hints like RAM/SSD,
                # while preserving brand-based smartphone detection for names such as
                # "Samsung Galaxy S25".
                smartphone_identifiers = self.smartphone_brands + [
                    "galaxy",
                    "pixel",
                ]
                if any(identifier in text for identifier in smartphone_identifiers):
                    metadata["category"] = "smartphone"
                elif any(term in text for term in self.laptop_terms):
                    metadata["category"] = "laptop"
                elif "mobile covers" in text or "screen guards" in text or "chargers" in text or "power banks" in text:
                    metadata["category"] = "accessories"
                elif any(term in text for term in ["ram", "gb", "ssd"]):
                    metadata["category"] = "laptop"
                else:
                    metadata.setdefault("category", "product")

        else:
            # Default to product for content-rich chunks and business only for pure profile blocks
            metadata["type"] = "product"
            metadata.setdefault("category", "product")

        chunk.metadata = metadata

        return chunk

    def split_documents(self, documents):

        chunks = self.text_splitter.split_documents(documents)

        return [
            self._classify_chunk(chunk)
            for chunk in chunks
        ]

    def split_text(self, text):

        chunks = self.text_splitter.create_documents([text])

        return [
            self._classify_chunk(chunk)
            for chunk in chunks
        ]