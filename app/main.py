"""
main.py

Entry point of the AI Business Agent
"""

from app.agent.ai_agent import AIAgent


def main():

    agent = AIAgent()

    business_id = input("Business ID: ").strip()

    while True:

        user_input = input("User: ")

        if user_input.lower() == "exit":
            break

        response = agent.chat(business_id, user_input)

        print(f"AI: {response}")


if __name__ == "__main__":
    main()