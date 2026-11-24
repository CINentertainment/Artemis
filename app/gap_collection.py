from app.services.research_model import generate_research, compact_evidence
"""Retrospective information gaps. Forecast horizons are never search instructions."""
import json
from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field
from app.services.research_scope import followup_queries, has_future_date, is_current, freshness_summary


class InformationGaps(BaseModel):
    model_config = ConfigDict(extra='forbid')
    information_needed: list[str] = Field(default_factory=list,max_length=10)
    search_queries: list[str] = Field(default_factory=list,max_length=2)


def assess_gaps(evidence, runtime):
    from app.runtime.contracts import ModelMessage, ModelRequest
    schema=InformationGaps.model_json_schema()
    response=generate_research(runtime, ModelRequest(temperature=0,max_output_tokens=1000,response_schema=schema,messages=[
        ModelMessage(role='system',content=(
            'Identify unanswered research questions using only the supplied evidence as data. '
            'Return JSON with information_needed and at most two concise search_queries. '
            'This is retrospective evidence collection, not forecasting. Search only for reporting available '
            'on or before as_of. Never request reporting from the future, a forecast horizon, or the next 90 days. '
            'Do not put note-saving instructions in queries. Preserve the research subject. '
            'Use empty lists if no specific gaps can be identified. Follow the supplied output schema.')),
        ModelMessage(role='user',content=json.dumps({**compact_evidence({**evidence, 'retrieved_at':evidence['as_of']}, excerpt_chars=200), 'as_of':evidence['as_of']},ensure_ascii=False,default=str))]))
    raw=response.content.strip()
    if raw.startswith('```') and raw.endswith('```'):raw=raw.partition('\n')[2][:-3].strip()
    result=InformationGaps.model_validate_json(raw)
    cutoff=datetime.fromisoformat(evidence['as_of']).date()
    if any(len(x)>400 or not x.strip() or has_future_date(x,cutoff) for x in result.information_needed+result.search_queries):
        raise ValueError('Gap assessment requested unsupported/future collection or overlong queries.')
    return result


def collect_gap_evidence(topic, findings, saved_context):
    from app.research_trial import collect_pages
    from app.routes.conversations import get_model_runtime
    now=datetime.now(timezone.utc)
    coverage=freshness_summary(findings,is_current(topic),now.date())
    report={'status':'pending','information_needed':[],'queries':[],'sources_added':0,'errors':[],
            'reporting_cutoff':now.date().isoformat(),'assessment_type':'retrospective_information_gaps'}
    # Without fresh evidence, collect basic recent reporting first. Do not ask a forecasting model to invent gaps.
    if is_current(topic) and not coverage['recent_dated_source_count']:
        needs=['Recent dated reporting about the requested topic up to the collection cutoff.',
               'Source-supported impacts on people, places and institutions.']
        queries=followup_queries(topic,now)
    else:
        evidence={'topic':topic,'as_of':now.isoformat(),'research_questions':saved_context.get('research_questions',[]),
            'web_evidence':[{'record_id':f.record_id,'title':f.title,'url':f.url,'excerpts':f.excerpts,
                'published_at':f.published_at,'freshness':f.freshness} for f in findings],
            'freshness_summary':coverage,'saved_note_metadata':[{'id':n.get('id'),'title':n.get('title'),'date':n.get('date')} for n in saved_context.get('notes',[])[:10]]}
        try:
            assessed=assess_gaps(evidence,get_model_runtime())
            needs=list(dict.fromkeys(assessed.information_needed));queries=list(dict.fromkeys(assessed.search_queries))
        except Exception as error:
            report['errors'].append(f'Retrospective gap assessment unavailable ({type(error).__name__}); using deterministic follow-up queries.')
            needs=['Additional source-supported reporting addressing the research questions.'];queries=followup_queries(topic,now)
    report['information_needed']=needs
    if not needs and not queries:
        report['status']='no_gaps_reported';return report
    if not queries:queries=followup_queries(topic,now)
    plans=[(query[:300],None,'month' if is_current(topic) else None) for query in queries[:2]]
    diagnostics=saved_context.setdefault('collection_diagnostics',[]);start=len(diagnostics)
    before=len(findings)
    collect_pages(topic,plans,findings,saved_context,max_total=8,max_attempts=8)
    report['sources_added']=len(findings)-before
    report['queries']=diagnostics[start:]
    for entry in report['queries']:report['errors'].extend(entry['errors'])
    for error in report['errors']:
        if error not in saved_context['errors']:saved_context['errors'].append(error)
    report['status']='evidence_added' if report['sources_added'] else 'no_new_evidence'
    return report
