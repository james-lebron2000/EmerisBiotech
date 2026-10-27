"""Seed explicit UC missing material from existing records; no download/review."""
import argparse
from pathlib import Path

from vbt.asset_diligence import TYPES, context, prepare_claim, sha
from vbt.programs import Programs


def seed(store, pid):
    p = store.get(pid)
    assets = [a for a in p['assets'] if a['asset_name'] == 'Vixarelimab' and a['nct'] == 'NCT06137183']
    if len(assets) != 1:
        raise ValueError('需唯一匹配 Vixarelimab / NCT06137183 的既有绑定来源')
    asset = assets[0]
    candidates = []
    for category, label in TYPES.items():
        row = dict(asset_id=asset['event_id'], asset_identity=asset['asset_name'], category=category,
                   territory='未知；不据单一试验推定全球权利', indication='UC 外部研究参考；其他适应症未核实',
                   rights_type='未知，未核实；申办或试验状态不代表资产权利',
                   direction='unknown', coverage_start='unknown', coverage_end='unknown', source_ref='',
                   claim=label + '所需资料尚未核实，不能推定已获得权利或存在交易机会',
                   limitations='仅整理既有记录和缺口；没有新增外部检索、法律调查或人工审阅',
                   missing='缺少精确分子、地区、用途、时间及' + label + '的完整证据链',
                   next_action='取得有日期的' + label + '原始材料，核对主体、范围及反证后由实际人员审阅')
        if category == 'sponsor':
            row.update(direction='support',source_ref='asset:' + asset['event_id'],
                       coverage_start=asset['last_update'], coverage_end=asset['last_update'],
                       rights_type='不适用；登记申办关系，非所有权或许可',
                       claim='NCT06137183 登记更新 ' + asset['last_update'] + ' 记载申办方为 ' + asset['registered_sponsor'],
                       missing='仍缺主体权利链与地区／用途范围；登记申办方不能证明所有权',
                       next_action='核对申办实体与分子身份，另行取得所有权或许可原始资料')
        elif category == 'development':
            row.update(direction='counter',source_ref='asset:' + asset['event_id'],
                       coverage_start=asset['last_update'],coverage_end=asset['last_update'],
                       rights_type='不适用；试验级开发风险',
                       claim='NCT06137183 在所绑更新记录中的状态为 ' + asset['trial_status'] + '；原因：' + asset['why_stopped'],
                       missing='其他试验、适应症、后续更新和完整资产开发状态未核实',
                       next_action='取得后续登记与开发披露，逐试验核对；不将单试验状态扩展为全球资产结论')
        candidates.append(row)
    prepared = [prepare_claim(store,pid,c,p['revision']) for c in candidates]
    content_hash = sha(prepared)
    prior = [e for e in p['events'] if e['kind']=='asset_matrix_seed']
    if prior:
        if prior[-1]['payload']['content_hash'] != content_hash:
            raise ValueError('既有导入依据已变化，需独立审阅并追加版本，不能覆盖或自动再导入')
        return prior[-1]['payload']['claim_ids']
    # Resume only exact prepared rows. No new authors/reviews/decisions fabricated.
    ids=[]
    for data in prepared:
        p=store.get(pid)
        if context(p) != data['context_hash']:
            raise ValueError('导入期间项目依据变化，停止追加；已有记录保留待核对')
        matches=[e for e in p['events'] if e['kind']=='asset_claim' and e['payload']==data]
        if len(matches)>1:
            raise ValueError('存在重复匹配资料，请人工核对导入历史')
        if matches:
            ids.append(matches[0]['id']);continue
        store._append(pid,'asset_claim',data,'既有来源整理流程（非人工审阅）',p['revision'])
        ids.append(store.events(pid)[-1]['id'])
    store._append(pid,'asset_matrix_seed',dict(content_hash=content_hash,claim_ids=ids,
                  scope='2 条登记级资料解释与 7 项未知；未核实权利、未生成审阅或决定'),
                  '既有来源整理流程（非人工审阅）',store.get(pid)['revision'])
    return ids


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--state-dir',required=True,type=Path)
    parser.add_argument('--program',default='osmr-uc-independent')
    args=parser.parse_args()
    print('\n'.join(seed(Programs(args.state_dir),args.program)))
