from app.database.session import normalize_async_url


def test_normalizes_postgres_scheme() -> None:
    assert normalize_async_url("postgresql://user:pass@localhost/db") == ("postgresql+asyncpg://user:pass@localhost/db")


def test_enforces_ssl_for_supabase() -> None:
    result = normalize_async_url("postgresql://postgres.ref:pass@aws-0-eu.pooler.supabase.com:5432/postgres")
    assert result.startswith("postgresql+asyncpg://")
    assert result.endswith("?ssl=require")


def test_preserves_explicit_supabase_ssl_setting() -> None:
    url = "postgresql+asyncpg://user:pass@db.ref.supabase.com/postgres?ssl=verify-full"
    assert normalize_async_url(url) == url
