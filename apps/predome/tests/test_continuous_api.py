"""Continuous release serving integrity and read-only boundaries."""
from pathlib import Path
import json,hashlib
import pytest
from fastapi.testclient import TestClient
from emeris_live import continuous_api
from emeris_live.main import create_app
from emeris_live.predome_statistics import sha

@pytest.fixture
def bundle(tmp_path,monkeypatch):
    root=tmp_path/'root';data=root/'predome_data/continuous_v4';data.mkdir(parents=True)
    report=root/continuous_api.REPORT;report.parent.mkdir();report.write_text('No new accuracy yet.\n')
    plan=root/continuous_api.OPTIMIZATION;plan.write_text('70% is a research target.\n')
    record={'benchmark':{'model_training_completed':False},'artifact_manifest':[{'path':str(p.relative_to(root)),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [report,plan]]}
    def freeze():(data/'release.json').write_text(json.dumps({'record':record,'sha256':sha(record)}))
    freeze();monkeypatch.setattr(continuous_api,'ROOT',root);monkeypatch.setattr(continuous_api,'DATA',data)
    client=TestClient(create_app(tmp_path/'store'));client.headers['Origin']='http://testserver'
    return client,record,report,plan,freeze

def test_readonly_and_artifact_bound_reports(bundle):
    client,record,report,plan,_=bundle
    response=client.get('/api/predome/continuous')
    assert response.status_code==200 and response.json()['integrity_verified']
    assert client.get('/api/predome/continuous/export').json()==response.json()
    assert client.get('/api/predome/continuous/report').text==report.read_text()
    assert client.get('/api/predome/continuous/optimization').text==plan.read_text()
    for suffix in ['','/export','/report','/optimization']:
        assert client.post('/api/predome/continuous'+suffix).status_code==405

@pytest.mark.parametrize('change',['tamper','missing','unlisted_report','unlisted_plan','relative_escape','duplicate'])
def test_integrity_fails_closed(bundle,change):
    client,record,report,plan,freeze=bundle
    suffix='/report'
    if change=='tamper':report.write_text('altered')
    elif change=='missing':report.unlink()
    elif change=='unlisted_report':record['artifact_manifest']=record['artifact_manifest'][1:];freeze()
    elif change=='unlisted_plan':record['artifact_manifest']=record['artifact_manifest'][:1];freeze();suffix='/optimization'
    elif change=='relative_escape':record['artifact_manifest'][0]['path']='../outside';freeze()
    elif change=='duplicate':record['artifact_manifest'].append(dict(record['artifact_manifest'][0]));freeze()
    response=client.get('/api/predome/continuous'+suffix)
    assert response.status_code==503
    assert str(report.parent) not in response.text
