import json
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import DateTime, ForeignKey, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base, get_database
from app.research_models import ResearchRun
from app.routes.research import compare_runs, run_data, run_warnings


router = APIRouter(
    prefix="/research/updates",
    tags=["research updates"],
)

FINISHED_STATUSES = (
    "completed",
    "failed",
    "interrupted",
)


class ResearchUpdateReview(Base):
    __tablename__ = "research_update_reviews"

    run_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("research_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )


class ReviewRequest(BaseModel):
    reviewed: bool = True


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None

    return value.replace(tzinfo=timezone.utc).isoformat()


def get_finished_run(
    database: Session,
    run_id: UUID,
) -> ResearchRun:
    record = database.get(
        ResearchRun,
        str(run_id),
    )

    if (
        record is None
        or record.status not in FINISHED_STATUSES
    ):
        raise HTTPException(
            status_code=404,
            detail="Finished research update not found.",
        )

    return record


def _update_summary_without_reference(
    run: ResearchRun,
    review: ResearchUpdateReview | None,
) -> dict:
    request = json.loads(run.request_json)

    return {
        "run_id": run.id,
        "requisition_id": run.requisition_id,
        "topic": request.get("topic", "Untitled research"),
        "questions": request.get("questions", []),
        "risk_tracker_case_id": request.get("risk_tracker_case_id"),
        "status": run.status,
        "error": run.error,
        "started_at": timestamp(run.started_at),
        "finished_at": timestamp(run.finished_at),
        "reviewed": review is not None,
        "reviewed_at": (
            timestamp(review.reviewed_at)
            if review is not None
            else None
        ),
    }


def warning_lines(warnings: dict | None) -> list[str]:
    if not warnings:
        return ["- No saved warning details."]

    lines = []

    for name in (
        "event_extraction_error",
        "synthesis_error",
    ):
        if warnings.get(name):
            label = name.replace("_", " ").capitalize()
            lines.append(f"- {label}: {warnings[name]}")

    for error in warnings.get("collection_errors") or []:
        text = (
            error
            if isinstance(error, str)
            else json.dumps(error, ensure_ascii=False)
        )
        lines.append(f"- Collection: {text}")

    for name, label in (
        ("source_save_errors", "Source save"),
        ("event_note_errors", "Event note save or link"),
    ):
        for record in warnings.get(name) or []:
            title = record.get("title") or "Untitled record"
            errors = [
                f"{key}: {value}"
                for key, value in record.items()
                if "error" in key and value
            ]
            lines.append(
                f"- {label}: {title} — "
                + ("; ".join(errors) or "See saved record.")
            )

    return lines or ["- No warnings reported in the saved result."]


def append_records(
    lines: list[str],
    heading: str,
    records: list[dict],
) -> None:
    lines.extend(["", f"### {heading}", ""])

    if not records:
        lines.append("- None.")
        return

    for record in records:
        title = record.get("title") or "Untitled record"
        lines.append(f"- {title}")

        if record.get("url"):
            lines.append(f"  Source: {record['url']}")

        if record.get("record_id"):
            lines.append(f"  Artemis record: {record['record_id']}")

        if record.get("intel_note_id"):
            lines.append(f"  Intel Note: {record['intel_note_id']}")

        if record.get("status"):
            lines.append(f"  Recorded status: {record['status']}")


def build_change_summary(
    database: Session,
    run: ResearchRun,
    warnings: dict | None,
) -> dict:
    lines = [
        "## What changed?",
        "",
        f"Target run: {run.id}",
    ]

    if run.status != "completed":
        lines.extend([
            "",
            f"This run {run.status}; no completed comparison is available.",
            f"Recorded error: {run.error or 'No error details saved.'}",
        ])
        return {
            "status": "run_not_completed",
            "baseline_run_id": None,
            "comparison": None,
            "brief": "\n".join(lines),
        }

    # Choose the most recently finished completed run that was
    # already available when this target run started.
    baseline = database.scalar(
        select(ResearchRun)
        .where(
            ResearchRun.requisition_id == run.requisition_id,
            ResearchRun.id != run.id,
            ResearchRun.status == "completed",
            ResearchRun.result_json.is_not(None),
            ResearchRun.finished_at <= run.started_at,
        )
        .order_by(
            ResearchRun.finished_at.desc(),
            ResearchRun.started_at.desc(),
            ResearchRun.id,
        )
        .limit(1)
    )

    if baseline is None:
        lines.extend([
            "",
            "No earlier completed run was available at the start of this run.",
            "This result can serve as a baseline for future updates.",
            "",
            "### Run warnings",
            "",
            *warning_lines(warnings),
        ])
        return {
            "status": "no_baseline",
            "baseline_run_id": None,
            "comparison": None,
            "brief": "\n".join(lines),
        }

    try:
        comparison = compare_runs(
            requisition_id=UUID(run.requisition_id),
            baseline_run_id=UUID(baseline.id),
            target_run_id=UUID(run.id),
            database=database,
        )
    except HTTPException as error:
        lines.extend([
            "",
            f"Comparison unavailable: {error.detail}",
            "",
            "### Run warnings",
            "",
            *warning_lines(warnings),
        ])
        return {
            "status": "comparison_unavailable",
            "baseline_run_id": baseline.id,
            "comparison": None,
            "brief": "\n".join(lines),
        }

    sources = comparison["sources"]
    notes = comparison["event_notes"]

    lines.extend([
        f"Baseline run: {baseline.id}",
        "",
        (
            f"Sources: {len(sources['added'])} added, "
            f"{len(sources['absent_from_target'])} absent from this run, "
            f"{len(sources['changed'])} records changed."
        ),
        (
            f"Saved event notes: {len(notes['added'])} added, "
            f"{len(notes['absent_from_target'])} absent from this run, "
            f"{len(notes['changed'])} records changed."
        ),
        (
            "Research request changed: "
            + ("Yes." if comparison["request_changed"] else "No.")
        ),
        (
            "Brief text changed: "
            + ("Yes." if comparison["brief_changed"] else "No.")
        ),
    ])

    append_records(lines, "Sources added", sources["added"])
    append_records(
        lines,
        "Sources absent from this run",
        sources["absent_from_target"],
    )
    append_records(lines, "Event notes added", notes["added"])
    append_records(
        lines,
        "Event notes absent from this run",
        notes["absent_from_target"],
    )

    for label, changes in (
        ("Changed source records", sources["changed"]),
        ("Changed event-note records", notes["changed"]),
    ):
        lines.extend(["", f"### {label}", ""])

        if not changes:
            lines.append("- None.")

        for change in changes:
            before = change["before"]
            after = change["after"]

            lines.append(
                f"- {after.get('title') or before.get('title') or change['identity']}"
            )

            for key in sorted(set(before) | set(after)):
                if before.get(key) != after.get(key):
                    old = json.dumps(before.get(key), ensure_ascii=False)
                    new = json.dumps(after.get(key), ensure_ascii=False)
                    lines.append(f"  {key}: {old} → {new}")

    lines.extend(["", "### Reported collection needs", ""])
    needs = comparison["collection_needs"]

    if not needs["available"]:
        lines.append(needs["reason"])
    else:
        for key, label in (
            ("newly_reported", "Newly reported"),
            ("no_longer_reported", "No longer reported"),
            ("still_reported", "Still reported"),
        ):
            lines.extend(["", f"{label}:"])
            lines.extend(
                [f"- {item}" for item in needs[key]]
                or ["- None."]
            )

    lines.extend([
        "",
        "### Target run warnings",
        "",
        *warning_lines(comparison["warnings"]["target"]),
        "",
        "### Baseline run warnings",
        "",
        *warning_lines(comparison["warnings"]["baseline"]),
        "",
        "### Interpretation limits",
        "",
        *[
            f"- {item}"
            for item in comparison["comparison_limits"]
        ],
    ])

    return {
        "status": "compared",
        "baseline_run_id": baseline.id,
        "comparison": comparison,
        "brief": "\n".join(lines),
    }


@router.get("")
def list_updates(
    unread_only: bool = False,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    database: Session = Depends(get_database),
) -> dict:
    base = (
        select(
            ResearchRun,
            ResearchUpdateReview,
        )
        .outerjoin(
            ResearchUpdateReview,
            ResearchUpdateReview.run_id == ResearchRun.id,
        )
        .where(
            ResearchRun.status.in_(FINISHED_STATUSES)
        )
    )

    unread_statement = (
        select(func.count())
        .select_from(ResearchRun)
        .outerjoin(
            ResearchUpdateReview,
            ResearchUpdateReview.run_id == ResearchRun.id,
        )
        .where(
            ResearchRun.status.in_(FINISHED_STATUSES),
            ResearchUpdateReview.run_id.is_(None),
        )
    )

    unread_count = database.scalar(unread_statement) or 0

    if unread_only:
        base = base.where(
            ResearchUpdateReview.run_id.is_(None)
        )

    total_count = database.scalar(
        select(func.count()).select_from(base.subquery())
    ) or 0

    rows = database.execute(
        base.order_by(
            ResearchRun.finished_at.desc(),
            ResearchRun.started_at.desc(),
            ResearchRun.id,
        )
        .offset(offset)
        .limit(limit)
    ).all()

    return {
        "items": [
            update_summary(run, review)
            for run, review in rows
        ],
        "unread_count": unread_count,
        "total_count": total_count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/{run_id}")
def read_update(
    run_id: UUID,
    database: Session = Depends(get_database),
) -> dict:
    run = get_finished_run(database, run_id)

    review = database.get(
        ResearchUpdateReview,
        str(run_id),
    )

    data = update_summary(run, review)

    # Opening an update does not automatically mark it reviewed.
    data["run"] = run_data(
        run,
        include_result=True,
    )

    result = data["run"]["result"]

    data["warnings"] = (
        run_warnings(result)
        if isinstance(result, dict)
        else None
    )

    data["changes"] = build_change_summary(
        database,
        run,
        data["warnings"],
    )

    return data


@router.put("/{run_id}/review")
def review_update(
    run_id: UUID,
    request: ReviewRequest,
    database: Session = Depends(get_database),
) -> dict:
    run = get_finished_run(database, run_id)

    review = database.get(
        ResearchUpdateReview,
        str(run_id),
    )

    if request.reviewed:
        if review is None:
            review = ResearchUpdateReview(
                run_id=str(run_id),
                reviewed_at=utc_now(),
            )
            database.add(review)
    elif review is not None:
        database.delete(review)
        review = None

    database.commit()

    return update_summary(run, review)

def update_summary(run, review) -> dict:
    from app.services.references import label_record

    return label_record(
        "run",
        _update_summary_without_reference(run, review),
    )
