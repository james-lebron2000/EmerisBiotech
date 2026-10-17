import json,threading,urllib.request
from urllib.parse import urlencode
from http.server import ThreadingHTTPServer
from vbt.workbench import Workbench,handler,page
from vbt.market import Market
from vbt.market_expansion import expand
from vbt.market_sources import parse_trials,trial_url


def test_expansion_is_repeatable_and_does_not_overwrite_mapping(tmp_path,monkeypatch):
 from vbt import market_expansion as module
 from vbt.issuer_discovery import initial_mappings
 m=Market(tmp_path/'w',tmp_path/'s');seed=initial_mappings()[0]
 with m.db() as db:db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',('p','US','NYSE','PFE','Pfizer Inc.','','USOTHER','2026-10-17T00:00:00+00:00'))
 monkeypatch.setattr(module,'initial_mappings',lambda:[seed])
 assert expand(m)['added']==['US:PFE']
 assert expand(m)['unchanged']==['US:PFE']
 with m.db() as db:db.execute('UPDATE companies SET aliases=?',(json.dumps(['Existing reviewed alias']),))
 assert expand(m)['conflicts']==['US:PFE']
 assert json.loads(m.rows('companies')[0]['aliases'])==['Existing reviewed alias']


def test_arm_role_source_projection_and_parsing():
 p={'protocolSection':{'identificationModule':{'nctId':'NCT00000001'},'sponsorCollaboratorsModule':{'leadSponsor':{'name':'Synthetic'}},'armsInterventionsModule':{'armGroups':[{'label':'A','type':'EXPERIMENTAL'},{'label':'B','type':'ACTIVE_COMPARATOR'}],'interventions':[{'type':'DRUG','name':'Novel','armGroupLabels':['A'],'otherNames':['N-1']},{'type':'DRUG','name':'Standard','armGroupLabels':['B']}]}}}
 rows,_,_=parse_trials(json.dumps({'studies':[p]}).encode(),['Synthetic'])
 assert rows[0]['interventions'][0]['arm_roles']==['EXPERIMENTAL']
 assert rows[0]['interventions'][1]['arm_roles']==['ACTIVE_COMPARATOR']
 assert 'protocolSection.armsInterventionsModule' in trial_url('Synthetic')


def test_full_routes_and_review_workflow(tmp_path):
 app=Workbench(tmp_path/'w',tmp_path/'s');m=Market(app.workspace,app.state)
 with m.db() as db:db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',('p','US','NYSE','PFE','Pfizer Inc.','','USOTHER','2026-10-17T00:00:00+00:00'))
 expand(m)
 server=ThreadingHTTPServer(('127.0.0.1',0),handler(app));threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
 try:
  for route,expected in [('/coverage','覆盖'),('/market/progress','覆盖'),('/assets?company=US%3APFE','候选药物'),('/market','市场与管线雷达')]:
   assert expected in urllib.request.urlopen(base+route).read().decode()
  data=urlencode({'token':app.token,'candidate_id':'US:PFE','decision':'accepted','reviewer':'SYNTHETIC TEST','reason':'Synthetic review of classification only'}).encode()
  result=urllib.request.urlopen(urllib.request.Request(base+'/market/review',data=data,headers={'Origin':base}))
  assert '/coverage?' in result.url and len(m.rows('companies'))==1
  with m.db() as db:assert db.execute('SELECT review_status FROM issuer_candidates').fetchone()[0]=='accepted'
 finally:server.shutdown();server.server_close()


def test_company_display_name_is_searchable(tmp_path):
 m=Market(tmp_path/'w',tmp_path/'s');m.add_company('A','600276','恒瑞医药',['Synthetic'],'Synthetic fixture')
 with m.db() as db:db.execute('INSERT INTO trials VALUES (?,?,?,?,?)',('A:600276','NCT00000001',json.dumps({'interventions':[],'phases':['PHASE3'],'status':'RECRUITING','source_updated':'2026-10-17'}),'hash','2026-10-17'))
 assert m.search_trials(query='恒瑞',phase='PHASE3',status='RECRUITING')[1]==1


def test_china_expansion_current_identity_and_grouping(tmp_path):
 from vbt.china_issuer_sources import source_mappings
 from vbt.issuer_discovery import discover
 m=Market(tmp_path/'w',tmp_path/'s');seeds=source_mappings()[:2]
 with m.db() as db:
  for r in seeds:db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',(r['id'],r['market'],'SYNTHETIC',r['ticker'],r['listing_names'][0],'','SYNTHETIC','2026-10-17T00:00:00+00:00'))
  db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',('invalid','US','SYNTHETIC','TEST$A','Synthetic Preferred','','SYNTHETIC','2026-10-17T00:00:00+00:00'))
 rows=discover(m.rows('listings'));assert len(rows)==2
 assert len({r['issuer_group'] for r in rows})==1
 assert all(r['classification']=='drug_developer' for r in rows)
 assert set(expand(m)['added'])=={r['id'] for r in seeds}
 assert len(expand(m)['unchanged'])==2
 with m.db() as db:db.execute("UPDATE listings SET name='Unrelated Issuer' WHERE id=?",(seeds[0]['id'],))
 assert seeds[0]['id'] in expand(m)['missing_listing']
