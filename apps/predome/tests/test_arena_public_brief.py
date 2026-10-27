"""Clinical Brief uses the actual public producer and isolated native/import ledgers."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from emeris_live import arena_public as public, arena_public_brief as brief
from emeris_live import arena_public_contract as public_contract
from emeris_live.arena_store import ArenaLedger, result_target_binding
from arena_scientific_fixture import scientifically_accept
from test_arena_core import finish, spec
from test_arena_import_public import integrated
from test_arena_imports import bundle_factory, signed


class Document(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags, self.text, self.links = [], [], []
        self.model_articles, self._article = {}, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.tags.append((tag, attrs))
        if tag == 'article' and 'model-evidence' in attrs.get('class', '').split():
            self._article = {'text': [], 'links': [], 'tags': []}
            self.model_articles[attrs.get('data-run-id')] = self._article
        if self._article is not None:
            self._article['tags'].append((tag, attrs))
        if tag == 'a':
            self.links.append(attrs.get('href'))
            if self._article is not None:
                self._article['links'].append(attrs.get('href'))

    def handle_endtag(self, tag):
        if tag == 'article':
            self._article = None

    def handle_data(self, data):
        self.text.append(data)
        if self._article is not None:
            self._article['text'].append(data)


@pytest.fixture
def native(tmp_path, monkeypatch):
    root = tmp_path / 'native-runtime'
    ledger = ArenaLedger(root)
    target, slots = spec()
    target.update(scope='prospective_challenge', trial_name='Synthetic clinical question',
                  drug='Synthetic study drug', readout={'label': 'Official readout time unconfirmed'})
    ids = ledger.register('brief-native-formal', [target], slots,
                          {'season_kind': 'registered_12_project_season'})
    finish(ledger, ids[0], .7, returned_model='A-fixed')
    ledger.start(ids[1])
    ledger.finish(ids[1], {'status': 'failed', 'probability': None, 'error_code': 'invalid_json'})
    monkeypatch.setattr(public, 'read_archived', lambda store: ([], [], {}))
    app = FastAPI()
    app.state.store = SimpleNamespace(path=root)
    app.include_router(public.router)
    return SimpleNamespace(ledger=ledger, target=target, ids=ids, client=TestClient(app))


@pytest.fixture
def native_references(native):
    """Synthetic source associations, through the real ledger and public API."""
    target, slots = spec()
    target.update(id='model-reference-target', scope='prospective_challenge',
                  trial_name='Synthetic model-reference question')
    evidence = []
    for ident, title, date, page in [('e1', 'Synthetic Alpha evidence', '2020-01-02', 3),
                                     ('e2', 'Synthetic Beta evidence', '2020-02-03', 7),
                                     ('unused', 'Synthetic uncited source', '2020-03-04', 11)]:
        evidence.append({'id': ident, 'title': title, 'text': 'Synthetic isolated source '+ident,
            'source_url': 'https://example.org/synthetic-'+ident,
            'source_sha256': hashlib.sha256(ident.encode()).hexdigest(),
            'available_at': date+'T00:00:00+00:00', 'availability_date': date,
            'kind': 'clinical_prior', 'page': page,
            'page_references': [page, page + 1], 'source_anchor': 'Synthetic section '+ident,
            'document_version': 'Synthetic v2, 2020-01-01'})
    third = deepcopy(slots[1])
    third.update(model_id='C', requested_model='C-fixed')
    slots.append(third)
    for slot in slots:
        slot['target_id'] = target['id']
        slot['packet']['evidence'] = deepcopy(evidence)
    ids = native.ledger.register('model-reference-cohort', [target], slots,
                                {'season_kind': 'registered_12_project_season'})
    finish(native.ledger, ids[0], .7, evidence_ids=['e1'], returned_model='A-fixed',
           reasoning='Synthetic Alpha model explanation.')
    finish(native.ledger, ids[1], .2, evidence_ids=['e2'], returned_model='B-fixed',
           reasoning='Synthetic Beta model explanation.')
    native.ledger.start(ids[2])
    native.ledger.finish(ids[2], {'status': 'failed', 'probability': None, 'error_code': 'invalid_json'})
    return SimpleNamespace(ledger=native.ledger, target=target, ids=ids, client=native.client)


def model_markdown(markdown, model):
    section = markdown.split('\n#### '+model+'\n', 1)[1]
    return section.split('\n#### ', 1)[0].split('\n## Consensus and actual result', 1)[0]


def unpack(response):
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/zip'
    assert 'attachment;' in response.headers['content-disposition']
    assert response.headers['cache-control'] == 'no-store'
    with zipfile.ZipFile(BytesIO(response.content)) as bundle:
        assert set(bundle.namelist()) == {'clinical-brief.html', 'clinical-brief.md', 'clinical-record.json', 'manifest.json'}
        files = {name: bundle.read(name) for name in bundle.namelist()}
    manifest = json.loads(files['manifest.json'])
    assert set(manifest['files']) == set(files) - {'manifest.json'}
    for name, value in manifest['files'].items():
        assert value == {'sha256': hashlib.sha256(files[name]).hexdigest(), 'bytes': len(files[name])}
    assert manifest['schema_version'] == brief.SCHEMA_VERSION
    assert manifest['provider_calls'] == manifest['ledger_writes'] == 0
    assert manifest['independent_timestamp_attestation'] is False
    assert manifest['clinical_prediction_validated'] is False
    return files, manifest


@pytest.mark.parametrize('outputs,denominator,agreement,counts,majority',[
    (['failed']*4,0,None,{'Success':0,'Failure':0,'Uncertain':4},'Uncertain'),
    ([.7,'failed','failed','pending'],1,None,{'Success':1,'Failure':0,'Uncertain':3},'Uncertain'),
    ([.7,'abstain','failed','pending'],2,.5,{'Success':1,'Failure':0,'Uncertain':3},'Uncertain'),
    (['abstain','abstained','failed','unavailable'],2,1,{'Success':0,'Failure':0,'Uncertain':4},'Uncertain'),
    ([.7,.8,'failed','pending'],2,1,{'Success':2,'Failure':0,'Uncertain':2},'Uncertain'),
    (['pending']*4,0,None,{'Success':0,'Failure':0,'Uncertain':4},'Uncertain'),
    ([.7,.8,.2,.3],4,.5,{'Success':2,'Failure':2,'Uncertain':0},'Uncertain'),
    ([.7,.8,.9,.3],4,.75,{'Success':3,'Failure':1,'Uncertain':0},'Success'),
])
def test_actual_ledger_agreement_contract_matches_api_json_brief_and_campaign(
        tmp_path,monkeypatch,outputs,denominator,agreement,counts,majority):
    from test_public_campaign_export import export
    from test_public_snapshot_proof import proof
    target,slots=spec()
    for model in ('C','D'):
        slot=deepcopy(slots[0]);slot.update(model_id=model,requested_model=model+'-fixed');slots.append(slot)
    root=tmp_path/'agreement-runtime';ledger=ArenaLedger(root)
    ids=ledger.register('agreement-formal',[target],slots,{'season_kind':'registered_12_project_season'})
    for ident,output in zip(ids,outputs):
        if output=='pending':continue
        ledger.start(ident)
        result={'status':'predicted' if isinstance(output,float) else output,
            'probability':output if isinstance(output,float) else None,
            'evidence_ids':['e1'] if isinstance(output,float) else []}
        ledger.finish(ident,result)
    before=ledger.events()
    monkeypatch.setattr(public,'read_archived',lambda store:([],[],{}))
    app=FastAPI();app.state.store=SimpleNamespace(path=root);app.include_router(public.router)
    client=TestClient(app);base='/api/arena/public/targets/'+target['id']
    record=client.get(base+'/record').json()
    assert record['version']=='arena-clinical-public-1.4'
    consensus=record['consensus']
    assert consensus['assigned']==4 and consensus['counts']==counts and consensus['predicted_outcome']==majority
    assert consensus['agreement_denominator']==denominator and consensus['agreement']==agreement
    files,_=unpack(client.get(base+'/brief/download'))
    assert json.loads(files['clinical-record.json'])==record
    expected='N/A (fewer than two returned judgments)' if agreement is None else str(int(agreement*100))+'%'
    for name in ('clinical-brief.md','clinical-brief.html'):
        text=files[name].decode()
        assert 'Model agreement: '+expected in text
        assert f'returned judgments: {denominator}/4' in text
    index=client.get('/api/arena/public').json()
    raw,manifest=export.bundle(index,lambda path:client.get(path).json())
    assert proof.validate_bundle(raw)==manifest
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        assert json.loads(archive.read('records/'+target['id']+'.json'))==record
        assert ('Model agreement: '+expected) in archive.read('reports/'+target['id']+'.md').decode()
    assert ledger.events()==before


@pytest.mark.parametrize('key,value',[
    ('agreement',1),('agreement',0),('agreement',False),('agreement_denominator',0),
    ('agreement_denominator',2),('agreement_denominator',True),('assigned',True),
    ('counts',{'Success':1,'Failure':0,'Uncertain':0}),
    ('counts',{'Success':True,'Failure':0,'Uncertain':1}),
    ('predicted_outcome','Success'),
])
def test_brief_and_campaign_reject_fabricated_agreement_denominators_and_majority(native,key,value):
    from test_public_campaign_export import export
    record=native.client.get('/api/arena/public/targets/'+native.target['id']+'/record').json()
    assert record['consensus']['agreement'] is None and record['consensus']['agreement_denominator']==1
    for consensus in (record['consensus'],record['target']['consensus']):consensus[key]=deepcopy(value)
    with pytest.raises(ValueError,match='consensus'):brief.build_brief(record)
    with pytest.raises(ValueError,match='consensus'):export.validate_record(record,record['target'])


@pytest.mark.parametrize('version',[brief.PREVIOUS_RECORD_VERSION,brief.CALENDAR_RECORD_VERSION])
def test_old_brief_record_can_be_verified_but_not_exported_with_new_semantics(native,version):
    record=native.client.get('/api/arena/public/targets/'+native.target['id']+'/record').json()
    record['version']=version
    record['consensus']=public_contract.public_consensus(record['runs'],legacy=version==brief.CALENDAR_RECORD_VERSION)
    record['target']['consensus']=deepcopy(record['consensus'])
    assert brief.validate_record(record,allow_legacy=True)==brief.canonical(record)
    with pytest.raises(ValueError,match='Current sanitized'):brief.build_brief(record)


def test_native_api_brief_uses_one_public_snapshot_preserves_json_times_and_failure(native, monkeypatch):
    calls = []
    original = public.overview
    def observed(request):
        calls.append(True)
        return original(request)
    monkeypatch.setattr(public, 'overview', observed)
    before = native.ledger.events()
    base = '/api/arena/public/targets/' + native.target['id']
    files, manifest = unpack(native.client.get(base + '/brief/download'))
    assert calls == [True]
    record = native.client.get(base + '/record').json()
    assert files['clinical-record.json'] == brief.canonical(record)
    assert manifest['record_kind'] == 'formal_season'
    assert manifest['comparison_scope'] == 'same_evidence_clinical_only'
    assert manifest['assigned_records'] == 2
    assert {r['status'] for r in record['runs']} == {'predicted', 'failed'}
    assert all(r['probability'] is None and r['confidence'] is None for r in record['runs'])
    original_times = {r['id']: r['locked_at_utc'] for r in record['runs']}
    assert {r['id']: r['locked_at_utc'] for r in manifest['original_model_times']} == original_times
    assert all(time < manifest['exported_at_utc'] for time in original_times.values())
    assert manifest['export_time_scope'] == 'public_brief_export_only'
    report = files['clinical-brief.md'].decode()
    assert 'Formal season clinical question' in report and 'Awaiting results' in report
    assert 'failed' in report and 'Not yet evaluable' in report and 'Uncertain' in report
    assert 'Direction counts: Success 1 · Failure 0 · Uncertain 1' in report
    assert 'does not independently prove those earlier times' in report
    doc = Document(files['clinical-brief.html'].decode())
    assert sum(tag == 'section' for tag, _ in doc.tags) == 3
    assert not any(tag in ('script', 'img', 'iframe', 'form', 'link', 'video', 'audio') for tag, _ in doc.tags)
    assert not any(key.startswith('on') for _, attrs in doc.tags for key in attrs)
    assert all(url.startswith('https://') for url in doc.links)
    assert any(tag == 'meta' and attrs.get('http-equiv') == 'Content-Security-Policy' for tag, attrs in doc.tags)
    assert native.ledger.events() == before


def test_native_api_groups_actual_citations_by_model_without_borrowing_global_sources(native_references):
    sample = native_references
    before = sample.ledger.events()
    base = '/api/arena/public/targets/' + sample.target['id']
    record = sample.client.get(base + '/record').json()
    runs = {run['id']: run for run in record['runs']}
    assert [source['id'] for source in runs[sample.ids[0]]['sources']] == ['e1']
    assert [source['id'] for source in runs[sample.ids[1]]['sources']] == ['e2']
    assert runs[sample.ids[2]]['sources'] == []
    assert {source['id'] for source in record['sources']} == {'e1', 'e2', 'unused'}
    files, manifest = unpack(sample.client.get(base + '/brief/download'))
    assert files['clinical-record.json'] == brief.canonical(record)
    assert manifest['assigned_records'] == 3
    document = Document(files['clinical-brief.html'].decode())
    assert sum(tag == 'section' for tag, _ in document.tags) == 3
    assert set(document.model_articles) == set(sample.ids)
    markdown = files['clinical-brief.md'].decode()
    for ident, model, own_title, other_title, explanation, page, date, source_id in [
            (sample.ids[0], 'A-fixed', 'Synthetic Alpha evidence', 'Synthetic Beta evidence',
             'Synthetic Alpha model explanation.', 3, '2020-01-02', 'e1'),
            (sample.ids[1], 'B-fixed', 'Synthetic Beta evidence', 'Synthetic Alpha evidence',
             'Synthetic Beta model explanation.', 7, '2020-02-03', 'e2')]:
        article = document.model_articles[ident]
        text = ' '.join(article['text'])
        section = model_markdown(markdown, model)
        assert explanation in text and explanation in section
        assert own_title in text and own_title in section
        assert other_title not in text and other_title not in section
        assert 'Synthetic uncited source' not in text and 'Synthetic uncited source' not in section
        assert 'Published '+date in text and 'Published '+date in section
        assert 'Page '+str(page) in text and 'Page '+str(page) in section
        assert 'Recorded pages '+str(page)+', '+str(page+1) in text
        assert 'Recorded pages '+str(page)+', '+str(page+1) in section
        assert 'Source anchor Synthetic section '+source_id in text
        assert 'Document version Synthetic v2, 2020-01-01' in text
        source = runs[ident]['sources'][0]
        assert source['page_references'] == [page, page+1]
        assert source['source_anchor'] == 'Synthetic section '+source_id
        assert source['document_version'] == 'Synthetic v2, 2020-01-01'
        assert source['source_url'] == 'https://example.org/synthetic-'+source_id
        assert article['links'] == ['https://example.org/synthetic-'+source_id]
    failure = document.model_articles[sample.ids[2]]
    failure_text = ' '.join(failure['text'])
    failure_markdown = model_markdown(markdown, 'C-fixed')
    assert failure['links'] == []
    assert runs[sample.ids[2]]['brief_reason'] in failure_text
    assert runs[sample.ids[2]]['brief_reason'] in failure_markdown
    assert 'No references recorded for this model.' in failure_text
    assert 'No references recorded for this model.' in failure_markdown
    assert 'Synthetic Alpha evidence' not in failure_text + failure_markdown
    assert 'Synthetic Beta evidence' not in failure_text + failure_markdown
    assert 'not an independent claim-by-claim support review' in markdown
    assert 'Locations are frozen evidence-card metadata' in markdown
    assert 'do not establish that a model read an original document' in markdown
    assert sample.ledger.events() == before


def test_model_rationale_and_reference_titles_escape_both_contexts_and_keep_safe_metadata(native_references):
    sample = native_references
    events_before = sample.ledger.events()
    record = sample.client.get('/api/arena/public/targets/' + sample.target['id'] + '/record').json()
    run = next(run for run in record['runs'] if run['id'] == sample.ids[0])
    synthetic_text = '<img src=x onerror=alert(1)> [bad](javascript:bad) | **marked**'
    run['brief_reason'] = run['reason'] = synthetic_text
    original_url = 'https://example.org/synthetic-e1?range=(1)&label=x%20y'
    snapshot = '/api/arena/sources/' + 'a' * 64
    for sources in (record['sources'], record['evidence'], record['target']['sources'], run['sources']):
        for source in sources:
            if source['id'] == 'e1':
                source.update(title=synthetic_text, source_url=original_url,
                              source_snapshot_url=snapshot, source_anchor=synthetic_text,
                              document_version=synthetic_text)
    input_before = deepcopy(record)
    raw, _ = brief.build_brief(record)
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        html = bundle.read('clinical-brief.html').decode()
        markdown = bundle.read('clinical-brief.md').decode()
        assert bundle.read('clinical-record.json') == brief.canonical(record)
    document = Document(html)
    article = document.model_articles[run['id']]
    assert article['links'] == [original_url, 'https://8.148.145.28' + snapshot]
    assert not any(tag in ('img', 'script', 'iframe') for tag, _ in document.tags)
    assert not any(key.startswith('on') for _, attrs in document.tags for key in attrs)
    assert synthetic_text in ' '.join(article['text'])
    section = model_markdown(markdown, 'A-fixed')
    assert '&lt;img' in html and '&lt;img' in section
    assert '\\[bad\\]' in section and '\\(javascript:bad\\)' in section
    assert '\\| \\*\\*marked\\*\\*' in section
    assert '[Original source](https://example.org/synthetic-e1?range=%281%29&label=x%20y)' in section
    assert '[Captured snapshot](https://8.148.145.28' + snapshot + ')' in section
    assert 'Published 2020-01-02' in section and 'Page 3' in section
    assert 'Recorded pages 3, 4' in section
    assert 'Source anchor &lt;img' in html and 'Document version &lt;img' in html
    assert '#page=' not in html+markdown
    assert record == input_before
    assert sample.ledger.events() == events_before


@pytest.mark.parametrize('field,value',[
    ('page_references',[1,1]),('page_references',[False]),('page_references','3'),
    ('source_anchor','anchor\nnext'),('document_version',''),
    ('source_anchor','sk-'+'S'*32),('document_version','-----BEGIN OPENSSH PRIVATE KEY-----'),
])
def test_brief_rejects_invalid_or_credential_like_locations_at_export_boundary(native_references,field,value):
    record=native_references.client.get('/api/arena/public/targets/'+native_references.target['id']+'/record').json()
    record['runs'][0]['sources'][0][field]=value
    before=deepcopy(record)
    with pytest.raises(ValueError):brief.build_brief(record)
    assert record==before


@pytest.mark.parametrize('version',[brief.PREVIOUS_RECORD_VERSION,brief.CALENDAR_RECORD_VERSION])
def test_new_locations_cannot_be_injected_into_legacy_brief_record(native,version):
    record=native.client.get('/api/arena/public/targets/'+native.target['id']+'/record').json()
    record['version']=version
    record['consensus']=public_contract.public_consensus(record['runs'],legacy=version==brief.CALENDAR_RECORD_VERSION)
    record['target']['consensus']=deepcopy(record['consensus'])
    record['runs'][0]['sources']=[{'id':'synthetic','page_references':[2]}]
    with pytest.raises(ValueError,match='Unexpected fields'):brief.validate_record(record,allow_legacy=True)


def test_predicted_model_with_missing_summary_keeps_its_own_references(native_references):
    sample = native_references
    record = sample.client.get('/api/arena/public/targets/' + sample.target['id'] + '/record').json()
    run = next(run for run in record['runs'] if run['id'] == sample.ids[0])
    run['brief_reason'] = run['reason'] = None
    before = deepcopy(record)
    raw, _ = brief.build_brief(record)
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        article = Document(bundle.read('clinical-brief.html').decode()).model_articles[run['id']]
        section = model_markdown(bundle.read('clinical-brief.md').decode(), 'A-fixed')
    assert 'No short rationale is available for this record.' in ' '.join(article['text'])
    assert 'No short rationale is available for this record.' in section
    assert article['links'] == ['https://example.org/synthetic-e1']
    assert 'Synthetic Beta model explanation.' not in section
    assert record == before


@pytest.mark.parametrize('field,url', [
    ('source_url', 'javascript:alert(1)'),
    ('source_url', 'http://example.org/insecure'),
    ('source_url', 'https://user:password@example.org/private'),
    ('source_url', 'https://example.org/source?api_key=private-value'),
    ('source_url', 'https://example.org/source\nnext'),
    ('source_snapshot_url', '/api/arena/sources/not-a-sha256'),
    ('source_snapshot_url', 'file:///private/isolated-source'),
    ('source_snapshot_url', 'https://example.org/source?authorization=private-value'),
])
def test_model_reference_links_obey_the_existing_export_boundary(native_references, field, url):
    sample = native_references
    record = sample.client.get('/api/arena/public/targets/' + sample.target['id'] + '/record').json()
    run = next(run for run in record['runs'] if run['id'] == sample.ids[0])
    run['sources'][0][field] = url
    before = deepcopy(record)
    with pytest.raises(ValueError):
        brief.build_brief(record)
    assert record == before


@pytest.mark.parametrize('credential', ['sk-' + 'X' * 32, '-----BEGIN OPENSSH PRIVATE KEY-----'])
def test_all_public_api_exports_withhold_unsafe_summary_and_preserve_real_temporary_ledger(native, credential):
    target, slots = spec()
    target.update(id='summary-contract-target', scope='prospective_challenge', trial_name='Synthetic summary boundary')
    for slot in slots:
        slot['target_id'] = target['id']
    ids = native.ledger.register('summary-contract-cohort', [target], slots,
                                {'season_kind': 'registered_12_project_season'})
    original_summary = 'Synthetic audit only. ' + credential
    finish(native.ledger, ids[0], .7, reasoning=original_summary, returned_model='A-fixed')
    native.ledger.start(ids[1])
    native.ledger.finish(ids[1], {'status': 'failed', 'probability': None, 'error_code': 'invalid_json'})
    before = native.ledger.events()
    original_run = next(run for run in native.ledger.snapshot()['runs'] if run['id'] == ids[0])
    base = '/api/arena/public/targets/' + target['id']
    overview = native.client.get('/api/arena/public')
    detail = native.client.get(base)
    downloaded_record = native.client.get(base + '/record')
    files, manifest = unpack(native.client.get(base + '/brief/download'))
    assert overview.status_code == detail.status_code == downloaded_record.status_code == 200
    record = json.loads(files['clinical-record.json'])
    assert record == detail.json() == downloaded_record.json()
    for view in (overview.json(), detail.json(), downloaded_record.json(), record):
        run = next(run for run in view['runs'] if run['id'] == ids[0])
        assert run['brief_reason'] is None and run['reason'] is None
        assert run['status'] == 'predicted' and run['predicted_outcome'] == 'Success'
        assert run['generated_at_utc'] == original_run['generated_at_utc']
        assert run['locked_at_utc'] == original_run['locked_at_utc']
        assert run['model_version'] == 'A-fixed'
        assert run['probability'] is None and run['confidence'] is None
        assert credential not in json.dumps(view)
    assert record['consensus']['counts'] == {'Success': 1, 'Failure': 0, 'Uncertain': 1}
    assert next(run for run in record['runs'] if run['id'] == ids[1])['status'] == 'failed'
    assert manifest['assigned_records'] == 2
    assert all(credential not in raw.decode() for raw in files.values())
    assert original_run['reasoning'] == original_summary
    assert native.ledger.events() == before


def test_actual_brief_build_has_no_server_provider_or_state_dependencies(native):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    program = r'''
import importlib.abc
from io import BytesIO
import json
import os
import sys
import zipfile

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])
class PureBriefImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        allowed = {'emeris_live.arena_public_brief', 'emeris_live.arena_public_contract',
                   'emeris_live.arena_evidence_locations'}
        if (fullname.startswith('emeris_live.') and fullname not in allowed
                or fullname.split('.')[0] in {'fastapi', 'starlette', 'sqlite3', '_sqlite3',
                    'httpx', 'requests', 'socket', '_socket', 'subprocess'}):
            raise RuntimeError('Offline brief imported a server, database or transport dependency')
sys.meta_path.insert(0, PureBriefImports())
def no_side_effect(event, args):
    if (event in {'sqlite3.connect', 'socket.__new__', 'socket.connect', 'subprocess.Popen'}
            or event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)):
        raise RuntimeError('Offline brief attempted a write, database or transport operation')
sys.addaudithook(no_side_effect)
from emeris_live.arena_public_brief import build_brief
record = json.loads(sys.stdin.read())
before = json.dumps(record, sort_keys=True)
raw, manifest = build_brief(record, cohort_kind='registered_12_project_season')
with zipfile.ZipFile(BytesIO(raw)) as bundle:
    files = sorted(bundle.namelist())
    unchanged = json.loads(bundle.read('clinical-record.json')) == record
print(json.dumps({'files': files, 'record_unchanged': unchanged,
    'input_unchanged': json.dumps(record, sort_keys=True) == before,
    'target_id': manifest['target_id'], 'provider_calls': manifest['provider_calls'],
    'ledger_writes': manifest['ledger_writes']}))
'''
    completed = subprocess.run([sys.executable, '-I', '-c', program, str(Path(__file__).resolve().parents[1])],
                               input=json.dumps(record), text=True, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        'files': ['clinical-brief.html', 'clinical-brief.md', 'clinical-record.json', 'manifest.json'],
        'record_unchanged': True, 'input_unchanged': True, 'target_id': native.target['id'],
        'provider_calls': 0, 'ledger_writes': 0}


def test_imported_pilot_brief_is_public_two_controls_and_not_the_four_slot_internal_record(integrated):
    sample, client = integrated.sample, integrated.client
    base = '/api/arena/public/targets/' + sample['target_id']
    files, manifest = unpack(client.get(base + '/brief/download'))
    record = json.loads(files['clinical-record.json'])
    assert record == client.get(base + '/record').json()
    assert manifest['record_kind'] == 'independent_research_pilot'
    assert manifest['assigned_records'] == len(record['runs']) == 2
    assert all(r['track'] == 'same_evidence' and r['condition'] == 'clinical_only' for r in record['runs'])
    report = files['clinical-brief.md'].decode()
    assert 'Independent research pilot' in report and 'Separate from the formal season' in report
    assert 'Randomized · Parallel group · Double masked' in report
    assert 'Current formal clinical question' not in report
    for raw in files.values():
        text = raw.decode()
        for private in ('source_events', 'record_origin', 'provider_config_snapshot', 'source_path',
                        'openai_compatible', 'expected_prompt_sha256', 'token_usage', 'sync_arena_records'):
            assert private not in text
    assert integrated.ledger.events() == integrated.before


@pytest.mark.parametrize('label,accepted', [('success', False), ('success', True), ('mixed', True), ('inconclusive', True)])
def test_real_outcome_review_states_preserve_nonbinary_and_unaccepted_comparisons(native, label, accepted):
    result = {'target_binding': result_target_binding(native.target), 'label': label,
        'source_url': 'https://example.org/isolated-result', 'source_sha256': 'b' * 64,
        'first_public_at': datetime.now(timezone.utc).isoformat(), 'endpoint_match_note': 'Synthetic exact primary target result'}
    for reviewer in ('synthetic-ai-one', 'synthetic-ai-two'):
        native.ledger.record_result(native.target['id'], result, reviewer_id=reviewer, reviewer_kind='ai',
                                    review_note='Synthetic source inspection, not a real human review.')
    if accepted:
        scientifically_accept(native.ledger, native.target)
    before = native.ledger.events()
    files, _ = unpack(native.client.get('/api/arena/public/targets/' + native.target['id'] + '/brief/download'))
    record = json.loads(files['clinical-record.json'])
    report = files['clinical-brief.md'].decode()
    if not accepted:
        assert 'Reported result awaiting independent scientific review' in report
        assert record['result']['label'] is None and all(r['match'] is None for r in record['runs'])
    else:
        assert 'Actual outcome: ' + label in report
        if label in ('mixed', 'inconclusive'):
            assert all(r['match'] is None for r in record['runs'])
            assert 'do not produce a right-or-wrong score' in report
        else:
            assert next(r for r in record['runs'] if r['status'] == 'predicted')['match'] is True
            assert '| Matched |' in report
    assert native.ledger.events() == before


def test_historical_scope_is_not_relabelled_as_formal_or_prospective(native):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    record['target'].update(record_kind='historical_replay', scope='archived_research')
    for run in record['runs']:
        run.update(scope='archived_research', track='archived', match=None, match_status='historical_replay')
    raw, manifest = brief.build_brief(record, cohort_kind='registered_12_project_season')
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        report = bundle.read('clinical-brief.md').decode()
    assert manifest['record_kind'] == 'historical_replay'
    assert manifest['comparison_scope'] == 'archived_historical_clinical_only'
    assert 'Historical replay / archived research record' in report
    assert 'Historical record; no prospective score' in report
    assert 'Formal season clinical question' not in report


def test_actual_registration_withdrawn_before_calls_is_not_a_current_formal_brief(native):
    target, slots = spec()
    target.update(id='withdrawn-brief-target', scope='prospective_challenge', trial_name='Withdrawn synthetic question')
    for slot in slots:
        slot['target_id'] = target['id']
    native.ledger.register('withdrawn-brief-cohort', [target], slots,
                           {'season_kind': 'registered_12_project_season'})
    native.ledger.withdraw_unstarted_cohort('withdrawn-brief-cohort', 'Synthetic source correction before any model execution')
    before = native.ledger.events()
    files, manifest = unpack(native.client.get('/api/arena/public/targets/' + target['id'] + '/brief/download'))
    assert manifest['record_kind'] == 'withdrawn_registration'
    record = json.loads(files['clinical-record.json'])
    assert record['target']['execution_status'] == 'withdrawn_before_execution'
    assert all(r['status'] == 'withdrawn' and r['generated_at_utc'] is None for r in record['runs'])
    report = files['clinical-brief.md'].decode()
    assert 'Preserved withdrawn registration' in report
    assert 'no prediction was generated' in report
    assert 'Formal season clinical question' not in report
    assert native.ledger.events() == before


def test_untrusted_clinical_text_is_escaped_in_offline_html_and_markdown(native):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    record['target']['trial_name'] = '<img src=x onerror=alert(1)> [bad](javascript:bad)'
    raw, _ = brief.build_brief(record)
    with zipfile.ZipFile(BytesIO(raw)) as bundle:
        html = bundle.read('clinical-brief.html').decode()
        markdown = bundle.read('clinical-brief.md').decode()
    document = Document(html)
    assert not any(tag in ('script', 'img') for tag, _ in document.tags)
    assert '&lt;img' in html and '&lt;img' in markdown
    assert '\\[bad\\]' in markdown and '\\(javascript:bad\\)' in markdown


@pytest.mark.parametrize('fault', ['internal_field', 'nested_internal', 'probability', 'patent_arm', 'premature_hit', 'unsafe_source'])
def test_brief_export_boundary_rejects_nonpublic_or_incomparable_objects(native, fault):
    record = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/record').json()
    if fault == 'internal_field':
        record['runs'][0]['reasoning'] = 'Internal reasoning must not be exported'
    elif fault == 'nested_internal':
        record['target']['trial_name'] = {'private_internal_field': 'Not a public scalar'}
    elif fault == 'probability':
        record['runs'][0]['probability'] = .78
    elif fault == 'patent_arm':
        record['runs'][0]['condition'] = 'with_patents'
    elif fault == 'premature_hit':
        record['runs'][0].update(match=True, match_status='matched')
    else:
        record['sources'][0]['source_url'] = 'https://example.org/source?api_key=private-value'
    with pytest.raises(ValueError):
        brief.build_brief(record)


def test_missing_target_and_unavailable_records_keep_clear_http_states(native, monkeypatch):
    assert native.client.get('/api/arena/public/targets/missing/brief/download').status_code == 404
    def unavailable(store):
        raise ValueError('Isolated source failure')
    monkeypatch.setattr(public, 'read_frozen', unavailable)
    response = native.client.get('/api/arena/public/targets/' + native.target['id'] + '/brief/download')
    assert response.status_code == 503
    assert 'application/zip' not in response.headers['content-type']


@pytest.mark.parametrize('kind',['quarter','range','unknown'])
def test_synthetic_current_calendar_brief_preserves_frozen_readout_and_original_model_times(native,kind):
    from test_public_campaign_export import synthetic_current_readout
    record=native.client.get('/api/arena/public/targets/'+native.target['id']+'/record').json()
    original=deepcopy(record)
    update=synthetic_current_readout()
    if kind=='range':update=synthetic_current_readout(start='2027-02-15',end='2028-04-01',precision='range')
    elif kind=='unknown':update=synthetic_current_readout(label='SYNTHETIC official time unconfirmed',start=None,end=None,precision='unknown')
    record['target']['current_readout']=update
    before=deepcopy(record)
    raw,manifest=brief.build_brief(record,cohort_kind='registered_12_project_season')
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        exported=json.loads(archive.read('clinical-record.json'))
        assert exported==before and record==before
        assert exported['target']['readout']==original['target']['readout']
        assert exported['runs']==original['runs'] and exported['result']==original['result']
        markdown=archive.read('clinical-brief.md').decode()
        doc=Document(archive.read('clinical-brief.html').decode())
        text=' '.join(doc.text)
        for label in ('Original frozen readout','Original frozen window','Current reviewed readout',
                      'Current reviewed window','Calendar source publication','Calendar update approved UTC'):
            assert label in markdown and label in text
        assert update['label'] in markdown and update['label'] in text
        assert 'this update was not model input' in markdown
        assert update['source_url'] in doc.links
        assert 'Awaiting results' in markdown and 'Awaiting results' in text
        assert (update['start'] in markdown and update['end'] in markdown) if kind!='unknown' else 'Readout unconfirmed' in markdown
    assert manifest['original_model_times']==[{key:r.get(key) for key in ('id','generated_at_utc','locked_at_utc')} for r in original['runs']]


@pytest.mark.parametrize('changes',[{'original_prediction_unchanged':False},{'source_granularity':'drug_program'},
    {'decision_sha256':'bad'}, {'source_url':'https://example.org/?api_key=secret'},
    {'approved_at':'2026-10-22T00:00:00Z'}, {'precision':'unknown'}, {'internal_prompt':'PRIVATE'}])
def test_synthetic_current_calendar_contract_is_validated_at_brief_boundary(native,changes):
    from test_public_campaign_export import synthetic_current_readout
    record=native.client.get('/api/arena/public/targets/'+native.target['id']+'/record').json()
    record['target']['current_readout']=synthetic_current_readout(**changes)
    with pytest.raises(ValueError):brief.build_brief(record)
