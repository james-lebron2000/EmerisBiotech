import json
import pytest
from vbt.market import Market
from vbt.market_coverage import refresh_candidates
from vbt.sponsor_resolution import resolve,proposals
from vbt.sponsor_review import review
from vbt.market_jobs import enqueue,claim,execute,jobs
from vbt.workbench import Workbench,page

@pytest.fixture
def case(tmp_path):
 m=Market(tmp_path/'w',tmp_path/'s')
 refresh_candidates(m,[{'market':'US','ticker':'TEST','name':'Synthetic Therapeutics Inc. - Common Stock','classification':'candidate','reason':'Synthetic fixture only','source':'synthetic','evidence':[{'source':'synthetic'}],'observed':'2026-10-17T00:00:00+00:00'}])
 raw=json.dumps({'studies':[{'protocolSection':{'identificationModule':{'nctId':'NCT00000001'},'sponsorCollaboratorsModule':{'leadSponsor':{'name':'Synthetic Therapeutics'}}}}]}).encode()
 return m,raw

def test_explicit_review_adds_mapping_without_claiming_pipeline(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw)
 assert m.rows('companies')==[]
 p=proposals(m)[0];result=review(m,p['id'],'accepted','SYNTHETIC TEST','Synthetic identity review of the documented source')
 assert result['company']=='US:TEST' and len(m.rows('companies'))==1 and m.count('trials')==0
 assert proposals(m)[0]['status']=='accepted'
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Test','Another review cannot overwrite prior review')

def test_changed_candidate_cannot_be_accepted(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0]
 with m.db() as db:db.execute("UPDATE issuer_candidates SET content_hash='changed'")
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Test','Synthetic stale-candidate rejection')
 assert m.count('companies')==0 and proposals(m)[0]['status']=='pending'

def test_reject_does_not_configure(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw)
 review(m,proposals(m)[0]['id'],'rejected','Test','Synthetic company and sponsor do not match')
 assert m.count('companies')==0

def test_resolution_runs_in_durable_queue(case,monkeypatch):
 import vbt.sponsor_resolution as module
 m,raw=case;original=module.resolve
 monkeypatch.setattr(module,'resolve',lambda market,cid,**kw:original(market,cid,fetch=lambda u:raw,**kw))
 enqueue(m,'resolve','US:TEST');execute(m,claim(m))
 assert jobs(m)[0]['status']=='succeeded' and jobs(m)[0]['run_id']
 assert proposals(m)[0]['status']=='pending' and m.count('companies')==0

def test_mapping_page_keeps_suggestions_pending(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw)
 app=Workbench(m.root.parent.parent,m.state)
 rendered=page(app,'/mapping',{})
 assert '接受关联并排队抓取' in rendered and '待主体核对' in rendered
 assert m.count('companies')==0

def archived_evidence(m,raw,p):
 import hashlib
 from vbt.io import now
 root=m.root/'identity_review'/'synthetic';root.mkdir(parents=True)
 def save(name,value):
  path=root/name;path.write_bytes(value)
  return {'path':str(path.relative_to(m.root.parent.parent)),'sha256':hashlib.sha256(value).hexdigest(),'bytes':len(value),'status':200}
 stamp=now()
 exchange=save('exchange.txt',b'Symbol|Security Name|Test Issue|ETF\nTEST|Synthetic Therapeutics Inc. - Common Stock|N|N\n')
 exchange.update(url='https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt',fetched_at=stamp)
 ex_path=save('exchange_manifest.json',json.dumps(exchange).encode())['path']
 registry=save('registry.json',json.dumps(json.loads(raw)['studies'][0]).encode())
 registry.update(url='https://clinicaltrials.gov/api/v2/studies/NCT00000001',fetched_at=stamp,ticker='TEST',kind='registry')
 captures_path=save('capture_manifest.json',json.dumps([registry]).encode())['path']
 return {'status':'verified','candidate_id':'US:TEST','proposal_id':p['id'],'candidate_hash':p['candidate_hash'],'alias':p['alias'],'verified_company_name':'Synthetic Therapeutics Inc.','market':'US','ticker':'TEST','official_source_url':exchange['url'],'registry_source_url':p['source_url'],'registry_api_url':registry['url'],'supporting_fact':'Synthetic issuer and sponsor names agree; fixture only','checked_at':stamp,'exchange_fetched_at':stamp,'registry_fetched_at':stamp,'exchange_sha256':exchange['sha256'],'registry_sha256':registry['sha256'],'exchange_capture_manifest':ex_path,'evidence_capture_manifest':captures_path,'reviewer_type':'automated_source_review','scope':'listed_entity_to_exact_registry_sponsor_name_only','human_sign_off':False,'drug_ownership_verified':False}

def test_automated_review_is_typed_and_requires_independent_source(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0]
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Source agent','Synthetic source reconciliation check',reviewer_type='automated_source_review')
 evidence=archived_evidence(m,raw,p)
 r=review(m,p['id'],'accepted','Source agent','Synthetic source reconciliation check',reviewer_type='automated_source_review',evidence=evidence)
 with m.db() as db:assert db.execute('SELECT reviewer_type FROM sponsor_review_evidence WHERE review_id=?',(r['review_id'],)).fetchone()[0]=='automated_source_review'
 assert 'not human sign-off' in m.rows('companies')[0]['provenance']

@pytest.mark.parametrize('field,value',[
 ('proposal_id','wrong-proposal'),('candidate_hash','old-hash'),('ticker','OTHER'),('market','HK'),
 ('verified_company_name','Unrelated Company'),('official_source_url','https://unrelated.example/'),
 ('checked_at','not-a-date'),('checked_at','2026-10-17T00:00:00'),('checked_at','2099-01-01T00:00:00+00:00'),
 ('checked_at','2000-01-01T00:00:00+00:00'),('human_sign_off',True),('drug_ownership_verified',True),
 ('reviewer_type','human_declared'),('registry_api_url','https://clinicaltrials.gov/api/v2/studies/NCT99999999'),
 ('registry_sha256','a'*64),('exchange_sha256','b'*64),
 ('exchange_capture_manifest','artifacts/market/../../escape.json')])
def test_automated_review_rejects_unbound_or_unverifiable_evidence(case,field,value):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0];evidence=archived_evidence(m,raw,p);evidence[field]=value
 with pytest.raises((ValueError,OSError)):review(m,p['id'],'accepted','Source agent','Synthetic source reconciliation check',reviewer_type='automated_source_review',evidence=evidence)
 assert m.count('companies')==0 and proposals(m)[0]['status']=='pending'

@pytest.mark.parametrize('source',['registry.json','exchange.txt'])
def test_automated_review_rejects_archive_tampering(case,source):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0];evidence=archived_evidence(m,raw,p)
 (m.root/'identity_review'/'synthetic'/source).write_text('tampered')
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Source agent','Synthetic source reconciliation check',reviewer_type='automated_source_review',evidence=evidence)
 assert m.count('companies')==0 and proposals(m)[0]['status']=='pending'

@pytest.mark.parametrize('change',['registry_name','registry_nct','exchange_name','exchange_etf','exchange_test'])
def test_automated_review_parses_raw_identity_even_with_matching_hashes(case,change):
 import hashlib
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0];evidence=archived_evidence(m,raw,p)
 root=m.root/'identity_review'/'synthetic'
 if change.startswith('registry'):
  path=root/'registry.json';document=json.loads(path.read_text())
  if change=='registry_name':document['protocolSection']['sponsorCollaboratorsModule']['leadSponsor']['name']='Unrelated Sponsor'
  else:document['protocolSection']['identificationModule']['nctId']='NCT99999999'
  body=json.dumps(document).encode();path.write_bytes(body)
  captures=json.loads((root/'capture_manifest.json').read_text());record=captures[0]
  record.update(sha256=hashlib.sha256(body).hexdigest(),bytes=len(body));evidence['registry_sha256']=record['sha256']
  (root/'capture_manifest.json').write_text(json.dumps(captures))
 else:
  path=root/'exchange.txt';body=path.read_bytes()
  if change=='exchange_name':body=body.replace(b'Synthetic Therapeutics Inc.',b'Unrelated Sponsor Inc.')
  elif change=='exchange_etf':body=body.replace(b'|N|N\n',b'|N|Y\n')
  else:body=body.replace(b'|N|N\n',b'|Y|N\n')
  path.write_bytes(body);record=json.loads((root/'exchange_manifest.json').read_text())
  record.update(sha256=hashlib.sha256(body).hexdigest(),bytes=len(body));evidence['exchange_sha256']=record['sha256']
  (root/'exchange_manifest.json').write_text(json.dumps(record))
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Source agent','Synthetic source reconciliation check',reviewer_type='automated_source_review',evidence=evidence)
 assert m.count('companies')==0

def test_automated_review_matches_current_candidate_name(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0];evidence=archived_evidence(m,raw,p)
 with m.db() as db:db.execute("UPDATE issuer_candidates SET name='Unrelated Sponsor Inc.' WHERE id='US:TEST'")
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Source agent','Synthetic source reconciliation check',reviewer_type='automated_source_review',evidence=evidence)
 assert m.count('companies')==0


@pytest.mark.parametrize('frozen',['not-json','[]','{"query":"different"}'])
def test_invalid_frozen_query_context_blocks_acceptance_without_mutating_history(case,frozen):
 m,raw=case;result=resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0]
 with m.db() as db:db.execute('UPDATE sponsor_resolution_runs SET query_evidence_json=? WHERE id=?',(frozen,result['run_id']))
 assert proposals(m)[0]['query_input_stale']
 with pytest.raises(ValueError,match='Frozen query evidence'):
  review(m,p['id'],'accepted','Synthetic reviewer','Synthetic acceptance must retain bound lookup input')
 assert m.count('companies')==0


def test_legacy_empty_query_context_remains_unrecorded_and_requires_current_matching_query(case):
 m,raw=case;result=resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0]
 with m.db() as db:db.execute("UPDATE sponsor_resolution_runs SET query_evidence_json='{}' WHERE id=?",(result['run_id'],))
 assert not proposals(m)[0]['query_input_stale']
 review(m,p['id'],'accepted','Synthetic reviewer','Synthetic explicit legacy input identity review')
 assert m.count('companies')==1


def test_automated_review_parses_official_otherlisted_act_symbol(case):
 import hashlib
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0]
 evidence=archived_evidence(m,raw,p);root=m.root/'identity_review'/'synthetic'
 body=b'ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Test Issue|NASDAQ Symbol\nTEST|Synthetic Therapeutics Inc. Common Stock|N|TEST|N|N|DIFFERENT\n'
 (root/'exchange.txt').write_bytes(body)
 record=json.loads((root/'exchange_manifest.json').read_text())
 record.update(url='https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt',bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
 (root/'exchange_manifest.json').write_text(json.dumps(record))
 evidence.update(official_source_url=record['url'],exchange_sha256=record['sha256'])
 result=review(m,p['id'],'accepted','Source agent','Synthetic otherlisted name reconciliation',reviewer_type='automated_source_review',evidence=evidence)
 assert result['company']=='US:TEST'


def test_uncertain_classification_cannot_be_accepted_automatically(case):
 m,raw=case;resolve(m,'US:TEST',fetch=lambda u:raw);p=proposals(m)[0]
 evidence=archived_evidence(m,raw,p)
 with m.db() as db:db.execute("UPDATE issuer_candidates SET classification='uncertain'")
 with pytest.raises(ValueError):review(m,p['id'],'accepted','Source agent','Synthetic uncertain issuer must be reviewed',reviewer_type='automated_source_review',evidence=evidence)
 assert m.count('companies')==0
 # Human-declared decisions remain backward compatible and separately typed.
 assert review(m,p['id'],'accepted','Declared human','Synthetic explicit classification-aware identity review')['company']=='US:TEST'
