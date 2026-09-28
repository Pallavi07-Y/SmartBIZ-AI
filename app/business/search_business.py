from app.database import Business, configure_database, get_session


class BusinessSearch:

    def __init__(self):
        configure_database()


    def search_by_id(
        self,
        business_id
    ):

        session = get_session()
        try:
            business = session.get(Business, business_id)
            return self._to_dict(business) if business else None
        finally:
            session.close()


    def search_by_name(
        self,
        name
    ):

        session = get_session()
        try:
            businesses = session.query(Business).filter(
                Business.name.ilike(f"%{name}%")
            ).all()
            return [self._to_dict(business) for business in businesses]
        finally:
            session.close()

    @staticmethod
    def _to_dict(business):
        return {
            "business_id": business.business_id,
            "name": business.name,
            "category": business.category,
            "address": business.address,
            "owner_phone": business.owner_phone,
            "username": business.username,
            "password_hash": business.password_hash,
            "created_at": business.created_at,
        }