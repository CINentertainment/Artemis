import argparse
import ipaddress
import json
import os
import re
import socket
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from uuid import UUID

import httpx

from app.config import get_settings
from app.connectors.base import (
    ConnectorError,
    ConnectorResponseError,
    ConnectorUnavailableError,
)
from app.connectors.intel_notes import IntelNotesConnector
from app.connectors.risk_tracker import RiskTrackerConnector
from app.connectors.web_search import search_web, normalize_result_url
from app.event_extraction import EventSource, extract_events
from app.event_notes import save_event_note
from app.services.research_scope import clean_topic, is_current, initial_queries, parse_date, freshness, freshness_summary


TRACKER_URL = (
    f"http://127.0.0.1:{get_settings().port}"
    "/information-sources/dashboard-notes/records"
)
MAX_PAGE_BYTES = 2_000_000
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
    risk_tracker_evidence_id: int | None = None
    risk_tracker_error: str | None = None
    published_at: str | None = None
    modified_at: str | None = None
    date_source: str | None = None
    freshness: str = "publication_date_unknown"


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def contains_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {' '.join(words(text))} "


class ParagraphReader(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paragraphs = []
        self.current = None
        self.meta = {}
        self.scripts = []
        self.script = None
        self.suppressed = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("style", "nav", "footer"):
            self.suppressed += 1
        if tag == "meta":
            key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").casefold()
            if attrs.get("content"):
                self.meta[key] = attrs["content"]
        if tag == "script":
            self.script = [] if attrs.get("type", "").lower() == "application/ld+json" else None
            self.suppressed += 1
        if tag == "p" and not self.suppressed:
            self.current = []

    def handle_data(self, data):
        if self.script is not None:
            self.script.append(data)
        if self.current is not None and not self.suppressed:
            self.current.append(data)

    def handle_endtag(self, tag):
        if tag == "script":
            if self.script is not None:
                self.scripts.append("".join(self.script))
                self.script = None
            self.suppressed = max(0, self.suppressed - 1)
        elif tag in ("style", "nav", "footer"):
            self.suppressed = max(0, self.suppressed - 1)
        if tag == "p" and self.current is not None:
            paragraph = " ".join("".join(self.current).split())
            if len(paragraph) >= 50:
                self.paragraphs.append(paragraph)
            self.current = None

    def dates(self, url):
        published = None
        date_source = None
        for key in ("article:published_time", "datepublished", "pubdate", "publishdate", "publication_date", "dc.date.issued"):
            if parse_date(self.meta.get(key)):
                published = parse_date(self.meta[key]).isoformat()
                date_source = "page_meta:" + key
                break
        modified = next((parse_date(self.meta[k]).isoformat() for k in ("article:modified_time", "datemodified") if parse_date(self.meta.get(k))), None)
        articles = []
        def walk(value):
            if isinstance(value, list):
                for v in value: walk(v)
            elif isinstance(value, dict):
                types = value.get("@type", [])
                types = [types] if isinstance(types, str) else types
                if isinstance(types, list) and any(t in ("NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting") for t in types):
                    articles.append(value)
                # Do not treat nested related-article lists as the current page's dates.
                if "@graph" in value: walk(value["@graph"])
        for script in self.scripts:
            try: walk(json.loads(script))
            except ValueError: continue
        matching = []
        for article in articles:
            locator = article.get("url") or article.get("mainEntityOfPage")
            if isinstance(locator, dict): locator = locator.get("@id")
            if isinstance(locator, str) and locator.split("?")[0].rstrip("/") == url.split("?")[0].rstrip("/"):
                matching.append(article)
        lone = articles[0] if len(articles) == 1 else None
        choices = matching or ([lone] if lone is not None and not (lone.get("url") or lone.get("mainEntityOfPage")) else [])
        for article in choices:
            if not published and parse_date(article.get("datePublished")):
                published = parse_date(article["datePublished"]).isoformat()
                date_source = "json_ld:datePublished"
            if not modified and parse_date(article.get("dateModified")):
                modified = parse_date(article["dateModified"]).isoformat()
        return published, modified, date_source


@dataclass
class PageEvidence:
    excerpts: list[str]
    published_at: str | None
    modified_at: str | None
    date_source: str | None
    freshness: str


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


def read_page_evidence(client, url, terms, subject, *, as_of=None):
    final_url = url
    for redirect in range(4):
        with client.stream("GET", final_url) as response:
            if response.is_redirect:
                location = response.headers.get("location")
                if not location or redirect == 3:
                    raise ValueError("The page exceeded the safe redirect limit.")
                target = urljoin(final_url, location)
                public_host(target)  # Every redirect must still be public HTTPS.
                final_url = target
                continue
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
            break
    reader = ParagraphReader()
    reader.feed(body.decode(encoding, errors="replace")); reader.close()
    published, modified, date_source = reader.dates(final_url)
    from app.services.research_relevance import tokens as relevance_tokens
    core_terms=relevance_tokens(' '.join(terms))
    ranked = []
    for position, paragraph in enumerate(dict.fromkeys(reader.paragraphs)):
        score = len(core_terms.intersection(relevance_tokens(paragraph)))
        if subject and not contains_phrase(paragraph, subject):
            continue
        if core_terms and score >= min(2, len(core_terms)):
            ranked.append((score, -position, paragraph[:2000]))
    ranked.sort(reverse=True)
    return PageEvidence([p for _, _, p in ranked[:3]], published, modified, date_source,
        freshness(published, as_of or datetime.now(timezone.utc).date()))


def read_evidence(client, url, terms, subject):
    # Preserve the list-returning interface for existing agent/market integrations.
    return read_page_evidence(client, url, terms, subject).excerpts


def save_finding(
    client: httpx.Client,
    title: str,
    url: str,
    excerpts: list[str],
    *,
    metadata: dict | None = None,
) -> str:
    content = f"Source URL: {url}\n"
    if metadata:
        content += "Publisher date metadata (not event dates): " + json.dumps(metadata, ensure_ascii=False) + "\n"
    content += "\n" + "\n\n".join(excerpts)
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


def collect_pages(topic, query_plan, findings, saved_context, *, max_total=6, max_attempts=18):
    terms = {w for w in words(topic) if len(w) > 2} - QUERY_FILLER - {"current", "recent", "between", "save", "notes", "sourced"}
    quoted = re.search(r'"([^"]+)"', topic)
    subject = " ".join(words(quoted.group(1))) if quoted else ""
    attempts = 0
    used_urls = {f.url for f in findings} | set(saved_context.setdefault('collection_attempted_urls', []))
    blocked_hosts = saved_context.setdefault('collection_blocked_hosts', {})
    hosts = {}
    for finding in findings:
        host = urlsplit(finding.url).hostname
        hosts[host] = hosts.get(host, 0) + 1
    now = datetime.now(timezone.utc)
    diagnostics = saved_context.setdefault("collection_diagnostics", [])
    added = 0
    with httpx.Client(timeout=12.0, follow_redirects=False, trust_env=False) as client:
        for query, categories, time_range in query_plan:
            entry = {"query": query, "categories": categories, "time_range": time_range, "sources_added": 0, "errors": [], "skipped_candidates": [], "pages_attempted": 0}
            diagnostics.append(entry)
            try:
                sources = search_web(query, limit=10, categories=categories, time_range=time_range)
            except (ConnectorError, RuntimeError, httpx.HTTPError) as error:
                message = f"Web search failed for {query}: {error}"
                entry["errors"].append(message); saved_context["errors"].append(message)
                continue
            if not sources:
                message = f"No search results returned for: {query}"
                entry["errors"].append(message); saved_context["errors"].append(message)
            # Search dates rank candidates only; page publication metadata determines classification.
            sources = sorted(sources, key=lambda x: freshness(getattr(x, "published_at", None), now.date()) != "recent_publication_claim")
            query_attempts=0
            for source in sources:
                if len(findings) >= max_total or attempts >= max_attempts: break
                normalized=normalize_result_url(source.url)
                if not normalized:
                    entry['skipped_candidates'].append({'url':source.url,'reason':'Malformed or unsupported search-result URL; no page requested.'});continue
                if normalized in used_urls:continue
                candidate_host=(urlsplit(normalized).hostname or '').removeprefix('www.')
                if candidate_host in blocked_hosts:
                    entry['skipped_candidates'].append({'url':normalized,'reason':'Host previously returned HTTP '+str(blocked_hosts[candidate_host])+' during this run.'});continue
                if hosts.get(candidate_host,0)>=2:continue
                if query_attempts>=4:break
                used_urls.add(normalized)
                saved_context['collection_attempted_urls'].append(normalized)
                attempts += 1;query_attempts += 1;entry['pages_attempted'] += 1
                try:
                    host = public_host(normalized)
                    if hosts.get(host, 0) >= 2: continue
                    page = read_page_evidence(client, normalized, terms, subject, as_of=now.date())
                    if not page.excerpts: raise ValueError("No matching article paragraphs were found.")
                    from app.services.research_relevance import select_excerpts
                    page.excerpts, rejected = select_excerpts(topic, source.title, page.excerpts)
                    saved_context.setdefault("source_relevance", []).append({
                        "url": source.url, "title": source.title, "accepted": bool(page.excerpts),
                        "rejected_excerpts": rejected,
                    })
                    if not page.excerpts:
                        raise ValueError("No excerpts passed topic relevance; source was not saved or used for analysis.")
                    if page.freshness == "publication_date_in_future": raise ValueError("Publisher publication date is later than the collection date.")
                    # Keep room for recent evidence rather than filling every slot with background.
                    nonrecent = sum(f.freshness != "recent_publication_claim" for f in findings)
                    if is_current(topic) and page.freshness != "recent_publication_claim" and nonrecent >= 2:
                        raise ValueError("Background/undated source allowance reached; still seeking recent reporting.")
                    metadata = {"published_at": page.published_at, "modified_at": page.modified_at, "date_source": page.date_source, "freshness": page.freshness}
                    record_id = save_finding(client, source.title, normalized, page.excerpts, metadata=metadata)
                except (httpx.HTTPError, OSError, ValueError) as error:
                    if isinstance(error,httpx.HTTPStatusError) and error.response.status_code in {403,429}:
                        blocked_hosts[candidate_host]=error.response.status_code
                    message = f"Skipped source {source.url}: {error}"
                    entry["errors"].append(message); saved_context["errors"].append(message)
                    continue
                findings.append(ResearchFinding(source.title, normalized, record_id, page.excerpts,
                    published_at=page.published_at, modified_at=page.modified_at, date_source=page.date_source, freshness=page.freshness))
                hosts[host] = hosts.get(host, 0) + 1
                entry["sources_added"] += 1; added += 1
            if len(findings) >= max_total or attempts >= max_attempts: break
    saved_context.setdefault("limits", []).append(f"Collection pass tried {attempts} pages; page budget {max_attempts}, total source target {max_total}. News/time-range filters depend on the configured search engines.")
    return added


def timeline_event_date(event: dict) -> str | None:
    if "validated_event_date" in event:
        parsed = parse_date(event["validated_event_date"])
        return parsed.isoformat() if parsed else None
    when = event.get("when")
    if not when:
        return None

    if not isinstance(when, dict) or not isinstance(when.get("text"), str):
        return None
    text = when["text"].strip().replace(".", "")
    text = re.sub(r"\bSept\b", "Sep", text, flags=re.IGNORECASE)
    for date_format in (
        "%Y-%m-%d",
        "%B %d, %Y",
        "%b %d, %Y",
        "%B %d %Y",
        "%b %d %Y",
        "%d %B %Y",
        "%d %b %Y",
    ):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    return None


def collect_saved_context(topic: str, case_id: int | None) -> dict:
    context = {
        "notes": [],
        "case": None,
        "case_event_notes": [],
        "case_related": {},
        "errors": [],
        "limits": [],
    }

    if case_id is not None:
        username = os.environ.get("ARTEMIS_RISK_WRITE_USERNAME", "").strip()
        password = os.environ.get("ARTEMIS_RISK_WRITE_PASSWORD", "")
        role = os.environ.get("ARTEMIS_RISK_WRITE_ROLE", "Analyst").strip()

        if not username or not password:
            context["errors"].append(
                "Case history unavailable: Risk Tracker credentials are missing."
            )
        else:
            try:
                with RiskTrackerConnector(
                    base_url=str(get_settings().risk_tracker_url),
                    username=username,
                    password=password,
                    expected_role=role,
                ) as connector:
                    context["case"] = connector.get_case(case_id)
                    collections = {
                        "case_event_notes": connector.list_case_event_notes,
                        "indicators": connector.list_case_indicators,
                        "assessments": connector.list_case_assessments,
                        "mitigations": connector.list_case_mitigations,
                    }

                    for name, read_collection in collections.items():
                        try:
                            records = read_collection(case_id)
                            if name == "case_event_notes":
                                records = sorted(
                                    records,
                                    key=lambda note: (
                                        not note.get("event_date"),
                                        note.get("event_date") or "",
                                    ),
                                )
                                context[name] = records
                            else:
                                context["case_related"][name] = records
                        except ConnectorError as error:
                            context["errors"].append(
                                f"Case {name} unavailable: {error}"
                            )
            except (ConnectorError, ValueError) as error:
                context["errors"].append(
                    f"Case history unavailable: {error}"
                )

    try:
        with IntelNotesConnector(
            base_url=str(get_settings().intel_notes_url)
        ) as connector:
            try:
                context["notes"] = connector.list_notes(keyword=topic)
            except ConnectorError as error:
                context["errors"].append(
                    f"Saved-note search unavailable: {error}"
                )

            known_ids = {note.get("id") for note in context["notes"]}
            for link in context["case_event_notes"][:10]:
                note_id = link.get("intel_note_id")
                if type(note_id) is not int or note_id in known_ids:
                    continue

                try:
                    context["notes"].append(connector.get_note(note_id))
                    known_ids.add(note_id)
                except ConnectorError as error:
                    context["errors"].append(
                        f"Intel Note {note_id} unavailable: {error}"
                    )

            if len(context["case_event_notes"]) > 10:
                context["limits"].append(
                    "Full note bodies were requested for the first 10 case events."
                )
    except ConnectorError as error:
        context["errors"].append(f"Intel Notes unavailable: {error}")

    return context


def generate_combined_brief(topic, findings, saved_context, now):
    from app.routes.conversations import get_model_runtime
    from app.services.research_model import render_brief, compact_evidence
    from app.outlook import generate_outlook
    evidence = {
        "topic": topic, "retrieved_at": now.isoformat(),
        "research_questions": saved_context.get("research_questions", []),
        "freshness_summary": saved_context.get("freshness_summary"),
        "saved_notes": saved_context.get("notes", []),
        "selected_case": saved_context.get("case"),
        "web_evidence": [{"record_id": f.record_id, "title": f.title, "url": f.url,
            "excerpts": f.excerpts, "published_at": f.published_at,
            "modified_at": f.modified_at, "freshness": f.freshness} for f in findings],
    }
    if not findings:
        return "# Research coverage insufficient\n\nNo collected source excerpts passed topic relevance. No source-based analysis or event notes could be produced. Broaden the subject or try a shorter subject-only query."
    coverage = saved_context.get("freshness_summary") or {}
    if coverage.get("current_request") and not coverage.get("recent_dated_source_count"):
        return "# Research coverage incomplete\n\nNo recent publisher-dated reporting was collected. Current conditions were not established. Historical/undated excerpts below are background.\n\n## Outlook and scenarios\n\nAssessment status: insufficient evidence."
    runtime = get_model_runtime()
    brief = render_brief(evidence, runtime)
    try:
        outlook = generate_outlook(evidence, runtime)
    except Exception as error:
        saved_context["errors"].append(f"Outlook unavailable ({type(error).__name__}): {str(error)[:500]}")
        outlook = "## Outlook and scenarios\n\nA validated outlook was not produced. No forecast is asserted."
    return brief + "\n\n" + outlook


def run_research(
    topic: str,
    *,
    risk_tracker_case_id: int | None = None,
    research_questions: list[str] | None = None,
) -> dict:
    original_topic = topic.strip()
    topic = clean_topic(original_topic)
    research_questions = list(research_questions or [])
    if topic != original_topic:
        research_questions.append(("Original research task: " + original_topic)[:500])
    market_mode = topic.casefold().startswith("market:")
    if market_mode:
        topic = topic.split(":", 1)[1].strip()
    quoted_subject = re.search(r'"([^"]+)"', topic)
    subject = " ".join(words(quoted_subject.group(1))) if quoted_subject else ""
    terms = {word for word in words(topic) if len(word) > 2} - QUERY_FILLER
    terms.update(subject.split())

    if not terms:
        raise RuntimeError("Enter a topic with at least one specific keyword.")

    note_query = quoted_subject.group(1).strip() if quoted_subject else topic
    saved_context = collect_saved_context(note_query, risk_tracker_case_id)
    saved_context["research_questions"] = research_questions or []

    findings: list[ResearchFinding] = []
    collect_pages(topic, initial_queries(topic, datetime.now(timezone.utc)), findings, saved_context)

    if not findings:
        saved_context["errors"].append(
            "No readable web pages with matching evidence were collected."
        )


    from app.gap_collection import collect_gap_evidence

    saved_context["gap_collection"] = collect_gap_evidence(
        topic, findings, saved_context
    )

    from app.services.research_relevance import select_excerpts, filter_events
    relevant_findings = []
    for finding in findings:
        finding.excerpts, rejected = select_excerpts(topic, finding.title, finding.excerpts)
        if finding.excerpts:
            relevant_findings.append(finding)
        else:
            saved_context["errors"].append(f"Excluded off-topic source before analysis and note saving: {finding.url}")
    findings = relevant_findings

    saved_context["freshness_summary"] = freshness_summary(findings, is_current(topic), datetime.now(timezone.utc).date())
    if is_current(topic) and not saved_context["freshness_summary"]["recent_dated_source_count"]:
        saved_context["errors"].append("Current reporting not established: no page with a recent publisher publication date was collected. Historical/undated records are background only.")

    market_report = None
    if market_mode:
        from app.market_research import collect_market_research
        market_report = collect_market_research(topic, findings)

    event_notes = []
    event_extraction_error = None
    event_sources = [
        EventSource(finding.record_id, finding.title, finding.url, finding.excerpts, finding.published_at)
        for finding in findings
        if not is_current(topic) or finding.freshness == "recent_publication_claim"
    ]

    event_extraction_warnings = []
    try:
        events = extract_events(topic, event_sources, warnings=event_extraction_warnings) if event_sources else []
    except RuntimeError as error:
        event_extraction_error = str(error)
        events = []

    events, event_relevance_decisions = filter_events(topic, events)
    saved_context["limits"].extend(event_extraction_warnings)
    from app.services.research_event_policy import apply_event_policy
    events, event_candidates_skipped, event_date_decisions = apply_event_policy(
        events, current=is_current(topic), cutoff=datetime.now(timezone.utc).date(),
    )

    if events:
        with IntelNotesConnector(
            base_url=str(get_settings().intel_notes_url)
        ) as connector:
            for event in events:
                note_result = {
                    "title": event["title"],
                    "status": event["status"],
                    "intel_note_id": None,
                    "error": None,
                    "risk_tracker_case_id": None,
                    "risk_tracker_link_error": None,
                }
                try:
                    note_result["intel_note_id"] = save_event_note(connector, event)
                except (
                    ConnectorUnavailableError,
                    ConnectorResponseError,
                    ValueError,
                ) as error:
                    note_result["error"] = str(error)
                event_notes.append(note_result)

    if risk_tracker_case_id is not None:
        username = os.environ.get("ARTEMIS_RISK_WRITE_USERNAME", "").strip()
        password = os.environ.get("ARTEMIS_RISK_WRITE_PASSWORD", "")
        role = os.environ.get("ARTEMIS_RISK_WRITE_ROLE", "Analyst").strip()

        tracker_error = None
        if not username or not password:
            tracker_error = "Risk Tracker write credentials are missing."
        elif role not in {"Analyst", "Administrator"}:
            tracker_error = (
                "Risk Tracker write access requires Analyst or Administrator."
            )

        if tracker_error:
            for finding in findings:
                finding.risk_tracker_error = tracker_error
            for note in event_notes:
                if note["intel_note_id"] is not None:
                    note["risk_tracker_link_error"] = tracker_error
        else:
            with RiskTrackerConnector(
                base_url=str(get_settings().risk_tracker_url),
                username=username,
                password=password,
                expected_role=role,
            ) as connector:
                for event, note in zip(events, event_notes, strict=True):
                    note_id = note["intel_note_id"]
                    if note_id is None:
                        continue

                    try:
                        connector.link_case_event_note(
                            case_id=risk_tracker_case_id,
                            intel_note_id=note_id,
                            title=note["title"],
                            event_date=timeline_event_date(event),
                            event_status=note["status"],
                            source_urls=sorted({
                                entry["url"]
                                for entry in event["evidence"].values()
                            }),
                        )
                    except (
                        ConnectorUnavailableError,
                        ConnectorResponseError,
                        ValueError,
                    ) as error:
                        note["risk_tracker_link_error"] = str(error)
                    else:
                        note["risk_tracker_case_id"] = risk_tracker_case_id

                for finding in findings:
                    try:
                        finding.risk_tracker_evidence_id = (
                            connector.attach_case_evidence(
                                case_id=risk_tracker_case_id,
                                artemis_record_id=UUID(finding.record_id),
                                title=(
                                    finding.title.strip()
                                    or "Public web research evidence"
                                )[:250],
                                source_url=finding.url,
                                content="\n\n".join(finding.excerpts),
                            )
                        )
                    except (
                        ConnectorUnavailableError,
                        ConnectorResponseError,
                    ) as error:
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
        "## Event notes",
        "",
    ])

    for warning in event_extraction_warnings:
        lines.append("Extraction note: " + warning)
    if event_extraction_error:
        lines.append(f"Event extraction failed: {event_extraction_error}")
    elif not event_notes:
        if event_candidates_skipped:
            lines.append(f"{len(event_candidates_skipped)} event candidates were extracted but withheld from note creation. See date decisions below.")
        elif any(not item["accepted"] for item in event_relevance_decisions):
            lines.append("Event candidates were withheld because their narrative or cited excerpts did not pass topic relevance. See relevance decisions below.")
        else:
            lines.append("The collected excerpts did not identify a distinct event.")
    else:
        for note in event_notes:
            label = f"{note['title']} ({note['status']})"
            if note["intel_note_id"] is None:
                lines.append(f"- {label} | Note save failed: {note['error']}")
                continue

            line = f"- {label} | Intel Notes ID: {note['intel_note_id']}"
            if note["risk_tracker_case_id"] is not None:
                line += (
                    f" | Linked to Risk Tracker case "
                    f"{note['risk_tracker_case_id']}"
                )
            elif note["risk_tracker_link_error"]:
                line += (
                    f" | Risk Tracker link failed: "
                    f"{note['risk_tracker_link_error']}"
                )
            lines.append(line)

    lines.extend(["", "## Event relevance decisions", ""])
    for decision in event_relevance_decisions:
        lines.append("- " + json.dumps(decision, ensure_ascii=False))

    lines.extend(["", "## Event date decisions", ""])
    for decision in event_date_decisions:
        lines.append("- " + json.dumps(decision, ensure_ascii=False))

    lines.extend(["", "## Source evidence", ""])

    for index, finding in enumerate(findings, start=1):
        lines.extend([
            f"### {index}. {finding.title}",
            "",
            f"[Open original source](<{finding.url}>)",
            f"Artemis record: `{finding.record_id}`",
            f"Publisher publication date: {finding.published_at or 'unknown'} | Modified: {finding.modified_at or 'unknown'}",
            f"Date evidence: {finding.date_source or 'unavailable'} | Classification: {finding.freshness}",
        ])

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

    synthesis_error = None
    try:
        synthesis = generate_combined_brief(topic, findings, saved_context, now)
    except Exception as error:
        print(f"Combined brief generation failed ({type(error).__name__}).")
        synthesis_error = (
            f"Combined analysis unavailable ({type(error).__name__}): {str(error)[:500]}. "
            "Collected evidence and save results follow."
        )
        synthesis = synthesis_error

    lines.extend(["", "## Previously saved notes", ""])
    for note in saved_context["notes"][:10]:
        lines.extend([
            f"### Intel Note {note.get('id')}: {note.get('title')}",
            "",
            str(note.get("body") or "")[:3000],
            "",
        ])

    if saved_context["case"] is not None:
        case = saved_context["case"]
        lines.extend([
            "",
            f"Selected Risk Tracker case {case.get('id')}: {case.get('title')}",
        ])

    gap_report = saved_context.get("gap_collection", {})
    lines.extend(["", "## Follow-up collection", ""])
    lines.append(f"Status: {gap_report.get('status', 'unavailable')}")
    lines.append(
        f"Additional sources saved: {gap_report.get('sources_added', 0)}"
    )
    for search in gap_report.get("queries", []):
        lines.append(
            f"- {search['query']} | Sources saved: {search['sources_added']}"
        )

    lines.extend(['', '## Collection query diagnostics', ''])
    for entry in saved_context.get('collection_diagnostics',[]):
        lines.append('- '+json.dumps(entry,ensure_ascii=False))
    lines.extend(["", "## Collection gaps", ""])
    lines.extend(f"- {error}" for error in saved_context["errors"])
    if not saved_context["errors"]:
        lines.append("No collection failures were reported.")

    if market_report is not None:
        from app.market_research import render_market_research
        synthesis += "\n\n" + render_market_research(market_report)

    brief = synthesis + "\n\n---\n\n" + "\n".join(lines)
    output_dir = Path(os.environ["LOCALAPPDATA"]) / "Artemis" / "briefs"
    output = output_dir / f"research-{now:%Y%m%dT%H%M%S%fZ}.md"

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        output.write_text(brief, encoding="utf-8")
    except OSError as error:
        raise RuntimeError(
            f"Could not save the research brief: {error}"
        ) from None

    print(f"Brief saved: {output}")
    return {
        "brief": brief,
        "collection_outcome": ('no_usable_evidence' if not findings else
            'background_only' if is_current(topic) and not saved_context['freshness_summary']['recent_dated_source_count'] else 'evidence_collected'),
        "freshness_summary": saved_context.get("freshness_summary"),
        "collection_diagnostics": saved_context.get("collection_diagnostics", []),
        "event_candidates_skipped": event_candidates_skipped,
        "event_date_decisions": event_date_decisions,
        "source_relevance": saved_context.get("source_relevance", []),
        "event_relevance_decisions": event_relevance_decisions,
        "market_research": market_report,
        "gap_collection": saved_context.get("gap_collection"),
        "synthesis_error": synthesis_error,
        "collection_errors": saved_context["errors"],
        "saved_note_ids": [
            note.get("id") for note in saved_context["notes"][:10]
        ],
        "event_notes": event_notes,
        "event_extraction_error": event_extraction_error,
        "event_extraction_warnings": event_extraction_warnings,
        "sources": [
            {
                "title": finding.title,
                "url": finding.url,
                "record_id": finding.record_id,
                "published_at": finding.published_at,
                "modified_at": finding.modified_at,
                "date_source": finding.date_source,
                "freshness": finding.freshness,
                "risk_tracker_case_id": risk_tracker_case_id,
                "risk_tracker_evidence_id": finding.risk_tracker_evidence_id,
                "risk_tracker_error": finding.risk_tracker_error,
            }
            for finding in findings
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Collect and cite public web evidence."
    )
    parser.add_argument("topic", help="Research topic")

    try:
        run_research(parser.parse_args().topic)
    except RuntimeError as error:
        parser.exit(1, f"{error}\n")
