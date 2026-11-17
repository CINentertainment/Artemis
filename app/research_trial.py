import argparse
import ipaddress
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import httpx

from app.config import get_settings
from app.connectors.base import (
    ConnectorResponseError,
    ConnectorUnavailableError,
)
from app.connectors.intel_notes import IntelNotesConnector
from app.connectors.risk_tracker import RiskTrackerConnector
from app.connectors.web_search import search_web


TRACKER_URL = "http://127.0.0.1:7000/information-sources/dashboard-notes/records"
MAX_PAGE_BYTES = 2_000_000
INTEL_NOTES_TITLE_LIMIT = 250
INTEL_NOTES_SOURCE_LIMIT = 250
INTEL_NOTES_BODY_LIMIT = 20_000
QUERY_FILLER = {
    "the", "and", "for", "with", "about", "from", "what", "how",
    "overview", "latest", "updates", "objectives", "mission", "research",
}


@dataclass
class ResearchFinding:
    title: str
    url: str
    record_id: str
    excerpts: list[str]
    intel_note_id: int | None = None
    intel_note_error: str | None = None
    risk_tracker_evidence_id: int | None = None
    risk_tracker_error: str | None = None


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def contains_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {' '.join(words(text))} "


class ParagraphReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paragraphs: list[str] = []
        self.current: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "p":
            self.current = []

    def handle_data(self, data):
        if self.current is not None:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "p" and self.current is not None:
            paragraph = " ".join("".join(self.current).split())
            if len(paragraph) >= 80:
                self.paragraphs.append(paragraph)
            self.current = None


def public_host(url: str) -> str:
    parts = urlsplit(url)
    host = parts.hostname
    if (
        parts.scheme != "https"
        or not host
        or parts.username is not None
        or parts.port not in (None, 443)
    ):
        raise ValueError("Expected a public HTTPS URL.")

    addresses = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    if not addresses or any(
        not ipaddress.ip_address(address[4][0]).is_global
        for address in addresses
    ):
        raise ValueError("The page resolves to a non-public address.")

    return host.removeprefix("www.")


def read_evidence(
    client: httpx.Client,
    url: str,
    terms: set[str],
    subject: str,
) -> list[str]:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        mime_type = response.headers.get("content-type", "").split(";", 1)[0]
        if mime_type.strip().lower() != "text/html":
            raise ValueError("The response is not an HTML page.")

        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=65536):
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                raise ValueError("The page exceeds the download limit.")
        encoding = response.encoding or "utf-8"

    reader = ParagraphReader()
    reader.feed(body.decode(encoding, errors="replace"))
    reader.close()

    ranked = []
    for paragraph in dict.fromkeys(reader.paragraphs):
        if subject and not contains_phrase(paragraph, subject):
            continue
        score = len(terms.intersection(words(paragraph)))
        if score >= min(2, len(terms)):
            ranked.append((score, paragraph))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [paragraph for _, paragraph in ranked[:2]]


def save_finding(
    client: httpx.Client,
    title: str,
    url: str,
    excerpts: list[str],
) -> str:
    content = f"Source URL: {url}\n\n" + "\n\n".join(excerpts)
    fingerprint = sha256(content.encode("utf-8")).hexdigest()
    response = client.post(
        TRACKER_URL,
        json={
            "title": title,
            "content": content,
            "source_locator": url,
            "external_id": f"web-research-{fingerprint}",
        },
    )
    response.raise_for_status()
    record_id = response.json()["id"]
    print(f"HTTP {response.status_code} | Artemis record ID: {record_id}")
    print(f"Source: {url}")
    return record_id


def import_finding(
    connector: IntelNotesConnector,
    finding: ResearchFinding,
) -> None:
    title = (finding.title.strip() or "Public web research evidence")[
        :INTEL_NOTES_TITLE_LIMIT
    ].rstrip()
    body = (
        f"Artemis record ID: {finding.record_id}\n"
        f"Source URL: {finding.url}\n\n"
        + "\n\n".join(finding.excerpts)
    )
    if len(body) > INTEL_NOTES_BODY_LIMIT:
        suffix = "\n\n[Full excerpts are in the Artemis record.]"
        body = body[:INTEL_NOTES_BODY_LIMIT - len(suffix)].rstrip() + suffix

    try:
        finding.intel_note_id = connector.import_artemis_note(
            record_id=UUID(finding.record_id),
            title=title,
            body=body,
            classification="UNCLASSIFIED",
            discipline="OSINT",
            source=(
                finding.url
                if len(finding.url) <= INTEL_NOTES_SOURCE_LIMIT
                else None
            ),
        )
    except (ConnectorUnavailableError, ConnectorResponseError) as error:
        finding.intel_note_error = str(error)
        print(f"Intel Notes import failed for {finding.record_id}: {error}")
        return

    print(f"Intel Notes ID: {finding.intel_note_id}")


def run_research(
    topic: str, *, risk_tracker_case_id: int | None = None
) -> dict:
    topic = topic.strip()
    quoted_subject = re.search(r'"([^"]+)"', topic)
    subject = " ".join(words(quoted_subject.group(1))) if quoted_subject else ""
    terms = {word for word in words(topic) if len(word) > 2} - QUERY_FILLER
    terms.update(subject.split())
    if not terms:
        raise RuntimeError("Enter a topic with at least one specific keyword.")

    sources = search_web(topic, limit=10)
    findings: list[ResearchFinding] = []
    used_hosts = set()

    with httpx.Client(
        timeout=15.0,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for source in sources:
            try:
                host = public_host(source.url)
                if host in used_hosts:
                    continue
                if subject and not contains_phrase(source.title, subject):
                    continue
                excerpts = read_evidence(client, source.url, terms, subject)
            except (httpx.HTTPError, OSError, ValueError) as error:
                print(f"Skipped {source.url} ({error})")
                continue

            if not excerpts:
                continue

            try:
                record_id = save_finding(client, source.title, source.url, excerpts)
            except httpx.HTTPError as error:
                raise RuntimeError(f"Artemis evidence write failed: {error}") from None

            findings.append(
                ResearchFinding(source.title, source.url, record_id, excerpts)
            )
            used_hosts.add(host)
            if len(findings) == 2:
                break

    if not findings:
        raise RuntimeError("No readable pages with matching evidence were found.")

    with IntelNotesConnector(
        base_url=str(get_settings().intel_notes_url)
    ) as connector:
        for finding in findings:
            import_finding(connector, finding)

    if risk_tracker_case_id is not None:
        username = os.environ.get("ARTEMIS_RISK_WRITE_USERNAME", "").strip()
        password = os.environ.get("ARTEMIS_RISK_WRITE_PASSWORD", "")
        role = os.environ.get("ARTEMIS_RISK_WRITE_ROLE", "Analyst")

        if not username or not password:
            for finding in findings:
                finding.risk_tracker_error = "Risk Tracker write credentials are missing."
        else:
            with RiskTrackerConnector(
                base_url=str(get_settings().risk_tracker_url),
                username=username,
                password=password,
                expected_role=role,
            ) as connector:
                for finding in findings:
                    try:
                        finding.risk_tracker_evidence_id = connector.attach_case_evidence(
                            case_id=risk_tracker_case_id,
                            artemis_record_id=UUID(finding.record_id),
                            title=(finding.title.strip() or "Public web research evidence")[:250],
                            source_url=finding.url,
                            content="\n\n".join(finding.excerpts),
                            intel_note_id=finding.intel_note_id,
                        )
                    except (ConnectorUnavailableError, ConnectorResponseError) as error:
                        finding.risk_tracker_error = str(error)

    now = datetime.now(timezone.utc)
    lines = [
        "# Artemis research brief",
        "",
        f"Topic: {topic}",
        f"Retrieved: {now:%Y-%m-%d %H:%M UTC}",
        f"Sources with matching excerpts: {len(findings)}",
        "",
        "## Key findings",
        "",
        "Selected excerpts from the collected sources:",
        "",
    ]
    for index, finding in enumerate(findings, start=1):
        lines.extend([
            f"**[{index}] {finding.title}**",
            "",
            f"> {finding.excerpts[0]}",
            "",
        ])

    lines.extend([
        "Evidence limits: These excerpts cover only the sources listed below.",
        "",
        "## Source evidence",
        "",
    ])

    for index, finding in enumerate(findings, start=1):
        lines.extend([
            f"### {index}. {finding.title}",
            "",
            f"[Open original source](<{finding.url}>)",
            f"Artemis record: `{finding.record_id}`",
        ])
        if finding.intel_note_id is not None:
            lines.append(f"Intel Notes ID: `{finding.intel_note_id}`")
        else:
            lines.append(f"Intel Notes import failed: {finding.intel_note_error}")

        if risk_tracker_case_id is not None:
            if finding.risk_tracker_evidence_id is not None:
                lines.append(
                    f"Risk Tracker case `{risk_tracker_case_id}` evidence ID: "
                    f"`{finding.risk_tracker_evidence_id}`"
                )
            elif finding.risk_tracker_error:
                lines.append(
                    f"Risk Tracker attachment failed: {finding.risk_tracker_error}"
                )

        lines.append("")
        for excerpt in finding.excerpts:
            lines.extend([f"> {excerpt}", ""])

    brief = "\n".join(lines)
    output_dir = Path(os.environ["LOCALAPPDATA"]) / "Artemis" / "briefs"
    output = output_dir / f"research-{now:%Y%m%dT%H%M%S%fZ}.md"
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        output.write_text(brief, encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"Could not save the research brief: {error}") from None

    print(f"Brief saved: {output}")
    return {
        "brief": brief,
        "sources": [
            {
                "title": finding.title,
                "url": finding.url,
                "record_id": finding.record_id,
                "intel_note_id": finding.intel_note_id,
                "intel_note_error": finding.intel_note_error,
                "risk_tracker_case_id": risk_tracker_case_id,
                "risk_tracker_evidence_id": finding.risk_tracker_evidence_id,
                "risk_tracker_error": finding.risk_tracker_error,
            }
            for finding in findings
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect and cite public web evidence.")
    parser.add_argument("topic", help="Research topic")
    try:
        run_research(parser.parse_args().topic)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")