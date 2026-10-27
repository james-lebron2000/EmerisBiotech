"""Actual temporary Arena ledgers exercise the public comparison boundary.

All trial, patent and model values here are synthetic. These tests neither call
models nor establish source truth, equal actual compute or clinical performance.
"""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import hashlib
from io import BytesIO
import json
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public
from emeris_live.arena_store import ArenaLedger, sha, result_target_binding
from arena_scientific_fixture import scientifically_accept
from test_arena_core import spec


CONFIG = 'b' * 64
PROMPT = 'c' * 64
BUDGET = 'd' * 64
CANARY = 'SYNTHETIC_PRIVATE_COMPARISON_CANARY'


def slot_for(target, packet, model='A', *, track='same_evidence', condition='clinical_only'):
    return {'target_id': target['id'], 'model_id': model, 'requested_model': model+'-fixed',
            'track': track, 'condition': condition, 'packet': deepcopy(packet),
            'workflow_version': 'synthetic-provider-1:single_pass',
            'provider': 'synthetic-offline-provider',
            'provider_config_snapshot': {'config_sha256': CONFIG, 'internal_note': CANARY},
            'expected_provider_input_sha256': sha(packet), 'expected_prompt_sha256': PROMPT,
            'execution_budget_sha256': BUDGET}


def patent_card(target):
    return {'id': 'synthetic-patent', 'kind': 'patent', 'title': 'Synthetic patent experiment',
            'text': 'Synthetic evidence card, not a real experiment. '+CANARY,
            'source_url': 'https://example.org/synthetic-patent', 'source_sha256': 'e'*64,
            'available_at': target['information_cutoff_utc'], 'mapping_status': 'verified',
            'review_status': 'verified', 'page': 7, 'page_references': [7, 8],
            'source_anchor': 'Synthetic Example 2', 'document_version': 'Synthetic v1',
            'review_receipt': 'f'*64, 'section_type': 'reported_experiment'}


def complete(ledger, ident, slot, *, status='predicted', value=.71, returned=True):
    if status == 'pending':
        return
    ledger.start(ident)
    result = {'status': status, 'probability': value if status == 'predicted' else None,
              'evidence_ids': [card['id'] for card in slot['packet']['evidence']],
              'reasoning': 'Synthetic concise public evidence summary.',
              'config_sha256': slot.get('provider_config_snapshot', {}).get('config_sha256'),
              'prompt_sha256': slot.get('expected_prompt_sha256'),
              'input_sha256': slot.get('expected_provider_input_sha256'),
              'prompt': CANARY, 'usage': {'private': CANARY},
              'attempts': [{'private': CANARY}], 'internal_analysis': CANARY,
              'cost_usd': .123456789}
    if returned:
        result['returned_model'] = returned if isinstance(returned, str) else slot['requested_model']
    if status == 'failed':
        result['error_code'] = 'synthetic_private_error'
    ledger.finish(ident, result)


@pytest.fixture
def factory(tmp_path, monkeypatch):
    """The only replacements isolate unrelated archives and source assets."""
    monkeypatch.setattr(public, 'ROOT', tmp_path/'absent-public-source-root')
    monkeypatch.setattr(public, 'read_archived', lambda store: ([], [], {}))
    count = 0

    def build(*, baseline_status='predicted', patent_status='predicted', baseline_value=.3,
              patent_value=.71, baseline_returned=True, patent_returned=True,
              mutate=None, include_baseline=True, agents=False, agent_mutate=None):
        nonlocal count
        count += 1
        root = tmp_path/('runtime-'+str(count))
        ledger = ArenaLedger(root)
        target, raw_slots = spec()
        target.update(trial_name='SYNTHETIC comparison trial', drug='SYNTHETIC intervention',
                      scope='prospective_challenge', readout={'label':'Readout unconfirmed'})
        packet = raw_slots[0]['packet']
        packet['evidence'][0].update(title='Synthetic clinical source', source_anchor='Synthetic section 1')
        control = slot_for(target, packet)
        treated_packet = deepcopy(packet)
        treated_packet['evidence'].append(patent_card(target))
        treated = slot_for(target, treated_packet, track='patent_ablation', condition='with_patents')
        if mutate:
            mutate(control, treated)
        # Match the frozen provider input to each final synthetic packet. This
        # remains a real registration/completion, not an altered ledger file.
        for slot in (control, treated):
            if 'expected_provider_input_sha256' in slot:
                slot['expected_provider_input_sha256'] = sha(slot['packet'])
        slots = ([control] if include_baseline else []) + [treated]
        if agents:
            shared = slot_for(target, packet, track='shared_agent')
            shared['workflow_version'] = 'synthetic-provider-1:evidence_review'
            shared['expected_prompt_sha256'] = '1'*64
            agent = slot_for(target, packet, model='synthetic-agent', track='open_agent')
            agent.update(workflow_version='synthetic-agent-1:frozen_lookup_review_forecast',
                requested_model='synthetic-agent:requested:declared-identity',
                agent_spec={'agent_id':'synthetic-agent','version':'synthetic-agent-1',
                    'tier':'synthetic-two-call','reviewer_model_id':'B','forecaster_model_id':'A',
                    'tool_allowlist':['frozen_evidence.list','frozen_evidence.lookup'],'max_tool_calls':101},
                agent_contract={'components':[
                    {'role':'evidence_review','id':'B','requested_model':'B-fixed','private':CANARY},
                    {'role':'forecast','id':'A','requested_model':'A-fixed','private':CANARY}],
                    'private':CANARY})
            if agent_mutate:
                agent_mutate(shared,agent)
            slots.extend((shared, agent))
        ids = ledger.register('synthetic-comparison-cohort', [target], slots,
            {'season_kind':'registered_12_project_season','private':CANARY})
        lookup = {slot['track']: (ident, slot) for ident, slot in zip(ids, slots)}
        if include_baseline:
            complete(ledger, *lookup['same_evidence'], status=baseline_status,
                     value=baseline_value, returned=baseline_returned)
        complete(ledger, *lookup['patent_ablation'], status=patent_status,
                 value=patent_value, returned=patent_returned)
        if agents:
            complete(ledger, *lookup['shared_agent'], value=.2)
            complete(ledger, *lookup['open_agent'], status='failed', returned=False)
        app = FastAPI()
        app.state.store = SimpleNamespace(path=root)
        app.include_router(public.router)
        return SimpleNamespace(ledger=ledger, target=target, slots=slots, ids=ids,
                               lookup=lookup, client=TestClient(app), root=root)
    return build


def response_for(sample):
    response = sample.client.get('/api/arena/public/targets/'+sample.target['id']+'/comparisons')
    assert response.status_code == 200, response.text
    return response.json()


def keys(value):
    if isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from keys(item)


def test_real_public_comparison_preserves_assigned_arms_sources_and_existing_14_record(factory):
    sample=factory(agents=True)
    path='/api/arena/public/targets/'+sample.target['id']
    before=sample.ledger.events()
    original=sample.client.get(path+'/record').json()
    digest=hashlib.sha256(sample.ledger.path.read_bytes()).hexdigest()
    result=response_for(sample)
    assert set(result)=={'schema_version','target_id','patent_comparisons','agent_comparisons','public_policy'}
    assert result['schema_version']=='predome-public-trial-comparisons-1'
    assert result['target_id']==sample.target['id']
    assert len(result['patent_comparisons'])==1 and len(result['agent_comparisons'])==2
    pair=result['patent_comparisons'][0]
    assert set(pair)=={'control','with_patents','registration_matched','model_version_matched',
        'comparison_status','judgment_changed','reasons','added_sources'}
    assert pair['control']['id']==sample.lookup['same_evidence'][0]
    assert pair['with_patents']['id']==sample.lookup['patent_ablation'][0]
    assert pair['registration_matched'] is True and pair['model_version_matched'] is True
    assert pair['comparison_status']=='comparable_judgments' and pair['judgment_changed'] is True
    assert pair['control']['predicted_outcome']=='Failure'
    assert pair['with_patents']['predicted_outcome']=='Success'
    assert {source['id'] for source in pair['control']['sources']}=={'e1'}
    assert {source['id'] for source in pair['with_patents']['sources']}=={'e1','synthetic-patent'}
    assert [source['id'] for source in pair['added_sources']]==['synthetic-patent']
    assert pair['added_sources'][0]['page_references']==[7,8]
    agents={row['workflow']:row for row in result['agent_comparisons']}
    for row in agents.values():
        assert set(row)=={'run','workflow','baseline_run_ids','clinical_input_matched',
                         'common_budget_cap','component_models','reasons'}
        assert row['baseline_run_ids']==[sample.lookup['same_evidence'][0]]
        assert row['clinical_input_matched'] is True and row['common_budget_cap'] is True
    assert agents['shared_agent']['run']['status']=='predicted'
    assert agents['open_agent']['run']['status']=='failed'
    assert agents['open_agent']['component_models']==[
        {'role':'evidence_review','requested_model':'B-fixed'},
        {'role':'forecast','requested_model':'A-fixed'}]
    encoded=json.dumps(result)
    assert CANARY not in encoded and 'synthetic_private_error' not in encoded
    assert ': 0.71' not in encoded and '.123456789' not in encoded
    assert not (set(keys(result)) & {'packet','prompt','reasoning','usage','cost_usd','attempts',
        'config_sha256','input_sha256','expected_prompt_sha256','agent_contract','internal_analysis',
        'probability_change','accuracy','score','gain','brier'})
    for run in (pair['control'],pair['with_patents'],*(row['run'] for row in agents.values())):
        assert run['probability'] is None and run['confidence'] is None
        assert run['match'] is None and run['match_status']=='not_evaluable'
    assert result['public_policy']['clinical_prediction_validated'] is False
    assert sample.client.get(path+'/record').json()==original
    assert original['version']=='arena-clinical-public-1.4'
    assert [r['track'] for r in original['runs']]==['same_evidence']
    response=sample.client.get(path+'/brief/download')
    assert response.status_code==200
    with zipfile.ZipFile(BytesIO(response.content)) as z:
        assert json.loads(z.read('clinical-record.json'))==original
    assert sample.ledger.events()==before
    assert hashlib.sha256(sample.ledger.path.read_bytes()).hexdigest()==digest


@pytest.mark.parametrize('status',['failed','pending'])
@pytest.mark.parametrize('arm',['control','with_patents'])
def test_incomplete_assignment_never_gets_a_changed_judgment(factory,status,arm):
    sample=factory(**{('baseline_status' if arm=='control' else 'patent_status'):status})
    pair=response_for(sample)['patent_comparisons'][0]
    assert pair[arm]['status']==status
    assert pair['comparison_status']!='comparable_judgments'
    assert pair['judgment_changed'] is None
    assert 'incomplete_judgments' in pair['reasons']


@pytest.mark.parametrize('values,expected',[(('.5','.5'),False),(('.3','.3'),False),(('.3','.71'),True)])
def test_returned_tied_and_directional_outputs_use_categorical_judgments_only(factory,values,expected):
    sample=factory(baseline_value=float(values[0]),patent_value=float(values[1]))
    pair=response_for(sample)['patent_comparisons'][0]
    assert pair['comparison_status']=='comparable_judgments'
    assert pair['judgment_changed'] is expected
    assert pair['control']['probability'] is pair['with_patents']['probability'] is None
    if values[0]=='.5':assert pair['control']['predicted_outcome']=='Uncertain'


def test_explicit_abstention_is_retained_as_uncertain_without_a_probability(factory):
    sample=factory(baseline_status='abstained',patent_status='abstained')
    pair=response_for(sample)['patent_comparisons'][0]
    assert pair['comparison_status']=='comparable_judgments'
    assert pair['judgment_changed'] is False
    assert pair['control']['predicted_outcome']==pair['with_patents']['predicted_outcome']=='Uncertain'


@pytest.mark.parametrize('field', ['config','budget','prompt','workflow','clinical_text','source_hash'])
def test_real_registration_differences_block_a_patent_comparability_claim(factory,field):
    def mutate(control, treated):
        if field=='config':treated['provider_config_snapshot']['config_sha256']='9'*64
        elif field=='budget':treated['execution_budget_sha256']='9'*64
        elif field=='prompt':treated['expected_prompt_sha256']='9'*64
        elif field=='workflow':treated['workflow_version']='synthetic-provider-2:single_pass'
        elif field=='clinical_text':treated['packet']['evidence'][0]['text']='Changed synthetic clinical facts.'
        elif field=='source_hash':treated['packet']['evidence'][0]['source_sha256']='9'*64
    pair=response_for(factory(mutate=mutate))['patent_comparisons'][0]
    assert pair['registration_matched'] is False
    assert pair['comparison_status']=='not_comparable' and pair['judgment_changed'] is None
    assert ('clinical_packet_changed' if field in ('clinical_text','source_hash')
            else 'registration_metadata_unverified') in pair['reasons']


@pytest.mark.parametrize('field',['config','budget','prompt'])
def test_both_missing_registration_values_are_not_treated_as_matching(factory,field):
    def mutate(control, treated):
        for slot in (control,treated):
            if field=='config':slot['provider_config_snapshot'].pop('config_sha256')
            else:slot.pop('execution_budget_sha256' if field=='budget' else 'expected_prompt_sha256')
    pair=response_for(factory(mutate=mutate))['patent_comparisons'][0]
    assert pair['registration_matched'] is False and pair['judgment_changed'] is None
    assert 'registration_metadata_unverified' in pair['reasons']


@pytest.mark.parametrize('returned,reason',[(False,'model_version_unconfirmed'),('A-other-version','model_version_changed')])
def test_unknown_or_changed_returned_identity_keeps_row_without_fair_pair(factory,returned,reason):
    pair=response_for(factory(patent_returned=returned))['patent_comparisons'][0]
    assert pair['with_patents']['id']
    assert pair['model_version_matched'] is (None if returned is False else False)
    assert pair['comparison_status']!='comparable_judgments' and pair['judgment_changed'] is None
    assert reason in pair['reasons']


def test_missing_control_keeps_the_actual_patent_assignment(factory):
    sample=factory(include_baseline=False)
    pair=response_for(sample)['patent_comparisons'][0]
    assert pair['control'] is None and pair['with_patents']['id']==sample.ids[0]
    assert pair['comparison_status']=='not_comparable' and pair['judgment_changed'] is None
    assert 'missing_control' in pair['reasons']


def test_unknown_target_and_absent_database_are_404_without_initialization(tmp_path,monkeypatch):
    root=tmp_path/'does-not-exist'
    monkeypatch.setattr(public,'ROOT',tmp_path/'absent-sources')
    monkeypatch.setattr(public,'read_archived',lambda store:([],[],{}))
    app=FastAPI();app.state.store=SimpleNamespace(path=root);app.include_router(public.router)
    with TestClient(app) as client:
        result=client.get('/api/arena/public/targets/unknown/comparisons')
    assert result.status_code==404
    assert not root.exists()


def test_same_nct_in_another_real_cohort_cannot_supply_missing_exact_control(factory):
    sample=factory(include_baseline=False)
    target,slots=spec()
    target.update(id='another-target-same-nct',trial_name='Synthetic separate registered analysis')
    other=slot_for(target,slots[0]['packet'])
    ids=sample.ledger.register('another-cohort-same-nct',[target],[other],{})
    complete(sample.ledger,ids[0],other)
    pair=response_for(sample)['patent_comparisons'][0]
    assert pair['control'] is None
    assert 'missing_control' in pair['reasons']
    assert pair['judgment_changed'] is None


def test_blocking_source_issue_is_publicly_uncertain_without_issue_text(factory):
    sample=factory(agents=True)
    sample.ledger.record_evidence_issue('e1','a'*64,note='Synthetic source correction. '+CANARY,
        reference_url='https://example.org/synthetic-correction',severity='blocking')
    result=response_for(sample)
    pair=result['patent_comparisons'][0]
    assert 'blocked_input' in pair['reasons']
    assert pair['judgment_changed'] is None and pair['comparison_status']!='comparable_judgments'
    assert pair['control']['predicted_outcome']==pair['with_patents']['predicted_outcome']=='Uncertain'
    assert all('blocked_input' in row['reasons'] for row in result['agent_comparisons'])
    assert CANARY not in json.dumps(result)
    assert 'input_evidence_issues' not in set(keys(result))


@pytest.mark.parametrize('credential',['sk-'+'S'*32,'-----BEGIN OPENSSH PRIVATE KEY-----'])
def test_credential_like_component_model_is_withheld_by_actual_public_producer(factory,credential):
    def mutate(shared,agent):
        agent['agent_contract']['components'][0]['requested_model']=credential
    result=response_for(factory(agents=True,agent_mutate=mutate))
    row=next(v for v in result['agent_comparisons'] if v['workflow']=='open_agent')
    assert credential not in json.dumps(result)
    assert row['component_models']==[{'role':'forecast','requested_model':'A-fixed'}]


@pytest.mark.parametrize('field,change',[('packet','different'),('budget','different'),('budget','unknown')])
def test_agent_rows_disclose_input_and_cap_unknowns_without_claiming_an_effect(factory,field,change):
    def mutate(shared,agent):
        for run in (shared,agent):
            if field=='packet':
                run['packet']['evidence'][0]['text']='Different synthetic workflow evidence.'
                run['expected_provider_input_sha256']=sha(run['packet'])
            elif change=='different':run['execution_budget_sha256']='8'*64
            else:run.pop('execution_budget_sha256')
    result=response_for(factory(agents=True,agent_mutate=mutate))
    for row in result['agent_comparisons']:
        if field=='packet':
            assert row['clinical_input_matched'] is False
            assert 'clinical_input_changed' in row['reasons']
        else:
            assert row['common_budget_cap'] is (False if change=='different' else None)
            assert ('budget_cap_changed' if change=='different' else 'budget_cap_unverified') in row['reasons']
        assert 'judgment_changed' not in row and 'gain' not in row


@pytest.mark.parametrize('fault',['ambiguous_control','wrong_cohort','wrong_target','bad_input_hash',
    'missing_input_hash','missing_actual_prompt','changed_actual_prompt','no_added_patents'])
def test_pure_production_helper_rejects_unverifiable_detached_record_pairs(factory,fault):
    """Store-disallowed corruptions use copies; actual ledger bytes stay intact."""
    from emeris_live.arena_comparisons import patent_comparisons
    sample=factory()
    snapshot=sample.ledger.snapshot()
    runs=deepcopy(snapshot['runs'])
    baseline=next(r for r in runs if r['track']=='same_evidence')
    treated=next(r for r in runs if r['track']=='patent_ablation')
    if fault=='ambiguous_control':
        duplicate=deepcopy(baseline);duplicate['id']='detached-duplicate';runs.append(duplicate)
    elif fault=='wrong_cohort':baseline['cohort_id']='another-detached-cohort'
    elif fault=='wrong_target':baseline['target_id']='another-detached-target'
    elif fault=='bad_input_hash':treated['input_sha256']='9'*64
    elif fault=='missing_input_hash':
        baseline.pop('input_sha256');treated.pop('input_sha256')
    elif fault=='missing_actual_prompt':
        baseline.pop('prompt_sha256');treated.pop('prompt_sha256')
    elif fault=='changed_actual_prompt':treated['prompt_sha256']='9'*64
    elif fault=='no_added_patents':
        treated['packet']=deepcopy(baseline['packet']);treated['input_sha256']=sha(treated['packet'])
    before=deepcopy(runs)
    pair=patent_comparisons(runs)[0]
    assert pair['comparison_status']=='not_comparable'
    assert pair['judgment_changed'] is None
    expected={'ambiguous_control':'ambiguous_control','wrong_cohort':'missing_control',
        'wrong_target':'missing_control','bad_input_hash':'clinical_packet_changed',
        'missing_input_hash':'clinical_packet_changed','missing_actual_prompt':'actual_prompt_unverified',
        'changed_actual_prompt':'actual_prompt_unverified','no_added_patents':'no_added_patents'}[fault]
    assert expected in pair['reasons']
    assert runs==before and sample.ledger.events()==snapshot['events']


def test_agent_missing_hashes_do_not_turn_two_unknowns_into_equal_clinical_input(factory):
    from emeris_live.arena_comparisons import agent_comparisons
    sample=factory(agents=True)
    runs=deepcopy(sample.ledger.snapshot()['runs'])
    for run in runs:run.pop('input_sha256',None)
    rows=agent_comparisons(runs)
    assert rows
    for row in rows:
        assert row['clinical_input_matched'] is None
        assert 'clinical_input_unverified' in row['reasons']


def test_agent_component_role_ambiguity_is_not_published_as_a_confirmed_identity(factory):
    def mutate(shared,agent):
        agent['agent_contract']['components'].append(
            {'role':'forecast','requested_model':'C-fixed'})
    result=response_for(factory(agents=True,agent_mutate=mutate))
    row=next(v for v in result['agent_comparisons'] if v['workflow']=='open_agent')
    assert row['component_models']==[{'role':'evidence_review','requested_model':'B-fixed'}]


@pytest.mark.parametrize('field',['provider_config_snapshot','expected_provider_input_sha256'])
def test_actual_ledger_absent_frozen_bindings_do_not_gain_comparability_from_observed_values(factory,field):
    def mutate(control,treated):
        for slot in (control,treated):slot.pop(field)
    sample=factory(mutate=mutate)
    pair=response_for(sample)['patent_comparisons'][0]
    assert pair['registration_matched'] is False
    assert pair['comparison_status']=='not_comparable' and pair['judgment_changed'] is None
    assert 'registration_metadata_unverified' in pair['reasons']
    reason=('actual_config_unverified' if field=='provider_config_snapshot'
            else 'actual_provider_input_unverified')
    assert reason in pair['reasons']


@pytest.mark.parametrize('fault,registration_matched,reason',[
    ('frozen_config_missing',False,'actual_config_unverified'),
    ('frozen_config_unknown',False,'actual_config_unverified'),
    ('frozen_config_changed',False,'actual_config_unverified'),
    ('actual_config_missing',True,'actual_config_unverified'),
    ('actual_config_changed',True,'actual_config_unverified'),
    ('expected_input_missing',False,'actual_provider_input_unverified'),
    ('expected_input_unknown',False,'actual_provider_input_unverified'),
    ('expected_input_changed',True,'actual_provider_input_unverified'),
    ('actual_input_missing',True,'actual_provider_input_unverified'),
    ('actual_input_changed',True,'actual_provider_input_unverified'),
    ('actual_input_reused_across_arms',True,'actual_provider_input_unverified'),
])
def test_pure_helper_requires_each_observed_config_and_input_to_match_its_frozen_binding(
        factory,fault,registration_matched,reason):
    from emeris_live.arena_comparisons import patent_comparisons
    sample=factory()
    snapshot=sample.ledger.snapshot()
    runs=deepcopy(snapshot['runs'])
    control=next(r for r in runs if r['track']=='same_evidence')
    treated=next(r for r in runs if r['track']=='patent_ablation')
    # The arms legitimately have different registered provider inputs. Equality
    # is required within each arm, never between the two input bindings.
    assert control['expected_provider_input_sha256']!=treated['expected_provider_input_sha256']
    assert patent_comparisons(runs)[0]['comparison_status']=='comparable_judgments'
    if fault=='frozen_config_missing':
        for run in runs:run.pop('provider_config_snapshot')
    elif fault=='frozen_config_unknown':
        for run in runs:run['provider_config_snapshot']['config_sha256']=None
    elif fault=='frozen_config_changed':treated['provider_config_snapshot']['config_sha256']='9'*64
    elif fault=='actual_config_missing':
        for run in runs:run.pop('config_sha256')
    elif fault=='actual_config_changed':treated['config_sha256']='9'*64
    elif fault=='expected_input_missing':
        for run in runs:run.pop('expected_provider_input_sha256')
    elif fault=='expected_input_unknown':
        for run in runs:run['expected_provider_input_sha256']='not-a-sha256'
    elif fault=='expected_input_changed':treated['expected_provider_input_sha256']='9'*64
    elif fault=='actual_input_missing':
        for run in runs:run.pop('provider_input_sha256')
    elif fault=='actual_input_changed':treated['provider_input_sha256']='9'*64
    elif fault=='actual_input_reused_across_arms':treated['provider_input_sha256']=control['provider_input_sha256']
    before=deepcopy(runs)
    pair=patent_comparisons(runs)[0]
    assert pair['registration_matched'] is registration_matched
    assert pair['comparison_status']=='not_comparable' and pair['judgment_changed'] is None
    assert reason in pair['reasons']
    assert runs==before and sample.ledger.events()==snapshot['events']


@pytest.mark.parametrize('state,label,late_public',[
    ('pending','success',False),('agreement','success',False),
    ('accepted','success',False),('accepted','failure',False),
    ('accepted','mixed',False),('accepted','inconclusive',False),
    ('accepted','success',True),
])
def test_individual_matches_follow_existing_acceptance_and_time_gates_without_aggregate_claims(
        factory,state,label,late_public):
    sample=factory(agents=True)
    if state!='pending':
        # late_public=True means the synthetic source predates the model output;
        # accepted source truth alone must not turn that into a prospective hit.
        published=datetime.now(timezone.utc)-(timedelta(minutes=30) if late_public else timedelta())
        outcome={'target_binding':result_target_binding(sample.target),'label':label,
            'source_url':'https://example.org/synthetic-official-outcome','source_sha256':'6'*64,
            'first_public_at':published.isoformat(),
            'endpoint_match_note':'Synthetic exact registered endpoint source agreement.'}
        for reviewer in ('synthetic-review-one','synthetic-review-two'):
            sample.ledger.record_result(sample.target['id'],outcome,reviewer_id=reviewer,
                reviewer_kind='ai',review_note='Synthetic source inspection for an isolated test only.')
        if state=='accepted':scientifically_accept(sample.ledger,sample.target)
    before=sample.ledger.events()
    result=response_for(sample)
    pair=result['patent_comparisons'][0]
    original=sample.client.get('/api/arena/public/targets/'+sample.target['id']+'/record').json()
    assert original['version']=='arena-clinical-public-1.4'
    assert pair['control']==original['runs'][0]
    eligible=state=='accepted' and label in ('success','failure') and not late_public
    for run in (pair['control'],pair['with_patents']):
        if eligible:
            expected=run['predicted_outcome'].lower()==label
            assert run['match'] is expected
            assert run['match_status']==('matched' if expected else 'mismatched')
        else:
            assert run['match'] is None and run['match_status']=='not_evaluable'
    failed=next(v['run'] for v in result['agent_comparisons'] if v['workflow']=='open_agent')
    assert failed['match'] is None and failed['match_status']=='not_evaluable'
    assert not (set(keys(result)) & {'gain','score','accuracy','brier','probability_change',
                                     'rank','ranking_allowed','workflow_benefit'})
    assert result['public_policy']['clinical_prediction_validated'] is False
    assert sample.ledger.events()==before
