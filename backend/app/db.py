from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


def make_engine(url: str) -> Engine:
    # Neon URLs are plain postgresql://; force the psycopg (v3) driver.
    return create_engine(url.replace("postgresql://", "postgresql+psycopg://", 1), pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
