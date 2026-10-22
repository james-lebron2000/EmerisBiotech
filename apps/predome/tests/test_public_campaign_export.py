"""Public campaign transport/identity boundaries with isolated API fixtures only."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import zipfile

import pytest
from emeris_live import arena_public as api
from test_arena_public import row, target

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
