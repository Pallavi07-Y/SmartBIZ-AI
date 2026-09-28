import uuid

from app.database import Business, configure_database, get_session


class BusinessManager:

    def __init__(self):
        configure_database()


    def register_business(
        self,
        name,
        category,
        address=None,
        owner_phone=None,
        username=None,
        password_hash=None,
    ):
        business_id = str(
            uuid.uuid4()
        )
        session = get_session()
        try:
            business = Business(
                business_id=business_id,
                name=name,
                category=category,
                address=address,
                owner_phone=owner_phone,
                username=username,
                password_hash=password_hash,
            )
            session.add(business)
            session.commit()
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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
        return business