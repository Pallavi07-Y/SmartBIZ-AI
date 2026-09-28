import pytest

from app.database import configure_database


@pytest.fixture
def isolated_database(tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'test.sqlite3').as_posix()}"
    engine = configure_database(database_url)
    yield engine
    engine.dispose()
