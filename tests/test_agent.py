from app.business.business_manager import BusinessController
from app.agent.ai_agent import AIAgent


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