"""
config.py

Loads environment variables and stores all project settings.
"""

import os
from dotenv import load_dotenv

# Load .env file
load_dotenv()


class Settings:
    """
    Application Configuration
    """

    # ==========================
    # xAI Configuration
    # ==========================
    BASE_URL = os.getenv("BASE_URL")
    MODEL = os.getenv("MODEL")
    DOCUMENT_PATH = "data/documents"

    CHROMA_DB_PATH = "data/chroma_db"

    CHUNK_SIZE = 800

    CHUNK_OVERLAP = 100

    TOP_K = 5


    EMBEDDING_MODEL = "all-MiniLM-L6-v2"

    @property
    def API_KEY(self):
        api_key = os.getenv("API_KEY")
        if not api_key:
            raise ValueError(" API_KEY not found in .env file")
        return api_key


settings = Settings()