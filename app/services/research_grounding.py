"""Extractive provenance checks; no claim of semantic entailment or source truth."""
import re
import unicodedata


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC',text).replace('’',"'").replace('‘',"'").replace('“','"').replace('”','"').split()).casefold()


def passages(text, limit=600):
    # Keep quoted commentary together with the speaker in the original paragraph.
    text = text.strip()
    quoted = bool(re.search(r'[“"]', text))
    attributed = bool(re.search(r'\b(?:says?|said|according to|stated|reported)\b', text, re.I))
    if quoted and attributed and len(text) <= limit:
        return [text]
    units = re.split(r'(?<=[.!?])\s+(?=[A-Z“"‘])', text)
    kept = []
    for unit in units:
        unit = unit.strip()
        if not unit or len(unit) > limit:
            continue
        # A long quoted paragraph must not shed its attribution when shortened.
        if quoted and attributed and not re.search(r'\b(?:says?|said|according to|stated|reported)\b', unit, re.I):
            continue
        # Quoted statements without any speaker attribution are not selected alone.
        if re.search(r'^[“"]', unit) and not attributed:
            continue
        kept.append(unit)
    return kept


def locate(text, excerpt, *, whole=False):
    target=normalized(text)
    if not target:return False
    if whole:return any(target==normalized(unit) for unit in passages(excerpt,400))
    return target in normalized(excerpt)


def ground_field(name, fact, evidence):
    if not fact:return None,'Unknown in draft.'
    text=fact.get('text','')
    if not isinstance(text,str) or len(text)>400:return None,'Field is empty or exceeds the extractive field limit.'
    ids=[i for i in dict.fromkeys(fact.get('excerpt_ids',[])) if i in evidence and locate(text,evidence[i]['excerpt'],whole=name=='what')]
    if not ids:return None,'Field text is not a matching cited source span' + (' or complete source sentence.' if name=='what' else '.')
    # Optional relationship fields need an explicit relationship marker in the span.
    if name=='why' and not re.search(r'\b(?:because|in order to|so that|aims? to|intended to|purpose|due to)\b',text,re.I):
        return None,'No explicit reason/purpose marker in the cited span.'
    if name=='how' and not re.search(r'\b(?:using|through|via|by|with)\b',text,re.I):
        return None,'No explicit method marker in the cited span.'
    if name=='when':
        if re.search(r'\b(?:up to|since|as of|through|between|from)\b',text,re.I):return None,'Coverage period is not a point event date.'
        # Do not relabel a cumulative-reporting cutoff as the date of a discrete event.
        for i in ids:
            source=normalized(evidence[i]['excerpt']);position=source.find(normalized(text))
            if re.search(r'\b(?:up to|since|as of|through|between|from)\s*$',source[max(0,position-30):position]):
                return None,'Date is a coverage boundary in cited context, not a point event date.'
    return {'text':text,'excerpt_ids':ids},'Exact cited source sentence.' if name=='what' else 'Exact cited source span; role classification remains model-assisted.'


def ground_event(event):
    result={**event};decisions=[]
    for name in ('who','what','when','where','why','how'):
        original=event.get(name)
        fact,reason=ground_field(name,original,event.get('evidence',{}))
        result[name]=fact
        if original:decisions.append({'field':name,'accepted':fact is not None,'original_text':original.get('text'),'reason':reason})
    if result.get('what') is None:return None,decisions
    # Titles and categorical status must not introduce claims absent from the excerpt.
    text=result['what']['text']
    result['title']='Source reporting: '+(text if len(text)<=210 else text[:210].rsplit(' ',1)[0]+'…')
    result['status']='reported'
    return result,decisions
