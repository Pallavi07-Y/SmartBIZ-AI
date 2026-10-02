import re

from app.database import Product, get_session
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker


class BusinessCatalog:

    DEFAULT_PRODUCTS = [
        {
            "product_name": "Samsung Galaxy S25",
            "brand": "Samsung",
            "price": 75000,
            "price_text": "₹75,000",
            "ram": "12GB",
            "storage": "256GB",
            "processor": "Snapdragon 8 Elite",
            "availability": "In Stock",
            "category": "smartphone",
        },
        {
            "product_name": "Samsung Galaxy S25 Ultra",
            "brand": "Samsung",
            "price": 120000,
            "price_text": "₹1,20,000",
            "ram": "12GB",
            "storage": "512GB",
            "processor": "Snapdragon 8 Elite",
            "availability": "In Stock",
            "category": "smartphone",
        },
        {
            "product_name": "iPhone 16",
            "brand": "Apple",
            "price": 80000,
            "price_text": "₹80,000",
            "storage": "256GB",
            "processor": "Apple A18 Chip",
            "availability": "In Stock",
            "category": "smartphone",
        },
        {
            "product_name": "OnePlus 13",
            "brand": "OnePlus",
            "price": 65000,
            "price_text": "₹65,000",
            "ram": "12GB",
            "storage": "256GB",
            "processor": "Snapdragon 8 Elite",
            "availability": "In Stock",
            "category": "smartphone",
        },
        {
            "product_name": "Nothing Phone 3",
            "brand": "Nothing",
            "price": 52000,
            "price_text": "₹52,000",
            "ram": "12GB",
            "storage": "256GB",
            "processor": "Snapdragon Processor",
            "availability": "In Stock",
            "category": "smartphone",
        },
    ]

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

            self.products = self._parse_products()
            if not self.products:
                self.products = [dict(product) for product in self.DEFAULT_PRODUCTS]
                for product in self.products:
                    product["business_id"] = self.business_id

        self.faq_items = self._parse_faqs()
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

    def _parse_price(self, value):
        if not value:
            return None
        match = re.search(r"([0-9][0-9,]*)", value.replace("₹", ""))
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    def _parse_number(self, value):
        if not value:
            return None
        match = re.search(r"([0-9]+)", value.replace(",", ""))
        if not match:
            return None
        return int(match.group(1))

    def _parse_products(self):
        products = []
        current = None
        current_category = None

        for document in self.documents:
            lines = [line.strip() for line in document.page_content.splitlines()]

            for line in lines:
                if not line:
                    continue

                lower = line.lower()

                if lower.startswith("category:"):
                    current_category = line.split(":", 1)[1].strip().lower()
                    continue

                if lower.startswith("product name:"):
                    if current:
                        products.append(current)

                    current = {
                        "business_id": self.business_id,
                        "category": current_category or "product"
                    }
                    current["product_name"] = line.split(":", 1)[1].strip()
                    continue

                if current and ":" in line:
                    key, value = line.split(":", 1)
                    normalized_key = key.strip().lower().replace(" ", "_")
                    current[normalized_key] = value.strip()
                    continue

                if current and lower.startswith("----"):
                    products.append(current)
                    current = None

            if current:
                products.append(current)
                current = None

        unique = {}
        for product in products:
            name = product.get("product_name")
            if not name:
                continue
            unique[name.lower()] = product

        for product in unique.values():
            product["price"] = self._parse_price(product.get("price") or product.get("price_range"))
            product["ram_gb"] = self._parse_number(product.get("ram"))
            product["storage_gb"] = self._parse_number(product.get("storage"))
            product["availability"] = product.get("availability", "").strip()
            product["brand"] = product.get("brand", "").strip()
            product["processor"] = product.get("processor", "").strip()
            product["price_text"] = product.get("price") and f"₹{product['price']:,}" or product.get("price_range")

        return list(unique.values())

    def _parse_faqs(self):
        faqs = []

        for chunk in self.chunks:
            text = chunk.page_content.strip()
            if chunk.metadata.get("type") == "faq" or any(term in text.lower() for term in ["q:", "a:", "do you provide", "do you offer", "can i", "how do", "what are your"]):
                faqs.append(text)

        return faqs

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

    def get_faqs(self, query=None):
        if not query:
            return self.faq_items

        keywords = [term.strip().lower() for term in re.split(r"\W+", query) if term.strip()]
        return [faq for faq in self.faq_items if all(term in faq.lower() for term in keywords)]

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
        price_text = product.get("price_text")
        if not price_text and product.get("price") is not None:
            price_text = f"₹{int(product['price']):,}"
        if price_text:
            parts.append(f"Price: {price_text}")
        if product.get("availability"):
            parts.append(f"Availability: {product['availability']}")
        if product.get("stock_quantity") is not None:
            parts.append(f"Stock Quantity: {product['stock_quantity']}")
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
