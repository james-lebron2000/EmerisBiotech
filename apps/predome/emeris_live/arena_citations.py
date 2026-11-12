"""Source-bound citation assessments; AI and human reviews remain distinguishable.

The denominator is the declared set of reviewed claim-source pairs. It is never
called the accuracy of an entire narrative or a clinical result. A review of the
frozen packet cannot silently claim that a full source document was checked.
"""
import re
from .arena_store import sha

VERSION = 'citation-assessment-1.0'
VERDICTS = ('supports', 'partially_supports', 'contradicts', 'not_supported', 'unverifiable')

def _bounded(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError('Missing or unbounded ' + name)
    return value.strip()


def validate_review(run, review):
    if run.get('status') not in ('predicted', 'abstained'):
        raise ValueError('Only completed model narratives can be citation-reviewed')
    if review.get('scope') != 'frozen_packet_claim_source_pairs':
        raise ValueError('Declare the exact frozen-packet review scope')
    if type(review.get('claim_inventory_complete')) is not bool:
        raise ValueError('Explicit declared claim-inventory completeness required')
    if review.get('run_output_sha256') != run.get('output_sha256'):
        raise ValueError('Review must bind the exact completed output')
    evidence = {e['id']: e for e in run['packet']['evidence']}
    cited = set(run.get('evidence_ids', []))
    pairs = review.get('pairs')
    if not isinstance(pairs, list) or not 1 <= len(pairs) <= 100:
        raise ValueError('One to 100 explicit claim-source pairs required')
    narrative='\n'.join(str(v) for v in [run.get('reasoning',''),run.get('reason',''),
        *run.get('key_assumptions',[]),*run.get('key_unknowns',[])])
    seen, semantic_seen, claims = set(), set(), {}
    clean = []
    for item in pairs:
        if not isinstance(item, dict):
            raise ValueError('Citation assessment must be an object')
        claim_id = _bounded(item.get('claim_id'), 'claim ID', 80)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', claim_id):
            raise ValueError('Invalid claim identifier')
        claim = _bounded(item.get('claim_text'), 'claim text', 2000)
        if claim_id in claims and claims[claim_id] != claim:
            raise ValueError('A claim ID must refer to one stable statement')
        if claim not in narrative:
            raise ValueError('Reviewed claim is not a literal statement in the frozen output')
        claims[claim_id] = claim
        source_id = item.get('source_id')
        if not isinstance(source_id,str) or source_id not in cited or source_id not in evidence:
            raise ValueError("Assessment must use the frozen cited sources of this run")
        key = (claim_id, source_id)
        if key in seen:
            raise ValueError('Duplicate claim-source assessment')
        seen.add(key)
        source = evidence[source_id]
        semantic_key=(sha(claim),source['source_sha256'])
        if semantic_key in semantic_seen:
            raise ValueError('Duplicate statement and source content under another identifier')
        semantic_seen.add(semantic_key)
        if item.get('source_sha256') != source['source_sha256']:
            raise ValueError('Reviewed source version differs from the frozen source')
        verdict = item.get('verdict')
        if verdict not in VERDICTS:
            raise ValueError('Unknown citation support verdict')
        note = _bounded(item.get('note'), 'concise assessment note', 1500)
        excerpt = item.get('packet_excerpt')
        if not isinstance(excerpt, str) or len(excerpt) > 1200:
            raise ValueError('Bounded literal packet excerpt required')
        if excerpt and excerpt not in source['text']:
            raise ValueError('Assessment excerpt is not present in the frozen packet')
        if verdict == 'supports' and not excerpt:
            raise ValueError('A support assessment requires a literal packet anchor')
        clean.append({'claim_id':claim_id,'claim_text':claim,'claim_sha256':sha(claim),
            'source_id':source_id,'source_sha256':source['source_sha256'],
            'verdict':verdict,'note':note,'packet_excerpt':excerpt})
    return {'version':VERSION,'scope':review['scope'],
        'run_output_sha256':review['run_output_sha256'],
        'claim_inventory_complete':review['claim_inventory_complete'],
        'inventory_method':_bounded(review.get('inventory_method'),'inventory method',1000),
        'pairs':clean,'clinical_prediction_validated':False}


def summaries(reviews):
    """Latest version per reviewer; corrections never become extra observations."""
    latest={}
    for review in reviews:
        latest[(review['reviewer_id'],review['reviewer_kind'])]=review
    result=[]
    for key, review in sorted(latest.items()):
        pairs=review['assessment']['pairs']
        counts={v:sum(p['verdict']==v for p in pairs) for v in VERDICTS}
        result.append({'reviewer_id':key[0],'reviewer_kind':key[1],
            'review_sha256':review['review_sha256'],'assessed_pairs':len(pairs),
            'assessed_claims':len({p['claim_id'] for p in pairs}),
            'supported_pairs':counts['supports'],'support_fraction':counts['supports']/len(pairs),
            'verdict_counts':counts,'claim_inventory_complete':review['assessment']['claim_inventory_complete'],
            'scope':'frozen_packet_claim_source_pairs','reviewed_at':review.get('reviewed_at'),
            'denominator_note':'Reviewed declared claim-source pairs only; not all narrative claims or original-source truth.',
            'human_review':key[1]=='human','clinical_prediction_validated':False})
    return result


def aggregate_runs(runs):
    result=[]
    for kind in ('ai','human'):
        reviewed=[]
        for run in runs:
            for entry in summaries(run.get('citation_reviews',[])):
                if entry['reviewer_kind']==kind:
                    reviewed.append((run['id'],entry))
        denominator=sum(e['assessed_pairs'] for _,e in reviewed)
        numerator=sum(e['supported_pairs'] for _,e in reviewed)
        result.append({'reviewer_kind':kind,'review_assessments':len(reviewed),
            'reviewed_runs':len({ident for ident,_ in reviewed}),'assigned_runs':len(runs),
            'assessed_pairs':denominator,'supported_pairs':numerator,
            'support_fraction':numerator/denominator if denominator else None,
            'scope':'frozen_packet_claim_source_pairs',
            'denominator_note':'Each reviewer assessment of a declared claim-source pair; not whole-output accuracy.',
            'clinical_prediction_validated':False})
    return result
