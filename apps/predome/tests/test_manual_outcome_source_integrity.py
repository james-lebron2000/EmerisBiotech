"""The manual API cannot impersonate server-verified intake provenance."""
import hashlib
import pytest
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from emeris_live.store import Store
from emeris_live import platform_store
from emeris_live.platform_store import PlatformStore
from emeris_live.platform_api import router

CONFIRM={k:True for k in ('endpoint_match','population_match','comparison_match','source_checked','prespecified_analysis','earliest_result_checked','evidence_cutoff_checked')}

@pytest.fixture
def manual(tmp_path,monkeypatch):
    monkeypatch.setenv('EMERIS_REVIEWERS','independent')
    monkeypatch.setattr(platform_store,'now',lambda:'2025-01-01T00:00:00+00:00')
    store=Store(tmp_path/'isolated');ledger=PlatformStore(store)
    target={'endpoint':'Synthetic PFS','population':'Synthetic adults','comparison':'Synthetic drug vs placebo','success_definition':'Prespecified adjusted superiority'}
    run=ledger.create_run('owner',{'nct_id':'NCT12345678'},{'forecast_kind':'uncalibrated_llm_research','forecasts':{'test':{'status':'predicted','probability':.7}}},model_version='test',pipeline_version='test',data_cutoff='2024-12-31',target=target,evidence=[{'url':'https://example.invalid/prior','availability_date':'2024-12-30'}])
    snap=ledger.lock('owner',run['id'],publish=True)
    monkeypatch.setattr(platform_store,'now',lambda:'2025-03-01T00:00:00+00:00')
    app=FastAPI();app.state.store=store
    def actor(request):
        user=request.headers.get('x-user')
        if not user:raise HTTPException(401)
        return {'owner':user,'name':user}
    app.state.platform_user=actor;app.include_router(router)
    outcome={'trial_id':'NCT12345678',**{k:target[k] for k in ('endpoint','population','comparison')},'label':1,'readout_date':'2025-02-01','source':{'url':'https://www.astrazeneca.com/synthetic-manual','published_at':'2025-02-01','sha256':'a'*64},'extraction_status':'complete','conflicts':[]}
    return TestClient(app),ledger,snap,outcome


@pytest.mark.parametrize('extra',[
    {'candidate_id':'does-not-exist'},
    {'candidate_kind':'official_announcement_candidate'},
    {'quote_validation':'continuous_normalized_excerpt_reextracted_from_verified_source_bytes'},
    {'excerpt':'Fabricated excerpt not present in any real source.'},
    {'excerpt_sha256':hashlib.sha256(b'Fabricated excerpt').hexdigest()},
    {'publication_date_basis':'document_metadata'},
    {'candidate_kind':'official_announcement_candidate','candidate_id':'missing','quote_validation':'continuous_normalized_excerpt_reextracted_from_verified_source_bytes','excerpt':'Fabricated excerpt not present in any real source.','excerpt_sha256':hashlib.sha256(b'Fabricated excerpt not present in any real source.').hexdigest(),'publication_date_basis':'document_metadata'},
])
def test_manual_http_source_cannot_claim_machine_validation(manual,extra):
    client,ledger,snapshot,outcome=manual;outcome['source'].update(extra)
    response=client.post('/api/platform/outcomes',json={'snapshot_id':snapshot['record']['id'],'outcome':outcome},headers={'x-user':'owner'})
    assert response.status_code==422,response.text
    assert ledger.list_outcomes(reviewer=True)==[]
    assert ledger.public_outcome_records()==[]
    assert ledger.evaluation_rows()==[]


def test_manual_reference_can_be_independently_reviewed_without_machine_provenance(manual):
    client,ledger,snapshot,outcome=manual
    response=client.post('/api/platform/outcomes',json={'snapshot_id':snapshot['record']['id'],'outcome':outcome},headers={'x-user':'owner'})
    assert response.status_code==200,response.text
    saved=response.json()
    assert saved['outcome']['source']['provenance']=='manual_source_reference_not_machine_verified'
    reviewed=client.post('/api/platform/reviews/'+saved['id'],json={'decision':'approved','reason':'Synthetic manual source independently checked for audit.','confirmations':CONFIRM},headers={'x-user':'independent'})
    assert reviewed.status_code==200,reviewed.text
    assert ledger.public_outcome_records()[0]['source']==outcome['source']
    assert 'excerpt' not in ledger.public_outcome_records()[0]['source']
