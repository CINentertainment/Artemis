"""Explicit reversible analyst identity assignments; original records stay intact."""
from app.services.intelligence_entity_policy import classify_name,is_population_name


def root_id(data,key):
    seen=set()
    while key in data.get('identity_assignments',{}):
        if key in seen:raise ValueError('Identity assignment cycle detected.')
        seen.add(key);key=data['identity_assignments'][key]['target']
        if key not in data['entities']:raise ValueError('Identity assignment target is missing.')
    return key


def roots(data):return {key:root_id(data,key) for key in data['entities']}


def members(data,key):
    root=root_id(data,key)
    return [e for e in data['entities'].values() if root_id(data,e['id'])==root]


def valid_mention(entity,mention):
    return (mention.get('review_status')!='rejected' and not mention.get('policy_exclusion')
        and not (is_population_name(mention.get('name',entity['name'])) and entity['kind']!='population_group'))


def option_label(entity):
    ids=sorted({m['note_id'] for m in entity['mentions']})
    notes=', '.join(str(n) for n in ids[:8])+('…' if len(ids)>8 else '')
    return f"{entity['name']} · {entity['kind'].replace('_',' ')} · Notes {notes or 'none'} · {entity['id'][:6]}"


def link_identity(data,source,target,reason):
    from app.services.intelligence_workspace import require,now
    require(source in data['entities'] and target in data['entities'] and source!=target,'Choose two distinct existing identities.')
    require(bool(reason.strip()),'Record why these mentions refer to the same identity.')
    require(source not in data.get('identity_assignments',{}),'Unlink this source identity before assigning it again.')
    target=root_id(data,target)
    require(root_id(data,source)!=target,'These identities are already linked.')
    group=members(data,source)+members(data,target)
    require(len({e['kind'] for e in group})==1,'Identity types must match. Correct the type first.')
    require(all(classify_name(e['name'],e['kind'])=='entity' for e in group),'Only eligible named identities or population groups can be linked.')
    require(any(valid_mention(data['entities'][source],m) for m in data['entities'][source]['mentions']),'Source identity has no eligible mention.')
    require(any(valid_mention(data['entities'][target],m) for m in data['entities'][target]['mentions']),'Target identity has no eligible mention.')
    assignment=dict(target=target,reason=reason.strip(),at=now(),basis='analyst_identity_judgment')
    data.setdefault('identity_assignments',{})[source]=assignment
    data.setdefault('identity_history',[]).append(dict(action='link',source=source,**assignment))


def unlink_identity(data,key):
    from app.services.intelligence_workspace import require,now
    require(key in data.get('identity_assignments',{}),'This identity has no direct assignment to undo.')
    previous=data['identity_assignments'].pop(key)
    data.setdefault('identity_history',[]).append(dict(action='unlink',source=key,previous=previous,at=now()))


def change_type(data,key,kind):
    from app.services.intelligence_workspace import require,now
    require(key in data['entities'],'Identity not found.')
    require(kind in {'person','population_group','organization','place','event'},'Unknown identity type.')
    require(len(members(data,key))==1,'Unlink this identity group before changing its type.')
    entity=data['entities'][key]
    require(classify_name(entity['name'],kind)=='entity','This name does not meet the selected identity type policy.')
    previous=entity['kind'];entity['kind']=kind
    entity.setdefault('type_history',[]).append(dict(before=previous,after=kind,at=now(),basis='analyst_correction'))
    for mention in entity['mentions']:
        # Clear only the specific type-mismatch exclusion. Other evidence exclusions stay intact.
        if mention.get('policy_exclusion')=='population_group_requires_group_type':mention.pop('policy_exclusion')


def endpoints_valid(data,link):
    return all(any(m['revision_id']==link['revision_id'] and valid_mention(entity,m)
        for entity in members(data,key) for m in entity['mentions']) for key in (link['left'],link['right']))
