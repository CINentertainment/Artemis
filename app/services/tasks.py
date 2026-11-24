import re
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import Date, DateTime, String, func, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class ArtemisTask(Base):
    __tablename__ = "artemis_tasks"

    id: Mapped[str] = mapped_column(
        String(32),
        primary_key=True,
        default=lambda: uuid4().hex,
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        default=utc_now,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


TASK_HELP = """Task commands:

Add a task to review the market brief tomorrow.
Add a task to organize my workspace.
Add a task to review my notes on 2026-11-25.
List my tasks.
What's due today?
Mark task TASK_ID complete.

Dates supported: today, tomorrow, or on YYYY-MM-DD.
Dates use the Artemis computer's local calendar.
This version tracks tasks; timed notifications are not connected yet.
"""


def commit(database: Session) -> None:
    try:
        database.commit()
    except Exception:
        database.rollback()
        raise


def task_label(task: ArtemisTask) -> str:
    due = (
        f"due {task.due_date.isoformat()}"
        if task.due_date is not None
        else "no due date"
    )

    if task.due_date is not None and task.due_date < date.today():
        due += ", overdue"

    return f"[{task.id[:8]}] {task.title} — {due}"


def add_task(database: Session, value: str) -> str:
    title = value.strip()
    due = None

    # Remove sentence punctuation before looking for a final date.
    title = title.rstrip(".!")

    relative = re.search(
        r"\s+(today|tomorrow)$",
        title,
        flags=re.IGNORECASE,
    )
    explicit = re.search(
        r"\s+on\s+(\d{4}-\d{2}-\d{2})$",
        title,
        flags=re.IGNORECASE,
    )

    if relative:
        due = date.today() + timedelta(
            days=1 if relative.group(1).lower() == "tomorrow" else 0
        )
        title = title[:relative.start()].strip()
    elif explicit:
        try:
            due = date.fromisoformat(explicit.group(1))
        except ValueError:
            return "That date is invalid. Use on YYYY-MM-DD."
        title = title[:explicit.start()].strip()
    elif re.search(
        r"\b(?:tonight|next\s+\w+|in\s+\d+\s+\w+|"
        r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
        r"\d{1,2}:\d{2}|[ap]\.?m\.?)\b",
        title,
        flags=re.IGNORECASE,
    ):
        return (
            "For this version, use today, tomorrow, or on YYYY-MM-DD. "
            "Specific reminder times aren't supported yet."
        )

    if not title or len(title) > 500:
        return "Enter a task title between 1 and 500 characters."

    task = ArtemisTask(title=title, due_date=due)
    database.add(task)
    commit(database)
    database.refresh(task)

    return (
        f"Task saved:\n{task_label(task)}\n\n"
        f"To finish it, send: Mark task {task.id[:8]} complete."
    )


def list_tasks(database: Session, *, due_only: bool = False) -> str:
    statement = select(ArtemisTask).where(
        ArtemisTask.completed_at.is_(None)
    )

    if due_only:
        statement = statement.where(
            ArtemisTask.due_date.is_not(None),
            ArtemisTask.due_date <= date.today(),
        )

    total = database.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0

    tasks = database.scalars(
        statement.order_by(
            ArtemisTask.due_date.is_(None),
            ArtemisTask.due_date,
            ArtemisTask.created_at,
            ArtemisTask.id,
        ).limit(50)
    ).all()

    if not tasks:
        return (
            "No incomplete tasks are due today or overdue."
            if due_only
            else "You have no incomplete tasks."
        )

    lines = [
        "Tasks due today and overdue:" if due_only else "Your open tasks:",
        "",
        *(f"- {task_label(task)}" for task in tasks),
    ]

    if total > len(tasks):
        lines.append(f"\nShowing {len(tasks)} of {total} tasks.")

    return "\n".join(lines)


def complete_task(database: Session, selector: str) -> str:
    selector = selector.lower()

    if not re.fullmatch(r"[0-9a-f]{8,32}", selector):
        return "Use the task ID shown by 'List my tasks'."

    tasks = database.scalars(
        select(ArtemisTask).where(
            ArtemisTask.id.startswith(selector)
        ).limit(2)
    ).all()

    if not tasks:
        return "No task matched that ID."

    if len(tasks) > 1:
        return "More than one task matched. Use the full task ID."

    task = tasks[0]

    if task.completed_at is not None:
        return f"That task is already complete: {task.title}"

    task.completed_at = utc_now()
    commit(database)

    return f"Task completed [{task.id[:8]}]: {task.title}"


def run_task_command(
    database: Session,
    user_content: str,
) -> str | None:
    text = re.sub(
        r"^(?:hey\s+)?artemis[\s,:]+",
        "",
        user_content.strip(),
        count=1,
        flags=re.IGNORECASE,
    )
    normalized = text.casefold().replace("’", "'").rstrip(".!?")

    if normalized in {"/tasks", "/tasks help", "task help"}:
        return TASK_HELP

    if normalized in {
        "list my tasks", "show my tasks", "list tasks",
        "what are my tasks",
    }:
        return list_tasks(database)

    if normalized in {
        "what's due today", "what is due today",
        "show overdue tasks",
    }:
        return list_tasks(database, due_only=True)

    adding = re.fullmatch(
        r"(?:please\s+)?add\s+(?:a\s+)?task\s+to\s+([\s\S]+)",
        text,
        flags=re.IGNORECASE,
    )
    if adding:
        return add_task(database, adding.group(1))

    completing = re.fullmatch(
        r"(?:please\s+)?mark\s+task\s+\[?([0-9a-f]{8,32})\]?"
        r"\s+complete[.!]?",
        text,
        flags=re.IGNORECASE,
    )
    if completing:
        return complete_task(database, completing.group(1))

    return None