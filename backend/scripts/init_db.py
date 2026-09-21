"""Create tables, seed the ToD tariff and the tariff catalogue, ingest live IEX prices. Safe to re-run.  Usage: python -m scripts.init_db"""
from sqlalchemy import func, select

from app import db, models
from app.catalogue import seed_catalogue
from app.config import settings
from app.ingest_iex import ingest
from app.models import AUDIT_GUARD, PriceSignal
from app.seed import seed_tod


def main() -> None:
    assert settings.database_url, "DATABASE_URL not set (see ~/.wattshift/.env)"
    engine = db.make_engine(settings.database_url)
    db.Base.metadata.create_all(engine)
    with engine.begin() as conn:  # a table created before the guard existed does not get it from create_all
        for ddl in AUDIT_GUARD:
            conn.execute(ddl)
    with db.make_session_factory(engine)() as s, s.begin():
        seed_tod(s)
        seed_catalogue(s)
        n = ingest(s)
        lo, hi, cnt = s.execute(select(func.min(PriceSignal.ts), func.max(PriceSignal.ts), func.count())).one()
    print(f"ingested {n} blocks; price_signals now {cnt} rows spanning {lo} .. {hi}")


if __name__ == "__main__":
    main()
