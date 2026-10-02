"""Seed the canonical 10-business SmartBiz demo knowledge dataset.

This module intentionally reuses the existing database models, document uploader,
chunking, and ChromaDB path instead of creating a separate RAG system.
"""

from pathlib import Path

import app.business.upload_documents as upload_module
from app.business.catalog import BusinessCatalog
from app.database import Business, Product, get_session


DEMO_SHOPS = [
    {
        "business_id": "SB-ABC001",
        "name": "ABC Mobiles",
        "category": "Mobile & Accessories",
        "location": "Visakhapatnam, Andhra Pradesh",
        "username": "abc-mobiles@smartbiz",
        "owner_phone": "9999900001",
        "products": [
            ("Samsung Galaxy S25", "S25-001", "Samsung", 75000, 18, "Premium Android smartphone with advanced camera capabilities and modern mobile features."),
            ("iPhone 16", "IPH16-001", "Apple", 82000, 12, "Premium Apple smartphone with modern ecosystem features and polished performance."),
            ("OnePlus 13", "OP13-001", "OnePlus", 68000, 15, "Performance-focused Android smartphone for power users and mobile enthusiasts."),
            ("USB-C Fast Charger", "CHG-USB-C-01", "VoltLine", 1999, 30, "Fast, compact charger for modern smartphones and tablets."),
        ],
        "document_name": "abc_mobiles.txt",
    },
    {
        "business_id": "SB-STY002",
        "name": "StyleHub Fashion",
        "category": "Fashion",
        "location": "Vijayawada, Andhra Pradesh",
        "username": "stylehub@smartbiz",
        "owner_phone": "9999900002",
        "products": [
            ("Classic Cotton Shirt", "SH-CT-001", "ThreadLine", 1499, 42, "Breathable cotton shirt in a classic fit for daily wear."),
            ("Slim Fit Denim", "SH-DN-001", "DenimCo", 2499, 32, "Straight-fit denim for casual wear with a timeless silhouette."),
            ("Women's A-line Dress", "SH-DR-001", "FableStyle", 2899, 18, "Easy-to-wear A-line dress for everyday and occasion styling."),
            ("Premium Cotton Kurta", "SH-KR-001", "StyleHub", 2799, 22, "Comfortable everyday kurta made for all-season wear."),
        ],
        "document_name": "stylehub_fashion.txt",
    },
    {
        "business_id": "SB-FRE003",
        "name": "FreshMart",
        "category": "Grocery",
        "location": "Tirupati, Andhra Pradesh",
        "username": "freshmart@smartbiz",
        "owner_phone": "9999900003",
        "products": [
            ("Organic Rice 5kg", "FR-RICE-001", "FarmPure", 649, 50, "Premium quality rice suitable for regular family meals."),
            ("Groundnut Oil 1L", "FR-OIL-001", "PureDrop", 299, 35, "Cooking oil for everyday home preparation and frying."),
            ("Classic Masala Snack Pack", "FR-SNACK-001", "FreshBite", 199, 40, "Lightly spiced snack pack for quick munching needs."),
            ("Protein Rich Flax Mix", "FR-HEALTH-001", "FreshMart", 399, 25, "Healthy blended mix for breakfast and quick nutrition."),
        ],
        "document_name": "freshmart.txt",
    },
    {
        "business_id": "SB-TEC004",
        "name": "TechZone Electronics",
        "category": "Electronics",
        "location": "Hyderabad, Telangana",
        "username": "techzone@smartbiz",
        "owner_phone": "9999900004",
        "products": [
            ("Noise Cancelling Headphones", "TZ-HP-001", "SoundArc", 5999, 16, "Wireless headphones designed for clear audio and focused listening."),
            ("USB-C Hub 7-in-1", "TZ-HUB-001", "PortWorks", 2499, 18, "Connectivity hub for laptops, tablets, and office accessories."),
            ("27-inch Monitor", "TZ-MON-001", "ViewTech", 11999, 9, "Balanced display for work, streaming, and productivity tasks."),
            ("Mechanical Keyboard", "TZ-KB-001", "TypePro", 4999, 11, "Comfortable keyboard designed for productivity and desk setups."),
        ],
        "document_name": "techzone_electronics.txt",
    },
    {
        "business_id": "SB-FIT005",
        "name": "FitLife Store",
        "category": "Fitness & Sports",
        "location": "Bengaluru, Karnataka",
        "username": "fitlife@smartbiz",
        "owner_phone": "9999900005",
        "products": [
            ("Premium Yoga Mat", "FL-MAT-001", "CoreForm", 899, 27, "Non-slip mat designed for yoga, stretching, and home workouts."),
            ("Resistance Band Set", "FL-BAND-001", "FlexCore", 1299, 20, "Training set for strength and mobility routines at home."),
            ("Adjustable Dumbbell Set", "FL-DUMB-001", "IronPeak", 4299, 12, "Compact dumbbell set for efficient home strength training."),
            ("Foam Roller", "FL-ROLL-001", "MotionFit", 799, 29, "Recovery tool for stretching and muscle comfort after workouts."),
        ],
        "document_name": "fitlife_store.txt",
    },
    {
        "business_id": "SB-HOM006",
        "name": "HomeNest",
        "category": "Home & Kitchen",
        "location": "Chennai, Tamil Nadu",
        "username": "homenest@smartbiz",
        "owner_phone": "9999900006",
        "products": [
            ("Stainless Steel Cookware Set", "HN-COOK-001", "HomeNest", 5499, 10, "Five-piece cookware set for daily kitchen preparation."),
            ("Storage Box Set", "HN-BOX-001", "OrganizePro", 1799, 25, "Functional storage solution for wardrobes and kitchen spaces."),
            ("Cotton Cushion Cover Pair", "HN-CUSH-001", "SoftWeave", 799, 21, "Machine-washable cushion covers for home styling."),
            ("Kitchen Tool Starter Kit", "HN-KT-001", "HomeNest", 1299, 18, "Practical kitchen essentials for everyday food preparation."),
        ],
        "document_name": "homenest.txt",
    },
    {
        "business_id": "SB-BOK007",
        "name": "BookWorld",
        "category": "Books & Stationery",
        "location": "Nellore, Andhra Pradesh",
        "username": "bookworld@smartbiz",
        "owner_phone": "9999900007",
        "products": [
            ("Competitive Exam Guide", "BK-EXAM-001", "StudyPrime", 599, 30, "Reference guide for competitive exam preparation and practice."),
            ("Premium Hardbound Notebook", "BK-NOTE-001", "PaperTrail", 499, 40, "A5 notebook designed for study, planning, and idea capture."),
            ("Fiction Bestsellers Bundle", "BK-FIC-001", "Literati", 899, 18, "Curated bundle of popular fiction titles for reading enjoyment."),
            ("Stationery Combo Pack", "BK-STN-001", "UrbanWrite", 449, 35, "Set of pens, clips, and writing tools for everyday study needs."),
        ],
        "document_name": "bookworld.txt",
    },
    {
        "business_id": "SB-BEA008",
        "name": "BeautyCare",
        "category": "Beauty & Personal Care",
        "location": "Bengaluru, Karnataka",
        "username": "beautycare@smartbiz",
        "owner_phone": "9999900008",
        "products": [
            ("Aloe Moisturizer 200ml", "BC-MOIST-001", "PureLeaf", 399, 26, "Daily moisturizer for soft, smooth, and hydrating skincare routines."),
            ("Gentle Face Cleanser", "BC-CLEAN-001", "PureLeaf", 329, 22, "Mild cleanser formulated for everyday facial care."),
            ("Hair Growth Serum", "BC-SERUM-001", "GlowBloom", 999, 14, "Personal care serum designed for routine haircare styling support."),
            ("Beauty Tool Kit", "BC-TOOL-001", "GlowBloom", 699, 19, "Pack of grooming and personal care accessories for daily routines."),
        ],
        "document_name": "beautycare.txt",
    },
    {
        "business_id": "SB-AUT009",
        "name": "AutoGear",
        "category": "Automobile Accessories",
        "location": "Hyderabad, Telangana",
        "username": "autogear@smartbiz",
        "owner_phone": "9999900009",
        "products": [
            ("Universal Car Phone Holder", "AG-HOLDER-001", "RoadReady", 799, 23, "Adjustable holder for dashboard or windshield use in many vehicles."),
            ("Portable Tyre Inflator", "AG-INFLATE-001", "RoadReady", 1899, 11, "Portable inflator designed for convenience and emergency vehicle upkeep."),
            ("Car Organizer Tray", "AG-ORG-001", "RoadReady", 1399, 15, "Interior accessory for organizing smaller essentials during travel."),
            ("LED Cabin Light", "AG-LIGHT-001", "RoadReady", 899, 17, "Compact light for cabin use and easy vehicle visibility support."),
        ],
        "document_name": "autogear.txt",
    },
    {
        "business_id": "SB-GIF010",
        "name": "GiftCorner",
        "category": "Gifts & Lifestyle",
        "location": "Vijayawada, Andhra Pradesh",
        "username": "giftcorner@smartbiz",
        "owner_phone": "9999900010",
        "products": [
            ("Handcrafted Desk Lamp", "GC-LAMP-001", "BrightNest", 1599, 13, "Charmingly designed desk lamp for gifting and home decor use."),
            ("Reusable Gift Box Set", "GC-GIFT-001", "BrightNest", 699, 24, "Gift-ready storage set for birthdays, anniversaries, and festive moments."),
            ("Decorative Photo Frame", "GC-FRAME-001", "BrightNest", 899, 22, "Lifestyle accessory for gifting and home personalization."),
            ("Anniversary Gift Hamper", "GC-HAMPER-001", "GiftCorner", 2599, 8, "Curated occasional gift hamper for meaningful gifting moments."),
        ],
        "document_name": "giftcorner.txt",
    },
]


def _shop_document(shop):
    business_id = shop["business_id"]
    name = shop["name"]
    category = shop["category"]
    location = shop["location"]
    return f"""Business Name: {name}

Category: {category}

Business ID: {business_id}

Location: {location}

About:
{name} is a trusted local business serving customers in {location}. The store offers a curated range of products and customer service designed for convenience, quality, and reliable support.

Business Hours:
Monday-Saturday: 9:30 AM-8:30 PM
Sunday: 10:00 AM-6:00 PM

Product Categories:
{category} essentials, relevant product lines, customer-requested selections, and store-supported accessories or complementary items.

Product Knowledge:
Our product lineup is selected for practical everyday use, convenience, and value. Product descriptions in the catalog are used for customer guidance, while live pricing and stock are managed through the transactional database.

Return Policy:
Returns are accepted when the product is unused, in acceptable condition, and accompanied by a valid purchase record. Opened, damaged, or customized items may be reviewed case by case under applicable store policy.

Exchange Policy:
Eligible products may be exchanged when they remain in acceptable condition and match the applicable exchange rules. Customer support can confirm whether a specific item qualifies.

Delivery Information:
Local delivery and store dispatch are available based on location, order size, and product category. Delivery timings are confirmed during checkout and by customer support.

Customer Support:
Customers can call the shop or ask the AI assistant about product details, order help, return guidance, and store timings. We do not use RAG to provide live price or live stock values.

Frequently Asked Questions:
Q: What products are available?
A: The store offers a curated selection of products appropriate to our business category and local customer demand.

Q: Do you offer returns?
A: Yes, subject to product condition and applicable shop policy.

Q: Do you have delivery support?
A: Yes, local delivery and dispatch support are available based on order details and location.

Q: Can I exchange a product?
A: Eligible products may be exchanged when they are in acceptable condition and satisfy exchange rules.

Q: Can I talk to the shopkeeper?
A: Yes, customers can contact the business for product support, order questions, and policy clarifications.

AI Usage Rules:
- Use business knowledge only for this business.
- Do not mix data from other businesses.
- Do not use RAG to provide live price or live stock.
- Price, stock, availability, order totals, and order status must come from the transactional database and product tools.
- Answer using only the current business knowledge base and the active business context.
"""


def _ensure_demo_businesses():
    session = get_session()
    try:
        for shop in DEMO_SHOPS:
            business = session.query(Business).filter(Business.business_id == shop["business_id"]).first()
            if business is None:
                business = Business(
                    business_id=shop["business_id"],
                    name=shop["name"],
                    category=shop["category"],
                    address=shop["location"],
                    owner_phone=shop["owner_phone"],
                    username=shop["username"],
                )
                session.add(business)
            else:
                business.name = shop["name"]
                business.category = shop["category"]
                business.address = shop["location"]
                business.owner_phone = shop["owner_phone"]
                business.username = shop["username"]

            for product_name, sku, brand, price, stock, description in shop["products"]:
                product = (
                    session.query(Product)
                    .filter(Product.business_id == shop["business_id"], Product.product_name == product_name)
                    .first()
                )
                if product is None:
                    session.add(
                        Product(
                            business_id=shop["business_id"],
                            product_name=product_name,
                            sku=sku,
                            category=shop["category"],
                            brand=brand,
                            price=price,
                            stock_quantity=stock,
                            low_stock_threshold=2,
                            description=description,
                            availability="In Stock" if stock > 0 else "Out of Stock",
                            is_active=True,
                        )
                    )
                else:
                    product.sku = sku
                    product.category = shop["category"]
                    product.brand = brand
                    product.price = price
                    product.stock_quantity = stock
                    product.description = description
                    product.availability = "In Stock" if stock > 0 else "Out of Stock"
        session.commit()
    finally:
        session.close()


def seed_demo_shops(*, index_knowledge=True, documents_root=None):
    """Create or update the exact 10 SmartBiz demo businesses and, optionally, index their knowledge docs."""
    documents_root = Path(documents_root) if documents_root is not None else Path("data/documents")
    documents_root.mkdir(parents=True, exist_ok=True)

    _ensure_demo_businesses()

    session = get_session()
    try:
        business_ids = [business.business_id for business in session.query(Business).filter(Business.business_id.in_([shop["business_id"] for shop in DEMO_SHOPS])).order_by(Business.business_id.asc()).all()]
        product_count = session.query(Product).filter(Product.business_id.in_(business_ids)).count()
    finally:
        session.close()

    if index_knowledge:
        uploader = upload_module.DocumentUploader()
        for shop in DEMO_SHOPS:
            document_path = documents_root / shop["document_name"]
            document_path.write_text(_shop_document(shop), encoding="utf-8")
            uploader.upload(shop["business_id"], str(document_path))
        for business_id in business_ids:
            BusinessCatalog(business_id).sync_products_to_rag(vector_store=uploader.vector_store)

    return {
        "business_count": len(DEMO_SHOPS),
        "product_count": product_count,
        "business_ids": [shop["business_id"] for shop in DEMO_SHOPS],
        "knowledge_indexed": index_knowledge,
    }


def main():
    result = seed_demo_shops(index_knowledge=True)
    print(f"Seeded {result['business_count']} demo businesses with {result['product_count']} products.")
    print(f"Indexed {len(result['business_ids'])} business knowledge documents.")


if __name__ == "__main__":
    main()