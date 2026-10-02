import pytest

from app.agent.ai_agent import AIAgent
from app.agent.tools import AgentTools
from app.business.register_business import BusinessManager
from app.dashboard import get_orders_for_business, update_order_status
from app.database import Notification, Order, Product, get_session


def test_order_intent_is_detected():
    assert AIAgent._is_order_intent("I want to order Samsung S25.") is True
    assert AIAgent._is_order_intent("Tell me about the latest laptops.") is False


def test_create_order_uses_new_status_and_total(isolated_database):
    business = BusinessManager().register_business(
        "Alpha Shop",
        "Electronics",
        owner_phone="9876543210",
        username="alpha-order-owner",
        password_hash="hashed-password",
    )

    order = AgentTools().create_order(
        business["business_id"],
        "Balu",
        "9876543210",
        "Samsung Galaxy S25",
        quantity=2,
    )

    assert order["status"] == "NEW"
    assert order["total"] == 150000
    assert order["quantity"] == 2


def test_order_status_update_is_business_isolated(isolated_database):
    shop_a = BusinessManager().register_business("Shop A", "Electronics", username="shop-a-order", password_hash="hash-a")
    shop_b = BusinessManager().register_business("Shop B", "Electronics", username="shop-b-order", password_hash="hash-b")

    tools = AgentTools()
    order_a = tools.create_order(shop_a["business_id"], "A", "1111111111", "Samsung Galaxy S25", quantity=1)
    tools.create_order(shop_b["business_id"], "B", "2222222222", "Samsung Galaxy S25", quantity=1)

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
