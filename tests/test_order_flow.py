import pytest
from types import SimpleNamespace

from app.agent.ai_agent import AIAgent
from app.agent.memory import ConversationMemory
from app.agent.tools import AgentTools
from app.business.register_business import BusinessManager
from app.dashboard import get_orders_for_business, update_order_status
from app.database import Conversation, Notification, Order, Product, get_session


def test_order_intent_is_detected():
    assert AIAgent._is_order_intent("I want to order Samsung S25.") is True
    assert AIAgent._is_order_intent("Order Samsung S25 with COD.") is True
    assert AIAgent._is_order_intent("I'll take this") is True
    assert AIAgent._is_order_intent("What is the price of iPhone 16?") is False
    assert AIAgent._is_order_intent("I want to know the price of Samsung S25", True) is False
    assert AIAgent._is_order_intent("I want Samsung S25 specifications", True) is False
    assert AIAgent._is_order_intent("Compare iPhone 16 and Samsung S25") is False
    assert AIAgent._is_order_intent("Do you have Samsung S25?") is False
    assert AIAgent._is_order_intent("Where is my order?") is False
    assert AIAgent._is_order_intent("Tell me about the latest laptops.") is False


def test_payment_method_normalization_and_quantity_parsing():
    assert AIAgent._normalize_payment_method("pay cash on delivery") == "COD"
    assert AIAgent._normalize_payment_method("cash") == "COD"
    assert AIAgent._normalize_payment_method("pay in installments") == "EMI"
    assert AIAgent._normalize_payment_method("EMI") == "EMI"
    assert AIAgent._normalize_payment_method("credit card") is None
    assert AIAgent._extract_order_quantity("Order 2 Samsung Galaxy S25") == 2
    assert AIAgent._extract_order_quantity("I want two of these") == 2
    assert AIAgent._extract_order_quantity("Order Samsung Galaxy S25") is None


def test_create_order_requires_a_supported_payment_method(isolated_database):
    shop = BusinessManager().register_business("Payment Required Shop", "Electronics", username="payment-required-shop")
    tools = AgentTools()

    missing = tools.create_order(
        shop["business_id"],
        "Customer",
        "9876543210",
        "Samsung Galaxy S25",
        confirmed=True,
    )
    unsupported = tools.create_order(
        shop["business_id"],
        "Customer",
        "9876543210",
        "Samsung Galaxy S25",
        payment_method="credit card",
        confirmed=True,
    )

    assert missing == {"error": "Payment method is required. Choose COD or EMI."}
    assert unsupported == {"error": "Unsupported payment method. Choose COD or EMI."}


def _create_order_chat_agent():
    agent = AIAgent.__new__(AIAgent)
    agent.memory = ConversationMemory()
    agent.tools = AgentTools.__new__(AgentTools)
    agent.llm = SimpleNamespace(generate_response=lambda *args, **kwargs: "Catalog comparison response")
    return agent


def _add_catalog_phone(business_id, *, price=75000, stock=4, active=True):
    session = get_session()
    try:
        product = Product(
            business_id=business_id,
            product_name="Samsung Galaxy S25",
            price=price,
            stock_quantity=stock,
            low_stock_threshold=1,
            availability="In Stock" if stock else "Out of Stock",
            is_active=active,
        )
        session.add(product)
        session.commit()
        return product.id
    finally:
        session.close()


def _order_count_and_stock(business_id, product_id):
    session = get_session()
    try:
        return (
            session.query(Order).filter_by(business_id=business_id).count(),
            session.get(Product, product_id).stock_quantity,
        )
    finally:
        session.close()


def test_conversational_order_requires_payment_and_confirmation_and_uses_current_price(isolated_database):
    shop = BusinessManager().register_business("Chat Order Shop", "Electronics", username="chat-order-shop")
    product_id = _add_catalog_phone(shop["business_id"])
    customer_session_id = "chat-order-session"

    first = _create_order_chat_agent().chat(
        shop["business_id"],
        "Order Samsung S25 with COD",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert first["reply"] == (
        "Samsung Galaxy S25 is ₹75,000 and COD is selected. Shall I place the order?"
    )
    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 4)

    session = get_session()
    try:
        conversation = session.query(Conversation).filter_by(customer_session_id=customer_session_id).one()
        assert conversation.order_state["payment_method"] == "COD"
        assert conversation.order_state["awaiting_order_confirmation"] is True
    finally:
        session.close()

    session = get_session()
    try:
        session.get(Product, product_id).price = 73000
        session.commit()
    finally:
        session.close()

    updated_price = _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert updated_price["reply"] == (
        "The current price for Samsung Galaxy S25 is ₹73,000. COD is selected. Shall I place the order?"
    )
    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 4)

    placed = _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes, place the order",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert "Order placed successfully" in placed["reply"]
    assert "Payment: COD" in placed["reply"]
    assert "₹73,000" in placed["reply"]
    assert _order_count_and_stock(shop["business_id"], product_id) == (1, 3)

    session = get_session()
    try:
        order = session.query(Order).filter_by(customer_session_id=customer_session_id).one()
        assert order.payment_method == "COD"
        assert order.items[0]["price"] == 73000
        assert order.items[0]["quantity"] == 1
        assert order.stock_deducted is True
    finally:
        session.close()


def test_payment_choice_persists_and_order_it_uses_previous_product_context(isolated_database):
    shop = BusinessManager().register_business("Payment Chat Shop", "Electronics", username="payment-chat-shop")
    product_id = _add_catalog_phone(shop["business_id"])
    customer_session_id = "payment-chat-session"

    agent = _create_order_chat_agent()
    compared = agent.chat(
        shop["business_id"],
        "Compare Samsung Galaxy S25 and iPhone 16",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert compared["reply"] == "Catalog comparison response"
    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 4)

    selected = _create_order_chat_agent().chat(
        shop["business_id"],
        "Samsung sounds better",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert "Samsung Galaxy S25 is ₹75,000. Would you like to order it?" == selected["reply"]

    payment_prompt = _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes, order it",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert payment_prompt["reply"] == "Sure. Samsung Galaxy S25 is ₹75,000. Which payment method would you prefer: COD or EMI?"

    confirmation_prompt = _create_order_chat_agent().chat(
        shop["business_id"],
        "Pay in installments",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert confirmation_prompt["reply"] == "EMI selected. Samsung Galaxy S25 is ₹75,000. Shall I place the order?"
    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 4)

    placed = _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert "Payment: EMI" in placed["reply"]
    assert _order_count_and_stock(shop["business_id"], product_id) == (1, 3)


def test_product_information_does_not_create_order_and_yes_without_pending_does_not_confirm(isolated_database):
    shop = BusinessManager().register_business("Info Only Shop", "Electronics", username="info-only-shop")
    product_id = _add_catalog_phone(shop["business_id"])
    customer_session_id = "info-only-session"

    for message in (
        "What is the price of Samsung Galaxy S25?",
        "What are its specifications?",
        "Compare Samsung Galaxy S25 and iPhone 16",
        "Which phone is best?",
        "Do you have Samsung Galaxy S25?",
        "Yes",
    ):
        _create_order_chat_agent().chat(
            shop["business_id"],
            message,
            customer_phone="9876543210",
            customer_session_id=customer_session_id,
        )

    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 4)


def test_pending_order_can_be_cancelled_without_creating_order(isolated_database):
    shop = BusinessManager().register_business("Cancel Chat Shop", "Electronics", username="cancel-chat-shop")
    product_id = _add_catalog_phone(shop["business_id"])
    customer_session_id = "cancel-chat-session"
    first = _create_order_chat_agent().chat(
        shop["business_id"],
        "I want Samsung S25",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert "Which payment method" in first["reply"]
    cancelled = _create_order_chat_agent().chat(
        shop["business_id"],
        "No, don't order",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert cancelled["reply"] == "No problem. I haven't placed the order."
    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 4)


def test_quantity_over_stock_requires_adjustment_confirmation_and_respects_stock(isolated_database):
    shop = BusinessManager().register_business("Quantity Chat Shop", "Electronics", username="quantity-chat-shop")
    product_id = _add_catalog_phone(shop["business_id"], stock=1)
    customer_session_id = "quantity-chat-session"

    quantity_prompt = _create_order_chat_agent().chat(
        shop["business_id"],
        "Order 2 Samsung S25 with COD",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert quantity_prompt["reply"] == "Only 1 unit is currently available. Would you like to order 1?"

    payment_prompt = _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert "COD is selected. Shall I place the order?" in payment_prompt["reply"]

    placed = _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    assert "Order placed successfully" in placed["reply"]
    assert _order_count_and_stock(shop["business_id"], product_id) == (1, 0)


def test_out_of_stock_product_is_not_ordered(isolated_database):
    shop = BusinessManager().register_business("Out of Stock Chat Shop", "Electronics", username="out-of-stock-chat-shop")
    product_id = _add_catalog_phone(shop["business_id"], stock=0)
    response = _create_order_chat_agent().chat(
        shop["business_id"],
        "Order Samsung S25 with COD",
        customer_phone="9876543210",
        customer_session_id="out-of-stock-session",
    )

    assert response["reply"] == "Sorry, Samsung Galaxy S25 is currently out of stock."
    assert _order_count_and_stock(shop["business_id"], product_id) == (0, 0)


def test_chat_order_deducts_stock_once_when_shopkeeper_confirms(isolated_database):
    shop = BusinessManager().register_business("No Double Deduction Shop", "Electronics", username="no-double-deduction-shop")
    product_id = _add_catalog_phone(shop["business_id"], stock=3)
    customer_session_id = "no-double-deduction-session"
    _create_order_chat_agent().chat(
        shop["business_id"],
        "Order Samsung S25 with COD",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )
    _create_order_chat_agent().chat(
        shop["business_id"],
        "Yes",
        customer_phone="9876543210",
        customer_session_id=customer_session_id,
    )

    session = get_session()
    try:
        order = session.query(Order).filter_by(customer_session_id=customer_session_id).one()
        assert order.stock_deducted is True
        assert session.get(Product, product_id).stock_quantity == 2
    finally:
        session.close()

    update_order_status(shop["business_id"], order.id, "CONFIRMED")
    assert _order_count_and_stock(shop["business_id"], product_id) == (1, 2)


def test_create_order_uses_new_status_and_total(isolated_database):
    business = BusinessManager().register_business(
        "Alpha Shop",
        "Electronics",
        owner_phone="9876543210",
        username="alpha-order-owner",
        password_hash="hashed-password",
    )
    product_id = _add_catalog_phone(business["business_id"], stock=3)

    order = AgentTools().create_order(
        business["business_id"],
        "Balu",
        "9876543210",
        "Samsung Galaxy S25",
        quantity=2,
        payment_method="COD",
        confirmed=True,
    )

    assert order["status"] == "NEW"
    assert order["total"] == 150000
    assert order["quantity"] == 2


def test_product_id_order_without_customer_session_uses_sqlite_inventory(isolated_database):
    shop = BusinessManager().register_business(
        "CLI Order Shop",
        "Electronics",
        username="cli-order-shop",
        password_hash="hashed-password",
    )
    product_id = _add_catalog_phone(shop["business_id"], price=73000, stock=3)

    order = AgentTools().create_order(
        shop["business_id"],
        "Customer",
        "9876543210",
        "Samsung Galaxy S25",
        quantity=2,
        payment_method="COD",
        product_id=product_id,
        confirmed=True,
    )

    assert order["price"] == 73000
    assert order["total"] == 146000
    assert order["payment_method"] == "COD"
    assert _order_count_and_stock(shop["business_id"], product_id) == (1, 1)
    session = get_session()
    try:
        persisted_order = session.get(Order, order["order_id"])
        assert persisted_order.customer_session_id is None
        assert persisted_order.stock_deducted is True
    finally:
        session.close()


def test_order_status_update_is_business_isolated(isolated_database):
    shop_a = BusinessManager().register_business("Shop A", "Electronics", username="shop-a-order", password_hash="hash-a")
    shop_b = BusinessManager().register_business("Shop B", "Electronics", username="shop-b-order", password_hash="hash-b")

    tools = AgentTools()
    product_a = _add_catalog_phone(shop_a["business_id"], stock=2)
    product_b = _add_catalog_phone(shop_b["business_id"], stock=2)
    order_a = tools.create_order(shop_a["business_id"], "A", "1111111111", "Samsung Galaxy S25", quantity=1, payment_method="COD", confirmed=True)
    tools.create_order(shop_b["business_id"], "B", "2222222222", "Samsung Galaxy S25", quantity=1, payment_method="COD", confirmed=True)

    orders_for_a = get_orders_for_business(shop_a["business_id"])
    orders_for_b = get_orders_for_business(shop_b["business_id"])

    assert len(orders_for_a) == 1
    assert len(orders_for_b) == 1
    assert orders_for_a[0]["order_id"] == order_a["order_id"]

    update_order_status(shop_a["business_id"], order_a["order_id"], "CONFIRMED")
    session = get_session()
    try:
        db_order = session.query(Order).filter(Order.id == order_a["order_id"]).first()
        assert db_order.status == "CONFIRMED"
    finally:
        session.close()


def test_confirming_order_deducts_stock_once_and_creates_low_stock_notification(isolated_database):
    shop = BusinessManager().register_business("Stock Shop", "Electronics", username="stock-order-owner")
    session = get_session()
    try:
        product = Product(
            business_id=shop["business_id"],
            product_name="Stock Phone",
            price=100,
            stock_quantity=3,
            low_stock_threshold=1,
            availability="In Stock",
            is_active=True,
        )
        session.add(product)
        session.flush()
        order = Order(
            business_id=shop["business_id"],
            customer_phone="1234567890",
            items=[{"product_name": "Stock Phone", "quantity": 2, "price": 100}],
            status="NEW",
        )
        session.add(order)
        session.commit()
        order_id = order.id
        product_id = product.id
    finally:
        session.close()

    update_order_status(shop["business_id"], order_id, "CONFIRMED")
    update_order_status(shop["business_id"], order_id, "PROCESSING")
    update_order_status(shop["business_id"], order_id, "CONFIRMED")

    session = get_session()
    try:
        product = session.query(Product).filter_by(id=product_id).one()
        order = session.query(Order).filter_by(id=order_id).one()
        low_stock_alerts = session.query(Notification).filter_by(
            business_id=shop["business_id"],
            type="LOW_STOCK",
            reference_id=str(product_id),
        ).all()
        assert product.stock_quantity == 1
        assert order.stock_deducted is True
        assert order.status == "CONFIRMED"
        assert len(low_stock_alerts) == 1
    finally:
        session.close()


def test_confirming_order_with_insufficient_stock_changes_nothing(isolated_database):
    shop = BusinessManager().register_business("Limited Stock Shop", "Electronics", username="limited-stock-owner")
    session = get_session()
    try:
        product = Product(
            business_id=shop["business_id"],
            product_name="Limited Phone",
            price=100,
            stock_quantity=1,
            low_stock_threshold=2,
            availability="In Stock",
            is_active=True,
        )
        session.add(product)
        session.flush()
        order = Order(
            business_id=shop["business_id"],
            customer_phone="1234567890",
            items=[{"product_name": "Limited Phone", "quantity": 2, "price": 100}],
            status="NEW",
        )
        session.add(order)
        session.commit()
        order_id = order.id
        product_id = product.id
    finally:
        session.close()

    with pytest.raises(ValueError, match="Insufficient stock"):
        update_order_status(shop["business_id"], order_id, "CONFIRMED")

    session = get_session()
    try:
        product = session.query(Product).filter_by(id=product_id).one()
        order = session.query(Order).filter_by(id=order_id).one()
        assert product.stock_quantity == 1
        assert order.status == "NEW"
        assert order.stock_deducted is False
    finally:
        session.close()
