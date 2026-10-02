import uuid
from types import SimpleNamespace

import pytest

import app.database as database_module
from app.agent.ai_agent import AIAgent
from app.business.business_manager import BusinessController
from app.database import Business, Conversation, ConversationStatus, Message, configure_database, get_session


@pytest.fixture(autouse=True)
def reset_database_state():
    database_module._engine = None
    database_module._session_factory = None
    database_module._configured_url = None
    engine = configure_database("sqlite:///:memory:")
    yield
    engine.dispose()
    database_module._engine = None
    database_module._session_factory = None
    database_module._configured_url = None


def test_agent_escalates_on_explicit_human_request():
    agent = AIAgent()
    business_id = f"biz-{uuid.uuid4().hex[:8]}"
    customer_phone = "+1234567890"

    session = get_session()
    try:
        session.add(
            Business(
                business_id=business_id,
                name="Test Shop",
                category="Electronics",
                username=f"shop-{uuid.uuid4().hex[:8]}",
                password_hash="hash",
            )
        )
        session.commit()
    finally:
        session.close()

    response = agent.chat(business_id, "I need to talk to the owner", customer_phone)

    assert response == {"signal": "awaiting_human", "reply": None}

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter_by(business_id=business_id, customer_phone=customer_phone)
            .order_by(Conversation.created_at.desc())
            .first()
        )
        assert conversation is not None
        assert conversation.status == ConversationStatus.ESCALATED
        assert session.query(Message).filter_by(conversation_id=conversation.id).count() == 1
    finally:
        session.close()


def test_agent_does_not_reply_if_conversation_already_escalated():
    agent = AIAgent()
    business_id = f"biz-{uuid.uuid4().hex[:8]}"
    customer_phone = "+1987654321"

    session = get_session()
    try:
        session.add(
            Business(
                business_id=business_id,
                name="Escalation Shop",
                category="Electronics",
                username=f"shop-{uuid.uuid4().hex[:8]}",
                password_hash="hash",
            )
        )
        session.add(
            Conversation(
                business_id=business_id,
                customer_phone=customer_phone,
                status=ConversationStatus.ESCALATED,
            )
        )
        session.commit()
    finally:
        session.close()

    response = agent.chat(business_id, "still waiting for a person", customer_phone)

    assert response == {"signal": "awaiting_human", "reply": None}

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter_by(business_id=business_id, customer_phone=customer_phone)
            .order_by(Conversation.created_at.desc())
            .first()
        )
        assert conversation is not None
        assert conversation.status == ConversationStatus.ESCALATED
        assert session.query(Message).filter_by(conversation_id=conversation.id).count() == 1
    finally:
        session.close()


def test_business_insights_use_verified_metrics_and_structured_llm_output(monkeypatch):
    agent = AIAgent.__new__(AIAgent)
    captured = {}

    def fake_context(business_id, query):
        assert business_id == "shop-a"
        captured["query"] = query
        return "Return policy: unopened items may be returned within 14 days."

    def fake_response(messages, **kwargs):
        captured["messages"] = messages
        captured["kwargs"] = kwargs
        return (
            '{"summary":"Recorded revenue is ₹200.","order_insights":[], '
            '"inventory_insights":[],"customer_insights":[], '
            '"operational_issues":[],"suggested_actions":[]}'
        )

    agent.tools = SimpleNamespace(get_business_context=fake_context)
    agent.llm = SimpleNamespace(generate_response=fake_response)
    metrics = {"business_id": "shop-a", "revenue": 200, "total_orders": 1}

    insights = agent.generate_business_insights("shop-a", metrics)

    assert insights["summary"] == "Recorded revenue is ₹200."
    assert captured["kwargs"]["temperature"] == 0.2
    assert '"revenue": 200' in captured["messages"][1]["content"]
    assert "14 days" in captured["messages"][1]["content"]


def test_business_insight_llm_failure_is_reported(monkeypatch):
    agent = AIAgent.__new__(AIAgent)
    agent.tools = SimpleNamespace(get_business_context=lambda *args: None)
    agent.llm = SimpleNamespace(
        generate_response=lambda *args, **kwargs: "Sorry, something went wrong while generating the response."
    )

    with pytest.raises(RuntimeError, match="LLM"):
        agent.generate_business_insights("shop-a", {"revenue": 0})


def main():

    business = BusinessController()

    shop = business.create_business(
        "ABC Mobiles",
        "Electronics"
    )

    business_id = shop["business_id"]

    print("Business Created:")
    print(shop)

    business.add_document(
        business_id,
        "data/documents/ABC_Mobiles/products.txt"
    )

    print("\nDocument uploaded successfully!")

    agent = AIAgent()

    while True:

        query = input("\nUser: ")

        if query.lower() == "exit":
            break

        response = agent.chat(
            business_id,
            query
        )

        print("\nAI:", response)


if __name__ == "__main__":
    main()