from app.database import Business, configure_database, get_session


class BusinessDB:
    def __init__(self):
        configure_database()

    def add_business(self, business):
        session = get_session()
        try:
            session.add(Business(
                business_id=business["business_id"],
                name=business["name"],
                category=business.get("category"),
                address=business.get("address"),
                latitude=business.get("latitude"),
                longitude=business.get("longitude"),
                location_precision=business.get("location_precision"),
                owner_phone=business.get("owner_phone"),
                username=business.get("username"),
                password_hash=business.get("password_hash"),
            ))
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_all(self):
        session = get_session()
        try:
            businesses = session.query(Business).all()
            return [
                {
                    "business_id": business.business_id,
                    "name": business.name,
                    "category": business.category,
                    "address": business.address,
                    "latitude": business.latitude,
                    "longitude": business.longitude,
                    "location_precision": business.location_precision,
                    "owner_phone": business.owner_phone,
                    "username": business.username,
                    "password_hash": business.password_hash,
                    "created_at": business.created_at,
                }
                for business in businesses
            ]
        finally:
            session.close()