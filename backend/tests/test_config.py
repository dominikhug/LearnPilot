import pytest

from learnpilot.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "postgres://u:p@host:5432/db",
        "postgresql://u:p@host:5432/db",
        "postgresql+psycopg://u:p@host:5432/db",
    ],
)
def test_database_url_uses_psycopg_driver(url):
    assert Settings(database_url=url).database_url == "postgresql+psycopg://u:p@host:5432/db"
