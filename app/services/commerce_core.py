"""Read-only Gumroad adapter and conservative commercial accounting.

No card data, buyer identity, outbound communication, or financial mutation.
"""
import hashlib
import io
import json
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED
import httpx

STORE = 'https://artemisbycin.gumroad.com'
API = 'https://api.gumroad.com/v2'
QUERIES = (
    'OSINT research collection planning template analyst workbook',
    'business risk intelligence briefing template source evaluation workbook',
    'intelligence analysis link matrix template research workflow',
)

class ProviderError(ValueError):
    pass

def request(client, path, params=None):
    if path not in {'user', 'products', 'sales'}:
        raise ProviderError('Unsupported provider operation.')
    try:
        response = client.get(API + '/' + path, params=params or {})
        response.raise_for_status()
        if len(response.content) > 4_000_000:
            raise ProviderError('Provider response exceeds the local size limit.')
        data = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        if isinstance(exc, ProviderError):
            raise
        # Never include request headers, tokens, buyer data or response bodies.
        raise ProviderError('Gumroad request failed. Check the token, permissions and provider availability.') from None
    if not isinstance(data, dict) or data.get('success') is not True:
        raise ProviderError('Gumroad did not confirm a successful response.')
    return data

def connect(token):
    return httpx.Client(headers={'Authorization': 'Bearer ' + token},
                        timeout=25, follow_redirects=False, trust_env=False)

def verify_account(data):
    user = data.get('user')
    if not isinstance(user, dict) or not isinstance(user.get('url'), str):
        raise ProviderError('Provider did not return a verifiable account URL.')
    if user['url'].rstrip('/').lower() != STORE:
        raise ProviderError('Token does not belong to artemisbycin.gumroad.com.')
    if not isinstance(user.get('id'), str) or not user['id']:
        raise ProviderError('Provider account ID is missing.')
    return {'id': user['id'], 'url': STORE}

def next_key(data):
    direct = data.get('next_page_key')
    url = data.get('next_page_url')
    if url:
        if not isinstance(url, str):
            raise ProviderError('Invalid provider pagination.')
        p = urlsplit(url)
        if p.scheme != 'https' or p.netloc != 'api.gumroad.com' or p.path != '/v2/sales':
            raise ProviderError('Unexpected provider pagination destination.')
        parsed = parse_qs(p.query).get('page_key', [])
        if len(parsed) != 1:
            raise ProviderError('Provider continuation key is missing.')
        if direct is not None and direct != parsed[0]:
            raise ProviderError('Provider continuation keys conflict.')
        direct = parsed[0]
    if direct is not None and (not isinstance(direct, str) or not 1 <= len(direct) <= 2000):
        raise ProviderError('Invalid provider continuation key.')
    return direct

def cents(value):
    return value if type(value) is int and 0 <= value <= 10**12 else None

def sale_record(item, stamp):
    if not isinstance(item, dict) or not isinstance(item.get('id'), str) or not 1 <= len(item['id']) <= 200:
        raise ProviderError('Provider sale lacks a stable identifier.')
    price = cents(item.get('price'))
    # Missing or malformed control fields stay unresolved, never assumed false.
    flags = ('test', 'refunded', 'disputed', 'partially_refunded')
    valid_flags = all(type(item.get(k)) is bool for k in flags)
    uncertain = any(k in item and type(item[k]) is not bool
                    for k in ('partially_refunded', 'chargedback', 'chargebacked'))
    try:
        created = datetime.fromisoformat(item['created_at'].replace('Z', '+00:00'))
        if created.tzinfo is None:
            raise ValueError()
        created = created.astimezone(timezone.utc).isoformat()
    except (ValueError, KeyError, TypeError, AttributeError):
        created = None
    currency = item.get('currency')
    if currency == 'usd' and price is not None and valid_flags and not uncertain and created:
        if item['test']:
            state = 'test'
        elif item['refunded']:
            state = 'refunded'
        elif item['disputed'] or item.get('chargedback') or item.get('chargebacked'):
            state = 'disputed'
        elif item.get('partially_refunded'):
            state = 'partial_refund_review'
        elif price == 0:
            state = 'free'
        else:
            state = 'paid'
    else:
        state = 'unresolved'
    return dict(id=item['id'], product_id=str(item.get('product_id', ''))[:200],
                state=state, gross_cents=price, currency=currency if isinstance(currency,str) else None,
                provider_fee_cents=cents(item.get('gumroad_fee')), created_at=created,
                verified_at=stamp, fulfillment='Provider-hosted download; customer receipt/download not independently checked',
                attribution='unknown')

def totals(rows, since=None):
    selected = [r for r in rows if since is None or (r.get('created_at') and r['created_at'] >= since)]
    paid = [r for r in selected if r['state'] == 'paid']
    return dict(paid_orders=len(paid), confirmed_gross_usd_cents=sum(r['gross_cents'] for r in paid),
                reported_fees_usd_cents=sum(r['provider_fee_cents'] for r in paid if r['provider_fee_cents'] is not None),
                fee_coverage_complete=all(r['provider_fee_cents'] is not None for r in paid),
                excluded_records=len(selected)-len(paid), autonomous_revenue=None, profit=None,
                qualification='Saved provider records only; excludes test, free, disputed, refunded, partially refunded and unresolved records. Gross is neither profit nor payout. Acquisition attribution and download completion are unknown.')

def product_record(p):
    if not isinstance(p, dict) or not isinstance(p.get('id'), str) or not p['id']:
        raise ProviderError('Provider product identifier missing.')
    name = p.get('name')
    if not isinstance(name, str):
        raise ProviderError('Provider product name missing.')
    return dict(id=p['id'], name=name[:500], published=p.get('published') is True,
                price_cents=cents(p.get('price')), currency=p.get('currency'),
                short_url=str(p.get('short_url',''))[:500])

def toolkit():
    """Original generic templates, with one fictional worked example; no saved intel."""
    tables = {
        '01-collection-plan.csv': 'Requirement ID,Decision to support,Question,Scope,Priority,Approved source,Collection method,Owner,Due date,Stop condition\n',
        '02-source-register.csv': 'Source ID,Title,Publisher,URL,Publication date,Collection time UTC,Source type,Provenance,Independence check,Access limitations\n',
        '03-evidence-ledger.csv': 'Claim ID,Exact supporting passage,Source ID,Event date literal,Location literal,Direct observation or reported claim,Corroboration,Contradictory evidence,Uncertainty\n',
        '04-entity-register.csv': 'Entity ID,Exact name,Type,Note reference,Supporting quote,Identity decision,Analyst reason,Review status\n',
        '05-relationship-register.csv': 'From entity,Relationship,To entity,Supporting quote,Source ID,Proposed or reviewed,Target role check,Analyst caveat\n',
        '06-hypothesis-matrix.csv': 'Hypothesis,Evidence ID,Supports or weakens,Reason,Alternative explanation,Missing evidence,Confidence rationale,Review date\n',
        '07-event-timeline.csv': 'Event ID,Event date literal,Resolved full date if explicit,Collection time UTC,Location,Reported event,Source ID,Uncertainty\n',
        '08-decision-log.csv': 'Decision ID,Decision,Date,Analyst,Evidence references,Assumptions,Review trigger,Outcome\n',
    }
    guide = '''<!doctype html><html lang="en"><meta charset="utf-8"><title>CIN Studios · Analyst Workflow Toolkit</title>
<style>body{font:17px/1.65 system-ui;color:#242033;max-width:920px;margin:50px auto;padding:24px}h1,h2{color:#50327d}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:12px}@media print{body{margin:0}h2{break-after:avoid}}</style>
<h1>Analyst Workflow Toolkit</h1><p>CIN Studios · Artemis Edition · Version 1.0</p>
<p>A reusable workflow for organizing public-source research, documenting reasoning and preparing clear briefings. Includes eight editable CSV templates, a worked fictional example and briefing structure.</p>
<h2>1. Define the decision</h2><p>Start with the decision the research will support. Set geographic and time boundaries, an answerable question, approved collection sources and a stop condition. Avoid collecting information simply because it is available.</p>
<h2>2. Register sources</h2><p>Record publisher, URL, publication date and collection timestamp separately. Document original reporting versus republication. Several websites repeating one statement do not provide independent corroboration.</p>
<h2>3. Capture evidence</h2><p>Copy short relevant passages with source references. Separate reported claims from direct observations. Preserve missing dates and uncertainty; do not infer an event year from the retrieval date.</p>
<h2>4. Map entities and relationships</h2><p>Use stable entity IDs. Matching names do not establish shared identity. A relationship requires support for the actor, action and target. Shared appearance in one note establishes co-occurrence only. Keep places inside a target description separate from the target itself.</p>
<h2>5. Compare explanations</h2><p>Record evidence that supports and weakens each hypothesis. Note whether a passage is diagnostic or fits several explanations. Explain confidence in words; do not convert a count of sources into an event probability.</p>
<h2>6. Brief the decision-maker</h2><p>Lead with the development and why it matters to the defined decision. Include what changed, a short chronology, affected locations, source references, alternatives, unknowns and the next collection question. Keep collection dates separate from event dates.</p>
<h2>Worked example — entirely fictional</h2><p>Requirement: assess whether a fictional warehouse can maintain deliveries after a fictional bridge closure. Source A reports the closure; Source B repeats Source A; Source C reports a warehouse rerouting trial. These are three publications, but only two independent reporting chains are established.</p>
<ul><li>Supported: the warehouse reportedly trialed an alternative route.</li><li>Unknown: actual delivery volume, delay and cost.</li><li>Hypothesis: the route may preserve some capacity if the trial scales.</li><li>Weakening observation: dated records show the alternative route cannot carry required loads.</li><li>Next question: what measured delivery outcomes followed the trial?</li></ul>
<h2>Briefing structure</h2><ol><li>Title and reporting window</li><li>Three key developments</li><li>Chronological event highlights with sources</li><li>Assessment and competing explanations</li><li>Evidence gaps and next collection priorities</li><li>References</li></ol>
<h2>Using the files</h2><p>Open the numbered CSV files in a spreadsheet editor and save working copies. Rows begin empty so you can use your own approved material. Keep the evidence IDs consistent across files. Review quotes, identity assignments and analytical judgments before distribution.</p>
<h2>Scope and license</h2><p>This is a research organization toolkit, not a certification, legal opinion, financial recommendation or substitute for professional judgment. Use only information you are authorized to process. A purchase permits personal and internal business use and distribution of completed research outputs. Do not resell or redistribute the blank toolkit as a competing template product. Copyright CIN Studios. Review these proposed license terms before first publication.</p></html>'''
    files = {'START-HERE.html': guide, **tables,
             'README.txt': 'Open START-HERE.html for instructions. All examples are fictional. No private Artemis records are included. CSV templates are editable. Version 1.0.\n'}
    output = io.BytesIO()
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for name, body in files.items():
            # Stable bytes allow approvals to bind to a reproducible artifact.
            from zipfile import ZipInfo
            info = ZipInfo(name, (2026, 11, 27, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, body.encode('utf-8'))
    return output.getvalue()

def artifact_hash():
    return hashlib.sha256(toolkit()).hexdigest()

def launch_copy():
    return dict(title='Analyst Workflow Toolkit — Artemis Edition',
                suggested_price_usd=19, price_status='Owner decision; not market validated',
                description='Organize public-source research from collection question to professional briefing. Includes eight editable CSV templates, a practical workflow guide, and a fictional worked example. Track sources, evidence, entities, relationships, competing hypotheses and decisions. No Artemis software subscription is included.',
                promotional_draft='From a research question to a traceable briefing: the CIN Studios Analyst Workflow Toolkit brings collection planning, evidence tracking and analytical reasoning into one reusable workflow. Eight editable templates and a practical guide. Storefront: https://artemisbycin.gumroad.com/ — confirm the toolkit is published before using this draft.',
                faq=[{'question':'Does this include Artemis software?', 'answer':'No. This is a standalone set of templates and a guide.'},
                     {'question':'What software do I need?', 'answer':'A browser for the guide and a CSV-compatible spreadsheet editor for the templates.'}],
                artifact_sha256=artifact_hash(), publication_status='Draft — review product, pricing, license and listing before uploading')
