"""Bounded, deterministic research workflows. No external AI or clinical model."""
import csv
import hashlib
import json
import math
import random
import statistics as st
from collections import Counter
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'research-0.3.0'
DOSES = [0.01, 0.03, 0.1, 0.3, 1.0, 3.0]


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)
    kind: Literal['public_cohort', 'pdo_simulation'] = 'pdo_simulation'
    disease: Literal['crc', 'gastric'] = 'crc'
    age_min: int = Field(default=0, ge=0, le=120)
    age_max: int = Field(default=120, ge=0, le=120)
    sex: Literal['all', 'FEMALE', 'MALE'] = 'all'
    stage: Literal['all', 'I', 'II', 'III', 'IV'] = 'all'
    donors: int = Field(default=24, ge=12, le=48)
    seed: int = Field(default=42, ge=0, le=1000000)
    noise: float = Field(default=0.06, ge=0, le=0.2)
    budget: int = Field(default=6, ge=1, le=12)

    @model_validator(mode='after')
    def valid_age(self):
        if self.age_min > self.age_max:
            raise ValueError('年龄下限不能大于上限。')
        return self


def stage_group(raw):
    text = raw.upper().replace('STAGE', '').strip()
    for s in ('IV', 'III', 'II', 'I'):
        if text == s or text in [s + suffix for suffix in ('A', 'B', 'C')]:
            return s
    return '未知'


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def cohort(req):
    study = 'coadread_tcga_pan_can_atlas_2018' if req.disease == 'crc' else 'stad_tcga_pan_can_atlas_2018'
    manifest = json.loads((ROOT / 'public_data/manifest.json').read_text())
    entry = next(e for e in manifest['files'] if e['path'] == study + '__data_clinical_patient.txt')
    raw = (ROOT / 'public_data' / entry['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry['sha256']:
        raise ValueError('公共数据来源哈希不一致，已停止分析。')
    rows = list(csv.DictReader((l for l in raw.decode().splitlines() if not l.startswith('#')), delimiter='\t'))
    selected, excluded = [], Counter()
    for row in rows:
        age = number(row.get('AGE'))
        if age is not None and not 0 <= age <= 120:
            age = None
        sex = row.get('SEX', '').strip().upper()
        stage = stage_group(row.get('AJCC_PATHOLOGIC_TUMOR_STAGE', ''))
        reason = None
        if (req.age_min, req.age_max) != (0, 120) and (age is None or not req.age_min <= age <= req.age_max):
            reason = '年龄不符或未知'
        elif req.sex != 'all' and sex != req.sex:
            reason = '性别不符或未知'
        elif req.stage != 'all' and stage != req.stage:
            reason = '分期不符或未知'
        if reason:
            excluded[reason] += 1
        else:
            selected.append((row, age, sex, stage))
    ages = [a for _, a, _, _ in selected if a is not None]
    stages = Counter(stage for _, _, _, stage in selected)
    sexes = Counter(s if s in ('FEMALE', 'MALE') else '未知' for _, _, s, _ in selected)
    age_groups = Counter('<40' if a < 40 else '40–59' if a < 60 else '60–79' if a < 80 else '≥80' for a in ages)
    age_groups['未知'] = len(selected) - len(ages)
    missing = Counter()
    for row, age, sex, stage in selected:
        missing['年龄'] += age is None
        missing['性别'] += sex not in ('FEMALE', 'MALE')
        missing['分期'] += stage == '未知'
        months = number(row.get('OS_MONTHS'))
        missing['OS 随访时长'] += months is None or months < 0
        missing['OS 状态'] += row.get('OS_STATUS', '') not in ('0:LIVING', '1:DECEASED')
    return {
        'title': 'TCGA 结直肠癌队列筛选' if req.disease == 'crc' else 'TCGA 胃癌队列筛选',
        'evidence_kind': 'public_real',
        'source': {'name': manifest['source'], 'url': entry['url'], 'sha256': entry['sha256'],
                   'revision': manifest['repository_revision'], 'retrieved_utc': manifest['retrieved_utc'],
                   'license_reference': manifest['license_reference'], 'license_note': manifest['license_note']},
        'summary': {'source_patients': len(rows), 'included_patients': len(selected),
                    'excluded_patients': len(rows) - len(selected), 'known_age_count': len(ages),
                    'age_median': round(st.median(ages), 1) if ages else None},
        'stage_counts': {k: stages[k] for k in ['I', 'II', 'III', 'IV', '未知']},
        'age_counts': {k: age_groups[k] for k in ['<40', '40–59', '60–79', '≥80', '未知']},
        'sex_counts': dict(sexes), 'missing_counts': dict(missing), 'exclusions': dict(excluded),
        'actions': ['按主要终点检查可用人数与缺失原因，再决定研究纳入规则。',
                    '如需治疗反应研究，另行接入实际治疗、暴露和结局；本队列概览不提供药敏标签。'],
        'methods': ['年龄不筛选时保留未知年龄；应用年龄范围时排除未知年龄。',
                    '病理分期合并 A/B/C 亚期到 I–IV；未知值单列，原始来源不被改写。',
                    '排除人数按年龄→性别→分期的首个不符原因计数，分母为来源全部患者。'],
        'limitations': ['历史公开队列的描述性分析，无因果或治疗效果推断；不代表本公司招募患者。',
                        '未计算生存模型、统计显著性或临床推荐。']}


def auc(values, doses=DOSES):
    """Observed trapezoidal area over log10 dose, normalized by range and 100%."""
    x = [math.log10(d) for d in doses]
    return sum((x[i+1] - x[i]) * (values[i] + values[i+1]) / 200 for i in range(len(x)-1)) / (x[-1] - x[0])


def fit_line(points):
    mx, my = st.mean(p[0] for p in points), st.mean(p[1] for p in points)
    denom = sum((p[0] - mx) ** 2 for p in points)
    slope = sum((x - mx) * (y - my) for x, y in points) / denom if denom else 0
    return {'intercept': my - slope * mx, 'slope': slope}


def simulation(req):
    rng = random.Random(req.seed)
    donor_ids = [f'SIM-D{i+1:03d}' for i in range(req.donors)]
    shuffled = donor_ids.copy(); rng.shuffle(shuffled)
    test_ids = set(shuffled[:max(3, req.donors // 4)])
    donors = [{'id': d, 'baseline_marker': round(rng.random(), 5),
               'split': 'test' if d in test_ids else 'train'} for d in donor_ids]
    compounds = [('SIM-A', -0.5, -1.6), ('SIM-B', -0.2, 1.5), ('SIM-C', 0.0, -0.3), ('SIM-D', 0.4, 0.3)]
    wells, curves, responses = [], [], []
    for donor in donors:
        control = [max(1, 100 + rng.gauss(0, req.noise * 100)) for _ in range(3)]
        control_mean = st.mean(control)
        for rep, value in enumerate(control):
            wells.append({'donor_id': donor['id'], 'split': donor['split'], 'compound': 'VEHICLE',
                          'dose_uM': 0, 'hours': 72, 'technical_replicate': rep+1,
                          'raw_signal': round(value, 5), 'viability_percent': round(value / control_mean * 100, 5),
                          'evidence_kind': 'synthetic'})
        for drug, intercept, sensitivity in compounds:
            latent_log_ic50 = intercept + sensitivity * (donor['baseline_marker'] - 0.5) + rng.gauss(0, 0.12)
            means, deviations = [], []
            for dose in DOSES:
                expected = 100 / (1 + (dose / 10**latent_log_ic50)**1.1)
                normalized = []
                for rep in range(3):
                    observed = min(120, max(0, expected + rng.gauss(0, req.noise * 100)))
                    normalized.append(observed / control_mean * 100)
                    wells.append({'donor_id': donor['id'], 'split': donor['split'], 'compound': drug,
                                  'dose_uM': dose, 'hours': 72, 'technical_replicate': rep+1,
                                  'raw_signal': round(observed, 5), 'viability_percent': round(normalized[-1], 5),
                                  'evidence_kind': 'synthetic'})
                means.append(st.mean(normalized)); deviations.append(st.stdev(normalized))
            a = auc(means)
            curves.append({'donor_id': donor['id'], 'compound': drug, 'mean': means, 'technical_sd': deviations})
            responses.append({'donor_id': donor['id'], 'compound': drug, 'auc': a, 'split': donor['split'],
                              'baseline_marker': donor['baseline_marker']})
    predictions, fits = [], []
    for drug, _, _ in compounds:
        train = [r for r in responses if r['compound'] == drug and r['split'] == 'train']
        fit = fit_line([(r['baseline_marker'], r['auc']) for r in train])
        mean = st.mean(r['auc'] for r in train)
        fits.append({'compound': drug, **fit, 'drug_mean_baseline': mean, 'training_donors': len(train)})
        for row in responses:
            if row['compound'] == drug and row['split'] == 'test':
                prediction = fit['intercept'] + fit['slope'] * row['baseline_marker']
                predictions.append({'donor_id': row['donor_id'], 'compound': drug, 'observed_auc': row['auc'],
                                    'predicted_auc': prediction, 'baseline_auc': mean})
    model_mae = st.mean(abs(p['observed_auc'] - p['predicted_auc']) for p in predictions)
    baseline_mae = st.mean(abs(p['observed_auc'] - p['baseline_auc']) for p in predictions)
    ranking = sorted([{'compound': drug,
                       'mean_auc': st.mean(r['auc'] for r in responses if r['compound'] == drug),
                       'donor_sd': st.stdev(r['auc'] for r in responses if r['compound'] == drug),
                       'donors': req.donors} for drug, _, _ in compounds], key=lambda r: r['mean_auc'])
    # Uses observed residuals only AFTER evaluation; follow-up is new independent replicate work.
    followup = sorted(predictions, key=lambda p: abs(p['observed_auc'] - p['predicted_auc']), reverse=True)[:req.budget]
    return {'title': '仿真 PDO 干预反应实验', 'evidence_kind': 'synthetic',
            'source': {'name': 'Emeris 参数化合成生成器', 'version': VERSION,
                       'note': '供体、标志物、药物及结果均为合成；没有真实药物对应关系。'},
            'summary': {'donors': req.donors, 'compounds': 4, 'doses': len(DOSES), 'wells': len(wells),
                        'training_donors': req.donors-len(test_ids), 'test_donors': len(test_ids)},
            'doses_uM': DOSES, 'donors': donors, 'curves': curves, 'responses': responses, 'ranking': ranking,
            'model_evaluation': {'model': '按化合物拟合基线标志物→AUC 的一元线性回归',
                                 'baseline': '训练供体内的化合物平均 AUC', 'model_mae': model_mae,
                                 'baseline_mae': baseline_mae, 'improvement': baseline_mae-model_mae,
                                 'train_donors': [d['id'] for d in donors if d['split'] == 'train'],
                                 'test_donors': sorted(test_ids), 'parameters': fits, 'predictions': predictions},
            'next_experiments': [{'donor_id': p['donor_id'], 'compound': p['compound'],
                                  'priority': i+1, 'observed_error': abs(p['observed_auc']-p['predicted_auc']),
                                  'action': '独立培养批次复测完整 6 浓度曲线，3 个技术重复并配溶剂对照',
                                  'reason': '本轮实测与预测偏差较大；检查重复性与模型失配'} for i, p in enumerate(followup)],
            'wells': wells,
            'methods': ['72 小时单时点；6 个浓度；每条件 3 个技术重复；每供体 3 个共享溶剂对照。',
                        '信号按供体溶剂对照均值归一化；合成原始信号截断为 0–120，归一化结果可超过 100%。',
                        'AUC 为实测浓度范围内 log10 浓度梯形积分，再除以跨度和 100%；越低表示该合成检测中抑制越强。',
                        '供体在训练/测试之间完全分离；回归系数和均值基线只使用训练供体。',
                        '曲线离散度为技术重复 SD；排序离散度为供体 SD，均不是置信区间。',
                        '复验优先级在评价后依据观察误差产生，属于下一轮计划；不得用这批结果重新声称独立测试。'],
            'limitations': ['生成器预设标志物与反应相关；模型增益只能证明软件链路，不能证明生物学发现或临床能力。',
                            '仅一个仿真培养批次，没有化学家族/外部中心验证，不输出真实选药建议。',
                            '复验清单按供体×化合物曲线计预算，不代表孔数或已经完成的实验；未训练或更新线上疾病模型。']}


def execute(req):
    output = cohort(req) if req.kind == 'public_cohort' else simulation(req)
    output.update(workflow_version=VERSION, request=req.model_dump(), clinical_use=False)
    encoded = json.dumps(output, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    output['result_sha256'] = hashlib.sha256(encoded).hexdigest()
    return output
