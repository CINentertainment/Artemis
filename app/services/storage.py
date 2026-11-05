from datetime import datetime, timezone

from sqlalchemy import Engine, inspect

from app.schemas import StorageHealthResponse


REQUIRED_TABLES = {
    "approval_requests",
    "audit_events",
    "conversations",
    "messages",
    "user_preferences",
}


def check_storage_health(
    database_engine: Engine,
) -> StorageHealthResponse:
    database_initialized = False
    readable = False
    writable = False
    raw_connection = None
    cursor = None

    try:
        existing_tables = set(
            inspect(
                database_engine
            ).get_table_names()
        )

        database_initialized = (
            REQUIRED_TABLES.issubset(
                existing_tables
            )
        )

        raw_connection = (
            database_engine.raw_connection()
        )

        cursor = raw_connection.cursor()

        cursor.execute("SELECT 1")

        result = cursor.fetchone()

        readable = (
            result is not None
            and result[0] == 1
        )

        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute("ROLLBACK")

        writable = True

    except Exception:
        if raw_connection is not None:
            try:
                raw_connection.rollback()
            except Exception:
                pass

    finally:
        if cursor is not None:
            cursor.close()

        if raw_connection is not None:
            raw_connection.close()

    status = (
        "healthy"
        if (
            database_initialized
            and readable
            and writable
        )
        else "unavailable"
    )

    return StorageHealthResponse(
        status=status,
        database_initialized=(
            database_initialized
        ),
        readable=readable,
        writable=writable,
        checked_at=datetime.now(timezone.utc),
    )