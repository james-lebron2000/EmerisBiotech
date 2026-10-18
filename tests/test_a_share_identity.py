"""Isolated synthetic A-share source-name admission; not real clinical evidence."""
import hashlib
import json
import sqlite3
import time
import datetime
from types import SimpleNamespace

import pytest

from test_issuer_directory_names import market,seed,candidates,sse,xlsx
from vbt import a_share_identity as adapter,issuer_directory_names as names
from vbt import identity_collection as identity
from vbt.sponsor_resolution import resolve,proposals


def setup_case(market,source='SH'):
    seed(market,source)
    names.enrich(market,source)
    candidate=candidates(market)[0]
    english=names.query_name(market,candidate)
    canonical={'protocolSection':{'identificationModule':{'nctId':'NCT00000001'},
        'sponsorCollaboratorsModule':{'leadSponsor':{'name':english}}}}
    resolve(market,candidate['id'],fetch=lambda u:json.dumps({'studies':[canonical]}).encode())
    proposal=proposals(market,candidate['id'])[0]
    return candidate,proposal,canonical


@pytest.mark.parametrize('source',names.SOURCES)
def test_admits_exact_sealed_name_with_one_fresh_canonical_fetch_and_typed_atomic_review(market,source):
    candidate,p,canonical=setup_case(market,source)
    assert p['id'] in {r['id'] for r in identity.eligible_proposals(market)}
    calls=[]
    result=identity.collect(market,p['id'],fetch=lambda url:(calls.append(url) or json.dumps(canonical).encode()))
    assert result['status']=='succeeded',result['error']
    assert calls==[identity.NCT_API+'NCT00000001']
    assert result['company']==candidate['id'] and result['review_id']
    assert market.count('companies')==1 and market.count('trials')==0
    with market.db() as db:
        review=db.execute('SELECT * FROM sponsor_review_evidence WHERE review_id=?',(result['review_id'],)).fetchone()
        assert review['reviewer_type']==adapter.REVIEWER_TYPE
        proof=json.loads(review['evidence_json'])
        assert proof['adapter_version']==adapter.VERSION and proof['scope']==adapter.SCOPE
        assert proof['human_sign_off'] is proof['drug_ownership_verified'] is proof['drug_developer_status_verified'] is False
        assert proof['full_pipeline_verified'] is False and proof['scientific_review']=='pending'
        assert proof['exchange_fetched_at']==proof['directory_name_receipt']['retrieved_at']
        assert proof['directory_age_limit_days']==7 and proof['directory_source_age_seconds']>=-300
        assert db.execute('SELECT status FROM sponsor_proposals WHERE id=?',(p['id'],)).fetchone()[0]=='accepted'
    for kind in ('proof','exchange','registry'):
        raw,mime=adapter.evidence_file(market,result['run_id'],kind)
        assert isinstance(raw,bytes) and raw
        assert mime==('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' if kind=='exchange' and source=='SZ' else 'application/json; charset=utf-8')
    again=identity.collect(market,p['id'],fetch=lambda u:pytest.fail('Idempotent recovery must not re-fetch'))
    assert again['run_id']==result['run_id'] and market.count('companies')==1


@pytest.mark.parametrize('change',['uncertain','excluded','stale_hash','missing_receipt','partial','changed_query','BSE','HK'])
def test_ineligible_inputs_do_not_fetch_or_map(market,change):
    candidate,p,_=setup_case(market)
    with market.db() as db:
        if change in ('uncertain','excluded'):db.execute('UPDATE issuer_candidates SET classification=? WHERE id=?',(change,candidate['id']))
        elif change=='stale_hash':db.execute("UPDATE issuer_candidates SET content_hash='changed' WHERE id=?",(candidate['id'],))
        elif change=='missing_receipt':db.execute("UPDATE universe_snapshots SET rows_sha256='changed'")
        elif change=='partial':db.execute("UPDATE sponsor_resolution_runs SET status='partial',truncated=1 WHERE id=?",(p['run_id'],))
        elif change=='changed_query':db.execute("UPDATE sponsor_resolution_runs SET query='different input' WHERE id=?",(p['run_id'],))
        elif change=='BSE':db.execute("UPDATE listings SET source='BSE'")
        else:db.execute("UPDATE issuer_candidates SET market='HK' WHERE id=?",(candidate['id'],))
    assert p['id'] not in {r['id'] for r in identity.eligible_proposals(market)}
    result=identity.collect(market,p['id'],fetch=lambda u:pytest.fail('Blocked identity must not fetch'))
    assert result['status']=='blocked' and market.count('companies')==0


@pytest.mark.parametrize('stamp',['2000-01-01T00:00:00+00:00','2099-01-01T00:00:00+00:00'])
def test_directory_age_uses_original_capture_not_fresh_enrich_time(market,stamp):
    rid=seed(market)
    meta_path=market.root/'captures'/rid/'0.json';meta=json.loads(meta_path.read_text())
    meta['retrieved_at']=stamp;meta_path.write_text(json.dumps(meta))
    assert names.enrich(market,'SH')['status']=='succeeded'
    candidate=candidates(market)[0];english=names.query_name(market,candidate)
    canonical={'protocolSection':{'identificationModule':{'nctId':'NCT00000001'},'sponsorCollaboratorsModule':{'leadSponsor':{'name':english}}}}
    resolve(market,candidate['id'],fetch=lambda u:json.dumps({'studies':[canonical]}).encode())
    p=proposals(market,candidate['id'])[0]
    result=identity.collect(market,p['id'],fetch=lambda u:pytest.fail('Stale directory must not be refreshed implicitly'))
    assert result['status']=='blocked' and 'stale or future' in result['error']
    assert market.count('companies')==0


@pytest.mark.parametrize('body',[b'[]',b'null',b'{"protocolSection":[]}',b'{"protocolSection":{"identificationModule":null}}'])
def test_malformed_canonical_source_retained_but_never_publishes(market,body):
    _,p,_=setup_case(market)
    result=identity.collect(market,p['id'],fetch=lambda u:body)
    assert result['status']=='failed' and 'ValueError' in result['error']
    assert market.count('companies')==0 and (market.root/'identity_review'/result['run_id']/'registry.json').read_bytes()==body


@pytest.mark.parametrize('change',['alias','nct','network','cancel','hash_during_fetch','source_during_fetch','new_owner','proposal_rejected'])
def test_source_failure_cancellation_and_input_or_owner_race_fail_closed(market,change):
    candidate,p,canonical=setup_case(market)
    cancelled=[False]
    def fetch(url):
        if change=='network':raise OSError('Connection failed synthetic')
        if change=='alias':canonical['protocolSection']['sponsorCollaboratorsModule']['leadSponsor']['name']='Different legal sponsor'
        if change=='nct':canonical['protocolSection']['identificationModule']['nctId']='NCT99999999'
        if change=='cancel':cancelled[0]=True
        with market.db() as db:
            if change=='hash_during_fetch':db.execute("UPDATE issuer_candidates SET content_hash='changed' WHERE id=?",(candidate['id'],))
            if change=='source_during_fetch':db.execute("UPDATE universe_snapshots SET rows_sha256='changed'")
            if change=='new_owner':db.execute("UPDATE leases SET owner='new-owner',until=? WHERE key=?",(time.time()+1000,'identity:'+p['id']))
            if change=='proposal_rejected':db.execute("UPDATE sponsor_proposals SET status='rejected' WHERE id=?",(p['id'],))
        return json.dumps(canonical).encode()
    result=identity.collect(market,p['id'],fetch=fetch,cancelled=lambda:cancelled[0])
    assert result['status'] in ('failed','cancelled','superseded') and market.count('companies')==0
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0]==0
        if change=='new_owner':assert db.execute('SELECT owner FROM leases WHERE key=?',('identity:'+p['id'],)).fetchone()[0]=='new-owner'


def test_sql_failure_rolls_back_mapping_review_and_run_success_together(market):
    _,p,canonical=setup_case(market)
    with market.db() as db:
        identity.init_schema(market)
        db.execute("CREATE TRIGGER synthetic_abort_success BEFORE UPDATE ON identity_collection_runs WHEN NEW.status='succeeded' BEGIN SELECT RAISE(ABORT,'synthetic completion failure'); END")
    result=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    assert result['status']=='failed' and market.count('companies')==0
    with market.db() as db:
        assert db.execute('SELECT count(*) FROM sponsor_proposal_reviews').fetchone()[0]==0
        assert db.execute('SELECT status FROM sponsor_proposals WHERE id=?',(p['id'],)).fetchone()[0]=='pending'


def test_same_name_new_snapshot_remains_idempotent_but_historical_read_survives_current_proof_loss(market):
    candidate,p,canonical=setup_case(market)
    first=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    assert first['status']=='succeeded',first['error']
    seed(market);names.enrich(market,'SH')
    assert identity.collect(market,p['id'],fetch=lambda u:pytest.fail('Same legal name is idempotent'))['run_id']==first['run_id']
    with market.db() as db:
        db.execute("UPDATE universe_snapshots SET rows_sha256='changed'")
        db.execute("UPDATE issuer_candidates SET content_hash='changed' WHERE id=?",(candidate['id'],))
    for kind in ('proof','exchange','registry'):
        assert adapter.evidence_file(market,first['run_id'],kind)[0]
    again=identity.collect(market,p['id'],fetch=lambda u:pytest.fail('Current invalid proof cannot reapprove mapping'))
    assert again['status']=='blocked' and again['run_id']!=first['run_id']


@pytest.mark.parametrize('name',['evidence.json','a-share-directory.bin','registry.json','exchange_manifest.json','capture_manifest.json'])
def test_history_rejects_tampered_frozen_files(market,name):
    _,p,canonical=setup_case(market)
    result=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    path=market.root/'identity_review'/result['run_id']/name
    path.write_bytes(b'tampered')
    with pytest.raises((ValueError,KeyError,TypeError,OSError)):
        adapter.evidence_file(market,result['run_id'],'exchange')


def test_historical_reader_is_read_only_and_does_not_expire_old_proof(market,monkeypatch):
    _,p,canonical=setup_case(market)
    first=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    class FutureClock(datetime.datetime):
        @classmethod
        def now(cls,tz=None):return cls(2099,1,1,tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(adapter,'datetime',SimpleNamespace(datetime=FutureClock,timezone=datetime.timezone,timedelta=datetime.timedelta))
    statements=[]
    def ro():
        db=sqlite3.connect(market.path.resolve().as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
        db.set_trace_callback(statements.append)
        return db
    monkeypatch.setattr(market,'db',ro)
    for kind in ('proof','exchange','registry'):assert adapter.evidence_file(market,first['run_id'],kind)[0]
    assert all(sql.lstrip().upper().startswith(('BEGIN','COMMIT','SELECT')) for sql in statements)


def test_lost_attempt_with_exact_accepted_review_recovers_without_deleting_new_lease(market):
    _,p,canonical=setup_case(market)
    first=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    with market.db() as db:
        db.execute("UPDATE identity_collection_runs SET status='running',review_id=NULL,company=NULL WHERE id=?",(first['run_id'],))
        db.execute('INSERT INTO leases VALUES (?,?,?)',('identity:'+p['id'],time.time()+1000,'new-owner'))
    identity.init_schema(market)
    with market.db() as db:
        current=db.execute('SELECT * FROM identity_collection_runs WHERE id=?',(first['run_id'],)).fetchone()
        assert current['status']=='succeeded' and current['review_id']==first['review_id'] and current['recovered']==1
        assert db.execute('SELECT owner FROM leases WHERE key=?',('identity:'+p['id'],)).fetchone()[0]=='new-owner'


def test_admission_does_not_trust_a_stale_pending_row_after_rejection(market):
    _,p,_=setup_case(market)
    identity.init_schema(market)
    with market.db() as db:
        row=dict(identity._row(db,p['id']))
        db.execute("UPDATE sponsor_proposals SET status='rejected' WHERE id=?",(p['id'],))
        with pytest.raises(ValueError,match='changed since'):
            adapter.admission_context(market,db,row)


def test_success_flag_without_matching_run_candidate_cannot_recover_or_serve_evidence(market):
    _,p,canonical=setup_case(market)
    first=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    with market.db() as db:
        db.execute("UPDATE identity_collection_runs SET candidate_id='A:999999' WHERE id=?",(first['run_id'],))
    with pytest.raises(ValueError):adapter.evidence_file(market,first['run_id'],'proof')
    result=identity.collect(market,p['id'],fetch=lambda u:pytest.fail('Bad old run cannot be accepted again'))
    assert result['status']=='blocked' and result['run_id']!=first['run_id']


def test_recovery_cannot_borrow_another_run_completed_proof_even_for_same_proposal(market):
    _,p,canonical=setup_case(market)
    first=identity.collect(market,p['id'],fetch=lambda u:json.dumps(canonical).encode())
    with market.db() as db:
        old=dict(db.execute('SELECT * FROM identity_collection_runs WHERE id=?',(first['run_id'],)).fetchone())
        wrong={**old,'id':'b'*32,'status':'running','review_id':None,'company':None}
        db.execute('INSERT INTO identity_collection_runs VALUES ('+','.join('?' for _ in wrong)+')',tuple(wrong.values()))
    identity.init_schema(market)
    with market.db() as db:
        assert db.execute('SELECT status FROM identity_collection_runs WHERE id=?',('b'*32,)).fetchone()[0]=='interrupted'


def test_duplicate_json_sponsor_fields_are_ambiguous_not_an_exact_proof(market):
    _,p,canonical=setup_case(market)
    raw=json.dumps(canonical).replace('"leadSponsor":', '"leadSponsor":{"name":"Different Sponsor"},"leadSponsor":').encode()
    result=identity.collect(market,p['id'],fetch=lambda u:raw)
    assert result['status']=='failed' and market.count('companies')==0


def test_queue_ready_to_atomic_identity_success_and_company_followup(market,monkeypatch):
    from vbt import market_jobs as jobs
    candidate,p,canonical=setup_case(market)
    states=jobs.identity_schedule(market)
    assert next(r for r in states if r['proposal_id']==p['id'])['status']=='ready'
    queued=jobs.enqueue_identity(market)
    assert len(queued)==1 and jobs.enqueue_identity(market)==[]
    original=identity.collect;calls=[]
    def fetch(url):calls.append(url);return json.dumps(canonical).encode()
    monkeypatch.setattr(identity,'collect',lambda m,pid,**kw:original(m,pid,fetch=fetch,**kw))
    job=jobs.claim(market)
    assert job['id']==queued[0] and job['kind']=='identity'
    jobs.execute(market,job)
    assert calls==[identity.NCT_API+'NCT00000001']
    with market.db() as db:
        finished=db.execute('SELECT * FROM collection_jobs WHERE id=?',(queued[0],)).fetchone()
        assert finished['status']=='succeeded' and finished['run_id']
        attempt=db.execute('SELECT * FROM identity_collection_runs WHERE id=?',(finished['run_id'],)).fetchone()
        assert attempt['status']=='succeeded' and attempt['review_id']
        assert db.execute('SELECT status FROM sponsor_proposals WHERE id=?',(p['id'],)).fetchone()[0]=='accepted'
        assert db.execute('SELECT reviewer_type FROM sponsor_review_evidence WHERE review_id=?',(attempt['review_id'],)).fetchone()[0]==adapter.REVIEWER_TYPE
        company=db.execute("SELECT * FROM collection_jobs WHERE kind='company'").fetchone()
        assert company['reference']==candidate['id'] and company['status']=='queued'
    assert market.count('companies')==1 and market.count('trials')==0


@pytest.mark.parametrize('change',['stale_source','query_mismatch','uncertain'])
def test_force_queue_does_not_bypass_a_share_source_or_classification_gate(market,change):
    from vbt import market_jobs as jobs
    candidate,p,_=setup_case(market)
    with market.db() as db:
        if change=='stale_source':db.execute("UPDATE universe_snapshots SET rows_sha256='changed'")
        elif change=='query_mismatch':db.execute("UPDATE sponsor_resolution_runs SET query='different name' WHERE id=?",(p['run_id'],))
        else:db.execute("UPDATE issuer_candidates SET classification='uncertain' WHERE id=?",(candidate['id'],))
    assert jobs.enqueue_identity(market,force=True)==[]
    assert market.count('companies')==0


def test_admission_rechecks_current_candidate_review_without_a_hash_change(market):
    _,p,_=setup_case(market)
    identity.init_schema(market)
    with market.db() as db:
        stale=dict(identity._row(db,p['id']))
        db.execute("UPDATE issuer_candidates SET review_status='rejected' WHERE id=?",(stale['candidate_id'],))
        with pytest.raises(ValueError,match='candidate eligibility changed'):
            adapter.admission_context(market,db,stale)
