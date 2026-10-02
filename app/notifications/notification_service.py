from app.database import Notification, get_session


def _serialize(notification):
    return {
        "notification_id": notification.notification_id,
        "business_id": notification.business_id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "reference_type": notification.reference_type,
        "reference_id": notification.reference_id,
        "is_read": bool(notification.is_read),
        "created_at": notification.created_at,
    }


def create_notification(business_id, notification_type, title, message, reference_type=None, reference_id=None):
    if not business_id:
        raise ValueError("business_id is required.")

    session = get_session()
    try:
        existing = None
        if reference_type and reference_id and title:
            existing = (
                session.query(Notification)
                .filter(
                    Notification.business_id == business_id,
                    Notification.type == notification_type,
                    Notification.reference_type == reference_type,
                    Notification.reference_id == reference_id,
                    Notification.title == title,
                )
                .first()
            )
        if existing:
            return _serialize(existing)

        notification = Notification(
            business_id=business_id,
            type=notification_type,
            title=title,
            message=message,
            reference_type=reference_type,
            reference_id=reference_id,
            is_read=False,
        )
        session.add(notification)
        session.commit()
        session.refresh(notification)
        return _serialize(notification)
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_notifications_for_business(business_id, unread_only=False, limit=None):
    if not business_id:
        return []

    session = get_session()
    try:
        query = session.query(Notification).filter(Notification.business_id == business_id)
        if unread_only:
            query = query.filter(Notification.is_read.is_(False))
        query = query.order_by(Notification.created_at.desc())
        if limit is not None:
            query = query.limit(limit)
        return [_serialize(item) for item in query.all()]
    finally:
        session.close()


def get_unread_count(business_id):
    return len(get_notifications_for_business(business_id, unread_only=True))


def mark_as_read(business_id, notification_id):
    session = get_session()
    try:
        notification = (
            session.query(Notification)
            .filter(
                Notification.business_id == business_id,
                Notification.notification_id == notification_id,
            )
            .first()
        )
        if notification is None:
            return None
        notification.is_read = True
        session.commit()
        return _serialize(notification)
    finally:
        session.close()


def mark_all_as_read(business_id):
    session = get_session()
    try:
        updated = (
            session.query(Notification)
            .filter(
                Notification.business_id == business_id,
                Notification.is_read.is_(False),
            )
            .update({"is_read": True})
        )
        session.commit()
        return updated
    finally:
        session.close()
