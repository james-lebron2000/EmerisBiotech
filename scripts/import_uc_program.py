"""Import verified, existing UC artifacts; never download or claim human review."""
import argparse
import csv
import hashlib
import json
from pathlib import Path
from vbt.programs import Programs


def import_uc(store, root):
    root=Path(root).resolve()
    manifest=json.loads((root/'ARTIFACT_MANIFEST.json').read_text())
    if manifest.get('run_id')!='uc_optimization_20261026': raise ValueError('Unexpected source run')
    hashes={}
    for row in manifest['files']:
        path=(root/row['path']).resolve()
        if not path.is_relative_to(root): raise ValueError('Unsafe manifest path')
        actual=hashlib.sha256(path.read_bytes()).hexdigest()
        if actual!=row['sha256']: raise ValueError('Input hash mismatch: '+row['path'])
        hashes[row['path']]=actual
    if json.loads((root/'verification.json').read_text()).get('status')!='passed': raise ValueError('Numerical verification missing')
    rows=list(csv.DictReader((root/'interaction_results.csv').open()))
    r=next(x for x in rows if x['gene']=='OSMR' and x['endpoint']=='healing')
    pid='osmr-uc-independent'
    existing=[p for p in store.list() if p['id']==pid]
    if existing:
        imports=[e for e in existing[0]['events'] if e['kind']=='import']
        if not imports or imports[-1]['payload']['files']!=hashes: raise ValueError('Existing source differs; create a new versioned import')
        if not any(e['kind']=='import_complete' for e in existing[0]['events']): raise ValueError('Previous import incomplete; inspect before retrying')
        return pid
    store.create(dict(title='OSMR × UC：分层假说与验证决策',target='OSMR / OSM 轴',population='溃疡性结肠炎；需进一步明确拟富集人群与未满足需求',modality='OSMR 阻断相关研究假说；尚无自有候选分子',hypothesis='基线 OSMR 能否区分特定干预的治疗获益，进而支持可验证的患者富集策略？',comparator='当前 UNIFI 数据为 UST 对照安慰剂；未来 OSMR 阻断干预需独立对照',endpoint='来源标签的 8 周黏膜愈合；临床缓解单独分析',differentiation='目标是证明相对现有临床变量的分层价值；尚未证明',material_rights='仅既有公开数据；药物、实验材料、样本授权与商业权利未落实'), '导入流程（非人工审阅）',pid)
    p=store.get(pid)
    store._append(pid,'import',dict(root=str(root),files=hashes,manifest_sha256=hashlib.sha256((root/'ARTIFACT_MANIFEST.json').read_bytes()).hexdigest(),scope='独立重算结果；完整 UC 案例部分复现；人工科学审阅待完成'), '导入流程（非人工审阅）',p['revision'])
    claims=[
        dict(direction='support',claim='OSMR 与治疗组和安慰剂组的黏膜愈合均存在负向组内关联，保留为研究线索',scope='GSE206285 基线活检；UST 358 人、安慰剂 184 人；非 OSMR 阻断疗效验证',limitations='组内关联不能代替治疗交互或临床效用；公开活检子集，缺少基线严重度等临床变量'),
        dict(direction='counter',claim=f"OSMR 治疗交互 OR={float(r['interaction_OR']):.3f}（95% CI {float(r['interaction_CI_low']):.3f}–{float(r['interaction_CI_high']):.3f}），P={float(r['lr_p']):.3f}；未获得 UST 获益分层证据",scope='8 周黏膜愈合；542 位唯一患者；odds 尺度线性交互',limitations='不支持现阶段据此选择 UST；CI 较宽，不能证明不存在交互；不能推断 OSMR 阻断疗效'),
        dict(direction='uncertain',claim='完整 UC 案例仍为部分复现，原作者病例脚本与部分人群选择尚未恢复',scope='原版对照、TAURUS 患者结局人数、健康对照选择及遗传 L2G 差异',limitations='来源缺口需要原始材料与独立审阅，不能靠调参或重命名模型补齐')]
    for claim in claims:
        claim.update(review='pending',source_date='2026-10-26',source='artifact:interaction_results.csv' if claim['direction']!='uncertain' else 'artifact:UC_优化补充报告_20261026.html')
        store.add_evidence(pid,claim,'导入流程（非人工审阅）',store.get(pid)['revision'])
    store._append(pid,'import_complete',{'evidence_count':3},'导入流程（非人工审阅）',store.get(pid)['revision'])
    return pid


def link_reference_asset(store,pid,source):
    source=Path(source).resolve()
    manifest=json.loads((source.parent.parent/'ARTIFACT_MANIFEST.json').read_text())
    row=next(r for r in manifest['files'] if r['path']=='source/NCT06137183_current.json')
    raw=source.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if sha!=row['sha256']: raise ValueError('Clinical source differs from sealed source')
    study=json.loads(raw)['protocolSection'];ident=study['identificationModule'];status=study['statusModule']
    if ident['nctId']!='NCT06137183': raise ValueError('Unexpected reference trial')
    p=store.get(pid)
    if any(a['source_sha256']==sha for a in p['assets']):return
    data=dict(asset_name='Vixarelimab',nct='NCT06137183',purpose='OSMR 阻断的外部临床参考与反证；不是本项目自有候选药',registered_sponsor=study['sponsorCollaboratorsModule']['leadSponsor']['name'],phases=study['designModule']['phases'],trial_status=status['overallStatus'],why_stopped=status.get('whyStopped','未记录'),last_update=status['lastUpdatePostDateStruct']['date'],snapshot_date='2026-10-25',rights_status='not_established',source_path=str(source),source_sha256=sha,source_url='https://clinicaltrials.gov/study/NCT06137183')
    store._append(pid,'asset_import',data,'来源导入流程（非权利核验）',p['revision'])

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--state-dir',required=True,type=Path);ap.add_argument('--source',required=True,type=Path);ap.add_argument('--clinical-source',type=Path)
    a=ap.parse_args();store=Programs(a.state_dir);pid=import_uc(store,a.source)
    if a.clinical_source:link_reference_asset(store,pid,a.clinical_source)
    print(pid)
