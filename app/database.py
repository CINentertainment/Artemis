from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import Engine, create_engine, update
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import Settings, get_settings


class Base(DeclarativeBase):
    pass


def create_database_engine(settings: Settings) -> Engine:
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
    from app.research_models import ResearchRun

    Base.metadata.create_all(bind=database_engine)

    # The local server uses one worker and one shared research lock.
    # Running rows left at startup belong to interrupted executions.
    with Session(database_engine) as database:
        database.execute(
            update(ResearchRun)
            .where(ResearchRun.status == "running")
            .values(
                status="interrupted",
                finished_at=datetime.now(timezone.utc).replace(
                    tzinfo=None
                ),
                error=(
                    "The server restarted before this run finished. "
                    "Some evidence or notes may already have been saved."
                ),
            )
        )
        database.commit()


def get_database() -> Generator[Session, None, None]:
    database = SessionLocal()

    try:
        yield database
    finally:
        database.close()