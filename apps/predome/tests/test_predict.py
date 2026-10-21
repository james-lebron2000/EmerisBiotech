from datetime import datetime,timezone
from io import BytesIO
import json
import math
import zipfile

import pytest
from pydantic import ValidationError
from fastapi.testclient import TestClient
from emeris_live import prediction_service as svc
from emeris_live.main import create_app


def profile():
    p=svc.empty_profile()
    for key,value in {'trial_name':'Test research','nct_id':'NCT12345678','indication':'Research indication','drug':'Test intervention','population':'Declared enrolled adults','primary_endpoint':'PFS','comparator':'Placebo'}.items():
        p['fields'][key]={'value':value,'origin':'user','confirmed':True}
    return p


def request_payload(method='evidence_review'):
    return {'profile':profile(),'target':{'endpoint':'PFS','population':'Declared enrolled adults','comparison':'Treatment versus placebo','success_definition':'Favorable final PFS log-rank test under explicitly stated single-analysis assumptions'},'data_cutoff':'2026-01-01','method':method,'confirm_fields_reviewed':True,'evidence':[]}


def scenario_payload():
    r=request_payload('scenario')
    r['scenario']={'endpoint':'PFS','prior_hr_median':.8,'prior_log_hr_sd':.2,'event_count':300,'allocation':.5,'alpha':.05,'transport_sd':.1,'clinical_hr_threshold':.8,'acknowledged_conditional_only':True}
    return r


def registry_bytes():
    return json.dumps({'hasResults':False,'protocolSection':{'identificationModule':{'nctId':'NCT12345678','briefTitle':'Registry title'},'statusModule':{'lastUpdatePostDateStruct':{'date':'2025-01-01'},'primaryCompletionDateStruct':{'date':'2026-12-31','type':'ESTIMATED'}},'designModule':{'phases':['PHASE3'],'enrollmentInfo':{'count':123,'type':'ESTIMATED'}},'conditionsModule':{'conditions':['Test condition']},'outcomesModule':{'primaryOutcomes':[{'measure':'First endpoint','timeFrame':'52 weeks'},{'measure':'Second endpoint','timeFrame':'104 weeks'}]}}}).encode()


def docx(text):
    b=BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>'+text+'</w:t></w:r></w:p></w:body></w:document>')
    return b.getvalue()


def test_registry_identity_dates_and_missing_are_preserved():
    r=svc.registry_profile('NCT12345678',registry_bytes())
    assert r['fields']['nct_id']['origin']=='public'
    assert r['fields']['mechanism']['value'] is None
    assert r['fields']['primary_endpoint']['value']=='First endpoint\nSecond endpoint'
    assert r['date_semantics']['readout_date'] is None
    assert r['date_semantics']['primary_completion']['date']=='2026-12-31'
    assert r['source']['sha256']
    with pytest.raises(ValueError):svc.registry_profile('NCT99999999',registry_bytes())
    with pytest.raises(ValueError):svc.registry_profile('http://localhost',registry_bytes())


def test_docx_inert_extraction_marks_suggestions_not_facts():
    r=svc.parse_document(docx('Primary Endpoint: Progression-free survival'),'trial.docx')
    assert r['fields']['primary_endpoint']['value']=='Progression-free survival'
    assert r['fields']['primary_endpoint']['origin']=='inferred'
    assert not r['fields']['primary_endpoint']['confirmed']
    assert r['fields']['sample_size']['value'] is None
    assert 'sha256' in r['document']
    with pytest.raises(Exception):svc.parse_document(docx('&xxe;'),'unsafe.docx')
    with pytest.raises(ValueError):svc.parse_document(b'hello','trial.exe')
    with pytest.raises(ValueError):svc.parse_document(b'x'*(svc.MAX_UPLOAD+1),'trial.docx')


def test_zip_bomb_and_macros_rejected():
    b=BytesIO()
    with zipfile.ZipFile(b,'w',compression=zipfile.ZIP_DEFLATED) as z:z.writestr('word/document.xml','x'*1000000)
    with pytest.raises(ValueError,match='压缩率'):svc.parse_document(b.getvalue(),'x.docx')
    b=BytesIO()
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('word/vbaProject.bin','x');z.writestr('word/document.xml','<x/>')
    with pytest.raises(ValueError,match='宏'):svc.parse_document(b.getvalue(),'x.docx')


def test_no_default_assumptions_or_scenario_as_trial_forecast():
    req=svc.PredictionRequest(**scenario_payload());r=svc.create_report(req)
    assert r['forecasts']=={}
    assert r['forecast_kind']=='conditional_scenario'
    assert 0<r['scenario']['summary']['assurance']<1
    assert r['primary_prediction_status']=='not_available'
    missing=scenario_payload();del missing['scenario']['event_count']
    with pytest.raises(ValidationError):svc.PredictionRequest(**missing)
    mismatch=scenario_payload();mismatch['target']['endpoint']='HbA1c'
    with pytest.raises(ValidationError):svc.PredictionRequest(**mismatch)


def test_evidence_cutoff_and_user_confirmation():
    x=request_payload();x['evidence']=[{'id':'later','text':'Eligible evidence text','availability_date':'2026-02-01'}]
    with pytest.raises(ValidationError):svc.PredictionRequest(**x)
    x=request_payload();x['profile']['fields']['drug']['confirmed']=False
    with pytest.raises(ValueError,match='逐项确认'):svc.create_report(svc.PredictionRequest(**x))
    x=request_payload();x['profile']['fields']['drug']['available_at']='2026-02-01'
    with pytest.raises(ValueError,match='晚于'):svc.create_report(svc.PredictionRequest(**x))


def test_provider_absence_no_synthetic_probability(monkeypatch):
    monkeypatch.delenv('EMERIS_ENABLE_USER_LLM',raising=False);monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    x=request_payload('llm');x.update(models=['deepseek-flash','deepseek-v4-pro'],allow_external_processing=True)
    r=svc.create_report(svc.PredictionRequest(**x))
    assert all(f['probability'] is None and f['status']=='unavailable' for f in r['forecasts'].values())
    assert r['ensemble_probability'] is None
    assert r['model_disagreement']['max_minus_min'] is None


def test_provider_output_target_and_citations_strict():
    x=request_payload('llm');x.update(models=['deepseek-flash'],allow_external_processing=True,evidence=[{'id':'source1','text':'User-reviewed randomized prior evidence','availability_date':'2025-01-01'}]);req=svc.PredictionRequest(**x)
    valid={'status':'predicted','probability':.6,'target_definition':req.target.success_definition,'reasoning':'来源有限的研究判断','evidence_ids':['source1'],'key_drivers':[],'risk_factors':[],'assumptions':[],'key_unknowns':[]}
    assert svc.validate_llm(valid,req)['probability']==.6
    for update in ({'probability':float('nan')},{'probability':True},{'target_definition':'other event'},{'evidence_ids':['invented']},{'status':'abstain','probability':.5}):
        with pytest.raises(ValueError):svc.validate_llm(valid|update,req)


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path/'store')) as c:yield c


def login(client,name='predict_user'):
    r=client.post('/api/auth/register',json={'name':name,'password':'TestOnlyPassword123!'},headers={'Origin':'http://testserver'})
    assert r.status_code==200,r.text
    return {'Origin':'http://testserver','x-csrf-token':r.json()['csrf']}


def test_auth_csrf_and_private_report_roundtrip(client):
    assert client.post('/api/predict/run',json=request_payload(),headers={'Origin':'http://testserver'}).status_code==401
    headers=login(client)
    assert client.post('/api/predict/run',json=request_payload(),headers={'Origin':'http://testserver'}).status_code==403
    run=client.post('/api/predict/run',json=request_payload(),headers=headers)
    assert run.status_code==200,run.text
    result=run.json();assert result['status']=='draft' and result['private'] is True
    pid=result['prediction_id'];saved=client.get('/api/platform/predictions/'+pid)
    assert saved.status_code==200 and saved.json()['trial_id']=='NCT12345678'
    assert client.post('/api/platform/predictions/'+pid+'/lock',json={},headers=headers).status_code==422
    login(client,'another_user')
    assert client.get('/api/platform/predictions/'+pid).status_code==404


def test_registry_provenance_cannot_be_forged(client,monkeypatch):
    monkeypatch.setattr('emeris_live.predict_api.retrieve_registry',lambda n:svc.registry_profile(n,registry_bytes()))
    imported=client.get('/api/predict/registry/NCT12345678').json();assert imported['import_receipt']
    headers=login(client)
    x=request_payload();x['profile']['import_receipt']=imported['import_receipt']
    x['profile']['fields']['trial_name']={**imported['fields']['trial_name'],'confirmed':True}
    first=client.post('/api/predict/run',json=x,headers=headers);assert first.status_code==200,first.text
    assert first.json()['report']['profile']['fields']['trial_name']['origin']=='public'
    x['profile']['fields']['trial_name']['value']='Altered text'
    changed=client.post('/api/predict/run',json=x,headers=headers);assert changed.status_code==200,changed.text
    assert changed.json()['report']['profile']['fields']['trial_name']['origin']=='user'
    x['profile']['import_receipt']='not-a-valid-token'
    assert client.post('/api/predict/run',json=x,headers=headers).status_code==422


def test_upload_no_external_call_and_report_parent_version(client,monkeypatch):
    headers=login(client)
    monkeypatch.setattr(svc,'call_model',lambda *a:pytest.fail('Upload must not call model'))
    parsed=client.post('/api/predict/parse',files={'file':('study.docx',docx('Study Drug: Research molecule'))},headers=headers)
    assert parsed.status_code==200,parsed.text
    assert parsed.json()['fields']['drug']['origin']=='inferred'
    first=client.post('/api/predict/run',json=request_payload(),headers=headers).json()
    x=request_payload();x['parent_prediction_id']=first['prediction_id']
    second=client.post('/api/predict/run',json=x,headers=headers)
    assert second.status_code==200,second.text
    assert second.json()['prediction_id']!=first['prediction_id']


def test_pdf_text_extraction_isolated_and_encrypted_rejected():
    pdf=pytest.importorskip('pypdf')
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=pdf.PdfWriter();page=writer.add_blank_page(width=300,height=300)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 10 250 Td (Primary Endpoint: Overall survival) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    out=BytesIO();writer.write(out)
    result=svc.parse_document(out.getvalue(),'research.pdf')
    assert result['document']['pages']==1
    assert result['fields']['primary_endpoint']['value']=='Overall survival'
    writer.encrypt('test-encrypted');out=BytesIO();writer.write(out)
    with pytest.raises(ValueError,match='未能'):svc.parse_document(out.getvalue(),'encrypted.pdf')


def protocol_pdf(pages=40):
    """Synthetic text protocol with headings and repeated body, not patient data."""
    pdf=pytest.importorskip('pypdf')
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=pdf.PdfWriter()
    for index in range(pages):
        page=writer.add_blank_page(width=612,height=792)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
        lines=['Trial Title: Representative synthetic protocol','Primary Endpoint: Progression-free survival',
               'Study Population: Adults meeting prespecified eligibility']+[
                   'Protocol section %d. Assessment visit schedule and endpoint adjudication.' % index]*35
        stream=DecodedStreamObject()
        stream.set_data(('BT /F1 10 Tf 40 750 Td 15 TL '+' '.join('('+line+') Tj T*' for line in lines)+' ET').encode())
        page[NameObject('/Contents')]=writer._add_object(stream)
        page.compress_content_streams()
    out=BytesIO();writer.write(out);return out.getvalue()


def test_representative_compressed_protocol_and_parser_budget(monkeypatch):
    calls=[];real_run=svc.subprocess.run
    def capture(*args,**kwargs):
        calls.append((args,kwargs));return real_run(*args,**kwargs)
    monkeypatch.setattr(svc.subprocess,'run',capture)
    result=svc.parse_document(protocol_pdf(),'representative-protocol.pdf')
    assert result['document']['pages']==40
    assert result['document']['characters']<=svc.MAX_TEXT
    assert result['fields']['primary_endpoint']['value']=='Progression-free survival'
    args,kwargs=calls[0]
    assert args[0][-1]==str(128*1024*1024)
    assert kwargs['timeout']==20 and kwargs['stderr']==svc.subprocess.DEVNULL


def test_parser_rejects_concurrent_work_without_queueing_and_recovers(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    entered=Event();release=Event();original=svc._parse_document
    def slow(*args):
        entered.set();assert release.wait(timeout=3);return original(*args)
    monkeypatch.setattr(svc,'_parse_document',slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending=pool.submit(svc.parse_document,docx('Primary Endpoint: Survival'),'one.docx')
        assert entered.wait(timeout=3)
        try:
            with pytest.raises(ValueError,match='正在处理'):svc.parse_document(b'%PDF-','two.pdf')
        finally:release.set()
        assert pending.result(timeout=3)['fields']['primary_endpoint']['value']=='Survival'
    monkeypatch.setattr(svc,'_parse_document',original)
    assert svc.parse_document(docx('Primary Endpoint: Survival'),'three.docx')


def test_timeout_and_invalid_document_release_parser_slot(monkeypatch):
    def timeout(*args,**kwargs):raise svc.subprocess.TimeoutExpired(args[0],20)
    monkeypatch.setattr(svc.subprocess,'run',timeout)
    with pytest.raises(ValueError,match='未能'):svc.parse_document(b'%PDF-1.7','timeout.pdf')
    with pytest.raises(ValueError,match='仅支持'):svc.parse_document(b'invalid','invalid.exe')
    assert svc.parse_document(docx('Primary Endpoint: Survival'),'recovered.docx')


def test_signed_comparable_receipt_in_report_not_model_evidence(client,monkeypatch):
    value={'schema_version':'predome.comparables.v1','records':[{'nct_id':'NCT87654321','source_url':'https://clinicaltrials.gov/study/NCT87654321'}],'selection':'Same-condition registry match, not effect transfer','retrieved_at':'2026-01-01T00:00:00+00:00'}
    monkeypatch.setattr('emeris_live.predict_api.retrieve_comparables',lambda *a:value.copy())
    result=client.get('/api/predict/comparables',params={'condition':'Test condition'});assert result.status_code==200
    headers=login(client)
    body=request_payload();body['comparable_receipt']=result.json()['receipt']
    report=client.post('/api/predict/run',json=body,headers=headers)
    assert report.status_code==200,report.text
    assert report.json()['report']['comparable_trials'][0]['nct_id']=='NCT87654321'
    assert report.json()['report']['evidence']==[]
    body['comparable_receipt']='invalid'
    assert client.post('/api/predict/run',json=body,headers=headers).status_code==422


def test_external_processing_requires_explicit_consent():
    value=request_payload('llm');value['models']=['deepseek-flash']
    with pytest.raises(ValidationError):svc.PredictionRequest(**value)
