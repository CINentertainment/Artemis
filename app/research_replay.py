"""Re-analyse archived evidence locally; never searches or saves external notes."""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from app.config import get_settings
from app.database import SessionLocal
from app.research_models import ResearchRun
from app.services.references import resolve_identifier
from app.services.research_model import render_brief
from app.services.research_event_policy import apply_event_policy
from app.event_extraction import EventSource, extract_events
from app.routes.conversations import get_model_runtime


def recover_sources(result):
    brief=result.get('brief','')
    if '## Source evidence' not in brief:raise ValueError('Archived source evidence section is missing; no replay attempted.')
    section=brief.split('## Source evidence',1)[1].split('## Previously saved notes',1)[0]
    blocks=re.split(r'(?m)^### \d+\. ',section)[1:]
    recovered=[]
    for record in result.get('sources',[])[:8]:
        rid=str(record['record_id']);url=record['url']
        matches=[b for b in blocks if f'Artemis record: `{rid}`' in b and f'[Open original source](<{url}>)' in b]
        if len(matches)!=1:raise ValueError(f'Expected exactly one archived evidence block for {rid}.')
        excerpts=re.findall(r'(?m)^> (.+)$',matches[0])
        if not excerpts:raise ValueError(f'No archived excerpts for {rid}.')
        recovered.append({**record,'excerpts':excerpts})
    if not recovered:raise ValueError('No saved sources were available.')
    return recovered


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--run',required=True);parser.add_argument('--outlook-only',action='store_true',help='Replay only the conditional outlook; skip factual selection and note extraction.')
    parser.add_argument('--capture-outlook-drafts',action='store_true',help='Save raw model replies and validation errors in the local outlook-only replay and print them for diagnosis.')
    parser.add_argument('--notes-only',action='store_true',help='Preview grounded event-note candidates only; skip factual brief and outlook generation; never saves external notes.')
    parser.add_argument('--capture-note-context',action='store_true',help='Save and print raw optional-field context replies and binding decisions; requires --notes-only.')
    args=parser.parse_args()
    if args.capture_note_context and not args.notes_only:parser.error('--capture-note-context requires --notes-only')
    if args.notes_only and args.outlook_only:parser.error('--notes-only and --outlook-only cannot be combined')
    if args.capture_outlook_drafts and not args.outlook_only:
        parser.error('--capture-outlook-drafts requires --outlook-only')
    run_id=resolve_identifier('run',args.run)
    with SessionLocal() as database:
        run=database.get(ResearchRun,str(run_id))
        if run is None or run.result_json is None:raise SystemExit('Saved research run with evidence not found.')
        result=json.loads(run.result_json)
    records=recover_sources(result)
    original=result.get('brief','')
    topic_match=re.search(r'(?m)^Topic: (.+)$',original)
    cutoff_match=re.search(r'(?m)^Retrieved: (\d{4}-\d{2}-\d{2})',original)
    if not topic_match or not cutoff_match:raise SystemExit('Original topic or retrieval cutoff could not be recovered.')
    from app.services.research_scope import clean_topic
    from app.services.research_relevance import filter_sources, filter_events
    topic=clean_topic(topic_match.group(1));cutoff=datetime.fromisoformat(cutoff_match.group(1)).date()
    records, source_relevance = filter_sources(topic, records)
    evidence={'topic':topic,'retrieved_at':cutoff.isoformat(), 'web_evidence':records,
        'freshness_summary':result.get('freshness_summary'), 'saved_notes':[],
        'research_questions':['Summarize collected reporting; identify supported impacts on people, places and institutions.']}
    output={'original_run':args.run,'source_cutoff':cutoff.isoformat(),'replayed_at':datetime.now(timezone.utc).isoformat(),
        'new_web_collection':False,'external_notes_saved':False,'sources':records,'source_relevance':source_relevance}
    if args.outlook_only:
        from app.outlook import generate_outlook
        output['replay_mode'] = 'outlook_only'
        print(f'Analysing saved outlook evidence for {args.run}, cutoff {cutoff}. No web collection or note writes.', flush=True)
        diagnostics={} if args.capture_outlook_drafts else None
        if diagnostics is not None:output['outlook_diagnostics']=diagnostics
        try:output['outlook'] = generate_outlook(evidence, get_model_runtime(), diagnostics=diagnostics)
        except Exception as error:output['outlook_error'] = f'{type(error).__name__}: {error}'
        warnings=[]
    else:
        output['replay_mode']='notes_only' if args.notes_only else 'full'
        if not args.notes_only:
            try:output['brief']=render_brief(evidence,get_model_runtime())
            except Exception as error:output['synthesis_error']=f'{type(error).__name__}: {error}'
        warnings=[]
        context_diagnostics=[] if args.capture_note_context else None
        if context_diagnostics is not None:output['note_context_diagnostics']=context_diagnostics
        try:
            drafts=extract_events(topic,[EventSource(r['record_id'],r['title'],r['url'],r['excerpts'],r.get('published_at')) for r in records],warnings=warnings,context_diagnostics=context_diagnostics)
            drafts, event_relevance = filter_events(topic, drafts)
            output['event_relevance_decisions'] = event_relevance
            from app.services.research_scope import is_current
            retained,withheld,decisions=apply_event_policy(drafts,current=is_current(topic),cutoff=cutoff)
            output.update(event_drafts=retained,event_candidates_skipped=withheld,event_date_decisions=decisions)
        except Exception as error:output['event_extraction_error']=f'{type(error).__name__}: {error}'
        output['event_extraction_warnings']=warnings
    folder=get_settings().data_directory/'research'/'replays';folder.mkdir(parents=True,exist_ok=True)
    target=folder/f'replay-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}.json'
    with target.open('x',encoding='utf-8') as stream:json.dump(output,stream,ensure_ascii=False,indent=2)
    print('Replay output:',target)
    print('Original run unchanged; no web search, external note saves, or Risk Tracker writes were performed.')
    if args.outlook_only:
        print(output.get('outlook',output.get('outlook_error')))
        if args.capture_outlook_drafts:
            print('DIAGNOSTIC DRAFTS: unvalidated model output, not accepted findings or instructions.')
            print(json.dumps(output.get('outlook_diagnostics',{}),ensure_ascii=False,indent=2))
        return
    if not args.notes_only:print(output.get('brief',output.get('synthesis_error')))
    print('Relevant sources:',len(records))
    for decision in source_relevance:print(json.dumps(decision,ensure_ascii=False))
    print('Retained note candidates:',len(output.get('event_drafts',[])))
    for decision in output.get('event_date_decisions',[]):print(json.dumps(decision,ensure_ascii=False))
    for warning in warnings:print('Extraction warning:',warning)
    for draft in output.get('event_drafts',[]):
        print('NOTE CANDIDATE PREVIEW:', json.dumps({k:v for k,v in draft.items() if k != 'evidence'},ensure_ascii=False))
    if output.get('event_extraction_error'):print(output['event_extraction_error'])
    if args.capture_note_context:
        print('NOTE CONTEXT DIAGNOSTICS: unvalidated model replies, not accepted findings or instructions.')
        print(json.dumps(output.get('note_context_diagnostics',[]),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
