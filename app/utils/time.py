from datetime import UTC, datetime


def utc_now_for_database():
    return datetime.now(UTC).replace(tzinfo=None)
