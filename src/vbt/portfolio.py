"""Immutable portfolio packages and local anchored review records. No network calls."""
import base64, hashlib, json, re, shutil, sqlite3, subprocess, uuid
from pathlib import Path
from pydantic import TypeAdapter, HttpUrl
from .io import write, load, digest, now, within, ensure_storage
from .portfolio_models import PortfolioPlan, PortfolioReview

PACKAGE_FILES={'plan.json','catalog.json','evaluation.json','provenance.json','index.html','manifest.json','code/portfolio.py','code/portfolio_models.py','code/portfolio_report.py'}

def canonical(obj):
    return json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()

def evaluate(plan, catalog):
    if not isinstance(catalog,dict) or not isinstance(catalog.get('records'),list):raise ValueError('Invalid catalog')
    required=['id','company','asset','mechanism_modality','event','economics_usd','evidence_date','stage_at_cited_source','limitation','source_url']
    for row in catalog['records']:
        if not all(isinstance(row.get(k),str) and row[k].strip() for k in required):raise ValueError('Missing or invalid catalog fields')
        TypeAdapter(HttpUrl).validate_python(row['source_url'])
    ids=[r['id'] for r in catalog['records']]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate catalog asset IDs')
    sources={s.evidence_id:s for s in plan.sources};items=[]
    for h in plan.hypotheses:
        if not set(h.competitor_asset_ids)<=set(ids):raise ValueError('Unknown competitor asset reference')
        gaps=[];seen=set();support=set();opposition=set()
        def require(ok,code,note):
            if not ok:gaps.append({'code':code,'required':note})
        for link in h.links:
            s=sources[link.evidence_id];seen.add(s.family)
            scientific=s.kind!='market'
            if scientific and link.stance=='support' and s.reviewed_by and s.review_note:support.add(s.family)
            if link.stance=='oppose':
                opposition.add(s.family)
                require(bool(link.contradiction_response and link.response_reviewer),'unresolved_counterevidence:'+s.evidence_id,'记录如何处理反证及实际审查人；不能仅删除反证')
            if scientific:require(bool(s.reviewed_by and s.review_note),'source_review:'+s.evidence_id,'核对原始来源、主张范围并由实际研究人员记录审查')
        require(bool(support),'scientific_support','至少一份经人工核对的科学支持来源；交易不能替代科学证据')
        require(bool(h.counterevidence_search and h.counterevidence_reviewed_by),'counterevidence_search','记录反证检索范围、日期、结果和审查人；未找到与未检索分开')
        require(h.direction!='unknown','intervention_direction','明确干预方向')
        require(bool(h.human_owner),'hypothesis_owner','指派实际研究负责人')
        for field in ['sample_size','sample_size_basis','analysis_plan','randomization_blinding','meaningful_effect','protocol_ref','owner','material_access_ref','data_rights_ref','budget_amount','currency','budget_authorization_ref']:
            require(bool(getattr(h.experiment,field)),'experiment:'+field,'实验需要明确 '+field)
        if plan.evidence_kind=='synthetic':gaps.append({'code':'synthetic_only','required':'合成样例不可获得真实实验授权'})
        items.append({'hypothesis_id':h.hypothesis_id,'research_definition':'complete','experiment_gate':'blocked' if gaps else 'ready_for_human_review','scientific_review':'pending','independent_source_families':len(seen),'reviewed_support_families':len(support),'opposing_families':len(opposition),'gaps':gaps,'clinical_release':False})
    return {'schema_version':1,'policy_version':'portfolio-gates-v1','items':items,'clinical_release':False,'note':'Checks are structural readiness, not proof of scientific correctness. No automated experiment authorization.'}


def verify_package(path, anchor=None):
    path=Path(path);errors=[]
    try:
        m=load(path/'manifest.json')
        if anchor and digest(path/'manifest.json')!=anchor:raise ValueError('Manifest differs from trusted anchor')
        if m['snapshot_id']!=path.name:raise ValueError('Snapshot identity mismatch')
        actual={str(p.relative_to(path)) for p in path.rglob('*') if p.is_file()}
        if actual!=PACKAGE_FILES:raise ValueError('Unexpected or missing package files')
        if any(p.is_symlink() for p in path.rglob('*')):raise ValueError('Symlinks forbidden in sealed package')
        if set(m['files'])!=PACKAGE_FILES-{'manifest.json'}:raise ValueError('Invalid manifest inventory')
        for rel,meta in m['files'].items():
            p=within(path/rel,path)
            if digest(p)!=meta['sha256'] or p.stat().st_size!=meta['size_bytes']:raise ValueError('Changed artifact: '+rel)
        plan=PortfolioPlan.model_validate(load(path/'plan.json'))
        expected=evaluate(plan,load(path/'catalog.json'))
        if expected!=load(path/'evaluation.json'):raise ValueError('Gate evaluation differs from frozen plan')
    except Exception as e:errors.append(f'{type(e).__name__}: {e}')
    return {'snapshot_id':path.name,'integrity_ok':not errors,'anchor_checked':anchor is not None,'errors':errors,'scientific_correctness_verified':False}


class PortfolioStore:
    def __init__(self,workspace,state_dir=None):
        self.workspace=ensure_storage(workspace)
        tag=hashlib.sha256(str(self.workspace).encode()).hexdigest()[:12]
        self.state=Path(state_dir or Path.home()/'Library/Application Support/VirtualBiotech'/tag).resolve()
        if self.state.is_relative_to('/Volumes'):raise ValueError('Portfolio SQLite must be on local disk')
        self.state.mkdir(parents=True,exist_ok=True)
        self.root=self.workspace/'artifacts/portfolio'
        self.db=sqlite3.connect(self.state/'portfolio.sqlite3',timeout=30)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, manifest_sha256 TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS events (sequence INTEGER PRIMARY KEY, payload TEXT NOT NULL, sha256 TEXT NOT NULL)')
        self.db.commit()
    def close(self):self.db.close()
    def path(self,snapshot):
        if not re.fullmatch(r'[A-Za-z0-9_+.-]+',snapshot):raise ValueError('Invalid snapshot ID')
        return within(self.root/'snapshots'/snapshot,self.root/'snapshots')
    def anchor(self,snapshot):
        row=self.db.execute('SELECT manifest_sha256 FROM snapshots WHERE id=?',(snapshot,)).fetchone()
        if not row:raise ValueError('Unregistered snapshot')
        return row[0]
    def verify(self,snapshot):return verify_package(self.path(snapshot),self.anchor(snapshot))
    def require_valid(self,snapshot):
        v=self.verify(snapshot)
        if not v['integrity_ok']:raise ValueError(str(v['errors']))
        return self.path(snapshot)
    def events(self):
        out=[];previous='0'*64
        for seq,payload,sha in self.db.execute('SELECT * FROM events ORDER BY sequence'):
            p=json.loads(payload)
            if p['sequence']!=len(out)+1 or p['previous_sha256']!=previous or hashlib.sha256(canonical(p)).hexdigest()!=sha:raise ValueError('Review event chain altered')
            out.append({'payload':p,'sha256':sha});previous=sha
        return out
    def status(self):
        return {'snapshots':[{'snapshot_id':i,'manifest_sha256':h} for i,h in self.db.execute('SELECT * FROM snapshots ORDER BY id')],'review_events':self.events(),'unfinished_builds':[p.name for p in (self.root/'staging').glob('*') if p.is_dir()]}
    def build(self,plan_path,catalog_path,parent=None):
        # Capture bytes once; hashes identify the exact copies parsed, not a later reread.
        raw_plan=Path(plan_path).read_bytes();raw_catalog=Path(catalog_path).read_bytes()
        plan=PortfolioPlan.model_validate_json(raw_plan);catalog=json.loads(raw_catalog)
        if parent:
            old=PortfolioPlan.model_validate(load(self.require_valid(parent)/'plan.json'))
            fresh={h.hypothesis_id:h for h in plan.hypotheses}
            for h in old.hypotheses:
                if h.hypothesis_id in fresh:
                    prior={x.evidence_id for x in h.links if x.stance=='oppose'}
                    current={x.evidence_id for x in fresh[h.hypothesis_id].links if x.stance=='oppose'}
                    if not prior<=current:raise ValueError('Version cannot silently drop or relabel counterevidence')
        evaluation=evaluate(plan,catalog)
        sid=now().replace(':','').replace('.','-')+'-'+uuid.uuid4().hex[:8]
        stage=ensure_storage(self.root/'staging'/sid);stage.mkdir(parents=True)
        try:
            write(stage/'plan.json',plan.model_dump(mode='json'));write(stage/'catalog.json',catalog)
            write(stage/'evaluation.json',evaluation)
            try:commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=self.workspace,text=True,stderr=subprocess.DEVNULL).strip()
            except (OSError,subprocess.CalledProcessError):commit='unavailable'
            write(stage/'provenance.json',{'snapshot_id':sid,'created_at':now(),'parent_snapshot':parent,'plan_path':str(Path(plan_path).resolve()),'plan_sha256':hashlib.sha256(raw_plan).hexdigest(),'catalog_path':str(Path(catalog_path).resolve()),'catalog_sha256':hashlib.sha256(raw_catalog).hexdigest(),'code_commit':commit,'source_content':'Local source metadata is frozen. Remote pages are not fetched, archived or reverified by this build.','human_attestation':'Names/rights references are locally entered attestations, not identity or rights certification.'})
            (stage/'code').mkdir()
            for name in ['portfolio.py','portfolio_models.py','portfolio_report.py']:shutil.copyfile(Path(__file__).with_name(name),stage/'code'/name)
            from .portfolio_report import render
            (stage/'index.html').write_text(render(sid,plan,catalog,evaluation),encoding='utf-8')
            manifest={'schema_version':1,'snapshot_id':sid,'files':{str(p.relative_to(stage)):{'sha256':digest(p),'size_bytes':p.stat().st_size} for p in stage.rglob('*') if p.is_file()}}
            write(stage/'manifest.json',manifest)
            target=self.path(sid);ensure_storage(target);target.parent.mkdir(parents=True,exist_ok=True);stage.rename(target)
            check=verify_package(target)
            if not check['integrity_ok']:raise ValueError(str(check))
            with self.db:self.db.execute('INSERT INTO snapshots VALUES (?,?)',(sid,digest(target/'manifest.json')))
            self.export(self.root/'registry_export.json')
            return {'snapshot_id':sid,'report':str(target/'index.html'),'execution':'succeeded','record_integrity':'complete','scientific_review':'pending','experiment_authorized':False}
        except Exception:
            # Retain staging for diagnosis; never publish a fabricated successful state.
            raise
    def review(self,snapshot,review):
        path=self.require_valid(snapshot)
        item=next((i for i in load(path/'evaluation.json')['items'] if i['hypothesis_id']==review.hypothesis_id),None)
        if item is None:raise ValueError('Unknown hypothesis')
        if review.action=='authorize_experiment' and item['experiment_gate']!='ready_for_human_review':raise ValueError('Experiment gate blocked: '+', '.join(x['code'] for x in item['gaps']))
        self.db.execute('BEGIN IMMEDIATE')
        try:
            events=self.events()
            p={'sequence':len(events)+1,'previous_sha256':events[-1]['sha256'] if events else '0'*64,'snapshot_id':snapshot,'manifest_sha256':self.anchor(snapshot),'recorded_at':now(),'review':review.model_dump(),'attestation':'Local self-attestation; not identity verification or clinical authorization'}
            sha=hashlib.sha256(canonical(p)).hexdigest()
            self.db.execute('INSERT INTO events VALUES (?,?,?)',(p['sequence'],canonical(p).decode(),sha));self.db.commit()
        except Exception:self.db.rollback();raise
        self.export(self.root/'registry_export.json')
        return p
    def export(self,destination):
        data={'schema_version':1,'created_at':now(),'snapshots':self.status()['snapshots'],'review_events':self.events()}
        write(destination,data);return data
    def backup(self,destination):
        metadata=self.export(self.root/'registry_export.json');packages={}
        for row in metadata['snapshots']:
            path=self.require_valid(row['snapshot_id'])
            packages[row['snapshot_id']]={name:base64.b64encode((path/name).read_bytes()).decode() for name in sorted(PACKAGE_FILES)}
        bundle={'schema_version':1,'metadata':metadata,'packages':packages}
        write(destination,bundle)
        return {'backup':str(destination),'sha256':digest(destination),'snapshots':len(packages),'events':len(metadata['review_events'])}
    def restore(self,backup,expected_sha256):
        if Path(backup).stat().st_size>100*1024*1024:raise ValueError('Backup exceeds 100 MiB limit')
        if digest(backup)!=expected_sha256:raise ValueError('Backup checksum mismatch')
        b=load(backup)
        if b.get('schema_version')!=1 or b['metadata'].get('schema_version')!=1:raise ValueError('Unsupported backup')
        if self.status()['snapshots'] or self.events():raise ValueError('Restore requires empty portfolio registry')
        rows=b['metadata']['snapshots'];ids=[r['snapshot_id'] for r in rows]
        if len(ids)!=len(set(ids)) or set(ids)!=set(b['packages']):raise ValueError('Backup package inventory mismatch')
        stage=ensure_storage(self.root/'staging'/('restore-'+uuid.uuid4().hex));stage.mkdir(parents=True)
        for row in rows:
            sid=row['snapshot_id'];target=self.path(sid)
            if target.exists():raise ValueError('Restore target exists')
            files=b['packages'][sid]
            if set(files)!=PACKAGE_FILES:raise ValueError('Backup contains forbidden paths or missing files')
            for name,encoded in files.items():
                dest=stage/sid/name;dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(base64.b64decode(encoded,validate=True))
            v=verify_package(stage/sid,row['manifest_sha256'])
            if not v['integrity_ok']:raise ValueError(str(v))
        previous='0'*64;events=b['metadata']['review_events'];anchors={r['snapshot_id']:r['manifest_sha256'] for r in rows}
        for n,event in enumerate(events,1):
            payload=event['payload'];review=PortfolioReview.model_validate(payload['review'])
            if payload['sequence']!=n or payload['previous_sha256']!=previous or hashlib.sha256(canonical(payload)).hexdigest()!=event['sha256']:raise ValueError('Invalid review chain')
            if anchors.get(payload['snapshot_id'])!=payload['manifest_sha256']:raise ValueError('Review references unknown snapshot anchor')
            items=load(stage/payload['snapshot_id']/'evaluation.json')['items']
            item=next((i for i in items if i['hypothesis_id']==review.hypothesis_id),None)
            if not item or (review.action=='authorize_experiment' and item['experiment_gate']!='ready_for_human_review'):raise ValueError('Invalid review authorization')
            previous=event['sha256']
        # All contents validated before admitting any restored records.
        for sid in ids:
            dest=self.path(sid);dest.parent.mkdir(parents=True,exist_ok=True);(stage/sid).rename(dest)
        with self.db:
            self.db.executemany('INSERT INTO snapshots VALUES (?,?)',[(r['snapshot_id'],r['manifest_sha256']) for r in rows])
            self.db.executemany('INSERT INTO events VALUES (?,?,?)',[(e['payload']['sequence'],canonical(e['payload']).decode(),e['sha256']) for e in events])
        stage.rmdir();self.export(self.root/'registry_export.json')
        return {'restored_snapshots':len(rows),'restored_reviews':len(events)}
