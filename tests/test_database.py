from app.agent.tools import AgentTools
from app.business.register_business import BusinessManager
from app.business.search_business import BusinessSearch
from app.database import Business, Product, get_session, seed_demo_data


class FakeCatalog:
    def find_product(self, product_name):
        if product_name.lower() != "test phone":
            return None
        return {
            "product_name": "Test Phone",
            "price": 1000,
            "availability": "In Stock",
        }


def test_business_registration_and_search_use_isolated_database(isolated_database):
    created = BusinessManager().register_business(
        "Test Shop",
        "Electronics",
        address="1 Main Street",
        owner_phone="9876543210",
        username="test-owner",
        password_hash="hashed-password",
    )

    found_by_id = BusinessSearch().search_by_id(created["business_id"])
    found_by_name = BusinessSearch().search_by_name("test shop")

    assert found_by_id["name"] == "Test Shop"
    assert found_by_id["address"] == "1 Main Street"
    assert found_by_id["owner_phone"] == "9876543210"
    assert found_by_name[0]["business_id"] == created["business_id"]


def test_cart_and_order_data_persist_across_tool_instances(isolated_database, monkeypatch):
    business = BusinessManager().register_business("Test Shop", "Electronics")
    business_id = business["business_id"]
    first_tools = AgentTools.__new__(AgentTools)
    second_tools = AgentTools.__new__(AgentTools)
    monkeypatch.setattr(first_tools, "get_catalog", lambda business_id: FakeCatalog())
    monkeypatch.setattr(second_tools, "get_catalog", lambda business_id: FakeCatalog())

    cart = first_tools.add_to_cart("session-1", business_id, "Test Phone")
    order = first_tools.create_order(
        business_id,
        "Customer",
        "9876543210",
        "Test Phone",
    )

    assert cart["cart"][0]["product_name"] == "Test Phone"
    assert second_tools.get_cart("session-1")[0]["quantity"] == 1
    assert second_tools.get_order_status(business_id, order["order_id"]) == order
    assert second_tools.get_recent_orders(business_id) == [order]

    session = get_session()
    try:
        assert session.query(Business).count() == 1
    finally:
        session.close()


def test_product_records_are_business_scoped_and_used_for_pricing(isolated_database):
    business = BusinessManager().register_business("Test Shop", "Electronics")
    business_id = business["business_id"]

    session = get_session()
    try:
        session.add(
            Product(
                business_id=business_id,
                product_name="Test Phone Pro",
                sku="TP-PRO-01",
                category="smartphone",
                price=999,
                stock_quantity=5,
                description="Premium device",
                availability="In Stock",
                is_active=True,
            )
        )
        session.commit()
    finally:
        session.close()

    tools = AgentTools.__new__(AgentTools)
    product = tools.get_product_details(business_id, "Test Phone Pro")
    assert product["product_name"] == "Test Phone Pro"
    assert product["price"] == 999
    assert product["availability"] == "In Stock"

    search_results = tools.search_products(business_id, "premium phone")
    assert any(item["product_name"] == "Test Phone Pro" for item in search_results)


def test_seed_demo_data_creates_ten_demo_businesses_and_products(isolated_database):
    result = seed_demo_data()

    expected_business_ids = {
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
    assert result["product_count"] == 40

    session = get_session()
    try:
        businesses = session.query(Business).all()
        assert {business.business_id for business in businesses} == expected_business_ids
        business = session.get(Business, "SB-ABC001")
        assert business is not None
        assert business.name == "ABC Mobiles"
        products = session.query(Product).filter(Product.business_id == business.business_id).all()
        assert len(products) == 4
        charger = next(product for product in products if product.product_name == "USB-C Fast Charger")
        assert charger.price == 1999
        assert charger.stock_quantity == 30
        assert charger.availability == "In Stock"
    finally:
        session.close()
