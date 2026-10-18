#!/usr/bin/env python3
"""Verify a local cold restore without restoring, collecting, or initializing it.

Required manifest fields are workspace_files and state_files (POSIX relative
path -> {bytes, sha256}), table_counts (state DB path -> {table: count}),
official_disclosure_captures (the two sealed capture IDs), and identity_runs
(all succeeded identity runs with a valid proof digest). source_commit is
reported as supplied metadata, never asserted to be the verifier's checkout.

Workspace artifacts must be inventoried completely; workspace source code need
not be listed. Every state file must be inventoried. All directory/file links,
nonregular files and files with nlink != 1 are rejected, including unlisted ones.
Cold SQLite copies must not have nonempty WAL or rollback-journal sidecars.
Reads use mode=ro&immutable=1 so even empty sidecars are not touched.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile


sys.dont_write_bytecode = True
MAX_MANIFEST = 32 * 1024**2
MAX_FILES = 100_000
HEX = re.compile(r'[a-f0-9]{64}')
RUN = re.compile(r'[a-f0-9]{32}')
TABLE = re.compile(r'[A-Za-z_][A-Za-z0-9_]*')
OFFICIAL = {'ascentage_annual_pipeline_2025', 'carsgen_annual_pipeline_2024'}
CORE_TABLES = {'companies', 'listings', 'trials', 'changes', 'runs',
               'identity_collection_runs', 'sponsor_proposals', 'sponsor_proposal_reviews',
               'sponsor_review_evidence', 'disclosure_captures', 'disclosure_observations',
               'official_disclosure_versions'}


class Rejected(ValueError):
    def __init__(self, code, reference=''):
        self.code, self.reference = code, reference
        super().__init__(code)


def _error(report, code, reference='', exception=None):
    report['error_count'] += 1
    if len(report['errors']) < 1000:
        entry = {'code': code}
        if reference:
            entry['reference'] = reference
        if exception is not None:
            entry['exception_type'] = type(exception).__name__
        report['errors'].append(entry)


def _absolute(value):
    path = Path(value).expanduser().absolute()
    if path != path.resolve() or '..' in path.parts:
        raise Rejected('path_alias_or_parent_traversal')
    return path


def _node(path, directory=False):
    # Check all ancestors as well as the final node, rather than resolve away
    # an alias before validating the supplied path.
    for node in reversed((path, *path.parents)):
        try:
            info = node.lstat()
        except OSError as exc:
            raise Rejected('missing_or_unreadable_path') from exc
        last = node == path
        if stat.S_ISLNK(info.st_mode):
            raise Rejected('symbolic_link_rejected')
        if not last or directory:
            if not stat.S_ISDIR(info.st_mode):
                raise Rejected('directory_required')
        elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise Rejected('independent_regular_file_required')
    return info


def _relative(value):
    if (not isinstance(value, str) or not value or len(value) > 4096 or '\\' in value
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or any(p in ('', '.', '..') for p in value.split('/'))
            or PurePosixPath(value).is_absolute() or re.match(r'^[A-Za-z]:', value)):
        raise Rejected('invalid_relative_manifest_path')
    return value


def _fingerprint(path, *, maximum=None, body=False):
    before = _node(path)
    if maximum is not None and before.st_size > maximum:
        raise Rejected('file_exceeds_bounded_read')
    flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
    descriptor = os.open(path, flags)
    digest, size, pieces = hashlib.sha256(), 0, []
    try:
        start = os.fstat(descriptor)
        if (start.st_dev, start.st_ino) != (before.st_dev, before.st_ino) or start.st_nlink != 1:
            raise Rejected('file_changed_during_open')
        with os.fdopen(descriptor, 'rb', closefd=False) as handle:
            for chunk in iter(lambda: handle.read(1024**2), b''):
                size += len(chunk)
                if maximum is not None and size > maximum:
                    raise Rejected('file_exceeds_bounded_read')
                digest.update(chunk)
                if body:
                    pieces.append(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    final = _node(path)
    signature = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns, s.st_nlink)
    if signature(before) != signature(start) or signature(start) != signature(after) or signature(after) != signature(final) or size != after.st_size:
        raise Rejected('file_changed_during_read')
    return {'bytes': size, 'sha256': digest.hexdigest()}, b''.join(pieces) if body else None


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Rejected('duplicate_manifest_json_key')
        result[key] = value
    return result


def _manifest(path):
    fingerprint, raw = _fingerprint(path, maximum=MAX_MANIFEST, body=True)
    try:
        value = json.loads(raw, object_pairs_hook=_unique,
                           parse_constant=lambda _: (_ for _ in ()).throw(Rejected('nonfinite_manifest_number')))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise Rejected('invalid_manifest_json') from exc
    if not isinstance(value, dict):
        raise Rejected('manifest_object_required')
    for key in ('workspace_files', 'state_files'):
        files = value.get(key)
        if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
            raise Rejected('nonempty_bounded_file_manifest_required', key)
        for name, entry in files.items():
            _relative(name)
            if (not isinstance(entry, dict) or type(entry.get('bytes')) is not int or entry['bytes'] < 0
                    or not isinstance(entry.get('sha256'), str) or not HEX.fullmatch(entry['sha256'])):
                raise Rejected('invalid_file_manifest_entry', key)
    counts = value.get('table_counts')
    if not isinstance(counts, dict) or not counts or len(counts) > 100:
        raise Rejected('database_table_counts_required')
    for dbname, tables in counts.items():
        _relative(dbname)
        if dbname not in value['state_files'] or not isinstance(tables, dict) or not tables:
            raise Rejected('database_counts_not_bound_to_manifest', dbname)
        if any(not isinstance(t, str) or not TABLE.fullmatch(t) or type(n) is not int or n < 0 for t, n in tables.items()):
            raise Rejected('invalid_table_count_contract', dbname)
    for key in ('official_disclosure_captures', 'identity_runs'):
        ids = value.get(key)
        if (not isinstance(ids, list) or len(ids) > MAX_FILES or len(set(ids)) != len(ids)
                or any(not isinstance(i, str) or not RUN.fullmatch(i) for i in ids)):
            raise Rejected('invalid_evidence_identifier_list', key)
    if len(value['official_disclosure_captures']) != 2:
        raise Rejected('two_official_captures_required')
    commit = value.get('source_commit')
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r'[a-f0-9]{7,40}', commit)):
        raise Rejected('invalid_source_commit_metadata')
    return value, fingerprint


def _tree(root):
    _node(root, directory=True)
    files = set()
    def unreadable(error):
        raise Rejected('unreadable_directory_in_tree') from error
    for folder, directories, names in os.walk(root, followlinks=False, onerror=unreadable):
        for name in directories:
            _node(Path(folder) / name, directory=True)
        for name in names:
            path = Path(folder) / name
            _node(path)
            files.add(path.relative_to(root).as_posix())
            if len(files) > MAX_FILES:
                raise Rejected('tree_exceeds_file_limit')
    return files


@contextmanager
def _connection(path):
    _node(path)
    connection = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('PRAGMA trusted_schema=OFF')
        yield connection
    finally:
        connection.close()


class ReadOnlyMarket:
    """Deliberately does not inherit Market or execute storage initialization."""
    def __init__(self, workspace, state):
        self.root, self.state = workspace / 'artifacts' / 'market', state
        self.path = state / 'market.sqlite3'

    def db(self):
        return _connection(self.path)


@contextmanager
def _no_external_io():
    def rejected(*args, **kwargs):
        raise RuntimeError('external_io_forbidden_in_restore_verifier')
    targets = [(socket, 'socket'), (socket, 'create_connection'), (socket, 'getaddrinfo'), (subprocess, 'Popen')]
    saved = [(owner, name, getattr(owner, name)) for owner, name in targets]
    try:
        for owner, name, _ in saved:
            setattr(owner, name, rejected)
        yield
    finally:
        for owner, name, original in saved:
            setattr(owner, name, original)


def _evidence(market, manifest, report):
    # Imports are local to the evidence stage. No collector, schema initializer,
    # recover function or application constructor is called.
    from vbt.official_disclosure_intake import verify_capture
    from vbt.identity_view import evidence_file
    with market.db() as db:
        sealed = [dict(r) for r in db.execute('SELECT source_id,capture_id FROM official_disclosure_versions')]
        successful = [dict(r) for r in db.execute("SELECT id,evidence_sha256 FROM identity_collection_runs WHERE status='succeeded'")]
    sealed_ids = {r['capture_id'] for r in sealed}
    if (sealed_ids != set(manifest['official_disclosure_captures']) or len(sealed) != 2
            or {r['source_id'] for r in sealed} != OFFICIAL):
        _error(report, 'official_capture_inventory_mismatch')
    valid = {r['id'] for r in successful if isinstance(r['id'], str) and RUN.fullmatch(r['id'])
             and isinstance(r['evidence_sha256'], str) and HEX.fullmatch(r['evidence_sha256'])}
    if len(valid) != len(successful):
        _error(report, 'successful_identity_run_missing_valid_proof')
    if valid != set(manifest['identity_runs']):
        _error(report, 'successful_identity_run_inventory_mismatch')
    if report['error_count']:
        return
    with _no_external_io():
        for ident in manifest['official_disclosure_captures']:
            result = verify_capture(market, ident)
            ok = isinstance(result, dict) and result.get('integrity_status') == 'verified' and result.get('capture_id') == ident
            report['official_captures'].append({'capture_id': ident, 'verified': ok,
                **({'source_id': result.get('source_id'), 'count': result.get('count')} if ok else {})})
            if not ok:
                _error(report, 'official_capture_integrity_failed', ident)
        for ident in manifest['identity_runs']:
            attachments = []
            for kind in ('proof', 'exchange', 'registry'):
                try:
                    raw, mime = evidence_file(market, ident, kind)
                    if not isinstance(raw, bytes) or not raw or not isinstance(mime, str):
                        raise Rejected('empty_or_invalid_identity_attachment')
                    attachments.append({'kind': kind, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()})
                except Exception as exc:
                    _error(report, 'identity_attachment_integrity_failed', ident + ':' + kind, exc)
            report['identity_runs'].append({'run_id': ident, 'verified': len(attachments) == 3, 'attachments': attachments})


def verify(workspace, state, manifest_path):
    report = {'schema_version': 'market-local-restore-verification-v1', 'result': 'failed',
              'acceptance_scope': 'local_restore_acceptance_only', 'offsite_restore_verified': False,
              'created_at': datetime.now(timezone.utc).isoformat(), 'errors': [], 'error_count': 0,
              'files_verified': 0, 'files_reverified': 0, 'databases': [], 'official_captures': [], 'identity_runs': [],
              'sqlite_access': 'mode=ro&immutable=1; query_only=ON', 'network_and_subprocess_forbidden': True}
    manifest, original_manifest, roots = None, None, None
    try:
        workspace, state, manifest_path = map(_absolute, (workspace, state, manifest_path))
        if workspace.is_relative_to(state) or state.is_relative_to(workspace):
            raise Rejected('workspace_and_state_must_be_separate')
        _node(workspace, directory=True); _node(state, directory=True)
        manifest, original_manifest = _manifest(manifest_path)
        report['manifest_sha256'] = original_manifest['sha256']
        report['source_commit_from_manifest'] = manifest.get('source_commit')
        roots = {'workspace_files': workspace, 'state_files': state}
        for key, root in roots.items():
            actual = _tree(root)
            expected = set(manifest[key])
            extra = actual - expected if key == 'state_files' else {p for p in actual - expected if p.split('/')[0] == 'artifacts'}
            if extra:
                _error(report, 'unmanifested_state_or_artifact_files', key)
            for relative, entry in manifest[key].items():
                reference = key + ':' + relative
                try:
                    observed, _ = _fingerprint(root / relative)
                    if observed != {'bytes': entry['bytes'], 'sha256': entry['sha256']}:
                        raise Rejected('file_size_or_hash_mismatch')
                    report['files_verified'] += 1
                except Exception as exc:
                    _error(report, exc.code if isinstance(exc, Rejected) else 'file_read_failed', reference, exc)
        if not report['error_count']:
            databases = set()
            for relative, entry in manifest['state_files'].items():
                path = state / relative
                if relative.endswith(('-wal', '-journal')) and entry['bytes']:
                    raise Rejected('nonempty_sqlite_sidecar_requires_cold_checkpoint', 'state_files:' + relative)
                with path.open('rb') as handle:
                    is_sqlite = handle.read(16) == b'SQLite format 3\x00'
                if is_sqlite or relative.endswith(('.sqlite3', '.sqlite', '.db')):
                    databases.add(relative)
            if databases != set(manifest['table_counts']) or 'market.sqlite3' not in databases:
                raise Rejected('sqlite_inventory_or_count_contract_mismatch')
            if not CORE_TABLES.issubset(manifest['table_counts']['market.sqlite3']):
                raise Rejected('critical_market_table_counts_missing')
            for relative, tables in manifest['table_counts'].items():
                result = {'database': relative, 'quick_check_ok': False, 'table_counts': {}}
                report['databases'].append(result)
                with _connection(state / relative) as db:
                    checked = [r[0] for r in db.execute('PRAGMA quick_check')]
                    if checked != ['ok']:
                        raise Rejected('sqlite_quick_check_failed', relative)
                    result['quick_check_ok'] = True
                    actual_tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    for name, expected in tables.items():
                        if name not in actual_tables:
                            raise Rejected('expected_sqlite_table_missing', relative + ':' + name)
                        count = db.execute('SELECT count(*) FROM "' + name + '"').fetchone()[0]
                        result['table_counts'][name] = {'expected': expected, 'actual': count}
                        if count != expected:
                            raise Rejected('sqlite_table_count_mismatch', relative + ':' + name)
            _evidence(ReadOnlyMarket(workspace, state), manifest, report)
    except Exception as exc:
        _error(report, exc.code if isinstance(exc, Rejected) else 'verification_stage_failed',
               exc.reference if isinstance(exc, Rejected) else '', exc)
    finally:
        if manifest is not None and roots is not None:
            for key, root in roots.items():
                try:
                    actual = _tree(root)
                    extra = actual - set(manifest[key])
                    if key == 'workspace_files':
                        extra = {p for p in extra if p.split('/')[0] == 'artifacts'}
                    if extra:
                        raise Rejected('input_tree_changed_during_verification', key)
                    for relative, entry in manifest[key].items():
                        observed, _ = _fingerprint(root / relative)
                        if observed != {'bytes': entry['bytes'], 'sha256': entry['sha256']}:
                            raise Rejected('input_changed_during_verification', key + ':' + relative)
                        report['files_reverified'] += 1
                except Exception as exc:
                    _error(report, exc.code if isinstance(exc, Rejected) else 'post_verification_read_failed',
                           exc.reference if isinstance(exc, Rejected) else '', exc)
        if original_manifest is not None:
            try:
                if _fingerprint(manifest_path, maximum=MAX_MANIFEST)[0] != original_manifest:
                    raise Rejected('manifest_changed_during_verification')
            except Exception as exc:
                _error(report, 'manifest_changed_during_verification', exception=exc)
    report['result'] = 'passed' if not report['error_count'] else 'failed'
    return report


def _output(path, report, protected):
    path = _absolute(path)
    if any(path == item or path.is_relative_to(item) for item in protected):
        raise Rejected('output_must_not_overwrite_inputs')
    path.parent.mkdir(parents=True, exist_ok=True)
    _node(path.parent, directory=True)
    if path.exists() or path.is_symlink():
        _node(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.restore-verify-', dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n'); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('workspace', 'state-dir', 'manifest', 'output'):
        parser.add_argument('--' + name, required=True, type=Path)
    args = parser.parse_args(argv)
    # Source modules come from this verifier's repository, not from a restored
    # workspace's optional/untrusted source directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    report = verify(args.workspace, args.state_dir, args.manifest)
    protected = [p.expanduser().absolute() for p in (args.workspace, args.state_dir, args.manifest)]
    protected += [p.resolve() for p in protected]
    try:
        _output(args.output, report, protected)
    except Exception:
        print(json.dumps({'result': 'failed', 'error': 'verification_report_output_rejected'}))
        return 2
    print(json.dumps({'result': report['result'], 'scope': report['acceptance_scope'],
        'files_verified': report['files_verified'], 'databases_checked': len(report['databases']),
        'official_captures_verified': sum(r['verified'] for r in report['official_captures']),
        'identity_runs_verified': sum(r['verified'] for r in report['identity_runs']), 'error_count': report['error_count']}))
    return 0 if report['result'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
