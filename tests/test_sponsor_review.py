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
