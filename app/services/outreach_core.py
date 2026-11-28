"""Business outreach primitives: pinned public-page reads and official Gmail API.

Web pages and incoming mail are untrusted data, never executable instructions.
"""
import base64
import hashlib
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import getaddresses, formataddr, formatdate
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, quote
from urllib.robotparser import RobotFileParser
from zoneinfo import ZoneInfo
import httpx

SENDER='artemis@cinentertainment.net'
MAILBOX='business@cinentertainment.net'
PRODUCT='https://artemisbycin.gumroad.com/l/analyst-workflow-toolkit'
SCOPES=('https://www.googleapis.com/auth/gmail.send','https://www.googleapis.com/auth/gmail.readonly','https://www.googleapis.com/auth/gmail.settings.basic')
ROLES={'info','hello','contact','business','partnerships','research','inquiries','enquiries'}
FIT=re.compile(r'\b(?:research|intelligence|risk|advisory|consulting|consultancy|analyst|investigation|training)\b',re.I)
US=re.compile(r'\bUnited States\b|\b(?:Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming|AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\s+\d{5}(?:-\d{4})?\b')
BLOCK_HOSTS={'linkedin.com','facebook.com','instagram.com','youtube.com','x.com','twitter.com','github.com','gumroad.com','google.com','bing.com','reddit.com','wikipedia.org'}
QUERIES=(
 'US research consulting firm "contact" "United States"',
 'US risk advisory consulting "contact" "Texas"',
 'US intelligence analysis training company "contact"',
 'US business research consultancy "contact us"')

class Error(ValueError):
    def __init__(self,message,code='error'):super().__init__(message);self.code=code
class Uncertain(Error):pass

def digest(*parts):return hashlib.sha256('\n'.join(str(p) for p in parts).encode()).hexdigest()
def clean(value,limit=500):return re.sub(r'\s+',' ',str(value)).strip()[:limit]
def email_address(value):
    value=str(value).strip().lower()
    return value if re.fullmatch(r'[a-z0-9][a-z0-9._+-]{0,63}@[a-z0-9][a-z0-9.-]{1,190}\.[a-z]{2,24}',value) else None

def site(value):
    try:
        u=urlsplit(value);host=(u.hostname or '').lower().encode('idna').decode()
        if u.scheme!='https' or not host or u.username or u.password or u.port not in (None,443) or len(value)>2000 or re.search(r'[\x00-\x20]',value):raise ValueError()
        root=host.removeprefix('www.')
        if any(root==b or root.endswith('.'+b) for b in BLOCK_HOSTS):raise ValueError()
        if '.' not in root or root.endswith(('.gov','.mil','.edu')):raise ValueError()
        return u,host,root
    except (ValueError,UnicodeError):raise Error('Unsupported public business URL.','url') from None

class Text(HTMLParser):
    def __init__(self):super().__init__(convert_charrefs=True);self.skip=0;self.parts=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript','svg'):self.skip+=1
        if tag=='a':
            a=dict(attrs)
            if a.get('href'):self.links.append(a['href'])
        if tag in ('p','div','br','li','h1','h2','h3','footer'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript','svg') and self.skip:self.skip-=1
    def handle_data(self,data):
        if not self.skip:self.parts.append(data)
    def text(self):return '\n'.join(clean(p,1500) for p in ''.join(self.parts).splitlines() if clean(p))

def fetch_public(url,limit=300000):
    u,host,root=site(url)
    try:
        addresses={a[4][0] for a in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):raise Error('Non-public network destination refused.','network')
        ip=sorted(addresses)[0]
        class Pinned(http.client.HTTPSConnection):
            def connect(self):
                sock=socket.create_connection((ip,443),timeout=12)
                self.sock=ssl.create_default_context().wrap_socket(sock,server_hostname=host)
        c=Pinned(host,timeout=12)
        try:
            path=u.path or '/'
            if u.query:path+='?'+u.query
            c.request('GET',path,headers={'User-Agent':'ArtemisBusinessResearch/1.0','Accept':'text/html,text/plain','Accept-Encoding':'identity'})
            r=c.getresponse();raw=r.read(limit+1)
            if len(raw)>limit:raise Error('Business page exceeded the collection bound.','size')
            if r.status in (301,302,303,307,308):raise Error('Business page redirects; skipped for automatic collection.','redirect')
            if r.status==404:return None
            if r.status!=200:raise Error('Business page unavailable.','http')
            if not any(t in r.getheader('Content-Type','').lower() for t in ('text/html','text/plain','application/xhtml+xml')):raise Error('Unsupported page content.','content')
            return raw.decode('utf-8',errors='replace')
        finally:c.close()
    except Error:raise
    except (OSError,ssl.SSLError,http.client.HTTPException):raise Error('Business page could not be collected.','network') from None

def collect(url):
    u,host,root=site(url);origin='https://'+host
    robots=fetch_public(origin+'/robots.txt',100000)
    rp=RobotFileParser();rp.parse((robots or '').splitlines())
    if not rp.can_fetch('ArtemisBusinessResearch',url):raise Error('Site robots policy does not permit collection.','robots')
    pages=[];pending=[url];seen=set()
    while pending and len(pages)<3:
        page=pending.pop(0)
        if page in seen:continue
        seen.add(page)
        if not rp.can_fetch('ArtemisBusinessResearch',page):continue
        raw=fetch_public(page)
        if raw is None:continue
        parser=Text();parser.feed(raw);text=parser.text();pages.append({'url':page,'text':text})
        for link in parser.links:
            target=urljoin(origin,link);p=urlsplit(target)
            if p.hostname==host and p.scheme=='https' and not p.query and not p.fragment and re.search(r'/(contact|about)(?:[-/]|$)',p.path,re.I):
                if target not in seen and len(pending)<3:pending.append(target)
    alltext='\n'.join(p['text'] for p in pages)
    if not US.search(alltext):raise Error('No explicit US location was found; automatic outreach held.','location')
    evidence=[]
    for p in pages:
        for line in p['text'].splitlines():
            if FIT.search(line) and len(line)>=30:evidence.append({'url':p['url'],'quote':clean(line,500)})
    if not evidence:raise Error('No relevant business activity was found in the collected pages.','fit')
    contacts=[]
    for p in pages:
        for addr in re.findall(r'[A-Za-z0-9][A-Za-z0-9._+-]*@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}',p['text']):
            addr=email_address(addr)
            if addr and addr.split('@')[0] in ROLES and addr.split('@')[1].removeprefix('www.')==root:
                contacts.append({'email':addr,'source_url':p['url'],'quote':next((clean(line,500) for line in p['text'].splitlines() if addr.lower() in line.lower()),addr)})
    if not contacts:raise Error('No published same-domain business contact address was found.','contact')
    return {'domain':root,'contact':contacts[0], 'evidence':evidence[:3], 'us_evidence':clean(US.search(alltext).group(),200),
            'collected_at':time.time(),'fit':'Rule-based match to public business wording; buying intent is unknown.'}

def business_time(now=None):
    dt=datetime.fromtimestamp(now or time.time(),ZoneInfo('America/Chicago'))
    return dt.weekday()<5 and 9<=dt.hour<17

def business_days_after(stamp,count=5):
    dt=datetime.fromtimestamp(stamp,ZoneInfo('America/Chicago'));n=0
    while n<count:
        dt+=timedelta(days=1)
        if dt.weekday()<5:n+=1
    return dt.timestamp()

def footer(postal):
    return '\nCIN Studios | Artemis, automated research assistant\nCommercial correspondence.\n'+postal+'\nTo stop all marketing from CIN Studios, reply STOP or ask us not to contact you again.\n'

def copy(prospect,offer,postal,followup=False):
    domain=prospect['domain']
    subject='CIN Studios introduction — research workflow toolkit' if offer=='toolkit' else 'CIN Studios introduction — public-source research briefs'
    if followup:subject='Follow-up: '+subject
    # The personalization cites the activity category and the exact source URL, not untrusted prose.
    source=prospect['evidence'][0]['url']
    intro=f'Hello,\n\nYour public business page describes research, advisory or related work: {source}\n'
    if followup:intro+='\nThis is one brief follow-up to our introduction. We will not send another automated follow-up.\n'
    if offer=='toolkit':
        body=('\nCIN Studios offers the Analyst Workflow Toolkit — Artemis Edition: eight editable CSV templates, a practical guide and a fictional worked example for organizing collection questions, sources, evidence, entities, relationships, hypotheses, events and decisions.\n\n'
              'You can review the contents and current price here:\n'+PRODUCT+'?utm_source=email&utm_medium=outreach&utm_campaign=toolkit-v1\n\n'
              'This is a standalone toolkit; Artemis software access is not included. If it fits your workflow, the product page has the details.\n')
    else:
        body=('\nCIN Studios is introducing custom public-source research briefs for business questions, industry developments and emerging risks. The proposed format is a concise summary with source references, a timeline where supported, and explicit information gaps.\n\n'
              'If this would be useful, reply with the business question and the intended audience. Daryl will review the scope before agreeing to any price, deadline or deliverables. No engagement is accepted by this introduction.\n')
    footer='\nCIN Studios | Artemis, automated research assistant\nCommercial introduction / advertisement.\n'+postal+'\nTo stop all marketing from CIN Studios, reply STOP or simply ask us not to contact you again.\n'
    return subject,intro+body+footer

def mime(to,subject,body,message_id,in_reply_to=None,references=None):
    if not email_address(to) or re.search(r'[\r\n]',subject) or not re.fullmatch(r'<artemis\.[a-f0-9]{32}@cinentertainment.net>',message_id):raise Error('Invalid outbound message headers.')
    m=EmailMessage(policy=SMTP);m['From']=formataddr(('CIN Studios | Artemis',SENDER));m['To']=to;m['Reply-To']=SENDER
    m['Subject']=subject;m['Message-ID']=message_id;m['Date']=formatdate(localtime=False)
    m['List-Unsubscribe']='<mailto:'+SENDER+'?subject=STOP>'
    m['Auto-Submitted']='auto-generated'
    if in_reply_to:
        if not re.fullmatch(r'<[^\s<>\r\n]{1,250}>',in_reply_to):raise Error('Invalid thread reference.')
        m['In-Reply-To']=in_reply_to;m['References']=in_reply_to
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()

def headers(data):
    h={}
    for x in (data.get('payload') or {}).get('headers',[]):
        name=str(x.get('name','')).lower()
        h[name]=(h.get(name,'')+', ' if name in h else '')+str(x.get('value','')).strip()
    return h

def addresses(h,name):return {a.lower() for _,a in getaddresses([h.get(name,'')]) if email_address(a)}
def body_text(data):
    found=[]
    def walk(part,depth=0):
        if depth>8:return
        if part.get('mimeType')=='text/plain':
            value=(part.get('body') or {}).get('data','')
            try:found.append(base64.urlsafe_b64decode(value+'='*(-len(value)%4)).decode('utf-8',errors='replace')[:8000])
            except (ValueError,TypeError):pass
        for child in part.get('parts',[])[:20]:walk(child,depth+1)
    walk(data.get('payload') or {})
    return '\n'.join(found)[:12000] or clean(data.get('snippet',''),1200)

def inbound_kind(data):
    h=headers(data);text=(h.get('subject','')+' '+body_text(data)).lower()
    if any(t in text for t in ('unsubscribe','remove me','do not contact','stop emailing','stop contacting','no more emails','opt out','not interested','no thanks','no thank you')) or re.search(r'^\s*stop\b',body_text(data),re.I):return 'opt_out'
    if any('mailer-daemon' in a or 'postmaster' in a for a in addresses(h,'from')) or 'delivery-status' in str(data.get('payload',{})) or 'undeliverable' in text:return 'bounce'
    if h.get('auto-submitted','no').lower()!='no' or h.get('x-autoreply') or h.get('x-autorespond') or 'out of office' in text:return 'automatic_reply'
    return 'reply'

class Gmail:
    def __init__(self,credentials,transport=None):
        self.credentials=credentials;self.http=httpx.Client(timeout=25,follow_redirects=False,trust_env=False,transport=transport)
        self.access=''
    def __enter__(self):return self
    def __exit__(self,*a):self.http.close()
    def authorize(self):
        try:
            r=self.http.post('https://oauth2.googleapis.com/token',data={'client_id':self.credentials['client_id'],'client_secret':self.credentials.get('client_secret',''), 'refresh_token':self.credentials['refresh_token'],'grant_type':'refresh_token'})
            r.raise_for_status();d=r.json();self.access=d['access_token']
            if not isinstance(self.access,str) or not self.access:raise ValueError()
        except (httpx.HTTPError,ValueError,KeyError):raise Error('Google authorization failed. Reconnect the mailbox locally.','auth') from None
    def get(self,path,params=None):
        if not re.fullmatch(r'(profile|settings/sendAs|messages|history|messages/[A-Za-z0-9_-]+|threads/[A-Za-z0-9_-]+)',path):raise Error('Unsupported Gmail operation.')
        try:
            r=self.http.get('https://gmail.googleapis.com/gmail/v1/users/me/'+path,params=params,headers={'Authorization':'Bearer '+self.access})
            if r.status_code==404 and path=='history':raise Error('Mailbox history expired; reconnect and recheck active threads.','history')
            r.raise_for_status()
            if len(r.content)>3_000_000:raise ValueError()
            d=r.json()
            if not isinstance(d,dict):raise ValueError()
            return d
        except Error:raise
        except (httpx.HTTPError,ValueError):raise Error('Mailbox read failed; outbound mail is held.','read') from None
    def binding(self):
        p=self.get('profile');email=str(p.get('emailAddress','')).lower();aliases=self.get('settings/sendAs').get('sendAs',[])
        if email!=MAILBOX:raise Error('Connect business@cinentertainment.net, which owns the Artemis alias.','identity')
        if not any(a.get('sendAsEmail','').lower()==SENDER and (a.get('verificationStatus')=='accepted' or a.get('isPrimary') is True) for a in aliases):
            raise Error('The Artemis send-as alias is not accepted in this Gmail mailbox.','alias')
        return {'mailbox':email,'sender':SENDER,'history_id':str(p.get('historyId','')),'verified_at':time.time()}
    def send(self,raw,thread_id=None):
        body={'raw':raw}
        if thread_id:body['threadId']=thread_id
        try:r=self.http.post('https://gmail.googleapis.com/gmail/v1/users/me/messages/send',json=body,headers={'Authorization':'Bearer '+self.access})
        except httpx.HTTPError:raise Uncertain('Mail send acknowledgement missing. Check Sent mail; no automatic resend.','uncertain') from None
        if r.status_code in (200,201):
            try:
                d=r.json()
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',d.get('id','')) or not re.fullmatch(r'[A-Za-z0-9_-]{1,200}',d.get('threadId','')):raise ValueError()
                return {'id':d['id'],'thread_id':d['threadId']}
            except (ValueError,TypeError):raise Uncertain('Mail may have been sent; provider IDs are missing. Check Sent mail.','uncertain') from None
        if r.status_code>=500:raise Uncertain('Mail provider outcome is uncertain. Check Sent mail.','uncertain')
        raise Error('Google rejected the send. Outreach is paused; check access or quota.','send')
