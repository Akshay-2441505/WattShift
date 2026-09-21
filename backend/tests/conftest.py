import pytest
from sqlalchemy.orm import Session

from app import db, models  # noqa: F401  (models import registers the tables on db.Base)
from app.config import settings


@pytest.fixture(scope="session")
def engine():
    url = settings.test_database_url
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    e = db.make_engine(url)
    with e.begin() as c:
        # tests drop tables, so refuse to run against anything that isn't obviously a test DB
        name = c.exec_driver_sql("select current_database()").scalar()
        assert "test" in name, f"refusing to run tests against database {name!r}"
    db.Base.metadata.drop_all(e)
    db.Base.metadata.create_all(e)
    yield e
    db.Base.metadata.drop_all(e)


@pytest.fixture()
def session(engine):
    """Each test runs in a transaction that is rolled back."""
    conn = engine.connect()
    tx = conn.begin()
    s = Session(bind=conn, join_transaction_mode="create_savepoint")
    yield s
    s.close()
    tx.rollback()
    conn.close()
