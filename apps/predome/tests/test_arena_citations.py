from copy import deepcopy
import pytest
from emeris_live.arena_store import ArenaLedger
from emeris_live.arena_citations import validate_review,summaries
from test_arena_core import spec,finish


def prepared(tmp_path):
    ledger=ArenaLedger(tmp_path);target,slots=spec()
    ident=ledger.register('citations',[target],[slots[0]],{})[0]
    finish(ledger,ident,reasoning="The evidence is a fixture.")
    run=ledger.snapshot()['runs'][0]
    review={'scope':'frozen_packet_claim_source_pairs','claim_inventory_complete':False,
        'inventory_method':'Isolated fixture: selected factual claims; not a clinical assessment.',
        'run_output_sha256':run['output_sha256'],'pairs':[{'claim_id':'claim-1',
        'claim_text':'The evidence is a fixture.', 'source_id':'e1','source_sha256':'a'*64,
        'verdict':'supports','note':'Literal frozen packet statement.', 'packet_excerpt':'Fixture evidence'}]}
    return ledger,ident,run,review


def test_append_ai_review_preserves_forecast_and_bounds_denominator(tmp_path):
    ledger,ident,run,review=prepared(tmp_path)
    before=ledger.snapshot()['ledger_sha256']
    event=ledger.record_citation_review(ident,review,reviewer_id='ai-source-check',reviewer_kind='ai')
    new=ledger.snapshot()['runs'][0]
    assert new['output_sha256']==run['output_sha256'] and new['probability']==run['probability']
    assert event['previous_hash']==before
    result=new['citation_support_reviews'][0]
    assert result['assessed_pairs']==1 and result['support_fraction']==1
    assert result['claim_inventory_complete'] is False and result['human_review'] is False
    assert result['scope']=='frozen_packet_claim_source_pairs'
    assert 'not all narrative' in result['denominator_note']


@pytest.mark.parametrize('mutation',[
    lambda v:v.update(run_output_sha256='b'*64),
    lambda v:v.update(scope='all_original_documents_verified'),
    lambda v:v.update(claim_inventory_complete=None),
    lambda v:v.update(claim_inventory_complete=1),
    lambda v:v['pairs'][0].update(claim_text='Invented output statement.'),
    lambda v:v['pairs'][0].update(source_id=['e1']),
    lambda v:v.update(pairs=[]),
    lambda v:v['pairs'][0].update(source_id='not-cited'),
    lambda v:v['pairs'][0].update(source_sha256='b'*64),
    lambda v:v['pairs'][0].update(packet_excerpt='A fabricated quotation.'),
    lambda v:v['pairs'][0].update(packet_excerpt=''),
    lambda v:v['pairs'][0].update(verdict='true'),
    lambda v:v['pairs'][0].update(claim_id='../../outside'),
    lambda v:v['pairs'].append(deepcopy(v['pairs'][0])),
    lambda v:v['pairs'].append({**v['pairs'][0],'claim_id':'alias-claim'}),
])
def test_rejects_wrong_output_source_scope_anchor_or_duplicate(tmp_path,mutation):
    ledger,ident,run,review=prepared(tmp_path);before=ledger.events()
    mutation(review)
    with pytest.raises(ValueError):
        ledger.record_citation_review(ident,review,reviewer_id='ai-check',reviewer_kind='ai')
    assert ledger.events()==before


def test_corrections_append_and_do_not_increase_denominator(tmp_path):
    ledger,ident,run,review=prepared(tmp_path)
    first=ledger.record_citation_review(ident,review,reviewer_id='ai-check',reviewer_kind='ai')
    with pytest.raises(ValueError,match='latest'):
        ledger.record_citation_review(ident,review,reviewer_id='ai-check',reviewer_kind='ai')
    review['pairs'][0].update(verdict='partially_supports',note='The claim is broader than the excerpt.')
    second=ledger.record_citation_review(ident,review,reviewer_id='ai-check',reviewer_kind='ai',supersedes=first['payload']['review_sha256'])
    run=ledger.snapshot()['runs'][0]
    assert len(run['citation_reviews'])==2 and len(run['citation_support_reviews'])==1
    assert run['citation_support_reviews'][0]['assessed_pairs']==1
    assert run['citation_support_reviews'][0]['support_fraction']==0
    assert second['payload']['supersedes']==first['payload']['review_sha256']
    with pytest.raises(ValueError,match='Identical'):
        ledger.record_citation_review(ident,review,reviewer_id='ai-check',reviewer_kind='ai',supersedes=second['payload']['review_sha256'])


def test_human_and_ai_declared_reviews_are_separate(tmp_path):
    ledger,ident,run,review=prepared(tmp_path)
    for kind in ('ai','human'):
        ledger.record_citation_review(ident,review,reviewer_id='declared-'+kind,reviewer_kind=kind)
    views=ledger.snapshot()['runs'][0]['citation_support_reviews']
    assert {r['reviewer_kind'] for r in views}=={'ai','human'}
    assert sum(r['human_review'] for r in views)==1
    assert all(r['clinical_prediction_validated'] is False for r in views)


def test_a_failed_or_pending_run_cannot_have_semantic_support_score(tmp_path):
    ledger,ident,run,review=prepared(tmp_path)
    target,slots=spec();target['id']='pending-target';slots=slots[:1];slots[0]['target_id']=target['id']
    pending=ledger.register('pending',[target],slots,{})[0]
    with pytest.raises(ValueError,match='completed'):
        ledger.record_citation_review(pending,review,reviewer_id='ai',reviewer_kind='ai')
    ledger.start(pending);ledger.finish(pending,{'status':'failed','probability':None})
    with pytest.raises(ValueError,match='narratives'):
        ledger.record_citation_review(pending,review,reviewer_id='ai',reviewer_kind='ai')
