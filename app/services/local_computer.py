import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.config import get_settings
from app.database import Base
from app.services.approvals import (
    create_approval_request,
    get_approval_request,
    read_approval_payload,
)


MAX_FILE_BYTES = 64_000
MAX_DIRECTORY_ENTRIES = 200

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".log",
    ".py",
    ".html",
    ".css",
    ".js",
    ".yaml",
    ".yml",
}


class LocalComputerError(Exception):
    pass


class LocalActionExecution(Base):
    __tablename__ = "local_action_executions"

    approval_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
    )

    application_id: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    process_id: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def workspace_directory() -> Path:
    return (
        get_settings().data_directory
        / "workspace"
    ).resolve()


def allowed_folders() -> dict[str, Path]:
    return {
        "workspace": workspace_directory(),
    }


def application_catalog() -> dict[str, dict[str, str]]:
    if os.name != "nt":
        return {}

    windows_directory = Path(
        os.environ.get("SystemRoot", r"C:\Windows")
    )

    return {
        "notepad": {
            "name": "Notepad",
            "executable": str(
                windows_directory / "System32" / "notepad.exe"
            ),
        },
        "calculator": {
            "name": "Calculator",
            "executable": str(
                windows_directory / "System32" / "calc.exe"
            ),
        },
    }


def computer_capabilities() -> dict:
    folders = allowed_folders()

    return {
        "platform": os.name,
        "folders": [
            {
                "id": folder_id,
                "name": "Artemis Workspace",
                "path": str(path),
                "exists": path.is_dir(),
            }
            for folder_id, path in folders.items()
        ],
        "applications": [
            {
                "id": application_id,
                "name": application["name"],
                "available": Path(
                    application["executable"]
                ).is_file(),
            }
            for application_id, application
            in application_catalog().items()
        ],
        "maximum_file_bytes": MAX_FILE_BYTES,
        "maximum_directory_entries": MAX_DIRECTORY_ENTRIES,
        "text_extensions": sorted(TEXT_EXTENSIONS),
    }


def resolve_workspace_path(
    folder_id: str,
    relative_path: str = "",
) -> Path:
    root = allowed_folders().get(folder_id)

    if root is None:
        raise LocalComputerError(
            "This folder is not designated for Artemis access."
        )

    supplied = Path(relative_path)

    if supplied.is_absolute() or supplied.drive or supplied.root:
        raise LocalComputerError(
            "Use a path relative to the designated folder."
        )

    try:
        target = (root / supplied).resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise LocalComputerError(
            "The requested path could not be resolved."
        ) from error

    if not target.is_relative_to(root):
        raise LocalComputerError(
            "The requested path is outside the designated folder."
        )

    return target


def list_local_files(
    folder_id: str = "workspace",
    relative_path: str = "",
) -> dict:
    directory = resolve_workspace_path(
        folder_id,
        relative_path,
    )

    if not directory.is_dir():
        raise LocalComputerError(
            "The requested path is not a directory."
        )

    root = allowed_folders()[folder_id]
    entries = []
    truncated = False

    try:
        for path in directory.iterdir():
            # Do not follow links when listing folder contents.
            if path.is_symlink():
                continue

            resolved = path.resolve()

            if not resolved.is_relative_to(root):
                continue

            if path.is_dir():
                kind = "directory"
                size = None
            elif path.is_file():
                kind = "file"
                size = path.stat().st_size
            else:
                continue

            if len(entries) >= MAX_DIRECTORY_ENTRIES:
                truncated = True
                break

            entries.append({
                "name": path.name,
                "relative_path": path.relative_to(root).as_posix(),
                "kind": kind,
                "size_bytes": size,
            })
    except OSError as error:
        raise LocalComputerError(
            "The directory could not be listed."
        ) from error

    entries.sort(
        key=lambda item: (
            item["kind"] != "directory",
            item["name"].lower(),
        )
    )

    return {
        "folder_id": folder_id,
        "relative_path": directory.relative_to(root).as_posix(),
        "entries": entries,
        "truncated": truncated,
    }


def read_local_text(
    folder_id: str,
    relative_path: str,
) -> dict:
    path = resolve_workspace_path(
        folder_id,
        relative_path,
    )

    if not path.is_file():
        raise LocalComputerError(
            "The requested path is not a regular file."
        )

    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise LocalComputerError(
            "This file extension is not supported for text reading."
        )

    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_FILE_BYTES + 1)
    except OSError as error:
        raise LocalComputerError(
            "The requested file could not be read."
        ) from error

    if len(content) > MAX_FILE_BYTES:
        raise LocalComputerError(
            f"The file exceeds the {MAX_FILE_BYTES}-byte reading limit."
        )

    if b"\x00" in content:
        raise LocalComputerError(
            "The file appears to contain binary or unsupported text data."
        )

    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise LocalComputerError(
            "The file must use UTF-8 text encoding."
        ) from error

    return {
        "folder_id": folder_id,
        "relative_path": path.relative_to(
            allowed_folders()[folder_id]
        ).as_posix(),
        "size_bytes": len(content),
        "content": text,
    }


def request_application_open(
    database: Session,
    application_id: str,
):
    application = application_catalog().get(application_id)

    if application is None:
        raise LocalComputerError(
            "This application is not in the configured application list."
        )

    executable = Path(application["executable"])

    if not executable.is_file():
        raise LocalComputerError(
            "The configured application executable was not found."
        )

    return create_approval_request(
        database,
        operation="local.open_application",
        resource_type="local_application",
        resource_id=application_id,
        request_payload={
            "application_id": application_id,
            "application_name": application["name"],
            "executable": str(executable),
            "arguments": [],
        },
    )


def execution_data(record: LocalActionExecution) -> dict:
    return {
        "approval_id": record.approval_id,
        "application_id": record.application_id,
        "status": record.status,
        "process_id": record.process_id,
        "started_at": record.started_at.replace(
            tzinfo=timezone.utc
        ).isoformat(),
        "finished_at": (
            record.finished_at.replace(
                tzinfo=timezone.utc
            ).isoformat()
            if record.finished_at
            else None
        ),
    }


def execute_application_open(
    database: Session,
    approval_id: UUID,
) -> dict:
    approval_key = str(approval_id)

    approval = get_approval_request(
        database,
        approval_key,
    )

    if approval is None:
        raise LocalComputerError(
            "Approval request not found."
        )

    if (
        approval.operation != "local.open_application"
        or approval.resource_type != "local_application"
    ):
        raise LocalComputerError(
            "This approval does not authorize opening an application."
        )

    if approval.status != "approved":
        raise LocalComputerError(
            "The application request has not been approved."
        )

    payload = read_approval_payload(approval)
    application_id = approval.resource_id

    application = application_catalog().get(application_id)

    if application is None or payload is None:
        raise LocalComputerError(
            "The approved application is no longer configured."
        )

    expected_payload = {
        "application_id": application_id,
        "application_name": application["name"],
        "executable": application["executable"],
        "arguments": [],
    }

    if payload != expected_payload:
        raise LocalComputerError(
            "The application configuration differs from the approved request. "
            "Create a new request."
        )

    existing = database.get(
        LocalActionExecution,
        approval_key,
    )

    if existing is not None:
        return execution_data(existing)

    executable = Path(application["executable"])

    if not executable.is_file():
        raise LocalComputerError(
            "The configured application executable was not found."
        )

    # Commit the execution claim before launching.
    # The primary key prevents concurrent requests from launching twice.
    execution = LocalActionExecution(
        approval_id=approval_key,
        application_id=application_id,
        status="starting",
        started_at=utc_now(),
    )
    database.add(execution)

    try:
        database.commit()
    except IntegrityError:
        database.rollback()

        existing = database.get(
            LocalActionExecution,
            approval_key,
        )

        if existing is None:
            raise

        return execution_data(existing)

    try:
        process = subprocess.Popen(
            [str(executable)],
            shell=False,
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except OSError:
        execution.status = "failed"
        execution.finished_at = utc_now()
        database.commit()
        return execution_data(execution)

    execution.status = "launch_submitted"
    execution.process_id = process.pid
    execution.finished_at = utc_now()
    database.commit()

    # This confirms process submission, not that a visible window
    # was successfully displayed.
    return execution_data(execution)