import csv, gzip, json, os, sys
from pathlib import Path
import numpy as np
import pytest
from scipy.stats import mannwhitneyu
from vbt.io import digest, load, write, within
from vbt.datasets import read_manifest, validate
from vbt.models import DatasetVersion, ResearchPlan
from vbt.registry import Registry
from vbt.engine import run_plan, verify_run, resume
from vbt.agents import DisabledProvider, draft_plan, require_tool
from vbt.analysis import run_geo
from vbt.cli import main


def plan(data):return ResearchPlan.model_validate(load(data/'plan.json'))


def test_register_idempotent_and_version_conflict(registry,fixture_data):
    ds=read_manifest(fixture_data/'manifest.json',fixture_data)[0]
    assert registry.register(ds,fixture_data)=='SYN_MATRIX@v1'
    assert len(registry.entries())==3
    ds=ds.model_copy(update={'source_url':'changed'})
    with pytest.raises(ValueError,match='conflict'):registry.register(ds,fixture_data)


def test_new_version_is_independent(registry,fixture_data):
    ds=registry.get('SYN_MATRIX@v1').model_copy(update={'version':'v2'})
    registry.register(ds,fixture_data)
    assert len(registry.entries())==4


def test_bad_hash_and_size(fixture_data):
    ds=read_manifest(fixture_data/'manifest.json',fixture_data)[0]
    with pytest.raises(ValueError,match='SHA'):validate(ds.model_copy(update={'sha256':'0'*64}),fixture_data)
    with pytest.raises(ValueError,match='Size'):validate(ds.model_copy(update={'size_bytes':2}),fixture_data)


def test_truncated_geo_even_with_matching_hash(fixture_data):
    ds=read_manifest(fixture_data/'manifest.json',fixture_data)[0];p=Path(ds.local_path)
    p.write_bytes(p.read_bytes()[:-20]);ds=ds.model_copy(update={'sha256':digest(p),'size_bytes':p.stat().st_size})
    with pytest.raises((EOFError,ValueError)):validate(ds,fixture_data)


def test_incomplete_and_missing_manifest_fields(fixture_data):
    rows=load(fixture_data/'manifest.json');rows[0]['download_status']='downloading';write(fixture_data/'bad.json',rows)
    with pytest.raises(ValueError):read_manifest(fixture_data/'bad.json',fixture_data)
    del rows[0]['version'];write(fixture_data/'bad.json',rows)
    with pytest.raises(ValueError,match='Missing required'):read_manifest(fixture_data/'bad.json',fixture_data)


def test_path_escape_and_symlink(fixture_data,tmp_path):
    outside=tmp_path/'secret';outside.write_text('secret')
    link=fixture_data/'link.csv';link.symlink_to(outside)
    with pytest.raises(ValueError,match='escapes'):within(link,fixture_data)
    with pytest.raises(ValueError):within(fixture_data/'..'/'secret',fixture_data)


def test_missing_columns(fixture_data):
    ds=read_manifest(fixture_data/'manifest.json',fixture_data)[1].model_copy(update={'required_columns':['absent']})
    with pytest.raises(ValueError,match='columns'):validate(ds,fixture_data)


def test_changing_file_detected(fixture_data,monkeypatch):
    import vbt.datasets as d
    ds=read_manifest(fixture_data/'manifest.json',fixture_data)[1]
    def change(_):Path(ds.local_path).write_text(Path(ds.local_path).read_text()+'\n')
    monkeypatch.setattr(d.time,'sleep',change)
    with pytest.raises(ValueError,match='changed during'):validate(ds,fixture_data)


def test_downloader_adapter_project_relative(tmp_path,fixture_data):
    rows=load(fixture_data/'manifest.json')
    for row in rows:
        row['local_path']=str(Path(row['local_path']).relative_to(tmp_path));row['version']='human readable source version'
    write(fixture_data/'external.json',rows)
    parsed=read_manifest(fixture_data/'external.json',fixture_data)
    assert parsed[0].version.startswith('sha256-')
    assert parsed[0].source_version=='human readable source version'


def test_csv_manifest(fixture_data):
    rows=load(fixture_data/'manifest.json');p=fixture_data/'manifest.csv'
    with p.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys());w.writeheader();w.writerows(rows)
    assert len(read_manifest(p,fixture_data))==3


def test_mann_whitney_effect_and_patient_counts(registry,fixture_data,tmp_path):
    p=plan(fixture_data);out=tmp_path/'out';out.mkdir()
    inputs={k:registry.get(k).model_dump() for k in p.keys()}
    result=run_geo(p,inputs,out);assert result['execution']=='succeeded'
    rows=load(out/'results.json');r=rows[0]
    assert r['median_difference']==pytest.approx(3)
    assert r['n_nonresponder']==r['n_responder']==6
    assert r['ci_low']<=3<=r['ci_high']
    a=np.array([4,5,4.5,5.2,4.8,5.5])+.5;b=a-3
    assert r['p_value']==pytest.approx(mannwhitneyu(a,b,method='asymptotic').pvalue)
    assert r['q_value']>=r['p_value']


def modify_sample(data,fn):
    p=data/'samples.csv'
    with p.open() as f:r=csv.DictReader(f);fields=r.fieldnames;rows=list(r)
    fn(rows)
    with p.open('w',newline='') as f:w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


def test_duplicate_patient_blocks(registry,fixture_data,tmp_path):
    modify_sample(fixture_data,lambda rows:rows[1].update(patient_id=rows[0]['patient_id']))
    p=plan(fixture_data);out=tmp_path/'out';out.mkdir()
    result=run_geo(p,{k:registry.get(k).model_dump() for k in p.keys()},out)
    assert result['execution']=='blocked'
    assert 'Duplicate patient' in result['gaps'][0]['reason']


def test_unknown_response_not_negative(registry,fixture_data,tmp_path):
    modify_sample(fixture_data,lambda rows:rows[0].update(response='unknown'))
    p=plan(fixture_data);out=tmp_path/'out';out.mkdir()
    run_geo(p,{k:registry.get(k).model_dump() for k in p.keys()},out)
    assert load(out/'results.json')[0]['n_responder']==5
    assert load(out/'SYN_UC_sample_flow.json')[0]['reason']=='missing_response'


def test_missing_probe_mapping_is_non_estimable(registry,fixture_data,tmp_path):
    p=plan(fixture_data).model_copy(update={'genes':['MISSING']});out=tmp_path/'out';out.mkdir()
    assert run_geo(p,{k:registry.get(k).model_dump() for k in p.keys()},out)['execution']=='blocked'
    assert load(out/'results.json')[0]['status']=='non_estimable'


def test_role_permissions_and_model_failure():
    with pytest.raises(PermissionError):require_tool('scientific_reviewer','execute_geo')
    with pytest.raises(RuntimeError,match='No model'):draft_plan(DisabledProvider(),'question')


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS Seatbelt acceptance')
def test_full_run_verify_tamper_restore_resume(registry,fixture_data,tmp_path):
    r=run_plan(registry,plan(fixture_data),fixture_data)
    status=load(r/'status.json')
    assert status['execution']=='succeeded',(status,(r/'stderr.log').read_text())
    assert status['record_integrity']=='complete' and status['scientific_review']=='revise'
    assert verify_run(r)['integrity_ok'] and verify_run(r)['evidence_ok']
    backup=tmp_path/'backup.json';registry.export(backup)
    recovered=Registry(registry.workspace,tmp_path/'recovered_state')
    assert recovered.restore(backup,fixture_data)==3;recovered.close()
    child=resume(registry,r,fixture_data)
    assert load(child/'status.json')['parent_run']==r.name
    assert load(child/'work'/'results.json')==load(r/'work'/'results.json')
    assert digest(child/'code'/'vbt'/'analysis.py')==digest(r/'code'/'vbt'/'analysis.py')
    (r/'claims.json').write_text('[]')
    assert not verify_run(r)['integrity_ok']


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS Seatbelt acceptance')
def test_os_sandbox_denies_writes_escape_secrets_network(registry,fixture_data,tmp_path,monkeypatch):
    secret=tmp_path/'private_secret';secret.write_text('PRIVATE')
    data=fixture_data/'samples.csv';original=digest(data)
    monkeypatch.setenv('ANTHROPIC_API_KEY','test-only-not-real')
    script=tmp_path/'proposed.py'
    script.write_text('''import os, socket, json
from pathlib import Path
result={}
for name,path in '''+repr({'raw':str(data),'outside':str(tmp_path/'escape')})+'''.items():
 try: Path(path).write_text('overwrite');result[name]='ALLOWED'
 except PermissionError: result[name]='DENIED'
try: Path('''+repr(str(secret))+''').read_text();result['secret']='ALLOWED'
except PermissionError:result['secret']='DENIED'
try: socket.create_connection(('127.0.0.1',9),timeout=1);result['network']='ALLOWED'
except PermissionError:result['network']='DENIED'
except OSError:result['network']='OTHER_ERROR'
try: os.link(''' + repr(str(data)) + ''','raw_link');result['hardlink']='ALLOWED'
except PermissionError:result['hardlink']='DENIED'
result['credential']=os.environ.get('ANTHROPIC_API_KEY')
Path('checks.json').write_text(json.dumps(result))
''')
    p=ResearchPlan(question='Engineering sandbox boundary test',workflow='custom',evidence_kind='synthetic',dataset_keys=['SYN_SAMPLES@v1'])
    r=run_plan(registry,p,fixture_data,script)
    assert load(r/'status.json')['execution']=='succeeded',(r/'stderr.log').read_text()
    assert load(r/'work'/'checks.json')=={'raw':'DENIED','outside':'DENIED','secret':'DENIED','network':'DENIED','hardlink':'DENIED','credential':None}
    assert digest(data)==original
    assert load(r/'status.json')['scientific_review']=='insufficient_evidence'


@pytest.mark.skipif(sys.platform!='darwin',reason='macOS Seatbelt acceptance')
def test_timeout_is_failed_recorded_and_recoverable(registry,fixture_data,tmp_path):
    script=tmp_path/'loop.py';script.write_text('while True: pass')
    p=ResearchPlan(question='Engineering timeout test',workflow='custom',evidence_kind='synthetic',timeout_seconds=1)
    r=run_plan(registry,p,fixture_data,script)
    assert load(r/'status.json')['execution']=='failed'
    assert verify_run(r)['integrity_ok']


def test_missing_dataset_records_gap(registry,fixture_data):
    p=ResearchPlan(question='Missing dataset should be recorded',workflow='inventory',dataset_keys=['NOT_HERE@v1'])
    r=run_plan(registry,p,fixture_data)
    assert load(r/'status.json')['execution']=='blocked'
    assert verify_run(r)['integrity_ok']


def test_synthetic_cannot_be_real(registry,fixture_data):
    p=plan(fixture_data).model_copy(update={'evidence_kind':'public_observational'})
    r=run_plan(registry,p,fixture_data)
    assert load(r/'status.json')['execution']=='blocked'
    assert 'evidence kind mismatch' in str(load(r/'status.json')['errors'])


def test_unknown_claim_row_is_rejected(registry,fixture_data,tmp_path):
    from vbt.engine import seal
    r=run_plan(registry,plan(fixture_data),fixture_data)
    if load(r/'status.json')['execution']!='succeeded':pytest.fail((r/'stderr.log').read_text())
    claims=load(r/'claims.json');claims[0]['row_id']='DOES_NOT_EXIST';write(r/'claims.json',claims)
    seal(r) # Simulate an internally inconsistent but freshly hashed package.
    assert not verify_run(r)['evidence_ok']


def test_no_external_volume_sqlite(tmp_path):
    with pytest.raises(ValueError,match='local disk'):Registry(tmp_path/'workspace','/Volumes/test/state')


def test_stale_input_blocks_new_run(registry,fixture_data):
    p=fixture_data/'samples.csv';p.write_text(p.read_text()+'\n')
    r=run_plan(registry,plan(fixture_data),fixture_data)
    assert load(r/'status.json')['execution']=='blocked'
    assert not verify_run(r)['integrity_ok']


def test_recovery_of_unsealed_interrupted_run(registry,fixture_data):
    r=run_plan(registry,plan(fixture_data),fixture_data)
    (r/'manifest.json').unlink()
    status=load(r/'status.json');status['execution']='running';status['owner_pid']=99999999;write(r/'status.json',status)
    child=resume(registry,r,fixture_data)
    assert load(r/'status.json')['execution']=='interrupted'
    assert load(child/'status.json')['execution']=='succeeded'


def test_model_failure_record(registry,fixture_data,monkeypatch,capsys):
    monkeypatch.delenv('VBT_MODEL_API_KEY',raising=False)
    code=main(['--workspace',str(registry.workspace),'--state-dir',str(registry.state_dir),'model-draft','--question','Draft a research plan','--base-url','https://example.invalid/v1','--model','test'])
    assert code==2
    records=list((registry.workspace/'artifacts'/'model_drafts').glob('*.json'))
    assert len(records)==1 and load(records[0])['status']=='failed'


def test_restore_detects_forged_manifest(registry,fixture_data,tmp_path):
    from vbt.engine import seal
    r=run_plan(registry,plan(fixture_data),fixture_data);backup=tmp_path/'backup.json';registry.export(backup)
    write(r/'claims.json',[]);seal(r)
    restored=Registry(registry.workspace,tmp_path/'other_state')
    try:
        with pytest.raises(ValueError,match='anchor'):restored.restore(backup,fixture_data)
    finally:restored.close()


def test_registry_survives_duplicate_register_new_default_fields(registry,fixture_data):
    ds=registry.get('SYN_MATRIX@v1')
    old=ds.model_dump();old.pop('source_version',None)
    with registry.db:registry.db.execute('UPDATE datasets SET payload=? WHERE key=?',(json.dumps(old),'SYN_MATRIX@v1'))
    assert registry.register(ds,fixture_data)=='SYN_MATRIX@v1'


def test_cannot_scientifically_pass_inventory(registry,fixture_data):
    r=run_plan(registry,ResearchPlan(question='Engineering inventory only',workflow='inventory',evidence_kind='synthetic',dataset_keys=['SYN_MATRIX@v1']),fixture_data)
    assert main(['--workspace',str(registry.workspace),'--state-dir',str(registry.state_dir),'human-review',r.name,'--reviewer','SYNTHETIC_TEST','--decision','pass','--reason','test','--signature','test'])==2


def test_disconnected_volume_cannot_create_workspace(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_mount', lambda self: False)
    with pytest.raises(OSError, match='not mounted'):
        Registry('/Volumes/VBT_DISCONNECTED_TEST/workspace')


def test_disconnected_volume_cannot_write_export(monkeypatch):
    from pathlib import Path
    monkeypatch.setattr(Path, 'is_mount', lambda self: False)
    with pytest.raises(OSError, match='not mounted'):
        write('/Volumes/VBT_DISCONNECTED_TEST/workspace/export.json', {})
