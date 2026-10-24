from app.business.register_business import BusinessManager
from sqlalchemy import or_

from app.utils.auth import hash_password, normalize_email, normalize_phone, verify_password
from app.business.upload_documents import DocumentUploader
from app.business.search_business import BusinessSearch
from app.database import Business, KnowledgeDocument, Product, get_session


class BusinessController:

    def __init__(self):

        self.register = BusinessManager()

        self.uploader = DocumentUploader()

        self.search = BusinessSearch()


    def create_business(
        self,
        name,
        category,
        address=None,
        owner_phone=None,
        username=None,
        password=None,
        owner_name=None,
        business_id=None,
        latitude=None,
        longitude=None,
        owner_email=None,
        email_verified=True,
    ):

        return self.register.register_business(
            name,
            category,
            address=address,
            owner_phone=owner_phone,
            username=username,
            password_hash=hash_password(password) if password else None,
            owner_name=owner_name,
            business_id=business_id,
            latitude=latitude,
            longitude=longitude,
            owner_email=owner_email,
            email_verified=email_verified,
        )

    def authenticate_business(self, identifier, password):
        if not identifier or not password:
            return None

        identifier = str(identifier).strip()
        normalized_email = normalize_email(identifier)
        session = get_session()
        try:
            account_filters = [Business.business_id == identifier]
            if normalized_email:
                account_filters.append(Business.owner_email == normalized_email)
            business = session.query(Business).filter(or_(*account_filters)).first()
            if business is None:
                return None
            if (
                not business.email_verified
                or not business.password_hash
                or not verify_password(password, business.password_hash)
            ):
                return None
            return {
                "business_id": business.business_id,
                "name": business.name,
                "owner_name": business.owner_name,
                "category": business.category,
                "address": business.address,
                "latitude": business.latitude,
                "longitude": business.longitude,
                "location_precision": business.location_precision,
                "role": "shopkeeper",
            }
        finally:
            session.close()

    def normalize_phone(self, phone):
        return normalize_phone(phone)


    def add_document(
        self,
        business_id,
        file_path
    ):

        return self.uploader.upload(
            business_id,
            file_path
        )

    def add_product(self, business_id, product_name, price=None, stock_quantity=0, category=None, brand=None, sku=None,
                    description=None, availability=None, ram_gb=None, storage_gb=None, processor=None,
                    low_stock_threshold=None):
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
                    low_stock_threshold=2 if low_stock_threshold is None else max(0, int(low_stock_threshold)),
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
                if low_stock_threshold is not None:
                    product.low_stock_threshold = max(0, int(low_stock_threshold))
                if product.availability is None and (product.stock_quantity or 0) > 0:
                    product.availability = "In Stock"
            session.commit()
            return {
                "business_id": business_id,
                "product_name": product.product_name,
                "price": product.price,
                "stock_quantity": product.stock_quantity,
                "low_stock_threshold": product.low_stock_threshold,
                "availability": product.availability,
            }
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_products(self, business_id, include_inactive=False):
        session = get_session()
        try:
            query = session.query(Product).filter(Product.business_id == business_id)
            if not include_inactive:
                query = query.filter(Product.is_active.is_(True))
            return [
                {
                    "id": product.id,
                    "business_id": product.business_id,
                    "product_name": product.product_name,
                    "sku": product.sku,
                    "category": product.category,
                    "brand": product.brand,
                    "price": product.price,
                    "stock_quantity": product.stock_quantity,
                    "low_stock_threshold": product.low_stock_threshold,
                    "availability": product.availability,
                    "description": product.description,
                    "ram_gb": product.ram_gb,
                    "storage_gb": product.storage_gb,
                    "processor": product.processor,
                    "is_active": product.is_active,
                    "created_at": product.created_at,
                    "updated_at": product.updated_at,
                }
                for product in query.order_by(Product.created_at.desc()).all()
            ]
        finally:
            session.close()

    def update_product(self, business_id, product_id, **payload):
        session = get_session()
        try:
            product = session.query(Product).filter(Product.business_id == business_id, Product.id == product_id).first()
            if product is None:
                return None
            for key, value in payload.items():
                if value is None:
                    continue
                if hasattr(product, key):
                    setattr(product, key, value)
            if product.stock_quantity is not None and product.stock_quantity <= 0:
                product.availability = "Out of Stock"
            elif product.stock_quantity is not None and product.stock_quantity > 0:
                product.availability = product.availability or "In Stock"
            session.commit()
            return self.list_products(business_id)
        finally:
            session.close()

    def deactivate_product(self, business_id, product_id):
        session = get_session()
        try:
            product = session.query(Product).filter(Product.business_id == business_id, Product.id == product_id).first()
            if product is None:
                return None
            product.is_active = False
            session.commit()
            return True
        finally:
            session.close()

    def list_knowledge_documents(self, business_id):
        session = get_session()
        try:
            documents = session.query(KnowledgeDocument).filter(KnowledgeDocument.business_id == business_id).order_by(KnowledgeDocument.updated_at.desc()).all()
            return [
                {
                    "document_id": doc.document_id,
                    "business_id": doc.business_id,
                    "document_name": doc.document_name,
                    "document_type": doc.document_type,
                    "source": doc.source,
                    "status": doc.status,
                    "chunk_count": doc.chunk_count,
                    "created_at": doc.created_at,
                    "updated_at": doc.updated_at,
                }
                for doc in documents
            ]
        finally:
            session.close()

    def find_business(
        self,
        business_id
    ):

        return self.search.search_by_id(
            business_id
        )


    def find_business_name(
        self,
        name
    ):

        return self.search.search_by_name(
            name
        )