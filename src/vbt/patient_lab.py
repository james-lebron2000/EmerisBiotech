"""Synthetic-only patient scenarios. No clinical model, external calls or dosing."""
import copy
import hashlib
import json
import math
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict
from .io import now
from .sqlite_connections import ClosingConnection

MODEL_VERSION = 'synthetic-two-population-v1'
PLANS = {
    'a': {'label': '方案 A', 'description': '虚构 · 机制 A', 'kill': .095, 'growth': .052, 'survival_scale': 21},
    'b': {'label': '方案 B', 'description': '虚构 · 机制 B', 'kill': .042, 'growth': .004, 'survival_scale': 25},
    'ab': {'label': '方案 A＋B', 'description': '虚构 · 联合机制', 'kill': .11, 'growth': -.008, 'survival_scale': 29},
}
PATIENT = {
    'patient_id': 'P001', 'source_kind': 'synthetic', 'fixture_version': 'patient-p001-v1',
    'clinical': {'疾病背景': '转移性结直肠癌', '年龄／体能': '58 岁／ECOG 1', '影像部位': '肝 2 处、肺 1 处（情景背景）', '基线负荷': '归一化指数 100，非直径或体积', '既往给药': '虚构机制 A（演练设定）', '症状／肝肾功能': '未提供，不视为正常'},
    'molecular': {'病理／DNA': '仅示意字段，未用于计算', 'RNA／蛋白／代谢': '未提供', '单细胞／空间组学': '未提供', '纵向 ctDNA': '未提供', '敏感／耐药构成': '人工情景假设，非检测结果'},
    'observations': [
        {'week': 0, 'available_week': 0, 'burden_index': 100., 'source_id': 'synthetic-baseline'},
        {'week': 6, 'available_week': 6, 'burden_index': 82., 'source_id': 'synthetic-followup-6', 'recorded_treatment': 'a'},
        {'week': 12, 'available_week': 12, 'burden_index': 69., 'source_id': 'synthetic-followup-12', 'recorded_treatment': 'a'},
    ],
}


class ScenarioRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    patient_id: Literal['P001'] = 'P001'
    source_kind: Literal['synthetic'] = 'synthetic'
    resistance: Literal['low', 'high'] = 'low'


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def snapshot(cutoff=0):
    if cutoff not in (0, 6, 12):
        raise ValueError('Unsupported synthetic cutoff')
    patient = copy.deepcopy(PATIENT)
    patient['cutoff_week'] = cutoff
    patient['observations'] = [row for row in patient['observations'] if row['available_week'] <= cutoff]
    return patient


def simulate(patient, resistance):
    """Explicit toy assumptions; resetting to an observation is not model training."""
    if patient != snapshot(patient.get('cutoff_week')) or resistance not in ('low', 'high'):
        raise ValueError('Only the built-in synthetic snapshot is supported')
    initial = patient['observations'][-1]['burden_index']
    fraction = .2 if resistance == 'low' else .45
    forecasts = {}
    for key, plan in PLANS.items():
        trajectory = []
        for week in range(25):
            sensitive = initial * (1 - fraction) * math.exp(-plan['kill'] * week)
            resistant = initial * fraction * math.exp(plan['growth'] * week)
            burden = sensitive + resistant
            spread = burden * (.04 + .012 * week)
            trajectory.append({'week_after_cutoff': week, 'burden_index': round(burden, 4),
                               'scenario_low': round(max(0, burden - spread), 4),
                               'scenario_high': round(burden + spread, 4),
                               'resistant_fraction': round(resistant / burden, 6)})
        forecasts[key] = {'trajectory': trajectory, 'clinical_response': None, 'pfs': None, 'os': None,
                          'toxicity': None, 'quality_of_life': None}
    return forecasts


def survival_illustration():
    # Independent, fixed mathematical illustration. Never derived from tumor burden.
    return {key: [{'month': month, 'illustrative_percent': round(100 * math.exp(-(month / p['survival_scale']) ** 1.28), 4)}
                  for month in range(25)] for key, p in PLANS.items()}


def result(request, cutoff=0, parent_id=None):
    patient = snapshot(cutoff)
    parameters = {key: {k: plan[k] for k in ('kill', 'growth')} for key, plan in PLANS.items()}
    inputs = {'patient': patient, 'resistance_assumption': request.resistance, 'model_version': MODEL_VERSION,
              'parameters': parameters, 'span_fraction': {'intercept': .04, 'per_week': .012}}
    return {'schema_version': 'patient-scenario-v1', 'model_version': MODEL_VERSION,
            'model_parameters': parameters, 'span_fraction': inputs['span_fraction'],
            'patient': patient, 'patient_sha256': digest(patient), 'input_sha256': digest(inputs), 'resistance_assumption': request.resistance,
            'parent_id': parent_id, 'forecasts': simulate(patient, request.resistance),
            'survival_illustration': {'kind': 'independent_math_illustration', 'shape': 1.28,
                                      'scale_months': {k: p['survival_scale'] for k, p in PLANS.items()},
                                      'curves': survival_illustration()},
            'execution': 'succeeded', 'scientific_review': 'not_performed', 'clinical_use': False,
            'limits': ['Synthetic data and fictional treatments only.', 'Scenario spans are not calibrated confidence intervals.',
                       'Molecular fields and anatomical sites are not model inputs.',
                       'Clinical response, PFS, OS, toxicity and quality of life remain unavailable.',
                       'Follow-up observations are predetermined synthetic fixtures, not validation outcomes.']}


class PatientLab:
    def __init__(self, state):
        state = Path(state).resolve()
        if state.is_relative_to('/Volumes'):
            raise ValueError('Active SQLite must be local')
        state.mkdir(parents=True, exist_ok=True)
        self.path = state / 'patient-lab.sqlite3'
        with self.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, created TEXT NOT NULL, parent_id TEXT UNIQUE, payload TEXT NOT NULL, sha256 TEXT NOT NULL)')

    def db(self):
        db = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        db.row_factory = sqlite3.Row
        return db

    def get(self, run_id):
        if not isinstance(run_id, str) or not re.fullmatch(r'[a-f0-9]{32}', run_id):
            raise ValueError('Invalid scenario ID')
        with self.db() as db:
            row = db.execute('SELECT * FROM scenarios WHERE id=?', (run_id,)).fetchone()
        if row is None:
            raise ValueError('Scenario not found')
        return self._read(row)

    def _read(self, row):
        payload = json.loads(row['payload'])
        if digest(payload) != row['sha256'] or payload.get('schema_version') != 'patient-scenario-v1':
            raise ValueError('Scenario integrity check failed')
        return {'run_id': row['id'], 'created_at': row['created'], 'sha256': row['sha256'], 'result': payload}

    def history(self):
        with self.db() as db:
            rows = db.execute('SELECT * FROM scenarios ORDER BY rowid DESC LIMIT 30').fetchall()
        return [self._read(row) for row in rows]

    def run(self, values):
        request = ScenarioRequest.model_validate(values)
        return self._save(result(request))

    def followup(self, parent_id):
        parent = self.get(parent_id)['result']
        cutoff = parent['patient']['cutoff_week']
        if cutoff == 12:
            raise ValueError('No further synthetic follow-up is available')
        request = ScenarioRequest(resistance=parent['resistance_assumption'])
        return self._save(result(request, cutoff + 6, parent_id))

    def _save(self, payload):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            parent_id = payload['parent_id']
            if parent_id:
                previous = db.execute('SELECT * FROM scenarios WHERE parent_id=?', (parent_id,)).fetchone()
                if previous:
                    return self._read(previous)
            run_id = uuid.uuid4().hex
            db.execute('INSERT INTO scenarios VALUES (?,?,?,?,?)', (run_id, now(), parent_id, canonical(payload), digest(payload)))
        return self.get(run_id)
