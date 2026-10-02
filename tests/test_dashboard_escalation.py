from app.agent.ai_agent import AIAgent
from app.business.register_business import BusinessManager
from app.dashboard import (
    get_escalated_conversations_for_business,
    save_shopkeeper_reply,
)
from app.database import Conversation, ConversationStatus, Message, MessageSender, get_session


def test_customer_escalation_sets_conversation_to_escalated(isolated_database):
    business = BusinessManager().register_business(
        "Alpha Shop",
        "Electronics",
        owner_phone="9876543210",
        username="alpha-owner",
        password_hash="hashed-password",
    )

    result = AIAgent().chat(
        business["business_id"],
        "I want to talk to the owner",
        customer_phone="9876543210",
    )

    assert result["signal"] == "awaiting_human"

    session = get_session()
    try:
        conversation = (
            session.query(Conversation)
            .filter(Conversation.business_id == business["business_id"])
            .order_by(Conversation.created_at.desc())
            .first()
        )
        assert conversation is not None
        assert conversation.status == ConversationStatus.ESCALATED
        assert conversation.customer_phone == "9876543210"
    finally:
        session.close()


def test_escalated_chat_retrieval_is_business_isolated(isolated_database):
    b1 = BusinessManager().register_business(
        "Shop A",
        "Electronics",
        owner_phone="111",
        username="shop-a",
        password_hash="hash-a",
    )
    b2 = BusinessManager().register_business(
        "Shop B",
        "Electronics",
        owner_phone="222",
        username="shop-b",
        password_hash="hash-b",
    )

    session = get_session()
    try:
        session.add_all(
            [
                Conversation(
                    business_id=b1["business_id"],
                    customer_phone="111111",
                    status=ConversationStatus.ESCALATED,
                ),
                Conversation(
                    business_id=b2["business_id"],
                    customer_phone="222222",
                    status=ConversationStatus.ESCALATED,
                ),
            ]
        )
        session.commit()
    finally:
        session.close()

    escalated_a = get_escalated_conversations_for_business(b1["business_id"])
    escalated_b = get_escalated_conversations_for_business(b2["business_id"])

    assert len(escalated_a) == 1
    assert len(escalated_b) == 1
    assert escalated_a[0].customer_phone == "111111"
    assert escalated_b[0].customer_phone == "222222"


def test_shopkeeper_reply_marks_conversation_keeper_handling(isolated_database):
    business = BusinessManager().register_business(
        "Alpha Shop",
        "Electronics",
        owner_phone="9876543210",
        username="alpha-owner-2",
        password_hash="hashed-password",
    )

    session = get_session()
    try:
        conversation = Conversation(
            business_id=business["business_id"],
            customer_phone="9876543210",
            status=ConversationStatus.ESCALATED,
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
    finally:
        session.close()

    save_shopkeeper_reply(business["business_id"], conversation.id, "Hello, I can help you.")

    session = get_session()
    try:
        updated = session.get(Conversation, conversation.id)
        assert updated.status == ConversationStatus.KEEPER_HANDLING
        messages = (
            session.query(Message)
            .filter(Message.conversation_id == conversation.id)
            .all()
        )
        assert len(messages) == 1
        assert messages[0].sender == MessageSender.KEEPER
        assert messages[0].text == "Hello, I can help you."
    finally:
        session.close()
