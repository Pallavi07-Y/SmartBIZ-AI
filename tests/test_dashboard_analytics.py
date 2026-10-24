from datetime import datetime

from app.utils.time import utc_now_for_database

from app.business.analytics import get_business_metrics
from app.business.register_business import BusinessManager
from app.database import Conversation, ConversationStatus, Order, Product, get_session


def test_business_metrics_are_scoped_and_calculated_from_live_records(isolated_database):
    manager = BusinessManager()
    shop_a = manager.register_business("Shop A", "Electronics", username="analytics-shop-a")
    shop_b = manager.register_business("Shop B", "Electronics", username="analytics-shop-b")
    today = utc_now_for_database().date()

    session = get_session()
    try:
        session.add_all([
            Product(
                business_id=shop_a["business_id"],
                product_name="Phone Alpha",
                category="Phones",
                price=100,
                stock_quantity=1,
                low_stock_threshold=2,
                is_active=True,
            ),
            Product(
                business_id=shop_a["business_id"],
                product_name="Phone Beta",
                category="Phones",
                price=200,
                stock_quantity=0,
                low_stock_threshold=2,
                is_active=True,
            ),
            Product(
                business_id=shop_b["business_id"],
                product_name="Other Shop Phone",
                category="Phones",
                price=500,
                stock_quantity=8,
                is_active=True,
            ),
            Order(
                business_id=shop_a["business_id"],
                customer_phone="1111111111",
                items=[{"product_name": "Phone Alpha", "quantity": 2, "price": 100, "customer_name": "A"}],
                status="COMPLETED",
                created_at=datetime.combine(today, datetime.min.time()),
            ),
            Order(
                business_id=shop_a["business_id"],
                customer_phone="1111111111",
                items=[{"product_name": "Phone Alpha", "quantity": 1, "price": 100}],
                status="CANCELLED",
                created_at=datetime.combine(today, datetime.min.time()),
            ),
            Order(
                business_id=shop_b["business_id"],
                customer_phone="2222222222",
                items=[{"product_name": "Other Shop Phone", "quantity": 1, "price": 500}],
                status="COMPLETED",
                created_at=datetime.combine(today, datetime.min.time()),
            ),
            Conversation(
                business_id=shop_a["business_id"],
                customer_phone="1111111111",
                status=ConversationStatus.ESCALATED,
            ),
            Conversation(
                business_id=shop_b["business_id"],
                customer_phone="2222222222",
                status=ConversationStatus.AI_HANDLING,
            ),
        ])
        session.commit()
    finally:
        session.close()

    metrics = get_business_metrics(shop_a["business_id"], today=today)

    assert metrics["total_orders"] == 2
    assert metrics["today_orders"] == 2
    assert metrics["revenue"] == 200
    assert metrics["average_order_value"] == 200
    assert metrics["completed_orders"] == 1
    assert metrics["cancelled_orders"] == 1
    assert metrics["total_products"] == 2
    assert metrics["total_units"] == 1
    assert metrics["low_stock_products"] == 1
    assert metrics["out_of_stock_products"] == 1
    assert metrics["customer_count"] == 1
    assert metrics["escalated_count"] == 1
    assert metrics["top_products"][0]["product_name"] == "Phone Alpha"
    assert metrics["top_products"][0]["quantity"] == 2
    assert metrics["orders_by_category"] == [{"category": "Phones", "quantity": 2, "revenue": 200}]
    assert metrics["daily"][-1] == {"date": today.isoformat(), "orders": 2, "revenue": 200}
    current_month = f"{today.year:04d}-{today.month:02d}"
    month_metrics = next(item for item in metrics["monthly"] if item["month"] == current_month)
    assert month_metrics == {"month": current_month, "orders": 2, "revenue": 200}


def test_business_metrics_return_empty_values_without_business_id():
    metrics = get_business_metrics(None)

    assert metrics["total_orders"] == 0
    assert metrics["revenue"] == 0
    assert metrics["daily"] == []


def test_business_metrics_support_date_range_filter(isolated_database):
    shop = BusinessManager().register_business("Date Shop", "Retail", username="analytics-date-shop")
    today = utc_now_for_database().date()
    session = get_session()
    try:
        session.add_all([
            Order(
                business_id=shop["business_id"],
                customer_phone="1111111111",
                items=[{"product_name": "Today item", "quantity": 1, "price": 50}],
                status="NEW",
                created_at=datetime.combine(today, datetime.min.time()),
            ),
            Order(
                business_id=shop["business_id"],
                customer_phone="1111111111",
                items=[{"product_name": "Old item", "quantity": 1, "price": 30}],
                status="COMPLETED",
                created_at=datetime.combine(today.fromordinal(today.toordinal() - 10), datetime.min.time()),
            ),
        ])
        session.commit()
    finally:
        session.close()

    metrics = get_business_metrics(shop["business_id"], date_from=today, date_to=today)

    assert metrics["total_orders"] == 1
    assert metrics["revenue"] == 50
    assert metrics["orders_by_product"] == [{
        "product_name": "Today item",
        "orders": 1,
        "quantity": 1,
        "revenue": 50,
    }]