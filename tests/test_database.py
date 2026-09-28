from app.agent.tools import AgentTools
from app.business.register_business import BusinessManager
from app.business.search_business import BusinessSearch
from app.database import Business, get_session


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
