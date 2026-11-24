"""Narrative-only extraction and conservative named-identity eligibility.

Dates and unnamed infrastructure are note observations, never matrix identities.
All source matching establishes provenance, not truth or entity resolution.
"""
import re

POLICY_VERSION = 2
MONTH = r'(?:January|February|March|April|May|June|July|August|September|October|November|December)'
DATE = re.compile(r'\b(?:[12]\d{3}-\d{2}-\d{2}|\d{1,2}(?:st|nd|rd|th)?\s+'+MONTH+r'(?:\s+[12]\d{3})?|'+MONTH+r'\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+[12]\d{3})?|[12]\d{3})\b', re.I)
INTERNAL = re.compile(r'(?:artemis\s+(?:source\s+record|record|note|run)|(?:source|record|note|run|request|excerpt)[ _-]*id\b|\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b)',re.I)
META = re.compile(r'^\s*(?:\[\d+\](?:\s|$)|(?:URL|Title|Artemis source record|Artemis record|Classification|Discipline|Source|Reliability|Credibility|Tags|Publisher date|Published|Retrieved|Evidence cutoff|Event status)\s*:)',re.I)
GENERIC = re.compile(r'(?:(?:a|an|the|local|general|thermal|residential|regional|public|private|central|main|damaged|medical|healthcare|health|power|civilian|military)\s+)*(?:hospital|hospitals|clinic|clinics|healthcare cent(?:re|er)|health cent(?:re|er)|medical cent(?:re|er)|power plant|power plants|building|buildings|school|schools|facility|facilities|bridge|bridges|road|roads|water supply|power grid|healthcare facilities|health care facilities)',re.I)
INFRA = re.compile(r'\b(?:(?:a|an|the|local|general|thermal|residential|regional|public|private|damaged|medical|healthcare|civilian|military)\s+)*(?:healthcare cent(?:re|er)|health cent(?:re|er)|medical cent(?:re|er)|power plants?|hospitals?|clinics?|buildings?|schools?|healthcare facilities|health care facilities|power grid|water supply|bridges?|roads?)\b',re.I)
HEADLINE = re.compile(r'\b(?:evacuates|evacuated|reports|reported|says|announces|reveals|warns|damages|damaged|kills|killed|injures|injured|attacked)\b',re.I)
FIELD = re.compile(r'^\s*(Who|What|When|Where|Why|How)\s*:\s*(.*)$',re.I)


def narrative_spans(body):
    """Use archived source excerpts preferentially. Titles, URLs and ID lines never enter the prompt."""
    lines=body.splitlines()
    excerpts=[]; active=[]; collecting=False
    def finish():
        nonlocal active
        value='\n'.join(active).strip()
        if value:excerpts.append(value)
        active=[]
    for line in lines:
        match=re.match(r'^\s*Excerpt\s*:\s*(.*)$',line,re.I)
        if match:
            finish();collecting=True
            if match.group(1).strip():active.append(match.group(1).strip())
        elif META.match(line) or re.match(r'^\s*Sources\s*$',line,re.I):
            finish();collecting=False
        elif collecting:
            active.append(line)
    finish()
    if excerpts:
        return list(dict.fromkeys(excerpts))
    # If a note advertises source blocks but has no usable excerpt, do not fall back to its generated summary.
    if any(re.match(r'^\s*(?:Sources|Excerpt\s*:)',line,re.I) for line in lines):
        return []
    spans=[]
    for line in lines:
        if META.match(line) or INTERNAL.search(line) or re.match(r'^\s*(?:https?://|#|```)',line,re.I):continue
        match=FIELD.match(line)
        value=match.group(2).strip() if match else line.strip()
        if value and value.casefold() not in {'unknown','none','n/a'}:spans.append(value)
    return list(dict.fromkeys(spans))


def grounded(body, text, quote):
    return isinstance(text,str) and isinstance(quote,str) and bool(text.strip()) and bool(quote.strip()) and text in quote and any(quote in span for span in narrative_spans(body))


# Bounded population vocabulary, not a universal demonym detector.
POPULATIONS={'ukrainians','russians','palestinians','israelis','syrians','americans','britons','afghans','iraqis','iranians','sudanese','yemenis'}

def is_population_name(name):
    return isinstance(name,str) and (name.casefold() in POPULATIONS or bool(re.fullmatch(
        r"[A-Z][\w’'-]*(?:\s+[A-Z][\w’'-]*)*\s+(?:civilians|refugees|patients|children|residents|communities|workers|staff|population)",name)))


def classify_name(name, kind):
    if not isinstance(name,str) or not name.strip():return 'invalid'
    if INTERNAL.search(name):return 'metadata'
    if DATE.fullmatch(name.strip()):return 'date'
    if GENERIC.fullmatch(name.strip()):return 'infrastructure'
    if re.search(r'\s+(?:where|which|that)\s+',name,re.I) and GENERIC.fullmatch(re.split(r'\s+(?:where|which|that)\s+',name,1,flags=re.I)[0]):return 'infrastructure'
    if is_population_name(name) and kind!='population_group':return 'population_group_requires_group_type'
    if kind=='population_group' and not is_population_name(name):return 'population_group_not_established'
    if kind not in {'person','population_group','organization','place','event'}:return 'unsupported_kind'
    if len(name)>200 or '\n' in name or name!=name.strip():return 'invalid'
    if HEADLINE.search(name) or name.rstrip().endswith(('...','…')):return 'headline'
    if kind=='event' and not re.match(r'^(?:Operation\s|Battle of\s|Siege of\s|World War\s|[A-Z][\w-]*(?:\s+[A-Z][\w-]*)*\s+(?:Summit|Conference|Olympics|Games)\b)',name):return 'unnamed_event'
    if not any(c.isupper() for c in name):return 'not_named'
    if name.casefold() in {'unknown','none','n/a','the attack','the war','the conflict','civilians','patients','people','healthcare'}:return 'not_named'
    return 'entity'


def observations(body):
    """Literal mentions only; no year inference or association with a particular event."""
    result=[]; seen=set()
    for span in narrative_spans(body):
        for kind,pattern in (('date',DATE),('infrastructure',INFRA)):
            for match in pattern.finditer(span):
                text=match.group()
                # Complete original sentence(s), never a character window through a word.
                from app.services.intelligence_presentation import sentence_spans
                containing=[sentence for a,b,sentence in sentence_spans(span) if a<match.end() and b>match.start()]
                quote=' '.join(containing) if containing else span
                # Joining across unusual whitespace must not rewrite an evidence quote.
                if quote not in span:quote=span
                key=(kind,text,quote)
                if key in seen:continue
                seen.add(key);result.append(dict(kind=kind,text=text,quote=quote))
                if len(result)>=40:return result
    return result


def add_observation(data,note_id,revision_id,kind,text,quote,review_status=None,origin='source_mention'):
    from app.services.intelligence_workspace import require,uid,snapshot
    snap=snapshot(data,note_id)
    require(snap['id']==revision_id,'Note changed during observation selection.')
    require(kind in {'date','infrastructure'} and grounded(snap['record']['body'],text,quote),'Observation must come from narrative evidence.')
    rows=data.setdefault('observations',[])
    existing=next((o for o in rows if o['note_id']==note_id and o['revision_id']==revision_id and o['kind']==kind and o['text']==text),None)
    if existing is None:
        existing=dict(id=uid(),note_id=note_id,revision_id=revision_id,kind=kind,text=text,quote=quote,origin=origin,review_status=review_status or 'pending')
        rows.append(existing)
    quotes=existing.setdefault('quotes',[existing['quote']])
    if quote not in quotes and len(quotes)<10:quotes.append(quote)
    return existing


def review_legacy(data,note_id):
    """Keep original rows/decisions, flag ineligible model mentions, and copy eligible observations."""
    from app.services.intelligence_workspace import snapshot
    snap=snapshot(data,note_id);body=snap['record']['body'];excluded=0
    for entity in data['entities'].values():
        for mention in entity['mentions']:
            if mention['revision_id']!=snap['id'] or mention['origin']!='model_candidate':continue
            literal=mention.get('name',entity['name'])
            classification=classify_name(literal,entity['kind'])
            reason=classification if classification!='entity' else ('non_narrative' if not grounded(body,literal,mention['quote']) else None)
            if reason:
                mention['policy_exclusion']=reason;excluded+=1
                if classification in {'date','infrastructure'} and grounded(body,literal,mention['quote']):
                    add_observation(data,note_id,snap['id'],classification,literal,mention['quote'],mention.get('review_status'),origin='reclassified_candidate')
    return excluded
