from app.database import Product, get_session
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker


class BusinessCatalog:
    def __init__(self, business_id):

        self.business_id = business_id
        self.loader = DocumentLoader()
        self.chunker = DocumentChunker()
        self.documents = []
        self.chunks = []

        self.products = self._load_products_from_db()
        if not self.products:
            folder = f"data/documents/{business_id}/"
            try:
                self.documents = self.loader.load_folder(folder)
            except FileNotFoundError:
                self.documents = []
            except Exception:
                self.documents = []

            if self.documents:
                self.chunks = self.chunker.split_documents(self.documents)
            else:
                self.chunks = []

        self.business_info = self._parse_business_info()

    def _load_products_from_db(self):
        session = get_session()
        try:
            products = (
                session.query(Product)
                .filter(Product.business_id == self.business_id, Product.is_active.is_(True))
                .order_by(Product.created_at.desc())
                .all()
            )
            return [self._product_to_dict(product) for product in products]
        finally:
            session.close()

    @staticmethod
    def _product_to_dict(product):
        price = product.price
        availability = product.availability or ("Out of Stock" if (product.stock_quantity or 0) <= 0 else "In Stock")
        ram_value = product.ram_gb
        storage_value = product.storage_gb
        return {
            "id": product.id,
            "business_id": product.business_id,
            "product_name": product.product_name,
            "sku": product.sku,
            "category": product.category or "product",
            "brand": product.brand or "",
            "price": price,
            "price_text": f"₹{price:,}" if price is not None else None,
            "ram": f"{ram_value}GB" if ram_value is not None else None,
            "storage": f"{storage_value}GB" if storage_value is not None else None,
            "processor": product.processor or "",
            "availability": availability,
            "stock_quantity": product.stock_quantity or 0,
            "low_stock_threshold": product.low_stock_threshold or 0,
            "description": product.description or "",
            "is_active": product.is_active,
        }

    def sync_products_to_rag(self, vector_store=None):
        if not self.products:
            return {"business_id": self.business_id, "documents_added": 0, "status": "no_products"}

        from types import SimpleNamespace
        if vector_store is None:
            from app.rag.vector_store import VectorStore
            vector_store = VectorStore()

        collection_name = f"business_{self.business_id}"

        vector_store.db.delete_by_metadata(
            collection_name,
            {"$and": [{"business_id": self.business_id}, {"document_type": "product"}]},
        )

        documents = []
        for product in self.products:
            if not product.get("product_name"):
                continue
            context = self.get_product_context(product["product_name"])
            if context:
                documents.append(
                    SimpleNamespace(
                        page_content=context,
                        metadata={
                            "business_id": self.business_id,
                            "product_id": f"product_{product.get('id') or product['product_name']}",
                            "document_type": "product",
                            "type": "product",
                            "category": product.get("category") or "product",
                            "brand": product.get("brand") or "",
                        },
                    )
                )

        if not documents:
            return {"business_id": self.business_id, "documents_added": 0, "status": "no_product_context"}

        result = vector_store.add_documents(collection_name, documents, self.business_id)
        return {"business_id": self.business_id, "documents_added": len(documents), "status": result}

    def _normalize_text(self, text):
        return text.strip().replace("\r", "").replace("\n", " ").strip()

    def _parse_business_info(self):
        business_info = []

        for chunk in self.chunks:
            if chunk.metadata.get("type") == "business":
                business_info.append(chunk.page_content.strip())

        return business_info

    def get_product_names(self):
        return [product["product_name"] for product in self.products if product.get("product_name")]

    def find_product(self, product_name):
        if not product_name:
            return None

        normalized = product_name.strip().lower()
        exact = next((p for p in self.products if p.get("product_name", "").lower() == normalized), None)
        if exact:
            return exact

        fuzzy = [p for p in self.products if normalized in p.get("product_name", "").lower()]
        return fuzzy[0] if fuzzy else None

    def filter_products(self, category=None, budget=None, keywords=None, require_specs=None):
        items = [p for p in self.products if p.get("product_name")]

        if category:
            items = [p for p in items if category.lower() in p.get("category", "").lower() or category.lower() in p.get("product_name", "").lower()]

        if budget is not None:
            items = [p for p in items if p.get("price") is not None and p["price"] <= budget]

        if keywords:
            terms = [keyword.lower() for keyword in keywords]
            def matches(product):
                text = " ".join(str(product.get(k, "")) for k in ["product_name", "brand", "category", "processor", "availability"]).lower()
                return all(term in text for term in terms)
            items = [p for p in items if matches(p)]

        if require_specs:
            def qualifies(product):
                text = " ".join([str(product.get(k, "")) for k in ["product_name", "brand", "category", "processor", "notes"]]).lower()
                return any(term in text for term in require_specs)
            items = [p for p in items if qualifies(p)]

        return items

    def get_business_info_text(self):
        return "\n\n".join(self.business_info)

    def get_product_context(self, product_name):
        product = self.find_product(product_name)
        if not product:
            return None

        parts = []
        parts.append(f"Product Name: {product.get('product_name')}")
        if product.get("sku"):
            parts.append(f"SKU: {product['sku']}")
        for key in ["brand", "ram", "storage", "processor", "category", "description"]:
            value = product.get(key)
            if value:
                parts.append(f"{key.replace('_', ' ').title()}: {value}")

        return "\n".join(parts)

    def get_search_summary(self, products):
        lines = []
        for product in products:
            lines.append(f"{product.get('product_name')} - {product.get('price_text') or 'Price not listed'} - {product.get('availability') or 'Availability unknown'}")
        return "\n".join(lines)
