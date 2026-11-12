from hashlib import sha256
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app import models
from app.config import Settings
from app.database import (
    Base,
    create_database_engine,
)


def build_test_engine(
    tmp_path: Path,
):
    settings = Settings(
        data_directory=tmp_path,
    )

    engine = create_database_engine(
        settings
    )

    Base.metadata.create_all(
        bind=engine
    )

    return engine


def content_hash(
    content: str,
) -> str:
    return sha256(
        content.encode("utf-8")
    ).hexdigest()


def test_foundational_tables_are_created(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    table_names = set(
        inspect(engine).get_table_names()
    )

    assert table_names == {
        "approval_requests",
        "audit_events",
        "conversations",
        "information_records",
        "information_sources",
        "messages",
        "user_preferences",
    }

    engine.dispose()


def test_user_preference_persists(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        preference = models.UserPreference(
            key="communication_style",
            value="clear and practical",
        )

        database.add(preference)
        database.commit()
        database.refresh(preference)

        assert preference.id is not None
        assert preference.value_type == "text"
        assert preference.is_sensitive is False

    engine.dispose()


def test_conversation_contains_messages(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    with Session(engine) as database:
        conversation = models.Conversation(
            title="Foundation Test",
        )

        conversation.messages.append(
            models.Message(
                role="user",
                content="Hello, Artemis.",
            )
        )

        conversation.messages.append(
            models.Message(
                role="assistant",
                content="Hello, Daryl.",
                model_name="test-model",
            )
        )

        database.add(conversation)
        database.commit()
        database.refresh(conversation)

        assert conversation.id is not None
        assert len(conversation.messages) == 2

        assert (
            conversation.messages[0].role
            == "user"
        )

        assert (
            conversation.messages[1].role
            == "assistant"
        )

    engine.dispose()


def test_information_source_contains_records(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    original_content = (
        "Artemis preserves the original "
        "retrieved information."
    )

    with Session(engine) as database:
        source = models.InformationSource(
            key="manual-entry",
            name="Manual Entry",
            source_type="manual",
            description=(
                "Information entered directly "
                "by the operator."
            ),
        )

        source.records.append(
            models.InformationRecord(
                external_id="manual-001",
                record_type="text",
                title="Foundation Record",
                original_content=(
                    original_content
                ),
                content_hash=content_hash(
                    original_content
                ),
                source_locator=(
                    "manual://manual-001"
                ),
                language="en",
            )
        )

        database.add(source)
        database.commit()
        database.refresh(source)

        assert source.id is not None
        assert source.status == "active"
        assert source.is_enabled is True
        assert source.is_read_only is True
        assert len(source.records) == 1

        record = source.records[0]

        assert record.id is not None
        assert record.source_id == source.id
        assert (
            record.original_content
            == original_content
        )

        assert (
            record.content_hash
            == content_hash(
                original_content
            )
        )

        assert record.status == "stored"
        assert record.is_duplicate is False
        assert record.retrieved_at is not None

    engine.dispose()


def test_information_record_tracks_duplicate(
    tmp_path: Path,
):
    engine = build_test_engine(tmp_path)

    original_content = (
        "The same information appeared twice."
    )

    with Session(engine) as database:
        source = models.InformationSource(
            key="duplicate-test",
            name="Duplicate Test Source",
            source_type="manual",
        )

        original = models.InformationRecord(
            external_id="record-001",
            original_content=original_content,
            content_hash=content_hash(
                original_content
            ),
        )

        duplicate = models.InformationRecord(
            external_id="record-002",
            original_content=original_content,
            content_hash=content_hash(
                original_content
            ),
            is_duplicate=True,
            duplicate_of=original,
        )

        source.records.extend(
            [
                original,
                duplicate,
            ]
        )

        database.add(source)
        database.commit()
        database.refresh(original)
        database.refresh(duplicate)

        assert original.is_duplicate is False
        assert duplicate.is_duplicate is True

        assert (
            duplicate.duplicate_of_id
            == original.id
        )

        assert duplicate.duplicate_of is original
        assert duplicate in original.duplicates

    engine.dispose()