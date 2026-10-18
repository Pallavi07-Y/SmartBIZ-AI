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


class Settings:
    """
    Application Configuration
    """

    # ==========================
    # xAI Configuration
    # ==========================
    BASE_URL = os.getenv("BASE_URL", "https://api.groq.com/openai/v1")
    MODEL = os.getenv("MODEL", "openai/gpt-oss-120b")
    VISION_MODEL = os.getenv("VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct")
    DOCUMENT_PATH = os.getenv("DOCUMENT_PATH", str(PROJECT_ROOT / "data" / "documents"))
    CHROMA_DB_PATH = os.getenv("CHROMA_DB_PATH", str(PROJECT_ROOT / "data" / "chroma_db"))

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
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise ValueError("API_KEY not found in the environment (.env or deployment config).")
        return api_key

    @property
    def document_path(self):
        return self._ensure_directory(self.DOCUMENT_PATH)

    @property
    def chroma_db_path(self):
        return self._ensure_directory(self.CHROMA_DB_PATH)


for directory_name in (Settings.DOCUMENT_PATH, Settings.CHROMA_DB_PATH):
    Settings._ensure_directory(directory_name)


settings = Settings()