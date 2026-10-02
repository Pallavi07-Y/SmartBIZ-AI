from app.agent.ai_agent import AIAgent
from app.agent.tools import AgentTools
from app.business.register_business import BusinessManager
from app.dashboard import get_notifications_for_business, set_conversation_status, update_order_status
from app.database import Conversation, ConversationStatus, get_session


def test_new_order_creates_business_scoped_notification(isolated_database):
    business_a = BusinessManager().register_business("Shop A", "Electronics", username="shop-a-phase3", password_hash="hash-a")
    business_b = BusinessManager().register_business("Shop B", "Electronics", username="shop-b-phase3", password_hash="hash-b")

    AgentTools().create_order(business_a["business_id"], "Customer", "9876543210", "Samsung Galaxy S25", quantity=1)
    AgentTools().create_order(business_b["business_id"], "Customer", "9876543210", "OnePlus 13", quantity=1)

    notifications_a = get_notifications_for_business(business_a["business_id"])
    notifications_b = get_notifications_for_business(business_b["business_id"])

    assert sum(1 for item in notifications_a if item["type"] == "NEW_ORDER") == 1
    assert sum(1 for item in notifications_b if item["type"] == "NEW_ORDER") == 1


def test_escalation_creates_notification(isolated_database):
    business = BusinessManager().register_business("Alpha Shop", "Electronics", username="alpha-phase3", password_hash="hashed-password")

    result = AIAgent().chat(business["business_id"], "I want to talk to the owner.", customer_phone="9876543210")

    assert result["signal"] == "awaiting_human"
    notifications = get_notifications_for_business(business["business_id"])
    assert sum(1 for item in notifications if item["type"] == "ESCALATED_CHAT") == 1


def test_take_over_chat_sets_keeper_handling(isolated_database):
    business = BusinessManager().register_business("Alpha Shop", "Electronics", username="alpha-phase3-takeover", password_hash="hashed-password")

    session = get_session()
    try:
        conversation = Conversation(
            business_id=business["business_id"],
            customer_phone="9876543210",
            status=ConversationStatus.AI_HANDLING,
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
    finally:
        session.close()

    set_conversation_status(business["business_id"], conversation.id, ConversationStatus.KEEPER_HANDLING)

    session = get_session()
    try:
        refreshed = session.get(Conversation, conversation.id)
        assert refreshed.status == ConversationStatus.KEEPER_HANDLING
    finally:
        session.close()


def test_order_status_change_creates_notification(isolated_database):
    business = BusinessManager().register_business("Shop X", "Electronics", username="shop-x-phase3", password_hash="hash-x")
    order = AgentTools().create_order(business["business_id"], "C", "9999999999", "Samsung Galaxy S25", quantity=1)

    update_order_status(business["business_id"], order["order_id"], "CONFIRMED")
    notifications = get_notifications_for_business(business["business_id"])

    assert any(item["type"] == "ORDER_STATUS" and "CONFIRMED" in item["title"] for item in notifications)


def test_customer_order_status_question_uses_database_truth(isolated_database):
    business = BusinessManager().register_business("Order Shop", "Electronics", username="order-shop-phase3", password_hash="hash")
    order = AgentTools().create_order(business["business_id"], "Customer", "5555555555", "Samsung Galaxy S25", quantity=2)

    response = AIAgent().chat(business["business_id"], "Where is my order?", customer_phone="5555555555")

    assert response["signal"] == "reply"
    assert order["order_id"] in response["reply"]
    assert "NEW" in response["reply"].upper()
