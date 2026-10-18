"""Synthetic stdlib-only safety QA, not real backup or paper validation."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(REPO/'src'))
spec=importlib.util.spec_from_file_location('restore_verifier',REPO/'scripts/verify_market_restore.py')
v=importlib.util.module_from_spec(spec);spec.loader.exec_module(v)
from vbt import official_disclosure_intake as official,identity_view

class RestoreGuards(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='emeris-restore-guards-')
  self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.w=self.root/'workspace';self.s=self.root/'state'
  (self.w/'artifacts/market').mkdir(parents=True);self.s.mkdir()
  self.artifact=self.w/'artifacts/market/synthetic.txt';self.artifact.write_bytes(b'SYNTHETIC RESTORE QA ONLY')
  self.m=self.root/'manifest.json';self.out=self.root/'verification.json'
  self.ids=['a'*32,'b'*32];self.run='c'*32
  with sqlite3.connect(self.s/'market.sqlite3') as db:
   for name in sorted(v.CORE_TABLES):
    if name=='official_disclosure_versions':
     db.execute('CREATE TABLE official_disclosure_versions(source_id,capture_id)')
     for source,ident in zip(sorted(v.OFFICIAL),self.ids):db.execute('INSERT INTO official_disclosure_versions VALUES (?,?)',(source,ident))
    elif name=='identity_collection_runs':
     db.execute('CREATE TABLE identity_collection_runs(id,status,evidence_sha256)')
     db.execute('INSERT INTO identity_collection_runs VALUES (?,?,?)',(self.run,'succeeded','d'*64))
    else:db.execute('CREATE TABLE "'+name+'"(id)')
  for name in ('workbench.sqlite3','registry.sqlite3','run-records.sqlite3'):
   with sqlite3.connect(self.s/name) as db:
    db.execute('CREATE TABLE synthetic(id)');db.execute('INSERT INTO synthetic VALUES (1)')
  self.contract={'source_commit':'9ed49b3','official_disclosure_captures':self.ids,'identity_runs':[self.run]}
  self.contract['table_counts']={}
  for path in self.s.iterdir():
   with sqlite3.connect(path) as db:
    tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    self.contract['table_counts'][path.name]={t:db.execute('SELECT count(*) FROM "'+t+'"').fetchone()[0] for t in tables}
  self.refresh_manifest()
  def capture(m,ident):
   with m.db() as db:
    with self.assertRaises(sqlite3.OperationalError):db.execute('CREATE TABLE forbidden_write(x)')
   return {'integrity_status':'verified','capture_id':ident,'source_id':dict(zip(self.ids,sorted(v.OFFICIAL)))[ident],'count':10}
  self.capture_patch=patch.object(official,'verify_capture',side_effect=capture)
  self.identity_patch=patch.object(identity_view,'evidence_file',return_value=(b'SYNTHETIC ATTACHMENT ONLY','text/plain'))
  self.capture=self.capture_patch.start();self.identity=self.identity_patch.start()
  self.addCleanup(self.capture_patch.stop);self.addCleanup(self.identity_patch.stop)
 def refresh_manifest(self):
  for key,root in [('workspace_files',self.w),('state_files',self.s)]:
   self.contract[key]={p.relative_to(root).as_posix():{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in root.rglob('*') if p.is_file()}
  self.m.write_text(json.dumps(self.contract))
 def verify(self):return v.verify(self.w,self.s,self.m)
 def assertFailed(self,code=None):
  result=self.verify();self.assertEqual(result['result'],'failed')
  if code:self.assertIn(code,[r['code'] for r in result['errors']])
  return result
 def test_complete_fixture_readonly_four_sqlite_and_all_attachments(self):
  before={p:(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_ctime_ns) for root in (self.w,self.s) for p in root.rglob('*') if p.is_file()}
  result=self.verify();self.assertEqual(result['result'],'passed',result)
  self.assertEqual(len(result['databases']),4);self.assertEqual(self.identity.call_count,3);self.assertEqual(self.capture.call_count,2)
  self.assertEqual(result['files_verified'],result['files_reverified'])
  self.assertFalse(result['offsite_restore_verified'])
  self.assertEqual(before,{p:(p.read_bytes(),p.stat().st_mtime_ns,p.stat().st_ctime_ns) for p in before})
 def test_missing_file_is_not_zero(self):self.artifact.unlink();self.assertFailed('missing_or_unreadable_path')
 def test_tampered_file(self):self.artifact.write_bytes(b'TAMPERED');self.assertFailed('file_size_or_hash_mismatch')
 def test_symlink_file(self):
  target=self.root/'outside';self.artifact.rename(target);self.artifact.symlink_to(target);self.assertFailed()
 def test_symlink_directory(self):
  (self.w/'alias').symlink_to(self.root,target_is_directory=True);self.assertFailed('symbolic_link_rejected')
 def test_hardlink_file(self):os.link(self.artifact,self.root/'hardlinked-copy');self.assertFailed('independent_regular_file_required')
 def test_fifo_is_not_read_or_blocked(self):os.mkfifo(self.w/'pipe');self.assertFailed('independent_regular_file_required')
 def test_unlisted_artifact(self):(self.w/'artifacts/unlisted').write_bytes(b'QA');self.assertFailed('unmanifested_state_or_artifact_files')
 def test_unlisted_state(self):(self.s/'unlisted').write_bytes(b'QA');self.assertFailed('unmanifested_state_or_artifact_files')
 def test_workspace_source_does_not_need_manifest(self):
  (self.w/'src').mkdir();(self.w/'src/optional.py').write_text('# optional code, not loaded')
  self.assertEqual(self.verify()['result'],'passed')
 def test_parent_traversal_contract(self):
  self.contract['workspace_files']['../outside']={'bytes':0,'sha256':'d'*64};self.m.write_text(json.dumps(self.contract));self.assertFailed('invalid_relative_manifest_path')
 def test_duplicate_json_keys(self):
  self.m.write_text('{"state_files":{},"state_files":{}}');self.assertFailed('duplicate_manifest_json_key')
 def test_missing_table_cannot_be_reported_zero(self):
  self.contract['table_counts']['market.sqlite3']['missing_table']=0;self.m.write_text(json.dumps(self.contract));self.assertFailed('expected_sqlite_table_missing')
 def test_table_count_mismatch(self):
  self.contract['table_counts']['registry.sqlite3']['synthetic']=0;self.m.write_text(json.dumps(self.contract));self.assertFailed('sqlite_table_count_mismatch')
 def test_critical_table_count_contract_missing(self):
  self.contract['table_counts']['market.sqlite3'].pop('trials');self.m.write_text(json.dumps(self.contract));self.assertFailed('critical_market_table_counts_missing')
 def test_sql_identifier_injection_rejected(self):
  self.contract['table_counts']['market.sqlite3']['x";DROP TABLE trials']=0;self.m.write_text(json.dumps(self.contract));self.assertFailed('invalid_table_count_contract')
 def test_corrupt_sqlite_even_with_matching_file_hash(self):
  path=self.s/'registry.sqlite3';raw=bytearray(path.read_bytes());raw[100:125]=b'X'*25;path.write_bytes(raw);self.refresh_manifest();self.assertFailed()
 def test_nonempty_wal_rejected_not_ignored(self):
  (self.s/'market.sqlite3-wal').write_bytes(b'SYNTHETIC WAL');self.refresh_manifest();self.assertFailed('nonempty_sqlite_sidecar_requires_cold_checkpoint')
 def test_empty_sidecars_are_untouched(self):
  for suffix in ('-wal','-shm'):(self.s/('market.sqlite3'+suffix)).write_bytes(b'')
  self.refresh_manifest();before={p:p.stat().st_mtime_ns for p in self.s.iterdir()}
  self.assertEqual(self.verify()['result'],'passed');self.assertEqual(before,{p:p.stat().st_mtime_ns for p in before})
 def test_success_identity_without_valid_sha_is_integrity_gap(self):
  with sqlite3.connect(self.s/'market.sqlite3') as db:db.execute('UPDATE identity_collection_runs SET evidence_sha256=NULL')
  self.contract['identity_runs']=[];self.refresh_manifest();self.assertFailed('successful_identity_run_missing_valid_proof')
 def test_success_identity_cannot_be_omitted(self):
  self.contract['identity_runs']=[];self.m.write_text(json.dumps(self.contract));self.assertFailed('successful_identity_run_inventory_mismatch')
 def test_official_capture_cannot_be_substituted(self):
  self.contract['official_disclosure_captures']=['e'*32,'f'*32];self.m.write_text(json.dumps(self.contract));self.assertFailed('official_capture_inventory_mismatch')
 def test_official_verifier_block_is_not_accepted(self):
  self.capture.side_effect=lambda m,ident:{'integrity_status':'blocked','errors':['password=SYNTHETIC_SECRET']}
  result=self.assertFailed('official_capture_integrity_failed');self.assertNotIn('SYNTHETIC_SECRET',json.dumps(result))
 def test_identity_attachment_failure_is_redacted(self):
  self.identity.side_effect=ValueError('password=SYNTHETIC_SECRET /Users/private/path')
  result=self.assertFailed('identity_attachment_integrity_failed');text=json.dumps(result)
  self.assertNotIn('SYNTHETIC_SECRET',text);self.assertNotIn('/Users/',text)
 def test_real_evidence_readers_fail_closed_on_missing_synthetic_archives(self):
  self.capture_patch.stop();self.identity_patch.stop()
  before={p:p.read_bytes() for root in (self.w,self.s) for p in root.rglob('*') if p.is_file()}
  result=self.assertFailed('official_capture_integrity_failed')
  self.assertIn('identity_attachment_integrity_failed',[e['code'] for e in result['errors']])
  self.assertEqual(before,{p:p.read_bytes() for p in before})
 def test_external_io_rejected_before_actual_socket(self):
  self.capture.side_effect=lambda *a:v.socket.socket()
  self.assertFailed('verification_stage_failed')
 def test_input_change_during_callbacks_is_detected(self):
  def changed(*a):self.artifact.write_bytes(b'CHANGED DURING CALLBACK');return b'QA','text/plain'
  self.identity.side_effect=changed;self.assertFailed('input_changed_during_verification')
 def test_new_state_file_during_callbacks_is_detected(self):
  def changed(*a):(self.s/'unexpected').write_bytes(b'QA');return b'QA','text/plain'
  self.identity.side_effect=changed;self.assertFailed('input_tree_changed_during_verification')
 def test_manifest_hardlink_rejected(self):os.link(self.m,self.root/'manifest-copy');self.assertFailed('independent_regular_file_required')
 def test_cli_output_small_and_detailed_json_saved(self):
  out=io.StringIO()
  with contextlib.redirect_stdout(out):code=v.main(['--workspace',str(self.w),'--state-dir',str(self.s),'--manifest',str(self.m),'--output',str(self.out)])
  self.assertEqual(code,0);self.assertLess(len(out.getvalue()),500);self.assertNotIn(str(self.root),out.getvalue())
  self.assertEqual(json.loads(self.out.read_text())['result'],'passed')
 def test_output_cannot_overwrite_an_input(self):
  before=self.artifact.read_bytes()
  with contextlib.redirect_stdout(io.StringIO()):code=v.main(['--workspace',str(self.w),'--state-dir',str(self.s),'--manifest',str(self.m),'--output',str(self.artifact)])
  self.assertEqual(code,2);self.assertEqual(self.artifact.read_bytes(),before)

if __name__ == '__main__':
 unittest.main(verbosity=1)
