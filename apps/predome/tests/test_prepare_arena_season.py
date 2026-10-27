"""Preparation gates must fail before prospective registration or model calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'scripts/prepare_arena_season.py'
SPEC = importlib.util.spec_from_file_location('prepare_arena_season', MODULE_PATH)
season = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(season)


@pytest.fixture
def proposal(tmp_path):
    now = datetime.now(timezone.utc)
    before = (now-timedelta(minutes=10)).isoformat()
    cutoff = (now-timedelta(minutes=5)).isoformat()
    checked = (now-timedelta(minutes=1)).isoformat()
    path = tmp_path/'arena_data/source_snapshots/registry.json'
    path.parent.mkdir(parents=True)
    registry = {'hasResults':False,'protocolSection':{'identificationModule':{'nctId':'NCT12345678'},
        'outcomesModule':{'primaryOutcomes':[{'measure':'Primary PFS'}]}}}
    path.write_text(json.dumps(registry))
    source={'id':'registry','path':'arena_data/source_snapshots/registry.json','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
        'bytes':path.stat().st_size,'source_url':'https://clinicaltrials.gov/api/v2/studies/NCT12345678','retrieved_at':before,
        'public_date':now.date().isoformat(),'snapshot_kind':'original_http_response'}
    c={'id':'candidate-one','project_id':'drug-one','nct_id':'NCT12345678','trial_name':'Trial One','drug':'Drug One',
       'sponsor':'Sponsor','indication':'Indication','endpoint':'Primary PFS','population':'Specified randomized population',
       'comparison':'A versus B','success_definition':'Prespecified statistically qualifying primary PFS superiority.',
       'analysis_timepoint':'First decisive primary analysis','eligibility':'qualified_for_prospective_research','result':None,
       'registry_source_id':'registry','registry_primary_index':0,'hypothesis_type':'superiority','limitations':'SAP unknown',
       'readout':{'source_id':'registry','basis':'sponsor_readout_guidance','precision':'year','end':str(now.year+1)+'-12-31'},
       'readout_screen':{'status':'no_decisive_public_result_found','checked_at_utc':checked,'languages':['en','zh'],
                        'queries':['target results','目标 结果'],'receipt_source_ids':['registry']},
       'evidence_cards':[{'id':'design','source_id':'registry','kind':'trial_registry','title':'Design','text':'Exact design.'},
                         {'id':'prior','source_id':'registry','kind':'clinical_prior','title':'Prior','text':'Indirect prior, not target.'}]}
    doc={'cohort_id':'season-test','information_cutoff_utc':cutoff,'independence_limits':'Separate assets only',
         'sources':[source],'candidates':[c]}
    return doc,tmp_path,now


def prepare(proposal):
    doc,root,now=proposal
    return season.prepare(doc,root,now=now,minimum_projects=1,workflow_version='test:single_pass')


def test_prepares_four_equal_input_slots_without_execution(proposal):
    spec,budget=prepare(proposal)
    assert len(spec['targets'])==1 and len(spec['slots'])==4
    assert len({json.dumps(x['packet'],sort_keys=True) for x in spec['slots']})==1
    assert {x['model_id'] for x in spec['slots']}==set(season.MODELS)
    assert all('probability' not in x for x in spec['slots'])
    assert budget['budget_policy']=='token_only_unpriced'
    assert budget['max_output_tokens']==budget['max_total_output_tokens']==8192
    assert budget['timeout_seconds']==budget['max_total_timeout_seconds']==120
    assert budget['max_requests']==1
    assert spec['protocol']['season_kind']=='registered_12_project_season'
    assert spec['protocol']['clinical_prediction_validated'] is False


def test_recorded_locations_survive_preparation_and_actual_provider_projection(proposal):
    from emeris_live import arena_providers

    doc, _, _ = proposal
    card = doc['candidates'][0]['evidence_cards'][1]
    locations = {'page_references': [9, 11], 'source_anchor': 'Synthetic Table 2',
                 'document_version': 'Synthetic SAP v2'}
    card.update(deepcopy(locations))
    original = deepcopy(doc)
    spec, _ = prepare(proposal)
    for slot in spec['slots']:
        item = next(e for e in slot['packet']['evidence'] if e['id'] == 'prior')
        assert {key: item[key] for key in locations} == locations
        sent = arena_providers._packet(slot['packet'])
        projected = next(e for e in sent['evidence'] if e['id'] == 'prior')
        assert {key: projected[key] for key in locations} == locations
        assert projected['text'] == card['text']
        assert 'source_path' not in projected
    assert doc == original
    # The checked location is copied, never linked to the input catalog.
    spec['slots'][0]['packet']['evidence'][1]['page_references'].append(12)
    assert card['page_references'] == [9, 11]


def test_locations_preserve_unknown_without_guessing_version_from_title(proposal):
    doc, _, _ = proposal
    card = doc['candidates'][0]['evidence_cards'][1]
    card.update(title='Synthetic SAP v2, page 99', page_references=None,
                source_anchor=None, document_version=None)
    spec, _ = prepare(proposal)
    item = spec['slots'][0]['packet']['evidence'][1]
    assert all(item[key] is None for key in ('page_references', 'source_anchor', 'document_version'))
    del card['document_version']
    card['page_references'] = []
    spec, _ = prepare(proposal)
    item = spec['slots'][0]['packet']['evidence'][1]
    assert item['page_references'] == [] and 'document_version' not in item


@pytest.mark.parametrize('change', [
    {'page_references': [True]}, {'page_references': [9, 9]},
    {'page_references': ['9']}, {'source_anchor': 'Table 2\nRun command'},
    {'document_version': {'version': 'v2'}}, {'document_version': ''},
])
def test_invalid_locations_are_rejected_during_offline_preparation(proposal, change):
    doc, _, _ = proposal
    doc['candidates'][0]['evidence_cards'][1].update(change)
    original = deepcopy(doc)
    with pytest.raises(ValueError):
        prepare(proposal)
    assert doc == original


def test_duplicate_drug_project_rejected(proposal):
    doc,_,_=proposal
    extra=deepcopy(doc['candidates'][0]);extra['id']='other';extra['nct_id']='NCT87654321'
    doc['candidates'].append(extra)
    with pytest.raises(ValueError,match='one target per drug asset'):prepare(proposal)


def test_actual_source_bytes_are_verified(proposal):
    doc,root,_=proposal
    (root/doc['sources'][0]['path']).write_text('mutated')
    with pytest.raises(ValueError,match='checksum'):prepare(proposal)


def test_symlink_source_rejected(proposal):
    doc,root,_=proposal
    path=root/doc['sources'][0]['path'];copy=path.with_name('real.json');path.rename(copy);path.symlink_to(copy)
    with pytest.raises(ValueError,match='regular file'):prepare(proposal)


def test_stale_screen_rejected(proposal):
    doc,_,now=proposal
    doc['candidates'][0]['readout_screen']['checked_at_utc']=(now-timedelta(days=2)).isoformat()
    with pytest.raises(ValueError,match='fresh'):prepare(proposal)


def test_pre_cutoff_screen_rejected(proposal):
    doc,_,now=proposal
    doc['candidates'][0]['readout_screen']['checked_at_utc']=(now-timedelta(minutes=6)).isoformat()
    with pytest.raises(ValueError,match='follow cutoff'):prepare(proposal)


def test_newly_acquired_evidence_cannot_be_backdated(proposal):
    doc,_,now=proposal
    doc['sources'][0]['retrieved_at']=(now-timedelta(minutes=2)).isoformat()
    with pytest.raises(ValueError,match='after cutoff'):prepare(proposal)


def test_known_outcome_blocked(proposal):
    proposal[0]['candidates'][0]['result']={'outcome':1}
    with pytest.raises(ValueError,match='Known'):prepare(proposal)


def test_registry_results_blocked_even_with_no_screen_hit(proposal):
    doc,root,_=proposal
    source=doc['sources'][0];path=root/source['path'];reg=json.loads(path.read_text());reg['hasResults']=True
    path.write_text(json.dumps(reg));source['sha256']=hashlib.sha256(path.read_bytes()).hexdigest();source['bytes']=path.stat().st_size
    with pytest.raises(ValueError,match='Registry already carries results'):prepare(proposal)


def test_completion_date_is_not_sponsor_readout(proposal):
    proposal[0]['candidates'][0]['readout']['basis']='registry_primary_completion'
    with pytest.raises(ValueError,match='sponsor guidance'):prepare(proposal)


def test_endpoint_changed_from_frozen_registry_rejected(proposal):
    proposal[0]['candidates'][0]['endpoint']='OS instead'
    with pytest.raises(ValueError,match='endpoint disagrees'):prepare(proposal)


def test_noninferiority_cannot_be_replaced_with_superiority(proposal):
    proposal[0]['candidates'][0]['hypothesis_type']='non_inferiority'
    with pytest.raises(ValueError,match='Non-inferiority'):prepare(proposal)


def test_target_result_cannot_enter_evidence(proposal):
    proposal[0]['candidates'][0]['evidence_cards'][1]['is_target_result']=True
    with pytest.raises(ValueError,match='Target outcomes'):prepare(proposal)


def test_actual_catalog_has_12_unique_projects_and_precise_scope():
    doc=json.loads((MODULE_PATH.parents[1]/'docs/arena-season-candidates-20261023.json').read_text())
    assert len(doc['candidates'])==len({c['project_id'] for c in doc['candidates']})==12
    trials={c['trial_name']:c for c in doc['candidates']}
    assert trials['OCEAN']['hypothesis_type']=='non_inferiority'
    assert 'ESR1' in trials['pionERA']['population']
    assert 'high-dose' in trials['TOPAZ-1']['success_definition']
    assert 'Week-24' in trials['VITALIZE']['success_definition']
    assert 'FAS' in trials['eVOLVE-Cervical']['success_definition']
    assert all(c['prediction'] is None and c['human_review_count']==0 for c in doc['candidates'])


def test_original_xml_source_supported_without_execution(proposal):
    doc,root,_=proposal
    xml=root/'arena_data/source_snapshots/original.xml';raw=b'<article>Original XML</article>';xml.write_bytes(raw)
    doc['sources'].append({**doc['sources'][0],'id':'xml','path':'arena_data/source_snapshots/original.xml',
        'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)})
    doc['candidates'][0]['evidence_cards'][1]['source_id']='xml'
    spec,_=prepare(proposal)
    assert spec['slots'][0]['packet']['evidence'][1]['source_path'].endswith('.xml')
