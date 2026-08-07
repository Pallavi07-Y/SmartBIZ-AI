import json
import uuid
import os


class BusinessDB:

    FILE = "data/businesses.json"

    def __init__(self):
        os.makedirs("data", exist_ok=True)

        if not os.path.exists(self.FILE):
            with open(self.FILE, "w") as f:
                json.dump([], f)

    def add_business(self, business):

        with open(self.FILE, "r") as f:
            businesses = json.load(f)

        businesses.append(business)

        with open(self.FILE, "w") as f:
            json.dump(businesses, f, indent=4)

    def get_all(self):

        with open(self.FILE, "r") as f:
            return json.load(f)