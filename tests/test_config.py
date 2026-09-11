import pytest

from inventario.config import ConfigError, database_url, normalize_database_url


def test_database_url_is_required(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "  ")

    with pytest.raises(ConfigError, match="DATABASE_URL"):
        database_url()


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u:p@ep-x.neon.tech/neondb?sslmode=require",
         "postgresql+psycopg://u:p@ep-x.neon.tech/neondb?sslmode=require"),
        ("postgres://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
        ("postgresql+psycopg://u:p@localhost:5432/db", "postgresql+psycopg://u:p@localhost:5432/db"),
    ],
)
def test_normalize_database_url(url, expected):
    assert normalize_database_url(url) == expected
