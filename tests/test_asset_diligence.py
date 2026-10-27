"""Synthetic engineering fixtures, never real rights or clinical evidence."""
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

from vbt.asset_diligence import add_claim, matrix, review_claim, export_matrix
from vbt.programs import Conflict, Programs
from vbt.workbench import Workbench, handler, page
from test_program_diligence import linked, payload as bd_payload
from test_programs import PROFILE, add


def claim(s, pid, **changes):
    asset = s.get(pid)['assets'][0]
    data = dict(asset_id=asset['event_id'], asset_identity=asset['asset_name'],
                category='sponsor', direction='support', territory='unknown', indication='synthetic UC only',
                rights_type='trial sponsorship only; no ownership inference', coverage_start='2020-01-01',
                coverage_end='2020-01-02', claim='Fixture sponsor was recorded in a dated trial snapshot',
                limitations='Not current asset status, ownership or licensing evidence',
                missing='Exact ownership chain and regional licensing scope remain unknown',
                next_action='Obtain dated rights chain and regional contract scope',
                source_ref='asset:' + asset['event_id'])
    data.update(changes)
    return data


def record(s, pid, **changes):
    return add_claim(s, pid, claim(s, pid, **changes), 'engineering fixture author', s.get(pid)['revision'])


def review(s, pid, ref, **changes):
    data = dict(claim_id=ref, judgment='supported', matrix_hash=matrix(s,pid)['matrix_hash'],
                reason='Read exact synthetic source; no actual scientific review',
                scope='Only fixture trial, territory and dated scope were reviewed',
                counterevidence='Regional ownership and licensing remain unverified',
                next_action='Request rights chain with dates and exact scope')
    data.update(changes)
    return review_claim(s,pid,data,'synthetic reviewer',s.get(pid)['revision'])


def test_no_rows_has_nine_explicit_gaps_and_no_implied_rights(linked):
    s,pid,_,_=linked; state=matrix(s,pid)
    assert len(state['gaps']) == 9 and state['rows']==[]
    assert state['asset_status']=='external_reference_rights_not_established'
    assert s.get(pid)['decisions']==[] and s.get(pid)['diligence']==[]


def test_scope_conflicts_coexist_and_export_without_last_write_wins(linked):
    s,pid,_,_=linked
    first=record(s,pid,territory='Region AAA',category='license',rights_type='exclusive use',claim='License assertion needs its own primary record')
    second=record(s,pid,territory='Region BBB',category='license',direction='counter',claim='Contradictory assertion retained, not silently resolved')
    state=matrix(s,pid)
    assert [r['event_id'] for r in state['rows']]==[first,second]
    assert [r['territory'] for r in state['rows']]==['Region AAA','Region BBB']
    assert all(r['review_state']=='pending' and r['current'] for r in state['rows'])
    data=json.loads(export_matrix(s,pid,'json')); assert len(data['rows'])==2
    assert s.get(pid)['assets'][0]['rights_status']=='not_established'
    assert len(json.loads(s.export(pid))['program']['events'])==5


@pytest.mark.parametrize('changes',[
    {'asset_id':'other'}, {'asset_identity':'wrong molecule'}, {'category':'rights_cleared'},
    {'direction':'approved'}, {'territory':''}, {'rights_type':''}, {'missing':''}, {'next_action':''},
    {'coverage_start':'2020-02-31'}, {'coverage_end':'current'},
    {'coverage_start':'2021-01-01','coverage_end':'2020-01-01'},
    {'coverage_start':'20200101'}, {'source_ref':''}, {'source_ref':'asset:other'},
    {'source_ref':'evidence:other-project'}, {'source_ref':42}
])
def test_reject_bad_identity_scope_dates_and_references_without_event(linked,changes):
    s,pid,_,_=linked; before=s.get(pid)['revision']
    with pytest.raises(ValueError):record(s,pid,**changes)
    assert s.get(pid)['revision']==before


def test_unknown_can_have_no_source_but_cannot_be_supported(linked):
    s,pid,_,_=linked
    ref=record(s,pid,category='ownership',direction='unknown',source_ref='',coverage_start='unknown',coverage_end='unknown')
    assert matrix(s,pid)['rows'][0]['source_integrity']=='no_source'
    with pytest.raises(ValueError,match='缺少来源'): review(s,pid,ref)
    review(s,pid,ref,judgment='unknown')
    assert matrix(s,pid)['rows'][0]['review_state']=='unknown'


def test_url_record_binding_does_not_claim_remote_content_verification(linked):
    s,pid,ref,_=linked
    record(s,pid,source_ref='evidence:'+ref,category='scientific')
    source=matrix(s,pid)['rows'][0]['source']
    assert source['integrity']=='record_bound_remote_not_checked' and source['content_sha256'] is None


def test_scoped_review_and_source_change_invalidate_without_rewriting(linked):
    s,pid,_,source=linked; ref=record(s,pid);review(s,pid,ref)
    before=matrix(s,pid); assert before['rows'][0]['review_state']=='supported'
    assert before['gaps'] # missing rights survives a supported sponsor review
    source.write_bytes(b'changed fixture')
    after=matrix(s,pid); row=after['rows'][0]
    assert not row['current'] and row['review_state']=='pending'
    assert row['reviews'][0]['current'] is False
    assert after['matrix_hash'] != before['matrix_hash']
    with pytest.raises(ValueError):review(s,pid,ref)
    with pytest.raises(ValueError):record(s,pid)
    assert len(s.get(pid)['events'])==5


def test_new_claim_invalidates_old_review_but_new_review_does_not_self_expire(linked):
    s,pid,_,_=linked; a=record(s,pid); review(s,pid,a)
    before=matrix(s,pid);assert before['rows'][0]['reviews'][0]['current']
    b=record(s,pid,direction='counter',claim='Conflicting source interpretation requires review')
    state=matrix(s,pid);assert state['rows'][0]['review_state']=='pending'
    review(s,pid,b,judgment='contested');review(s,pid,a)
    state=matrix(s,pid);assert [r['review_state'] for r in state['rows']]==['supported','contested']
    assert len(state['rows'][0]['reviews'])==2


def test_profile_change_and_missing_volume_are_visible_gaps(linked):
    s,pid,_,source=linked;record(s,pid)
    s.revise(pid,dict(PROFILE,hypothesis='Different indication changes relevance',revision_reason='Changed project scope needs review'),'tester',s.get(pid)['revision'])
    assert not matrix(s,pid)['rows'][0]['current']
    source.unlink()
    assert matrix(s,pid)['rows'][0]['source_integrity']=='unavailable_or_changed'
    assert 'unavailable_or_changed' in export_matrix(s,pid,'md').decode()


def test_stale_revision_rejects_claim_and_review(linked):
    s,pid,_,_=linked; old=s.get(pid)['revision']; ref=record(s,pid)
    with pytest.raises(Conflict):add_claim(s,pid,claim(s,pid),'tester',old)
    state=matrix(s,pid)
    record(s,pid,category='transaction',direction='unknown',source_ref='')
    with pytest.raises(ValueError):review(s,pid,ref,matrix_hash=state['matrix_hash'])


def test_bd_must_face_all_claims_and_binds_review_version_independent_of_rd(linked):
    s,pid,ref,_=linked; a=record(s,pid); b=record(s,pid,direction='counter')
    data=bd_payload(s,pid,ref)
    with pytest.raises(ValueError):s.add_diligence(pid,data,'tester',s.get(pid)['revision'])
    data.update(asset_claim_ids=[a],asset_matrix_hash=matrix(s,pid)['matrix_hash'],acknowledge_gaps='yes')
    with pytest.raises(ValueError):s.add_diligence(pid,data,'tester',s.get(pid)['revision'])
    data['asset_claim_ids']=[a,b];s.add_diligence(pid,data,'tester',s.get(pid)['revision'])
    assert s.get(pid)['decisions']==[]
    saved=s.get(pid)['diligence'][-1];review(s,pid,a)
    assert saved['asset_matrix_hash']!=matrix(s,pid)['matrix_hash']
    assert saved['rights_status']=='not_established'


def test_stale_scope_can_be_explicitly_deferred_not_permanently_deadlocked(linked):
    s,pid,ref,_=linked; a=record(s,pid)
    s.revise(pid,dict(PROFILE,revision_reason='Scope amendment needs new review'),'tester',s.get(pid)['revision'])
    data=bd_payload(s,pid,ref);data.update(asset_claim_ids=[a],asset_matrix_hash=matrix(s,pid)['matrix_hash'],acknowledge_gaps='yes')
    s.add_diligence(pid,data,'tester',s.get(pid)['revision'])
    assert s.get(pid)['diligence'][-1]['stale_claim_ids']==[a]


def test_csv_formula_and_html_escaping_and_history(linked,tmp_path):
    s,pid,_,_=linked
    record(s,pid,claim=' =HYPERLINK("https://example.invalid")',missing='<script>alert(1)</script> | gap')
    csv_rows=list(csv.DictReader(io.StringIO(export_matrix(s,pid,'csv').decode('utf-8-sig'))))
    assert csv_rows[0]['claim'].startswith("'")
    assert '&lt;script&gt;' in export_matrix(s,pid,'md').decode()
    app=Workbench(tmp_path/'work',s.state);body=page(app,'/programs',{'id':pid})
    assert '<script>alert' not in body and '&lt;script&gt;' in body
    assert '权利未核实' in body and '资产资料与缺失矩阵' in body
    with pytest.raises(ValueError):export_matrix(s,pid,'xlsx')


def test_http_claim_review_export_csrf_and_multiref(linked,tmp_path):
    s,pid,ref,_=linked; app=Workbench(tmp_path/'work',s.state)
    server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start()
    base=f'http://127.0.0.1:{server.server_port}'
    def post(data,token=None):
        payload=dict(data,id=pid,revision=s.get(pid)['revision'],author='fixture author',token=token or app.token)
        return urllib.request.urlopen(urllib.request.Request(base+'/program/action',data=urlencode(payload,doseq=True).encode(),headers={'Origin':base}))
    try:
        data=dict(claim(s,pid),operation='asset_claim')
        with pytest.raises(urllib.error.HTTPError) as err:post(data,'bad-token')
        assert err.value.code==403 and not matrix(s,pid)['rows']
        assert post(data).status==200
        a=matrix(s,pid)['rows'][0]['event_id']; b=record(s,pid,direction='counter')
        assert post(dict(operation='asset_claim_review',claim_id=a,matrix_hash=matrix(s,pid)['matrix_hash'],judgment='supported',reason='Read the actual synthetic source record only',scope='Synthetic trial and exact dated snapshot only',counterevidence='No ownership or licensing evidence has been supplied',next_action='Obtain primary regional rights records')).status==200
        assert matrix(s,pid)['rows'][0]['review_state']=='supported'
        data=bd_payload(s,pid,ref);data.update(operation='diligence',asset_claim_ids=[a,b],asset_matrix_hash=matrix(s,pid)['matrix_hash'],acknowledge_gaps='yes')
        assert post(data).status==200
        assert s.get(pid)['diligence'][-1]['asset_claim_ids']==[a,b]
        response=urllib.request.urlopen(base+'/program/asset-matrix?id='+pid+'&format=json')
        assert json.load(response)['rows'][0]['event_id']==a
        asset=s.get(pid)['assets'][0]
        assert urllib.request.urlopen(base+'/program/asset-source?id='+pid+'&asset_id='+asset['event_id']).read()==linked[3].read_bytes()
        for fmt in ('csv','md'):
            assert urllib.request.urlopen(base+'/program/asset-matrix?id='+pid+'&format='+fmt).status==200
    finally:server.shutdown();server.server_close()


def test_ai_packet_tracks_rights_scope_and_stale_citations_without_private_metadata(linked):
    from vbt.ai_research import packet, validate_proposal
    from vbt.ai_research import DOMAINS
    s,pid,ref,source=linked
    before=packet(s,pid); claim_id=record(s,pid)
    after=packet(s,pid);assert after['schema_version']==4 and before['packet_hash']!=after['packet_hash']
    asset_context=after['asset_diligence'];row=asset_context['rows'][0]
    assert row['reference_id']=='assetclaim:'+claim_id and row['citation_eligible']
    assert str(source) not in json.dumps(asset_context) and 'engineering fixture author' not in json.dumps(asset_context)
    proposal=dict(packet_hash=after['packet_hash'],summary='Synthetic engineering proposal, not an actual model output',
                  risks=[dict(domain=k,status='unknown',reason='This synthetic fixture cannot establish rights or efficacy',references=[]) for k in DOMAINS],
                  tasks=[dict(id='rights',tool='manual_research',question='Check exact rights scope',rationale='Ownership evidence is missing',next_action='Obtain regional rights documentation',success_criterion='Exact scope supported by primary records',stop_criterion='Stop when exact molecule identity differs',references=[ref])])
    proposal['tasks'][0]['references']=[row['reference_id']]
    validate_proposal(proposal,after)
    review(s,pid,claim_id)
    assert packet(s,pid)['packet_hash']!=after['packet_hash']
    source.write_bytes(b'changed fixture source')
    changed=packet(s,pid);proposal['packet_hash']=changed['packet_hash']
    assert not changed['asset_diligence']['rows'][0]['citation_eligible']
    with pytest.raises(ValueError,match='未知证据'):validate_proposal(proposal,changed)


def test_uc_seed_uses_existing_source_only_is_idempotent_and_no_fake_review(linked):
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('uc_asset_matrix_import',Path(__file__).parents[1]/'scripts'/'import_uc_asset_matrix.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    seed=module.seed
    s,pid,_,_=linked
    # Explicit synthetic identity used to exercise the import contract, not real source evidence.
    asset=s.get(pid)['assets'][0]
    s._append(pid,'asset_import',dict(asset,asset_name='Vixarelimab'),'synthetic fixture',s.get(pid)['revision'])
    ids=seed(s,pid);revision=s.get(pid)['revision']
    assert len(ids)==9 and seed(s,pid)==ids and s.get(pid)['revision']==revision
    state=matrix(s,pid)
    assert len(state['rows'])==9 and len(state['gaps'])==18 # original fixture asset retains its own nine gaps
    assert sum(r['source'] is not None for r in state['rows'])==2
    assert all(r['review_state']=='pending' for r in state['rows'])
    assert not s.get(pid)['decisions'] and not s.get(pid)['diligence']


def test_each_asset_download_uses_its_own_bound_snapshot_not_latest(linked,tmp_path):
    from vbt.asset_diligence import asset_source
    s,pid,_,source=linked; original=s.get(pid)['assets'][0]
    other=tmp_path/'other.json';other.write_bytes(b'{"different_fixture":true}')
    s._append(pid,'asset_import',dict(original,asset_name='Different fixture',source_path=str(other),source_sha256=hashlib.sha256(other.read_bytes()).hexdigest()),'fixture',s.get(pid)['revision'])
    second=s.get(pid)['assets'][-1]
    assert asset_source(s,pid,original['event_id'])==source.read_bytes()
    assert asset_source(s,pid,second['event_id'])==other.read_bytes()
    with pytest.raises(ValueError):asset_source(s,pid,'other-project')
    source.write_bytes(b'changed')
    with pytest.raises(ValueError):asset_source(s,pid,original['event_id'])
    assert asset_source(s,pid,second['event_id'])==other.read_bytes()


def test_short_valid_scope_and_nonlocal_or_oversized_sources(linked):
    s,pid,_,source=linked
    record(s,pid,territory='中国',indication='UC',rights_type='许可')
    assert matrix(s,pid)['rows'][0]['territory']=='中国'
    source.write_bytes(b'0'*(1024*1024+1))
    with pytest.raises(ValueError,match='超出限制'):record(s,pid)
