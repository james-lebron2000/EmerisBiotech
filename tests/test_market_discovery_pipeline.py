"""Synthetic automatic discovery, never real-market coverage validation."""
from vbt.market import Market
from vbt.market_discovery_pipeline import tick
from vbt.market_jobs import jobs


def test_directory_expansion_and_hourly_batch_are_persistent_and_do_not_approve(tmp_path):
 m=Market(tmp_path/'w',tmp_path/'s')
 with m.db() as db:
  for ident,ticker,name in [('p','PFE','Pfizer Inc.'),('s','SYN','Synthetic Therapeutics')]:
   db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',(ident,'US','SYNTHETIC',ticker,name,'','USOTHER','2026-10-17T00:00:00+00:00'))
 first=tick(m)
 assert first['expanded'] and len(first['company_jobs'])==1 and len(first['resolution_jobs'])==1
 assert [r['id'] for r in m.rows('companies')]==['US:PFE']
 assert {j['kind'] for j in jobs(m)}=={'company','resolve'}
 restored=Market(tmp_path/'w',tmp_path/'s');second=tick(restored)
 assert not second['expanded'] and not second['resolution_jobs']
 with m.db() as db:
  assert db.execute('SELECT count(*) FROM sponsor_proposals').fetchone()[0]==0
  db.execute("UPDATE listings SET observed='2026-10-18T00:00:00+00:00'")
 assert tick(m)['expanded'] and len(m.rows('companies'))==1


def test_batch_has_fixed_limit_and_skips_already_observed_candidates(tmp_path):
 m=Market(tmp_path/'w',tmp_path/'s')
 with m.db() as db:
  for i in range(25):db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',(str(i),'US','SYNTHETIC','S'+str(i),'Synthetic '+str(i)+' Therapeutics','','USOTHER','2026-10-17T00:00:00+00:00'))
 assert len(tick(m)['resolution_jobs'])==20
 assert len(m.rows('companies'))==0


def test_identity_change_requeues_but_timestamp_only_refresh_does_not(tmp_path):
 import json
 from vbt.sponsor_resolution import resolve
 m=Market(tmp_path/'w',tmp_path/'s')
 with m.db() as db:db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',('s','US','SYNTHETIC','SYN','Synthetic Therapeutics','','USOTHER','2026-10-17T00:00:00+00:00'))
 tick(m);resolve(m,'US:SYN',fetch=lambda u:json.dumps({'studies':[]}).encode())
 # The injected direct resolver stands in for consuming the queued task.
 with m.db() as db:db.execute("UPDATE collection_jobs SET status='succeeded' WHERE kind='resolve'")
 with m.db() as db:
  original=db.execute('SELECT content_hash FROM issuer_candidates').fetchone()[0]
  db.execute("UPDATE listings SET observed='2026-10-18T00:00:00+00:00'")
  db.execute("UPDATE settings SET value='0' WHERE key='discovery.last_resolution_batch'")
 assert not tick(m)['resolution_jobs']
 with m.db() as db:
  assert db.execute('SELECT content_hash FROM issuer_candidates').fetchone()[0]==original
  db.execute("UPDATE listings SET name='Changed Therapeutics',observed='2026-10-19T00:00:00+00:00'")
  db.execute("UPDATE settings SET value='0' WHERE key='discovery.last_resolution_batch'")
 assert len(tick(m)['resolution_jobs'])==1
 with m.db() as db:assert db.execute('SELECT content_hash FROM issuer_candidates').fetchone()[0]!=original


def test_old_zero_result_can_be_revisited_after_thirty_days(tmp_path):
 import json
 from vbt.sponsor_resolution import resolve
 m=Market(tmp_path/'w',tmp_path/'s')
 with m.db() as db:db.execute('INSERT INTO listings VALUES (?,?,?,?,?,?,?,?)',('s','US','SYNTHETIC','SYN','Synthetic Therapeutics','','USOTHER','2026-10-17T00:00:00+00:00'))
 tick(m);resolve(m,'US:SYN',fetch=lambda u:json.dumps({'studies':[]}).encode())
 # The injected direct resolver stands in for consuming the queued task.
 with m.db() as db:db.execute("UPDATE collection_jobs SET status='succeeded' WHERE kind='resolve'")
 with m.db() as db:
  db.execute("UPDATE settings SET value='0' WHERE key='discovery.last_resolution_batch'")
 assert not tick(m)['resolution_jobs']
 with m.db() as db:
  db.execute("UPDATE sponsor_resolution_runs SET started='2000-01-01T00:00:00+00:00'")
  db.execute("UPDATE settings SET value='0' WHERE key='discovery.last_resolution_batch'")
 assert len(tick(m)['resolution_jobs'])==1
