"""Explicit read-only Intel Notes commands; saved content bypasses model generation."""
import json
import re

MAX_IDS = 3
MAX_NOTE_BODY = 20000
MAX_ID = 2147483647
HELP = 'Use: Show me Intel Note 56, Read note 57, or Show me Intel Notes 56 and 57. Up to three positive numeric Intel Notes IDs are supported.'
PREFIX = r'(?:please\s+)?(?:show|read|open|get)(?:\s+me)?\s+(?:(?:the\s+)?(?:contents?|text)\s+of\s+)?(?:intel(?:ligence)?\s+)?notes?\b'


def parse_note_command(content):
    text=content.strip()
    if not text or '\n' in text or '\r' in text or text.startswith(('"',"'",'`','>')):
        return None
    command=re.match(r'^(?:'+PREFIX+r'|/intel-notes\b|/notes\b)',text,re.I)
    if not command:return None
    rest=text[command.end():].strip().rstrip('.!').strip()
    rest=re.sub(r'^ids?\s*[:#]?\s*','',rest,flags=re.I)
    rest=re.sub(r'#(?=[0-9])','',rest)
    if not re.fullmatch(r'[0-9]{1,10}(?:\s*(?:,|\band\b|&)\s*[0-9]{1,10})*',rest,flags=re.I):
        return ()
    ids=list(dict.fromkeys(int(x) for x in re.findall(r'[0-9]+',rest)))
    if not ids or len(ids)>MAX_IDS or any(i<1 or i>MAX_ID for i in ids):return ()
    return tuple(ids)


def is_note_chat_request(content):
    return parse_note_command(content) is not None


def _fence(text):
    runs=re.findall(r'`+',text)
    fence='`'*max(3,1+max((len(r) for r in runs),default=0))
    return fence+'text\n'+text+'\n'+fence


def format_note(note, requested_id):
    if not isinstance(note,dict) or type(note.get('id')) is not int or note['id']!=requested_id:
        raise ValueError('Returned note ID did not match the requested record.')
    if not isinstance(note.get('body'),str):raise ValueError('Saved note content was missing or invalid.')
    fields=[('title','Title'),('date','Stored date'),('classification','Classification'),('discipline','Discipline'),
        ('source','Source'),('reliability','Reliability'),('credibility','Credibility'),('tags','Tags')]
    lines=[]
    for key,label in fields:
        if key not in note or note[key] is None:continue
        value=note[key] if isinstance(note[key],str) else json.dumps(note[key],ensure_ascii=False)
        if len(value)>1000:value=value[:1000]+' [metadata shortened]'
        lines.append(label+': '+value)
    body=note['body']
    truncated=len(body)>MAX_NOTE_BODY
    if truncated:body=body[:MAX_NOTE_BODY]+'\n[Content shortened for chat. Open Intel Notes Manager for the complete saved note.]'
    lines.extend(['','Stored note content:',body or '[Empty saved body]'])
    return f'Intel Note {requested_id}\nRetrieved directly from Intel Notes Manager; no model summary. Stored claims have not been independently verified.\n\n'+_fence('\n'.join(lines))


def _http_status(error):
    seen=set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        response=getattr(error,'response',None)
        if response is not None:return getattr(response,'status_code',None)
        error=error.__cause__ or error.__context__
    return None


def run_note_command(content, *, connector=None):
    ids=parse_note_command(content)
    if ids is None:return None
    if not ids:return HELP
    from app.connectors.base import ConnectorError
    from app.connectors.intel_notes import IntelNotesConnector
    from app.config import get_settings

    def retrieve(client):
        answers=[]
        for note_id in ids:
            try:answers.append(format_note(client.get_note(note_id),note_id))
            except (ConnectorError,ValueError) as error:
                status=_http_status(error)
                if status==404:message='was not found in Intel Notes Manager.'
                elif status in {401,403}:message='could not be read because Intel Notes Manager denied access.'
                elif isinstance(error,ValueError):message='could not be displayed because the returned saved record failed validation.'
                else:message='could not be read. Intel Notes Manager may be unavailable or returned an error; no content was inferred.'
                answers.append(f'Intel Note {note_id} '+message)
        return '\n\n---\n\n'.join(answers)

    if connector is not None:return retrieve(connector)
    try:
        with IntelNotesConnector(base_url=str(get_settings().intel_notes_url)) as client:
            return retrieve(client)
    except ConnectorError:
        return 'Intel Notes Manager could not be reached. Start Artemis and its supporting services, then retry. No note content was inferred.'
