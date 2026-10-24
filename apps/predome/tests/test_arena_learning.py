"""Isolated ledger fixtures only; no public outcomes, provider calls or live data."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3

import pytest

from emeris_live.arena_store import ArenaLedger, sha


@pytest.fixture
def learning():
    spec = importlib.util.spec_from_file_location('arena_learning_test',
        Path(__file__).resolve().parents[1] / 'scripts/arena_learning_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_ledger(tmp_path, *, adjudicated=False, label='success'):
    now = datetime.now(timezone.utc)
    target = {'id':'isolated-target', 'project_id':'isolated-project', 'nct_id':'NCT00000001',
        'endpoint':'Primary PFS', 'population':'Fixture adults', 'comparison':'Fixture A vs B',
        'success_definition':'Prespecified primary PFS hypothesis met', 'analysis_timepoint':'Primary analysis',
        'information_cutoff_utc':(now-timedelta(hours=2)).isoformat(),
        'submission_deadline_utc':(now+timedelta(days=1)).isoformat(),
        'readout_screen':{'status':'no_decisive_public_result_found','checked_at_utc':now.isoformat(),
            'sources':[{'url':'https://example.org/fixture-screen','sha256':'a'*64}]}}
    packet = {'target':{k:target[k] for k in ('endpoint','population','comparison','success_definition')},
        'data_cutoff':target['information_cutoff_utc'], 'evidence':[{'id':'e1','kind':'clinical_prior',
            'text':'Isolated test evidence only.', 'source_url':'https://example.org/fixture',
            'source_sha256':'a'*64,'available_at':(now-timedelta(days=1)).isoformat()}]}
    slots = [{'target_id':target['id'],'model_id':m,'requested_model':m+'-fixed','provider':'fixture',
        'track':'same_evidence','condition':'clinical_only','workflow_version':'fixture-workflow-v1',
        'packet':deepcopy(packet)} for m in ('correct','wrong','abstain','timeout','citation','pending')]
    ledger = ArenaLedger(tmp_path / 'ledger')
    ids = ledger.register('isolated-cohort',[target],slots,{'status':'isolated fixture, no live models'})
    for index, p in ((0,.8),(1,.1)):
        ledger.start(ids[index])
        ledger.finish(ids[index],{'status':'predicted','probability':p,'evidence_ids':['e1'],
            'returned_model':slots[index]['requested_model']})
    for index, status, code in ((2,'abstain',None),(3,'timed_out','provider_timeout'),(4,'failed','unsupported_citation')):
        ledger.start(ids[index])
        ledger.finish(ids[index],{'status':status,'probability':None,'error_code':code})
    result = {'label':label,'source_url':'https://example.org/fixture-result','source_sha256':'b'*64,
        'first_public_at':datetime.now(timezone.utc).isoformat(), 'endpoint_match_note':'Fixture exact primary target'}
    if adjudicated:
        ledger.record_result(target['id'],result,reviewer_id='fixture-reviewer-1')
        ledger.record_result(target['id'],result,reviewer_id='fixture-reviewer-2')
    return ledger, ids, result


def test_read_only_export_verifies_hashes_without_constructor_or_provider(tmp_path,monkeypatch,learning):
    import httpx
    ledger, _, _ = fixture_ledger(tmp_path)
    before = ledger.path.read_bytes()
    events = ledger.events()
    def forbidden(*args,**kwargs):
        raise AssertionError('A report must not initialize a ledger or call a provider')
    monkeypatch.setattr(ArenaLedger,'__init__',forbidden)
    monkeypatch.setattr(httpx,'Client',forbidden)
    report = learning.build_report(learning.read_snapshot(ledger.path))
    assert report['ledger_sha256'] == events[-1]['event_hash']
    assert report['ledger_event_count'] == len(events)
    assert report['provider_calls'] == report['ledger_writes'] == 0
    assert ledger.path.read_bytes() == before
    assert report['report_sha256'] == sha({k:v for k,v in report.items() if k!='report_sha256'})


def test_unrevealed_has_no_accuracy_loss_or_training_claim(tmp_path,learning):
    ledger, _, _ = fixture_ledger(tmp_path)
    report = learning.build_report(learning.read_snapshot(ledger.path))
    assert report['status'] == 'no_eligible_adjudicated_predictions'
    assert report['outcome_losses'] == []
    assert report['summary']['assigned'] == 6 and report['summary']['predicted'] == 2
    assert report['summary']['failed'] == 2 and report['summary']['abstained'] == 1
    assert report['summary']['pending'] == 1 and report['summary']['coverage'] == pytest.approx(1/3)
    for group in [report['summary'], *report['model_groups'], *report['workflow_groups'], *report['project_groups']]:
        assert group['accuracy'] is None and group['brier'] is None and group['log_loss'] is None
    assert all(p['kind']=='engineering_review' and p['training_performed'] is False
               and p['predictive_improvement_claim'] is False for p in report['version_proposals'])
    assert report['training_performed'] is False and report['automatic_deployment'] is False


def test_adjudicated_eligible_predictions_only_scored_and_failures_separate(tmp_path,learning):
    ledger, ids, _ = fixture_ledger(tmp_path,adjudicated=True)
    report = learning.build_report(learning.read_snapshot(ledger.path))
    losses = {r['run_id']:r for r in report['outcome_losses']}
    assert set(losses) == set(ids[:2])
    assert losses[ids[0]]['brier'] == pytest.approx(.04)
    assert losses[ids[1]]['brier'] == pytest.approx(.81)
    assert losses[ids[1]]['error_category']=='predicted_failure_actual_success'
    assert losses[ids[1]]['high_confidence_error'] is True
    assert report['summary']['independent_projects']==1 and report['summary']['scored_targets']==1
    assert report['summary']['accuracy'] is None  # No score pooling across participants.
    engineering = {r['run_id']:r['categories'] for r in report['engineering_observations']}
    assert engineering[ids[2]] == ['abstention']
    assert engineering[ids[3]] == ['timeout']
    assert engineering[ids[4]] == ['citation_invalid']
    proposal = next(p for p in report['version_proposals'] if p['kind']=='evidence_hypothesis_review')
    assert proposal['supporting_run_ids']==[ids[1]] and proposal['status']=='pending_human_review'
    assert proposal['applied'] is False and proposal['candidate_version'] is None
    assert 'independent_holdout_projects' in report['proposal_required_review_fields']
    assert any('newly preregistered' in gate for gate in report['next_version_gates'])


@pytest.mark.parametrize('mutation', ['late_lock','late_tsa','missing_version','incomplete_contract','invalid_probability'])
def test_ineligible_predictions_never_become_outcome_errors(tmp_path,learning,mutation):
    ledger, ids, result = fixture_ledger(tmp_path,adjudicated=True)
    state = learning.read_snapshot(ledger.path)
    run = state['runs'][1]
    if mutation=='late_lock':run['locked_at_utc']=result['first_public_at']
    elif mutation=='late_tsa':run['timestamp_proof']={'status':'attested','generated_at_utc':result['first_public_at']}
    elif mutation=='missing_version':run['returned_model']=None
    elif mutation=='incomplete_contract':run['contract_complete']=False
    elif mutation=='invalid_probability':run['probability']=float('nan')
    report = learning.build_report(state)
    assert [r['run_id'] for r in report['outcome_losses']]==[ids[0]]
    assert not any(p['kind']=='evidence_hypothesis_review' for p in report['version_proposals'])
    assert any(row['run_id']==ids[1] for row in report['excluded_from_loss'])


def test_announced_once_and_not_adjudicable_are_not_binary_labels(tmp_path,learning):
    ledger, _, result = fixture_ledger(tmp_path)
    ledger.record_result('isolated-target',result,reviewer_id='one')
    assert learning.build_report(learning.read_snapshot(ledger.path))['outcome_losses']==[]
    other, _, _ = fixture_ledger(tmp_path/'other',adjudicated=True,label='not_adjudicable')
    report=learning.build_report(learning.read_snapshot(other.path))
    assert report['outcome_losses']==[]
    assert 'outcome_not_binary_adjudicable' in {r['reason'] for r in report['excluded_from_loss']}


@pytest.mark.parametrize('issue', ['source_missing','citation_invalid','evidence_time_invalid'])
def test_evidence_defects_are_engineering_not_loss(tmp_path,learning,issue):
    ledger, ids, result = fixture_ledger(tmp_path,adjudicated=True)
    state=learning.read_snapshot(ledger.path);run=state['runs'][1]
    if issue=='source_missing':run['packet']['evidence'][0].pop('source_sha256')
    elif issue=='citation_invalid':run['evidence_ids']=['fabricated']
    else:run['packet']['evidence'][0]['available_at']=result['first_public_at']
    report=learning.build_report(state)
    assert ids[1] not in {r['run_id'] for r in report['outcome_losses']}
    assert issue in next(r['categories'] for r in report['engineering_observations'] if r['run_id']==ids[1])


def test_grouping_preserves_versions_workflows_and_independent_project_count(tmp_path,learning):
    ledger, _, _ = fixture_ledger(tmp_path,adjudicated=True)
    state=learning.read_snapshot(ledger.path)
    clone=deepcopy(state['runs'][0]);clone.update(id='new-version-fixture',returned_model='changed-returned-v2',
        workflow_version='new-workflow-fixture',track='shared_agent')
    state['runs'].append(clone)
    report=learning.build_report(state)
    groups=[r for r in report['model_groups'] if r['group']['model_id']=='correct']
    assert len(groups)==2
    assert {r['group']['returned_model'] for r in groups}=={'correct-fixed','changed-returned-v2'}
    assert report['summary']['scored_forecasts']==3 and report['summary']['independent_projects']==1
    assert len(report['workflow_groups'])==2


def test_tamper_fails_closed_and_missing_ledger_is_not_created(tmp_path,learning):
    missing=tmp_path/'missing.sqlite3'
    with pytest.raises(FileNotFoundError):learning.read_snapshot(missing)
    assert not missing.exists()
    ledger, _, _=fixture_ledger(tmp_path)
    with sqlite3.connect(ledger.path) as db:
        db.execute('DROP TRIGGER arena_no_update')
        db.execute("UPDATE arena_events SET payload='{}' WHERE seq=1")
    with pytest.raises(ValueError,match='integrity'):learning.read_snapshot(ledger.path)


def test_export_is_new_file_only_and_cli_output_contains_no_raw_errors(tmp_path,monkeypatch,capsys,learning):
    ledger, _, _=fixture_ledger(tmp_path)
    output=tmp_path/'report.json'
    before=hashlib.sha256(ledger.path.read_bytes()).hexdigest()
    monkeypatch.setattr(learning.sys,'argv',['arena_learning_report.py','--ledger',str(ledger.path),
        '--cohort','isolated-cohort','--output',str(output)])
    learning.main()
    summary=json.loads(capsys.readouterr().out)
    assert summary['scored_forecasts']==0 and summary['provider_calls']==0
    body=output.read_bytes();assert output.stat().st_mode & 0o777==0o600
    with pytest.raises(FileExistsError):learning.main()
    assert output.read_bytes()==body and hashlib.sha256(ledger.path.read_bytes()).hexdigest()==before
    with pytest.raises(ValueError,match='Unknown requested cohort'):
        learning.build_report(learning.read_snapshot(ledger.path),cohort_id='unknown')


def test_private_error_text_never_appears_in_offline_export(tmp_path,learning):
    ledger, _, _=fixture_ledger(tmp_path)
    state=learning.read_snapshot(ledger.path)
    state['runs'][3]['error_code']='private-provider-error-with-fixture-secret'
    state['runs'][3]['raw_response']='secret body'
    report=learning.build_report(state)
    encoded=json.dumps(report)
    assert 'fixture-secret' not in encoded and 'secret body' not in encoded
