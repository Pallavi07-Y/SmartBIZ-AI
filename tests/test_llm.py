from app.llm.llm import LLM


def test_llm():

    llm = LLM()

    response = llm.generate_response(
        [
            {
                "role": "user",
                "content": "Hello"
            }
        ]
    )

    print(response)


if __name__ == "__main__":
    test_llm()