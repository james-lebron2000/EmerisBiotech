from copy import deepcopy
from datetime import datetime, timezone, timedelta
import sqlite3
import pytest
from emeris_live.arena_store import ArenaLedger, sha, validate_packet, validate_target
from emeris_live.arena_metrics import build_metrics, patent_pairs
from arena_scientific_fixture import scientifically_accept


def spec():
    time = datetime.now(timezone.utc)
    target = dict(id='target-test',project_id='asset-A',nct_id='NCT00000001',endpoint='PFS',population='specified adults',
        comparison='A vs B',success_definition='Prespecified primary PFS hypothesis officially met',analysis_timepoint='primary analysis',
        information_cutoff_utc=(time-timedelta(hours=1)).isoformat(),submission_deadline_utc=(time+timedelta(days=1)).isoformat(),
        readout_screen={'status':'no_decisive_public_result_found','checked_at_utc':time.isoformat(),
                        'sources':[{'url':'https://example.org/registry','sha256':'a'*64}]})
    packet={'target':{k:target[k] for k in ('endpoint','population','comparison','success_definition')},
            'data_cutoff':target['information_cutoff_utc'],'evidence':[{'id':'e1','text':'Fixture evidence for isolated tests only.',
            'source_url':'https://example.org/evidence','source_sha256':'a'*64,
            'available_at':(time-timedelta(days=1)).isoformat(),'kind':'clinical_prior'}]}
    slots=[{'target_id':target['id'],'model_id':m,'requested_model':m+'-fixed','track':'same_evidence','condition':'clinical_only',
            'workflow_version':'single-v1','packet':deepcopy(packet)} for m in ['A','B']]
    return target,slots


def registered(tmp_path):
    ledger=ArenaLedger(tmp_path); target,slots=spec()
    ids=ledger.register('season-test',[target],slots,{'status':'test fixture'})
    return ledger,target,ids


def finish(ledger,ident,p=.7,**extra):
    ledger.start(ident)
    return ledger.finish(ident,{'status':'predicted','probability':p,'evidence_ids':['e1'],**extra})


def test_registration_before_execution_and_no_replacement(tmp_path):
    ledger,target,ids=registered(tmp_path)
    with pytest.raises(ValueError):ledger.finish(ids[0],{'status':'abstain','probability':None})
    finish(ledger,ids[0])
    with pytest.raises(ValueError):finish(ledger,ids[0])
    with pytest.raises(ValueError):ledger.finish(ids[0],{'status':'abstain','probability':None})
    assert len(ledger.snapshot()['runs'])==2


def test_duplicate_cohort_and_slots_rejected(tmp_path):
    ledger=ArenaLedger(tmp_path);t,s=spec()
    with pytest.raises(ValueError):ledger.register('c',[t],s+[s[0]],{})
    assert ledger.events()==[]
    ledger.register('c',[t],s,{})
    with pytest.raises(ValueError):ledger.register('c',[t],s,{})


def test_ledger_append_only_and_tamper_detection(tmp_path):
    ledger,t,ids=registered(tmp_path)
    with ledger.connect() as db:
        with pytest.raises(sqlite3.IntegrityError):db.execute('DELETE FROM arena_events')
        with pytest.raises(sqlite3.IntegrityError):db.execute("UPDATE arena_events SET payload='{}'")
    with ledger.connect() as db:
        db.execute('DROP TRIGGER arena_no_update')
        db.execute("UPDATE arena_events SET payload='{}' WHERE seq=1")
    with pytest.raises(ValueError,match='integrity'):ledger.events()


@pytest.mark.parametrize('p',[float('nan'),float('inf'),True,-.01,1.01])
def test_invalid_probabilities_rejected(tmp_path,p):
    ledger,t,ids=registered(tmp_path);ledger.start(ids[0])
    with pytest.raises(ValueError):ledger.finish(ids[0],{'status':'predicted','probability':p,'evidence_ids':['e1']})


def test_citations_and_abstention_null(tmp_path):
    ledger,t,ids=registered(tmp_path);ledger.start(ids[0])
    with pytest.raises(ValueError):ledger.finish(ids[0],{'status':'predicted','probability':.7,'evidence_ids':['invented']})
    with pytest.raises(ValueError):ledger.finish(ids[0],{'status':'abstain','probability':0})
    ledger.finish(ids[0],{'status':'abstain','probability':None})
    assert ledger.snapshot()['runs'][0]['probability'] is None


def test_cutoff_and_patent_review_gates():
    t,s=spec();packet=deepcopy(s[0]['packet'])
    packet['evidence'][0]['available_at']=t['submission_deadline_utc']
    with pytest.raises(ValueError,match='later'):validate_packet(packet,t,'clinical_only')
    packet=deepcopy(s[0]['packet']);packet['evidence'][0]['kind']='patent'
    with pytest.raises(ValueError,match='review'):validate_packet(packet,t,'with_patents')
    with pytest.raises(ValueError,match='requires'):validate_packet(s[0]['packet'],t,'with_patents')


def test_controlled_models_cannot_receive_different_evidence(tmp_path):
    t,s=spec();s[1]['packet']['evidence'][0]['text']='Different facts.'
    with pytest.raises(ValueError,match='identical'):ArenaLedger(tmp_path).register('c',[t],s,{})


def test_two_distinct_reviews_and_scoring(tmp_path):
    ledger,t,ids=registered(tmp_path)
    finish(ledger,ids[0],.8,returned_model='A-fixed');finish(ledger,ids[1],.6,returned_model='B-fixed')
    result={'label':'success','source_url':'https://example.org/result','source_sha256':'b'*64,
            'first_public_at':datetime.now(timezone.utc).isoformat(),'endpoint_match_note':'same primary PFS target'}
    assert ledger.record_result(t['id'],result,reviewer_id='one')=='announced_pending_adjudication'
    assert build_metrics(ledger.snapshot())['common_tasks']==0
    with pytest.raises(ValueError):ledger.record_result(t['id'],result,reviewer_id='one')
    assert ledger.record_result(t['id'],result,reviewer_id='two')=='adjudicated'
    agreement = ledger.events()
    assert build_metrics(ledger.snapshot())['common_tasks'] == 0
    assert all(row['brier'] is None for row in build_metrics(ledger.snapshot())['by_model'])
    scientifically_accept(ledger, t)
    assert ledger.events()[:len(agreement)] == agreement
    metrics=build_metrics(ledger.snapshot())
    assert metrics['common_tasks']==1 and metrics['independent_projects']==1
    assert metrics['by_model'][0]['common_brier']==pytest.approx(.04)
    assert metrics['ranking_allowed'] is False


def test_failure_stays_in_denominator_and_common_set_empty(tmp_path):
    ledger,t,ids=registered(tmp_path);finish(ledger,ids[0])
    ledger.start(ids[1]);ledger.finish(ids[1],{'status':'unavailable','probability':None})
    m=build_metrics(ledger.snapshot())
    assert m['assigned']==2 and m['answered']==1 and m['failed']==1
    assert m['common_tasks']==0 and all(row['common_brier'] is None for row in m['by_model'])


def test_patent_pair_requires_identical_nonpatent_packet():
    t,s=spec();base={'id':'b','target_id':t['id'],'cohort_id':'c','model_id':'A','returned_model':'A','workflow_version':'v1',
        'condition':'clinical_only','track':'same_evidence','packet':s[0]['packet'],'status':'predicted','probability':.5}
    pat=deepcopy(base);pat.update(id='p',condition='with_patents',track='patent_ablation',probability=.6)
    pat['packet']['evidence'].append({'id':'pat','kind':'patent'})
    state={'runs':[base,pat],'targets':[t]}
    assert patent_pairs(state)[0]['probability_change']==pytest.approx(.1)
    pat['packet']['evidence'][0]['text']='changed clinical input'
    assert patent_pairs(state)==[]


def outcome():
    return {'label':'success','source_url':'https://example.org/result','source_sha256':'b'*64,
        'first_public_at':datetime.now(timezone.utc).isoformat(),'endpoint_match_note':'same primary PFS target'}


def receipt(ledger, purpose='predictions', response='c'):
    events=ledger.events()
    return {'purpose':purpose,'ledger_sha256':events[-1]['event_hash'],'covered_event_count':len(events),
        'tsa':'FreeTSA','generated_at_utc':datetime.now(timezone.utc).isoformat(),
        'verified_at_utc':datetime.now(timezone.utc).isoformat(),'verification_method':'openssl_ts_verify',
        'signature_verified':True,'timestamp_response_sha256':response*64,'manifest_sha256':'d'*64,
        'public_url':'https://example.org/attestations/test'}


def test_future_readout_screen_rejected():
    t,_=spec();t['readout_screen']['checked_at_utc']=t['submission_deadline_utc']
    with pytest.raises(ValueError,match='refreshed'):validate_target(t)


@pytest.mark.parametrize('field,value',[('eligible_for_forecast',False),('kind','target_result'),('is_target_result',True)])
def test_known_ineligible_or_target_outcome_cannot_be_registered(field,value):
    t,s=spec();s[0]['packet']['evidence'][0][field]=value
    with pytest.raises(ValueError):validate_packet(s[0]['packet'],t,'clinical_only')


def test_result_announcement_closes_unstarted_slots(tmp_path):
    ledger,t,ids=registered(tmp_path)
    ledger.record_result(t['id'],outcome(),reviewer_id='first')
    with pytest.raises(ValueError,match='public result'):ledger.start(ids[0])
    assert all(r['status']=='pending' and not r.get('started_at') for r in ledger.snapshot()['runs'])


def test_stale_screen_rechecked_at_execution(tmp_path,monkeypatch):
    import emeris_live.arena_store as store
    ledger,t,ids=registered(tmp_path)
    class Later(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime.now(timezone.utc)+timedelta(days=2)
    monkeypatch.setattr(store,'datetime',Later)
    with pytest.raises(ValueError):ledger.start(ids[0])


@pytest.mark.parametrize('field,value',[('packet',{}),('cohort_id','other'),('registered_at','old'),
    ('input_sha256','f'*64),('workflow_version','changed')])
def test_frozen_run_cannot_be_overwritten_by_output(tmp_path,field,value):
    ledger,t,ids=registered(tmp_path);ledger.start(ids[0])
    result={'status':'predicted','probability':.7,'evidence_ids':['e1'],field:value}
    if field=='input_sha256':
        # Provider input hashes have a separate namespace; immutable packet hash survives.
        ledger.finish(ids[0],result)
        run=ledger.snapshot()['runs'][0]
        assert run['provider_input_sha256']==value and run['input_sha256']==sha(run['packet'])
    else:
        with pytest.raises(ValueError):ledger.finish(ids[0],result)


def test_frozen_provider_contract_checked_on_completion(tmp_path):
    t,s=spec();s[0]['provider_config_snapshot']={'config_sha256':'a'*64}
    s[0]['expected_provider_input_sha256']='b'*64;s[0]['expected_prompt_sha256']='c'*64
    ledger=ArenaLedger(tmp_path);ids=ledger.register('c',[t],s,{})
    ledger.start(ids[0])
    base={'status':'predicted','probability':.5,'evidence_ids':['e1'],'config_sha256':'a'*64,
        'input_sha256':'b'*64,'prompt_sha256':'c'*64}
    for key in ('config_sha256','input_sha256','prompt_sha256'):
        with pytest.raises(ValueError):ledger.finish(ids[0],{**base,key:'f'*64})
    ledger.finish(ids[0],base)


def test_tampering_blocks_execution_before_billing(tmp_path):
    ledger,t,ids=registered(tmp_path)
    with ledger.connect() as db:
        db.execute('DROP TRIGGER arena_no_update')
        db.execute("UPDATE arena_events SET payload='{}' WHERE seq=1")
    with pytest.raises(ValueError,match='integrity'):ledger.start(ids[0])


def test_registration_timestamp_never_proves_later_predictions(tmp_path):
    ledger,t,ids=registered(tmp_path)
    ledger.attest(receipt(ledger,'registration'))
    finish(ledger,ids[0],returned_model='A-fixed')
    assert ledger.snapshot()['runs'][0]['timestamp_proof']['status']=='not_attested'
    proof=receipt(ledger,response='e');ledger.attest(proof)
    state=ledger.snapshot()
    assert state['runs'][0]['timestamp_proof']['status']=='attested'
    assert state['runs'][1]['timestamp_proof']['status']=='not_attested'
    with pytest.raises(ValueError,match='already'):ledger.attest(proof)


def test_timestamp_rejects_wrong_prefix_future_and_missing_signature(tmp_path):
    ledger,t,ids=registered(tmp_path);finish(ledger,ids[0])
    base=receipt(ledger)
    for key,value in [('ledger_sha256','f'*64),('covered_event_count',100),('signature_verified',False),
        ('generated_at_utc',(datetime.now(timezone.utc)+timedelta(days=1)).isoformat())]:
        with pytest.raises(ValueError):ledger.attest({**base,key:value})


def test_unconfirmed_returned_version_is_not_scored(tmp_path):
    ledger,t,ids=registered(tmp_path);finish(ledger,ids[0]);finish(ledger,ids[1],returned_model='B-fixed')
    r=outcome();ledger.record_result(t['id'],r,reviewer_id='one');ledger.record_result(t['id'],r,reviewer_id='two')
    scientifically_accept(ledger, t)
    m=build_metrics(ledger.snapshot())
    assert m['answered']==2 and m['common_tasks']==0
    assert m['by_model'][0]['scored_tasks']==0


def test_tsa_after_outcome_not_counted_as_prospective(tmp_path):
    ledger,t,ids=registered(tmp_path);finish(ledger,ids[0],returned_model='A-fixed')
    r=outcome();ledger.record_result(t['id'],r,reviewer_id='one');ledger.record_result(t['id'],r,reviewer_id='two')
    scientifically_accept(ledger, t)
    assert build_metrics(ledger.snapshot())['by_model'][0]['scored_tasks'] == 1
    ledger.attest(receipt(ledger))
    assert build_metrics(ledger.snapshot())['by_model'][0]['scored_tasks']==0


def test_paired_scores_exclude_mismatched_frozen_inputs(tmp_path):
    ledger,t,ids=registered(tmp_path)
    finish(ledger,ids[0],returned_model='A-fixed');finish(ledger,ids[1],returned_model='B-fixed')
    r=outcome();ledger.record_result(t['id'],r,reviewer_id='one');ledger.record_result(t['id'],r,reviewer_id='two')
    scientifically_accept(ledger, t)
    assert build_metrics(ledger.snapshot())['common_tasks'] == 1
    state=ledger.snapshot();state['runs'][1]['input_sha256']='f'*64
    m=build_metrics(state)
    assert m['common_tasks']==0 and m['paired_differences'][0]['tasks']==0


def admin_module():
    import importlib.util
    from pathlib import Path
    spec=importlib.util.spec_from_file_location('arena_admin_test',Path(__file__).resolve().parents[1]/'scripts/arena_admin.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_cli_frozen_execution_contract_detects_config_and_budget_changes():
    admin=admin_module();t,s=spec();slot=s[0];slot.pop('workflow_version')
    budget={'budget_policy':'token_only_unpriced','max_requests':1,'max_output_tokens':1024,
        'max_input_chars':4000,'timeout_seconds':90}
    model={'requested_model':'A-fixed','provider':'openai_compatible','config_sha256':'a'*64}
    slot.update(provider='openai_compatible',provider_config_snapshot=model)
    slot.update(admin.execution_contract(slot,budget))
    admin.verify_registered_model(slot,model,budget)
    with pytest.raises(ValueError,match='configuration'):
        admin.verify_registered_model(slot,{**model,'config_sha256':'b'*64},budget)
    with pytest.raises(ValueError,match='budget'):
        admin.verify_registered_model(slot,model,{**budget,'max_output_tokens':2048})
    with pytest.raises(ValueError,match='limits'):admin.validate_budget({'max_output_tokens':1024})


@pytest.mark.parametrize('status', ['predicted', 'abstain'])
def test_complete_outputs_require_complete_prompt_contract(tmp_path, status):
    t,s=spec();s[0]['expected_prompt_sha256']='c'*64
    ledger=ArenaLedger(tmp_path);ids=ledger.register('c',[t],s,{})
    ledger.start(ids[0])
    with pytest.raises(ValueError,match='prompt'):
        ledger.finish(ids[0],{'status':status,'probability':.5 if status=='predicted' else None,
            'evidence_ids':['e1'],'prompt_sha256':'d'*64})
    ledger.finish(ids[0],{'status':'failed','probability':None,'prompt_sha256':'d'*64,'contract_complete':True})
    assert ledger.snapshot()['runs'][0]['contract_complete'] is False


def test_real_shared_adapter_early_failure_preserves_receipt_and_billing(tmp_path, monkeypatch):
    """Real adapter path with isolated transport; this is not a live model call."""
    import hashlib
    import json
    import httpx
    from emeris_live import arena_providers as providers
    admin=admin_module()
    config=tmp_path/'models.json'
    config.write_text(json.dumps({'models':[{'id':'A','provider':'openai','model':'fixture-only',
        'api_key_env':'ARENA_ISOLATED_KEY','pricing':{'input_usd_per_million':1,'output_usd_per_million':2,
        'source_url':'https://example.org/test-only-prices','verified_at':'2026-01-01'}}]}))
    monkeypatch.setenv('EMERIS_ARENA_MODELS_FILE',str(config))
    monkeypatch.setenv('EMERIS_ENABLE_ARENA_LLM','true')
    monkeypatch.setenv('ARENA_ISOLATED_KEY','isolated-fixture-secret')
    original=httpx.Client;calls=[]
    def respond(request):
        calls.append(request)
        return httpx.Response(200,json={'model':'fixture-returned','choices':[{'finish_reason':'stop',
            'message':{'content':'{"not_the_required_review_schema":true}'}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20}})
    monkeypatch.setattr(providers.httpx,'Client',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    t,s=spec();slot=s[0];slot.pop('workflow_version');slot['track']='shared_agent'
    model=providers.list_models()[0]
    slot.update(requested_model=model['requested_model'],provider=model['provider'],provider_config_snapshot=model)
    budget={'max_requests':2,'max_output_tokens':512,'max_input_chars':4000,'timeout_seconds':30}
    slot.update(admin.execution_contract(slot,budget))
    ledger=ArenaLedger(tmp_path/'runtime');ident=ledger.register('early-stop',[t],[slot],{'execution_budget':budget})[0]
    started=ledger.start(ident)
    result=providers.forecast('A',started['packet'],workflow='evidence_review',budget=budget)
    assert len(calls)==1 and result['error_code']=='invalid_review'
    assert result['prompt_sha256']!=slot['expected_prompt_sha256']
    assert result['cost_usd']==pytest.approx(.00014)
    admin.finish_with_receipt(ledger,tmp_path/'runtime',ident,started,result)
    run=ledger.snapshot()['runs'][0]
    assert run['status']=='failed' and run['contract_complete'] is False
    assert run['attempts'][0]['stage']=='evidence_review' and run['usage']['input_tokens']==100
    assert run['cost_usd']==pytest.approx(.00014) and build_metrics(ledger.snapshot(),track='shared_agent')['failed']==1
    saved=tmp_path/'runtime'/run['receipt_path'];raw=saved.read_bytes()
    assert hashlib.sha256(raw).hexdigest()==run['receipt_sha256']
    assert json.loads(raw)['result']['attempts']==run['attempts']
    assert saved.stat().st_mode & 0o777==0o600
    assert b'isolated-fixture-secret' not in raw and not run['receipt_path'].startswith('/')
    with pytest.raises(ValueError,match='already started'):ledger.start(ident)


def test_contract_rejection_is_terminal_with_receipt_and_known_cost(tmp_path,monkeypatch):
    import hashlib
    import json
    admin=admin_module();ledger,t,ids=registered(tmp_path);slot=ledger.start(ids[0])
    result={'status':'predicted','probability':.8,'evidence_ids':['invented'],
        'cost_usd':None,'known_cost_usd':.001,'usage':{'input_tokens':100},'returned_model':'observed-version',
        'attempts':[{'stage':'forecast','cost_usd':.001,'usage':{'input_tokens':100},'raw_error':'secret-error'}],
        'api_key':'top-secret','raw_response':'private transport response'}
    original=ledger.finish
    def receipt_exists_first(ident,value):
        path=tmp_path/value['receipt_path']
        assert path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==value['receipt_sha256']
        return original(ident,value)
    monkeypatch.setattr(ledger,'finish',receipt_exists_first)
    rejected=admin.finish_with_receipt(ledger,tmp_path,ids[0],slot,result)
    run=ledger.snapshot()['runs'][0]
    assert rejected['error_code']==run['error_code']=='contract_rejected'
    assert run['probability'] is None and run['known_cost_usd']==.001 and run['cost_usd'] is None
    assert run['contract_complete'] is False and run['returned_model']=='observed-version'
    raw=(tmp_path/run['receipt_path']).read_text()
    assert all(secret not in raw for secret in ['top-secret','secret-error','private transport response'])
    assert json.loads(raw)['result']['probability']==.8
    with pytest.raises(ValueError):ledger.start(ids[0])
    assert build_metrics(ledger.snapshot())['assigned']==2 and build_metrics(ledger.snapshot())['failed']==1


def test_receipts_never_overwrite_existing_file(tmp_path,monkeypatch):
    admin=admin_module()
    class Fixed:
        hex='fixed-fixture'
    monkeypatch.setattr(admin.uuid,'uuid4',lambda:Fixed())
    _,link=admin.save_result_receipt(tmp_path,'run-fixture',{'status':'failed','probability':None})
    path=tmp_path/link['receipt_path'];before=path.read_bytes()
    with pytest.raises(FileExistsError):
        admin.save_result_receipt(tmp_path,'run-fixture',{'status':'predicted','probability':.9})
    assert path.read_bytes()==before
