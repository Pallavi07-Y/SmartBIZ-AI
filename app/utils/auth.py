import hashlib
import secrets
import string
from datetime import datetime, timedelta

import bcrypt


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


def generate_business_id():
    alphabet = string.ascii_uppercase + string.digits
    suffix = "".join(secrets.choice(alphabet) for _ in range(6))
    return f"SB-{suffix}"


def generate_mock_otp(length=6):
    return "".join(secrets.choice(string.digits) for _ in range(length))


def hash_otp(otp):
    if not isinstance(otp, str):
        return ""
    return hashlib.sha256(otp.strip().encode("utf-8")).hexdigest()


def verify_otp(candidate, expected_hash):
    if not isinstance(candidate, str) or not isinstance(expected_hash, str):
        return False
    return hash_otp(candidate) == expected_hash


def issue_mock_otp(phone_number, *, ttl_seconds=300, cooldown_seconds=30):
    if not isinstance(phone_number, str):
        raise TypeError("Phone number must be a string.")

    from app.database import OtpVerification, get_session

    normalized_phone = normalize_phone(phone_number)
    if not normalized_phone:
        raise ValueError("Phone number is required.")

    otp_code = generate_mock_otp()
    otp_hash = hash_otp(otp_code)
    now = datetime.utcnow()

    session = get_session()
    try:
        otp_record = session.query(OtpVerification).filter(OtpVerification.phone_number == normalized_phone).first()
        if (
            otp_record is not None
            and otp_record.last_sent_at is not None
            and now - otp_record.last_sent_at < timedelta(seconds=cooldown_seconds)
        ):
            raise ValueError("Please wait before requesting a new OTP.")
        if otp_record is None:
            otp_record = OtpVerification(
                phone_number=normalized_phone,
                otp_hash=otp_hash,
                expires_at=now + timedelta(seconds=ttl_seconds),
                attempts_used=0,
                max_attempts=5,
                last_sent_at=now,
            )
            session.add(otp_record)
        else:
            otp_record.otp_hash = otp_hash
            otp_record.expires_at = now + timedelta(seconds=ttl_seconds)
            otp_record.attempts_used = 0
            otp_record.max_attempts = 5
            otp_record.last_sent_at = now
            otp_record.verified_at = None

        session.commit()
        return {
            "otp_code": otp_code,
            "phone_number": normalized_phone,
            "expires_at": otp_record.expires_at,
            "attempts_used": otp_record.attempts_used,
            "max_attempts": otp_record.max_attempts,
        }
    finally:
        session.close()


def verify_phone_otp(phone_number, candidate_otp, *, max_attempts=5):
    from app.database import OtpVerification, get_session

    normalized_phone = normalize_phone(phone_number)
    if not normalized_phone:
        return False

    session = get_session()
    try:
        otp_record = session.query(OtpVerification).filter(OtpVerification.phone_number == normalized_phone).first()
        if otp_record is None:
            return False
        if otp_record.expires_at <= datetime.utcnow():
            session.delete(otp_record)
            session.commit()
            return False
        if otp_record.attempts_used >= max_attempts:
            return False

        is_valid = verify_otp(candidate_otp, otp_record.otp_hash)
        otp_record.attempts_used += 1
        if is_valid:
            otp_record.verified_at = datetime.utcnow()
            session.commit()
            return True

        session.commit()
        return False
    finally:
        session.close()