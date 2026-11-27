"""Source-driven collected-information briefs. No model memories or template claims."""
import json
import re
from collections import Counter
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
from app.services.event_geography import fields
from app.services.note_headlines import note_headline


def is_report_request(text):
    value=text.casefold()
    if re.search(r'\b(?:do not|don.t|never|cancel)\b.{0,30}\b(?:create|generate|export|build|make)\b',value):return False
    return bool(re.search(r'\b(?:daily intelligence brief|daily brief|24.hour brief)\b',value)or(re.search(r'\bpdf\b',value)and re.search(r'\b(?:notes?|research|information|brief|reports?|summary)\b',value)and re.search(r'\b(?:build|create|generate|export|compile|prepare|produce|make)\b',value)))

def utc(value,db=False):
    if isinstance(value,str):
        try:value=datetime.fromisoformat(value.replace('Z','+00:00'))
        except ValueError:return None
    if not isinstance(value,datetime):return None
    if value.tzinfo is None:
        if not db:return None
        value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def windowed(value,start,end):return value is not None and start<=value<=end

def all_notes(client,warnings):
    # Discover pagination rather than guessing unsupported query parameters.
    try:
        schema=client.read_json('/openapi.json');params=schema.get('paths',{}).get('/notes',{}).get('get',{}).get('parameters',[]);names={p['name']:p for p in params if p.get('in')=='query'}
    except Exception:names={}
    if not {'limit','offset'}.issubset(names):
        rows=client.list_notes();warnings.append('Intel Notes coverage is the returned API collection; no supported limit/offset pagination was advertised. Referenced run note IDs were fetched individually as needed.');return rows
    size=max(1,min(500,int(names['limit'].get('schema',{}).get('maximum')or 500)));rows=[];seen=set()
    for offset in range(0,100000,size):
        batch=client.read_json('/notes',params={'limit':size,'offset':offset})
        if not isinstance(batch,list):raise ValueError('Intel Notes pagination returned an invalid collection.')
        added=0
        for n in batch:
            key=n.get('id')
            if key not in seen:seen.add(key);rows.append(n);added+=1
        if len(batch)<size:return rows
        if not added:warnings.append('Intel Notes pagination stopped making progress; coverage is incomplete.');return rows
    warnings.append('Intel Notes retrieval reached the 100,000-record bound; coverage is incomplete.');return rows

def assemble(notes,runs,records,markets,start,end,warnings=None):
    warnings=list(warnings or[]);refs={};source_topics={};note_topics={};run_items=[]
    for run in runs:
        completed=utc(run.get('finished_at'),db=True)
        if not windowed(completed,start,end):continue
        result=run.get('result')or{};topic=run.get('topic')or'Unassigned topic';run_items.append(run)
        for n in result.get('event_notes',[]):
            nid=n.get('intel_note_id')
            if type(nid)is int and nid>0 and not n.get('error'):
                refs.setdefault(nid,[]).append(completed);note_topics.setdefault(nid,set()).add(topic)
        for s in result.get('sources',[]):
            if s.get('record_id'):source_topics.setdefault(str(s['record_id']),set()).add(topic)
    entries=[];uncertain=[]
    for note in notes:
        nid=note.get('id')
        if type(nid)is not int:continue
        created=utc(note.get('created_at'));ref=min(refs[nid])if nid in refs else None
        if windowed(created,start,end):stamp=created;basis='Intel Notes creation timestamp';lifecycle='Note created during the window'
        elif ref:stamp=ref;basis='Research run completion (collection-time proxy)';lifecycle='Existing note referenced during the window'if created else'Creation time unavailable; successful saved-note reference during the window'
        else:
            if created is None:uncertain.append(dict(id=nid,title=note.get('title')or'Untitled',reason='No timezone-qualified creation timestamp or successful in-window run reference.'))
            continue
        body=note.get('body')or'';f=fields(body)
        entries.append(dict(key='note-'+str(nid),kind='Intel Note',reference='Intel Note '+str(nid),title=note.get('title')or'Untitled',collected_at=stamp,time_basis=basis,lifecycle=lifecycle,topic='; '.join(sorted(note_topics.get(nid,{'Unassigned topic'}))),location=f.get('where')or note.get('location')or'Not established',event_date=f.get('when','Not established'),classification=note.get('classification')or'Not recorded',bullets=[k.capitalize()+': '+v for k,v in f.items()]or['Saved note text is reproduced below.'],body=body))
    for record in records:
        stamp=utc(record.get('retrieved_at'),db=True);referenced=record['id']in source_topics
        if not windowed(stamp,start,end)and not referenced:continue
        if not windowed(stamp,start,end):
            related=[utc(r.get('finished_at'),db=True)for r in run_items if any(str(s.get('record_id'))==record['id']for s in(r.get('result')or{}).get('sources',[]))];stamp=min(t for t in related if t)
            basis='Research run completion; previously stored source record reused'
        else:basis='Artemis source-record retrieval timestamp (database UTC)'
        entries.append(dict(key='source-'+record['id'],kind='Source record',reference='Artemis source '+record['id'],title=record.get('title')or'Untitled source',collected_at=stamp,time_basis=basis,lifecycle='Collected or reused source material; not an independently confirmed event',topic='; '.join(sorted(source_topics.get(record['id'],{'Collected source material'}))),location='Not established for this source record',event_date='Not inferred from publication metadata',classification='Not recorded',bullets=['Source locator: '+(record.get('source_locator')or'Not recorded')],body=record.get('body')or''))
    for item in markets:
        r=item.get('record')or{};stamp=utc(r.get('created_at'))
        if not windowed(stamp,start,end):continue
        entries.append(dict(key='market-'+item['id'],kind='Market outlook',reference='Market Lens research '+item['id'],title=r.get('symbol','')+' - '+r.get('name',''),collected_at=stamp,time_basis='Market research creation timestamp',lifecycle='Conditional model analysis; not an observed future event',topic='Markets / '+r.get('asset_type','Instrument'),location='Not geographically assigned',event_date='Outlook horizon: '+r.get('horizon_end','Unknown'),classification='Not recorded',bullets=['Direction: '+r.get('direction','Unknown'),'Confidence: '+r.get('confidence','Unknown'),'Reasoning (analysis): '+r.get('reasoning','')],body=json.dumps(item,ensure_ascii=False,indent=2)))
    entries.sort(key=lambda e:(e['collected_at'],e['reference']))
    if uncertain:warnings.append(str(len(uncertain))+' returned notes could not be assigned to the requested collection window. Their IDs and titles appear in the coverage register; no collection time was invented.')
    return dict(start=start,end=end,entries=entries,uncertain=uncertain,runs=run_items,warnings=warnings)

REPORT_SOURCES = ('notes', 'risk_tracker', 'market_lens')

def selected_sources(sources=None):
    values=list(dict.fromkeys(REPORT_SOURCES if sources is None else sources))
    if not values or any(v not in REPORT_SOURCES for v in values):
        raise ValueError('Choose at least one supported report source.')
    return values

def risk_entries(cases,start,end):
    entries=[]
    for case in cases:
        stamp=utc(case.get('updated_at'),db=True)or utc(case.get('created_at'),db=True)
        if not windowed(stamp,start,end):continue
        ref=case.get('case_number')or str(case.get('id','Unknown'))
        entries.append(dict(kind='Risk Tracker',reference='Risk Tracker case '+ref,title=case.get('title')or'Untitled case',collected_at=stamp,time_basis='Case last updated; not an incident date',body=case.get('summary')or'',classification='Not recorded',bullets=['Case status: '+str(case.get('status','Unknown')),'Priority: '+str(case.get('priority','Unknown')),'Recorded risk level: '+str(case.get('risk_level','Unknown'))]))
    return entries

def collect(database,hours=24,end=None,sources=None):
    """Read only the selected services; retain the existing note-window policy."""
    chosen=selected_sources(sources);end=end or datetime.now(timezone.utc);start=end-timedelta(hours=hours)
    notes=[];runs=[];markets=[];warnings=[]
    if 'notes' in chosen:
        from sqlalchemy import select
        from app.research_models import ResearchRun,ResearchRequisition
        from app.connectors.intel_notes import IntelNotesConnector
        from app.config import get_settings
        rows=database.scalars(select(ResearchRun).where(ResearchRun.finished_at>=start.replace(tzinfo=None),ResearchRun.finished_at<=end.replace(tzinfo=None))).all()
        for row in rows:
            req=database.get(ResearchRequisition,row.requisition_id)
            runs.append(dict(id=row.id,topic=req.topic if req else'Unassigned',status=row.status,finished_at=row.finished_at,result=json.loads(row.result_json)if row.result_json else{}))
        try:
            with IntelNotesConnector(base_url=str(get_settings().intel_notes_url))as client:
                indexed={n['id']:n for n in all_notes(client,warnings)if type(n.get('id'))is int}
                ids={n['intel_note_id']for r in runs for n in(r['result'].get('event_notes')or[])if type(n.get('intel_note_id'))is int and not n.get('error')}
                for nid in ids|set(indexed):
                    if nid not in indexed or 'body'not in indexed[nid]:
                        try:indexed[nid]=client.get_note(nid)
                        except Exception:warnings.append('Intel Note '+str(nid)+' could not be retrieved.')
                notes=list(indexed.values())
        except Exception as e:warnings.append('Intel Notes unavailable: '+type(e).__name__+'.')
    if 'market_lens' in chosen:
        try:
            from app.services.market_automation import connector,call
            with connector()as client:markets=call(client,'/research',params={'limit':300})
            if len(markets)>=300:warnings.append('Market Lens returns at most 300 research records; coverage may be incomplete.')
        except Exception as e:warnings.append('Market Lens unavailable: '+type(e).__name__+'.')
    data=assemble(notes,runs,[],markets,start,end,warnings)
    if 'risk_tracker' in chosen:
        try:
            from app.services.risk_map import tracker
            with tracker(write=False)as client:
                client.authenticate();cases=client.list_cases(include_archived=True)
            data['entries'].extend(risk_entries(cases,start,end))
        except Exception as e:data['warnings'].append('Risk Tracker unavailable: '+type(e).__name__+'.')
    for source,kind in [('notes','Intel Note'),('risk_tracker','Risk Tracker'),('market_lens','Market outlook')]:
        if source in chosen and not any(e['kind']==kind for e in data['entries']):data['warnings'].append('No '+kind+' records matched the selected reporting window; current coverage is not established for this source.')
    data['entries'].sort(key=lambda e:(e['collected_at'],e['reference']))
    data['sources']=chosen
    return data

def reader_text(value):
    """Remove application wrappers, preserving the stored claim and its uncertainty."""
    value=re.sub(r'\[\d+\]', '', str(value or ''))
    value=re.sub(r'^(?:(?:Undated reporting|Partially dated reporting|Source reporting)\s*:\s*)+', '',value,flags=re.I)
    value=re.sub(r'^Source reporting;[^.]*\.\s*','',value,flags=re.I)
    return re.sub(r'\s+',' ',value).strip()

def reader_note(entry):
    f=fields(entry['body']);items=[]
    for key in ('what','who','when','where','why','how'):
        text=reader_text(f.get(key,''))
        if not text or text.lower() in ('unknown','not established','none','n/a'):continue
        label={'what':'','who':'Reported actor: ','when':'Source-stated date: ','where':'Location: ','why':'Reported purpose: ','how':'Reported method: '}[key]
        if key=='what' or not any(text in old for old in items):items.append(label+text)
    if not items:
        body=re.split(r'\nSources\b',entry['body'],flags=re.I)[0]
        items=[reader_text(body)or reader_text(entry['title'])]
    return items

def source_paragraphs(body):
    blocks=[]
    for block in re.split(r'\n\s*\n',body):
        block=block.strip()
        if not block or block.startswith(('Source URL:','Publisher date metadata','Artemis source record:','{','[')):continue
        text=reader_text(block)
        if text and not re.fullmatch(r'https?://\S+',text):blocks.append(text)
    return blocks

def build_pdf(data,target,zone='America/Chicago'):
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import BaseDocTemplate,PageTemplate,Frame,Paragraph,Spacer,PageBreak,KeepTogether
    from reportlab.platypus.tableofcontents import TableOfContents
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    fonts=Path(__file__).resolve().parents[1]/'data/report-fonts'
    for name,file in [('Brief','DejaVuSans.ttf'),('BriefBold','DejaVuSans-Bold.ttf')]:
        if name not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(name,str(fonts/file)))
    pdfmetrics.registerFontFamily('Brief',normal='Brief',bold='BriefBold',italic='Brief',boldItalic='BriefBold')
    tz=ZoneInfo(zone);fmt=lambda t:t.astimezone(tz).strftime('%d %b %Y, %H:%M %Z')
    styles=getSampleStyleSheet()
    for s in styles.byName.values():s.fontName='Brief';s.fontSize=10;s.leading=15;s.spaceAfter=8
    for n,size in [('Title',32),('Heading1',21),('Heading2',12)]:
        styles[n].fontName='BriefBold';styles[n].fontSize=size;styles[n].leading=size*1.3;styles[n].textColor=colors.HexColor('#38244e');styles[n].spaceBefore=14;styles[n].spaceAfter=10
    styles.add(ParagraphStyle(name='Meta',fontName='Brief',fontSize=8,leading=12,textColor=colors.HexColor('#62616a'),spaceAfter=9))
    styles.add(ParagraphStyle(name='ReaderBullet',parent=styles['Normal'],leftIndent=12,firstLineIndent=-9,spaceAfter=7))
    def p(text,style='Normal'):
        text=''.join(c for c in str(text) if ord(c)>=32 or c=='\n')
        return Paragraph(escape(text).replace('\n','<br/>'),styles[style])
    class Doc(BaseDocTemplate):
        def afterFlowable(self,f):
            if getattr(f,'anchor',None):self.canv.bookmarkPage(f.anchor);self.notify('TOCEntry',(0,f.getPlainText(),self.page,f.anchor))
    doc=Doc(str(target),pagesize=letter,leftMargin=50,rightMargin=50,topMargin=55,bottomMargin=50,title='Artemis | Daily Intelligence Brief',author='Artemis')
    def page(c,d):
        c.saveState();c.setStrokeColor(colors.HexColor('#785599'));c.line(50,758,562,758);c.setFont('Brief',8);c.setFillColor(colors.HexColor('#68616e'));c.drawString(50,27,'ARTEMIS  /  DAILY INTELLIGENCE BRIEF');c.drawRightString(562,27,str(d.page));c.restoreState()
    doc.addPageTemplates(PageTemplate(id='brief',frames=[Frame(50,50,512,687,leftPadding=0,rightPadding=0,topPadding=0,bottomPadding=0)],onPage=page))
    story=[]
    def h(text,key):q=p(text,'Heading1');q.anchor=key;story.append(q)
    notes=[e for e in data['entries']if e['kind']=='Intel Note'];sources=[e for e in data['entries']if e['kind']=='Source record'];markets=[e for e in data['entries']if e['kind']=='Market outlook'];risks=[e for e in data['entries']if e['kind']=='Risk Tracker']
    refs=[];seen_urls=set()
    def reference(title,url,note=None):
        if url in seen_urls:return
        seen_urls.add(url);refs.append((title,url,note))
    story += [Spacer(1,95),p('ARTEMIS','Title'),p('Daily Intelligence\nBrief','Title'),Spacer(1,24),p(fmt(data['start'])+'\nto '+fmt(data['end'])),Spacer(1,30),p('Selected sources: '+', '.join({'notes':'Intel Notes','risk_tracker':'Risk Tracker','market_lens':'Market Lens'}[v]for v in data.get('sources',REPORT_SOURCES))),p('A concise digest of saved reporting, with collection times and references to the underlying notes. Collection during this period does not mean an event occurred during it.','Meta'),p('Source statements and stored analysis have not been independently verified.','Meta'),PageBreak(),p('Contents','Heading1')]
    toc=TableOfContents();toc.levelStyles=[ParagraphStyle(name='ContentsRow',fontName='Brief',fontSize=11,leading=20,spaceBefore=8)];story += [toc,PageBreak()];h('Summary of events','summary')
    story.append(p('The following highlights use the wording retained in the saved notes. Historical observations, ongoing conditions and appeals are not presented as newly occurring incidents.','Meta'))
    if not notes:story.append(p('No chronological Intel Note entries were selected for this window. Reviewed event notices may reference previously saved notes; case updates and market assessments follow when selected.'))
    seen=set()
    for e in notes:
        claim=reader_note(e)[0]
        if claim not in seen:story.append(p('• '+claim+' ('+e['reference']+')','ReaderBullet'));seen.add(claim)
    if data.get('event_updates'):
        story.append(PageBreak());h('Event tracking updates','event-tracking')
        story.append(p('Reviewed dossier notices since '+fmt(data['event_baseline'])+'. Coverage is limited to this brief’s selected window and at most one hundred reviewed notices. Review records an analyst judgment; it does not independently verify a source claim.','Meta'))
        for update in data['event_updates']:
            story.append(p(update['event'],'Heading2'))
            story.append(p(update['title']+' · Intel Note '+str(update['note_id'])+' · '+update['classification'],'Meta'))
            if update.get('before'):story.append(p('Earlier saved wording: '+update['before'],'ReaderBullet'))
            story.append(p('• '+update['quote'],'ReaderBullet'))
            story.append(p(update['reason'],'Meta'))
            if update.get('when_literal'):story.append(p('Saved event-date wording: '+update['when_literal']+'; not inferred from collection time.','Meta'))
            for url in update.get('source_urls',[]):story.append(p(url,'Meta'))
    if data.get('missions'):
        story.append(PageBreak());h('Mission briefing','mission-briefing')
        story.append(p('Current mission work status at report generation. This section is not a historical reconstruction of the collection window or a statement of newly occurring events. Reported findings and analytical assessments are presented separately.','Meta'))
        for mission in data['missions']:
            story.append(p(mission['title'],'Heading2'))
            story.append(p('Objective: '+mission['objective']))
            story.append(p('Mission is '+mission['state']+'. The following saved reporting and open questions inform the next research cycle.'))
            for highlight in mission['highlights']:
                story.append(p('• '+highlight['quote'],'ReaderBullet'))
                story.append(p('Intel Note '+str(highlight['note_id'])+' · '+highlight['classification']+' · Copied saved reporting; not independently verified.','Meta'))
            if mission['note_ids']:story.append(p('Saved note references in the latest run receipts: '+', '.join('Intel Note '+str(n) for n in mission['note_ids']),'Meta'))
            for question in mission['next_questions']:story.append(p('• Open work question: '+question,'ReaderBullet'))
            for gap in mission['unknowns']:story.append(p('• Evidence limit: '+gap,'ReaderBullet'))
            if mission['symbols']:story.append(p('Linked market symbols (monitored separately): '+', '.join(mission['symbols']),'Meta'))
    if data.get('assessments'):
        story.append(PageBreak());h('Analytical assessments','assessments')
        story.append(p('Reviewed analytical judgments based on saved reporting; reasoning may be AI-assisted. These are distinct from reported events. Source accuracy and causal support are not independently verified. Confidence describes an analyst judgment, not an event probability.','Meta'))
        for assessment in data['assessments']:
            a=assessment['analysis'];evidence={e['id']:e for e in assessment['evidence']}
            story.append(p(assessment['title'],'Heading2'))
            if assessment.get('model'):story.append(p('AI-assisted draft reviewed by an analyst. Citation membership was checked; the reasoning still requires verification.','Meta'))
            story.append(p('Assessment recorded '+fmt(utc(assessment['created_at']))+'; not an event date.','Meta'))
            markings=sorted({e['classification'] for e in evidence.values()})
            story.append(p('Source markings: '+', '.join(markings),'Meta'))
            def reasoning(label,claim):
                story.append(p(label+': '+claim['text']))
                linked=[evidence[k] for k in dict.fromkeys(claim['evidence_ids'])]
                story.append(p('Related notes: '+', '.join('Intel Note '+str(e['note_id']) for e in linked),'Meta'))
            reasoning('Assessment',a['judgment'])
            for claim in a['alternatives']:reasoning('Competing explanation',claim)
            for claim in a['weakening']:reasoning('What would weaken the assessment',claim)
            for label,key in [('Assumption','assumptions'),('Unknown','unknowns')]:
                for value in a[key]:story.append(p('• '+label+': '+value,'ReaderBullet'))
            story.append(p('Confidence: '+a['confidence']+'. '+a['confidence_reason']))
            story.append(p('Supporting reporting — copied saved wording','Heading2'))
            used={k for c in [a['judgment'],*a['alternatives'],*a['weakening']] for k in c['evidence_ids']}
            for key,e in evidence.items():
                if key not in used:continue
                story.append(p('Intel Note '+str(e['note_id'])+': '+e['quote'],'ReaderBullet'))
                if e.get('when_literal'):story.append(p('Source-stated date: '+e['when_literal']+'; unresolved components are not inferred.','Meta'))
                for url in e['source_urls']:reference('Assessment source',url,'Intel Note '+str(e['note_id']))
            for judgment in assessment.get('provenance',[]):
                left=evidence[judgment['left']];right=evidence[judgment['right']]
                story.append(p('Source relationship judgment (Intel Notes '+str(left['note_id'])+' and '+str(right['note_id'])+'): '+judgment['relation'].replace('_',' ')+'. '+judgment['reason'],'Meta'))
    if notes:
        story.append(PageBreak());h('Notes by collection time','notes')
        for e in notes:
            note_fields=fields(e['body']);title=note_headline(note_fields.get('what') or reader_text(e['title']),note_fields.get('where'));story.append(p(title,'Heading2'));story.append(p(e['reference'],'Meta'))
            timing='Collected '+fmt(e['collected_at'])
            if 'proxy' in e.get('time_basis','').lower():timing='Referenced by research completed '+fmt(e['collected_at'])+'; note creation time not established'
            elif 'Existing note' in e.get('lifecycle',''):timing='Existing note referenced '+fmt(e['collected_at'])
            story.append(p(timing,'Meta'))
            classification=e.get('classification','Not recorded')
            story.append(p('Classification: '+classification,'Meta'))
            for item in reader_note(e):story.append(p('• '+item,'ReaderBullet'))
            # Readable source references only; internal source-record IDs are excluded.
            for match in re.finditer(r'\[\d+\]\s*([^\n]+)\nURL:\s*(https?://\S+)',e['body']):reference(match[1],match[2],e['reference'])
    if sources:
        story.append(PageBreak());h('Additional collected reporting','sources')
        story.append(p('Selected saved excerpts, presented separately from event notes. These may repeat reporting above or provide background; collection alone does not establish recency or accuracy. The complete source text remains in Artemis.','Meta'))
        for e in sources:
            blocks=source_paragraphs(e['body']);urls=re.findall(r'https?://[^\s<>]+',e['body']);url=next((b.split(': ',1)[1]for b in e.get('bullets',[])if b.startswith('Source locator: https://')),urls[0]if urls else'')
            title=reader_text(e['title']);story.append(p(title,'Heading2'));story.append(p('Collected '+fmt(e['collected_at']),'Meta'))
            if 'reused' in e.get('time_basis',''):story.append(p('Previously stored reporting referenced again during this period.','Meta'))
            for block in blocks[:2]:story.append(p('• '+block,'ReaderBullet'))
            if not blocks:story.append(p('No readable narrative excerpt was available in the saved record.','Meta'))
            if url:reference(title,url);story.append(p(url,'Meta'))
    if risks:
        story.append(PageBreak());h('Risk Tracker case updates','risk-tracker')
        story.append(p('Cases last updated during the reporting period. Case updates are not incident dates; risk ratings are stored assessments, not independently verified findings.','Meta'))
        for e in risks:
            story.append(p(e['title'],'Heading2'));story.append(p(e['reference']+' | Updated '+fmt(e['collected_at']),'Meta'))
            if e['body']:story.append(p('• '+e['body'],'ReaderBullet'))
            for line in e['bullets']:story.append(p(line,'Meta'))
    if markets:
        story.append(PageBreak());h('Market research outlook','markets')
        story.append(p('Research hypotheses, separate from event reporting. Directional views are low confidence and uncalibrated; they are not guaranteed returns. No trading activity is implied.','Meta'))
        for e in markets:
            try:r=json.loads(e['body']).get('record',{})
            except (ValueError,TypeError):r={}
            direction={'bullish':'Potential rise','bearish':'Potential fall','neutral':'No clear directional bias','insufficient_evidence':'Insufficient evidence for a directional assessment'}.get(r.get('direction'),r.get('direction','Assessment unavailable'))
            story.append(p(e['title'],'Heading2'));story.append(p('Collected '+fmt(e['collected_at'])+' | Horizon: '+str(r.get('horizon_end','Not established')),'Meta'));story.append(p(direction))
            if r.get('reasoning'):story.append(p('• '+reader_text(r['reasoning']),'ReaderBullet'))
            for label,key in [('Assumption','assumptions'),('What would weaken this view','invalidation')]:
                values=r.get(key,[]);values=[values]if isinstance(values,str)else values
                for v in values or []:story.append(p('• '+label+': '+str(v),'ReaderBullet'))
            for ev in r.get('evidence',[])or[]:
                if isinstance(ev,dict):
                    url=ev.get('url')or ev.get('source_url')
                    if url:reference(ev.get('title')or e['title'],url)
    story.append(PageBreak());h('References and coverage','references')
    for i,(title,url,note)in enumerate(refs,1):
        story.append(p(str(i)+'. '+title+(' | '+note if note else''),'Heading2'));story.append(p(url,'Meta'))
    story.append(p('About this brief','Heading2'));story.append(p('This is an extractive reading edition. All selected notes are represented; source-only records are condensed to their first two saved narrative passages. Repeated reporting does not establish independent corroboration. Material source figures are preserved; database identifiers, raw payloads and processing diagnostics are omitted. Full records remain available in Artemis.'))
    if data.get('uncertain'):story.append(p(str(len(data['uncertain']))+' notes could not be assigned reliably to this collection period and are excluded.','Meta'))
    if data.get('warnings'):story.append(p('Coverage is qualified: one or more retrieval limits or unavailable services were reported. Consult the report status in Artemis before treating this as complete coverage.','Meta'))
    doc.multiBuild(story)

def generate(database,hours=24,zone='America/Chicago',sources=None,*,report_id=None,end=None):
    from app.config import get_settings
    target=Path(get_settings().data_directory)/'reports';target.mkdir(parents=True,exist_ok=True);identity=str(__import__('uuid').UUID(str(report_id)))if report_id else str(uuid4());pdf=target/(identity+'.pdf');tmp=target/(identity+'.'+str(uuid4())+'.building.pdf')
    data=collect(database,hours=hours,sources=sources,end=end)
    if 'notes'in data['sources']:
        try:
            from app.services.event_tracking import previous_brief_end,brief_updates
            previous=previous_brief_end(target,data['end'],identity)
            data['event_baseline']=max(data['start'],previous)if previous else data['start']
            data['event_updates']=brief_updates(data['event_baseline'],data['end'])
        except Exception:data['warnings'].append('Reviewed event dossier updates could not be retrieved.')
    if 'notes' in data['sources']:
        try:
            from app.services.evidence_assessment import brief_assessments
            data['assessments']=brief_assessments(data['start'],data['end'])
        except Exception:data['warnings'].append('Analytical assessments could not be retrieved.')
    if 'notes' in data['sources']:
        try:
            from app.services.mission_control import brief_missions
            data['missions']=brief_missions(data['start'],data['end'])
        except Exception:data['warnings'].append('Mission work summaries could not be retrieved.')
    try:build_pdf(data,tmp,zone);tmp.replace(pdf)
    finally:tmp.unlink(missing_ok=True)
    manifest=dict(id=identity,sources=data['sources'],hours=hours,timezone=zone,start=data['start'].isoformat(),end=data['end'].isoformat(),entries=len(data['entries']),uncertain_notes=len(data['uncertain']),warnings=data['warnings'],created_at=datetime.now(timezone.utc).isoformat())
    meta_tmp=target/(identity+'.'+str(uuid4())+'.building.tmp')
    try:
        meta_tmp.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8');meta_tmp.replace(target/(identity+'.json'))
    finally:meta_tmp.unlink(missing_ok=True)
    return manifest

def run_report_command(database,text):
    if not is_report_request(text):return None
    m=re.search(r'\b(?:past|last)\s+(\d+)\s+hours?\b',text,re.I);hours=int(m[1])if m else 24
    if not 1<=hours<=168:return 'Choose a reporting window from 1 to 168 hours.'
    try:
        result=generate(database,hours)
        return 'Created the saved-record PDF for '+result['start']+' through '+result['end']+'.\n\n[Download the PDF](http://127.0.0.1:8200/reports/'+result['id']+'/pdf)\n\n'+str(result['entries'])+' entries included. '+str(result['uncertain_notes'])+' notes have unresolved collection times. '+('Coverage qualifications are included in the PDF: '+'; '.join(result['warnings'])if result['warnings']else'No retrieval gaps were reported.')
    except ImportError:return 'PDF generation needs ReportLab. Install the daily-report update dependency in the Artemis virtual environment; no template or invented notes were substituted.'
    except Exception as e:return 'The saved-record report could not be completed ('+type(e).__name__+'). No placeholder PDF or model-memory summary was substituted. Use the Reports page to retry after checking the connected services.'
