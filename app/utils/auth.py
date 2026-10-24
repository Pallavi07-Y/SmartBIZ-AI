import hashlib
import ipaddress
import logging
import re
import secrets
import string
from datetime import timedelta

import bcrypt

from app.config import settings
from app.utils.time import utc_now_for_database


logger = logging.getLogger(__name__)
EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+"
)


def hash_password(password):
    if not isinstance(password, str):
        raise TypeError("Password must be a string.")

    return bcrypt.hashpw(
        password.encode("utf-8"),
        bcrypt.gensalt(),
    ).decode("utf-8")


def verify_password(password, stored_hash):
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False

    try:
        return bcrypt.checkpw(
            password.encode("utf-8"),
            stored_hash.encode("utf-8"),
        )
    except (ValueError, TypeError, UnicodeError):
        return False


def normalize_phone(phone):
    if phone is None:
        return ""
    digits = "".join(ch for ch in str(phone) if ch.isdigit())
    return digits


def normalize_email(email):
    if not isinstance(email, str):
        return ""
    normalized_email = email.strip().casefold()
    if not EMAIL_PATTERN.fullmatch(normalized_email):
        return ""
    return normalized_email


def generate_business_id():
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"SB-{suffix}"


def generate_account_id(role):
    prefixes = {"customer": "SBZ-U", "shopkeeper": "SBZ-S"}
    try:
        prefix = prefixes[role]
    except KeyError as exc:
        raise ValueError("Unsupported account role.") from exc
    return f"{prefix}-{secrets.token_hex(6).upper()}"


def register_customer_account(name, email, password, *, email_verified=True):
    from app.database import Business, CustomerAccount, get_session

    normalized_email = normalize_email(email)
    if not normalized_email:
        raise ValueError("Enter a valid email address.")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Enter your name.")
    if not isinstance(password, str) or len(password) < 8:
        raise ValueError("Password must be at least 8 characters long.")

    session = get_session()
    try:
        existing_business_email = session.query(Business.business_id).filter(
            Business.owner_email == normalized_email
        ).first()
        existing_customer = session.query(CustomerAccount.customer_id).filter(
            CustomerAccount.email == normalized_email
        ).first()
        if existing_business_email or existing_customer:
            raise ValueError("An account already exists for this email address.")

        customer_id = generate_account_id("customer")
        while session.query(CustomerAccount.customer_id).filter(
            CustomerAccount.customer_id == customer_id
        ).first():
            customer_id = generate_account_id("customer")

        account = CustomerAccount(
            customer_id=customer_id,
            name=name.strip(),
            email=normalized_email,
            email_verified=bool(email_verified),
            password_hash=hash_password(password),
        )
        session.add(account)
        session.commit()
        return {"customer_id": account.customer_id, "name": account.name}
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def authenticate_customer_account(identifier, password):
    from sqlalchemy import or_

    from app.database import CustomerAccount, get_session

    if not isinstance(identifier, str) or not isinstance(password, str):
        return None
    cleaned_identifier = identifier.strip()
    if not cleaned_identifier:
        return None
    normalized_email = normalize_email(cleaned_identifier)

    session = get_session()
    try:
        predicates = [CustomerAccount.customer_id == cleaned_identifier]
        if normalized_email:
            predicates.append(CustomerAccount.email == normalized_email)
        account = session.query(CustomerAccount).filter(or_(*predicates)).first()
        if (
            account is None
            or not account.email_verified
            or not verify_password(password, account.password_hash)
        ):
            return None
        return {
            "customer_id": account.customer_id,
            "name": account.name,
            "role": "customer",
        }
    finally:
        session.close()


def _ip_subject_key(client_ip):
    if not isinstance(client_ip, str) or not client_ip.strip():
        return None
    try:
        normalized_ip = ipaddress.ip_address(client_ip.strip()).compressed
    except ValueError:
        return None
    return f"ip:{hashlib.sha256(normalized_ip.encode('utf-8')).hexdigest()}"


def _account_subject_key(identifier):
    normalized_identifier = str(identifier).strip()
    normalized_email = normalize_email(normalized_identifier)
    if normalized_email:
        normalized_identifier = normalized_email
    digest = hashlib.sha256(normalized_identifier.encode("utf-8")).hexdigest()
    return f"account:{digest}"


def _record_rate_events(session, event_type, subject_keys, occurred_at):
    from app.database import RateLimitEvent

    session.add_all(
        RateLimitEvent(
            event_type=event_type,
            subject_key=subject_key,
            created_at=occurred_at,
        )
        for subject_key in subject_keys
        if subject_key
    )


def login_lockout_remaining(business_id, client_ip=None, *, now=None):
    from app.database import RateLimitEvent, get_session

    now = now or utc_now_for_database()
    cutoff = now - timedelta(seconds=settings.login_window_seconds)
    subjects = [
        (_account_subject_key(business_id), settings.login_account_attempt_limit),
    ]
    ip_key = _ip_subject_key(client_ip)
    if ip_key:
        subjects.append((ip_key, settings.login_ip_attempt_limit))

    session = get_session()
    try:
        remaining = 0
        for subject_key, attempt_limit in subjects:
            attempts = (
                session.query(RateLimitEvent)
                .filter(
                    RateLimitEvent.event_type == "login_failure",
                    RateLimitEvent.subject_key == subject_key,
                    RateLimitEvent.created_at >= cutoff,
                )
                .order_by(RateLimitEvent.created_at.asc())
                .all()
            )
            if len(attempts) >= attempt_limit:
                unlock_at = attempts[-1].created_at + timedelta(seconds=settings.login_lockout_seconds)
                remaining = max(remaining, int((unlock_at - now).total_seconds()))
        return max(0, remaining)
    finally:
        session.close()


def record_login_failure(business_id, client_ip=None, *, now=None):
    from app.database import RateLimitEvent, get_session

    now = now or utc_now_for_database()
    subjects = [_account_subject_key(business_id)]
    ip_key = _ip_subject_key(client_ip)
    if ip_key:
        subjects.append(ip_key)

    session = get_session()
    try:
        session.query(RateLimitEvent).filter(
            RateLimitEvent.created_at < now - timedelta(days=1)
        ).delete(synchronize_session=False)
        _record_rate_events(session, "login_failure", subjects, now)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def clear_login_failures(business_id):
    from app.database import RateLimitEvent, get_session

    session = get_session()
    try:
        session.query(RateLimitEvent).filter(
            RateLimitEvent.event_type == "login_failure",
            RateLimitEvent.subject_key == _account_subject_key(business_id),
        ).delete(synchronize_session=False)
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()