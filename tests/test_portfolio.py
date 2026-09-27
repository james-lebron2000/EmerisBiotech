import copy,json
from pathlib import Path
import pytest
from vbt.portfolio import PortfolioStore,evaluate,canonical
from vbt.portfolio_models import PortfolioPlan,PortfolioReview
from vbt.io import digest
ROOT=Path(__file__).resolve().parents[1]
@pytest.fixture
def inputs(tmp_path):
 p=json.loads((ROOT/'examples/portfolio_mvp.json').read_text());c=json.loads((ROOT/'docs/intelligence/2026-09-25-mnc/assets.json').read_text())
 a=tmp_path/'plan.json';b=tmp_path/'catalog.json';a.write_text(json.dumps(p));b.write_text(json.dumps(c));return p,c,a,b
@pytest.fixture
def store(tmp_path):
 s=PortfolioStore(tmp_path/'workspace',tmp_path/'state');yield s;s.close()
def build(store,inputs):return store.build(inputs[2],inputs[3])['snapshot_id']
def test_gates(inputs):
 p,c,*_=inputs;r=evaluate(PortfolioPlan.model_validate(p),c)
 assert len(r['items'])==3 and all(x['experiment_gate']=='blocked' for x in r['items'])
 assert any(x['code']=='unresolved_counterevidence:OSMR_NEG' for x in r['items'][0]['gaps'])
 assert all(x['reviewed_support_families']==0 for x in r['items'])
@pytest.mark.parametrize('mutation',['dangling','duplicate','javascript','future','unknown_field'])
def test_contract_rejects(inputs,mutation):
 p=inputs[0]
 if mutation=='dangling':p['hypotheses'][0]['links'][0]['evidence_id']='missing'
 if mutation=='duplicate':p['sources'].append(p['sources'][0])
 if mutation=='javascript':p['sources'][0]['url']='javascript:alert(1)'
 if mutation=='future':p['sources'][0]['checked_on']='2099-01-01'
 if mutation=='unknown_field':p['invented_approval']=True
 with pytest.raises(ValueError):PortfolioPlan.model_validate(p)
def test_freeze_and_tamper(store,inputs):
 sid=build(store,inputs);inputs[2].write_text('{}');assert store.verify(sid)['integrity_ok']
 (store.path(sid)/'index.html').write_text('tampered');assert not store.verify(sid)['integrity_ok']
def test_parent_retains_counterevidence(store,inputs):
 sid=build(store,inputs);p=inputs[0];p['hypotheses'][0]['links']=[x for x in p['hypotheses'][0]['links'] if x['stance']!='oppose'];inputs[2].write_text(json.dumps(p))
 with pytest.raises(ValueError,match='counterevidence'):store.build(inputs[2],inputs[3],sid)
def test_path_escape(store):
 with pytest.raises(ValueError):store.path('../../bad')
def test_reviews_and_recovery(store,inputs,tmp_path):
 sid=build(store,inputs)
 r=PortfolioReview(hypothesis_id='H_UC_OSMR',action='authorize_experiment',reviewer='TEST ONLY',reason='Unit test record, no human approval.',signature='TEST ONLY')
 with pytest.raises(ValueError,match='blocked'):store.review(sid,r)
 r.action='request_revision';store.review(sid,r)
 b=tmp_path/'backup.json';receipt=store.backup(b)
 other=PortfolioStore(tmp_path/'recovered',tmp_path/'state2')
 try:
  with pytest.raises(ValueError,match='checksum'):other.restore(b,'0'*64)
  assert other.restore(b,receipt['sha256'])=={'restored_snapshots':1,'restored_reviews':1}
  assert other.verify(sid)['integrity_ok'] and other.events()==store.events()
 finally:other.close()
def test_interruption(store,inputs,monkeypatch):
 import vbt.portfolio_report as report
 def fail(*a):raise RuntimeError('injected interruption')
 monkeypatch.setattr(report,'render',fail)
 with pytest.raises(RuntimeError):build(store,inputs)
 assert not store.status()['snapshots'] and store.status()['unfinished_builds']
def test_html_escape(store,inputs):
 p=inputs[0];p['hypotheses'][0]['title']='<script>alert(1)</script>';inputs[2].write_text(json.dumps(p));sid=build(store,inputs)
 html=(store.path(sid)/'index.html').read_text();assert '<script>alert(1)</script>' not in html and '&lt;script&gt;' in html
@pytest.mark.parametrize('mutation',['path','event'])
def test_corrupt_backup_atomic(store,inputs,tmp_path,mutation):
 sid=build(store,inputs);b=tmp_path/'backup.json';store.backup(b);obj=json.loads(b.read_text())
 if mutation=='path':obj['packages'][sid]['../../escape']='eA=='
 else:obj['metadata']['review_events']=[{'payload':{'review':{}},'sha256':'bad'}]
 b.write_text(json.dumps(obj));other=PortfolioStore(tmp_path/'restore',tmp_path/'state3')
 try:
  with pytest.raises((ValueError,KeyError)):other.restore(b,digest(b))
  assert not other.status()['snapshots']
 finally:other.close()
def test_event_chain_tamper(store,inputs):
 sid=build(store,inputs);store.review(sid,PortfolioReview(hypothesis_id='H_UC_OSMR',action='stop',reviewer='TEST',reason='Synthetic test of stop event.',signature='TEST'))
 store.db.execute("UPDATE events SET sha256='broken'");store.db.commit()
 with pytest.raises(ValueError,match='chain'):store.events()
def test_market_cannot_satisfy_science(inputs):
 p,c,*_=inputs
 for s in p['sources']:
  if s['kind']=='market':s.update(reviewed_by='TEST',review_note='Verified announcement only')
 result=evaluate(PortfolioPlan.model_validate(p),c)
 assert all(x['reviewed_support_families']==0 for x in result['items'])
def test_synthetic_and_unknown_direction(inputs):
 p,c,*_=inputs;p['evidence_kind']='synthetic';p['hypotheses'][0]['direction']='unknown'
 codes={x['code'] for x in evaluate(PortfolioPlan.model_validate(p),c)['items'][0]['gaps']}
 assert {'synthetic_only','intervention_direction'}<=codes
def test_manifest_forgery_fails_anchor(store,inputs):
 sid=build(store,inputs);path=store.path(sid);(path/'index.html').write_text('forged')
 m=json.loads((path/'manifest.json').read_text());m['files']['index.html']={'sha256':digest(path/'index.html'),'size_bytes':6};(path/'manifest.json').write_text(json.dumps(m))
 assert not store.verify(sid)['integrity_ok']
def test_same_family_deduplicated(inputs):
 p,c,*_=inputs;extra=copy.deepcopy(p['sources'][-2]);extra['evidence_id']='OSM_DUP';p['sources'].append(extra);p['hypotheses'][0]['links'].append({'evidence_id':'OSM_DUP','stance':'support','interpretation':'Same original study republished'})
 result=evaluate(PortfolioPlan.model_validate(p),c)
 assert result['items'][0]['independent_source_families']==3
