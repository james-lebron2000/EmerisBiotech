"""Bounded official-source retrieval, identity and evidence provenance tests."""
import hashlib
from urllib.request import Request
import pytest
from emeris_live import official_intake as official, intake
from emeris_live.store import Store

URL='https://www.novartis.com/news/media-releases/shared-guidance'

@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path/'official-store');intake.initialize(value);return value


def config(nct='NCT01234567',trial='STUDY-1',drug='testdrug',url=URL,date=None):
    return {'nct_id':nct,'trial':trial,'drug':drug,'sources':[{'url':url,'source_date':date}]}


def html(body,meta=''):
    return ('<html><head><title>Official research update</title>'+meta+'</head><body>'+body+'</body></html>').encode()


@pytest.mark.parametrize('url',[
    'http://www.novartis.com/news/a','https://novartis.com/news/a',
    'https://www.novartis.com.attacker.example/a','https://attacker.example/?next=www.novartis.com',
    'https://user:pass@www.novartis.com/a','https://www.novartis.com:444/a',
    'https://www.novartis.com:invalid/a','https://127.0.0.1/a','file:///etc/passwd',
])
def test_allowlist_rejects_credentials_ports_lookalikes_and_local_urls(url):
    assert not official.allowed(url)
    with pytest.raises(ValueError):official.fetch_official(url)


def test_redirect_rechecks_target_allowlist():
    redirect=official.SafeRedirect();request=Request(URL)
    for url in ('https://www.gsk.com/en-gb/media/press-releases/research/',URL+'?version=2'):
        assert official.allowed(url)
        assert redirect.redirect_request(request,None,302,'Found',{},url).full_url==url
    for url in ('http://www.novartis.com/a','https://127.0.0.1/a','https://www.novartis.com.attacker.example/a'):
        with pytest.raises(ValueError,match='redirect'):redirect.redirect_request(request,None,302,'Found',{},url)


@pytest.mark.parametrize('final_url,oversize',[(URL,True),('https://attacker.example/file',False)])
def test_fetch_validates_final_destination_and_size(monkeypatch,final_url,oversize):
    class Response:
        url=final_url
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self,limit):
            assert limit==official.MAX_BYTES+1
            return b'x'*limit if oversize else b'valid'
    class Opener:
        def open(self,request,timeout):
            assert request.full_url==URL and timeout==20
            return Response()
    monkeypatch.setattr(official,'build_opener',lambda *args:Opener())
    with pytest.raises(ValueError):official.fetch_official(URL)


@pytest.mark.parametrize('meta',[
    '', '<meta name="completionDate" content="2025-01-01">',
    '<meta property="article:published_time" content="2999-01-01">',
    '<meta property="article:published_time" content="not a date">',
])
def test_completion_dates_or_unknown_publication_never_become_readout_dates(store,meta):
    raw=html('STUDY-1 testdrug primary completion 2025-01-01; follow-up ends 2025-03-02.',meta)
    result=official.collect_official(store,['NCT01234567'],fetcher=lambda _:raw,config=[config()],listings=[])
    assert result['new_candidates']==1
    item=intake.list_candidates(store)[0]['structured']
    assert item['published_at'] is None
    assert item['publication_date_basis']=='unknown'
    assert item['date_kind'] is None and item['label'] is None
    assert 'readout_date' not in item or item['readout_date'] is None


def test_publication_date_has_explicit_provenance_and_never_becomes_label(store):
    raw=html('STUDY-1 testdrug did not meet its primary endpoint.', '<meta property="article:published_time" content="2025-04-03T13:00:00Z">')
    official.collect_official(store,['NCT01234567'],fetcher=lambda _:raw,config=[config(date='2024-01-01')],listings=[])
    item=intake.list_candidates(store)[0]
    assert item['structured']['published_at']=='2025-04-03'
    assert item['structured']['publication_date_basis']=='document_metadata'
    assert item['structured']['label'] is None and not item['auto_verified']


def test_previously_checked_guidance_date_preserved_as_such(store):
    official.collect_official(store,['NCT01234567'],fetcher=lambda _:html('testdrug STUDY-1 guidance'),config=[config(date='2025-03-01')],listings=[])
    item=intake.list_candidates(store)[0]['structured']
    assert item['published_at']=='2025-03-01'
    assert item['publication_date_basis']=='previously_source_checked_guidance_date'
    assert item['date_kind'] is None


def test_one_original_source_can_create_two_trials_without_deduplication_loss(store):
    raw=html('NCT01234567 STUDY-1 testdrug and NCT07654321 STUDY-2 otherdrug')
    records=[config(),config('NCT07654321','STUDY-2','otherdrug')]
    fetches=[]
    def fetch(url):fetches.append(url);return raw
    first=official.collect_official(store,[r['nct_id'] for r in records],fetcher=fetch,config=records,listings=[])
    assert first['new_candidates']==2 and fetches==[URL]
    candidates=intake.list_candidates(store);assert {c['nct_id'] for c in candidates}=={'NCT01234567','NCT07654321'}
    assert {c['source']['url'] for c in candidates}=={URL}
    assert {c['source']['sha256'] for c in candidates}=={hashlib.sha256(raw).hexdigest()}
    again=official.collect_official(store,[r['nct_id'] for r in records],fetcher=fetch,config=records,listings=[])
    assert again['new_candidates']==0 and len(intake.list_candidates(store))==2
    with store.connect() as db:assert db.execute('SELECT count(*) FROM intake_source_blobs').fetchone()[0]==1


def test_trial_alias_requires_drug_and_exact_alias_boundary():
    record=config()
    assert official.identity('testdrug only; study not specified',record)==['drug_name']
    assert official.identity('STUDY-1 unrelated medicine',record)==[]
    assert official.identity('STUDY-10 testdrug',record)==['drug_name']
    assert official.identity('STUDY-1-extension testdrug',record)==['drug_name']
    assert official.identity('Study-1 testdrug',record)==['trial_alias','drug_name']
    assert official.identity('NCT01234567 no drug token',record)==['nct']


def test_matching_news_links_discovered_and_offdomain_links_never_requested(store):
    listing='https://www.novartis.com/news/newsroom'
    news='https://www.novartis.com/news/media-releases/testdrug-update'
    page=html('<a href="/news/media-releases/testdrug-update#details">News</a><a href="https://attacker.example/news/testdrug">Unsafe</a><a href="/news/media-releases/unrelated">Other</a>')
    requested=[]
    def fetch(url):
        requested.append(url)
        if url==listing:return page
        if url==news:return html('testdrug STUDY-1 update')
        raise AssertionError('unexpected source '+url)
    record=config();record['sources']=[]
    report=official.collect_official(store,[record['nct_id']],fetcher=fetch,config=[record],listings=[listing])
    assert requested==[listing,news]
    assert report['new_candidates']==1 and report['failures']==[]
    assert intake.list_candidates(store)[0]['source']['url']==news


def test_fetch_failure_does_not_create_negative_result(store):
    def failed(url):raise TimeoutError('fixture source timeout')
    report=official.collect_official(store,['NCT01234567'],fetcher=failed,config=[config()],listings=[])
    assert report['new_candidates']==0 and len(report['failures'])==1
    assert report['failures'][0]['error_type']=='TimeoutError'
    assert intake.list_candidates(store)==[]
    assert report['matching_is_not_result_verification'] is True


def test_source_sha_and_download_preserve_exact_non_utf8_bytes(store):
    raw=html('testdrug STUDY-1')+b'\xff\xfe\x00'
    official.collect_official(store,['NCT01234567'],fetcher=lambda _:raw,config=[config()],listings=[])
    item=intake.list_candidates(store)[0]
    assert item['source']['hash_scope']=='source_response_bytes'
    downloaded,source=intake.get_source_bytes(store,item['id'])
    assert downloaded==raw
    assert source['source']['sha256']==hashlib.sha256(raw).hexdigest()
    assert b'\xff\xfe' in downloaded


def test_script_and_style_content_cannot_supply_trial_identity():
    doc=official.extract(html('<script>testdrug STUDY-1</script><style>testdrug STUDY-1</style><p>Unrelated update</p>'))
    assert official.identity(doc['text'],config())==[]
