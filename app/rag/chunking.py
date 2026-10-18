import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.config import settings


class DocumentChunker:
    SECTION_HEADER = re.compile(
        r"^(?:#{1,6}\s+(.+?)\s*#*|([A-Za-z][A-Za-z0-9 &()/,_-]{1,78}):)\s*$"
    )
    PRODUCT_CATEGORY = re.compile(
        r"\b(?:smart\s*phones?|mobiles?|iphones?|galax(?:y|ies)|oneplus)\b",
        re.IGNORECASE,
    )
    LAPTOP_CATEGORY = re.compile(
        r"\b(?:laptops?|notebooks?|vivobook|thinkpad|macbook|ssd\s+upgrade)\b",
        re.IGNORECASE,
    )

    def __init__(self):
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=settings.CHUNK_SIZE,
            chunk_overlap=settings.CHUNK_OVERLAP,
            separators=["\n\n", "\n", ". ", " ", ""],
        )

    @classmethod
    def _split_sections(cls, document):
        sections = []
        current_heading = None
        current_lines = []

        def add_section():
            text = "\n".join(current_lines).strip()
            if text:
                sections.append((current_heading, text))

        for line in document.page_content.splitlines():
            match = cls.SECTION_HEADER.fullmatch(line.strip())
            heading = (match.group(1) or match.group(2)).strip() if match else None
            if heading and heading.casefold() not in {"q", "a"}:
                add_section()
                current_heading = heading
                current_lines = [heading + ":"]
            else:
                current_lines.append(line)

        add_section()
        return sections or [(None, document.page_content.strip())]

    @staticmethod
    def _classify_chunk(chunk):
        section = str(chunk.metadata.get("section") or "").casefold()
        text = chunk.page_content.casefold()
        is_product = section in {"product", "products", "product knowledge", "product categories"}
        is_product = is_product or any(
            term in text for term in ("product name:", "brand:", "processor:", "storage:")
        )
        if is_product:
            chunk.metadata["type"] = "product"
            if not chunk.metadata.get("category"):
                if DocumentChunker.PRODUCT_CATEGORY.search(text):
                    chunk.metadata["category"] = "smartphone"
                elif DocumentChunker.LAPTOP_CATEGORY.search(text) or (
                    "ssd" in text and "ram" in text
                ):
                    chunk.metadata["category"] = "laptop"
                else:
                    chunk.metadata["category"] = "product"
        else:
            chunk.metadata["type"] = "business"
        return chunk

    def split_documents(self, documents):
        chunks = []
        for document in documents:
            for section, text in self._split_sections(document):
                metadata = dict(document.metadata or {})
                if section:
                    metadata["section"] = section
                for chunk_text in self.text_splitter.split_text(text):
                    chunks.append(
                        self._classify_chunk(
                            Document(page_content=chunk_text, metadata=dict(metadata))
                        )
                    )
        return chunks

    def split_text(self, text):
        return self.split_documents([Document(page_content=text)])
