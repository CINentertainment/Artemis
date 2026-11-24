"""Bound optional event fields to a core sentence and its explicit local antecedent."""
import re
from app.services.research_grounding import passages, normalized, locate

# Explicit backward references only; no cross-paragraph or cross-source joins.
LINKS = {
    'attack': r'\b(?:attacked|attack|struck|strike)\b',
    'strike': r'\b(?:struck|strike|attacked|attack)\b',
    'explosion': r'\b(?:explosion|exploded|blast)\b',
    'fire': r'\b(?:fire|burned|burning)\b',
    'evacuation': r'\b(?:evacuation|evacuated)\b',
}


def local_context(core, paragraph):
    units=passages(paragraph,400)
    matches=[i for i,value in enumerate(units) if normalized(value)==normalized(core)]
    if len(matches)!=1:return [core]
    index=matches[0]
    reference=re.match(r'^(?:The|This|That)\s+(attack|strike|explosion|fire|evacuation)\b',core,re.I)
    if not reference or index==0:return [core]
    previous=units[index-1]
    if re.search(LINKS[reference.group(1).lower()],previous,re.I):
        return [previous,core]
    return [core]


def bound_fact(fact, selected_id, evidence):
    if fact is None:return None
    entry=evidence[selected_id]
    allowed=local_context(entry['selection_text'],entry['excerpt'])
    matching=[]
    for cited in fact.excerpt_ids:
        other=evidence.get(cited)
        if not other:continue
        if other['record_id']!=entry['record_id'] or other['excerpt']!=entry['excerpt']:continue
        if any(locate(fact.text,unit) for unit in allowed):matching.append(cited)
    return fact.model_copy(update={'excerpt_ids':list(dict.fromkeys(matching))}) if matching else None


def span_candidates(context):
    """Small grammatical nominations, not NER or independent role verification."""
    from app.services.research_event_policy import DATE_PATTERN
    choices={name:[] for name in ('who','when','where')}
    word=r"[A-Za-z][A-Za-z’'\-]*"
    proper=r"[A-Z][A-Za-z’'\-]*(?:\s+[A-Z][A-Za-z’'\-]*){0,3}"
    actor=re.compile(r'(?:^|,\s+)(?P<text>'+word+r'(?:\s+'+word+r'){0,5}?)\s+(?:reportedly\s+)?(?:attacked|struck|bombed|shelled|evacuated|announced|reported|opened|closed)\b')
    places=[re.compile(r'\b(?:in|at|near)\s+(?P<text>'+proper+r'(?:,\s*'+proper+r')?)'),
            re.compile(r'\b(?:attacked|struck|bombed|shelled)\s+(?P<text>'+proper+r',\s*'+proper+r')')]
    def add(field,text,sentence):
        if len(choices[field])>=6 or text in [item['text'] for item in choices[field]]:return
        choices[field].append({'id':field+':'+str(len(choices[field])+1),'text':text,'sentence':sentence})
    for sentence in context:
        # Do not nominate fields from explicitly negated/conditional event clauses.
        if re.search(r'\b(?:not|never|denied|if|may|might|could|would)\b',sentence,re.I):continue
        for match in actor.finditer(sentence):
            text=match.group('text')
            if re.match(r'^(?:The|This|That)\s+(?:attack|strike|explosion|fire|evacuation)\b',text,re.I):continue
            if re.match(r'^(?:On|In|At|After|Before|Since|According)\b',text):continue
            add('who',text,sentence)
        for match in DATE_PATTERN.finditer(sentence):
            prefix=sentence[max(0,match.start()-45):match.start()]
            if re.search(r'\b(?:up to|since|as of|through|between|from|until|before|after|by)\s*$',prefix,re.I):continue
            add('when',match.group(),sentence)
        for pattern in places:
            for match in pattern.finditer(sentence):add('where',match.group('text'),sentence)
    return choices
