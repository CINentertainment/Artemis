"""Deterministic topic anchors; conservative relevance checks, not truth validation."""
import math
import re
from app.services.research_scope import clean_topic, search_base

STOP = set('a an the and or for of in on to with from about at by as is are was were be been this that these those how what who why when where which research investigate information overview latest current recent today ongoing news update updates development developments affecting affected impact impacts effect effects source sources report reports reporting analysis assess assessment study studies finding findings preserve attribution uncertainty supported save notes intel leaving unknown event dates blank please one two three publicly traded company companies'.split())
ALIASES = {
    'ukraine': {'ukraine','ukrainian','ukrainians'},
    'russia': {'russia','russian','russians'},
    'healthcare': {'healthcare','health','hospital','hospitals','clinic','clinics','medical','medicine','medicines'},
    'cybersecurity': {'cybersecurity','cyber','cyberattack','cyberattacks','ransomware'},
}
BIO = re.compile(r"\b(?:is (?:a |an |the )?(?:senior |chief |international )*(?:reporter|correspondent|journalist)|holds (?:a |an )?(?:bachelor|master)|journalism fellow|speaks \w+ and \w+|recipient of the .*award)\b", re.I)

def tokens(text):
    words=re.findall(r"[^\W_]+",str(text).casefold())
    out=set()
    for word in words:
        if len(word)<3 or word in STOP or word.isdecimal():continue
        canonical=next((key for key,values in ALIASES.items() if word in values),None)
        if canonical:out.add(canonical);continue
        if word.endswith('ies') and len(word)>5:word=word[:-3]+'y'
        elif word.endswith('s') and not word.endswith('ss') and len(word)>4:word=word[:-1]
        out.add(word)
    return out

def anchors(topic):
    found=tokens(search_base(clean_topic(topic)))
    if 'healthcare' in found:found-= {'facility','facilitie','worker','service','delivery','access','care','provision','war'}
    return found

def check_excerpt(topic, title, excerpt):
    required=anchors(topic)
    if not required:return False,'No specific topic anchors; relevance is unresolved.'
    if BIO.search(excerpt):return False,'Author biography is not research evidence.'
    body=tokens(excerpt);combined=body|tokens(title)
    missing=[]
    # Preserve both sides of multi-entity topics, rather than accepting a generic word.
    must=required.intersection({'ukraine','russia','healthcare','cybersecurity'})
    # In Russia-Ukraine war health reporting, a Ukraine-focused health paragraph
    # need not repeat Russia in each excerpt. Explicit comparisons still require both.
    if {'ukraine','russia','healthcare'}.issubset(must) and not re.search(r'\b(?:compar\w*|versus|vs)\b',topic,re.I):
        must.discard('russia')
    if 'healthcare' in required:
        must |= required-({'russia'} if 'russia' not in must and 'ukraine' in must else set())
    missing=sorted(must-combined)
    matched=required&combined
    coverage_required=required-({'russia'} if 'russia' not in must and 'ukraine' in must else set())
    matched=coverage_required&combined
    threshold=min(len(coverage_required),max(2,math.ceil(len(coverage_required)*0.6)))
    if missing:return False,'Missing required subject anchors: '+', '.join(missing)
    if len(matched)<threshold:return False,'Insufficient topic-anchor coverage: '+', '.join(sorted(matched))
    # A title alone cannot make unrelated body paragraphs relevant.
    body_needed=required-{'ukraine','russia'}
    if not body.intersection(body_needed or required):return False,'Topic appears in title only, not excerpt body.'
    if 'healthcare' in required and 'healthcare' not in body:return False,'Healthcare impact is absent from excerpt body.'
    return True,'Matched topic anchors: '+', '.join(sorted(matched))

def select_excerpts(topic,title,excerpts):
    kept=[];rejected=[]
    for text in excerpts:
        accepted,reason=check_excerpt(topic,title,text)
        if accepted:kept.append(text)
        else:rejected.append({'reason':reason,'preview':text[:140]})
    return kept,rejected

def filter_sources(topic,records):
    kept=[];decisions=[]
    for record in records:
        excerpts,rejected=select_excerpts(topic,record.get('title',''),record.get('excerpts',[]))
        decisions.append({'record_id':record.get('record_id'),'title':record.get('title'),'accepted':bool(excerpts),'rejected_excerpts':rejected})
        if excerpts:kept.append({**record,'excerpts':excerpts})
    return kept,decisions

def filter_events(topic,events):
    kept=[];decisions=[]
    for event in events:
        evidence=event.get('evidence',{})
        relevant={key:value for key,value in evidence.items() if check_excerpt(topic,value.get('title',''),value.get('excerpt',''))[0]}
        cited={i for field in ('who','what','when','where','why','how') if event.get(field) for i in event[field].get('excerpt_ids',[])}
        narrative=' '.join(str(event.get(name,{}).get('text','')) for name in ('what','who','where') if isinstance(event.get(name),dict))+' '+str(event.get('title',''))
        subject=anchors(topic);narrative_tokens=tokens(narrative)
        body_subject=subject-{'ukraine','russia'}
        accepted=bool(cited) and cited.issubset(relevant) and bool(narrative_tokens.intersection(body_subject or subject))
        if 'healthcare' in subject:accepted=accepted and 'healthcare' in narrative_tokens
        decisions.append({'title':event.get('title'),'accepted':bool(accepted),'reason':'Cited excerpts and event narrative match topic.' if accepted else 'Event narrative or cited evidence did not pass topic relevance.'})
        if accepted:kept.append(event)
    return kept,decisions
