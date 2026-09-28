from app.business.business_manager import BusinessController
from app.utils.auth import hash_password, verify_password


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
