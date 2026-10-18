from types import SimpleNamespace

from app.llm.llm import LLM


def test_llm_uses_configured_model_and_returns_response():
    llm = LLM.__new__(LLM)
    request = {}

    def create_completion(**kwargs):
        request.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Hello from the demo model."))]
        )

    llm.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create_completion))
    )
    llm.model = "demo-model"
    messages = [{"role": "user", "content": "Hello"}]

    response = llm.generate_response(messages)

    assert response == "Hello from the demo model."
    assert request == {
        "model": "demo-model",
        "messages": messages,
        "temperature": 0.7,
        "max_tokens": 1024,
    }