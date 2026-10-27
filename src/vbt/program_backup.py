"""Versioned program-only snapshots and quarantined read-only restores.

No environment/configuration, locks, raw datasets, model calls or worker dispatch.
"""
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .programs import Programs, canonical

MARKER = 'PROGRAMS_RESTORE_READ_ONLY'
DB_LIMIT = 256 * 1024 * 1024
MANIFEST_LIMIT = 2 * 1024 * 1024
SOURCE_LIMIT = 4 * 1024 * 1024


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def regular(path, limit):
    path = Path(path)
    if not path.exists() and not path.is_symlink():
        raise FileNotFoundError('来源不存在：' + path.name)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError('不是允许大小的普通文件：' + path.name)
    with path.open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('文件超过大小限制：' + path.name)
    return raw


def json_bytes(raw):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise ValueError('清单包含重复字段')
            result[k] = v
        return result
    try:
        value = json.loads(raw, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError('非有限清单值')))
    except (UnicodeError, RecursionError, TypeError) as exc:
        raise ValueError('清单格式错误') from exc
    if not isinstance(value, dict):
        raise ValueError('清单须为 JSON 对象')
    return value


def state_inventory(state):
    """Read chains and exports without changing schema or reading bound inputs."""
    store = Programs(state, read_only=True)
    with store.db() as db:
        if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('项目 SQLite 完整性校验失败')
        schema = [tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
        tables = {r[1] for r in schema if r[0] == 'table'}
        if tables != {'program_events', 'sqlite_sequence'}:
            raise ValueError('项目数据库表结构未受此备份版本支持')
        columns = [r[1] for r in db.execute('PRAGMA table_info(program_events)')]
        if columns != ['seq','id','program','kind','created','author','payload','previous','digest']:
            raise ValueError('项目事件字段版本不受支持')
        names = {r[1] for r in schema}
        expected = {'program_events','sqlite_sequence','program_by_id','program_no_update','program_no_delete',
                    'sqlite_autoindex_program_events_1'}
        if names != expected or db.execute('PRAGMA user_version').fetchone()[0] != 0:
            raise ValueError('项目数据库结构版本不受支持')
        count = db.execute('SELECT count(*) FROM program_events').fetchone()[0]
        if count > 100000:
            raise ValueError('项目事件数超过首版备份上限')
        ids = [r[0] for r in db.execute('SELECT DISTINCT program FROM program_events ORDER BY program')]
        if len(ids) > 1000:
            raise ValueError('项目数超过首版备份上限')
        sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='program_events'").fetchone()
    programs = []
    for pid in ids:
        p = store.get(pid)
        events = p['events']; kinds = Counter(e['kind'] for e in events)
        if not events or events[0]['kind'] != 'created':
            raise ValueError('项目缺少初始定义')
        known = {e['id']:e['kind'] for e in events}
        by_id = {e['id']:e for e in events}
        current_seq = 0
        comparisons = {'difference:' + r['item'] for e in events if e['kind']=='comparison_import' for r in e['payload']['rows']}
        batches = {e['payload']['batch_id'] for e in events if e['kind']=='ai_batch'}
        references = 0
        def check(ref, allowed=None, scientific=False):
            nonlocal references
            if scientific and ref in comparisons:
                references += 1; return
            if scientific and isinstance(ref,str) and ref.startswith('assetclaim:'):
                ref=ref.removeprefix('assetclaim:');allowed={'asset_claim'}
            if not isinstance(ref,str) or ref not in known or (allowed and known[ref] not in allowed) or by_id[ref]['seq']>=current_seq:
                raise ValueError('项目包含悬空或错误类型的引用：' + str(ref))
            references += 1
        for e in events:
            data=e['payload'];kind=e['kind'];current_seq=e['seq']
            for field, allowed in [('evidence_ids',{'evidence'}),('result_ids',{'result'}),('asset_claim_ids',{'asset_claim'}),('claim_ids',{'asset_claim'})]:
                if field in data:
                    if not isinstance(data[field],list):raise ValueError('项目引用须为列表')
                    for ref in data[field]:check(ref,allowed)
            for field, allowed in [('evidence_id',{'evidence'}),('experiment_id',{'experiment'}),('result_id',{'result'}),
                                   ('asset_id',{'asset_import'}),('claim_id',{'asset_claim'}),('proposal_id',{'ai_proposal','ai_model_receipt'})]:
                if field in data:check(data[field],allowed)
            if kind in ('ai_model_receipt','ai_tool_receipt'):
                check(data['request_id'],{'ai_model_request'} if kind=='ai_model_receipt' else {'ai_tool_request'})
            if kind=='ai_task_retry':check(data['previous_request_id'],{'ai_tool_request'})
            if 'batch_id' in data and data['batch_id'] not in batches:
                raise ValueError('项目包含悬空批次引用')
            if kind=='decision':
                for ref in data.get('experiments',[]):check(ref,{'experiment'})
            if kind=='scientific_assessment':
                for ref in data['references']:check(ref,scientific=True)
                if data.get('comparison_id'):check(data['comparison_id'],{'comparison_import'})
            if kind=='asset_claim' and data.get('source'):
                reference=data['source']['reference']
                if not isinstance(reference,str) or ':' not in reference:raise ValueError('资产来源引用错误')
                prefix, ref=reference.split(':',1)
                if prefix not in ('asset','evidence'):raise ValueError('资产来源引用类型错误')
                check(ref,{'asset_import'} if prefix=='asset' else {'evidence'})
                if prefix=='asset' and ref!=data['asset_id']:
                    raise ValueError('资产资料引用了另一个资产的来源')
            draft=data if kind=='ai_proposal' else data.get('draft') if kind=='ai_model_receipt' else None
            if draft:
                for item in draft['proposal']['risks'] + draft['proposal']['tasks']:
                    for ref in item['references']:check(ref,scientific=True)
        pending = []
        for request_kind, receipt_kind in [('ai_model_request','ai_model_receipt'),('ai_tool_request','ai_tool_receipt')]:
            receipts={e['payload']['request_id'] for e in events if e['kind']==receipt_kind}
            pending += [dict(request_id=e['id'],kind=request_kind,status='unknown_no_receipt') for e in events if e['kind']==request_kind and e['id'] not in receipts]
        programs.append(dict(program_id=pid,events=len(events),revision=p['revision'],kinds=dict(sorted(kinds.items())),
                             export_sha256=digest(store.export(pid)),references_checked=references,
                             unresolved_requests=pending,human_identity='self_declared_not_authenticated'))
    return dict(schema_version=1,programs=programs,events=count,sequence=sequence[0] if sequence else 0,
                schema_sha256=digest(canonical(schema).encode()),
                scope='事件、引用与导出完整性；未检查来源内容、身份资格、科学或法律结论')


def write_new(path, raw):
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'wb') as stream:
        stream.write(raw);stream.flush();os.fsync(stream.fileno())


def snapshot(state, output, release):
    source=Path(state).resolve(); output=Path(output)
    if not re.fullmatch('[a-f0-9]{40}', release):raise ValueError('需填写固定的 40 位发布提交')
    if output.is_symlink() or output.exists():raise ValueError('备份目标必须是全新目录')
    output=output.resolve()
    if output.is_relative_to(source) or source.is_relative_to(output):raise ValueError('备份与源状态目录须独立')
    source_db=source/'programs.sqlite3'
    if source_db.is_symlink() or not source_db.is_file() or source_db.stat().st_size>DB_LIMIT:
        raise ValueError('源项目数据库不存在、不安全或超过大小限制')
    output.mkdir(mode=0o700)
    target=output/'programs.sqlite3'; started=time.monotonic()
    try:
        with closing(sqlite3.connect(source_db.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(target)) as dst:
            def progress(status,remaining,total):
                if time.monotonic()-started>60:raise ValueError('一致性备份超时；不生成完成清单')
            src.backup(dst,pages=128,progress=progress,sleep=.01)
            if dst.execute('PRAGMA journal_mode=DELETE').fetchone()[0]!='delete':
                raise ValueError('备份无法生成独立数据库文件')
        os.chmod(target,0o600)
        raw=regular(target,DB_LIMIT)
        inventory=state_inventory(output)
        manifest=dict(schema_version=1,kind='vbt_program_snapshot',created=datetime.now(timezone.utc).isoformat(),
                      release_ref=release,release_attestation='operator_supplied_not_cryptographically_attested',
                      database=dict(name='programs.sqlite3',bytes=len(raw),sha256=digest(raw)),inventory=inventory,
                      excludes=['host_configuration_and_secret_files','workbench_and_market_databases','external_bound_sources','worker_locks_and_processes'],
                      human_identity='historical_declarations_preserved_not_authenticated')
        raw_manifest=canonical(manifest).encode()
        if len(raw_manifest)>MANIFEST_LIMIT:
            raise ValueError('备份清单超过首版上限，不生成完成清单')
        write_new(output/'manifest.json',raw_manifest)
    except BaseException:
        # Incomplete output remains inspectable; an absent manifest never verifies.
        raise
    return manifest


def verify(snapshot_dir):
    root=Path(snapshot_dir)
    if root.is_symlink() or not root.is_dir():raise ValueError('备份目录不可用或为符号链接')
    root=root.resolve()
    if {p.name for p in root.iterdir()} != {'manifest.json','programs.sqlite3'}:
        raise ValueError('备份文件清单不符或备份尚未完成')
    manifest=json_bytes(regular(root/'manifest.json',MANIFEST_LIMIT))
    if manifest.get('schema_version')!=1 or manifest.get('kind')!='vbt_program_snapshot':raise ValueError('备份版本不受支持')
    bound=manifest.get('database')
    if not isinstance(bound,dict) or bound.get('name')!='programs.sqlite3':raise ValueError('备份数据库路径不符')
    raw=regular(root/'programs.sqlite3',DB_LIMIT)
    if len(raw)!=bound.get('bytes') or digest(raw)!=bound.get('sha256'):raise ValueError('备份数据库大小或哈希不符')
    inventory=state_inventory(root)
    if inventory!=manifest.get('inventory'):raise ValueError('备份事件、引用或导出清单不符')
    return manifest


def source_status(state):
    """Inspect bounded existing summary files, never fetch or rewrite sources."""
    store=Programs(state,read_only=True); rows=[]
    for p in store.list():
        for event in p['events']:
            data=event['payload'];kind=event['kind']; bindings=[]
            if kind in ('import','comparison_import'):
                root=Path(data['root']).resolve()
                for name, expected in data['files'].items():
                    relative=PurePosixPath(name)
                    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
                        bindings.append((name,None,expected));continue
                    path=(root/name).resolve()
                    bindings.append((name,path if path.is_relative_to(root) else None,expected))
                if data.get('manifest_sha256'):bindings.append(('ARTIFACT_MANIFEST.json',root/'ARTIFACT_MANIFEST.json',data['manifest_sha256']))
            elif kind=='asset_import':bindings=[('asset_snapshot',Path(data['source_path']),data['source_sha256'])]
            for name,path,expected in bindings:
                try:
                    if path is None:raise ValueError('unsafe binding')
                    raw=regular(path,SOURCE_LIMIT);status='verified' if digest(raw)==expected else 'changed'
                except FileNotFoundError:status='unavailable'
                except OSError:status='unavailable'
                except ValueError:status='unsafe_or_oversized'
                rows.append(dict(program_id=p['id'],event_id=event['id'],file=name,status=status,expected_sha256=expected))
    return dict(checked_at=datetime.now(timezone.utc).isoformat(),files=rows,
                complete=bool(rows) and all(r['status']=='verified' for r in rows),
                scope='仅现有绑定摘要文件的可读性和字节哈希；不认证网页现状或科学内容')


def restore(snapshot_dir, destination):
    manifest=verify(snapshot_dir);source=Path(snapshot_dir).resolve(); target=Path(destination)
    if target.exists() or target.is_symlink():raise ValueError('恢复仅允许全新目录，不能覆盖既有状态')
    target=target.resolve()
    if target.is_relative_to('/Volumes'):raise ValueError('恢复状态必须位于本机磁盘')
    if target.is_relative_to(source) or source.is_relative_to(target):raise ValueError('恢复与备份目录须独立')
    target.mkdir(mode=0o700)
    manifest_hash=digest(regular(source/'manifest.json',MANIFEST_LIMIT))
    write_new(target/MARKER,canonical(dict(schema_version=1,mode='read_only_restore',manifest_sha256=manifest_hash,
                                         activation='not_supported_pending_ownership_handoff')).encode())
    raw=regular(source/'programs.sqlite3',DB_LIMIT)
    if digest(raw)!=manifest['database']['sha256']:raise ValueError('复制前备份发生变化，恢复保持隔离')
    write_new(target/'programs.sqlite3',raw)
    inventory=state_inventory(target)
    if inventory!=manifest['inventory']:raise ValueError('恢复核对失败，目标保持隔离只读')
    report=dict(schema_version=1,restored_at=datetime.now(timezone.utc).isoformat(),manifest_sha256=manifest_hash,
                record_integrity='verified',mode='read_only_restore',inventory=inventory,
                source_integrity='not_checked',credentials='host_files_not_copied_record_text_not_scanned',workers='not_replayed',
                human_identity='historical_declarations_preserved_not_authenticated',
                scope='UC 项目记录恢复核对；非整工作台迁移、身份认证、生产或科学验收')
    write_new(target/'program_restore_receipt.json',canonical(report).encode())
    return report


def recovery_status(state, check_sources=False):
    root=Path(state).resolve()
    if not (root/MARKER).exists() and not (root/MARKER).is_symlink():
        raise ValueError('该状态不是只读恢复副本')
    marker=json_bytes(regular(root/MARKER,MANIFEST_LIMIT))
    if marker.get('mode')!='read_only_restore' or marker.get('schema_version')!=1:
        raise ValueError('恢复隔离标记格式错误；写入仍被拒绝')
    receipt_path=root/'program_restore_receipt.json'
    if not receipt_path.exists():
        return dict(mode='read_only_restore',record_integrity='incomplete_no_receipt',sources='not_checked',
                    workers='not_replayed',scope='恢复尚未完成，未授权写入或执行')
    receipt=json_bytes(regular(receipt_path,MANIFEST_LIMIT))
    if receipt.get('manifest_sha256')!=marker.get('manifest_sha256'):
        raise ValueError('恢复标记与回执不一致')
    current=state_inventory(root)
    return dict(mode='read_only_restore',record_integrity='verified' if current==receipt.get('inventory') else 'changed',
                manifest_sha256=marker['manifest_sha256'],inventory=current,
                sources=source_status(root) if check_sources else 'not_checked',workers='not_replayed',
                credentials='host_files_not_copied_record_text_not_scanned',
                scope='恢复核对不认证历史身份、科学结论、生产部署或跨实例执行所有权')
