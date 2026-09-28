import re
from pathlib import Path

from app.business.business_manager import BusinessController
from app.rag.rag_pipeline import RAGPipeline
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker
from app.business.catalog import BusinessCatalog
from app.utils.helpers import generate_order_id, validate_phone_number
from app.utils.logger import logger
from app.database import CartItem, Order, configure_database, get_session


class AgentTools:

    def __init__(self):

        self.business = BusinessController()
        self.rag = RAGPipeline()
        configure_database()


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

        session = get_session()
        try:
            cart_items = session.query(CartItem).filter_by(
                session_id=session_id,
                business_id=business_id,
            ).all()
            existing = next(
                (
                    item for item in cart_items
                    if item.product_name.lower() == product_name.lower()
                ),
                None,
            )
            if existing:
                existing.quantity += quantity
            else:
                session.add(CartItem(
                    session_id=session_id,
                    business_id=business_id,
                    product_name=product["product_name"],
                    quantity=quantity,
                    price=product.get("price"),
                    availability=product.get("availability"),
                ))
            session.commit()
            return {"cart": self._get_cart(session, session_id)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def remove_from_cart(self, session_id, product_name):

        session = get_session()
        try:
            items = session.query(CartItem).filter_by(session_id=session_id).all()
            for item in items:
                if item.product_name.lower() == product_name.lower():
                    session.delete(item)
            session.commit()
            return {"cart": self._get_cart(session, session_id)}
        finally:
            session.close()

    def clear_cart(self, session_id):

        session = get_session()
        try:
            session.query(CartItem).filter_by(session_id=session_id).delete()
            session.commit()
            return {"cart": []}
        finally:
            session.close()

    def get_cart(self, session_id):

        session = get_session()
        try:
            return self._get_cart(session, session_id)
        finally:
            session.close()

    @staticmethod
    def _cart_item_to_dict(item):
        return {
            "product_name": item.product_name,
            "quantity": item.quantity,
            "price": item.price,
            "availability": item.availability,
        }

    def _get_cart(self, session, session_id):
        items = session.query(CartItem).filter_by(session_id=session_id).all()
        return [self._cart_item_to_dict(item) for item in items]

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
        order_items = [{
            "product_name": product["product_name"],
            "quantity": quantity,
            "price": price,
            "availability": product.get("availability"),
            "customer_name": customer_name,
        }]
        session = get_session()
        try:
            session.add(Order(
                id=order_id,
                business_id=business_id,
                customer_phone=phone,
                items=order_items,
                status="confirmed",
            ))
            session.commit()
            return self._order_to_dict(session.get(Order, order_id))
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_order_status(self, business_id, order_id):

        session = get_session()
        try:
            order = session.query(Order).filter_by(
                id=order_id,
                business_id=business_id,
            ).first()
            return self._order_to_dict(order) if order else None
        finally:
            session.close()

    def get_recent_orders(self, business_id):

        session = get_session()
        try:
            orders = session.query(Order).filter_by(
                business_id=business_id,
            ).order_by(Order.created_at).all()
            return [self._order_to_dict(order) for order in orders]
        finally:
            session.close()

    @staticmethod
    def _order_to_dict(order):
        item = order.items[0]
        quantity = item["quantity"]
        price = item["price"]
        return {
            "order_id": order.id,
            "business_id": order.business_id,
            "customer_name": item.get("customer_name"),
            "phone": order.customer_phone,
            "product_name": item["product_name"],
            "quantity": quantity,
            "price": price,
            "total": price * quantity,
            "status": order.status,
        }
