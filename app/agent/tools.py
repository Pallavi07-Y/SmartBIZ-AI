import re
from pathlib import Path

from app.business.business_manager import BusinessController
from app.rag.rag_pipeline import RAGPipeline
from app.business.upload_documents import DocumentUploader
from app.business.catalog import BusinessCatalog
from app.utils.helpers import generate_order_id, validate_phone_number
from app.utils.logger import logger
from app.database import Business, CartItem, Order, Product, configure_database, get_session
from app.notifications.notification_service import create_notification


class AgentTools:

    def __init__(self):

        self.business = BusinessController()
        self.rag = RAGPipeline()
        configure_database()

    @staticmethod
    def _validated_business_id(business_id):
        if (
            not isinstance(business_id, str)
            or not business_id.strip()
            or len(business_id) > 128
            or not re.fullmatch(r"[A-Za-z0-9_.-]+", business_id.strip())
        ):
            raise ValueError("A valid business ID is required.")
        return business_id.strip()

    @staticmethod
    def _validated_text(value, name, *, max_length=500, required=True):
        if value is None and not required:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{name} must be text.")
        value = value.strip()
        if required and not value:
            raise ValueError(f"{name} is required.")
        if len(value) > max_length:
            raise ValueError(f"{name} is too long.")
        return value


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

        business_id = self._validated_business_id(business_id)
        query = self._validated_text(query, "Knowledge query", max_length=2000)
        collection_name = f"business_{business_id}"

        logger.debug("AgentTools.get_business_context: collection=%s query=%s", collection_name, query[:120].strip())

        session = get_session()
        try:
            if session.query(Business.business_id).filter(
                Business.business_id == business_id
            ).first() is None:
                return "No business information found."
        finally:
            session.close()

        try:
            context = self.rag.retrieve_context(
                collection_name,
                query
            )
        except Exception:
            logger.exception("AgentTools.get_business_context: Chroma retrieval failed.")
            raise

        if context == "No business information found.":
            collection_exists = self.rag.retriever.vector_store.db.collection_exists(collection_name)
            collection_empty = (
                not collection_exists
                or self.rag.retriever.vector_store.db.get_collection(collection_name).count() == 0
            )
            folder_path = Path("data/documents") / business_id
            if collection_empty and folder_path.is_dir():
                uploader = DocumentUploader(
                    vector_store=self.rag.retriever.vector_store
                )
                for document_path in folder_path.iterdir():
                    if document_path.is_file():
                        uploader.upload(business_id, document_path)
                context = self.rag.retrieve_context(collection_name, query)

        logger.debug("AgentTools.get_business_context result length=%s", len(context) if isinstance(context, str) else 0)

        return context

    def get_live_products(self, business_id, query=None, *, category=None, available_only=False, limit=20):
        """Return current catalog values directly from SQLite for one business."""
        business_id = self._validated_business_id(business_id)
        query = self._validated_text(query, "Product query", required=False)
        category = self._validated_text(category, "Product category", required=False)
        if not isinstance(available_only, bool):
            raise ValueError("available_only must be a boolean.")
        if limit is not None and (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 1000
        ):
            raise ValueError("Product result limit must be between 1 and 1000.")

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

    def get_marketplace_catalog(self, limit=250):
        """Return active products with current shop data and recorded order counts."""
        session = get_session()
        try:
            products = (
                session.query(Product, Business)
                .join(Business, Business.business_id == Product.business_id)
                .filter(Product.is_active.is_(True))
                .order_by(Product.product_name.asc())
                .limit(limit)
                .all()
            )
            order_counts = {}
            orders = (
                session.query(Order)
                .filter(Order.status.notin_(("CANCELLED", "CANCELED")))
                .all()
            )
            for order in orders:
                for item in order.items if isinstance(order.items, list) else []:
                    if not isinstance(item, dict) or not item.get("product_name"):
                        continue
                    key = (order.business_id, str(item["product_name"]).casefold())
                    order_counts[key] = order_counts.get(key, 0) + 1

            return [
                {
                    "id": product.id,
                    "business_id": business.business_id,
                    "shop_name": business.name,
                    "shop_category": business.category,
                    "shop_address": business.address,
                    "product_name": product.product_name,
                    "brand": product.brand,
                    "category": product.category,
                    "description": product.description,
                    "price": product.price,
                    "stock_quantity": max(0, product.stock_quantity or 0),
                    "availability": product.availability or (
                        "In Stock" if (product.stock_quantity or 0) > 0 else "Out of Stock"
                    ),
                    "recorded_orders": order_counts.get(
                        (business.business_id, product.product_name.casefold()), 0
                    ),
                }
                for product, business in products
            ]
        finally:
            session.close()

    def get_catalog(self, business_id):

        business_id = self._validated_business_id(business_id)
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

        business_id = self._validated_business_id(business_id)
        query = self._validated_text(query, "Product query", max_length=2000)
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

        business_id = self._validated_business_id(business_id)
        product_name = self._validated_text(product_name, "Product name", max_length=200)
        catalog = self.get_catalog(business_id)
        return catalog.find_product(product_name)

    def recommend_products(self, business_id, query):

        return self.search_products(business_id, query)

    def compare_products(self, business_id, product_names):

        catalog = self.get_catalog(business_id)
        products = [catalog.find_product(name) for name in product_names]
        return [product for product in products if product]

    def add_to_cart(self, session_id, business_id, product_name, quantity=1):

        session_id = self._validated_text(session_id, "Customer session ID", max_length=200)
        business_id = self._validated_business_id(business_id)
        product_name = self._validated_text(product_name, "Product name", max_length=200)
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return {"error": "Quantity must be a positive whole number."}

        session = get_session()
        try:
            product = session.query(Product).filter(
                Product.business_id == business_id,
                Product.product_name == product_name,
                Product.is_active.is_(True),
            ).first()
            if product is None:
                return {"error": "Product not found."}
            stock_quantity = max(0, product.stock_quantity or 0)
            if stock_quantity == 0:
                return {"error": "Product is currently out of stock."}

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
            requested_quantity = quantity + (existing.quantity if existing else 0)
            if requested_quantity > stock_quantity:
                return {"error": "There is not enough stock to add that quantity to the cart."}
            if existing:
                existing.quantity = requested_quantity
                existing.price = product.price
                existing.availability = product.availability
            else:
                session.add(CartItem(
                    session_id=session_id,
                    business_id=business_id,
                    product_name=product.product_name,
                    quantity=quantity,
                    price=product.price,
                    availability=product.availability,
                ))
            session.commit()
            return {"cart": self._get_cart(session, session_id, business_id)}
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def remove_from_cart(self, session_id, product_name, business_id):

        session_id = self._validated_text(session_id, "Customer session ID", max_length=200)
        product_name = self._validated_text(product_name, "Product name", max_length=200)
        business_id = self._validated_business_id(business_id)
        session = get_session()
        try:
            items = session.query(CartItem).filter_by(
                session_id=session_id,
                business_id=business_id,
            ).all()
            for item in items:
                if item.product_name.lower() == product_name.lower():
                    session.delete(item)
            session.commit()
            return {"cart": self._get_cart(session, session_id, business_id)}
        finally:
            session.close()

    def clear_cart(self, session_id, business_id):

        session_id = self._validated_text(session_id, "Customer session ID", max_length=200)
        business_id = self._validated_business_id(business_id)
        session = get_session()
        try:
            session.query(CartItem).filter_by(
                session_id=session_id,
                business_id=business_id,
            ).delete()
            session.commit()
            return {"cart": []}
        finally:
            session.close()

    def get_cart(self, session_id, business_id):

        session_id = self._validated_text(session_id, "Customer session ID", max_length=200)
        business_id = self._validated_business_id(business_id)
        session = get_session()
        try:
            return self._get_cart(session, session_id, business_id)
        finally:
            session.close()

    @staticmethod
    def _cart_item_to_dict(item, product=None):
        return {
            "product_name": item.product_name,
            "quantity": item.quantity,
            "price": product.price if product is not None else None,
            "availability": (
                product.availability or (
                    "In Stock" if (product.stock_quantity or 0) > 0 else "Out of Stock"
                )
                if product is not None
                else "Unavailable"
            ),
            "stock_quantity": max(0, product.stock_quantity or 0) if product is not None else 0,
        }

    def _get_cart(self, session, session_id, business_id=None):
        if business_id is None:
            raise ValueError("A business ID is required to access a cart.")
        query = session.query(CartItem).filter_by(session_id=session_id)
        query = query.filter(CartItem.business_id == business_id)
        items = query.all()
        current_cart = []
        for item in items:
            product = session.query(Product).filter(
                Product.business_id == business_id,
                Product.product_name == item.product_name,
                Product.is_active.is_(True),
            ).first()
            current_cart.append(self._cart_item_to_dict(item, product))
        return current_cart

    def create_order(
        self,
        business_id,
        customer_name,
        phone,
        product_name,
        quantity=1,
        customer_session_id=None,
        customer_address=None,
        payment_method=None,
        product_id=None,
        confirmed=False,
    ):

        try:
            business_id = self._validated_business_id(business_id)
            customer_name = self._validated_text(customer_name, "Customer name", max_length=200)
            product_name = self._validated_text(product_name, "Product name", max_length=200)
            phone = self._validated_text(phone, "Phone number", max_length=32)
            customer_address = self._validated_text(
                customer_address,
                "Customer address",
                max_length=1000,
                required=False,
            )
            if customer_session_id is not None:
                customer_session_id = self._validated_text(
                    customer_session_id,
                    "Customer session ID",
                    max_length=200,
                )
        except ValueError as exc:
            return {"error": str(exc)}

        if not validate_phone_number(phone):
            return {"error": "Invalid phone number."}

        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return {"error": "Quantity must be a positive whole number."}
        if payment_method is None:
            return {"error": "Payment method is required. Choose COD or EMI."}
        payment_method = str(payment_method).strip().upper()
        if payment_method not in {"COD", "EMI"}:
            return {"error": "Unsupported payment method. Choose COD or EMI."}
        if confirmed is not True:
            return {"error": "Explicit customer confirmation is required before placing an order."}
        if product_id is not None and (
            isinstance(product_id, bool)
            or not isinstance(product_id, int)
            or product_id <= 0
        ):
            return {"error": "Product ID must be a positive integer."}

        order_id = generate_order_id()
        session = get_session()
        try:
            product_query = session.query(Product).filter(
                Product.business_id == business_id,
                Product.is_active.is_(True),
            )
            if product_id is not None:
                product_query = product_query.filter(Product.id == product_id)
            else:
                product_query = product_query.filter(Product.product_name == product_name)
            inventory_product = product_query.with_for_update().first()
            if inventory_product is None:
                return {"error": "Product is no longer available in this shop."}
            if inventory_product.price is None:
                return {"error": "Price information is unavailable for this product."}
            available_stock = max(0, inventory_product.stock_quantity or 0)
            if available_stock == 0:
                return {"error": f"Sorry, {inventory_product.product_name} is currently out of stock."}
            if available_stock < quantity:
                return {"error": "There is not enough stock to complete this order."}
            price = inventory_product.price
            inventory_product.stock_quantity -= quantity
            inventory_product.availability = "Out of Stock" if inventory_product.stock_quantity <= 0 else "In Stock"
            product = {
                "product_name": inventory_product.product_name,
                "availability": inventory_product.availability,
            }

            order_items = [{
                "product_name": product["product_name"],
                "quantity": quantity,
                "price": price,
                "availability": inventory_product.availability,
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
                payment_method=payment_method,
                stock_deducted=True,
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
            if inventory_product.stock_quantity <= inventory_product.low_stock_threshold:
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

        business_id = self._validated_business_id(business_id)
        order_id = self._validated_text(order_id, "Order ID", max_length=128)
        if customer_session_id is not None:
            customer_session_id = self._validated_text(
                customer_session_id,
                "Customer session ID",
                max_length=200,
            )
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

        business_id = self._validated_business_id(business_id)
        if customer_session_id is not None:
            customer_session_id = self._validated_text(
                customer_session_id,
                "Customer session ID",
                max_length=200,
            )
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
            "payment_method": order.payment_method,
        }
