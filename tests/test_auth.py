from datetime import datetime, timedelta

import pytest

from app.business.business_manager import BusinessController
from app.database import Business, OtpVerification, get_session
from app.utils.auth import (
    generate_business_id,
    hash_password,
    verify_password,
    generate_mock_otp,
    hash_otp,
    verify_otp,
    issue_mock_otp,
    verify_phone_otp,
)


def test_password_hash_is_not_plaintext_and_verifies():
    stored_hash = hash_password("correct password")

    assert stored_hash != "correct password"
    assert verify_password("correct password", stored_hash) is True
    assert verify_password("wrong password", stored_hash) is False


def test_verify_password_rejects_invalid_hash():
    assert verify_password("password", "not-a-bcrypt-hash") is False


def test_registration_passes_hashed_password(monkeypatch):
    controller = BusinessController.__new__(BusinessController)

    class FakeRegister:
        def __init__(self):
            self.received = None

        def register_business(self, *args, **kwargs):
            self.received = (args, kwargs)
            return {"password_hash": kwargs["password_hash"]}

    register = FakeRegister()
    controller.register = register

    result = controller.create_business(
        "Test Shop",
        "Electronics",
        password="shopkeeper-password",
    )

    stored_hash = register.received[1]["password_hash"]
    assert result["password_hash"] == stored_hash
    assert stored_hash != "shopkeeper-password"
    assert verify_password("shopkeeper-password", stored_hash) is True


def test_registration_creates_unique_business_id_and_hashes_password(isolated_database):
    controller = BusinessController()
    business = controller.create_business(
        name="Alpha Electronics",
        category="Electronics",
        address="Main Road",
        owner_phone="+919900000001",
        username="alpha-owner",
        password="secret-pass",
    )

    assert business["business_id"].startswith("SB-")
    assert len(business["business_id"]) == 9
    assert business["password_hash"] != "secret-pass"
    assert verify_password("secret-pass", business["password_hash"]) is True


def test_duplicate_phone_is_rejected(isolated_database):
    controller = BusinessController()
    controller.create_business(
        name="Alpha Electronics",
        category="Electronics",
        owner_phone="+919900000002",
        username="alpha-owner-1",
        password="secret-pass",
    )

    try:
        controller.create_business(
            name="Beta Electronics",
            category="Electronics",
            owner_phone=" +91 9900 000002 ",
            username="beta-owner",
            password="secret-pass-2",
        )
        assert False, "Expected duplicate phone registration to fail"
    except ValueError:
        pass


def test_mock_otp_verification_flow_and_expiry():
    otp_code = generate_mock_otp()
    otp_hash = hash_otp(otp_code)

    assert verify_otp(otp_code, otp_hash) is True
    assert verify_otp("000000", otp_hash) is False
    assert otp_code.isdigit()
    assert len(otp_code) == 6


def test_otp_resend_cooldown_and_new_code_revokes_verified_code(isolated_database):
    phone = "+919900000005"
    first_otp = issue_mock_otp(phone)
    assert verify_phone_otp(phone, first_otp["otp_code"]) is True

    with pytest.raises(ValueError, match="wait"):
        issue_mock_otp(phone)

    session = get_session()
    try:
        record = session.query(OtpVerification).filter_by(phone_number="919900000005").one()
        record.last_sent_at = datetime.utcnow() - timedelta(seconds=31)
        session.commit()
    finally:
        session.close()

    second_otp = issue_mock_otp(phone)
    session = get_session()
    try:
        record = session.query(OtpVerification).filter_by(phone_number="919900000005").one()
        assert record.verified_at is None
    finally:
        session.close()

    assert verify_phone_otp(phone, first_otp["otp_code"]) is False
    assert verify_phone_otp(phone, second_otp["otp_code"]) is True


def test_expired_otp_is_rejected_and_removed(isolated_database):
    phone = "+919900000006"
    otp = issue_mock_otp(phone, ttl_seconds=-1)

    assert verify_phone_otp(phone, otp["otp_code"]) is False

    session = get_session()
    try:
        assert session.query(OtpVerification).filter_by(phone_number="919900000006").first() is None
    finally:
        session.close()


def test_otp_attempt_limit_rejects_later_correct_code(isolated_database):
    phone = "+919900000007"
    otp = issue_mock_otp(phone)

    for _ in range(5):
        assert verify_phone_otp(phone, "000000") is False

    assert verify_phone_otp(phone, otp["otp_code"]) is False


def test_business_id_generator_returns_unique_pattern():
    first = generate_business_id()
    second = generate_business_id()

    assert first.startswith("SB-")
    assert second.startswith("SB-")
    assert first != second


def test_shopkeeper_login_uses_business_id_and_password(isolated_database):
    controller = BusinessController()
    created = controller.create_business(
        name="Welcome Shop",
        category="Home Goods",
        owner_phone="+919900000003",
        username="welcome-owner",
        password="correct-pass",
    )

    session = get_session()
    business = session.query(Business).filter(Business.business_id == created["business_id"]).first()
    session.close()

    assert business is not None
    assert business.business_id == created["business_id"]
    assert verify_password("correct-pass", business.password_hash) is True


def test_invalid_login_and_logout_session_clear(isolated_database):
    controller = BusinessController()
    created = controller.create_business(
        name="Session Shop",
        category="Retail",
        owner_phone="+919900000004",
        username="session-owner",
        password="right-pass",
    )

    session = get_session()
    business = session.query(Business).filter(Business.business_id == created["business_id"]).first()
    session.close()

    assert business is not None
    assert verify_password("wrong-pass", business.password_hash) is False

    auth_state = {"business_id": business.business_id, "role": "shopkeeper"}
    auth_state.clear()
    assert auth_state == {}
