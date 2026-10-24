from datetime import timedelta

import pytest

from app.business.business_manager import BusinessController
from app.database import Business, CustomerAccount, RateLimitEvent, get_session
from app.utils.auth import (
    authenticate_customer_account,
    generate_account_id,
    hash_password,
    normalize_email,
    register_customer_account,
    verify_password,
)
from app.utils.time import utc_now_for_database


def _register_shopkeeper(email="shopkeeper@example.com", password="shopkeeper-pass"):
    business_id = generate_account_id("shopkeeper")
    business = BusinessController().create_business(
        name="Shopkeeper Test Shop",
        category="Retail",
        owner_email=email,
        password=password,
        business_id=business_id,
    )
    return business["business_id"]


def test_password_hash_is_not_plaintext_and_verifies():
    stored_hash = hash_password("correct password")

    assert stored_hash != "correct password"
    assert verify_password("correct password", stored_hash) is True
    assert verify_password("wrong password", stored_hash) is False


def test_verify_password_rejects_invalid_hash():
    assert verify_password("password", "not-a-bcrypt-hash") is False


def test_account_ids_use_role_specific_prefixes_and_random_identifiers():
    shopkeeper_id = generate_account_id("shopkeeper")
    customer_id = generate_account_id("customer")

    assert shopkeeper_id.startswith("SBZ-S-")
    assert customer_id.startswith("SBZ-U-")
    assert shopkeeper_id != generate_account_id("shopkeeper")
    assert customer_id != generate_account_id("customer")


def test_registration_passes_hashed_password(monkeypatch):
    controller = BusinessController.__new__(BusinessController)

    class FakeRegister:
        def register_business(self, *args, **kwargs):
            self.received = (args, kwargs)
            return {"business_id": "SBZ-S-TEST01"}

    controller.register = FakeRegister()
    result = controller.create_business(
        "Test Shop",
        "Electronics",
        password="shopkeeper-password",
    )

    stored_hash = controller.register.received[1]["password_hash"]
    assert "password_hash" not in result
    assert stored_hash != "shopkeeper-password"
    assert verify_password("shopkeeper-password", stored_hash) is True


def test_shopkeeper_can_login_with_email_or_account_id(isolated_database):
    email = "shopkeeper@example.com"
    shopkeeper_id = _register_shopkeeper(email)
    controller = BusinessController()

    by_email = controller.authenticate_business(email.upper(), "shopkeeper-pass")
    by_id = controller.authenticate_business(shopkeeper_id, "shopkeeper-pass")

    assert by_email["business_id"] == shopkeeper_id
    assert by_id["business_id"] == shopkeeper_id
    assert by_email["role"] == by_id["role"] == "shopkeeper"
    assert "password_hash" not in by_email


def test_customer_can_login_with_email_or_account_id(isolated_database):
    account = register_customer_account(
        "Customer One",
        "Customer@Example.com",
        "customer-pass",
    )

    by_email = authenticate_customer_account("customer@example.com", "customer-pass")
    by_id = authenticate_customer_account(account["customer_id"], "customer-pass")

    assert account["customer_id"].startswith("SBZ-U-")
    assert by_email["customer_id"] == by_id["customer_id"] == account["customer_id"]
    assert by_email["role"] == by_id["role"] == "customer"
    assert "password_hash" not in by_email

    session = get_session()
    try:
        stored = session.get(CustomerAccount, account["customer_id"])
        assert stored.password_hash != "customer-pass"
        assert stored.email == "customer@example.com"
    finally:
        session.close()


def test_unverified_accounts_cannot_authenticate(isolated_database):
    business = BusinessController().register.register_business(
        name="Unverified Shop",
        category="Retail",
        owner_email="unverified-shop@example.com",
        username="unverified-shop",
        password_hash=hash_password("shopkeeper-pass"),
        email_verified=False,
    )
    customer = register_customer_account(
        "Unverified Customer",
        "unverified-customer@example.com",
        "customer-pass",
        email_verified=False,
    )

    assert BusinessController().authenticate_business(
        business["business_id"],
        "shopkeeper-pass",
    ) is None
    assert authenticate_customer_account(
        customer["customer_id"],
        "customer-pass",
    ) is None


@pytest.mark.parametrize("role", ["shopkeeper", "customer"])
def test_invalid_password_is_rejected_for_each_role(isolated_database, role):
    if role == "shopkeeper":
        identifier = _register_shopkeeper()
        assert BusinessController().authenticate_business(identifier, "incorrect-pass") is None
    else:
        account = register_customer_account(
            "Customer Two",
            "customer-two@example.com",
            "customer-pass",
        )
        assert authenticate_customer_account(
            account["customer_id"],
            "incorrect-pass",
        ) is None


def test_account_role_separation_rejects_other_role_identifiers(isolated_database):
    shopkeeper_id = _register_shopkeeper(
        email="shop-owner@example.com",
        password="same-password",
    )
    customer = register_customer_account(
        "Customer Three",
        "shop-customer@example.com",
        "same-password",
    )
    controller = BusinessController()

    assert controller.authenticate_business(
        customer["customer_id"],
        "same-password",
    ) is None
    assert authenticate_customer_account(
        shopkeeper_id,
        "same-password",
    ) is None
    assert authenticate_customer_account(
        "shop-owner@example.com",
        "same-password",
    ) is None
    assert controller.authenticate_business(
        "shop-customer@example.com",
        "same-password",
    ) is None


def test_duplicate_signup_email_is_rejected_across_roles(isolated_database):
    _register_shopkeeper(email="shared@example.com")

    with pytest.raises(ValueError, match="account already exists"):
        register_customer_account(
            "Duplicate Customer",
            "SHARED@example.com",
            "customer-pass",
        )

    register_customer_account(
        "Unique Customer",
        "unique@example.com",
        "customer-pass",
    )
    with pytest.raises(ValueError, match="account already exists"):
        _register_shopkeeper(email="UNIQUE@example.com")


def test_duplicate_shopkeeper_email_is_rejected(isolated_database):
    _register_shopkeeper(email="duplicate@example.com")

    with pytest.raises(ValueError, match="email address"):
        _register_shopkeeper(email="DUPLICATE@example.com")


def test_customer_account_rejects_invalid_email_and_short_password(isolated_database):
    with pytest.raises(ValueError, match="valid email"):
        register_customer_account("Customer", "not-an-email", "customer-pass")
    with pytest.raises(ValueError, match="8 characters"):
        register_customer_account("Customer", "valid@example.com", "short")


@pytest.mark.parametrize(
    "value,expected",
    [
        ("owner@example.com", "owner@example.com"),
        (" Owner@Example.COM ", "owner@example.com"),
        ("invalid", ""),
        ("owner@", ""),
        ("owner@example", ""),
        ("a b@example.com", ""),
    ],
)
def test_normalize_email(value, expected):
    assert normalize_email(value) == expected


def test_login_lockout_and_success_reset_account_failures(isolated_database, monkeypatch):
    from app.utils.auth import (
        clear_login_failures,
        login_lockout_remaining,
        record_login_failure,
    )

    monkeypatch.setenv("LOGIN_ACCOUNT_ATTEMPT_LIMIT", "2")
    now = utc_now_for_database()
    for _ in range(2):
        record_login_failure("SBZ-S-TEST01", "203.0.113.26", now=now)

    assert login_lockout_remaining("SBZ-S-TEST01", "203.0.113.26", now=now) > 0
    assert login_lockout_remaining(
        "SBZ-S-TEST01",
        "203.0.113.26",
        now=now + timedelta(seconds=901),
    ) == 0

    clear_login_failures("SBZ-S-TEST01")
    assert login_lockout_remaining("SBZ-S-TEST01", "203.0.113.26", now=now) == 0


def test_login_ip_limit_applies_across_accounts(isolated_database, monkeypatch):
    from app.utils.auth import login_lockout_remaining, record_login_failure

    monkeypatch.setenv("LOGIN_IP_ATTEMPT_LIMIT", "1")
    now = utc_now_for_database()
    record_login_failure("SBZ-S-ACCOUNT1", "203.0.113.29", now=now)

    assert login_lockout_remaining(
        "SBZ-U-ACCOUNT2",
        "203.0.113.29",
        now=now,
    ) > 0


def test_login_failure_tracking_does_not_store_ip(isolated_database):
    from app.utils.auth import record_login_failure

    record_login_failure("SBZ-S-TEST02", "203.0.113.30")
    session = get_session()
    try:
        subjects = [
            row.subject_key
            for row in session.query(RateLimitEvent).filter_by(
                event_type="login_failure"
            )
        ]
    finally:
        session.close()

    assert all("203.0.113.30" not in subject for subject in subjects)


def test_login_failure_tracking_does_not_store_email_identifier(isolated_database):
    from app.utils.auth import record_login_failure

    record_login_failure("private@example.com")
    session = get_session()
    try:
        subjects = [
            row.subject_key
            for row in session.query(RateLimitEvent).filter_by(
                event_type="login_failure"
            )
        ]
    finally:
        session.close()

    assert all("private@example.com" not in subject for subject in subjects)


def test_duplicate_phone_is_still_rejected_for_legacy_business_registration(isolated_database):
    controller = BusinessController()
    controller.create_business(
        name="Alpha Electronics",
        category="Electronics",
        owner_phone="+919900000002",
        username="alpha-owner-1",
        password="alpha-pass",
    )

    with pytest.raises(ValueError, match="phone number"):
        controller.create_business(
            name="Beta Electronics",
            category="Electronics",
            owner_phone=" +91 9900 000002 ",
            username="beta-owner",
            password="beta-pass",
        )


def test_registration_result_does_not_expose_credentials(isolated_database):
    business = _register_shopkeeper()

    assert business.startswith("SBZ-S-")
    session = get_session()
    try:
        registered = session.get(Business, business)
        assert registered.password_hash != "shopkeeper-pass"
        assert verify_password("shopkeeper-pass", registered.password_hash)
    finally:
        session.close()
