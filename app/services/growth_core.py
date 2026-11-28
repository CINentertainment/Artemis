"""Bounded editorial campaign and official LinkedIn API adapter.

Search snippets never become publishing instructions or product claims.
"""
import hashlib
import math
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlsplit, urlencode
import httpx

PRODUCT_URL = 'https://artemisbycin.gumroad.com/l/analyst-workflow-toolkit'
PRODUCT_NAME = 'Analyst Workflow Toolkit — Artemis Edition'
CAMPAIGN = 'toolkit-v1'
API_VERSION = '202610'
# Original factual copy tied to the shipped toolkit, without market or revenue claims.
LESSONS = (
 ('collection', 'Start a research task with the decision it needs to support. Write the question, scope, approved sources and a stop condition before opening another search tab.'),
 ('sources', 'A useful source register captures more than a URL. Save the publisher, publication date, collection time and access limitations so another analyst can retrace the work.'),
 ('evidence', 'Separate the claim from the evidence. Keep the supporting passage alongside its source reference, then record uncertainty and contradictory reporting.'),
 ('entities', 'Matching names are a starting point for an identity check. Record why two mentions refer to the same entity before combining their histories.'),
 ('relationships', 'Two names appearing in one note does not establish a relationship. Record the passage that supports the specific connection and preserve its caveats.'),
 ('hypotheses', 'Before settling on an explanation, write down at least one plausible alternative. Track which evidence supports or weakens each explanation and what remains missing.'),
 ('timeline', 'Collection time and event time answer different questions. Keep them separate, and leave an event date unresolved when the source does not establish it.'),
 ('decisions', 'A decision log makes assumptions visible. Record the evidence used, the reason for the decision and the trigger that would make you reconsider.'),
 ('handover', 'Make a research handover easier to follow: state the question, summarize the supported findings, identify the gaps and point back to the evidence.'),
 ('independence', 'Several articles may repeat one original report. Record source dependence before treating repeated coverage as independent corroboration.'),
 ('uncertainty', 'An unresolved field is useful information. Mark what is unknown rather than filling a gap with a plausible detail.'),
 ('briefing', 'Give a briefing reader a clear route back to the source. Keep concise findings in the brief and maintain supporting passages in the research records.'),
 ('review', 'Set a review trigger when recording an assessment: new evidence, a changed assumption or a decision deadline. That gives the next research session a purpose.'),
 ('scope', 'A collection plan helps keep a broad topic manageable. Define the geographic and time boundaries and decide which questions are outside the current task.'),
 ('traceability', 'A finding is easier to evaluate when its source, evidence and analytical judgment are distinguishable. Preserve all three through the research workflow.'),
 ('workflow', 'A repeatable workflow connects collection questions, sources, evidence, entities, relationships, hypotheses, events and decisions. The structure helps make reasoning inspectable.'),
)
# Additional material stays within the standing factual workflow policy; examples are explicitly fictional.
EXTRAS = (
('collection','Checklist: define the decision, the collection question, the geographic and time boundaries, and the stop condition.','Fictional example: a team comparing two warehouse locations records transport disruption questions before starting collection.'),
('sources','Checklist: capture title, publisher, URL, publication date, collection time and access limitations.','Fictional example: three news summaries cite the same company statement. The team records that shared origin rather than counting three independent sources.'),
('evidence','Checklist: retain the exact passage, identify its source, distinguish event time from collection time, and state uncertainty.','Fictional example: a report says service may resume next week. The ledger preserves that uncertainty instead of recording a confirmed reopening.'),
('entities','Checklist: compare names, location, role and context, then record the reason for any identity decision.','Fictional example: two businesses share a trading name in different cities. Their records remain separate until supporting identity evidence is found.'),
('relationships','Checklist: identify both endpoints, the specific relationship, its supporting passage and its review status.','Fictional example: two organizations appear in a conference attendee list. A shared mention is recorded without asserting a partnership.'),
('hypotheses','Checklist: record competing explanations, evidence that supports or weakens each, and the information still needed.','Fictional example: delayed deliveries might reflect road closures or supplier problems. The team keeps both explanations open while collecting evidence.'),
('timeline','Checklist: preserve the source date wording, distinguish publication and collection times, and leave missing dates unresolved.','Fictional example: a source says Monday without a year. The event timeline retains that wording rather than assigning an invented full date.'),
('decisions','Checklist: state the decision, evidence references, assumptions, review trigger and later outcome.','Fictional example: a team postpones a route change until a bridge status update. The decision log records what new evidence would change that choice.'),
('handover','Checklist: give the next analyst the question, key findings, evidence references, open gaps and next collection step.','Fictional example: a weekend handover flags one unresolved location and includes the original passage so the next analyst can investigate it.'),
('independence','Checklist: identify original reporting, shared statements, syndication and sources that genuinely add independent information.','Fictional example: multiple articles quote the same press release. The source register keeps the shared provenance visible.'),
('uncertainty','Checklist: separate known information, reported claims, analytical judgments and unresolved questions.','Fictional example: an unconfirmed outage report stays a reported claim while the team seeks the operator statement.'),
('briefing','Checklist: lead with the question and key findings, preserve caveats, and provide source references for follow-up.','Fictional example: a one-page brief summarizes service disruptions and links each finding to the evidence record rather than repeating every collected paragraph.'),
('review','Checklist: set a review date, identify assumptions and specify the new evidence that would trigger reconsideration.','Fictional example: an assessment is scheduled for review after the next operator update rather than left unchanged indefinitely.'),
('scope','Checklist: define the time window, geography, relevant actors and questions outside the current research task.','Fictional example: a team scopes a report to one region and one month, placing older background material in a separate context section.'),
('traceability','Checklist: keep each finding connected to a claim, evidence passage, source and analyst judgment.','Fictional example: a reviewer follows a brief finding back through its evidence reference to the saved source passage.'),
('workflow','Checklist: connect the collection plan, source register, evidence ledger, entity and relationship records, hypotheses, timeline and decisions.','Fictional example: a small research team uses a shared workflow to hand over an unfinished question without losing its evidence trail.'),
)
LESSONS = LESSONS + tuple((key+'-checklist',check) for key,check,example in EXTRAS) + tuple((key+'-example',example) for key,check,example in EXTRAS)
BANK_HASH = hashlib.sha256(repr(LESSONS).encode()).hexdigest()

class ProviderError(ValueError):
    def __init__(self, message, code='provider_error'):
        super().__init__(message); self.code = code

class UncertainSend(ProviderError):
    pass

def draft(index, channel):
    key, lesson = LESSONS[index % len(LESSONS)]
    opening = 'A practical research habit:' if channel == 'member' else 'From the CIN Studios analyst workflow:'
    ending = ('The Analyst Workflow Toolkit — Artemis Edition includes eight editable CSV templates, a workflow guide and a fictional worked example. '
              'This is a standalone toolkit; Artemis software access is not included.')
    url = PRODUCT_URL + '?' + urlencode({'utm_source':'linkedin','utm_medium':'organic','utm_campaign':CAMPAIGN,'utm_content':channel+'-'+key})
    return dict(key=key, text=opening+'\n\n'+lesson+'\n\n'+ending+'\n\n'+url+'\n\n#IntelligenceAnalysis #ResearchWorkflow #CINStudios', url=url)

# Bind standing approval to every exact public string, including openings, links and hashtags.
BANK_HASH = hashlib.sha256(repr({k:[draft(i,k) for i in range(len(LESSONS))] for k in ('member','organization')}).encode()).hexdigest()

def next_slot(after, hour, minute):
    """Next M/W/F UTC slot, strictly after now; no missed-slot backlog."""
    dt = datetime.fromtimestamp(after, timezone.utc)
    for n in range(8):
        slot = (dt + timedelta(days=n)).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if slot.weekday() in (0,2,4) and slot.timestamp() > after:
            return slot.timestamp()
    raise ValueError('No next slot')

def valid_post_id(value):
    return isinstance(value,str) and bool(re.fullmatch(r'urn:li:(share|ugcPost):[0-9]+',value))

def post_url(value):
    return 'https://www.linkedin.com/feed/update/'+value+'/' if valid_post_id(value) else None

def client(token='', transport=None):
    return httpx.Client(headers={'Authorization':'Bearer '+token,'Linkedin-Version':API_VERSION,
        'X-Restli-Protocol-Version':'2.0.0'}, timeout=25, follow_redirects=False, trust_env=False, transport=transport)

def get_json(c, path, params=None):
    if not (path == '/v2/userinfo' or path in ('/rest/organizationAcls',) or
            re.fullmatch(r'/rest/(organizations/[0-9]+|socialActions/urn%3Ali%3A(?:share|ugcPost)%3A[0-9]+|posts/urn%3Ali%3A(?:share|ugcPost)%3A[0-9]+)',path)):
        raise ProviderError('Unsupported provider read operation.')
    try:
        r=c.get('https://api.linkedin.com'+path,params=params)
        if r.status_code == 401: raise ProviderError('LinkedIn connection expired. Reconnect locally.','auth')
        if r.status_code == 403: raise ProviderError('LinkedIn did not grant the required API access.','access')
        r.raise_for_status()
        if len(r.content)>1_000_000: raise ValueError()
        data=r.json()
        if not isinstance(data,dict): raise ValueError()
        return data
    except ProviderError: raise
    except (httpx.HTTPError,ValueError):
        raise ProviderError('LinkedIn read failed; check provider availability.','read') from None

def verify(token, app_id, secret, channel, organization_id=''):
    with client(token) as c:
        try:
            r=c.post('https://www.linkedin.com/oauth/v2/introspectToken', data={
                'client_id':app_id,'client_secret':secret,'token':token})
            r.raise_for_status(); info=r.json()
            expires=info.get('expires_at')
            if info.get('active') is not True or info.get('client_id')!=app_id or type(expires) not in (int,float) or not math.isfinite(expires) or expires<=time.time()+300:
                raise ValueError()
            scopes=set(re.split(r'[ ,]+',str(info.get('scope',''))))
        except (httpx.HTTPError,ValueError,AttributeError):
            raise ProviderError('Token verification failed. Check the application ID, secret and unexpired member token.','auth') from None
        person=get_json(c,'/v2/userinfo')
        sub=person.get('sub')
        if not isinstance(sub,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,150}',sub):
            raise ProviderError('LinkedIn did not provide a stable member identity.','identity')
        member='urn:li:person:'+sub
        if channel=='member':
            if 'w_member_social' not in scopes: raise ProviderError('Token requires w_member_social.','access')
            author=member; name=str(person.get('name','LinkedIn member'))[:160]
        else:
            if 'w_organization_social' not in scopes: raise ProviderError('Company publishing requires w_organization_social and approved Page API access.','access')
            if not re.fullmatch(r'[0-9]{1,25}',organization_id): raise ProviderError('Use the numeric LinkedIn organization ID.','identity')
            author='urn:li:organization:'+organization_id
            # Use roleAssignee finder; restrict to exact member, organization and approved role.
            found=False
            for start in range(0,1000,100):
                page=get_json(c,'/rest/organizationAcls',{'q':'roleAssignee','state':'APPROVED','count':100,'start':start})
                rows=page.get('elements')
                if not isinstance(rows,list): raise ProviderError('Organization access response is invalid.','identity')
                if any(row.get('organization')==author and row.get('roleAssignee')==member and row.get('state')=='APPROVED' and
                       row.get('role') in ('ADMINISTRATOR','CONTENT_ADMINISTRATOR','CONTENT_ADMIN') for row in rows if isinstance(row,dict)):
                    found=True;break
                if len(rows)<100:break
            if not found: raise ProviderError('No supported approved publishing role was verified for this Page.','access')
            org=get_json(c,'/rest/organizations/'+organization_id)
            name=str(org.get('localizedName') or 'LinkedIn organization '+organization_id)[:160]
        return dict(author=author,member=member,name=name,expires_at=expires,scopes=sorted(scopes),verified_at=time.time(),channel=channel)

def publish(c, author, text):
    if not re.fullmatch(r'urn:li:(person:[A-Za-z0-9_-]{1,150}|organization:[0-9]{1,25})',author): raise ProviderError('Invalid publishing identity.','identity')
    if not 1<=len(text)<=2900: raise ProviderError('Post length invalid.','content')
    payload={'author':author,'commentary':text,'visibility':'PUBLIC', 'distribution':{
        'feedDistribution':'MAIN_FEED','targetEntities':[],'thirdPartyDistributionChannels':[]},
        'lifecycleState':'PUBLISHED','isReshareDisabledByAuthor':False}
    try:r=c.post('https://api.linkedin.com/rest/posts',json=payload)
    except httpx.HTTPError:
        raise UncertainSend('Publish acknowledgement missing. Check LinkedIn before resolving; no automatic resend.','uncertain') from None
    if r.status_code==201:
        pid=r.headers.get('x-restli-id')
        if valid_post_id(pid): return pid
        raise UncertainSend('LinkedIn accepted the request without a usable post ID. Check your feed.','uncertain')
    if r.status_code in (401,403): raise ProviderError('LinkedIn rejected publishing permissions. Reconnect or request API access.','auth')
    if r.status_code==429: raise ProviderError('LinkedIn rate limit reached. Channel paused.','rate_limit')
    if r.status_code>=500 or 200<=r.status_code<300:
        raise UncertainSend('Provider outcome is unclear. Check LinkedIn before resolving; no automatic resend.','uncertain')
    raise ProviderError('LinkedIn rejected the post. Channel paused; review the saved text.','rejected')

def metrics(c, pid):
    if not valid_post_id(pid):raise ProviderError('Invalid post reference.')
    data=get_json(c,'/rest/socialActions/'+quote(pid,safe=''))
    def count(key):
        summary=data.get(key)
        if not isinstance(summary,dict):return None
        v=summary.get('totalFirstLevelComments' if key=='commentsSummary' else 'totalLikes')
        return v if type(v) is int and v>=0 else None
    return {'likes':count('likesSummary'),'comments':count('commentsSummary'),'collected_at':time.time(),
            'impressions':None,'clicks':None,'attributed_orders':None}

def candidate(row):
    """Transparent snippet triage only; no inference of contacts or buying intent."""
    u=urlsplit(str(row.get('url','')))
    if u.scheme!='https' or not u.hostname or u.username or u.password:return None
    text=(str(row.get('title',''))+' '+str(row.get('snippet',''))).lower()
    label='related resource'
    if any(w in text for w in ('forum','community','discussion')):label='possible discussion venue'
    elif any(w in text for w in ('template','toolkit','course','handbook')):label='comparable resource'
    return {'url':u.geturl(),'title':str(row.get('title',''))[:500],'snippet':str(row.get('snippet',''))[:1200],
            'category':label,'collected_at':row.get('collected_at'),'verified':False,
            'posting_permission':'unknown','buying_intent':'unknown','contact':None}
