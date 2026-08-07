import uuid
import json
import os


BUSINESS_FILE = "data/businesses.json"


class BusinessManager:

    def __init__(self):

        if not os.path.exists(BUSINESS_FILE):

            with open(
                BUSINESS_FILE,
                "w"
            ) as file:

                json.dump([], file)


    def register_business(
        self,
        name,
        category
    ):

        business_id = str(
            uuid.uuid4()
        )


        business = {

            "business_id": business_id,

            "name": name,

            "category": category

        }


        with open(
            BUSINESS_FILE,
            "r"
        ) as file:

            businesses = json.load(file)


        businesses.append(
            business
        )


        with open(
            BUSINESS_FILE,
            "w"
        ) as file:

            json.dump(
                businesses,
                file,
                indent=4
            )


        return business