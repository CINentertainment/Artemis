"""Ground event dates in cited text and preserve undated reporting explicitly."""
import copy
import re
from datetime import datetime, timedelta
from app.services.research_scope import parse_date

MONTH = r'(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)'
DATE_PATTERN = re.compile(r'\b(?:\d{4}-\d{2}-\d{2}|'+MONTH+r'\.?\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|\d{1,2}(?:st|nd|rd|th)?\s+'+MONTH+r'\.?(?:,?\s+\d{4})?)\b', re.I)

def normalize(text):
    text=re.sub(r'(?<=\d)(st|nd|rd|th)\b','',text,flags=re.I)
    text=re.sub(r'\bSept\b','Sep',text,flags=re.I)
    return ' '.join(text.replace('.','').replace(',','').split())

def parse_text_date(text, year=None):
    text=normalize(text)
    for fmt in ('%Y-%m-%d','%B %d %Y','%b %d %Y','%d %B %Y','%d %b %Y'):
        try:return datetime.strptime(text,fmt).date()
        except ValueError:pass
    if year:
        for fmt in ('%B %d','%b %d','%d %B','%d %b'):
            try:return datetime.strptime(text,fmt).date().replace(year=year)
            except ValueError:pass
    return None

def resolve_event_date(when, citations):
    if not when:return None
    requested=str(when.get('text',''))
    # No year is borrowed from publication metadata, URLs, titles or the current day.
    supported=[]
    for i in when.get('excerpt_ids',[]):
        item=citations.get(i)
        if not item:continue
        excerpt=item.get('event_date_context',item.get('excerpt',''))
        for match in DATE_PATTERN.finditer(excerpt):
            literal=match.group()
            if not re.search(r'\b\d{4}\b',literal):continue
            prefix=excerpt[max(0,match.start()-40):match.start()]
            if re.search(r'\b(?:up to|since|as of|through|between|from)\s*$',prefix,re.I):continue
            candidate=parse_text_date(literal)
            if not candidate:continue
            desired=parse_text_date(requested)
            if desired is None and normalize(requested) in normalize(literal):
                desired=parse_text_date(requested,candidate.year)
            if desired==candidate:supported.append(candidate)
    return supported[0].isoformat() if supported and len(set(supported))==1 else None


def partial_point_date(when):
    if not when:return False
    text=str(when.get('text',''))
    return bool(DATE_PATTERN.fullmatch(text)) and not re.search(r'\b\d{4}\b',text)


def apply_event_policy(events, *, current, cutoff):
    retained=[];withheld=[];decisions=[]
    for original in events:
        event=copy.deepcopy(original)
        grounded=resolve_event_date(event.get('when'),event.get('evidence',{}))
        event['validated_event_date']=grounded
        partial=grounded is None and partial_point_date(event.get('when'))
        event['date_resolution']={'state':'explicit_date' if grounded else 'partial_date' if partial else 'not_established','literal':event.get('when'),'year_inferred':False}
        when=parse_date(grounded)
        status=str(event.get('status','reported')).casefold()
        detail={'title':event.get('title'),'original_status':status,'original_when':event.get('when'),
                'validated_event_date':grounded,'source_publication_dates':sorted({str(e['published_at']) for e in event.get('evidence',{}).values() if e.get('published_at')})}
        recent=when and cutoff-timedelta(days=30)<=when<=cutoff
        future_plan=when and when>cutoff and status in {'planned','announced','scheduled'}
        if not current or recent or future_plan:
            detail['decision']='retain';detail['reason']='Grounded date fits scope.' if current else 'Historical research scope.'
            retained.append(event)
        elif when is None and any((p:=parse_date(e.get('published_at'))) and cutoff-timedelta(days=30)<=p<=cutoff for e in event.get('evidence',{}).values()):
            event['original_when']=event.get('when')
            if not partial:event['when']=None
            event['status']='reported'
            label='Partially dated reporting: ' if partial else 'Undated reporting: '
            event['title']=label+str(event.get('title',''))[:250-len(label)]
            event['what']['text']=('Source reporting; full event date not established (year unresolved). ' if partial else 'Source reporting; event date not established. ')+event['what']['text']
            detail['decision']='retain_as_partial_date_reporting' if partial else 'retain_as_undated_reporting'
            detail['reason']='Recent publication supports retaining attributed reporting; the source-stated partial date is preserved without inferring its year.' if partial else 'Recent publication supports retaining attributed reporting; publication date is not used as an event date.'
            retained.append(event)
        else:
            detail['decision']='withhold'
            detail['reason']='Grounded event is outside the recent window or has an incompatible future status.' if when else 'No grounded event date and no recent dated cited publication.'
            withheld.append(detail)
        decisions.append(detail)
    return retained,withheld,decisions
