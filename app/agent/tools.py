import re
from pathlib import Path

from app.business.business_manager import BusinessController
from app.rag.rag_pipeline import RAGPipeline
from app.rag.document_loader import DocumentLoader
from app.rag.chunking import DocumentChunker
from app.business.catalog import BusinessCatalog
from app.utils.helpers import generate_order_id, validate_phone_number
from app.utils.logger import logger
from app.database import CartItem, Order, Product, configure_database, get_session
from app.notifications.notification_service import create_notification


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

    def get_live_products(self, business_id, query=None, *, category=None, available_only=False, limit=20):
        """Return current catalog values directly from SQLite for one business."""
        if not business_id:
            return []

        session = get_session()
        try:
            product_query = session.query(Product).filter(
                Product.business_id == business_id,
                Product.is_active.is_(True),
            )
            if category:
                product_query = product_query.filter(Product.category.ilike(f"%{category}%"))
            if query and query.strip():
                term = f"%{query.strip()}%"
                product_query = product_query.filter(
                    Product.product_name.ilike(term)
                    | Product.brand.ilike(term)
                    | Product.category.ilike(term)
                    | Product.description.ilike(term)
                )
            if available_only:
                product_query = product_query.filter(Product.stock_quantity > 0)
            products = product_query.order_by(Product.product_name.asc()).limit(limit).all()
            return [
                {
                    "id": product.id,
                    "business_id": product.business_id,
                    "product_name": product.product_name,
                    "category": product.category,
                    "brand": product.brand,
                    "description": product.description,
                    "price": product.price,
                    "stock_quantity": max(0, product.stock_quantity or 0),
                    "availability": product.availability or ("Out of Stock" if (product.stock_quantity or 0) <= 0 else "In Stock"),
                    "ram_gb": product.ram_gb,
                    "storage_gb": product.storage_gb,
                    "processor": product.processor,
                }
                for product in products
            ]
        finally:
            session.close()

    def get_catalog(self, business_id):

        return BusinessCatalog(business_id)

    def add_product(self, business_id, product_name, price=None, stock_quantity=0, category=None, brand=None, sku=None,
                    description=None, availability=None, ram_gb=None, storage_gb=None, processor=None):
        session = get_session()
        try:
            product = session.query(Product).filter(Product.business_id == business_id, Product.product_name == product_name).first()
            if product is None:
                product = Product(
                    business_id=business_id,
                    product_name=product_name,
                    sku=sku,
                    category=category,
                    brand=brand,
                    price=price,
                    ram_gb=ram_gb,
                    storage_gb=storage_gb,
                    processor=processor,
                    description=description,
                    availability=availability or ("Out of Stock" if (stock_quantity or 0) <= 0 else "In Stock"),
                    stock_quantity=stock_quantity or 0,
                    is_active=True,
                )
                session.add(product)
            else:
                if price is not None:
                    product.price = price
                if stock_quantity is not None:
                    product.stock_quantity = stock_quantity
                if category is not None:
                    product.category = category
                if brand is not None:
                    product.brand = brand
                if sku is not None:
                    product.sku = sku
                if description is not None:
                    product.description = description
                if availability is not None:
                    product.availability = availability
                if ram_gb is not None:
                    product.ram_gb = ram_gb
                if storage_gb is not None:
                    product.storage_gb = storage_gb
                if processor is not None:
                    product.processor = processor
                if product.availability is None and (product.stock_quantity or 0) > 0:
                    product.availability = "In Stock"
            session.commit()
            return self.get_product_details(business_id, product_name)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_products(self, business_id):
        catalog = self.get_catalog(business_id)
        return catalog.products

    def update_stock(self, business_id, product_name, stock_quantity):
        session = get_session()
        try:
            product = session.query(Product).filter(Product.business_id == business_id, Product.product_name == product_name).first()
            if product is None:
                return None
            product.stock_quantity = max(0, int(stock_quantity))
            product.availability = "Out of Stock" if product.stock_quantity <= 0 else "In Stock"
            session.commit()
            return self.get_product_details(business_id, product_name)
        finally:
            session.close()

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
            return {"cart": self._get_cart(session, session_id, business_id)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def remove_from_cart(self, session_id, product_name, business_id=None):

        session = get_session()
        try:
            query = session.query(CartItem).filter_by(session_id=session_id)
            if business_id:
                query = query.filter(CartItem.business_id == business_id)
            items = query.all()
            for item in items:
                if item.product_name.lower() == product_name.lower():
                    session.delete(item)
            session.commit()
            return {"cart": self._get_cart(session, session_id, business_id)}
        finally:
            session.close()

    def clear_cart(self, session_id, business_id=None):

        session = get_session()
        try:
            query = session.query(CartItem).filter_by(session_id=session_id)
            if business_id:
                query = query.filter(CartItem.business_id == business_id)
            query.delete()
            session.commit()
            return {"cart": []}
        finally:
            session.close()

    def get_cart(self, session_id, business_id=None):

        session = get_session()
        try:
            return self._get_cart(session, session_id, business_id)
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

    def _get_cart(self, session, session_id, business_id=None):
        query = session.query(CartItem).filter_by(session_id=session_id)
        if business_id:
            query = query.filter(CartItem.business_id == business_id)
        items = query.all()
        return [self._cart_item_to_dict(item) for item in items]

    def create_order(
        self,
        business_id,
        customer_name,
        phone,
        product_name,
        quantity=1,
        customer_session_id=None,
        customer_address=None,
    ):

        if not validate_phone_number(phone):
            return {"error": "Invalid phone number."}

        catalog = self.get_catalog(business_id)
        product = catalog.find_product(product_name)
        if not product:
            return {"error": "Product not found."}

        if "out of stock" in product.get("availability", "").lower():
            return {"error": "Product is currently out of stock."}

        if quantity < 1:
            return {"error": "Quantity must be at least 1."}

        order_id = generate_order_id()
        session = get_session()
        try:
            inventory_product = None
            if customer_session_id:
                inventory_product = (
                    session.query(Product)
                    .filter(
                        Product.business_id == business_id,
                        Product.product_name.ilike(product["product_name"]),
                        Product.is_active.is_(True),
                    )
                    .with_for_update()
                    .first()
                )
                if inventory_product is None:
                    return {"error": "Product is no longer available in this shop."}
                if inventory_product.price is None:
                    return {"error": "Price information is unavailable for this product."}
                if inventory_product.stock_quantity < quantity:
                    return {"error": "There is not enough stock to complete this order."}
                price = inventory_product.price
                inventory_product.stock_quantity -= quantity
                inventory_product.availability = "Out of Stock" if inventory_product.stock_quantity <= 0 else "In Stock"
            else:
                price = product.get("price")
                if price is None:
                    return {"error": "Price information is unavailable for this product."}

            order_items = [{
                "product_name": product["product_name"],
                "quantity": quantity,
                "price": price,
                "availability": inventory_product.availability if inventory_product else product.get("availability"),
                "customer_name": customer_name,
            }]
            session.add(Order(
                id=order_id,
                business_id=business_id,
                customer_session_id=customer_session_id,
                customer_phone=phone,
                customer_address=customer_address,
                items=order_items,
                status="NEW",
                stock_deducted=inventory_product is not None,
            ))
            session.commit()
            order = self._order_to_dict(session.get(Order, order_id))
            create_notification(
                business_id,
                "NEW_ORDER",
                "New Order Received",
                f"Order: {order_id}\nCustomer: {customer_name}\nItems: {product['product_name']} x{quantity}\nTotal: ₹{order['total']:,}\nStatus: NEW",
                reference_type="ORDER",
                reference_id=order_id,
            )
            if inventory_product and inventory_product.stock_quantity <= inventory_product.low_stock_threshold:
                create_notification(
                    business_id,
                    "LOW_STOCK" if inventory_product.stock_quantity else "OUT_OF_STOCK",
                    f"Low stock: {inventory_product.product_name}" if inventory_product.stock_quantity else f"Out of stock: {inventory_product.product_name}",
                    f"{inventory_product.product_name} has {inventory_product.stock_quantity} unit(s) remaining.",
                    reference_type="PRODUCT",
                    reference_id=str(inventory_product.id),
                )
            return order
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_order_status(self, business_id, order_id, customer_session_id=None):

        session = get_session()
        try:
            query = session.query(Order).filter_by(id=order_id, business_id=business_id)
            if customer_session_id:
                query = query.filter(Order.customer_session_id == customer_session_id)
            order = query.first()
            return self._order_to_dict(order) if order else None
        finally:
            session.close()

    def get_recent_orders(self, business_id, customer_session_id=None):

        session = get_session()
        try:
            query = session.query(Order).filter_by(business_id=business_id)
            if customer_session_id:
                query = query.filter(Order.customer_session_id == customer_session_id)
            orders = query.order_by(Order.created_at.desc()).all()
            return [self._order_to_dict(order) for order in orders]
        finally:
            session.close()

    @staticmethod
    def _order_to_dict(order):
        item = order.items[0] if isinstance(order.items, list) and order.items else {}
        quantity = item.get("quantity", 1)
        price = item.get("price", 0)
        status = str(order.status or "NEW").upper()
        return {
            "order_id": order.id,
            "business_id": order.business_id,
            "customer_session_id": order.customer_session_id,
            "customer_name": item.get("customer_name"),
            "phone": order.customer_phone,
            "product_name": item.get("product_name"),
            "quantity": quantity,
            "price": price,
            "total": price * quantity,
            "status": status,
        }
