"""Encrypted, revision-aware analyst boards. Co-mentions are not relationships."""
import hashlib
import json
from datetime import datetime, timezone
from itertools import combinations
from uuid import uuid4

from sqlalchemy import Integer, String, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.services.encryption import protect_text, unprotect_text


class IntelligenceBoard(Base):
    __tablename__ = 'intelligence_boards'
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[str] = mapped_column(Text, nullable=False)


def now():
    return datetime.now(timezone.utc).isoformat()


def uid():
    return uuid4().hex


def require(value, message):
    if not value:
        raise ValueError(message)


def create_board(database, title):
    data = dict(id=uid(), title=title.strip(), notes={}, entities={}, links=[], history=[])
    require(data['title'], 'Enter a board title.')
    row = IntelligenceBoard(id=data['id'], version=1, payload=protect_text(json.dumps(data)))
    database.add(row)
    database.commit()
    return dict(data, version=1)


def read_board(database, board_id):
    row = database.get(IntelligenceBoard, board_id)
    if row is None:
        raise LookupError('Board not found.')
    return dict(json.loads(unprotect_text(row.payload)), version=row.version)


def save_board(database, data, action):
    expected = data.pop('version')
    data['history'].append(dict(at=now(), action=action))
    result = database.execute(update(IntelligenceBoard).where(
        IntelligenceBoard.id == data['id'], IntelligenceBoard.version == expected
    ).values(version=expected + 1, payload=protect_text(json.dumps(data, ensure_ascii=False))))
    if result.rowcount != 1:
        database.rollback()
        raise ValueError('Board changed in another request. Refresh and retry.')
    database.commit()
    return dict(data, version=expected + 1)


def import_note(data, note, requested_id):
    require(type(note.get('id')) is int and note['id'] == requested_id, 'Note ID mismatch.')
    require(isinstance(note.get('body'), str), 'Note body is missing.')
    require(len(note['body']) <= 200000, 'Note is too large for this workspace (200,000 characters).')
    # Snapshot the record, including its stored source block; never infer event dates.
    captured = {key: note.get(key) for key in ('id', 'title', 'body', 'date', 'source', 'classification', 'discipline', 'tags')}
    fingerprint = hashlib.sha256(json.dumps(captured, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    require(str(requested_id) in data['notes'] or len(data['notes']) < 200, 'This board has reached its 200-note limit. Create another board.')
    entry = data['notes'].setdefault(str(requested_id), dict(status='new', revisions=[]))
    if entry['revisions'] and entry['revisions'][-1]['hash'] == fingerprint:
        return False
    entry['revisions'].append(dict(id=uid(), hash=fingerprint, captured_at=now(), record=captured))
    entry['status'] = 'new'
    return True


def snapshot(data, note_id):
    require(str(note_id) in data['notes'], 'Import this note first.')
    return data['notes'][str(note_id)]['revisions'][-1]


def add_entity(data, note_id, name, kind, quote, origin='analyst', entity_id=''):
    snap = snapshot(data, note_id)
    from app.services.intelligence_entity_policy import classify_name, grounded
    require(classify_name(name,kind)=='entity', 'Use a named person, population group, organization, place, or explicitly named event. Dates and unnamed facilities belong in Observations.')
    require(grounded(snap['record']['body'],name,quote), 'Quote must come from narrative evidence, not a title, URL, or internal metadata.')
    require(len(name) <= 200 and len(quote) <= 5000, 'Entity name or quote is too long.')
    require(name.strip() and name == name.strip(), 'Use an exact entity name without outside spaces.')
    require(name in quote and quote in snap['record']['body'], 'Entity and quote must be exact spans from the stored note.')
    require(kind in {'person', 'population_group', 'organization', 'place', 'event', 'other'}, 'Unknown entity type.')
    require(not entity_id or entity_id in data['entities'], 'Entity not found.')
    entity = data['entities'].get(entity_id) if entity_id else next((e for e in data['entities'].values()
        if e['name'] == name and e['kind'] == kind and any(m['revision_id'] == snap['id'] for m in e['mentions'])), None)
    if entity is not None:
        require(entity['kind'] == kind, 'Existing entity type must match.')
    if entity is None:
        require(len(data['entities']) < 100, 'This board has reached its 100-entity limit.')
        entity = dict(id=uid(), name=name, kind=kind, mentions=[])
        data['entities'][entity['id']] = entity
    mention = dict(note_id=note_id, revision_id=snap['id'], quote=quote, origin=origin, name=name)
    if not any(all(m.get(k)==v for k,v in mention.items()) for m in entity['mentions']):
        entity['mentions'].append(mention)
    return entity['id']


def add_link(data, left, right, label, note_id, quote):
    require(left != right and left in data['entities'] and right in data['entities'], 'Choose two different entities.')
    from app.services.intelligence_identities import root_id
    require(root_id(data,left)!=root_id(data,right),'These endpoints are the same analyst-linked identity.')
    snap = snapshot(data, note_id)
    require(bool(quote.strip()) and quote in snap['record']['body'], 'Relationship evidence must be copied exactly from this note.')
    for key in (left, right):
        from app.services.intelligence_identities import members,valid_mention
        require(any(m['revision_id']==snap['id'] and valid_mention(entity,m) for entity in members(data,key) for m in entity['mentions']), 'Both entities need a mention in the current note revision.')
    require(label.strip(), 'Enter a relationship description.')
    data['links'].append(dict(id=uid(), left=left, right=right, label=label.strip(), note_id=note_id,
        revision_id=snap['id'], quote=quote, status='proposed', created_at=now()))


def matrix(data):
    """One count per shared current, non-archived note, regardless of mention count."""
    from app.services.intelligence_identities import roots,valid_mention
    mapping=roots(data)
    active = {k: v['revisions'][-1]['id'] for k, v in data['notes'].items() if v['status'] != 'archived'}
    pairs = {}
    for note_id, revision_id in active.items():
        ids = sorted({mapping[e['id']] for e in data['entities'].values() if any(m['revision_id']==revision_id and valid_mention(e,m) for m in e['mentions'])})
        for a, b in combinations(ids, 2):
            pairs.setdefault((a, b), []).append(int(note_id))
    return [dict(left=a, right=b, shared_note_ids=ids, count=len(ids)) for (a,b), ids in pairs.items()]


def view(data):
    from app.services.intelligence_presentation import present
    from app.services.intelligence_relationship_policy import flags
    from app.services.intelligence_identities import roots,root_id,endpoints_valid,option_label,is_population_name
    entity_data={key:dict(e,option_label=option_label(e),type_review_needed=is_population_name(e['name']) and e['kind']!='population_group') for key,e in data['entities'].items()}
    current = {n['revisions'][-1]['id'] for n in data['notes'].values()}
    return dict(data, entities=entity_data, identity_roots=roots(data), presentation=present(data), matrix=matrix(data),
        links=[dict(link,stale=link['revision_id'] not in current,endpoint_review_needed=not endpoints_valid(data,link),
            identity_self_link=root_id(data,link['left'])==root_id(data,link['right']),**flags(data,link)) for link in data['links']])



def propose_entities(body, runtime):
    # Reuse the narrative-only model contract for both scan entry points.
    from app.services.research_workspace_bridge import propose_candidates
    return propose_candidates(body,runtime)['entities']
