"""Versioned R&D decisions. Local records are not approvals or clinical validation."""
import hashlib
import json
import math
import re
import sqlite3
import uuid
from datetime import datetime, timezone, date
from pathlib import Path
from .sqlite_connections import ClosingConnection


class Conflict(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def text(value, label, minimum=3, maximum=4000):
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise ValueError(label + '长度或格式不正确')
    return value.strip()

def finite(value, label):
    try: number=float(value)
    except (ValueError,TypeError): raise ValueError(label+'须为有限数值')
    if not math.isfinite(number): raise ValueError(label+'须为有限数值')
    return number

def compare_interval(criterion, result):
    """Compare reported interval to frozen fences, never certify an experiment."""
    if not criterion: return 'no_frozen_numeric_criterion'
    if criterion['direction']=='higher':
        if result['ci_low']>=criterion['success_threshold']: return 'meets_recorded_threshold'
        if result['ci_high']<=criterion['stop_threshold']: return 'reaches_recorded_stop'
    else:
        if result['ci_high']<=criterion['success_threshold']: return 'meets_recorded_threshold'
        if result['ci_low']>=criterion['stop_threshold']: return 'reaches_recorded_stop'
    return 'inconclusive_between_thresholds'


class Programs:
    def __init__(self, state):
        self.state = Path(state).resolve()
        if self.state.is_relative_to('/Volumes'):
            raise ValueError('研发项目数据库必须位于本机磁盘')
        self.state.mkdir(parents=True, exist_ok=True)
        self.path = self.state / 'programs.sqlite3'
        if self.path.is_symlink():
            raise ValueError('数据库不能是符号链接')
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS program_events (seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, program TEXT NOT NULL, kind TEXT NOT NULL, created TEXT NOT NULL, author TEXT NOT NULL, payload TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS program_by_id ON program_events(program,seq)')
            db.execute("CREATE TRIGGER IF NOT EXISTS program_no_update BEFORE UPDATE ON program_events BEGIN SELECT RAISE(ABORT,'append only'); END")
            db.execute("CREATE TRIGGER IF NOT EXISTS program_no_delete BEFORE DELETE ON program_events BEGIN SELECT RAISE(ABORT,'append only'); END")

    def db(self):
        db = sqlite3.connect(self.path, timeout=15, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def events(self, pid):
        with self.db() as db:
            rows = [dict(x) for x in db.execute('SELECT * FROM program_events WHERE program=? ORDER BY seq', (pid,))]
        for row in rows:
            row['payload'] = json.loads(row['payload'])
        previous = ''
        for row in rows:
            body = {k: row[k] for k in ('id', 'program', 'kind', 'created', 'author', 'payload', 'previous')}
            if row['previous'] != previous or hashlib.sha256(canonical(body).encode()).hexdigest() != row['digest']:
                raise ValueError('项目历史哈希链不一致，停止显示为可信记录')
            previous = row['digest']
        return rows

    def list(self):
        with self.db() as db:
            ids = [r[0] for r in db.execute('SELECT DISTINCT program FROM program_events ORDER BY program')]
        return [self.get(pid) for pid in ids]

    def get(self, pid):
        events = self.events(pid)
        if not events:
            raise ValueError('研发项目不存在')
        current = dict(events[0]['payload'])
        evidence, experiments, decisions, results, assets, diligence = [], [], [], [], [], []
        for event in events[1:]:
            if event['kind'] == 'profile':
                current.update(event['payload'])
                for item in evidence: item.update(review='pending',scope_review='hypothesis_changed_requires_review')
                for item in experiments: item.update(status='needs_review_after_hypothesis_change')
                for item in results: item.update(current_scope=False)
            if event['kind'] == 'evidence': evidence.append(dict(event['payload'], event_id=event['id']))
            if event['kind'] == 'evidence_review':
                for item in evidence:
                    if item['event_id']==event['payload']['evidence_id']:
                        item.update(review=event['payload']['review'],review_author=event['author'],review_reason=event['payload']['reason'])
            if event['kind'] == 'experiment': experiments.append(dict(event['payload'], event_id=event['id'],created=event['created']))
            if event['kind'] == 'result': results.append(dict(event['payload'],event_id=event['id'],created=event['created'],author=event['author'],current_scope=True,review='pending'))
            if event['kind'] == 'result_review':
                for item in results:
                    if item['event_id']==event['payload']['result_id']:
                        item.update(review=event['payload']['review'],review_author=event['author'],review_reason=event['payload']['reason'])
            if event['kind'] == 'decision': decisions.append(dict(event['payload'], author=event['author'], created=event['created']))
            if event['kind'] == 'asset_import': assets.append(dict(event['payload'],event_id=event['id']))
            if event['kind'] == 'diligence': diligence.append(dict(event['payload'],author=event['author'],created=event['created']))
        return dict(current, id=pid, revision=events[-1]['digest'], evidence=evidence, experiments=experiments, decisions=decisions, results=results, assets=assets,diligence=diligence, events=events)

    def _append(self, pid, kind, payload, author, expected):
        author = text(author, '记录人', 1, 100)
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT digest FROM program_events WHERE program=? ORDER BY seq DESC LIMIT 1', (pid,)).fetchone()
            previous = row[0] if row else ''
            if expected != previous:
                raise Conflict('项目已有更新，请刷新后重新提交；旧页面不会覆盖新判断')
            body = dict(id=uuid.uuid4().hex, program=pid, kind=kind, created=datetime.now(timezone.utc).isoformat(), author=author, payload=payload, previous=previous)
            digest = hashlib.sha256(canonical(body).encode()).hexdigest()
            db.execute('INSERT INTO program_events(id,program,kind,created,author,payload,previous,digest) VALUES(?,?,?,?,?,?,?,?)', (body['id'],pid,kind,body['created'],author,canonical(payload),previous,digest))
        return pid

    def create(self, payload, author, pid=None):
        required = ('title', 'target', 'population', 'modality', 'hypothesis', 'comparator', 'endpoint', 'differentiation')
        data = {key: text(payload.get(key), key) for key in required}
        data['material_rights'] = text(payload.get('material_rights', '未知，尚未核实材料与权利'), '材料与权利')
        data['stage'] = '研究假说'
        pid = pid or uuid.uuid4().hex
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}', pid): raise ValueError('项目编号格式错误')
        return self._append(pid, 'created', data, author, '')

    def add_evidence(self, pid, payload, author, revision):
        self.get(pid)
        if payload.get('direction') not in ('support', 'counter', 'uncertain'): raise ValueError('需要明确证据方向')
        if payload.get('review') not in ('pending', 'reviewed'): raise ValueError('审阅状态错误')
        data = {key: text(payload.get(key), key) for key in ('claim','scope','limitations','source')}
        data.update(direction=payload['direction'], review=payload['review'])
        try: date.fromisoformat(payload.get('source_date',''))
        except (ValueError,TypeError): raise ValueError('需要来源日期 YYYY-MM-DD')
        data['source_date'] = payload['source_date']
        if not data['source'].startswith(('https://','http://','artifact:')): raise ValueError('来源须为网页或已导入附件引用')
        if data['source'].startswith('artifact:'):
            pack = self.attachment(pid, data['source'].removeprefix('artifact:'))
            data['source_sha256'] = pack[1]
        return self._append(pid,'evidence',data,author,revision)

    def revise(self, pid, payload, author, revision):
        self.get(pid)
        keys=('title','target','population','modality','hypothesis','comparator','endpoint','differentiation','material_rights')
        data={k:text(payload.get(k),k) for k in keys}
        data['revision_reason']=text(payload.get('revision_reason'),'修订依据',10)
        return self._append(pid,'profile',data,author,revision)

    def review_evidence(self, pid, payload, author, revision):
        p=self.get(pid)
        if payload.get('evidence_id') not in {r['event_id'] for r in p['evidence']}: raise ValueError('证据不属于当前项目')
        if payload.get('review') not in ('pending','reviewed'): raise ValueError('审阅状态错误')
        data=dict(evidence_id=payload['evidence_id'],review=payload['review'],reason=text(payload.get('reason'),'审阅依据',10),identity='self_declared_not_authenticated')
        return self._append(pid,'evidence_review',data,author,revision)

    def add_experiment(self, pid, payload, author, revision):
        self.get(pid)
        data = {k:text(payload.get(k),k) for k in ('question','design','success','stop','owner','currency')}
        if data['currency'] not in ('CNY','USD'): raise ValueError('预算币种须为 CNY 或 USD')
        try: cost=float(payload.get('cost','')); days=int(payload.get('days',''))
        except (ValueError,TypeError): raise ValueError('预算或周期格式不正确')
        if not math.isfinite(cost) or not 0<cost<=1e9 or not 1<=days<=3650: raise ValueError('预算须大于零，周期须为 1–3650 天')
        known = {r['event_id'] for r in self.get(pid)['evidence']}
        refs = payload.get('evidence_ids',[])
        if not isinstance(refs,list) or not refs or not set(refs)<=known: raise ValueError('验证计划必须关联本项目已有证据')
        data.update(cost=cost, days=days, evidence_ids=refs, status='proposed', budget_status='estimate_not_committed')
        numeric_keys=('metric','unit','success_threshold','stop_threshold')
        if any(payload.get(k) not in ('',None) for k in numeric_keys):
            criterion=dict(metric=text(payload.get('metric'),'数值指标'),unit=text(payload.get('unit'),'指标单位',1),direction=payload.get('direction'),success_threshold=finite(payload.get('success_threshold'),'成功门槛'),stop_threshold=finite(payload.get('stop_threshold'),'停止门槛'))
            if criterion['direction'] not in ('higher','lower'): raise ValueError('数值指标须注明期望方向')
            if (criterion['direction']=='higher' and criterion['stop_threshold']>=criterion['success_threshold']) or (criterion['direction']=='lower' and criterion['stop_threshold']<=criterion['success_threshold']): raise ValueError('成功和停止门槛必须分开且符合方向')
            data['criterion']=criterion
        else: data['criterion']=None
        return self._append(pid,'experiment',data,author,revision)

    def add_result(self,pid,payload,author,revision):
        p=self.get(pid)
        plan=next((x for x in p['experiments'] if x['event_id']==payload.get('experiment_id')),None)
        if not plan: raise ValueError('结果必须关联本项目已有验证计划')
        if plan['status']!='proposed': raise ValueError('假说已变化，请先建立当前范围的验证计划')
        data={k:text(payload.get(k),k,1 if k=='unit' else 3) for k in ('metric','unit','independent_unit','population','method','limitations','source')}
        try:
            n=int(payload.get('n',''))
            available=datetime.fromisoformat(payload.get('available_at','').replace('Z','+00:00'))
            if available.tzinfo is None: raise ValueError('timezone required')
        except (ValueError,TypeError,AttributeError): raise ValueError('需要独立单位数及带时区的来源可用时间')
        if n<2 or n>10**9: raise ValueError('独立单位数至少为 2；技术重复不得计为独立单位')
        if available>datetime.now(timezone.utc): raise ValueError('来源可用时间不能在未来')
        data.update(estimate=finite(payload.get('estimate'),'效应估计'),ci_low=finite(payload.get('ci_low'),'95% 区间下限'),ci_high=finite(payload.get('ci_high'),'95% 区间上限'),n=n,available_at=available.isoformat(),interval_level=0.95)
        if not data['ci_low']<=data['estimate']<=data['ci_high']: raise ValueError('效应估计必须落在所填区间内')
        if not data['source'].startswith(('http://','https://','artifact:')): raise ValueError('需要来源 URL 或导入附件引用')
        if data['source'].startswith('artifact:'):
            _,sha=self.attachment(pid,data['source'].removeprefix('artifact:'));data['source_sha256']=sha
        criterion=plan.get('criterion')
        if criterion and (data['metric']!=criterion['metric'] or data['unit']!=criterion['unit']): raise ValueError('结果指标与单位必须与冻结计划完全一致')
        data.update(experiment_id=plan['event_id'],plan_revision=next(e['digest'] for e in p['events'] if e['id']==plan['event_id']),criterion=criterion,assessment=compare_interval(criterion,data),timing='historical_source' if available<=datetime.fromisoformat(plan['created']) else 'source_available_after_plan_not_independent_validation',scope='报告区间与已记录门槛的机械比对；原始数据与统计方法待科学审阅')
        return self._append(pid,'result',data,author,revision)

    def review_result(self,pid,payload,author,revision):
        p=self.get(pid)
        if payload.get('result_id') not in {r['event_id'] for r in p['results']}: raise ValueError('结果不属于当前项目')
        if payload.get('review') not in ('pending','reviewed'): raise ValueError('审阅状态错误')
        data=dict(result_id=payload['result_id'],review=payload['review'],reason=text(payload.get('reason'),'结果审阅依据',10),identity='self_declared_not_authenticated')
        return self._append(pid,'result_review',data,author,revision)

    @staticmethod
    def gaps(program):
        gaps=[]
        if not program['evidence']: gaps.append('尚无可追溯证据')
        if not any(e['direction']=='counter' for e in program['evidence']): gaps.append('反证检索未形成记录')
        if any(e['review']=='pending' for e in program['evidence']): gaps.append('证据仍有待审阅项')
        if not any(x['status']=='proposed' for x in program['experiments']): gaps.append('缺少与当前假说对应的验证计划')
        return gaps

    def decide(self, pid, payload, author, revision):
        p=self.get(pid)
        action=payload.get('action')
        if action not in ('advance','revise','hold','stop'): raise ValueError('未知研发动作')
        refs=payload.get('evidence_ids',[])
        known={e['event_id']:e for e in p['evidence']}
        if not isinstance(refs,list) or not refs or not set(refs)<=set(known): raise ValueError('每次决定必须引用本项目证据')
        result_refs=payload.get('result_ids',[])
        current_results={r['event_id']:r for r in p['results'] if r['current_scope']}
        if not isinstance(result_refs,list) or not set(result_refs)<=set(current_results): raise ValueError('只能引用当前假说下本项目的验证结果')
        if current_results and not result_refs: raise ValueError('已有验证结果，请在决定中明确引用，不可略过')
        if action=='advance':
            if set(result_refs)!=set(current_results): raise ValueError('推进时必须面对当前全部结果，不能只选择有利结果')
            if self.gaps(p): raise ValueError('暂不能推进：'+'；'.join(self.gaps(p)))
            if not any(known[r]['direction']=='counter' for r in refs): raise ValueError('推进决定必须引用反证并解释它')
            if not any(known[r]['direction']=='support' for r in refs): raise ValueError('推进决定必须引用支持证据')
            if any(current_results[r]['review']!='reviewed' for r in result_refs): raise ValueError('引用的验证结果仍待审阅')
            if any(r['assessment']=='reaches_recorded_stop' for r in current_results.values()): raise ValueError('当前结果已触及记录的停止门槛，请修订或停止并记录依据')
            for ref in refs:
                item=known[ref]
                if item['source'].startswith('artifact:'):
                    _,sha=self.attachment(pid,item['source'].removeprefix('artifact:'))
                    if sha!=item.get('source_sha256'): raise ValueError('证据文件已发生版本变化，需要重新导入和审阅')
            for ref in result_refs:
                item=current_results[ref]
                if item['source'].startswith('artifact:'):
                    _,sha=self.attachment(pid,item['source'].removeprefix('artifact:'))
                    if sha!=item.get('source_sha256'): raise ValueError('验证结果源文件已变化，需要重新记录和审阅')
        data=dict(action=action,reason=text(payload.get('reason'),'决定依据',10),evidence_ids=refs,result_ids=result_refs,remaining_risk=text(payload.get('remaining_risk'),'剩余风险'),basis_revision=p['revision'],experiments=[x['event_id'] for x in p['experiments']],decision_scope='研究计划决定；不构成实验授权、科学审批或临床验证')
        return self._append(pid,'decision',data,author,revision)

    def attachment(self, pid, name, max_bytes=None):
        def read_bound(path):
            with Path(path).open('rb') as f:raw=f.read() if max_bytes is None else f.read(max_bytes+1)
            if max_bytes is not None and len(raw)>max_bytes: raise ValueError('附件超过本次工具允许的大小')
            return raw
        if name=='NCT06137183_current.json':
            imports=[e for e in self.events(pid) if e['kind']=='asset_import']
            if not imports: raise ValueError('项目没有参考资产来源')
            record=imports[-1]['payload'];raw=read_bound(record['source_path'])
            if hashlib.sha256(raw).hexdigest()!=record['source_sha256']: raise ValueError('参考资产来源已变化')
            return raw,record['source_sha256']
        if name not in ('interaction_results.csv','sample_flow.csv','verification.json','UC_优化补充报告_20261026.html'): raise ValueError('附件不在允许清单中')
        # Import event binds the file hash. A missing drive or changed file fails closed.
        imports=[e for e in self.events(pid) if e['kind']=='import']
        if not imports: raise ValueError('项目没有已导入附件')
        record=imports[-1]['payload']; path=Path(record['root'])/name
        raw=read_bound(path); expected=record['files'].get(name)
        if not expected or hashlib.sha256(raw).hexdigest()!=expected: raise ValueError('附件与导入时哈希不一致')
        return raw, expected

    def add_diligence(self,pid,payload,author,revision):
        p=self.get(pid)
        asset=next((a for a in p['assets'] if a['event_id']==payload.get('asset_id')),None)
        if not asset: raise ValueError('尽调必须关联本项目有来源的参考资产')
        refs=payload.get('evidence_ids',[]);known={e['event_id'] for e in p['evidence']}
        if not isinstance(refs,list) or not refs or not set(refs)<=known: raise ValueError('尽调必须引用本项目研究证据')
        results={r['event_id'] for r in p['results'] if r['current_scope']};result_refs=payload.get('result_ids',[])
        if not isinstance(result_refs,list) or not set(result_refs)<=results or (results and not result_refs): raise ValueError('尽调需要面对本项目当前验证结果')
        if payload.get('action') not in ('collect_evidence','defer','decline'): raise ValueError('未知尽调动作')
        data={k:text(payload.get(k),k,10) for k in ('thesis','unresolved','next_request')}
        _,sha=self.attachment(pid,'NCT06137183_current.json')
        if sha!=asset['source_sha256']: raise ValueError('参考资产来源版本发生变化')
        data.update(action=payload['action'],asset_id=asset['event_id'],evidence_ids=refs,result_ids=result_refs,basis_revision=p['revision'],rights_status='not_established',scope='研究尽调记录；不构成资产权利、交易承诺、成功概率或投资收益判断')
        return self._append(pid,'diligence',data,author,revision)

    def export(self, pid):
        p=self.get(pid)
        return canonical({'schema_version':1,'program':p,'record_integrity':'hash_chain_verified_at_export','human_identity':'self_declared_not_authenticated','scientific_review':'not_inferred','clinical_validation':'not_inferred'}).encode()
