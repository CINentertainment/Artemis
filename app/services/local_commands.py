import re
from uuid import UUID

from sqlalchemy.orm import Session

from app.services.approvals import (
    ApprovalContentError,
    ApprovalStateError,
    decide_approval_request,
    get_approval_request,
)
from app.services.local_computer import (
    LocalComputerError,
    execute_application_open,
    list_local_files,
    read_local_text,
    request_application_open,
)
from app.services.workspace_files import (
    save_workspace_text,
    workspace_file_version,
)


HELP = """Local computer commands:

- Open Notepad
- Open Calculator
- Run Artemis diagnostics
- List workspace files
- Read file computer-test.txt

To create a file:
Create file example.txt
Put the complete file content on the following lines.

To replace a file:
Replace file example.txt
Put the complete replacement content on the following lines.

To append text:
Append to file example.txt
Put the additional text on the following lines.

Your direct command authorizes the requested action.
File paths are relative to Artemis Workspace.
Create never overwrites an existing file.
Replace and Append keep a backup of the previous contents.
Append adds the supplied text exactly, without inserting a newline.

Existing application-request commands:
- /computer approve REQUEST_ID
- /computer reject REQUEST_ID
"""


def execution_message(result: dict) -> str:
    if result["status"] == "launch_submitted":
        name = {
            "notepad": "Notepad",
            "calculator": "Calculator",
        }.get(
            result["application_id"],
            result["application_id"],
        )

        return (
            f"Launch request sent to Windows for {name}.\n"
            f"Process ID: {result['process_id']}"
        )

    if result["status"] == "starting":
        return (
            "This request already has an execution record, but launch "
            "completion was not recorded. It will not be retried automatically."
        )

    return (
        f"Application action status: {result['status']}.\n"
        "Send a new Open command if you want to try again."
    )


def application_request(
    database: Session,
    application_id: str,
) -> str:
    approval = request_application_open(
        database,
        application_id,
    )

    decide_approval_request(
        database,
        approval,
        decision="approved",
        reason=(
            "Authorized by the user's direct chat command to open "
            "this configured application."
        ),
    )

    result = execute_application_open(
        database,
        UUID(str(approval.id)),
    )

    return execution_message(result)


def decide_application_request(
    database: Session,
    request_id: str,
    approve: bool,
) -> str:
    approval_id = UUID(request_id)

    approval = get_approval_request(
        database,
        str(approval_id),
    )

    if approval is None:
        raise LocalComputerError("Application request not found.")

    if (
        approval.operation != "local.open_application"
        or approval.resource_type != "local_application"
    ):
        raise LocalComputerError(
            "This is not a local application request."
        )

    if not approve:
        if approval.status == "rejected":
            return "This application request is already rejected."

        if approval.status != "pending":
            return (
                "This request has already been approved and cannot "
                "be rejected. Rejecting a request does not close "
                "an application that has already launched."
            )

        decide_approval_request(
            database,
            approval,
            decision="rejected",
            reason="Rejected through an explicit local chat command.",
        )

        return "Application request rejected. Nothing was launched."

    if approval.status == "pending":
        decide_approval_request(
            database,
            approval,
            decision="approved",
            reason="Approved through an explicit local chat command.",
        )
    elif approval.status != "approved":
        raise LocalComputerError(
            "This request was rejected. Create a new application request."
        )

    result = execute_application_open(
        database,
        approval_id,
    )

    return execution_message(result)


def unquote_path(value: str) -> str:
    value = value.strip()

    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] in {'"', "'"}
    ):
        return value[1:-1]

    return value


def run_file_write(
    command: str,
    relative_path: str,
    content: str,
) -> str:
    mode = {
        "create file": "create",
        "replace file": "replace",
        "append to file": "append",
    }[command.lower()]

    relative_path = unquote_path(relative_path)

    expected_sha256 = (
        workspace_file_version(
            folder_id="workspace",
            relative_path=relative_path,
        )
        if mode != "create"
        else None
    )

    result = save_workspace_text(
        relative_path=relative_path,
        content=content,
        mode=mode,
        folder_id="workspace",
        expected_sha256=expected_sha256,
    )

    verb = {
        "create": "Created",
        "replace": "Replaced",
        "append": "Appended text to",
    }[mode]

    lines = [
        f"{verb} Artemis Workspace file: {result['relative_path']}",
        f"Size: {result['size_bytes']} bytes",
    ]

    if result["backup_id"]:
        lines.append(
            f"Previous contents backed up. Backup ID: {result['backup_id']}"
        )

    lines.extend([
        "",
        "To read the saved contents, send:",
        f"Read file {result['relative_path']}",
    ])

    return "\n".join(lines)


def run_local_command(
    database: Session,
    user_content: str,
) -> str | None:
    from app.services.note_commands import run_note_command

    note_reply = run_note_command(user_content)
    if note_reply is not None:
        return note_reply

    # Preserve the file body, including indentation and blank lines.
    # The first newline separates the command from its content.
    from app.services.memory import run_memory_command

    memory_reply = run_memory_command(database, user_content)
    if memory_reply is not None:
        return memory_reply

    header, separator, body = user_content.partition("\n")
    header = header.strip()

    writing = re.fullmatch(
        r"(create file|replace file|append to file)\s+(.+)",
        header,
        flags=re.IGNORECASE,
    )

    if writing:
        if not separator:
            return (
                "Put the file content on a new line after the command.\n\n"
                "Example:\n"
                "Create file example.txt\n"
                "Artemis created this file."
            )

        try:
            return run_file_write(
                writing.group(1),
                writing.group(2),
                body,
            )
        except (LocalComputerError, ValueError) as error:
            return f"File operation could not complete: {error}"

    from app.services.agent_commands import run_agent_command

    agent_reply = run_agent_command(database, user_content)
    if agent_reply is not None:
        return agent_reply

    from app.services.research_chat import run_research_command

    research_reply = run_research_command(database, user_content)
    if research_reply is not None:
        return research_reply

    from app.services.tasks import run_task_command

    task_reply = run_task_command(database, user_content)
    if task_reply is not None:
        return task_reply

    text = user_content.strip()

    if text.lower().rstrip(".!") in {
        "run artemis diagnostics",
        "/computer diagnostics",
    }:
        from app.services.local_scripts import run_artemis_diagnostics
        return run_artemis_diagnostics()


    if text.lower() in {
        "/computer",
        "/computer help",
        "computer help",
    }:
        return HELP

    application = re.fullmatch(
        r"(?:please\s+)?open\s+(notepad|calculator)[.!]?",
        text,
        flags=re.IGNORECASE,
    )

    decision = re.fullmatch(
        r"/computer\s+(approve|reject)\s+"
        r"([0-9a-fA-F-]{36})",
        text,
        flags=re.IGNORECASE,
    )

    listing = text.lower() in {
        "list workspace files",
        "/computer files",
    }

    reading = re.fullmatch(
        r"(?:read\s+file|/computer\s+read)\s+(.+)",
        text,
        flags=re.IGNORECASE,
    )

    if not any((application, decision, listing, reading)):
        if text.lower().startswith("/computer"):
            return HELP
        return None

    try:
        if application:
            return application_request(
                database,
                application.group(1).lower(),
            )

        if decision:
            return decide_application_request(
                database,
                decision.group(2),
                decision.group(1).lower() == "approve",
            )

        if listing:
            result = list_local_files()
            lines = ["Artemis Workspace files:", ""]

            for entry in result["entries"]:
                suffix = "/" if entry["kind"] == "directory" else ""
                lines.append(f"- {entry['relative_path']}{suffix}")

            if not result["entries"]:
                lines.append("The workspace is empty.")

            if result["truncated"]:
                lines.append("\nThe listing reached its entry limit.")

            return "\n".join(lines)

        result = read_local_text(
            folder_id="workspace",
            relative_path=unquote_path(reading.group(1)),
        )

        return (
            f"Read from Artemis Workspace: {result['relative_path']}\n"
            f"Size: {result['size_bytes']} bytes\n\n"
            "File contents follow as text:\n\n"
            + result["content"]
        )

    except (
        LocalComputerError,
        ApprovalStateError,
        ValueError,
    ) as error:
        return f"Computer action could not complete: {error}"
    except ApprovalContentError:
        return (
            "Computer action could not complete: "
            "approval data could not be decrypted."
        )
