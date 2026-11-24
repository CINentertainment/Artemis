import re
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.task_agents import (
    AgentMission,
    MissionRequest,
    cancel_mission,
    create_mission,
    mission_data,
)


HELP = """Agent mission commands:

- Deploy a research mission to investigate YOUR TOPIC
- Deploy a market mission to research COMPANY SYMBOL
- List agent missions
- Mission status MISSION_ID
- Open mission MISSION_ID
- Cancel mission MISSION_ID

Use the mission reference, such as MSN-2026-0001. Legacy IDs also work.
Reports are also available in the Agent Missions dashboard.
"""


def resolve_mission(database: Session, reference: str):
    reference = reference.strip().lower()

    if reference.upper().startswith("MSN-"):
        from app.services.references import resolve_identifier

        reference = resolve_identifier("mission", reference)


    try:
        canonical_id = str(UUID(reference))
    except ValueError:
        if not re.fullmatch(r"[0-9a-f]{8}", reference):
            raise ValueError(
                "Use the full mission ID or its first eight characters."
            )

        matches = database.scalars(
            select(AgentMission)
            .where(AgentMission.id.startswith(reference))
            .limit(2)
        ).all()

        if not matches:
            raise ValueError("Agent mission not found.")

        if len(matches) > 1:
            raise ValueError(
                "That short ID matches multiple missions. Use the full ID."
            )

        return matches[0]

    mission = database.get(AgentMission, canonical_id)

    if mission is None:
        raise ValueError("Agent mission not found.")

    return mission


def status_message(data: dict) -> str:
    active = data["status"] in {"queued", "running"}

    lines = [
        f"Mission [{data['reference']}]: {data['status']}",
        f"Assignment: {data['assignment']}",
    ]

    for step in data["steps"]:
        status = step["status"]

        if not active and status in {"queued", "running"}:
            status = "unfinished"

        lines.append(f"- {step['agent']}: {status}")

    if data["cancel_requested"] and active:
        lines.append(
            "Cancellation requested; the current operation may finish "
            "before the worker stops."
        )

    if data["error"]:
        lines.append(f"Notice: {data['error']}")

    return "\n".join(lines)


def run_agent_command(
    database: Session,
    user_content: str,
) -> str | None:
    text = user_content.strip()

    # Accept a direct address when typed or retained by transcription.
    text = re.sub(
        r"^(?:hey\s+)?artemis[\s,:]+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"^please\s+",
        "",
        text,
        count=1,
        flags=re.IGNORECASE,
    )

    normalized = text.lower().rstrip(".!?")

    if normalized in {
        "agent help",
        "agent mission help",
        "/agents",
        "/agents help",
    }:
        return HELP

    listing = normalized in {
        "list agent missions",
        "show agent missions",
        "list my missions",
        "/agents list",
    }

    deployment = re.fullmatch(
        r"deploy\s+(?:a\s+)?(market|research)\s+mission"
        r"\s+(?:to\s+|:\s*)(.+)",
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )

    inspection = re.fullmatch(
        r"(mission status|open mission|cancel mission)\s+"
        r"([0-9a-z-]+)[.!?]?",
        text,
        flags=re.IGNORECASE,
    )

    if not any((listing, deployment, inspection)):
        if normalized.startswith((
            "/agents",
            "deploy a market mission",
            "deploy a research mission",
            "deploy market mission",
            "deploy research mission",
            "mission status ",
            "open mission ",
            "cancel mission ",
        )):
            return HELP
        return None

    try:
        if deployment:
            assignment = deployment.group(2).strip()

            if not assignment or len(assignment) > 300:
                return (
                    "Use an assignment between 1 and 300 characters."
                )

            data = create_mission(
                request=MissionRequest(
                    assignment=assignment,
                    mission_type=deployment.group(1).lower(),
                ),
                database=database,
            )

            short_id = data["reference"]

            return (
                f"Mission queued [{short_id}].\n"
                f"Assignment: {data['assignment']}\n"
                f"Workers: "
                f"{', '.join(step['agent'] for step in data['steps'])}\n\n"
                "I'll work on it in the background while Artemis is running.\n"
                f"Check progress: Mission status {short_id}\n"
                f"Read the result: Open mission {short_id}\n"
                "It will also appear in the Agent Missions dashboard."
            )

        if listing:
            missions = database.scalars(
                select(AgentMission)
                .order_by(
                    AgentMission.created_at.desc(),
                    AgentMission.id,
                )
                .limit(10)
            ).all()

            if not missions:
                return "No agent missions saved yet.\n\n" + HELP

            lines = ["Latest agent missions:", ""]

            for mission in missions:
                data = mission_data(mission)
                lines.append(
                    f"- [{data['reference']}] {data['status']} — "
                    f"{data['assignment']}"
                )

            lines.append(
                "\nSend Open mission MISSION_ID to read a report."
            )
            return "\n".join(lines)

        mission = resolve_mission(
            database,
            inspection.group(2),
        )
        action = inspection.group(1).lower()

        if action == "cancel mission":
            data = cancel_mission(
                mission_id=UUID(mission.id),
                database=database,
            )
            return status_message(data)

        data = mission_data(mission)
        message = status_message(data)

        if action == "mission status":
            return message

        brief = next(
            (
                step["result"].get("brief")
                for step in data["steps"]
                if step["agent"] == "briefing"
                and isinstance(step.get("result"), dict)
                and step["result"].get("brief")
            ),
            None,
        )

        if brief:
            return (
                message
                + "\n\nModel-generated briefing:\n\n"
                + brief
                + "\n\nSupporting worker evidence and retrieval notices "
                "are available in the Agent Missions dashboard. "
                "Retrieval time does not establish market-data freshness."
            )

        return (
            message
            + "\n\nNo briefing has been saved yet. "
            "The dashboard shows available worker results and errors."
        )

    except HTTPException as error:
        return f"Agent command could not complete: {error.detail}"
    except (ValueError, ValidationError) as error:
        return f"Agent command could not complete: {error}"