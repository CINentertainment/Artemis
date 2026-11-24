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
    selected=[citations[i] for i in when.get('excerpt_ids',[]) if i in citations]
    requested=str(when.get('text',''))
    # Every cited date-bearing excerpt must permit the same interpretation.
    supported=[]
    for item in selected:
        pub=parse_date(item.get('published_at'))
        desired=parse_text_date(requested,pub.year if pub else None)
        if not desired:continue
        for match in DATE_PATTERN.finditer(item.get('excerpt','')):
            literal=match.group()
            explicit=bool(re.search(r'\b\d{4}\b',literal))
            candidate=parse_text_date(literal,pub.year if pub else None)
            if candidate==desired and (explicit or (pub and 0 <= (pub-candidate).days <= 30)):
                supported.append(candidate)
    return supported[0].isoformat() if supported and len(set(supported))==1 else None

def apply_event_policy(events, *, current, cutoff):
    retained=[];withheld=[];decisions=[]
    for original in events:
        event=copy.deepcopy(original)
        grounded=resolve_event_date(event.get('when'),event.get('evidence',{}))
        event['validated_event_date']=grounded
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
            event['original_when']=event.get('when');event['when']=None;event['status']='reported'
            event['title']='Undated reporting: '+str(event.get('title',''))[:230]
            event['what']['text']='Source reporting; event date not established. '+event['what']['text']
            detail['decision']='retain_as_undated_reporting'
            detail['reason']='Recent publication supports retaining attributed reporting; publication date is not used as an event date.'
            retained.append(event)
        else:
            detail['decision']='withhold'
            detail['reason']='Grounded event is outside the recent window or has an incompatible future status.' if when else 'No grounded event date and no recent dated cited publication.'
            withheld.append(detail)
        decisions.append(detail)
    return retained,withheld,decisions
