import pytest

from app.config import Settings, _positive_int_env
from app.dashboard import _safe_html_text, _validate_upload_size
from app.config import settings
from app.business.register_business import BusinessManager
from app.business.search_business import BusinessSearch
from app.database.business_db import BusinessDB
from app.dashboard import get_customer_business


def test_required_configuration_reports_missing_names_without_values(monkeypatch):
    monkeypatch.delenv("API_KEY", raising=False)

    missing = Settings().missing_required_configuration()

    assert missing == ("API_KEY",)


def test_login_lockout_window_is_configurable(monkeypatch):
    monkeypatch.setenv("LOGIN_WINDOW_SECONDS", "120")

    assert Settings().login_window_seconds == 120


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_positive_integer_environment_setting_rejects_invalid_values(monkeypatch, value):
    monkeypatch.setenv("MAX_UPLOAD_SIZE_MB", value)

    with pytest.raises(ValueError, match="MAX_UPLOAD_SIZE_MB"):
        _positive_int_env("MAX_UPLOAD_SIZE_MB", 20)


def test_dashboard_upload_size_limit_rejects_over_limit(monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 1)

    with pytest.raises(ValueError, match="1 MB"):
        _validate_upload_size(1024 * 1024 + 1)


def test_user_controlled_html_text_is_escaped():
    assert _safe_html_text("<img src=x onerror=alert(1)>") == (
        "&lt;img src=x onerror=alert(1)&gt;"
    )


def test_business_result_dictionaries_exclude_credentials_and_phone(isolated_database):
    business = BusinessManager().register_business(
        "Safe Shop",
        "Retail",
        owner_phone="9876543210",
        username="safe-owner",
        password_hash="not-a-real-hash",
    )

    for result in (
        business,
        BusinessSearch().search_by_id(business["business_id"]),
        BusinessDB().get_all()[0],
        get_customer_business(business["business_id"]),
    ):
        assert "password_hash" not in result
        assert "username" not in result
        assert "owner_phone" not in result
