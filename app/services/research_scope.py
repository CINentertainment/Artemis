"""Pure query/date planning shared by research collection and chat."""
import re
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime


def clean_topic(value: str) -> str:
    requested_current=bool(re.search(r'\b(current|latest|recent|today|ongoing|news|updates?)\b',value,re.I))
    # Only task-like clauses are removed, not arbitrary periods in names such as U.S.
    commands = r"(?:preserve|save|create|make|take|leave|keep|cite|distinguish|return|provide|write|identify|include|use\s+(?:recent|current|latest|sources?|reporting)|do\s+not)"
    value = re.split(r"(?:[.!?;]\s+|\n+)" + commands + r"\b", value, maxsplit=1, flags=re.I)[0]
    value = re.split(r"\s+and\s+(?:save|create|make|take|preserve|cite|leave|keep)\b", value, maxsplit=1, flags=re.I)[0]
    value = re.split(r"\s+(?:save|create|make|take)\s+(?:(?:sourced|supported|research|structured|event)\s+)*(?:notes?|findings|brief)\b", value, maxsplit=1, flags=re.I)[0]
    value = re.split(r"\s+(?:and\s+)?identify\s+(?:the\s+)?(?:peoples?|places?|ideas|groups|communities)\b", value, maxsplit=1, flags=re.I)[0]
    value=value.strip(' \t\r\n.,;!?')
    if requested_current and not re.search(r'\b(current|latest|recent|today|ongoing|news|updates?)\b',value,re.I):value='recent '+value
    return value


def is_current(topic: str) -> bool:
    return bool(re.search(r'\b(current|latest|recent|today|ongoing|news|updates?)\b',topic,re.I))


# A trailing list can request report sections rather than constrain every excerpt.
# Remove it only when every word is a recognized generic report facet.
REPORT_FACETS = set('affected impacted people peoples populations groups communities places locations areas infrastructure ideas rights systems services medical healthcare health delivery care effects impacts and or the their on in of to by with include including identify identifying describe describing relevant'.split())


def subject_topic(topic: str) -> str:
    value=clean_topic(topic)
    value=re.sub(r'^\s*(?:(?:current|latest|recent|today|ongoing)\s+)?(?:reporting|reports?|information|updates?|research)\s+(?:on|about|regarding)\s+(?:how\s+)?','',value,flags=re.I)
    facet=re.search(r'(?:[,;]\s*|\s+)(?:including|covering|identifying|with\s+(?:a\s+)?focus\s+on)\s+(.+)$',value,re.I)
    if facet:
        words=set(re.findall(r"[^\W_]+",facet.group(1).casefold()))
        if words and words.issubset(REPORT_FACETS):
            value=value[:facet.start()]
    return value.strip(' \t\r\n.,;!?')


def search_base(topic: str) -> str:
    topic=subject_topic(topic)
    topic=re.sub(r'\b(?:current|latest|recent|today|ongoing|updates?)\b',' ',topic,flags=re.I)
    topic=re.sub(r'\b(?:the|between|information|about|regarding|one|developments?|affects?|affecting|affected|how|is|are|has|been|its|including|covering|identifying)\b',' ',topic,flags=re.I)
    topic=re.sub(r'[–—,;!?()]',' ',topic)
    return ' '.join(topic.split())


def healthcare_focus(topic):
    return bool(re.search(r'\b(?:healthcare|health|medical|hospitals?)\b',topic,re.I)) and not bool(re.search(r'\b(?:stocks?|equities|equity|tickers?|earnings|market|shares?)\b',topic,re.I))


def focused_base(topic):
    base=search_base(topic)
    # Keep geography and the requested domain prominent in humanitarian health queries.
    if healthcare_focus(base) and re.search(r'\bukrain',base,re.I):
        focused=re.sub(r'\b(?:russia|russian|ukraine|ukrainian|war)\b',' ',base,flags=re.I)
        focused=re.sub(r'[–—]',' ',focused)
        focused=re.sub(r'\b(?:in|on|of|for|and|the|at|to|with)\b',' ',focused,flags=re.I)
        focused=' '.join(dict.fromkeys(focused.split()))
        return 'Ukraine '+focused
    return base


def initial_queries(topic: str, now: datetime) -> list[tuple[str,str|None,str|None]]:
    from app.services.world_watch_profiles import profile
    selected=profile(clean_topic(topic))
    if selected:
        query=f"{selected['query']} {now:%B %Y}"
        return [(query,None,'month'), *[(f'site:{domain} {query}',None,'month') for domain in selected['domains']], (query,'news','month')]
    base=focused_base(topic)
    current=is_current(topic)
    dated=f'{base} {now:%B %Y}' if current else base
    window='month' if current else None
    healthcare=healthcare_focus(base)
    if healthcare:
        # Different query families get page attempts before the overall budget is exhausted.
        return [(dated,None,window),
                (f'site:who.int {dated}',None,window),
                (f'site:msf.org {dated}',None,window),
                (f'site:reliefweb.int {dated}',None,window),
                (f'{base} patient care access report',None,window)]
    if not current:return [(base,None,None)]
    return [(dated,None,window),(f'{base} official report {now:%B %Y}',None,window),
            (dated,'news',window)]


def followup_queries(topic: str, now: datetime) -> list[str]:
    base=focused_base(topic)
    if healthcare_focus(base):
        suffix=f' {now:%B %Y}' if is_current(topic) else ''
        return [f'site:msf.org {base} patients hospital report{suffix}',
                f'site:who.int {base} situation report{suffix}']
    if is_current(topic):
        return [f'{base} official situation report {now:%B %Y}',f'{base} documented impacts {now:%B %Y}']
    return [f'{base} timeline primary sources',f'{base} documented impacts']


def parse_date(value) -> date | None:
    if not isinstance(value,str):return None
    value=value.strip()
    if not value:return None
    try:
        result=datetime.fromisoformat(value.replace('Z','+00:00'))
        # Compare publisher dates using their stated calendar day, not inferred event dates.
        return result.date()
    except ValueError:pass
    for fmt in ('%Y-%m-%d','%B %d, %Y','%b %d, %Y','%d %B %Y','%d %b %Y'):
        try:return datetime.strptime(value,fmt).date()
        except ValueError:pass
    try:return parsedate_to_datetime(value).date()
    except (ValueError,TypeError,OverflowError):return None


def freshness(published: str|None, as_of: date) -> str:
    value=parse_date(published)
    if value is None:return 'publication_date_unknown'
    if value>as_of:return 'publication_date_in_future'
    return 'recent_publication_claim' if value>=as_of-timedelta(days=30) else 'older_publication'


def freshness_summary(findings, current: bool, as_of: date) -> dict:
    from urllib.parse import urlsplit
    recent=[f for f in findings if freshness(getattr(f,'published_at',None),as_of)=='recent_publication_claim']
    hosts={urlsplit(f.url).hostname.removeprefix('www.') for f in recent if urlsplit(f.url).hostname}
    return {'current_request':current,'as_of':as_of.isoformat(),'lookback_start':(as_of-timedelta(days=30)).isoformat(),
            'recent_dated_source_count':len(recent),'recent_source_host_count':len(hosts),
            'older_source_count':sum(freshness(getattr(f,'published_at',None),as_of)=='older_publication' for f in findings),
            'unknown_date_source_count':sum(freshness(getattr(f,'published_at',None),as_of)=='publication_date_unknown' for f in findings),
            'status':'recent_reporting_collected' if recent else 'no_recent_dated_reporting',
            'limits':'Publication dates are publisher metadata claims, not event dates. Distinct hosts do not prove independent reporting.'}


def has_future_date(text: str, as_of: date) -> bool:
    for value in re.findall(r'\b\d{4}-\d{2}-\d{2}\b',text):
        parsed=parse_date(value)
        if parsed and parsed>as_of:return True
    return bool(re.search(r'\b(?:forecast horizon|next 90 days|future reporting)\b',text,re.I))
