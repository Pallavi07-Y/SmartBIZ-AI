from app.database.business_db import BusinessDB
from app.utils.location import distance


class NearbyBusinesses:

    def __init__(self):

        self.db = BusinessDB()

    def search(self, latitude, longitude):

        shops = self.db.get_all()

        result = []

        for shop in shops:

            d = distance(
                latitude,
                longitude,
                shop["latitude"],
                shop["longitude"]
            )

            shop["distance"] = round(d, 2)

            result.append(shop)

        result.sort(key=lambda x: x["distance"])

        return result[:5]