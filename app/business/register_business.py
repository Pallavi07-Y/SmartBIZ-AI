from app.database import Business, configure_database, get_session
from app.utils.auth import generate_business_id, normalize_phone
from app.utils.location import validate_coordinates


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
        owner_name=None,
        business_id=None,
        latitude=None,
        longitude=None,
    ):
        if (latitude is None or str(latitude).strip() == "") and (
            longitude is None or str(longitude).strip() == ""
        ):
            latitude = longitude = None
        elif latitude is None or str(latitude).strip() == "" or longitude is None or str(longitude).strip() == "":
            raise ValueError("Enter both shop coordinates, or leave both blank.")
        else:
            latitude, longitude = validate_coordinates(latitude, longitude)

        session = get_session()
        try:
            normalized_phone = normalize_phone(owner_phone)
            if normalized_phone:
                existing_phones = session.query(Business.owner_phone).filter(Business.owner_phone.is_not(None)).all()
                if any(normalize_phone(phone[0]) == normalized_phone for phone in existing_phones):
                    raise ValueError("A shopkeeper account already exists for this phone number.")

            generated_business_id = business_id or generate_business_id()
            while session.query(Business).filter(Business.business_id == generated_business_id).first() is not None:
                generated_business_id = generate_business_id()

            business = Business(
                business_id=generated_business_id,
                name=name,
                owner_name=owner_name,
                category=category,
                address=address,
                latitude=latitude,
                longitude=longitude,
                location_precision="shop" if latitude is not None else None,
                owner_phone=normalized_phone or owner_phone,
                username=username or f"shop-{generated_business_id.lower()}",
                password_hash=password_hash,
            )
            session.add(business)
            session.commit()
            return {
                "business_id": business.business_id,
                "name": business.name,
                "owner_name": business.owner_name,
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
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()