from datetime import timedelta
import smtplib

import pytest

from app.database import EmailVerification, RateLimitEvent, get_session
from app.utils.email_verification import (
    EmailVerificationError,
    send_email_verification_code,
    verify_email_verification_code,
)
from app.utils.time import utc_now_for_database


class FakeSMTP:
    sent_messages = []

    def __init__(self, *args, **kwargs):
        self.args = args

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def login(self, username, password):
        self.username = username

    def send_message(self, message):
        self.sent_messages.append(message)


def _configure_mail(monkeypatch):
    monkeypatch.setenv("SMTP_USERNAME", "smartbiz@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "test-only-app-password")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "smartbiz@example.com")
    monkeypatch.setenv("EMAIL_OTP_HASH_SECRET", "test-secret-" * 6)


def test_email_code_is_sent_as_mail_and_consumed_once(isolated_database, monkeypatch):
    _configure_mail(monkeypatch)
    FakeSMTP.sent_messages = []
    monkeypatch.setattr(
        "app.utils.email_verification.smtplib.SMTP_SSL",
        FakeSMTP,
    )
    monkeypatch.setattr(
        "app.utils.email_verification.secrets.randbelow",
        lambda upper_bound: 12345,
    )
    now = utc_now_for_database()

    send_email_verification_code(
        "Person@Example.com",
        "192.0.2.10",
        now=now,
    )

    assert len(FakeSMTP.sent_messages) == 1
    message = FakeSMTP.sent_messages[0]
    assert message["To"] == "person@example.com"
    assert "012345" in message.get_content()
    assert "SMTP_PASSWORD" not in message.get_content()

    session = get_session()
    try:
        stored = session.get(EmailVerification, "person@example.com")
        assert stored is not None
        assert stored.code_hash != "012345"
        assert "012345" not in stored.code_hash
    finally:
        session.close()

    assert verify_email_verification_code(
        "PERSON@example.com",
        "012345",
        now=now + timedelta(seconds=1),
    )
    assert not verify_email_verification_code(
        "person@example.com",
        "012345",
        now=now + timedelta(seconds=2),
    )


def test_email_code_expires(isolated_database, monkeypatch):
    _configure_mail(monkeypatch)
    monkeypatch.setattr("app.utils.email_verification.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(
        "app.utils.email_verification.secrets.randbelow",
        lambda upper_bound: 654321,
    )
    now = utc_now_for_database()
    send_email_verification_code("expiry@example.com", now=now)

    assert not verify_email_verification_code(
        "expiry@example.com",
        "654321",
        now=now + timedelta(minutes=11),
    )
    session = get_session()
    try:
        assert session.get(EmailVerification, "expiry@example.com") is None
    finally:
        session.close()


def test_email_code_attempt_limit_invalidates_challenge(isolated_database, monkeypatch):
    _configure_mail(monkeypatch)
    monkeypatch.setenv("EMAIL_OTP_MAX_ATTEMPTS", "2")
    monkeypatch.setattr("app.utils.email_verification.smtplib.SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(
        "app.utils.email_verification.secrets.randbelow",
        lambda upper_bound: 876543,
    )
    now = utc_now_for_database()
    send_email_verification_code("attempts@example.com", now=now)

    assert not verify_email_verification_code(
        "attempts@example.com",
        "000000",
        now=now + timedelta(seconds=1),
    )
    assert not verify_email_verification_code(
        "attempts@example.com",
        "000000",
        now=now + timedelta(seconds=2),
    )
    assert not verify_email_verification_code(
        "attempts@example.com",
        "876543",
        now=now + timedelta(seconds=3),
    )


def test_resend_cooldown_and_hourly_email_limit(isolated_database, monkeypatch):
    _configure_mail(monkeypatch)
    monkeypatch.setenv("EMAIL_OTP_COOLDOWN_SECONDS", "1")
    monkeypatch.setenv("EMAIL_OTP_EMAIL_HOURLY_LIMIT", "1")
    monkeypatch.setattr("app.utils.email_verification.smtplib.SMTP_SSL", FakeSMTP)
    now = utc_now_for_database()

    send_email_verification_code("rate@example.com", now=now)
    with pytest.raises(EmailVerificationError, match="wait"):
        send_email_verification_code(
            "rate@example.com",
            now=now + timedelta(milliseconds=500),
        )
    with pytest.raises(EmailVerificationError, match="Too many"):
        send_email_verification_code(
            "rate@example.com",
            now=now + timedelta(seconds=2),
        )

    session = get_session()
    try:
        assert session.query(RateLimitEvent).filter(
            RateLimitEvent.event_type == "email_otp_request_email"
        ).count() == 1
    finally:
        session.close()


def test_email_verification_has_per_ip_hourly_limit(isolated_database, monkeypatch):
    _configure_mail(monkeypatch)
    monkeypatch.setenv("EMAIL_OTP_IP_HOURLY_LIMIT", "1")
    monkeypatch.setattr("app.utils.email_verification.smtplib.SMTP_SSL", FakeSMTP)
    now = utc_now_for_database()

    send_email_verification_code("first@example.com", "192.0.2.11", now=now)
    with pytest.raises(EmailVerificationError, match="Too many"):
        send_email_verification_code(
            "second@example.com",
            "192.0.2.11",
            now=now + timedelta(seconds=61),
        )


def test_failed_mail_delivery_removes_challenge_and_keeps_safe_error(
    isolated_database,
    monkeypatch,
):
    _configure_mail(monkeypatch)

    class FailingSMTP(FakeSMTP):
        def send_message(self, message):
            raise smtplib.SMTPException("sensitive server response")

    monkeypatch.setattr("app.utils.email_verification.smtplib.SMTP_SSL", FailingSMTP)
    with pytest.raises(EmailVerificationError, match="could not send"):
        send_email_verification_code("failure@example.com")

    session = get_session()
    try:
        assert session.get(EmailVerification, "failure@example.com") is None
    finally:
        session.close()


def test_otp_configuration_can_be_loaded_from_streamlit_secrets(monkeypatch):
    from app.config import _setting_value

    class FakeSecrets:
        @staticmethod
        def get(name):
            return "secret-config-value" if name == "SMTP_PASSWORD" else None

    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    monkeypatch.setattr("streamlit.secrets", FakeSecrets())

    assert _setting_value("SMTP_PASSWORD") == "secret-config-value"
    monkeypatch.setenv("SMTP_PASSWORD", "environment-value")
    assert _setting_value("SMTP_PASSWORD") == "environment-value"
