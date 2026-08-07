import re
from pathlib import Path

from app.business.business_manager import BusinessController
from app.rag.rag_pipeline import RAGPipeline
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker
from app.business.catalog import BusinessCatalog
from app.utils.helpers import generate_order_id, validate_phone_number
from app.utils.logger import logger


class AgentTools:

    def __init__(self):

        self.business = BusinessController()
        self.rag = RAGPipeline()
        self.carts = {}
        self.orders = {}


    def get_business_details(
        self,
        business_id
    ):

        return self.business.find_business(
            business_id
        )


    def search_business(
        self,
        name
    ):

        return self.business.find_business_name(
            name
        )


    def get_business_context(
        self,
        business_id,
        query
    ):

        collection_name = f"business_{business_id}"

        logger.debug("AgentTools.get_business_context: collection=%s query=%s", collection_name, query[:120].strip())

        context = None
        try:
            context = self.rag.retrieve_context(
                collection_name,
                query
            )
        except Exception as e:
            logger.debug("AgentTools.get_business_context: Chroma retrieval failed: %s", e)

        if context is None or context == "No business information found.":
            collection_exists = self.rag.retriever.vector_store.db.collection_exists(collection_name)
            collection_empty = False

            if collection_exists:
                try:
                    collection = self.rag.retriever.vector_store.db.get_collection(collection_name)
                    collection_empty = collection.count() == 0
                except Exception as e:
                    logger.debug("AgentTools.get_business_context: Unable to inspect collection contents: %s", e)

            if not collection_exists or collection_empty:
                if collection_empty:
                    logger.debug("AgentTools.get_business_context: Chroma collection exists but is empty. Attempting local fallback.")
                else:
                    logger.debug("AgentTools.get_business_context: Chroma collection not found. Attempting local fallback.")
                try:
                    loader = DocumentLoader()
                    folder_path = Path("data/documents") / business_id
                    if folder_path.exists():
                        docs = loader.load_folder(str(folder_path))
                    else:
                        docs = []

                    if docs:
                        chunker = DocumentChunker()
                        chunks = chunker.split_documents(docs)

                        document_distances = [(c.page_content, 0.1) for c in chunks]
                        filtered = self.rag._filter_documents_for_query(document_distances, query)

                        if filtered:
                            context = "\n\n".join([d for d, _ in filtered])
                except Exception as e:
                    logger.debug("AgentTools fallback error: %s", e)
            else:
                logger.debug("AgentTools.get_business_context: No relevant Chroma documents found, skipping local fallback.")

        logger.debug("AgentTools.get_business_context result length=%s", len(context) if isinstance(context, str) else 0)

        return context

    def get_catalog(self, business_id):

        return BusinessCatalog(business_id)

    def search_products(self, business_id, query):

        catalog = self.get_catalog(business_id)
        budget = None
        keywords = []
        category = None

        text = query.lower()

        if "under" in text or "below" in text:
            match = re.search(r"(?:under|below)\s*[₹$]?\s*([0-9,]+)", text)
            if match:
                budget = int(match.group(1).replace(",", ""))

        if "laptop" in text or "notebook" in text:
            category = "laptop"
        elif "phone" in text or "smartphone" in text or "mobile" in text:
            category = "smartphone"

        for term in ["gaming", "coding", "performance", "ram", "processor", "ssd", "storage", "battery"]:
            if term in text:
                keywords.append(term)

        products = catalog.filter_products(
            category=category,
            budget=budget,
            keywords=keywords
        )

        return products

    def get_product_details(self, business_id, product_name):

        catalog = self.get_catalog(business_id)
        return catalog.find_product(product_name)

    def recommend_products(self, business_id, query):

        return self.search_products(business_id, query)

    def compare_products(self, business_id, product_names):

        catalog = self.get_catalog(business_id)
        products = [catalog.find_product(name) for name in product_names]
        return [product for product in products if product]

    def add_to_cart(self, session_id, business_id, product_name, quantity=1):

        catalog = self.get_catalog(business_id)
        product = catalog.find_product(product_name)
        if not product:
            return {"error": "Product not found."}

        if "out of stock" in product.get("availability", "").lower():
            return {"error": "Product is currently out of stock."}

        if quantity < 1:
            return {"error": "Quantity must be at least 1."}

        cart = self.carts.setdefault(session_id, [])
        existing = next((item for item in cart if item["product_name"].lower() == product_name.lower()), None)
        if existing:
            existing["quantity"] += quantity
        else:
            cart.append({
                "product_name": product["product_name"],
                "quantity": quantity,
                "price": product.get("price"),
                "availability": product.get("availability")
            })

        return {"cart": cart}

    def remove_from_cart(self, session_id, product_name):

        cart = self.carts.get(session_id, [])
        cart = [item for item in cart if item["product_name"].lower() != product_name.lower()]
        self.carts[session_id] = cart
        return {"cart": cart}

    def clear_cart(self, session_id):

        self.carts[session_id] = []
        return {"cart": []}

    def get_cart(self, session_id):

        return self.carts.get(session_id, [])

    def create_order(self, business_id, customer_name, phone, product_name, quantity=1):

        if not validate_phone_number(phone):
            return {"error": "Invalid phone number."}

        catalog = self.get_catalog(business_id)
        product = catalog.find_product(product_name)
        if not product:
            return {"error": "Product not found."}

        if "out of stock" in product.get("availability", "").lower():
            return {"error": "Product is currently out of stock."}

        price = product.get("price")
        if price is None:
            return {"error": "Price information is unavailable for this product."}

        if quantity < 1:
            return {"error": "Quantity must be at least 1."}

        order_id = generate_order_id()
        total = price * quantity
        order = {
            "order_id": order_id,
            "business_id": business_id,
            "customer_name": customer_name,
            "phone": phone,
            "product_name": product["product_name"],
            "quantity": quantity,
            "price": price,
            "total": total,
            "status": "confirmed"
        }

        self.orders.setdefault(business_id, []).append(order)
        return order

    def get_order_status(self, business_id, order_id):

        business_orders = self.orders.get(business_id, [])
        return next((order for order in business_orders if order["order_id"] == order_id), None)

    def get_recent_orders(self, business_id):

        return self.orders.get(business_id, [])
