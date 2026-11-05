from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    sessionmaker,
)

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def create_database_engine(
    settings: Settings,
) -> Engine:
    settings.data_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    return create_engine(
        settings.database_url,
        connect_args={
            "check_same_thread": False,
        },
        pool_pre_ping=True,
    )


settings = get_settings()

engine = create_database_engine(settings)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def initialize_database(
    database_engine: Engine = engine,
) -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(
        bind=database_engine
    )


def get_database() -> Generator[
    Session,
    None,
    None,
]:
    database = SessionLocal()

    try:
        yield database
    finally:
        database.close()