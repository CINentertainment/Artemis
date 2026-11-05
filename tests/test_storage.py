from pathlib import Path

from app.config import Settings
from app.database import (
    create_database_engine,
    initialize_database,
)
from app.services.storage import (
    check_storage_health,
)


def test_initialized_storage_is_healthy(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    initialize_database(engine)

    result = check_storage_health(engine)

    assert result.status == "healthy"
    assert result.database_initialized is True
    assert result.readable is True
    assert result.writable is True

    engine.dispose()


def test_uninitialized_storage_is_unavailable(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    result = check_storage_health(engine)

    assert result.status == "unavailable"
    assert result.database_initialized is False
    assert result.readable is True
    assert result.writable is True

    engine.dispose()