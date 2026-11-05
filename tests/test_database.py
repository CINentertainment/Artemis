from pathlib import Path

from sqlalchemy import text

from app.config import Settings
from app.database import create_database_engine


def test_database_uses_private_data_directory(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    assert settings.database_path == (
        tmp_path / "artemis.db"
    )

    assert (
        str(settings.database_path)
        not in str(Path.cwd())
    )


def test_database_engine_creates_database(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT 1")
        )

        assert result.scalar_one() == 1

    assert settings.database_path.exists()

    engine.dispose()