import json
from uuid import NAMESPACE_URL, uuid5

from app.connectors.intel_notes import IntelNotesConnector
from app.event_extraction import FACT_FIELDS


def save_event_note(
    connector: IntelNotesConnector,
    event: dict,
) -> int:
    evidence = event["evidence"]
    citation_numbers = {
        excerpt_id: number
        for number, excerpt_id in enumerate(sorted(evidence), start=1)
    }

    fields = [f"Event status: {event['status']}"]
    for name in FACT_FIELDS:
        fact = event[name]
        if fact is None:
            fields.append(f"{name.capitalize()}: Unknown")
            continue

        references = " ".join(
            f"[{citation_numbers[excerpt_id]}]"
            for excerpt_id in fact["excerpt_ids"]
        )
        fields.append(f"{name.capitalize()}: {fact['text']} {references}")

    sources = []
    for excerpt_id, number in citation_numbers.items():
        entry = evidence[excerpt_id]
        sources.append(
            f"[{number}] {entry['title']}\n"
            f"URL: {entry['url']}\n"
            f"Artemis source record: {entry['record_id']}\n"
            f"Excerpt: {entry['excerpt']}"
        )

    body = "\n".join(fields) + "\n\nSources\n\n" + "\n\n".join(sources)
    if len(body) > 20_000:
        raise ValueError("The event note exceeds Intel Notes' 20,000-character limit.")

    title = event["title"]
    payload = {
        "title": title,
        "body": body,
        "classification": "UNCLASSIFIED",
        "discipline": "OSINT",
    }

    # An exact retry of this rendered event note uses the same import UUID.
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    record_id = uuid5(NAMESPACE_URL, f"artemis:event-note:v1:{canonical}")

    return connector.import_artemis_note(record_id=record_id, **payload)