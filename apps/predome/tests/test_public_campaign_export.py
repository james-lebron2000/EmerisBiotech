"""Public campaign transport/identity boundaries with isolated API fixtures only."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import zipfile
from datetime import datetime, timezone

import pytest
from emeris_live import arena_public as api
from emeris_live.arena_public_contract import public_consensus
from test_arena_public import row, target
from emeris_live.arena_store import ArenaLedger, result_target_binding
from test_arena_core import spec as trial_spec, finish
from test_arena_scientific_consumers import public_client
from arena_scientific_fixture import scientifically_accept

spec=importlib.util.spec_from_file_location('public_campaign',Path(__file__).parents[1]/'scripts/export_public_campaign.py')
export=importlib.util.module_from_spec(spec);spec.loader.exec_module(export)


@pytest.fixture
def records():
    records={};targets=[]
    for i in range(12):
        raw=target(id='fixture-'+str(i),trial_name='Fixture '+str(i))
        runs=[api.run_view(row(id=f'fixture-{i}-{j}',target_id=raw['id'],model_id='model-'+str(j),
            requested_model='model-'+str(j),returned_model='version-'+str(j),
            status='failed' if j==3 else 'predicted',probability=None if j==3 else .7),raw,[]) for j in range(4)]
        t=api.target_view(raw,[]);t['consensus']=api.consensus(runs)
        record={'version':api.VERSION,'target':t,'runs':runs,'consensus':t['consensus'],
            'sources':[],'evidence':[],'result':None,'public_policy':dict(api.POLICY)}
        targets.append(t);records[raw['id']]=record
    index={'current_cohort_id':'c1','targets':targets,'runs':[r for d in records.values() for r in d['runs']],
        'public_policy':dict(api.POLICY)}
    paths=[]
    def fetch(path):
        paths.append(path)
        return deepcopy(records[path.split('/')[-2]])
    return index,records,paths,fetch


def test_bundle_preserves_actual_objects_failures_and_hashes_without_private_api(records):
    index,records,paths,fetch=records
    raw,manifest=export.bundle(index,fetch)
    assert manifest['formal_trials']==12 and manifest['formal_assigned_records']==48
    assert manifest['provider_calls']==manifest['ledger_writes']==0
    assert manifest['clinical_prediction_validated'] is False
    assert manifest['independent_timestamp_attestation'] is False
    assert len(paths)==12 and all(p.startswith('/api/arena/public/targets/') and p.endswith('/record') for p in paths)
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        assert z.testzip() is None and len(z.namelist())==26
        for name,entry in manifest['files'].items():
            data=z.read(name)
            assert len(data)==entry['bytes'] and export.digest(data)==entry['sha256']
        assert json.loads(z.read('records/fixture-0.json'))==records['fixture-0']
        report=z.read('reports/fixture-0.md').decode()
        assert '| failed |' in report and 'N/A' in report and 'Awaiting results' in report
        assert all(word not in report for word in ('PRIVATE','Token','sk-'))


@pytest.mark.parametrize('fault',['probability','private_key','unknown_run_field','wrong_target',
    'wrong_cohort','mixed_track','duplicate_model','missing_version','consensus','unsafe_id','failed_direction',
    'nested_policy','nested_readout','nested_design'])
def test_export_fails_closed_before_returning_any_bundle(records,fault):
    index,data,_,fetch=records
    record=data['fixture-0']
    if fault=='probability':record['runs'][0]['probability']=.7
    elif fault=='private_key':record['sources']=[{'id':'bad','title':'sk-'+'x'*32}]
    elif fault=='unknown_run_field':record['runs'][0]['provider_config_snapshot']={'api_key':'private'}
    elif fault=='wrong_target':record['target']['endpoint']='Changed after index capture'
    elif fault=='wrong_cohort':record['runs'][0]['cohort_id']='another-cohort'
    elif fault=='mixed_track':record['runs'][0]['track']='shared_agent'
    elif fault=='duplicate_model':record['runs'][1]['model_id']=record['runs'][0]['model_id']
    elif fault=='missing_version':record['runs'][0]['model_version']=None
    elif fault=='consensus':record['consensus']['assigned']=99
    elif fault=='unsafe_id':index['targets'][0]['id']='../outside'
    elif fault=='failed_direction':record['runs'][3]['predicted_outcome']='Success'
    elif fault=='nested_policy':record['public_policy']['internal_analysis']='PRIVATE'
    elif fault=='nested_readout':record['target']['readout']['admin_detail']='PRIVATE'
    elif fault=='nested_design':record['target']['design']={'allocation':'RANDOMIZED','internal_analysis':'PRIVATE'}
    # The real index request and detail request return independent objects.
    frozen_index=deepcopy(index)
    if fault=='wrong_target':frozen_index['targets'][0]['endpoint']='Prespecified PFS'
    with pytest.raises(ValueError):export.bundle(frozen_index,fetch)


def test_historical_replay_cannot_become_a_prospective_match(records):
    index,data,_,fetch=records
    history=deepcopy(data['fixture-0']);history['target'].update(id='replay',cohort_id=None,
        scope='archived_research',record_kind='historical_replay')
    for r in history['runs']:
        r.update(id='replay-'+r['id'],target_id='replay',cohort_id=None,scope='archived_research',track='archived',match=None,match_status='historical_replay')
    index['targets'].append(history['target']);data['replay']=history
    raw,manifest=export.bundle(deepcopy(index),fetch)
    assert manifest['historical_replays']==1
    with zipfile.ZipFile(io.BytesIO(raw)) as z:assert b'not a prospective prediction' in z.read('reports/replay.md')
    history['runs'][0]['match']=True
    with pytest.raises(ValueError,match='cannot earn'):export.bundle(deepcopy(index),fetch)


@pytest.mark.parametrize('path',['/api/arena','/api/arena/ledger','https://elsewhere/','/api/arena/public?x=1','/api/arena/public/targets/../secret/record'])
def test_transport_never_calls_nonpublic_routes(path,monkeypatch):
    def forbidden(*a,**kw):raise AssertionError('No network should start')
    monkeypatch.setattr(export.urllib.request,'build_opener',forbidden)
    with pytest.raises(ValueError,match='Only fixed public'):export.fetch_public(path)


def test_existing_output_never_overwritten_or_triggers_fetch(tmp_path,monkeypatch):
    out=tmp_path/'bundle.zip';out.write_bytes(b'existing')
    monkeypatch.setattr('sys.argv',['export','--output',str(out)])
    monkeypatch.setattr(export,'fetch_public',lambda *a:pytest.fail('No request on existing output'))
    with pytest.raises(ValueError,match='never overwritten'):export.main()
    assert out.read_bytes()==b'existing'


def test_readable_report_escapes_untrusted_html_links_and_table_cells(records):
    _,data,_,_=records
    record=deepcopy(data['fixture-0'])
    record['target']['trial_name']='<img src=x onerror=alert(1)> | [click](javascript:alert(1))\n# heading'
    text=export.report_text(record).decode()
    assert '<img' not in text and '[click](' not in text
    assert '&lt;img' in text and '\\|' in text and '\n# heading' not in text


def test_report_rebuild_is_stable_after_canonical_json_sorts_registry_fields(records):
    _,data,_,_=records
    record=deepcopy(data['fixture-0'])
    record['target']['design']={'masking':'NONE','allocation':'RANDOMIZED',
        'enrollment':{'type':'ACTUAL','count':100},'intervention_model':'PARALLEL'}
    assert export.report_text(record)==export.report_text(json.loads(export.canonical(record)))


def real_public_record(tmp_path, monkeypatch, state, label='success', *, evidence_locations=None):
    """Actual four-model ledger and public API; no provider or live-state access."""
    raw,slots=trial_spec()
    for model in ('C','D'):
        slot=deepcopy(slots[0]);slot.update(model_id=model,requested_model=model+'-fixed')
        slots.append(slot)
    if evidence_locations is not None:
        for slot in slots:
            slot['packet']['evidence'][0].update(deepcopy(evidence_locations))
    ledger=ArenaLedger(tmp_path/'runtime')
    ids=ledger.register('c1',[raw],slots,{'season_kind':'registered_12_project_season'})
    for ident,probability in zip(ids,(.8,.2,.6,.4)):
        finish(ledger,ident,probability,returned_model='fixture-version')
    if state!='pending':
        result={'target_binding':result_target_binding(raw),'label':label,
            'source_url':'https://example.org/isolated-source','source_sha256':'b'*64,
            'first_public_at':datetime.now(timezone.utc).isoformat(),
            'endpoint_match_note':'Synthetic exact-primary-endpoint source agreement for isolated export tests.'}
        for reviewer in ('isolated-one','isolated-two'):
            ledger.record_result(raw['id'],result,reviewer_id=reviewer,reviewer_kind='ai',
                review_note='Synthetic source inspection; no real scientific reviewer exists.')
    if state=='accepted':scientifically_accept(ledger,raw)
    client=public_client(ledger,monkeypatch)
    index=client.get('/api/arena/public').json()
    response=client.get('/api/arena/public/targets/'+raw['id']+'/record')
    assert response.status_code==200, response.text
    before=ledger.events()
    return ledger,before,index,response.json(),client


@pytest.mark.parametrize('state,label',[('pending','success'),('agreement','success'),
    ('accepted','success'),('accepted','failure'),('accepted','mixed'),('accepted','inconclusive')])
def test_actual_ledger_public_record_exports_exact_review_state_without_writes(tmp_path,monkeypatch,state,label):
    ledger,before,index,record,client=real_public_record(tmp_path,monkeypatch,state,label)
    raw,manifest=export.bundle(index,lambda path:client.get(path).json())
    assert manifest['schema_version']==export.BUNDLE_VERSION
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert json.loads(archive.read('records/target-test.json'))==record
        report=archive.read('reports/target-test.md').decode()
        if state=='pending':assert 'Awaiting results' in report
        elif state=='agreement':
            assert 'pending independent scientific review' in report
            assert record['result']['label'] is None and all(r['match'] is None for r in record['runs'])
            assert 'Formal binary scoring eligibility: Yes' not in report
        else:
            assert 'accepted by the responsible operator' in report
            assert ('Formal binary scoring eligibility: Yes.' in report)==(label in ('success','failure'))
            assert record['result']['label']==label
    assert ledger.events()==before


def test_actual_ledger_locations_survive_public_record_campaign_json_and_report(tmp_path,monkeypatch):
    locations={'page_references':[3,8],'source_anchor':'Synthetic section 4.2 / Table 3',
               'document_version':'Synthetic protocol v2 / 2020-01-01'}
    ledger,before,index,record,client=real_public_record(tmp_path,monkeypatch,'pending',evidence_locations=locations)
    assert record['version']=='arena-clinical-public-1.4'
    for run in record['runs']:
        assert {key:run['sources'][0][key] for key in locations}==locations
    raw,_=export.bundle(index,lambda path:client.get(path).json())
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert json.loads(archive.read('records/target-test.json'))==record
        report=archive.read('reports/target-test.md').decode()
        assert 'Recorded pages: 3, 8.' in report
        assert 'Source anchor: '+locations['source_anchor'] in report
        assert 'Document version: '+locations['document_version'] in report
        assert 'Locations are frozen evidence-card metadata' in report
        assert '#page=' not in report and 'PDF pages' not in report
    assert ledger.events()==before


@pytest.mark.parametrize('field,value',[
    ('page_references',[1,1]),('page_references',[True]),('page_references','2'),
    ('source_anchor','anchor\nnext'),('document_version',''),
    ('source_anchor','sk-'+'S'*32),('document_version','-----BEGIN OPENSSH PRIVATE KEY-----'),
])
def test_campaign_rejects_invalid_and_credential_like_locations(records,field,value):
    index,data,_,fetch=records
    data['fixture-0']['runs'][0]['sources']=[{'id':'synthetic',field:value}]
    before=deepcopy(data)
    with pytest.raises(ValueError):export.bundle(index,fetch)
    assert data==before


@pytest.mark.parametrize('fault',['false_eligibility','raw_adjudicated','leaked_label','fake_hit',
    'string_bool','unknown_review','hit_text','wrong_hit','wrong_match_status','mixed_binary'])
def test_new_export_rejects_inconsistent_assurance_and_hit_fields(tmp_path,monkeypatch,fault):
    accepted=fault in ('wrong_hit','wrong_match_status','mixed_binary')
    _,_,_,record,_=real_public_record(tmp_path,monkeypatch,'accepted' if accepted else 'agreement',
        'mixed' if fault=='mixed_binary' else 'success')
    result=record['target']['result']
    if fault=='false_eligibility':result['scoring_eligible']=True
    elif fault=='raw_adjudicated':result['status']=record['target']['status']='adjudicated'
    elif fault=='leaked_label':result['label']='success'
    elif fault=='fake_hit':record['runs'][0].update(match=True,match_status='matched')
    elif fault=='string_bool':result['scoring_eligible']='false'
    elif fault=='unknown_review':result['review_status']='automatic_scientific_approval'
    elif fault=='hit_text':record['runs'][0]['match_status']='matched'
    elif fault=='wrong_hit':record['runs'][0].update(match=False,match_status='mismatched')
    elif fault=='wrong_match_status':record['runs'][0]['match_status']='mismatched'
    elif fault=='mixed_binary':result['scoring_eligible']=True
    record['result']=deepcopy(result)
    with pytest.raises(ValueError):export.validate_record(record,record['target'])


def test_live_export_rejects_legacy_version_even_without_results(records):
    index,data,_,fetch=records
    data['fixture-0']['version']=export.LEGACY_VERSION
    with pytest.raises(ValueError,match='verification-only'):export.bundle(index,fetch)


def synthetic_current_readout(**changes):
    """Publication-contract fixture only; it is not an actual approved schedule."""
    return {'label':'SYNTHETIC reviewed calendar · 2027 Q1','start':'2027-01-01','end':'2027-03-31',
        'precision':'quarter','source_url':'https://example.org/synthetic-current-calendar',
        'source_title':'SYNTHETIC current timing source','source_granularity':'exact_analysis',
        'source_note':'Synthetic exact primary-analysis timing; no actual result.',
        'published_at':'2026-10-23','retrieved_at':'2026-10-23T14:00:00Z','approved_at':'2026-10-24T14:00:00Z',
        'proposal_id':'ru_'+'1'*24,'proposal_sha256':'1'*64,'decision_id':'rd_'+'2'*24,
        'decision_sha256':'2'*64,'source_sha256':'3'*64,'hash_scope':'source_response_bytes',
        'timestamp_attestation':'server_clock_not_independent_timestamp','original_prediction_unchanged':True,**changes}


def test_synthetic_current_calendar_exports_separately_from_frozen_forecasts(records):
    index,data,_,fetch=records
    original=deepcopy(data['fixture-0'])
    update=synthetic_current_readout()
    data['fixture-0']['target']['current_readout']=deepcopy(update)
    index=deepcopy(index)
    raw,manifest=export.bundle(index,fetch)
    assert manifest['schema_version']=='predome-public-launch-bundle-3'
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        record=json.loads(archive.read('records/fixture-0.json'))
        assert record['target']['readout']==original['target']['readout']
        assert record['target']['current_readout']==update
        assert record['runs']==original['runs'] and record['result']==original['result']
        report=archive.read('reports/fixture-0.md').decode()
        assert 'Original frozen window' in report and 'Current reviewed readout' in report
        assert '2027-01-01 to 2027-03-31' in report
        assert 'This update was not model input' in report and 'Awaiting results' in report
        assert update['source_url'] in report and update['approved_at'] in report


@pytest.mark.parametrize('changes',[{'original_prediction_unchanged':False}, {'source_granularity':'drug_program'},
    {'source_sha256':'invalid'}, {'precision':'quarter','end':'2027-04-01'}, {'approved_at':'2026-10-22T00:00:00Z'},
    {'source_url':'javascript:alert(1)'}, {'internal_reason':{'prompt':'PRIVATE'}}])
def test_current_calendar_export_rejects_unapproved_or_unsafe_contract(records,changes):
    index,data,_,fetch=records
    data['fixture-0']['target']['current_readout']=synthetic_current_readout(**changes)
    with pytest.raises(ValueError):export.bundle(deepcopy(index),fetch)


@pytest.mark.parametrize('version',[export.LEGACY_VERSION,export.PREVIOUS_VERSION,export.CALENDAR_VERSION,export.RETURNED_JUDGMENT_VERSION])
def test_old_record_contracts_are_only_offline_verification_inputs(records,version):
    index,data,_,fetch=records
    record=data['fixture-0'];record['version']=version
    record['consensus']=public_consensus(record['runs'],legacy=version!=export.RETURNED_JUDGMENT_VERSION)
    record['target']['consensus']=deepcopy(record['consensus'])
    with pytest.raises(ValueError,match='verification-only'):export.bundle(index,fetch)
    assert export.validate_record(record,record['target'],allow_legacy=True) is False
    record['target']['current_readout']=synthetic_current_readout()
    if version in (export.CALENDAR_VERSION,export.RETURNED_JUDGMENT_VERSION):
        assert export.validate_record(record,record['target'],allow_legacy=True) is False
    else:
        with pytest.raises(ValueError,match='Legacy records'):export.validate_record(record,record['target'],allow_legacy=True)


@pytest.mark.parametrize('version',[export.LEGACY_VERSION,export.PREVIOUS_VERSION,export.CALENDAR_VERSION,export.RETURNED_JUDGMENT_VERSION])
def test_campaign_legacy_source_schema_cannot_gain_new_location_fields(records,version):
    _,data,_,_=records
    record=data['fixture-0'];record['version']=version
    record['consensus']=public_consensus(record['runs'],legacy=version!=export.RETURNED_JUDGMENT_VERSION)
    record['target']['consensus']=deepcopy(record['consensus'])
    record['runs'][0]['sources']=[{'id':'synthetic','page_references':[2]}]
    with pytest.raises(ValueError,match='Unknown source metadata'):
        export.validate_record(record,record['target'],allow_legacy=True)


@pytest.mark.parametrize('calendar,expected_hash,expected_bytes',[
    (False,'0d9d442056c787c56f1203a46e101dc74e5fecdf3065e2eb27b5de810913e40d',1710),
    (True,'85b9ff66cc64d9b765233ccc652618c7e3f3c7cbcb633926e95349820677bc6a',2353),
])
def test_legacy_13_report_bytes_match_pre_location_producer(records,calendar,expected_hash,expected_bytes):
    """Pinned from a985543's actual report producer on these synthetic records."""
    _,data,_,_=records
    record=deepcopy(data['fixture-0']);record['version']=export.RETURNED_JUDGMENT_VERSION
    if calendar:record['target']['current_readout']=synthetic_current_readout()
    raw=export.report_text(record)
    assert len(raw)==expected_bytes and export.digest(raw)==expected_hash


def test_legacy_13_cannot_use_pre_13_assigned_agreement_rule(records):
    _,data,_,_=records
    record=data['fixture-0'];record['version']=export.RETURNED_JUDGMENT_VERSION
    assert export.validate_record(record,record['target'],allow_legacy=True) is False
    record['consensus']=public_consensus(record['runs'],legacy=True)
    record['target']['consensus']=deepcopy(record['consensus'])
    with pytest.raises(ValueError,match='consensus'):
        export.validate_record(record,record['target'],allow_legacy=True)
