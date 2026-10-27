"""Engineering fixtures only; these files are not UC scientific evidence."""
import csv
import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest
from vbt.programs import Programs, Conflict
from vbt.scientific_review import (BASE_FILES, COLUMNS, REPORT, assess, comparison_source, export_report,
                                   import_comparison, view)
from vbt.ai_research import packet
from vbt.workbench import Workbench, handler, page
from test_programs import PROFILE, add


def write_csv(path, fields, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)


def seal(root):
    paths = [p for p in root.iterdir() if p.name != 'ARTIFACT_MANIFEST.json']
    (root/'ARTIFACT_MANIFEST.json').write_text(json.dumps(dict(run_id='uc_independent_20261025',
        files=[dict(path=p.name, sha256=hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths])))


@pytest.fixture
def ready(tmp_path):
    store = Programs(tmp_path/'state'); pid = store.create(PROFILE, 'engineering author'); ref = add(store, pid)
    root = tmp_path/'sealed'; root.mkdir()
    rows = [dict(item='COHORT_OSMR', original='=untrusted source text', independent='engineering value',
                 status='not_reproduced', difference_class='missing_selection_manifest',
                 explanation='Engineering fixture; missing original sample mapping', evidence='sample_flow.csv')]
    write_csv(root/'claim_comparison.csv', COLUMNS, rows)
    context = dict(analysis_id='synthetic-run', cohort='COHORT', gene='OSMR', endpoint_rule='unknown', unit='unknown',
                   scale='synthetic', probes='unknown', n='unknown', n_responder='unknown', n_nonresponder='unknown',
                   compatible_test='unknown', original_choice_status='unknown engineering choice')
    write_csv(root/'published_compatible_reconstruction.csv', list(context), [context])
    (root/'sample_flow.csv').write_text('synthetic,unknown\nfixture,unknown\n')
    (root/REPORT).write_text('Synthetic engineering report; not independent science.')
    (root/'replication_status.json').write_text(json.dumps(dict(claims=rows, scientific_review='pending',
                      reference='synthetic fixture only', author_commit='unknown', full_original_UC_case_replication='partial')))
    seal(root)
    cid = import_comparison(store, pid, root, 'engineering importer', store.get(pid)['revision'])
    return store, pid, ref, root, cid


def assessment(store, pid, **changes):
    data = dict(subject_kind='difference', subject='COHORT_OSMR', basis_hash=view(store,pid)['basis_hash'],
                judgment='unknown', priority='high', references=[],
                reason='Engineering explanation with missing original mappings', scope='Synthetic test scope; no scientific approval',
                missing='Original sample manifest and patient mapping unknown', next_action='Obtain the actual missing source before testing',
                success='Exact original mapping recovered and independently checked', stop='No mapping available; keep the difference unresolved')
    data.update(changes); return data


def test_import_is_idempotent_bounded_and_never_approves(ready):
    store,pid,ref,root,cid=ready
    before=store.get(pid)
    assert import_comparison(store,pid,root,'importer',before['revision'])==cid
    assert store.get(pid)['revision']==before['revision']
    state=view(store,pid)
    assert state['source_integrity']=='verified' and not state['reviews']
    assert state['comparison']['payload']['rows'][0]['analysis_context'][0]['unit']=='unknown'
    assert store.get(pid)['evidence'][0]['review']=='pending'
    assert not store.get(pid)['decisions'] and not store.get(pid)['experiments']


@pytest.mark.parametrize('damage',['hash','missing','traversal','duplicate_item','wrong_run','oversized','status_mismatch'])
def test_import_rejects_untrusted_sources_without_partial_event(ready,damage):
    store,pid,ref,root,cid=ready;before=len(store.events(pid))
    if damage=='hash':(root/'sample_flow.csv').write_text('changed')
    elif damage=='missing':(root/'sample_flow.csv').unlink()
    elif damage=='oversized':(root/'sample_flow.csv').write_bytes(b'x'*(1024*1024+1));seal(root)
    elif damage in ('traversal','duplicate_item'):
        rows=list(csv.DictReader((root/'claim_comparison.csv').open()))
        if damage=='traversal':rows[0]['evidence']='../outside.csv'
        else:rows.append(rows[0])
        write_csv(root/'claim_comparison.csv',COLUMNS,rows);seal(root)
    elif damage=='status_mismatch':
        path=root/'replication_status.json';data=json.loads(path.read_text());data['claims']=[];path.write_text(json.dumps(data));seal(root)
    else:
        path=root/'ARTIFACT_MANIFEST.json';data=json.loads(path.read_text());data['run_id']='wrong';path.write_text(json.dumps(data))
    with pytest.raises((ValueError,OSError)):
        import_comparison(store,pid,root,'engineering importer',store.get(pid)['revision'])
    assert len(store.events(pid))==before


def test_disagreement_history_scope_and_packet_are_preserved(ready):
    store,pid,ref,root,cid=ready;initial=packet(store,pid)
    assess(store,pid,assessment(store,pid),'reviewer A',store.get(pid)['revision'])
    current=view(store,pid);assert not current['reviews'][-1]['stale']
    assert packet(store,pid)['packet_hash']!=initial['packet_hash']
    assert current['reviews'][-1]['author']=='reviewer A'
    assess(store,pid,assessment(store,pid,judgment='contested'),'reviewer B',store.get(pid)['revision'])
    assert len(view(store,pid)['reviews'])==2
    assert all(not r['stale'] for r in view(store,pid)['reviews'])
    store.revise(pid,dict(PROFILE,hypothesis='Revised engineering hypothesis only',revision_reason='New scope requires renewed scientific review'), 'editor',store.get(pid)['revision'])
    assert all(r['stale'] for r in view(store,pid)['reviews'])
    assert store.get(pid)['evidence'][0]['review']=='pending'
    assert not store.get(pid)['decisions']


@pytest.mark.parametrize('change', ['wrong_subject','fake_ref','empty_supported','missing_reason','stale_basis','stale_revision'])
def test_assess_rejects_invalid_or_stale_declarations(ready,change):
    store,pid,ref,root,cid=ready;data=assessment(store,pid);revision=store.get(pid)['revision']
    if change=='wrong_subject':data['subject']='other_project_item'
    elif change=='fake_ref':data['references']=['fabricated']
    elif change=='empty_supported':data.update(subject_kind='risk',subject='safety',judgment='supported')
    elif change=='missing_reason':data['reason']=''
    elif change=='stale_basis':data['basis_hash']='old'
    else:add(store,pid)
    with pytest.raises(ValueError):assess(store,pid,data,'reviewer',revision)
    assert not view(store,pid)['reviews']


def test_source_loss_blocks_new_review_and_marks_history_stale(ready):
    store,pid,ref,root,cid=ready
    assess(store,pid,assessment(store,pid),'reviewer',store.get(pid)['revision'])
    (root/'sample_flow.csv').unlink()
    assert view(store,pid)['source_integrity']=='unavailable_or_changed'
    assert view(store,pid)['reviews'][-1]['stale']
    with pytest.raises(ValueError,match='来源缺失或变化'):
        assess(store,pid,assessment(store,pid),'reviewer',store.get(pid)['revision'])
    assert 'needs_review' in export_report(store,pid,'csv').decode('utf-8-sig')


def test_risk_references_bind_current_comparison_and_do_not_approve(ready):
    store,pid,ref,root,cid=ready
    assess(store,pid,assessment(store,pid,subject_kind='risk',subject='biomarker',judgment='contested',
        references=[ref,'difference:COHORT_OSMR']),'reviewer',store.get(pid)['revision'])
    r=view(store,pid)['reviews'][-1]
    assert r['comparison_id']==cid and r['judgment']=='contested' and not r['stale']
    assert not store.get(pid)['decisions'] and store.get(pid)['evidence'][0]['review']=='pending'
    out=json.loads(store.export(pid)); assert out['scientific_review']=='not_inferred'


def test_new_import_preserves_old_reviews_and_invalidates_scope(ready):
    store,pid,ref,root,cid=ready
    assess(store,pid,assessment(store,pid),'reviewer',store.get(pid)['revision'])
    (root/'sample_flow.csv').write_text('new,engineering-only\nfixture,unknown\n');seal(root)
    new_id=import_comparison(store,pid,root,'importer',store.get(pid)['revision'])
    assert new_id!=cid and view(store,pid)['reviews'][-1]['stale']
    assert len([e for e in store.events(pid) if e['kind']=='comparison_import'])==2
    assert 'pending' in export_report(store,pid,'md').decode()


def test_source_route_refuses_arbitrary_paths_and_changed_files(ready):
    store,pid,ref,root,cid=ready
    assert comparison_source(store,pid,'sample_flow.csv')==(root/'sample_flow.csv').read_bytes()
    for name in ['../secret','ARTIFACT_MANIFEST.json','file:///etc/passwd','unbound.csv']:
        with pytest.raises(ValueError):comparison_source(store,pid,name)
    (root/'sample_flow.csv').write_text('changed')
    with pytest.raises(ValueError):comparison_source(store,pid,'sample_flow.csv')


def test_bound_artifact_loss_invalidates_risk_even_without_comparison_change(ready,tmp_path):
    store,pid,ref,root,cid=ready
    bound=tmp_path/'optimization';bound.mkdir();source=bound/'interaction_results.csv';source.write_bytes(b'synthetic bytes')
    sha=hashlib.sha256(source.read_bytes()).hexdigest()
    store._append(pid,'import',dict(root=str(bound),files={source.name:sha}),'importer',store.get(pid)['revision'])
    store.add_evidence(pid,dict(direction='uncertain',review='pending',claim='Engineering source only',scope='Synthetic source not clinical',
        limitations='No scientific validity',source='artifact:'+source.name,source_date='2026-10-26'),'author',store.get(pid)['revision'])
    ref=store.get(pid)['evidence'][-1]['event_id']
    assess(store,pid,assessment(store,pid,subject_kind='risk',subject='mechanism',references=[ref]),'reviewer',store.get(pid)['revision'])
    assert not view(store,pid)['reviews'][-1]['stale']
    source.unlink()
    assert view(store,pid)['reviews'][-1]['stale']


def test_report_preserves_unknowns_review_history_and_neutralizes_formula(ready):
    store,pid,ref,root,cid=ready
    assess(store,pid,assessment(store,pid),'reviewer',store.get(pid)['revision'])
    rows=list(csv.DictReader(io.StringIO(export_report(store,pid,'csv').decode('utf-8-sig'))))
    assert rows[0]['original'].startswith("'=untrusted")
    assert rows[0]['review_state']=='local_declaration' and 'reviewer' in rows[0]['review_history']
    assert 'unknown' in rows[0]['analysis_context']
    report=export_report(store,pid,'md').decode()
    assert 'partial' in report and 'missing_selection_manifest' in report and 'reviewer' in report
    with pytest.raises(ValueError):export_report(store,pid,'html')


def test_http_forms_csrf_export_and_injection_boundaries(ready,tmp_path):
    store,pid,ref,root,cid=ready;app=Workbench(tmp_path/'workspace',store.state)
    assess(store,pid,assessment(store,pid,reason='<script>alert(1)</script> engineering canary'),'reviewer',store.get(pid)['revision'])
    body=page(app,'/programs',{'id':pid})
    assert '原版对照与风险审阅' in body and '&lt;script&gt;' in body and '<script>alert' not in body
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        response=urllib.request.urlopen(base+'/program/comparison-report?id='+pid+'&format=csv')
        assert response.headers['Content-Type'].startswith('text/csv') and 'sandbox;' in response.headers['Content-Security-Policy']
        assert 'COHORT_OSMR' in response.read().decode('utf-8-sig')
        data=dict(assessment(store,pid,subject_kind='risk',subject='safety'),operation='scientific_assessment',id=pid,
                  revision=store.get(pid)['revision'],author='HTTP engineering reviewer',token='invalid')
        data.pop('references')
        request=lambda:urllib.request.Request(base+'/program/action',data=urlencode(data).encode(),headers={'Origin':base})
        with pytest.raises(urllib.error.HTTPError) as exc:urllib.request.urlopen(request())
        assert exc.value.code==403
        data['token']=app.token
        assert urllib.request.urlopen(request()).status==200
        assert view(store,pid)['reviews'][-1]['subject']=='safety'
    finally:
        server.shutdown();server.server_close()
