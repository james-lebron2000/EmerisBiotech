from copy import deepcopy
from email.message import Message
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError,URLError
import hashlib,json,subprocess
import pytest
from emeris_live import official_intake as mod
from emeris_live.store import Store

URL='https://www.daiichisankyo.com/files/official.pdf'

@pytest.mark.parametrize('status,category',[(401,'access_denied'),(403,'access_denied'),(404,'not_found'),(429,'rate_limited'),(503,'source_unavailable')])
def test_http_diagnostics_are_bounded_not_clinical_labels(status,category):
    h=Message();h['Server']='CloudFront';h['Retry-After']='120';h['Set-Cookie']='PRIVATE'
    err=HTTPError(URL,status,'PRIVATE exception reason',h,BytesIO(b'PRIVATE response'))
    r=mod.failure_details('official_document',URL,err)
    assert r['category']==category and r['http_status']==status
    assert r['response_server']=='CloudFront' and r['retry_after_seconds']==120
    assert 'PRIVATE' not in json.dumps(r) and 'label' not in r
    assert r['checked_at'].endswith('+00:00')

@pytest.mark.parametrize('retry', ['9999999999','-1','tomorrow','90000','１２３'])
def test_unbounded_or_ambiguous_retry_headers_are_not_instructions(retry):
    h=Message();h['Retry-After']=retry;h['Server']='<script>bad()</script>'
    r=mod.failure_details('official_listing',URL,HTTPError(URL,429,'hidden',h,None))
    assert r['retry_after_seconds'] is None and r['response_server'] is None

@pytest.mark.parametrize('error',[TimeoutError('secret'),URLError(TimeoutError('secret'))])
def test_timeout_is_explicit_without_invented_http_status(error):
    r=mod.failure_details('official_listing',URL,error)
    assert r['http_status'] is None and r['category']=='timeout'

@pytest.mark.parametrize('error,category',[
    (subprocess.CalledProcessError(1,'private command',stderr=b'PRIVATE'),'parse_error'),
    (subprocess.TimeoutExpired('private command',60,stderr=b'PRIVATE'),'parse_timeout')])
def test_parser_errors_are_separate_from_http_and_never_expose_stderr(error,category):
    r=mod.failure_details('official_document',URL,error)
    assert r['category']==category and r['http_status'] is None
    assert 'PRIVATE' not in json.dumps(r) and 'private command' not in json.dumps(r)


def setup_config(tmp_path,monkeypatch):
    root=tmp_path/'project';d=root/'dashboard_data';d.mkdir(parents=True)
    raw=b'Immutable source fixture';(d/'watch.pdf').write_bytes(raw)
    original={'trials':[{'nct_id':'NCT01234567','trial':'AVANZAR','drug':'testdrug','sources':[{'url':'https://www.astrazeneca.com/original.pdf','source_date':None}]}]}
    (d/'evidence_updates.json').write_text(json.dumps(original))
    extra={'schema_version':'predome.official_watch_sources.v1','records':[{'nct_id':'NCT01234567','trial':'AVANZAR','drug':'testdrug','sources':[{'url':URL,'source_date':None,'path':'dashboard_data/watch.pdf','sha256':hashlib.sha256(raw).hexdigest(),'source_role':'additional_official_watch_source'}]}]}
    path=d/'official_watch_sources.json';path.write_text(json.dumps(extra));monkeypatch.setattr(mod,'ROOT',root)
    return root,path,extra,original


def test_additional_source_is_additive_preserves_old_guidance_and_unknown_date(tmp_path,monkeypatch):
    root,path,extra,original=setup_config(tmp_path,monkeypatch)
    before=(root/'dashboard_data/evidence_updates.json').read_bytes()
    records=mod.tracked_config()
    assert len(records[0]['sources'])==2 and records[0]['sources'][0]==original['trials'][0]['sources'][0]
    assert records[0]['sources'][1]['source_date'] is None
    assert (root/'dashboard_data/evidence_updates.json').read_bytes()==before
    extra['records'][0]['sources']*=2;path.write_text(json.dumps(extra))
    assert len(mod.tracked_config()[0]['sources'])==2

@pytest.mark.parametrize('change',['identity','sha','path','url','role','future_date'])
def test_extra_source_integrity_failure_is_visible_and_does_not_fetch(tmp_path,monkeypatch,change):
    root,path,extra,_=setup_config(tmp_path,monkeypatch);record=extra['records'][0];s=record['sources'][0]
    if change=='identity':record['trial']='Different trial'
    elif change=='sha':s['sha256']='0'*64
    elif change=='path':s['path']='../../outside'
    elif change=='url':s['url']='https://attacker.invalid/doc.pdf'
    elif change=='role':s['source_role']='forecast_input'
    else:s['source_date']='2999-01-01'
    path.write_text(json.dumps(extra))
    with pytest.raises((ValueError,OSError)):mod.tracked_config()
    fetched=[]
    r=mod.collect_official(Store(tmp_path/'store'),['NCT01234567'],fetcher=lambda u:fetched.append(u))
    assert not fetched and r['new_candidates']==0 and r['sources_checked']==0
    assert r['failures'][0]['category']=='integrity_error'


def test_real_collection_preserves_failed_request_diagnostics(tmp_path):
    def fetch(url):raise HTTPError(url,403,'hidden response',Message(),None)
    r=mod.collect_official(Store(tmp_path/'store'),['NCT01234567'],fetcher=fetch,
        config=[{'nct_id':'NCT01234567','trial':'AVANZAR','drug':'testdrug','sources':[{'url':URL}]}],listings=[URL+'/index'])
    assert len(r['failures'])==2 and all(f['category']=='access_denied' for f in r['failures'])
    assert r['new_candidates']==0 and r['sources_checked']==0
