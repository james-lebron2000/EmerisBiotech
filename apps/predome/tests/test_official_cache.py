"""Source-bound parser cache; every run still fetches official response bytes.

Run: python -m pytest tests/test_official_cache.py -q
The first test is the isolated same-source-twice operational contract.
"""
import hashlib
import subprocess
import pytest
from emeris_live import intake, official_intake as official
from emeris_live.store import Store

URL='https://www.gsk.com/media/research-guidance.pdf'
NCT='NCT01234567'
RAW=b'%PDF-1.4 fixture original bytes'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    store=Store(tmp_path/'store')
    calls=[]
    def extract(raw):
        calls.append(raw)
        return {'text':'NCT01234567 STUDY-1 testdrug and NCT07654321 STUDY-2 otherdrug',
                'title':'Guidance','published_at':None,'links':[],'content_type':'application/pdf'}
    monkeypatch.setattr(official,'extract',extract)
    return store,calls


def record(nct=NCT,trial='STUDY-1',drug='testdrug',date='2025-01-01',url=URL):
    return {'nct_id':nct,'trial':trial,'drug':drug,'sources':[{'url':url,'source_date':date}]}


def collect(store,raw=RAW,records=None,fetches=None):
    records=records or [record()]
    def fetch(url):
        if fetches is not None:fetches.append(url)
        return raw
    return official.collect_official(store,[r['nct_id'] for r in records],fetcher=fetch,config=records,listings=[])


def test_same_source_twice_still_fetches_but_parses_once_and_preserves_dates(setup,monkeypatch):
    store,calls=setup;fetches=[]
    monkeypatch.setattr(intake,'now',lambda:'2026-01-01T00:00:00+00:00')
    first=collect(store,fetches=fetches)
    candidate=intake.list_candidates(store)[0]
    monkeypatch.setattr(intake,'now',lambda:'2026-01-02T00:00:00+00:00')
    second=collect(store,records=[record(date='2025-02-01')],fetches=fetches)
    assert fetches==[URL,URL] and calls==[RAW]
    assert first['parser_runs']==1 and first['cache_hits']==0
    assert second['parser_runs']==0 and second['cache_hits']==1 and second['new_candidates']==0
    assert second['sources_checked']==1 and second['failures']==[]
    assert second['source_checks'][0]['checked_at']=='2026-01-02T00:00:00+00:00'
    after=intake.list_candidates(store)[0]
    assert after==candidate and after['source']['retrieved_at']=='2026-01-01T00:00:00+00:00'
    assert after['structured']['published_at']=='2025-01-01'
    assert after['structured']['parser_version']==official.PARSER_VERSION
    with store.connect() as db:
        row=db.execute('SELECT payload FROM official_parse_cache').fetchone()
    assert b'Guidance' not in row['payload']
    assert store.unpack(row['payload'])['document']['published_at'] is None


def test_changed_raw_requires_new_parse_and_source_hash(setup):
    store,calls=setup;collect(store)
    changed=RAW+b' changed'
    report=collect(store,changed)
    assert calls==[RAW,changed] and report['parser_runs']==1 and report['cache_hits']==0
    assert report['new_candidates']==1
    assert {x['source']['sha256'] for x in intake.list_candidates(store)}=={hashlib.sha256(x).hexdigest() for x in (RAW,changed)}


def test_parser_version_change_requires_reparse(setup,monkeypatch):
    store,calls=setup;collect(store)
    monkeypatch.setattr(official,'PARSER_VERSION','official-document-vNEXT')
    report=collect(store)
    assert len(calls)==2 and report['cache_hits']==0 and report['parser_runs']==1
    assert collect(store)['cache_hits']==1  # New cache may bind to same immutable legacy candidate.
    assert intake.list_candidates(store)[0]['structured']['parser_version']!='official-document-vNEXT'


@pytest.mark.parametrize('corruption',['ciphertext','incomplete','wrong_version','wrong_url'])
def test_corrupt_extraction_cache_falls_back_without_negative_result(setup,corruption):
    store,calls=setup;collect(store)
    with store.connect() as db:
        row=db.execute('SELECT payload FROM official_parse_cache').fetchone()
        value=store.unpack(row['payload'])
        if corruption=='incomplete':value['document'].pop('links')
        if corruption=='wrong_version':value['parser_version']='obsolete'
        if corruption=='wrong_url':value['url']='https://www.novartis.com/other'
        payload=b'corrupt ciphertext' if corruption=='ciphertext' else store.pack(value)
        db.execute('UPDATE official_parse_cache SET payload=?',(payload,))
    report=collect(store)
    assert len(calls)==2 and report['cache_hits']==0 and report['cache_rejections']==1
    assert report['cache_warnings'][0]['reason']=='extraction_cache_verification_failed'
    assert report['failures']==[]
    assert intake.list_candidates(store)[0]['structured']['label'] is None


@pytest.mark.parametrize('corruption',['ciphertext','wrong_bytes','missing'])
def test_corrupt_source_blob_never_hits_or_is_silently_repaired(setup,corruption):
    store,calls=setup;collect(store)
    with store.connect() as db:
        if corruption=='missing':db.execute('DELETE FROM intake_source_blobs')
        else:
            payload=b'corrupt' if corruption=='ciphertext' else store.pack({'bytes_b64':'d3Jvbmc='})
            db.execute('UPDATE intake_source_blobs SET payload=?',(payload,))
    report=collect(store)
    assert len(calls)==2 and report['cache_hits']==0 and report['cache_rejections']==1
    assert report['failures'][0]['error_type']=='SourceIntegrityError'
    assert intake.list_candidates(store)[0]['structured']['label'] is None
    with store.connect() as db:
        rows=db.execute('SELECT payload FROM intake_source_blobs').fetchall()
    assert rows==[] if corruption=='missing' else rows[0]['payload']==payload


def test_same_pdf_multi_nct_parses_once_and_keeps_separate_candidates(setup):
    store,calls=setup;records=[record(),record('NCT07654321','STUDY-2','otherdrug')]
    first=collect(store,records=records);second=collect(store,records=records)
    assert len(calls)==1 and first['new_candidates']==2 and second['cache_hits']==1
    candidates=intake.list_candidates(store)
    assert {x['nct_id'] for x in candidates}=={NCT,'NCT07654321'}
    assert all(x['structured']['label'] is None for x in candidates)


def test_legacy_candidate_without_parser_version_cannot_seed_extraction_cache(setup):
    store,calls=setup;intake.initialize(store)
    intake.save_candidate(store,NCT,'official_announcement_candidate',URL,RAW,
                          {'text':'forged legacy text'},source_key=URL+'#predome-trial='+NCT,hash_scope='source_response_bytes')
    report=collect(store)
    assert len(calls)==1 and report['parser_runs']==1 and report['cache_hits']==0
    assert collect(store)['cache_hits']==1


def test_url_binding_does_not_reuse_equal_bytes_at_different_url(setup):
    store,calls=setup;collect(store)
    report=collect(store,records=[record(url='https://www.gsk.com/media/other-guidance.pdf')])
    assert len(calls)==2 and report['cache_hits']==0 and report['new_candidates']==1


def test_fetch_or_parser_timeout_does_not_use_old_cache_or_create_negative(setup,monkeypatch):
    store,calls=setup;collect(store)
    def failed(_):raise TimeoutError('network fixture')
    report=official.collect_official(store,[NCT],fetcher=failed,config=[record()],listings=[])
    assert report['sources_checked']==0 and report['cache_hits']==0 and report['failures']
    def timeout(_):raise subprocess.TimeoutExpired('pdf',official.PDF_WALL_TIMEOUT)
    monkeypatch.setattr(official,'extract',timeout)
    report=collect(store,RAW+b'new')
    assert report['sources_checked']==0 and report['parser_runs']==1 and report['failures']
    assert len(intake.list_candidates(store))==1
    assert intake.list_candidates(store)[0]['structured']['label'] is None


def test_pdf_wall_budget_keeps_cpu_and_memory_limits(monkeypatch):
    def run(command,**kwargs):
        assert kwargs['timeout']==60
        assert 'RLIMIT_CPU,(12,12)' in command[-1]
        assert 'RLIMIT_AS,(134217728,134217728)' in command[-1]
        return subprocess.CompletedProcess(command,0,b'{"text":"source","title":"PDF"}')
    monkeypatch.setattr(official.subprocess,'run',run)
    assert official.extract(RAW)['content_type']=='application/pdf'


@pytest.mark.parametrize('password,allow_text,accepted',[
    ('',True,True),('',False,False),('nonempty-password',True,False)])
def test_official_encrypted_pdf_requires_passwordless_open_and_text_permission(password,allow_text,accepted):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.constants import UserAccessPermissions
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    permissions=UserAccessPermissions.EXTRACT_TEXT_AND_GRAPHICS if allow_text else UserAccessPermissions(0)
    writer.encrypt(password,owner_password='not-used-by-parser',permissions_flag=permissions,algorithm='AES-256')
    buffer=BytesIO();writer.write(buffer)
    if accepted:
        assert official.extract(buffer.getvalue())['content_type']=='application/pdf'
    else:
        with pytest.raises(subprocess.CalledProcessError):official.extract(buffer.getvalue())


def test_cache_never_bypasses_domain_validation(setup):
    store,calls=setup;collect(store)
    requested=[]
    report=official.collect_official(store,[NCT],fetcher=lambda u:requested.append(u),
        config=[record(url='https://attacker.example/file.pdf')],listings=['https://attacker.example/news/'])
    assert requested==[] and report['sources_checked']==0 and report['cache_hits']==0
    assert report['failures'][0]['error_type']=='ValueError'


def test_cached_document_date_not_old_guidance_date_for_new_trial(setup):
    store,calls=setup;collect(store,records=[record(date='2025-01-01')])
    second=collect(store,records=[record('NCT07654321','STUDY-2','otherdrug',date=None)])
    assert second['cache_hits']==1 and second['new_candidates']==1
    new=next(c for c in intake.list_candidates(store) if c['nct_id']=='NCT07654321')
    assert new['structured']['published_at'] is None
    assert new['structured']['publication_date_basis']=='unknown'


def test_real_pdf_roundtrip_then_verified_cache_hit(tmp_path):
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
    writer=PdfWriter();page=writer.add_blank_page(width=400,height=400)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):font})})
    stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 350 Td (NCT01234567 STUDY-1 testdrug official guidance.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    buffer=BytesIO();writer.write(buffer);raw=buffer.getvalue()
    store=Store(tmp_path/'real-pdf')
    first=collect(store,raw);second=collect(store,raw)
    assert first['parser_runs']==1 and first['new_candidates']==1 and first['failures']==[]
    assert second['cache_hits']==1 and second['parser_runs']==0 and second['failures']==[]
    candidate=intake.list_candidates(store)[0]
    assert 'NCT01234567' in candidate['structured']['text']
    archived,_=intake.get_source_bytes(store,candidate['id'])
    assert archived==raw


def test_real_archived_daiichi_watch_pdf_extracts_without_inferred_outcomes():
    """Exercise the actual PDF subprocess on the exact publisher bytes, offline.

    This deliberately does not mock subprocess.run or extraction; synthetic
    one-page PDFs cannot expose real publisher-font/stream memory regressions.
    """
    import json
    import hashlib
    manifest=json.loads((official.ROOT/'dashboard_data/official_watch_sources.json').read_text())
    records=[record for record in manifest['records'] if record['nct_id']=='NCT05687266']
    assert len(records)==1
    sources=[source for source in records[0]['sources']
             if source['url']=='https://www.daiichisankyo.com/files/investors/library/quarterly_result/2026/FY2026Q1_Financial_Results_Presentation_E.pdf']
    assert len(sources)==1
    source=sources[0];raw=(official.ROOT/source['path']).read_bytes()
    assert raw.startswith(b'%PDF-')
    assert hashlib.sha256(raw).hexdigest()==source['sha256']=='b699c882915208010063106d310164da48b422ccaf02c87d5964ad860781afd4'
    document=official.extract(raw)
    normalized=official.normalize(document['text'])
    assert 'AVANZAR' in normalized
    assert 'CY2026 H2' in normalized
    assert document['content_type']=='application/pdf'
    # Cover/index guidance dates are separate provenance. The parser must not
    # promote them into document metadata or an observed clinical readout.
    assert document['published_at'] is None
    assert document.get('label') is None
    assert document.get('readout_date') is None
    assert document.get('date_kind') is None
