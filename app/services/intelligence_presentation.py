"""Read-only grouping and exact source-sentence display. Stored evidence remains unchanged."""
import re
from collections import Counter
from app.services.intelligence_entity_policy import narrative_spans


def sentence_spans(text):
    """Conservative punctuation boundaries; offsets always refer to unchanged source text."""
    start=0
    for match in re.finditer(r'[.!?][”’\"\']*(?=\s|$)',text):
        prefix=text[:match.start()+1]
        if re.search(r'\b(?:Mr|Mrs|Ms|Dr|Prof|Inc|No|vs|e\.g|i\.e|U\.S)\.$',prefix,re.I):
            continue
        end=match.end()
        if text[start:end].strip():yield start,end,text[start:end].strip()
        start=end
    if text[start:].strip():yield start,len(text),text[start:].strip()


def source_sentences(body,literal,quotes):
    """Show complete sentences containing the literal within each original quote's source scope.

    If no narrative match can be established, return no replacement. The UI retains
    the original quote, explicitly labelled as such, rather than inventing context.
    """
    output=[]
    for quote in quotes:
        if not isinstance(quote,str) or not quote:continue
        for span in narrative_spans(body):
            offset=0
            while True:
                index=span.find(quote,offset)
                if index<0:break
                end=index+len(quote)
                for a,b,sentence in sentence_spans(span):
                    if a<end and b>index and literal in sentence and sentence not in output:
                        output.append(sentence)
                offset=index+max(1,len(quote))
    return output


def observation_label(kind,text):
    if kind=='infrastructure':
        # Remove only an indefinite article. Do not equate local/general/central,
        # damaged/undamaged, named facilities, or definite backward references.
        return re.sub(r'^(?:a|an)\s+','',text.strip(),flags=re.I)
    return text.strip()


def present(data):
    result={'entities':{},'observation_groups':[]}
    for entity in data['entities'].values():
        mentions=[]
        for mention in entity['mentions']:
            note=data['notes'].get(str(mention['note_id']))
            snap=next((r for r in note['revisions'] if r['id']==mention['revision_id']),None) if note else None
            quotes=source_sentences(snap['record']['body'],mention.get('name',entity['name']),[mention['quote']]) if snap else []
            mentions.append(dict(mention,display_quotes=quotes))
        result['entities'][entity['id']]=dict(entity,mentions=mentions)
    grouped={}
    for observation in data.get('observations',[]):
        label=observation_label(observation['kind'],observation['text'])
        key=(observation['note_id'],observation['revision_id'],observation['kind'],re.sub(r'\s+',' ',label).casefold())
        if key not in grouped:
            grouped[key]=dict(note_id=observation['note_id'],revision_id=observation['revision_id'],kind=observation['kind'],label=label,
                unresolved_reference=observation['kind']=='infrastructure' and bool(re.match(r'^the\s+',label,re.I)),members=[],display_quotes=[])
        group=grouped[key];group['members'].append(observation)
        note=data['notes'].get(str(observation['note_id']))
        snap=next((r for r in note['revisions'] if r['id']==observation['revision_id']),None) if note else None
        if snap:
            quotes=source_sentences(snap['record']['body'],observation['text'],observation.get('quotes') or [observation['quote']])
            for quote in quotes:
                if quote not in group['display_quotes']:group['display_quotes'].append(quote)
    for group in grouped.values():
        group['review_counts']=dict(Counter(m.get('review_status','pending') for m in group['members']))
        result['observation_groups'].append(group)
    from app.services.intelligence_identities import roots,option_label,is_population_name
    mapping=roots(data);groups={}
    for key,entity in result['entities'].items():
        canonical=mapping[key]
        if canonical not in groups:
            groups[canonical]=dict(result['entities'][canonical],mentions=[],member_ids=[],aliases=[])
        group=groups[canonical];group['member_ids'].append(key)
        for name in [entity['name']]+[m.get('name',entity['name']) for m in entity['mentions']]:
            if name not in group['aliases']:group['aliases'].append(name)
        group['mentions'].extend(dict(m,entity_id=key) for m in entity['mentions'])
    for group in groups.values():
        group['option_label']=option_label(group)
        group['type_review_needed']=is_population_name(group['name']) and group['kind']!='population_group'
    result['entities']=groups
    return result
