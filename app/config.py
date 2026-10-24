"""
config.py

Loads environment variables and stores all project settings.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Load .env file
load_dotenv(PROJECT_ROOT / ".env")


def _positive_int_env(name, default):
    value = os.getenv(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


def _setting_value(name, default=None):
    value = os.getenv(name)
    if value:
        return value

    try:
        import streamlit as st

        value = st.secrets.get(name)
    except FileNotFoundError:
        return default

    return value if value not in (None, "") else default


def _positive_int_setting(name, default):
    value = _setting_value(name)
    if value is None:
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a positive integer.") from exc
    if parsed < 1:
        raise ValueError(f"{name} must be a positive integer.")
    return parsed


class Settings:
    """
    Application Configuration
    """

    # ==========================
    # xAI Configuration
    # ==========================
    BASE_URL = _setting_value("BASE_URL", "https://api.groq.com/openai/v1")
    MODEL = _setting_value("MODEL", "openai/gpt-oss-120b")
    VISION_MODEL = _setting_value(
        "VISION_MODEL",
        "meta-llama/llama-4-scout-17b-16e-instruct",
    )
    DOCUMENT_PATH = os.getenv("DOCUMENT_PATH", str(PROJECT_ROOT / "data" / "documents"))
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", str(PROJECT_ROOT / "data" / "chroma_db"))
    MAX_UPLOAD_SIZE_MB = _positive_int_env("MAX_UPLOAD_SIZE_MB", 20)

    CHUNK_SIZE = 800
    CHUNK_OVERLAP = 100
    TOP_K = 5
    EMBEDDING_MODEL = "all-MiniLM-L6-v2"

    @staticmethod
    def _ensure_directory(path_value):
        path = Path(path_value)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    @property
    def API_KEY(self):
        api_key = _setting_value("API_KEY")
        if not api_key:
            raise ValueError(
                "API_KEY not found in the environment or Streamlit secrets."
            )
        return api_key

    @property
    def smtp_host(self):
        return _setting_value("SMTP_HOST", "smtp.gmail.com")

    @property
    def smtp_port(self):
        return _positive_int_setting("SMTP_PORT", 465)

    @property
    def smtp_username(self):
        return _setting_value("SMTP_USERNAME")

    @property
    def smtp_password(self):
        return _setting_value("SMTP_PASSWORD")

    @property
    def smtp_from_email(self):
        return _setting_value("SMTP_FROM_EMAIL", self.smtp_username)

    @property
    def email_otp_hash_secret(self):
        return _setting_value("EMAIL_OTP_HASH_SECRET")

    @property
    def email_otp_ttl_seconds(self):
        return _positive_int_setting("EMAIL_OTP_TTL_SECONDS", 600)

    @property
    def email_otp_max_attempts(self):
        return _positive_int_setting("EMAIL_OTP_MAX_ATTEMPTS", 5)

    @property
    def email_otp_cooldown_seconds(self):
        return _positive_int_setting("EMAIL_OTP_COOLDOWN_SECONDS", 60)

    @property
    def email_otp_email_hourly_limit(self):
        return _positive_int_setting("EMAIL_OTP_EMAIL_HOURLY_LIMIT", 5)

    @property
    def email_otp_ip_hourly_limit(self):
        return _positive_int_setting("EMAIL_OTP_IP_HOURLY_LIMIT", 20)

    @property
    def login_account_attempt_limit(self):
        return _positive_int_env("LOGIN_ACCOUNT_ATTEMPT_LIMIT", 5)

    @property
    def login_window_seconds(self):
        return _positive_int_env("LOGIN_WINDOW_SECONDS", 900)

    @property
    def login_ip_attempt_limit(self):
        return _positive_int_env("LOGIN_IP_ATTEMPT_LIMIT", 20)

    @property
    def login_lockout_seconds(self):
        return _positive_int_env("LOGIN_LOCKOUT_SECONDS", 900)

    @property
    def max_upload_size_bytes(self):
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    def missing_required_configuration(self):
        missing = []
        for name in ("API_KEY",):
            if not _setting_value(name):
                missing.append(name)
        return tuple(missing)

    @property
    def document_path(self):
        return self._ensure_directory(self.DOCUMENT_PATH)

    @property
    def chroma_db_path(self):
        return self._ensure_directory(self.CHROMA_DB_PATH)


for directory_name in (Settings.DOCUMENT_PATH, Settings.CHROMA_DB_PATH):
    Settings._ensure_directory(directory_name)


settings = Settings()