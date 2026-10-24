import hashlib
import hmac
import logging
import re
import secrets
import smtplib
import ssl
from datetime import timedelta
from email.message import EmailMessage

from app.config import settings
from app.utils.auth import _account_subject_key, _ip_subject_key, normalize_email
from app.utils.time import utc_now_for_database


logger = logging.getLogger(__name__)
_CODE_PATTERN = re.compile(r"\A[0-9]{6}\Z")
_EMAIL_RATE_EVENT = "email_otp_request_email"
_IP_RATE_EVENT = "email_otp_request_ip"


class EmailVerificationError(ValueError):
    pass


def _verification_configuration():
    try:
        smtp_host = settings.smtp_host
        smtp_port = settings.smtp_port
    except ValueError as exc:
        raise EmailVerificationError(
            "Email verification settings are invalid. Ask the administrator to check the mail settings."
        ) from exc

    required = {
        "SMTP_USERNAME": settings.smtp_username,
        "SMTP_PASSWORD": settings.smtp_password,
        "SMTP_FROM_EMAIL": settings.smtp_from_email,
        "EMAIL_OTP_HASH_SECRET": settings.email_otp_hash_secret,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise EmailVerificationError(
            "Email verification is not configured. Ask the administrator to check the mail settings."
        )
    secret = required["EMAIL_OTP_HASH_SECRET"]
    if not all(isinstance(value, str) for value in required.values()):
        raise EmailVerificationError(
            "Email verification is not configured. Ask the administrator to check the mail settings."
        )
    if len(secret.encode("utf-8")) < 32:
        raise EmailVerificationError(
            "Email verification is not configured. Ask the administrator to check the mail settings."
        )
    if (
        not isinstance(smtp_host, str)
        or not smtp_host.strip()
        or not 1 <= smtp_port <= 65535
        or not normalize_email(required["SMTP_FROM_EMAIL"])
    ):
        raise EmailVerificationError(
            "Email verification settings are invalid. Ask the administrator to check the mail settings."
        )
    required["SMTP_HOST"] = smtp_host
    required["SMTP_PORT"] = smtp_port
    return required


def _otp_policy():
    try:
        return {
            "ttl_seconds": settings.email_otp_ttl_seconds,
            "max_attempts": settings.email_otp_max_attempts,
            "cooldown_seconds": settings.email_otp_cooldown_seconds,
            "email_hourly_limit": settings.email_otp_email_hourly_limit,
            "ip_hourly_limit": settings.email_otp_ip_hourly_limit,
        }
    except ValueError as exc:
        raise EmailVerificationError(
            "Email verification settings are invalid. Ask the administrator to check the mail settings."
        ) from exc


def _code_digest(email, code, secret):
    message = f"{email}:{code}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def _send_message(message, configuration):
    host = configuration["SMTP_HOST"]
    port = configuration["SMTP_PORT"]
    username = configuration["SMTP_USERNAME"]
    password = configuration["SMTP_PASSWORD"]

    if port == 465:
        with smtplib.SMTP_SSL(
            host,
            port,
            timeout=15,
            context=ssl.create_default_context(),
        ) as client:
            client.login(username, password)
            client.send_message(message)
        return

    with smtplib.SMTP(host, port, timeout=15) as client:
        client.ehlo()
        client.starttls(context=ssl.create_default_context())
        client.ehlo()
        client.login(username, password)
        client.send_message(message)


def send_email_verification_code(email, client_ip=None, *, now=None):
    from app.database import EmailVerification, RateLimitEvent, get_session

    normalized_email = normalize_email(email)
    if not normalized_email:
        raise EmailVerificationError("Enter a valid email address.")

    configuration = _verification_configuration()
    policy = _otp_policy()
    now = now or utc_now_for_database()
    email_subject = _account_subject_key(normalized_email)
    ip_subject = _ip_subject_key(client_ip)
    session = get_session()
    try:
        session.query(RateLimitEvent).filter(
            RateLimitEvent.created_at < now - timedelta(days=1)
        ).delete(synchronize_session=False)

        current_verification = (
            session.query(EmailVerification)
            .filter(EmailVerification.email == normalized_email)
            .first()
        )
        if current_verification and (
            now - current_verification.last_sent_at
        ).total_seconds() < policy["cooldown_seconds"]:
            raise EmailVerificationError(
                "Please wait before requesting another verification email."
            )

        cutoff = now - timedelta(hours=1)
        email_requests = (
            session.query(RateLimitEvent)
            .filter(
                RateLimitEvent.event_type == _EMAIL_RATE_EVENT,
                RateLimitEvent.subject_key == email_subject,
                RateLimitEvent.created_at >= cutoff,
            )
            .count()
        )
        if email_requests >= policy["email_hourly_limit"]:
            raise EmailVerificationError(
                "Too many verification emails were requested. Try again later."
            )

        if ip_subject:
            ip_requests = (
                session.query(RateLimitEvent)
                .filter(
                    RateLimitEvent.event_type == _IP_RATE_EVENT,
                    RateLimitEvent.subject_key == ip_subject,
                    RateLimitEvent.created_at >= cutoff,
                )
                .count()
            )
            if ip_requests >= policy["ip_hourly_limit"]:
                raise EmailVerificationError(
                    "Too many verification emails were requested. Try again later."
                )

        code = f"{secrets.randbelow(1_000_000):06d}"
        session.query(EmailVerification).filter(
            EmailVerification.email == normalized_email
        ).delete(synchronize_session=False)
        session.add(
            EmailVerification(
                email=normalized_email,
                code_hash=_code_digest(
                    normalized_email,
                    code,
                    configuration["EMAIL_OTP_HASH_SECRET"],
                ),
                created_at=now,
                expires_at=now + timedelta(seconds=policy["ttl_seconds"]),
                last_sent_at=now,
                attempt_count=0,
            )
        )
        session.add(
            RateLimitEvent(
                event_type=_EMAIL_RATE_EVENT,
                subject_key=email_subject,
                created_at=now,
            )
        )
        if ip_subject:
            session.add(
                RateLimitEvent(
                    event_type=_IP_RATE_EVENT,
                    subject_key=ip_subject,
                    created_at=now,
                )
            )
        session.commit()
    except EmailVerificationError:
        session.rollback()
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()

    message = EmailMessage()
    message["Subject"] = "Your SmartBiz AI verification code"
    message["From"] = configuration["SMTP_FROM_EMAIL"]
    message["To"] = normalized_email
    message.set_content(
        "Use the six-digit verification code below to complete your SmartBiz AI "
        f"signup. It expires in {max(1, policy['ttl_seconds'] // 60)} minutes.\n\n"
        f"{code}\n\nIf you did not request this, you can ignore this message."
    )

    try:
        _send_message(message, configuration)
    except (smtplib.SMTPException, OSError) as exc:
        cleanup_session = get_session()
        try:
            cleanup_session.query(EmailVerification).filter(
                EmailVerification.email == normalized_email
            ).delete(synchronize_session=False)
            cleanup_session.commit()
        except Exception as cleanup_error:
            cleanup_session.rollback()
            logger.error(
                "Could not remove an undelivered email verification challenge (%s).",
                type(cleanup_error).__name__,
            )
        finally:
            cleanup_session.close()
        logger.warning(
            "Email verification delivery failed (%s).",
            type(exc).__name__,
        )
        raise EmailVerificationError(
            "We could not send the verification email. Check the mail settings or try again later."
        ) from exc


def verify_email_verification_code(email, code, *, now=None):
    from app.database import EmailVerification, get_session

    normalized_email = normalize_email(email)
    if not normalized_email or not isinstance(code, str) or not _CODE_PATTERN.fullmatch(code):
        return False

    secret = settings.email_otp_hash_secret
    if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
        raise EmailVerificationError(
            "Email verification is not configured. Ask the administrator to check the mail settings."
        )
    policy = _otp_policy()

    now = now or utc_now_for_database()
    session = get_session()
    try:
        verification = (
            session.query(EmailVerification)
            .filter(EmailVerification.email == normalized_email)
            .first()
        )
        if verification is None:
            return False
        if verification.expires_at <= now:
            session.delete(verification)
            session.commit()
            return False
        if verification.attempt_count >= policy["max_attempts"]:
            session.delete(verification)
            session.commit()
            return False

        candidate_hash = _code_digest(normalized_email, code, secret)
        if not hmac.compare_digest(verification.code_hash, candidate_hash):
            verification.attempt_count += 1
            if verification.attempt_count >= policy["max_attempts"]:
                session.delete(verification)
            session.commit()
            return False

        session.delete(verification)
        session.commit()
        return True
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
