from hashlib import sha256

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import InformationRecord, InformationSource
from app.services.information_sources import normalize_optional_text


class BlankRecordContentError(ValueError):
    pass


class RecordContentConflictError(Exception):
    pass


def normalize_record_content(content: str) -> str:
    return content.replace("\r\n", "\n").replace("\r", "\n").strip()


def check_record_retry(
    existing: InformationRecord,
    normalized_content: str,
) -> tuple[InformationRecord, bool]:
    if normalize_record_content(existing.original_content) != normalized_content:
        raise RecordContentConflictError(
            "This source and external ID already identify different content."
        )

    return existing, False


def ingest_text_record(
    database: Session,
    source: InformationSource,
    *,
    content: str,
    title: str | None = None,
    external_id: str | None = None,
    source_locator: str | None = None,
) -> tuple[InformationRecord, bool]:
    """Return the record and whether this submission created it."""
    normalized_content = normalize_record_content(content)
    if not normalized_content:
        raise BlankRecordContentError("Record content must contain text.")

    content_hash = sha256(normalized_content.encode("utf-8")).hexdigest()
    external_id = normalize_optional_text(external_id)
    source_id = source.id

    retry_lookup = None
    if external_id is not None:
        retry_lookup = select(InformationRecord).where(
            InformationRecord.source_id == source_id,
            InformationRecord.external_id == external_id,
        )
        existing = database.scalar(retry_lookup)
        if existing is not None:
            return check_record_retry(existing, normalized_content)

    original_id = database.scalar(
        select(InformationRecord.id)
        .where(
            InformationRecord.source_id == source_id,
            InformationRecord.content_hash == content_hash,
            InformationRecord.is_duplicate.is_(False),
        )
        .order_by(InformationRecord.retrieved_at, InformationRecord.id)
        .limit(1)
    )

    record = InformationRecord(
        source_id=source_id,
        external_id=external_id,
        record_type="text",
        title=normalize_optional_text(title),
        original_content=content,
        normalized_content=normalized_content,
        content_hash=content_hash,
        source_locator=normalize_optional_text(source_locator),
        mime_type="text/plain",
        is_duplicate=original_id is not None,
        duplicate_of_id=original_id,
    )

    database.add(record)
    try:
        database.commit()
    except IntegrityError:
        database.rollback()
        if retry_lookup is not None:
            existing = database.scalar(retry_lookup)
            if existing is not None:
                return check_record_retry(existing, normalized_content)
        raise
    except Exception:
        database.rollback()
        raise

    database.refresh(record)
    return record, True