"""Route explicit chat research requests through Artemis's existing saved workflow."""
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4


@dataclass(frozen=True)
class ResearchIntent:
    topic: str
    capability_question: bool = False
    read_only: bool = False
    original_request: str = ""


CAPABILITY_REPLY = (
    "Yes. Artemis has an on-demand web research workflow that can collect sources, "
    "prepare a research brief, and save event notes when collection and saving succeed. "
    "Tell me a topic and what you want to learn. For example:\n\n"
    "Research the current Russia–Ukraine war and save sourced notes about affected "
    "people, places, and ideas.\n\n"
    "I will report the sources and saved note IDs returned by the workflow, along "
    "with any failures or gaps. This reply has not started research or monitoring."
)


def parse_research_intent(content: str) -> ResearchIntent | None:
    text = content.strip()
    # Existing file, memory, task and agent commands retain their own parsing paths.
    # Quoted requests and prose containing the word 'research' are not commands.
    if not text or text.startswith(('"', "'", '`', '>')):
        return None
    simple = re.sub(r'\s+', ' ', text).strip()
    lower = simple.lower().replace('’', "'")
    if re.match(r'^(?:so[, ]+)?(?:can|could|are)\s+you\b', lower):
        if re.search(r'\b(?:make|create|take|save|collect|capable|able)\b',lower) and re.search(r'\b(?:notes?|research)\b',lower) and re.search(r'\b(?:events?|world|news|research|internet|web)\b',lower):
            # Do not treat a direct request with a concrete trailing topic as a capability-only question.
            if not re.search(r'\b(?:research|information|notes?)\s+(?:on|about|regarding)\s+\S',lower):
                return ResearchIntent('', capability_question=True)
    prefixes = (
        r'^(?:please\s+)?(?:research|investigate)\s+(?!capabilit(?:y|ies)\b)(.+)$',
        r'^(?:(?:we|i)\s+need|i\s+want|(?:can|could|would)\s+you\s+(?:get|find|gather|collect)|(?:please\s+)?(?:get|find|gather|collect))\s+(?:(?:current|latest|recent|up.to.date)\s+)?information\s+(?:on|about|regarding)\s+(.+)$',
        r'^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?(?:make|create|take|save)\s+(?:sourced\s+|research\s+)?notes?\s+(?:on|about|regarding)\s+(.+)$',
        r'^(?:can|could|would)\s+you\s+(?:please\s+)?research\s+(.+)$',
    )
    match = next((m for p in prefixes if (m := re.match(p,simple,flags=re.I))),None)
    if not match:return None
    topic = match.group(1).strip().rstrip(' .!?')
    if not topic:return None
    # Respect an explicit no-write instruction: the existing research runner saves records.
    readonly = bool(re.search(r"\b(?:do\s+not|don't|without)\s+(?:sav(?:e|ing)|stor(?:e|ing)|creat(?:e|ing)\s+notes?)\b|\bread.only\b",lower))
    from app.services.research_scope import clean_topic
    return ResearchIntent(clean_topic(topic),read_only=readonly,original_request=simple)


def is_research_chat_request(content: str) -> bool:
    return parse_research_intent(content) is not None


def research_questions(topic: str, as_of: str) -> list[str]:
    return [
        f"As of {as_of}, what recent developments are supported by collected sources? Distinguish publication dates, event dates, historical context and unverified claims. Do not imply old reporting is current.",
        "Which people or groups are negatively affected, and what specific source evidence establishes the impact? Separate reported facts from inference and uncertainty.",
        "Which places, communities or infrastructure are negatively affected, and what specific source evidence establishes the impact?",
        "Which ideas, rights, institutions or systems are negatively affected? Explain the evidence and label analytical interpretations rather than treating them as established facts.",
        "Provide source URLs and available dates, identify conflicting accounts and information gaps, and keep event-note candidates tied to their supporting evidence.",
    ]


def describe_warnings(result: dict) -> list[str]:
    warnings=[]
    for key in ('collection_errors','event_extraction_error','synthesis_error'):
        value=result.get(key)
        if value:warnings.append(key.replace('_',' ')+': '+json.dumps(value,ensure_ascii=False,default=str)[:1500])
    for collection in ('sources','event_notes'):
        for item in result.get(collection) or []:
            if not isinstance(item,dict):continue
            for key,value in item.items():
                if 'error' in key.lower() and value:
                    warnings.append(f"{collection}: {key}: {str(value)[:500]}")
    return warnings


def format_result(run: dict, requisition: dict) -> str:
    reference = run.get('reference') or run.get('id') or 'unavailable'
    reqref = requisition.get('reference') or requisition.get('id') or 'unavailable'
    lines=[f"Research run: {reference}",f"Requisition: {reqref}",f"Workflow status: {run.get('status','unknown')}"]
    if run.get('status')!='completed':
        lines.extend(['', 'The workflow did not return a completed result. No successful note save is confirmed by this chat response. Check the saved run for errors and any partial writes.'])
        if run.get('error'):lines.append('Run error: '+str(run['error'])[:1500])
        return '\n'.join(lines)
    result=run.get('result')
    if not isinstance(result,dict):
        return '\n'.join(lines+['','The run has no supported structured result. No note saves are confirmed here.'])
    sources=[s for s in result.get('sources') or [] if isinstance(s,dict)]
    urls=list(dict.fromkeys(s['url'].strip() for s in sources if isinstance(s.get('url'),str) and s['url'].strip().lower().startswith(('http://','https://'))))
    outcome=result.get('collection_outcome') or ('evidence_collected' if urls else 'no_usable_evidence')
    lines.extend(['', 'Research collection outcome: '+str(outcome).replace('_',' ')])
    notes=[n for n in result.get('event_notes') or [] if isinstance(n,dict)]
    saved=list(dict.fromkeys(n['intel_note_id'] for n in notes if type(n.get('intel_note_id')) is int and n['intel_note_id']>0))
    lines.extend(['',f"Source URLs returned: {len(urls)}",f"Saved Intel Note IDs returned: {len(saved)}"])
    if saved:lines.append('Intel Notes: '+', '.join(str(n) for n in saved))
    else:lines.append('No saved Intel Notes were confirmed in this result. A completed run alone does not mean notes were saved.')
    coverage=result.get('freshness_summary')
    if isinstance(coverage,dict):
        lines.extend(['', 'Date coverage: '+str(coverage.get('status','unknown')),
            'Recently published sources / distinct hosts: '+str(coverage.get('recent_dated_source_count',0))+' / '+str(coverage.get('recent_source_host_count',0))])
        if coverage.get('current_request') and not coverage.get('recent_dated_source_count'):
            lines.append('Current reporting was not established. Historical/undated sources must not be treated as a current update.')
    skipped=result.get('event_candidates_skipped') or []
    if skipped:lines.append(f'Event candidates withheld from note creation: {len(skipped)} (see saved run for reasons).')
    brief=result.get('brief')
    if urls and isinstance(brief,str) and brief.strip():
        lines.extend(['','Research brief (from the saved workflow):',brief.strip()[:16000]])
        if len(brief.strip())>16000:lines.append('[Brief shortened for chat. Open the saved research run for the full result.]')
    elif not urls:
        lines.extend(['','No usable source URLs were returned. This result does not establish current events; no answer from model memory has been substituted.'])
    else:lines.extend(['','No text brief was returned. Open the saved run to inspect its collected records.'])
    if urls:
        lines.extend(['','Sources:']+['- '+u for u in urls[:12]])
        if len(urls)>12:lines.append(f'- {len(urls)-12} more source URLs are available in the saved run.')
    warnings=describe_warnings(result)
    if warnings:lines.extend(['','Warnings / partial failures:']+['- '+w for w in warnings[:12]])
    lines.extend(['','Collection attempt finished: '+datetime.now(timezone.utc).isoformat(timespec='seconds'), 'See Research requisitions / saved runs and the Research updates inbox for the complete output. Collection time is not the event date or proof that every source is current.'])
    return '\n'.join(lines)


def run_research_command(database, user_content: str) -> str | None:
    intent=parse_research_intent(user_content)
    if intent is None:return None
    if intent.capability_question:return CAPABILITY_REPLY
    if intent.read_only:
        return ('The existing research workflow saves collected records and may create event notes. Your request says not to save, so I have not started that workflow. Read-only web research needs a separate path; no notes or research run were created by this command.')
    if not intent.topic:
        return "Name a specific research topic. Nothing has been started."
    if len(intent.topic)>300:
        return ('This research workflow accepts a topic up to 300 characters. Shorten the research request to one focused topic; nothing has been started.')
    from fastapi import HTTPException
    from app.routes.research import RequisitionRequest, RunRequest, create_requisition, run_requisition
    requisition=None
    try:
        requisition=create_requisition(RequisitionRequest(topic=intent.topic,questions=research_questions(intent.topic,datetime.now(timezone.utc).date().isoformat()) + ['Original research task (subject already separated): '+intent.original_request[:430]]),database=database)
        # The existing saved-run function uses separate sessions and can write through local endpoints.
        # create_requisition already commits; release any reference-label read transaction as well.
        database.commit()
        run=run_requisition(str(requisition['id']),RunRequest(request_id=uuid4()))
        return format_result(run,requisition)
    except HTTPException as error:
        reference=(requisition.get('reference') or requisition.get('id')) if requisition else None
        message=f"Research could not complete: {error.detail}"
        if reference:message+=f"\nRequisition saved: {reference}. A completed run and successful note saves are not confirmed here."
        return message
    except Exception as error:
        # Do not fall back to invented current information after an operational failure.
        reference=(requisition.get('reference') or requisition.get('id')) if requisition else None
        message=f"Research could not complete ({type(error).__name__}). Inspect Artemis's server log and saved runs. The workflow was not retried automatically; it may have partial saved records."
        if reference:message+=f"\nRequisition: {reference}"
        return message
