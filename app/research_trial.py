import argparse
import ipaddress
import os
import re
import socket
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.connectors.web_search import search_web


TRACKER_URL = "http://127.0.0.1:7000/information-sources/dashboard-notes/records"
MAX_PAGE_BYTES = 2_000_000
QUERY_FILLER = {
    "the", "and", "for", "with", "about", "from", "what", "how",
    "overview", "latest", "updates", "objectives", "mission", "research",
}


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
    print(f"HTTP {response.status_code} | Record ID: {record_id}")
    print(f"Source: {url}")
    return record_id


def run_research(topic: str) -> dict:
    topic = topic.strip()
    quoted_subject = re.search(r'"([^"]+)"', topic)
    subject = " ".join(words(quoted_subject.group(1))) if quoted_subject else ""
    terms = {word for word in words(topic) if len(word) > 2} - QUERY_FILLER
    terms.update(subject.split())
    if not terms:
        raise RuntimeError("Enter a topic with at least one specific keyword.")

    sources = search_web(topic, limit=10)
    findings = []
    used_hosts = set()

    with httpx.Client(timeout=15.0, follow_redirects=False, trust_env=False) as client:
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
                raise RuntimeError(f"Tracker write failed: {error}") from None

            findings.append((source.title, source.url, record_id, excerpts))
            used_hosts.add(host)
            if len(findings) == 2:
                break

    if not findings:
        raise RuntimeError("No readable pages with matching evidence were found.")

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
    for index, (title, _url, _record_id, excerpts) in enumerate(findings, start=1):
        lines.extend([f"**[{index}] {title}**", "", f"> {excerpts[0]}", ""])

    lines.extend([
        "Evidence limits: These excerpts cover only the sources listed below.",
        "",
        "## Source evidence",
        "",
    ])

    for index, (title, url, record_id, excerpts) in enumerate(findings, start=1):
        lines.extend([
            f"### {index}. {title}",
            "",
            f"[Open original source](<{url}>)",
            f"Tracker record: `{record_id}`",
            "",
        ])
        for excerpt in excerpts:
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
            {"title": title, "url": url, "record_id": record_id}
            for title, url, record_id, _ in findings
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Collect and cite public web evidence.")
    parser.add_argument("topic", help="Research topic")
    try:
        run_research(parser.parse_args().topic)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")