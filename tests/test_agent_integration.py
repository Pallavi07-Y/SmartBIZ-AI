import pytest

import app.database as database_module
import app.agent.ai_agent as ai_agent_module
from app.database import configure_database
from app.rag.rag_pipeline import RAGPipeline


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


class InMemoryBusinessTools:
    def __init__(self):
        self.rag = self
        self.classifier = RAGPipeline.__new__(RAGPipeline)
        self.calls = []
        self.documents = {
            "products": "Products: Samsung Galaxy S25 ₹75,000; iPhone 16 ₹79,000; OnePlus 13 ₹65,000.",
            "specifications": "Samsung Galaxy S25 has 12GB RAM and a 1-year warranty.",
            "availability": "Samsung Galaxy S25 is in stock.",
            "delivery": "Home delivery is available.",
            "hours": "Working hours are 10:00 AM to 8:00 PM.",
            "oneplus": "OnePlus 13 is available for ₹65,000.",
        }

    def is_business_query(self, query):
        return self.classifier.is_business_query(query)

    def get_business_context(self, business_id, query):
        self.calls.append((business_id, query))
        text = query.lower()

        if "macbook" in text:
            return ""

        matching_keys = []

        if any(term in text for term in ["product", "sell", "under", "cheapest", "price", "cost", "offer", "about"]):
            matching_keys.append("products")
        if any(term in text for term in ["ram", "warranty", "specification", "samsung galaxy s25", "iphone 16", "oneplus 13", "tell me about"]):
            matching_keys.append("specifications")
        if any(term in text for term in ["available", "stock", "do you have", "have", "in stock"]):
            matching_keys.append("availability")
        if "delivery" in text:
            matching_keys.append("delivery")
        if "working hours" in text or "open" in text:
            matching_keys.append("hours")
        if "oneplus" in text:
            matching_keys.append("oneplus")

        return "\n".join(self.documents[key] for key in dict.fromkeys(matching_keys))


class FakeLLM:
    instances = []

    def __init__(self):
        self.messages = []
        self.__class__.instances.append(self)

    def generate_response(self, messages, **kwargs):
        self.messages.append(messages)
        business_message = messages[-1]["content"]
        return f"mocked response: {business_message}"


@pytest.fixture
def isolated_agent(monkeypatch):
    tools = InMemoryBusinessTools()
    FakeLLM.instances.clear()
    monkeypatch.setattr(ai_agent_module, "AgentTools", lambda: tools)
    monkeypatch.setattr(ai_agent_module, "LLM", FakeLLM)
    return ai_agent_module.AIAgent(), tools


@pytest.mark.parametrize(
    ("query", "expected_context"),
    [
        ("What products do you sell?", "Products:"),
        ("What is the price of Samsung Galaxy S25?", "Samsung Galaxy S25"),
        ("How much RAM does Samsung Galaxy S25 have?", "12GB RAM"),
        ("What is the price of iPhone 16?", "iPhone 16"),
        ("Which phone is the cheapest?", "OnePlus 13"),
        ("Do you provide home delivery?", "Home delivery"),
        ("What are your working hours?", "Working hours"),
        ("What products do you have under ₹70,000?", "OnePlus 13"),
        ("Tell me about OnePlus 13.", "OnePlus 13"),
        ("What is the warranty of Samsung Galaxy S25?", "1-year warranty"),
        ("Do you have iPhone 16?", "iPhone 16"),
        ("Tell me about Samsung Galaxy S25.", "Samsung Galaxy S25"),
    ],
)
def test_agent_business_questions_use_isolated_context(
    isolated_agent, query, expected_context
):
    agent, tools = isolated_agent

    response = agent.chat("test-business", query)

    assert tools.calls[-1] == ("test-business", query)
    assert response["signal"] == "reply"
    assert expected_context in response["reply"]
    assert FakeLLM.instances[-1].messages


def test_agent_returns_missing_information_without_calling_llm(isolated_agent):
    agent, tools = isolated_agent

    response = agent.chat("test-business", "What is the price of a MacBook?")

    assert response == {
        "signal": "reply",
        "reply": "I don't have that information in the business knowledge base.",
    }
    assert tools.calls[-1] == ("test-business", "What is the price of a MacBook?")
    assert FakeLLM.instances[-1].messages == []