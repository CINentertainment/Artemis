"""Conservative source-span validation for simple active relationships.

This is a bounded grammar, not a general semantic parser or truth verifier.
Complex/uncertain scope is withheld rather than assigned guessed roles.
"""
import re
from app.services.intelligence_entity_policy import grounded,narrative_spans
from app.services.intelligence_presentation import sentence_spans

VERBS=(r'attacked|attacks|attack|supports|supported|support|funds|funded|fund|owns|owned|own|'
       r'operates|operated|operate|supplies|supplied|supply|evacuated|evacuates|evacuate|'
       r'damaged|damages|damage|destroyed|destroys|destroy|struck|strikes|'
       r'launched strikes on|launched attacks on|conducted strikes on|conducted attacks on')
ACTION=r'(?:(?:reportedly|allegedly)\s+)?(?:(?:has|have|had)\s+)?(?:'+VERBS+r')'
UNSAFE=re.compile(r'\b(?:not|never|denied|denies|deny|false|falsely|allegations?|unconfirmed|rumou?rs?|may|might|could|would|will|plans?|planned|if|whether|unless|possibly|claims?|claimed|claiming|accused|accuses)\b',re.I)


def validate(body,subject,target,label,quote,location=''):
    if not all(isinstance(v,str) for v in (subject,target,label,quote,location)):
        raise ValueError('Relationship fields must be text.')
    if not grounded(body,subject,quote) or not grounded(body,target,quote):
        raise ValueError('Relationship endpoints must come from the quoted narrative evidence.')
    if subject==target:
        raise ValueError('Relationship endpoints must differ.')
    pattern=re.compile(r'(?<!\w)'+re.escape(subject)+r'\s+(?P<action>'+ACTION+r')\s+'+re.escape(target)+r'(?!\w)',re.I)
    for span in narrative_spans(body):
        if quote not in span:continue
        for _,_,sentence in sentence_spans(span):
            # The proposed action must exist in a complete available source sentence,
            # not just in an extracted fragment which hid negation or attribution.
            for match in pattern.finditer(sentence):
                if match.group() not in quote:continue
                if UNSAFE.search(sentence) or '?' in sentence:continue
                prefix=sentence[:match.start()].strip()
                if prefix:
                    # Allow an explicit source date/location lead-in, but no reporting,
                    # denial, quotation or nested-clause lead-in.
                    if not re.fullmatch(r'(?:On|In|At|During)\s+[^,;.!?]{1,100},',prefix,re.I):continue
                    if re.search(r'\b(?:report|statement|account|claim|scenario|hypothesis|said|says|according)\b',prefix,re.I):continue
                tail=sentence[match.end():]
                if re.match(r"[’']s\b",tail):continue
                if tail.strip() and not re.match(r'^(?:\s*[,;.!?:]|\s+(?:in|near|at|on|during|after|before|with|using|reportedly)\b)',tail,re.I):continue
                # Relative/complement targets and coordinated objects need a richer parser.
                if re.match(r'\s+(?:and|or|that|which|who|where)\b',tail,re.I):continue
                action=match.group('action')
                if label.strip()!=action:
                    raise ValueError('Relationship label must copy the complete source action phrase, including any uncertainty wording.')
                if location:
                    if location==target:
                        raise ValueError('A location qualifier must be separate from the direct target.')
                    if not grounded(body,location,quote) or not re.search(r'\b(?:in|near|at)\s+'+re.escape(location)+r'(?!\w)',tail,re.I):
                        raise ValueError('Location must be an explicit in/near/at qualifier after the direct target in the same source sentence.')
                return dict(action_span=action,target_span=target,location_span=location or None,source_sentence=sentence,
                    role_validation='bounded_active_clause_v1',role_validation_limit='Text-role eligibility only; not source truth or independent verification.')
    raise ValueError('No supported direct actor/action/target clause. A location inside the target description must not replace the target; complex, negated or uncertain clauses need analyst review.')


def flags(data,link):
    if link.get('origin')!='model_candidate':return {}
    note=data['notes'].get(str(link['note_id']))
    snap=next((r for r in note['revisions'] if r['id']==link['revision_id']),None) if note else None
    if snap is None:return dict(role_review_needed=True,role_review_reason='Source snapshot unavailable.')
    try:
        checked=validate(snap['record']['body'],data['entities'][link['left']]['name'],data['entities'][link['right']]['name'],
            link['label'],link['quote'],link.get('location_span') or '')
        return dict(role_review_needed=False,role_source_sentence=checked['source_sentence'])
    except ValueError as error:
        return dict(role_review_needed=True,role_review_reason=str(error))
