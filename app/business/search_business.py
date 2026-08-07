import json

from app.business.register_business import BUSINESS_FILE


class BusinessSearch:

    def __init__(self):

        self.file = BUSINESS_FILE


    def search_by_id(
        self,
        business_id
    ):

        with open(
            self.file,
            "r"
        ) as file:

            businesses = json.load(file)


        for business in businesses:

            if business["business_id"] == business_id:

                return business


        return None


    def search_by_name(
        self,
        name
    ):

        with open(
            self.file,
            "r"
        ) as file:

            businesses = json.load(file)


        results = []


        for business in businesses:

            if name.lower() in business["name"].lower():

                results.append(
                    business
                )


        return results