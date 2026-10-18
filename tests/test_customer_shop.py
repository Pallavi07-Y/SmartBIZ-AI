from pathlib import Path
from types import SimpleNamespace

from app.agent.ai_agent import AIAgent
from app.agent.memory import ConversationMemory
from app.agent.tools import AgentTools
from app.business.business_manager import BusinessController
from app.business.register_business import BusinessManager
from app.business.search_business import BusinessSearch
from app.database import Business, Conversation, ConversationStatus, Message, MessageSender, Order, Product, get_session
from app.rag.rag_pipeline import RAGPipeline
from app.dashboard import (
    create_customer_conversation,
    get_customer_business,
    get_customer_conversation_messages,
    get_customer_conversations,
    get_customer_orders,
    get_customer_products,
    search_customer_marketplace,
)
from app.seed_demo_data import DEMO_SHOPS, _shop_document, seed_demo_shops


def test_demo_rag_dataset_has_exact_business_ids_and_documents(isolated_database, tmp_path):
    result = seed_demo_shops(index_knowledge=False)
    expected_ids = {
        "SB-ABC001",
        "SB-STY002",
        "SB-FRE003",
        "SB-TEC004",
        "SB-FIT005",
        "SB-HOM006",
        "SB-BOK007",
        "SB-BEA008",
        "SB-AUT009",
        "SB-GIF010",
    }
    assert result["business_count"] == 10
    assert set(result["business_ids"]) == expected_ids

    for file_name in (
        "abc_mobiles.txt",
        "stylehub_fashion.txt",
        "freshmart.txt",
        "techzone_electronics.txt",
        "fitlife_store.txt",
        "homenest.txt",
        "bookworld.txt",
        "beautycare.txt",
        "autogear.txt",
        "giftcorner.txt",
    ):
        document_path = Path("data/documents") / file_name
        assert document_path.exists(), f"Missing demo document: {document_path}"
        content = document_path.read_text(encoding="utf-8")
        assert "Business ID:" in content
        assert "Frequently Asked Questions:" not in content


def test_default_shop_knowledge_template_uses_prose_sections_without_qa_pairs():
    template = _shop_document(DEMO_SHOPS[0])

    assert "Frequently Asked Questions:" not in template
    assert "Q:" not in template
    assert "A:" not in template
    for section in (
        "Business Information:",
        "Products:",
        "Return Policy:",
        "Exchange Policy:",
        "Delivery:",
        "Customer Support:",
    ):
        assert section in template


def test_customer_business_lookup_and_business_isolation(isolated_database):
    controller = BusinessController()
    shop_a = controller.create_business("ABC Mobiles", "Electronics", address="Visakhapatnam", owner_phone="1111111111", username="abc-shop", password="secret123")
    shop_b = controller.create_business("XYZ Mobiles", "Electronics", address="Hyderabad", owner_phone="2222222222", username="xyz-shop", password="secret456")

    session = get_session()
    try:
        session.add_all([
            Product(business_id=shop_a["business_id"], product_name="Samsung Galaxy S25", category="smartphone", price=75000, stock_quantity=10, availability="In Stock", is_active=True),
            Product(business_id=shop_b["business_id"], product_name="Samsung Galaxy S25", category="smartphone", price=68000, stock_quantity=6, availability="In Stock", is_active=True),
        ])
        session.commit()
    finally:
        session.close()

    assert get_customer_business(shop_a["business_id"])["name"] == "ABC Mobiles"
    products = get_customer_products(shop_a["business_id"], query="Samsung Galaxy S25")
    assert len(products) == 1
    assert products[0]["price"] == 75000
    assert products[0]["business_id"] == shop_a["business_id"]
    assert BusinessSearch().search_by_id(shop_a["business_id"])["name"] == "ABC Mobiles"


def test_customer_product_search_uses_business_scope(isolated_database):
    controller = BusinessController()
    shop = controller.create_business("Smart Shop", "Electronics", username="smart-shop", password="pass123")

    session = get_session()
    try:
        session.add_all([
            Product(business_id=shop["business_id"], product_name="iPhone 16", category="smartphone", price=80000, stock_quantity=5, availability="In Stock", is_active=True),
            Product(business_id=shop["business_id"], product_name="OnePlus 13", category="smartphone", price=65000, stock_quantity=8, availability="In Stock", is_active=True),
        ])
        session.commit()
    finally:
        session.close()

    search_results = get_customer_products(shop["business_id"], query="iphone")
    assert len(search_results) == 1
    assert search_results[0]["product_name"] == "iPhone 16"


def test_customer_marketplace_search_finds_products_across_shops(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Golden Lane", "Jewellery", username="golden-lane")
    shop_b = manager.register_business("City Gifts", "Gifts", username="city-gifts")
    session = get_session()
    try:
        session.add_all([
            Product(
                business_id=shop_a["business_id"],
                product_name="Gold Ring",
                category="Jewellery",
                price=12000,
                stock_quantity=2,
                is_active=True,
            ),
            Product(
                business_id=shop_b["business_id"],
                product_name="Silver Pendant",
                category="Jewellery",
                price=3000,
                stock_quantity=4,
                is_active=True,
            ),
        ])
        session.commit()
    finally:
        session.close()

    shops, products = search_customer_marketplace("gold ring")

    assert [shop["name"] for shop in shops] == ["Golden Lane"]
    assert [product["product_name"] for product in products] == ["Gold Ring"]
    assert products[0]["shop_name"] == "Golden Lane"


def test_marketplace_assistant_compares_live_listings_without_claiming_reviews(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Golden Lane", "Jewellery", username="gold-lane-ai")
    shop_b = manager.register_business("City Jewels", "Jewellery", username="city-jewels-ai")
    session = get_session()
    try:
        session.add_all([
            Product(
                business_id=shop_a["business_id"],
                product_name="Gold Ring",
                category="Jewellery",
                price=12000,
                stock_quantity=2,
                is_active=True,
            ),
            Product(
                business_id=shop_b["business_id"],
                product_name="Gold Ring",
                category="Jewellery",
                price=11500,
                stock_quantity=0,
                is_active=True,
            ),
            Order(
                business_id=shop_b["business_id"],
                customer_phone="1234567890",
                items=[{"product_name": "Gold Ring", "quantity": 1, "price": 11500}],
                status="COMPLETED",
            ),
        ])
        session.commit()
    finally:
        session.close()

    agent = AIAgent.__new__(AIAgent)
    agent.tools = AgentTools.__new__(AgentTools)
    answer = agent.chat_marketplace("I want to buy a gold ring. Which shop is best according to reviews?")

    assert "Golden Lane" in answer
    assert "₹12,000" in answer
    assert "currently out of stock" in answer
    assert "recorded order" in answer
    assert "don't have verified customer reviews" in answer


def test_marketplace_assistant_does_not_recommend_unrelated_items(isolated_database):
    shop = BusinessManager().register_business("Everyday Shop", "General", username="everyday-shop")
    session = get_session()
    try:
        session.add(Product(
            business_id=shop["business_id"],
            product_name="Cotton Shirt",
            category="Clothing",
            price=1000,
            stock_quantity=3,
            is_active=True,
        ))
        session.commit()
    finally:
        session.close()

    agent = AIAgent.__new__(AIAgent)
    agent.tools = AgentTools.__new__(AgentTools)
    answer = agent.chat_marketplace("I want to buy a gold ring. Which shop is best?")

    assert "couldn't find a current shop listing" in answer
    assert "Cotton Shirt" not in answer
    assert "database" not in answer.casefold()


def test_demo_shop_seed_is_ten_businesses_and_idempotent(isolated_database):
    first = seed_demo_shops(index_knowledge=False)
    second = seed_demo_shops(index_knowledge=False)

    assert first["business_count"] == 10
    assert second["business_count"] == 10
    assert len(set(second["business_ids"])) == 10
    assert first["product_count"] == second["product_count"]
    assert second["product_count"] >= 20

    session = get_session()
    try:
        assert session.query(Business).count() == 10
        names = {business.name for business in session.query(Business).all()}
        assert {
            "ABC Mobiles",
            "StyleHub Fashion",
            "FreshMart",
            "TechZone Electronics",
            "FitLife Store",
            "HomeNest",
            "BookWorld",
            "BeautyCare",
            "AutoGear",
            "GiftCorner",
        }.issubset(names)
    finally:
        session.close()


def test_demo_shop_seed_indexes_all_business_documents(isolated_database, tmp_path, monkeypatch):
    uploads = []
    indexed_products = []

    class FakeVectorStore:
        def __init__(self):
            self.db = SimpleNamespace(delete_by_metadata=lambda *args: True)

        def add_documents(self, collection_name, documents, business_id):
            indexed_products.extend((collection_name, business_id, document) for document in documents)
            return {"status": "success", "documents_added": len(documents)}

    class FakeUploader:
        def __init__(self):
            self.vector_store = FakeVectorStore()

        def upload(self, business_id, file_path):
            uploads.append((business_id, Path(file_path).read_text(encoding="utf-8")))
            return {"business_id": business_id, "status": {"status": "success"}}

    import app.business.upload_documents as upload_module

    monkeypatch.setattr(upload_module, "DocumentUploader", FakeUploader)
    result = seed_demo_shops(index_knowledge=True, documents_root=tmp_path)

    assert result["business_count"] == 10
    assert result["knowledge_indexed"] is True
    assert len(uploads) == 10
    assert len({business_id for business_id, _ in uploads}) == 10
    assert all(f"Business ID: {business_id}" in " ".join(content.split()) for business_id, content in uploads)
    assert any("return policy" in content.casefold() for _, content in uploads)
    assert len(indexed_products) == 40
    assert all(collection == f"business_{business_id}" for collection, business_id, _ in indexed_products)
    assert all(document.metadata["business_id"] == business_id for _, business_id, document in indexed_products)
    assert {document.metadata["type"] for _, _, document in indexed_products} == {"product"}
    product_context = "\n".join(document.page_content for _, _, document in indexed_products)
    assert "USB-C Fast Charger" in product_context
    assert "₹1,999" not in product_context
    assert "Price:" not in product_context
    assert "Availability:" not in product_context
    assert "Stock Quantity:" not in product_context


def test_demo_shop_seed_indexes_business_specific_knowledge(isolated_database, tmp_path, monkeypatch):
    uploaded = []

    class FakeVectorStore:
        def __init__(self):
            self.db = SimpleNamespace(delete_by_metadata=lambda *args: True)

        def add_documents(self, collection_name, documents, business_id):
            return {"status": "success", "documents_added": len(documents)}

    class FakeUploader:
        def __init__(self):
            self.vector_store = FakeVectorStore()

        def upload(self, business_id, file_path):
            content = Path(file_path).read_text(encoding="utf-8")
            uploaded.append((business_id, content))
            return {"business_id": business_id, "status": {"status": "success"}}

    import app.business.upload_documents as upload_module

    monkeypatch.setattr(upload_module, "DocumentUploader", FakeUploader)
    result = seed_demo_shops(index_knowledge=True, documents_root=tmp_path)

    assert result["knowledge_indexed"] is True
    assert len(uploaded) == 10
    assert len({business_id for business_id, _ in uploaded}) == 10
    assert all(f"Business ID: {business_id}" in " ".join(content.split()) for business_id, content in uploaded)
    assert any("return policy" in content.casefold() for _, content in uploaded)


def test_customer_orders_and_conversations_are_session_and_shop_scoped(isolated_database):
    shop_a = BusinessController().create_business("Shop A", "Electronics", username="cust-scope-a")
    shop_b = BusinessController().create_business("Shop B", "Fashion", username="cust-scope-b")
    session_a = "customer-session-a"
    session_b = "customer-session-b"

    db = get_session()
    try:
        db.add_all([
            Order(
                business_id=shop_a["business_id"],
                customer_session_id=session_a,
                customer_phone="1111111111",
                items=[{"product_name": "Phone", "quantity": 1, "price": 100}],
                status="NEW",
            ),
            Order(
                business_id=shop_a["business_id"],
                customer_session_id=session_b,
                customer_phone="2222222222",
                items=[{"product_name": "Phone", "quantity": 1, "price": 100}],
                status="NEW",
            ),
            Order(
                business_id=shop_b["business_id"],
                customer_session_id=session_a,
                customer_phone="1111111111",
                items=[{"product_name": "Shirt", "quantity": 1, "price": 50}],
                status="NEW",
            ),
        ])
        db.commit()
    finally:
        db.close()

    conversation_id = create_customer_conversation(shop_a["business_id"], session_a, "1111111111")
    other_conversation_id = create_customer_conversation(shop_b["business_id"], session_b, "2222222222")
    db = get_session()
    try:
        db.add_all([
            Message(conversation_id=conversation_id, sender=MessageSender.CUSTOMER, text="phone question"),
            Message(conversation_id=other_conversation_id, sender=MessageSender.CUSTOMER, text="shirt question"),
        ])
        db.commit()
    finally:
        db.close()

    orders = get_customer_orders(session_a)
    assert {order["business_id"] for order in orders} == {shop_a["business_id"], shop_b["business_id"]}
    assert len(orders) == 2
    assert get_customer_orders(session_b)[0]["items"][0]["product_name"] == "Phone"
    assert len(get_customer_conversations(shop_a["business_id"], session_a)) == 1
    assert get_customer_conversations(shop_b["business_id"], session_a) == []
    assert get_customer_conversation_messages(shop_b["business_id"], session_b, conversation_id) == []
    assert get_customer_orders("unknown-customer-session") == []


def test_live_product_tool_filters_by_business_id(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Live Shop A", "Mobile", username="live-shop-a")
    shop_b = manager.register_business("Live Shop B", "Mobile", username="live-shop-b")
    session = get_session()
    try:
        session.add_all([
            Product(business_id=shop_a["business_id"], product_name="Galaxy S25", brand="Samsung", price=75000, stock_quantity=4, is_active=True),
            Product(business_id=shop_b["business_id"], product_name="Galaxy S25", brand="Samsung", price=68000, stock_quantity=2, is_active=True),
        ])
        session.commit()
    finally:
        session.close()

    tools = AgentTools.__new__(AgentTools)
    results_a = tools.get_live_products(shop_a["business_id"], query="Samsung")
    results_b = tools.get_live_products(shop_b["business_id"], query="Samsung")

    assert len(results_a) == len(results_b) == 1
    assert results_a[0]["price"] == 75000
    assert results_b[0]["price"] == 68000
    assert results_a[0]["business_id"] != results_b[0]["business_id"]


def test_customer_checkout_uses_sqlite_price_stock_and_session(isolated_database):
    shop = BusinessController().create_business("Checkout Shop", "Electronics", username="cust-checkout")
    db = get_session()
    try:
        product = Product(
            business_id=shop["business_id"],
            product_name="Checkout Phone",
            price=120,
            stock_quantity=3,
            low_stock_threshold=1,
            availability="In Stock",
            is_active=True,
        )
        db.add(product)
        db.commit()
        product_id = product.id
    finally:
        db.close()

    tools = AgentTools.__new__(AgentTools)
    tools.get_catalog = lambda business_id: type(
        "CatalogStub",
        (),
        {"find_product": lambda self, name: {
            "product_name": "Checkout Phone",
            "price": 999999,
            "availability": "In Stock",
        }},
    )()
    first = tools.create_order(
        shop["business_id"],
        "Buyer",
        "1234567890",
        "Checkout Phone",
        quantity=2,
        customer_session_id="checkout-session-a",
        customer_address="Main Road",
        payment_method="COD",
        confirmed=True,
    )
    assert first["total"] == 240
    assert first["customer_session_id"] == "checkout-session-a"
    assert tools.get_recent_orders(shop["business_id"], "checkout-session-a")[0]["order_id"] == first["order_id"]
    assert tools.get_recent_orders(shop["business_id"], "checkout-session-b") == []

    second = tools.create_order(
        shop["business_id"],
        "Buyer",
        "1234567890",
        "Checkout Phone",
        quantity=2,
        customer_session_id="checkout-session-a",
        payment_method="COD",
        confirmed=True,
    )
    assert second["error"] == "There is not enough stock to complete this order."

    db = get_session()
    try:
        product = db.get(Product, product_id)
        assert product.stock_quantity == 1
        assert db.query(Order).filter_by(customer_session_id="checkout-session-a").count() == 1
    finally:
        db.close()

def test_return_policy_query_is_routed_as_business_knowledge():
    router = RAGPipeline.__new__(RAGPipeline)
    assert router.is_business_query("What is your return policy?") is True


def test_customer_agent_lists_current_business_products_without_llm(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Catalog Shop A", "Electronics", username="catalog-a")
    shop_b = manager.register_business("Catalog Shop B", "Electronics", username="catalog-b")
    db = get_session()
    try:
        db.add_all([
            Product(business_id=shop_a["business_id"], product_name="USB-C Fast Charger", price=1999, stock_quantity=30, availability="In Stock", is_active=True),
            Product(business_id=shop_a["business_id"], product_name="OnePlus 13", price=68000, stock_quantity=15, availability="In Stock", is_active=True),
            Product(business_id=shop_b["business_id"], product_name="Blue Cotton Shirt", price=1200, stock_quantity=5, availability="In Stock", is_active=True),
        ])
        db.commit()
    finally:
        db.close()

    llm_calls = []
    agent = AIAgent.__new__(AIAgent)
    agent.tools = AgentTools.__new__(AgentTools)
    agent.llm = SimpleNamespace(generate_response=lambda *args, **kwargs: llm_calls.append(args) or "Unexpected LLM response")
    agent.memory = ConversationMemory()

    response = agent.chat(shop_a["business_id"], "what are the available products?")

    assert "USB-C Fast Charger" in response["reply"]
    assert "OnePlus 13" in response["reply"]
    assert "Blue Cotton Shirt" not in response["reply"]
    assert "₹1,999" in response["reply"]
    assert "30" in response["reply"]
    assert llm_calls == []


def test_customer_agent_retrieves_the_active_business_name(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("North Shop", "Mobile", username="profile-context-a")
    shop_b = manager.register_business("South Shop", "Fashion", username="profile-context-b")
    contexts = {
        shop_a["business_id"]: f"Business Name: North Shop\nBusiness ID: {shop_a['business_id']}",
        shop_b["business_id"]: f"Business Name: South Shop\nBusiness ID: {shop_b['business_id']}",
    }
    retrieval_calls = []
    llm_inputs = []

    class ProfileTools:
        rag = SimpleNamespace(is_business_query=lambda query: True)

        def get_business_context(self, business_id, query):
            retrieval_calls.append((business_id, query))
            return contexts[business_id]

    def generate_response(messages, **kwargs):
        llm_inputs.append(messages)
        return "Verified profile response"

    agent = AIAgent.__new__(AIAgent)
    agent.tools = ProfileTools()
    agent.llm = SimpleNamespace(generate_response=generate_response)
    agent.memory = ConversationMemory()

    first = agent.chat(shop_a["business_id"], "what is the shop name?")
    agent.memory.clear()
    second = agent.chat(shop_b["business_id"], "what is the shop name?")

    assert first["reply"] == second["reply"] == "Verified profile response"
    assert [business_id for business_id, _ in retrieval_calls] == [shop_a["business_id"], shop_b["business_id"]]
    assert "Business Name: North Shop" in llm_inputs[0][-1]["content"]
    assert "Business Name: South Shop" in llm_inputs[1][-1]["content"]
    assert shop_b["business_id"] not in llm_inputs[0][-1]["content"]
    assert shop_a["business_id"] not in llm_inputs[1][-1]["content"]


def test_customer_agent_exact_product_query_includes_scoped_context(isolated_database):
    shop = BusinessManager().register_business("Charger Shop", "Electronics", username="charger-shop")
    db = get_session()
    try:
        db.add(Product(
            business_id=shop["business_id"],
            product_name="USB-C Fast Charger",
            brand="VoltLine",
            category="Accessories",
            price=1999,
            stock_quantity=30,
            availability="In Stock",
            description="Compact fast charger for modern phones.",
            is_active=True,
        ))
        db.commit()
    finally:
        db.close()

    llm_inputs = []
    rag_calls = []
    live_tools = AgentTools.__new__(AgentTools)

    class ProductTools:
        rag = SimpleNamespace(is_business_query=lambda query: True)

        def get_live_products(self, business_id, **kwargs):
            return live_tools.get_live_products(business_id, **kwargs)

        def get_business_context(self, business_id, query):
            rag_calls.append((business_id, query))
            return f"Product Name: USB-C Fast Charger\nBusiness ID: {business_id}\nRecommended for phones."

    def generate_response(messages, **kwargs):
        llm_inputs.append(messages)
        return "Verified charger response"

    agent = AIAgent.__new__(AIAgent)
    agent.tools = ProductTools()
    agent.llm = SimpleNamespace(generate_response=generate_response)
    agent.memory = ConversationMemory()

    result = agent.chat(shop["business_id"], "tell me about USB-C Fast Charger")

    assert result["reply"] == "Verified charger response"
    assert rag_calls == [(shop["business_id"], "tell me about USB-C Fast Charger")]
    assert "USB-C Fast Charger" in llm_inputs[0][-1]["content"]
    assert "1999" in llm_inputs[0][-1]["content"]
    assert "Recommended for phones." in llm_inputs[0][-1]["content"]


def test_customer_agent_exact_product_price_is_business_scoped(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Price Shop A", "Electronics", username="price-a")
    shop_b = manager.register_business("Price Shop B", "Electronics", username="price-b")
    db = get_session()
    try:
        db.add_all([
            Product(business_id=shop_a["business_id"], product_name="USB-C Fast Charger", price=1999, stock_quantity=30, availability="In Stock", is_active=True),
            Product(business_id=shop_b["business_id"], product_name="USB-C Fast Charger", price=2999, stock_quantity=2, availability="In Stock", is_active=True),
        ])
        db.commit()
    finally:
        db.close()

    agent = AIAgent.__new__(AIAgent)
    agent.tools = AgentTools.__new__(AgentTools)
    agent.llm = SimpleNamespace(generate_response=lambda *args, **kwargs: "Unexpected generated price")
    agent.memory = ConversationMemory()

    answer_a = agent.chat(shop_a["business_id"], "what is the price of USB-C Fast Charger?")["reply"]
    answer_b = agent.chat(shop_b["business_id"], "what is the price of USB-C Fast Charger?")["reply"]

    assert "₹1,999" in answer_a
    assert "₹2,999" not in answer_a
    assert "₹2,999" in answer_b
    assert "₹1,999" not in answer_b


def test_customer_agent_routes_live_product_questions_and_keeps_shop_memory_separate(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Agent Shop A", "Mobile", username="agent-customer-a")
    shop_b = manager.register_business("Agent Shop B", "Fashion", username="agent-customer-b")
    llm_inputs = []

    class LiveTools:
        rag = SimpleNamespace(is_business_query=lambda query: True)

        def get_live_products(self, business_id, **kwargs):
            product = {
                "product_name": "Samsung Galaxy S25" if business_id == shop_a["business_id"] else "Blue Cotton Shirt",
                "price": 75000 if business_id == shop_a["business_id"] else 1200,
                "stock_quantity": 4,
                "availability": "In Stock",
                "business_id": business_id,
            }
            return [product]

    def generate_response(messages, **kwargs):
        llm_inputs.append(messages)
        return f"Verified shop response {len(llm_inputs)}"

    agent = AIAgent.__new__(AIAgent)
    agent.tools = LiveTools()
    agent.llm = SimpleNamespace(generate_response=generate_response)
    agent.memory = ConversationMemory()

    first = agent.chat(
        shop_a["business_id"],
        "What is the price of Samsung Galaxy S25?",
        customer_phone="1111111111",
        customer_session_id="browser-customer-1",
    )
    followup = agent.chat(
        shop_a["business_id"],
        "Is it available?",
        customer_phone="1111111111",
        customer_session_id="browser-customer-1",
    )
    second_shop = agent.chat(
        shop_b["business_id"],
        "What is the price of Blue Cotton Shirt?",
        customer_phone="1111111111",
        customer_session_id="browser-customer-1",
    )

    assert first["reply"] == "Samsung Galaxy S25 is priced at ₹75,000."
    assert followup["reply"] == "Samsung Galaxy S25 is in stock (4 unit(s) available)."
    assert second_shop["reply"] == "Blue Cotton Shirt is priced at ₹1,200."
    assert llm_inputs == []

    session = get_session()
    try:
        conversations = session.query(Conversation).filter(
            Conversation.customer_session_id == "browser-customer-1"
        ).all()
        assert {conversation.business_id for conversation in conversations} == {
            shop_a["business_id"],
            shop_b["business_id"],
        }
        message_counts = {
            conversation.business_id: session.query(Message).filter(Message.conversation_id == conversation.id).count()
            for conversation in conversations
        }
        assert message_counts[shop_a["business_id"]] == 4
        assert message_counts[shop_b["business_id"]] == 2
    finally:
        session.close()
